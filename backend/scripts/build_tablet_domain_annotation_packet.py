"""Build a 3-annotator blind pool from official final system outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_annotation import (  # noqa: E402
    OfficialProductCandidate,
    OfficialProductPacket,
    OfficialProductScenario,
    OfficialReviewCandidate,
    OfficialReviewPacket,
    OfficialReviewScenario,
    OptionalOrdinalGrade,
)
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.models.actual_demo import ActualPipelineTurn  # noqa: E402

DEFAULT_RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
DEFAULT_ANALYSIS = (
    BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1_analysis.json"
)
DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEFAULT_OUTPUT = (
    BACKEND_ROOT / "reports" / "tablet_domain_holdout_annotation_packet_v1"
)

PRODUCT_INSTRUCTIONS = [
    "Judge how suitable each product is for the user's final accumulated needs in this conversation.",
    "Use 0: violates a confirmed must-have or is irrelevant; 1: somewhat related but weak; 2: a good option satisfying must-haves; 3: directly and strongly fits the use and priorities.",
    "Use only the supplied conversation and product information. Do not search externally.",
    "Fill annotation.grade for every candidate. Rationale is optional and is useful for ambiguous or strong 0/3 judgments.",
    "Do not edit IDs, conversation text, product fields, or packet metadata.",
]
REVIEW_INSTRUCTIONS = [
    "Judge how useful each review is as evidence for the user's final accumulated needs in this conversation.",
    "Use 0: unrelated; 1: mentions a related topic but lacks decision-useful detail; 2: directly addresses an important aspect; 3: gives concrete experience, conditions, or trade-offs that are highly useful.",
    "Use only the supplied conversation and review. Do not search externally.",
    "Fill annotation.grade for every review. Rationale is optional and is useful for ambiguous or strong 0/3 judgments.",
    "Do not edit IDs, conversation text, review fields, or packet metadata.",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_final_turn(
    scenario_record: dict[str, Any], expected_turn: int
) -> ActualPipelineTurn | None:
    if scenario_record["completed_turn_count"] != expected_turn:
        return None
    if not scenario_record["turns"]:
        return None
    turn = ActualPipelineTurn.model_validate(scenario_record["turns"][-1])
    if int(turn.turn_id.split("-")[-1]) != expected_turn:
        return None
    return turn


def _rank_map(turn: ActualPipelineTurn | None) -> dict[str, int]:
    return (
        {item.product_id: item.rank for item in turn.rankings[:3]}
        if turn is not None
        else {}
    )


def _review_rank_map(turn: ActualPipelineTurn | None) -> dict[str, dict[str, int]]:
    if turn is None:
        return {}
    result: dict[str, dict[str, int]] = {}
    for card in turn.final_response.product_cards[:3]:
        for review_rank, review in enumerate(card.evidence_reviews, start=1):
            result[review.review_id] = {
                "product_rank": card.ranking.rank,
                "review_rank_within_product": review_rank,
            }
    return result


def build(args: argparse.Namespace) -> dict[str, Any]:
    raw_path = args.raw.resolve()
    analysis_path = args.analysis.resolve()
    dataset_path = args.dataset.resolve()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"refusing to overwrite frozen annotation packet: {output}")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    dataset = load_tablet_holdout_dataset(dataset_path)
    raw_hash = _sha256(raw_path)
    if analysis["raw_report_sha256"] != raw_hash:
        raise RuntimeError("analysis does not belong to the official raw report")

    catalog = ExperimentalAmazonCatalog()
    if not catalog.available:
        raise RuntimeError("official local catalog is unavailable")
    raw_by_condition = {
        condition: {
            item["scenario_id"]: item for item in payload["scenarios"]
        }
        for condition, payload in raw["conditions"].items()
        if condition in {"full", "no_memory"}
    }
    fixed_records = {
        (item["scenario_id"], item["turn"]): item
        for item in analysis["fixed_upstream_no_review"]["records"]
    }

    public_scenarios: dict[str, dict[str, Any]] = {}
    provenance_scenarios: dict[str, Any] = {}
    skipped_scenarios = []
    for scenario in dataset.scenarios:
        final_turn_number = len(scenario.turns)
        full = _completed_final_turn(
            raw_by_condition["full"][scenario.id], final_turn_number
        )
        no_memory = _completed_final_turn(
            raw_by_condition["no_memory"][scenario.id], final_turn_number
        )
        fixed = fixed_records.get((scenario.id, final_turn_number))
        source_ranks = {
            "full": _rank_map(full),
            "no_memory": _rank_map(no_memory),
            "fixed_upstream_no_review": {
                product_id: rank
                for rank, product_id in enumerate(
                    (fixed or {}).get("fixed_upstream_no_review_top10", [])[:3],
                    start=1,
                )
            },
        }
        product_ids = sorted(
            set().union(*(set(ranks) for ranks in source_ranks.values()))
        )
        if not product_ids:
            skipped_scenarios.append(scenario.id)
            continue
        product_blind = {
            product_id: f"{scenario.id}-p{index:02d}"
            for index, product_id in enumerate(product_ids, start=1)
        }

        products = {}
        for product_id in product_ids:
            detail = catalog.get_product(product_id)
            blind_id = product_blind[product_id]
            products[blind_id] = OfficialProductCandidate(
                candidate_id=blind_id,
                title=detail.title,
                brand=detail.brand,
                price_usd=detail.price_usd,
                average_rating=detail.average_rating,
                rating_number=detail.rating_number,
                selected_review_count=detail.selected_review_count,
                storage_gb=detail.storage_gb,
                memory_gb=detail.memory_gb,
                screen_inches=detail.screen_inches,
                weight_grams=detail.weight_grams,
                operating_system=detail.operating_system,
                stylus_mentioned=detail.stylus_mentioned,
                features=detail.features[:12],
                description=detail.description[:6],
                annotation=OptionalOrdinalGrade(),
            )

        review_sources = {
            "full": _review_rank_map(full),
            "no_memory": _review_rank_map(no_memory),
        }
        review_ids = sorted(
            set().union(*(set(items) for items in review_sources.values()))
        )
        review_rows = {
            item.review_id: item for item in catalog.get_reviews_by_ids(review_ids)
        }
        if set(review_rows) != set(review_ids):
            raise RuntimeError(f"review pool is incomplete for {scenario.id}")
        review_blind: dict[str, str] = {}
        reviews = {}
        counts_by_product: dict[str, int] = {}
        for review_id in review_ids:
            review = review_rows[review_id]
            candidate_id = product_blind[review.parent_asin]
            counts_by_product[candidate_id] = counts_by_product.get(candidate_id, 0) + 1
            blind_review_id = (
                f"{candidate_id}-r{counts_by_product[candidate_id]:02d}"
            )
            review_blind[review_id] = blind_review_id
            reviews[blind_review_id] = OfficialReviewCandidate(
                review_id=blind_review_id,
                candidate_id=candidate_id,
                product_title=products[candidate_id].title,
                rating=review.rating,
                review_title=review.title,
                review_text=review.text,
                verified_purchase=review.verified_purchase,
                helpful_vote=review.helpful_vote,
                annotation=OptionalOrdinalGrade(),
            )

        public_scenarios[scenario.id] = {
            "conversation_turns": [turn.utterance for turn in scenario.turns],
            "products": products,
            "reviews": reviews,
        }
        provenance_scenarios[scenario.id] = {
            "expected_final_turn": final_turn_number,
            "condition_output_available": {
                "full": full is not None,
                "no_memory": no_memory is not None,
                "fixed_upstream_no_review": fixed is not None,
            },
            "products": {
                product_blind[product_id]: {
                    "parent_asin": product_id,
                    "system_ranks": {
                        source: ranks[product_id]
                        for source, ranks in source_ranks.items()
                        if product_id in ranks
                    },
                }
                for product_id in product_ids
            },
            "reviews": {
                review_blind[review_id]: {
                    "review_id": review_id,
                    "candidate_id": product_blind[review_rows[review_id].parent_asin],
                    "system_ranks": {
                        source: ranks[review_id]
                        for source, ranks in review_sources.items()
                        if review_id in ranks
                    },
                }
                for review_id in review_ids
            },
        }

    output.mkdir(parents=True)
    packet_hashes = {}
    for annotator_number in range(1, args.annotators + 1):
        annotator_id = f"annotator-{annotator_number:02d}"
        scenario_ids = sorted(public_scenarios)
        product_scenarios = []
        review_scenarios = []
        for scenario_id in scenario_ids:
            payload = public_scenarios[scenario_id]
            product_candidates = list(payload["products"].values())
            review_candidates = list(payload["reviews"].values())
            random.Random(f"{raw_hash}:{annotator_id}:{scenario_id}:product").shuffle(
                product_candidates
            )
            random.Random(f"{raw_hash}:{annotator_id}:{scenario_id}:review").shuffle(
                review_candidates
            )
            product_scenarios.append(
                OfficialProductScenario(
                    scenario_id=scenario_id,
                    conversation_turns=payload["conversation_turns"],
                    evaluation_question=(
                        "How suitable is this product for the user's final accumulated "
                        "requirements and priorities?"
                    ),
                    candidates=product_candidates,
                )
            )
            if review_candidates:
                review_scenarios.append(
                    OfficialReviewScenario(
                        scenario_id=scenario_id,
                        conversation_turns=payload["conversation_turns"],
                        evaluation_question=(
                            "How useful is this review as evidence for judging the "
                            "user's final accumulated requirements and priorities?"
                        ),
                        reviews=review_candidates,
                    )
                )
        annotator_dir = output / annotator_id
        product_path = annotator_dir / "product_annotations.json"
        review_path = annotator_dir / "review_annotations.json"
        write_report(
            product_path,
            OfficialProductPacket(
                schema_version="tablet-domain-product-annotation-packet-v1",
                packet_id=f"{raw['run_id']}-{annotator_id}-products",
                annotator_id=annotator_id,
                official_run_id=raw["run_id"],
                official_raw_sha256=raw_hash,
                instructions=PRODUCT_INSTRUCTIONS,
                scenarios=product_scenarios,
            ),
        )
        write_report(
            review_path,
            OfficialReviewPacket(
                schema_version="tablet-domain-review-annotation-packet-v1",
                packet_id=f"{raw['run_id']}-{annotator_id}-reviews",
                annotator_id=annotator_id,
                official_run_id=raw["run_id"],
                official_raw_sha256=raw_hash,
                instructions=REVIEW_INSTRUCTIONS,
                scenarios=review_scenarios,
            ),
        )
        for path in (product_path, review_path):
            packet_hashes[str(path.relative_to(output)).replace("\\", "/")] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }

    provenance_path = output / "private_provenance.json"
    provenance = {
        "schema_version": "tablet-domain-private-provenance-v1",
        "official_run_id": raw["run_id"],
        "official_raw_sha256": raw_hash,
        "analysis_sha256": _sha256(analysis_path),
        "systems": ["full", "no_memory", "fixed_upstream_no_review"],
        "top_k": 3,
        "scenarios": provenance_scenarios,
    }
    write_report(provenance_path, provenance)
    packet_hashes[provenance_path.name] = {
        "bytes": provenance_path.stat().st_size,
        "sha256": _sha256(provenance_path),
    }

    product_count = sum(
        len(item["products"]) for item in public_scenarios.values()
    )
    review_count = sum(len(item["reviews"]) for item in public_scenarios.values())
    manifest = {
        "schema_version": "tablet-domain-annotation-packet-build-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "official_run_id": raw["run_id"],
        "official_raw_sha256": raw_hash,
        "analysis_sha256": _sha256(analysis_path),
        "annotator_count": args.annotators,
        "pooled_scenario_count": len(public_scenarios),
        "skipped_no_output_scenarios": skipped_scenarios,
        "unique_product_judgment_count_per_annotator": product_count,
        "unique_review_judgment_count_per_annotator": review_count,
        "all_grades_blank": True,
        "public_packets_hide_system_rank_and_score": True,
        "private_provenance_not_for_annotators": True,
        "files": packet_hashes,
    }
    write_report(output / "packet_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--analysis", type=Path, default=DEFAULT_ANALYSIS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--annotators", type=int, default=3)
    args = parser.parse_args()
    if args.annotators != 3:
        raise ValueError("the frozen official packet requires exactly three annotators")
    manifest = build(args)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"packet_dir={args.output.resolve()}")


if __name__ == "__main__":
    main()
