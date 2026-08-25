"""Build blinded product/review packets and a private system-provenance manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.evaluation.poster_annotation import (  # noqa: E402
    BlankOrdinalAnnotation,
    BlindedProductCandidate,
    BlindedProductScenario,
    BlindedReviewCandidate,
    BlindedReviewScenario,
    ProductAnnotationPacket,
    ReviewAnnotationPacket,
    build_poster_scenario_state,
    load_poster_scenario_dataset,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.nodes.actual_policy import select_actual_policy  # noqa: E402
from app.nodes.actual_recommendation import (  # noqa: E402
    browse_actual_catalog,
    generate_actual_query,
    rank_actual_products,
)
from app.review_retrieval import build_review_retriever  # noqa: E402

DEFAULT_DATASET = BACKEND_ROOT / "data" / "poster_recommendation_scenarios_v1.json"
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / "reports" / "poster_annotation_v1"
DEFAULT_SEED = "poster-annotation-v1-frozen-20260807"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _shuffle(items: list[Any], seed: str) -> list[Any]:
    output = list(items)
    random.Random(seed).shuffle(output)
    return output


def _product_candidate(candidate_id: str, product: Any) -> BlindedProductCandidate:
    detail = product
    return BlindedProductCandidate(
        candidate_id=candidate_id,
        title=detail.title,
        brand=detail.brand,
        price_usd=detail.price_usd,
        average_rating=detail.average_rating,
        selected_review_count=detail.selected_review_count,
        storage_gb=detail.storage_gb,
        memory_gb=detail.memory_gb,
        screen_inches=detail.screen_inches,
        weight_grams=detail.weight_grams,
        operating_system=detail.operating_system,
        stylus_mentioned=detail.stylus_mentioned,
        features=detail.features[:8],
        description=detail.description[:4],
        annotation=BlankOrdinalAnnotation(),
    )


def _packet_instructions(kind: str) -> list[str]:
    if kind == "product":
        return [
            "Judge each candidate independently from the conversation and the same catalog fields.",
            "Use relevance 0 for a hard-constraint violation or an unrelated product, 1 for marginal fit, 2 for a good substitute, and 3 for a strong direct fit.",
            "Do not infer missing attributes. Fill both relevance and rationale for every candidate.",
            "Candidate order, originating system, original rank, and production score are intentionally hidden.",
        ]
    return [
        "Judge whether each review supplies decision-useful evidence for the conversation's soft needs.",
        "Use relevance 0 for unrelated, 1 for a weak mention, 2 for direct evidence, and 3 for specific decision-useful evidence.",
        "A negative review can be highly relevant. Fill both relevance and rationale for every review.",
        "Review order, originating system, original rank, retrieval score, and source review ID are intentionally hidden.",
    ]


def build_packets(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset.resolve()
    dataset = load_poster_scenario_dataset(dataset_path)
    scenarios = dataset.scenarios[: args.limit] if args.limit else dataset.scenarios
    annotator_count = args.annotators or dataset.recommended_annotator_count
    if not 2 <= annotator_count <= 5:
        raise ValueError("--annotators must be between 2 and 5")
    if args.hard_negatives < 0 or args.hard_negatives > 5:
        raise ValueError("--hard-negatives must be between 0 and 5")

    catalog = ExperimentalAmazonCatalog()
    if not catalog.available:
        raise RuntimeError(catalog.status().unavailable_reason)
    settings = load_review_retrieval_settings()
    retrievers = {
        mode: build_review_retriever(replace(settings, mode=mode))
        for mode in ("token", "semantic")
    }
    dataset_sha = _sha256(dataset_path)
    packet_base = "poster-v1-" + hashlib.sha256(
        f"{dataset_sha}:{args.seed}".encode("utf-8")
    ).hexdigest()[:16]

    product_scenarios: list[BlindedProductScenario] = []
    review_scenarios: list[BlindedReviewScenario] = []
    private_scenarios: list[dict[str, Any]] = []
    total_products = 0
    total_reviews = 0

    for scenario in scenarios:
        state = build_poster_scenario_state(scenario)
        policy = select_actual_policy(state)
        if policy.lane != "recommend-lane":
            raise RuntimeError(f"{scenario.id} unexpectedly selected {policy.lane}")
        query = generate_actual_query(state)
        if query.hard_filters != scenario.expected_hard_filters:
            raise RuntimeError(f"{scenario.id} hard filters differ from the frozen contract")

        rankings_by_mode: dict[str, list[Any]] = {}
        reviews_by_id: dict[str, Any] = {}
        candidate_products: dict[str, Any] = {}
        all_filtered_products: dict[str, Any] = {}
        for mode, retriever in retrievers.items():
            products, reviews, browse = browse_actual_catalog(
                query,
                catalog,
                rejected_product_ids=set(),
                review_retriever=retriever,
            )
            if mode == "semantic" and (
                browse.review_retrieval_method != "semantic_cross_encoder"
                or browse.review_retrieval_fallback_reason is not None
            ):
                raise RuntimeError(
                    f"{scenario.id} semantic packet build fell back: "
                    f"{browse.review_retrieval_fallback_reason}"
                )
            rankings, visible_reviews = rank_actual_products(
                state, query, products, reviews
            )
            rankings_by_mode[mode] = rankings
            all_filtered_products.update(
                {product.parent_asin: product for product in products}
            )
            candidate_products.update(
                {
                    ranking.product_id: all_filtered_products[ranking.product_id]
                    for ranking in rankings
                }
            )
            reviews_by_id.update(
                {review.review_id: review for review in visible_reviews}
            )

        pooled_ids = list(candidate_products)
        remaining_ids = sorted(set(all_filtered_products) - set(pooled_ids))
        hard_negative_ids = _shuffle(
            remaining_ids, f"{args.seed}:{scenario.id}:hard-negatives"
        )[: args.hard_negatives]
        for parent_asin in hard_negative_ids:
            candidate_products[parent_asin] = all_filtered_products[parent_asin]
            pooled_ids.append(parent_asin)

        pooled_ids = _shuffle(pooled_ids, f"{args.seed}:{scenario.id}:products")
        blind_product_ids = {
            parent_asin: f"{scenario.id}-p{index:02d}"
            for index, parent_asin in enumerate(pooled_ids, start=1)
        }
        public_products = [
            _product_candidate(
                blind_product_ids[parent_asin],
                catalog.get_product(parent_asin),
            )
            for parent_asin in pooled_ids
        ]
        total_products += len(public_products)

        review_target_ids = {
            ranking.product_id
            for rankings in rankings_by_mode.values()
            for ranking in rankings[:3]
        }
        review_entries: list[BlindedReviewCandidate] = []
        private_review_map: dict[str, Any] = {}
        for parent_asin in sorted(review_target_ids):
            cited_review_ids = list(
                dict.fromkeys(
                    review_id
                    for rankings in rankings_by_mode.values()
                    for ranking in rankings[:3]
                    if ranking.product_id == parent_asin
                    for review_id in ranking.evidence_review_ids
                )
            )
            cited_review_ids = _shuffle(
                cited_review_ids,
                f"{args.seed}:{scenario.id}:{parent_asin}:reviews",
            )
            for index, source_review_id in enumerate(cited_review_ids, start=1):
                review = reviews_by_id[source_review_id]
                blind_review_id = (
                    f"{blind_product_ids[parent_asin]}-r{index:02d}"
                )
                review_entries.append(
                    BlindedReviewCandidate(
                        review_id=blind_review_id,
                        candidate_id=blind_product_ids[parent_asin],
                        product_title=candidate_products[parent_asin].title,
                        rating=review.rating,
                        review_title=review.title,
                        review_text=review.text,
                        verified_purchase=review.verified_purchase,
                        helpful_vote=review.helpful_vote,
                        annotation=BlankOrdinalAnnotation(),
                    )
                )
                private_review_map[blind_review_id] = {
                    "source_review_id": source_review_id,
                    "parent_asin": parent_asin,
                    "cited_by": sorted(
                        mode
                        for mode, rankings in rankings_by_mode.items()
                        for ranking in rankings[:3]
                        if ranking.product_id == parent_asin
                        and source_review_id in ranking.evidence_review_ids
                    ),
                }
        review_entries = _shuffle(
            review_entries, f"{args.seed}:{scenario.id}:review-tasks"
        )
        total_reviews += len(review_entries)

        product_scenarios.append(
            BlindedProductScenario(
                scenario_id=scenario.id,
                conversation_turns=scenario.conversation_turns,
                final_request_summary=scenario.final_request_summary,
                candidates=public_products,
            )
        )
        review_scenarios.append(
            BlindedReviewScenario(
                scenario_id=scenario.id,
                conversation_turns=scenario.conversation_turns,
                final_request_summary=scenario.final_request_summary,
                reviews=review_entries,
            )
        )
        private_scenarios.append(
            {
                "scenario_id": scenario.id,
                "query": query.model_dump(mode="json"),
                "product_map": {
                    blind_product_ids[parent_asin]: {
                        "parent_asin": parent_asin,
                        "pool_source": (
                            "hard_negative"
                            if parent_asin in hard_negative_ids
                            else "system_union"
                        ),
                        "systems": {
                            mode: {
                                "rank": ranking.rank,
                                "total_score": ranking.score.total,
                                "evidence_review_ids": ranking.evidence_review_ids,
                            }
                            for mode, rankings in rankings_by_mode.items()
                            for ranking in rankings
                            if ranking.product_id == parent_asin
                        },
                    }
                    for parent_asin in pooled_ids
                },
                "review_map": private_review_map,
            }
        )

    output_dir = args.output_dir.resolve()
    packet_files: list[str] = []
    for annotator_number in range(1, annotator_count + 1):
        annotator_id = f"annotator-{annotator_number:02d}"
        annotator_dir = output_dir / annotator_id
        public_products = [
            scenario.model_copy(
                update={
                    "candidates": _shuffle(
                        scenario.candidates,
                        f"{args.seed}:{annotator_id}:{scenario.scenario_id}:products",
                    )
                }
            )
            for scenario in product_scenarios
        ]
        public_reviews = [
            scenario.model_copy(
                update={
                    "reviews": _shuffle(
                        scenario.reviews,
                        f"{args.seed}:{annotator_id}:{scenario.scenario_id}:reviews",
                    )
                }
            )
            for scenario in review_scenarios
        ]
        product_packet = ProductAnnotationPacket(
            schema_version="poster-product-annotation-packet-v1",
            packet_id=f"{packet_base}-{annotator_id}",
            annotator_id=annotator_id,
            dataset_sha256=dataset_sha,
            instructions=_packet_instructions("product"),
            scenarios=public_products,
        )
        review_packet = ReviewAnnotationPacket(
            schema_version="poster-review-annotation-packet-v1",
            packet_id=f"{packet_base}-{annotator_id}",
            annotator_id=annotator_id,
            dataset_sha256=dataset_sha,
            instructions=_packet_instructions("review"),
            scenarios=public_reviews,
        )
        for filename, packet in (
            ("product_annotations.json", product_packet),
            ("review_annotations.json", review_packet),
        ):
            path = annotator_dir / filename
            write_report(path, packet.model_dump(mode="json"))
            packet_files.append(str(path.resolve()))

    private_path = output_dir / "private_provenance.json"
    catalog_manifest = catalog.catalog_dir / "manifest.json"
    semantic_manifest = catalog.catalog_dir / "review_semantic_manifest.json"
    write_report(
        private_path,
        {
            "schema_version": "poster-annotation-private-provenance-v1",
            "packet_base_id": packet_base,
            "dataset_version": dataset.dataset_version,
            "dataset_sha256": dataset_sha,
            "dataset_revision": dataset.dataset_revision,
            "catalog_manifest_sha256": _sha256(catalog_manifest),
            "semantic_manifest_sha256": _sha256(semantic_manifest),
            "randomization_seed": args.seed,
            "annotator_count": annotator_count,
            "hard_negatives_per_scenario": args.hard_negatives,
            "scenario_count": len(scenarios),
            "product_judgment_count_per_annotator": total_products,
            "review_judgment_count_per_annotator": total_reviews,
            "scenarios": private_scenarios,
        },
    )
    return {
        "status": "ok",
        "packet_base_id": packet_base,
        "scenario_count": len(scenarios),
        "annotator_count": annotator_count,
        "product_judgment_count_per_annotator": total_products,
        "review_judgment_count_per_annotator": total_reviews,
        "packet_files": packet_files,
        "private_provenance": str(private_path.resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--annotators", type=int)
    parser.add_argument("--hard-negatives", type=int, default=2)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    print(json.dumps(build_packets(args), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
