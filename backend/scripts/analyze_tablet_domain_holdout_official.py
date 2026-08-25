"""Deterministically analyze the immutable official tablet holdout raw report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.actual_recommendation import (  # noqa: E402
    product_satisfies_hard_filters,
)
from app.evaluation.tablet_domain_official import (  # noqa: E402
    aggregate_condition_metrics,
    compare_condition_outputs,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.experiment_conditions import (  # noqa: E402
    rank_products_without_review_contribution,
)
from app.llm import write_report  # noqa: E402
from app.models.actual_demo import ActualHardFilters, ActualPipelineTurn  # noqa: E402

DEFAULT_RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
DEFAULT_OUTPUT = (
    BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1_analysis.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _candidate_products(turn: ActualPipelineTurn, catalog: ExperimentalAmazonCatalog):
    if turn.query is None:
        return []
    filters = turn.query.hard_filters
    result = catalog.search_products(
        max_price_usd=(None if turn.query.allow_budget_overrun else filters.max_price_usd),
        min_storage_gb=filters.min_storage_gb,
        min_memory_gb=filters.min_memory_gb,
        max_weight_grams=filters.max_weight_grams,
        min_rating=filters.min_rating,
        min_screen_inches=filters.min_screen_inches,
        operating_system=filters.operating_system,
        sort_by="review_count",
        limit=300,
    )
    rejected = {item.product_id for item in turn.dialogue_state.rejected_items}
    return [
        product for product in result.products if product.parent_asin not in rejected
    ]


def fixed_upstream_no_review(
    raw: dict[str, Any], catalog: ExperimentalAmazonCatalog
) -> dict[str, Any]:
    """Remove review contribution from Full while holding every upstream output fixed."""
    records = []
    top3_exact = 0
    top1_changed = 0
    jaccards = []
    evaluated_products = 0
    hard_violations = 0
    candidate_count_exact = 0
    for scenario in raw["conditions"]["full"]["scenarios"]:
        for metric, payload in zip(
            scenario.get("turn_metrics", []), scenario.get("turns", []), strict=True
        ):
            turn = ActualPipelineTurn.model_validate(payload)
            if turn.query is None or turn.browse_result is None:
                continue
            products = _candidate_products(turn, catalog)
            candidate_count_exact += (
                len(products) == turn.browse_result.candidate_product_count
            )
            rankings, reviews = rank_products_without_review_contribution(
                turn.dialogue_state,
                turn.query,
                products,
                [],
            )
            if reviews:
                raise AssertionError("fixed-upstream No-review exposed review rows")
            if any(
                ranking.evidence_review_ids
                or ranking.score.review_evidence_score
                or ranking.score.evidence_reliability
                for ranking in rankings
            ):
                raise AssertionError("fixed-upstream No-review retained review contribution")

            full_top = [item.product_id for item in turn.rankings[:3]]
            no_review_top = [item.product_id for item in rankings[:3]]
            top3_exact += full_top == no_review_top
            top1_changed += bool(
                full_top and no_review_top and full_top[0] != no_review_top[0]
            )
            union = set(full_top) | set(no_review_top)
            if union:
                jaccards.append(len(set(full_top) & set(no_review_top)) / len(union))

            expected_filters = ActualHardFilters.model_validate(
                metric["hard_filters_expected"]
            )
            allow_budget_overrun = turn.query.allow_budget_overrun
            violations = 0
            for ranking in rankings[:3]:
                evaluated_products += 1
                if not product_satisfies_hard_filters(
                    catalog.get_product(ranking.product_id),
                    expected_filters,
                    allow_budget_overrun=allow_budget_overrun,
                ):
                    violations += 1
                    hard_violations += 1
            records.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "turn": metric["turn"],
                    "candidate_product_count": len(products),
                    "full_top10": [item.product_id for item in turn.rankings[:10]],
                    "fixed_upstream_no_review_top10": [
                        item.product_id for item in rankings[:10]
                    ],
                    "top3_hard_constraint_violations": violations,
                }
            )

    count = len(records)
    return {
        "contract": {
            "source": "validated Full turn output",
            "persistent_state": True,
            "query_and_candidate_set": "held fixed from Full",
            "review_score_and_reliability": 0,
            "additional_llm_calls": 0,
            "reason": (
                "The independently replayed live No-review condition had non-identical "
                "Understanding outputs, so it cannot isolate review contribution alone."
            ),
        },
        "turn_count": count,
        "candidate_count_identity_rate": _rate(candidate_count_exact, count),
        "top3_order_identity_rate": _rate(top3_exact, count),
        "top1_change_rate": _rate(top1_changed, count),
        "mean_top3_jaccard": sum(jaccards) / len(jaccards) if jaccards else None,
        "hard_constraint_violation_rate": _rate(hard_violations, evaluated_products),
        "records": records,
    }


def error_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for condition, payload in raw["conditions"].items():
        over = Counter()
        missing = Counter()
        hard_mismatches = []
        error_types = Counter()
        error_signatures = Counter()
        for scenario in payload["scenarios"]:
            if scenario["status"] != "completed":
                error_types[scenario.get("error_type") or "unknown"] += 1
                message = scenario.get("error_message") or ""
                if "preference IDs require scope" in message:
                    error_signatures["facet_scope_schema_failure"] += 1
                elif "duplicate canonical_id" in message:
                    error_signatures["duplicate_canonical_id"] += 1
                elif "item_action requires its matching intent" in message:
                    error_signatures["item_action_intent_mismatch"] += 1
                else:
                    error_signatures["other"] += 1
            for turn in scenario.get("turn_metrics", []):
                over.update(
                    set(turn["candidate_ids_actual"])
                    - set(turn["candidate_ids_expected"])
                )
                missing.update(
                    set(turn["candidate_ids_expected"])
                    - set(turn["candidate_ids_actual"])
                )
                if not turn["hard_filter_exact"]:
                    hard_mismatches.append(
                        {
                            "scenario_id": scenario["scenario_id"],
                            "turn": turn["turn"],
                            "expected": turn["hard_filters_expected"],
                            "actual": turn["hard_filters_actual"],
                            "top3_violations": turn[
                                "top3_hard_constraint_violations"
                            ],
                        }
                    )
        result[condition] = {
            "error_types": dict(error_types),
            "error_signatures": dict(error_signatures),
            "most_common_over_extractions": over.most_common(),
            "most_common_missing_ids": missing.most_common(),
            "hard_filter_mismatch_count": len(hard_mismatches),
            "hard_filter_mismatches": hard_mismatches,
        }
    return result


def analyze(raw_path: Path, output_path: Path) -> dict[str, Any]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    if raw["status"] != "completed":
        raise RuntimeError("official raw report is not complete")
    recomputed = {
        condition: aggregate_condition_metrics(payload["scenarios"])
        for condition, payload in raw["conditions"].items()
    }
    for condition, metrics in recomputed.items():
        if metrics != raw["conditions"][condition]["metrics"]:
            raise AssertionError(f"condition metrics do not reproduce: {condition}")

    catalog = ExperimentalAmazonCatalog()
    if not catalog.available:
        raise RuntimeError("frozen local catalog is unavailable")
    analysis = {
        "schema_version": "tablet-domain-official-holdout-analysis-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": raw["run_id"],
        "raw_report_sha256": _sha256(raw_path),
        "dataset_sha256": raw["frozen_inputs"]["dataset_sha256"],
        "condition_metrics": recomputed,
        "paired_comparisons": compare_condition_outputs(raw),
        "fixed_upstream_no_review": fixed_upstream_no_review(raw, catalog),
        "error_analysis": error_analysis(raw),
        "interpretation_guardrails": [
            "State Diff is evaluated as per-turn update operations; final-state F1 separately measures retained accumulated state.",
            "Full and No-memory differences are reported on paired completed turns as well as full condition totals.",
            "The independent live No-review replay is a secondary robustness observation because upstream identity was below 1.0.",
            "RQ3 uses the deterministic fixed-upstream No-review counterfactual for pooling and later human NDCG.",
            "No NDCG or relevance claim is made before blind human annotation.",
        ],
    }
    write_report(output_path, analysis)
    return analysis


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    analysis = analyze(args.raw.resolve(), args.output.resolve())
    summary = {
        "raw_report_sha256": analysis["raw_report_sha256"],
        "paired_comparisons": analysis["paired_comparisons"],
        "fixed_upstream_no_review": {
            key: value
            for key, value in analysis["fixed_upstream_no_review"].items()
            if key != "records"
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"analysis_report={args.output.resolve()}")


if __name__ == "__main__":
    main()
