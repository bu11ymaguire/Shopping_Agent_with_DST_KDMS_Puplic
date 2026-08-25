"""Evaluate frozen real-catalog product ranking and review retrieval judgments."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.evaluation.actual_recommendation import (  # noqa: E402
    aggregate_recommendation_case_metrics,
    aggregate_review_case_metrics,
    build_recommendation_eval_state,
    load_actual_recommendation_eval_dataset,
    product_satisfies_hard_filters,
    score_graded_ranking,
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

DEFAULT_DATASET = BACKEND_ROOT / "data" / "actual_recommendation_eval_v1.json"
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "actual_recommendation_eval_v1.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rounded_metrics(metrics: dict[str, float | int]) -> dict[str, float | int]:
    return {
        key: round(value, 6) if isinstance(value, float) else value
        for key, value in metrics.items()
    }


def _evaluate_product_cases(
    *,
    dataset: Any,
    catalog: ExperimentalAmazonCatalog,
    retriever: Any,
    mode: str,
) -> tuple[list[dict[str, Any]], float]:
    case_results: list[dict[str, Any]] = []
    started = time.perf_counter()
    for case in dataset.product_cases:
        state = build_recommendation_eval_state(case)
        policy = select_actual_policy(state)
        if policy.lane != "recommend-lane":
            raise RuntimeError(f"{case.id} unexpectedly selected {policy.lane}")
        query = generate_actual_query(state)
        if query.hard_filters != case.expected_hard_filters:
            raise RuntimeError(f"{case.id} hard filters differ from the frozen contract")
        products, reviews, browse = browse_actual_catalog(
            query,
            catalog,
            rejected_product_ids=set(),
            review_retriever=retriever,
        )
        if mode == "semantic" and browse.review_retrieval_method != "semantic_cross_encoder":
            raise RuntimeError(
                f"{case.id} semantic evaluation fell back: "
                f"{browse.review_retrieval_fallback_reason}"
            )
        rankings, visible_reviews = rank_actual_products(
            state,
            query,
            products,
            reviews,
        )
        ranked_ids = [item.product_id for item in rankings]
        judgments = {
            item.parent_asin: item.relevance for item in case.product_judgments
        }
        top3 = score_graded_ranking(ranked_ids, judgments, k=3)
        top10 = score_graded_ranking(ranked_ids, judgments, k=10)

        product_by_id = {product.parent_asin: product for product in products}
        hard_filter_violations = [
            item.product_id
            for item in rankings
            if not product_satisfies_hard_filters(
                product_by_id[item.product_id],
                query.hard_filters,
                allow_budget_overrun=query.allow_budget_overrun,
            )
        ]
        visible_by_id = {review.review_id: review for review in visible_reviews}
        inconsistent_evidence = [
            item.product_id
            for item in rankings
            if any(
                review_id not in visible_by_id
                or visible_by_id[review_id].parent_asin != item.product_id
                for review_id in item.evidence_review_ids
            )
        ]
        visible_ids = set(visible_by_id)
        referenced_ids = {
            review_id
            for item in rankings
            for review_id in item.evidence_review_ids
        }
        if visible_ids != referenced_ids:
            inconsistent_evidence.append("visible_review_union_mismatch")

        metrics: dict[str, float | int] = {
            **top3,
            **top10,
            "hard_filter_violation_rate": (
                len(hard_filter_violations) / len(rankings) if rankings else 0.0
            ),
            "evidence_consistency_rate": 0.0 if inconsistent_evidence else 1.0,
        }
        case_results.append(
            {
                "case_id": case.id,
                "candidate_product_count": browse.candidate_product_count,
                "retrieved_review_count": browse.retrieved_review_count,
                "retrieval_method": browse.review_retrieval_method,
                "retrieval_fallback_reason": browse.review_retrieval_fallback_reason,
                "ranked_product_ids": ranked_ids,
                "hard_filter_violation_ids": hard_filter_violations,
                "inconsistent_evidence": inconsistent_evidence,
                "metrics": _rounded_metrics(metrics),
                **metrics,
            }
        )
    return case_results, (time.perf_counter() - started) * 1000


def _evaluate_review_cases(
    *,
    dataset: Any,
    catalog: ExperimentalAmazonCatalog,
    retriever: Any,
    mode: str,
) -> tuple[list[dict[str, Any]], float]:
    case_results: list[dict[str, Any]] = []
    started = time.perf_counter()
    for case in dataset.review_cases:
        result = retriever.retrieve(catalog, [case.parent_asin], case.query)
        if mode == "semantic" and result.method != "semantic_cross_encoder":
            raise RuntimeError(
                f"{case.id} semantic evaluation fell back: {result.fallback_reason}"
            )
        ranked_ids = [review.review_id for review in result.reviews]
        judgments = {
            item.review_id: item.relevance for item in case.review_judgments
        }
        metrics = score_graded_ranking(ranked_ids, judgments, k=3)
        case_results.append(
            {
                "case_id": case.id,
                "parent_asin": case.parent_asin,
                "retrieval_method": result.method,
                "retrieval_fallback_reason": result.fallback_reason,
                "ranked_review_ids": ranked_ids,
                "metrics": _rounded_metrics(metrics),
                **metrics,
            }
        )
    return case_results, (time.perf_counter() - started) * 1000


def execute(args: argparse.Namespace) -> Path:
    dataset = load_actual_recommendation_eval_dataset(args.dataset)
    catalog = ExperimentalAmazonCatalog()
    if not catalog.available:
        raise RuntimeError(catalog.status().unavailable_reason)

    requested_modes = ["token", "semantic"] if args.mode == "both" else [args.mode]
    settings = load_review_retrieval_settings()
    mode_reports: dict[str, Any] = {}
    for mode in requested_modes:
        retriever = build_review_retriever(replace(settings, mode=mode))
        product_cases, product_latency_ms = _evaluate_product_cases(
            dataset=dataset,
            catalog=catalog,
            retriever=retriever,
            mode=mode,
        )
        review_cases, review_latency_ms = _evaluate_review_cases(
            dataset=dataset,
            catalog=catalog,
            retriever=retriever,
            mode=mode,
        )
        mode_reports[mode] = {
            "product_ranking": {
                "metrics": _rounded_metrics(
                    aggregate_recommendation_case_metrics(product_cases)
                ),
                "latency_ms": round(product_latency_ms, 1),
                "cases": [
                    {key: value for key, value in result.items() if key not in {
                        "ndcg_at_3", "ndcg_at_10", "recall_at_3", "recall_at_10",
                        "precision_at_3", "precision_at_10", "judgment_coverage_at_3",
                        "judgment_coverage_at_10", "relevant_retrieved_at_3",
                        "relevant_retrieved_at_10", "judged_relevant_count",
                        "hard_filter_violation_rate", "evidence_consistency_rate",
                    }}
                    for result in product_cases
                ],
            },
            "review_retrieval": {
                "metrics": _rounded_metrics(
                    aggregate_review_case_metrics(review_cases)
                ),
                "latency_ms": round(review_latency_ms, 1),
                "cases": [
                    {key: value for key, value in result.items() if key not in {
                        "ndcg_at_3", "recall_at_3", "precision_at_3",
                        "judgment_coverage_at_3", "relevant_retrieved_at_3",
                        "judged_relevant_count",
                    }}
                    for result in review_cases
                ],
            },
        }

    catalog_manifest = catalog.catalog_dir / "manifest.json"
    semantic_manifest = catalog.catalog_dir / "review_semantic_manifest.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_protocol": (
            "The fixture and pooled 0-3 judgments were frozen before this first "
            "baseline run. Unjudged results score zero; coverage is reported."
        ),
        "dataset_version": dataset.dataset_version,
        "dataset_revision": dataset.dataset_revision,
        "dataset_sha256": _sha256(args.dataset),
        "catalog_schema_version": dataset.catalog_schema_version,
        "catalog_manifest_sha256": _sha256(catalog_manifest),
        "semantic_manifest_sha256": (
            _sha256(semantic_manifest) if semantic_manifest.is_file() else None
        ),
        "modes": mode_reports,
    }
    write_report(args.output, payload)
    print(json.dumps({
        "status": "ok",
        "dataset_sha256": payload["dataset_sha256"],
        "modes": {
            mode: {
                "product_ranking": report["product_ranking"]["metrics"],
                "review_retrieval": report["review_retrieval"]["metrics"],
            }
            for mode, report in mode_reports.items()
        },
        "output": str(args.output.resolve()),
    }, ensure_ascii=False, indent=2))
    return args.output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--mode", choices=("token", "semantic", "both"), default="both"
    )
    execute(parser.parse_args())


if __name__ == "__main__":
    main()
