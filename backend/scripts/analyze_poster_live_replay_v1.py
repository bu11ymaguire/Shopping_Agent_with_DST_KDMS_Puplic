"""Derive slot, routing, safety, and node-latency metrics from frozen v1 traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.poster_annotation import load_poster_scenario_dataset  # noqa: E402
from app.llm import write_report  # noqa: E402

DEFAULT_RAW = BACKEND_ROOT / "reports" / "poster_scenarios_live_v1.json"
DEFAULT_DATASET = BACKEND_ROOT / "data" / "poster_recommendation_scenarios_v1.json"
DEFAULT_OUTPUT = (
    BACKEND_ROOT / "data" / "manifests" / "poster_live_replay_v1_analysis.json"
)

LATENCY_GROUPS = {
    "understanding_llm": {"spn-understanding"},
    "state_policy": {"ra-state-manager", "spn-policy"},
    "query_generation": {"ra-query-generator"},
    "retrieval_and_cross_encoder": {"spn-browsing-actions"},
    "ranking": {"ra-recommendation-engine"},
    "response_composer_llm": {
        "spn-response-clarify",
        "spn-response-recommend",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "observation_count": len(values),
        "median_ms": statistics.median(values) if values else None,
        "p95_ms": _percentile(values, 0.95),
        "max_ms": max(values) if values else None,
    }


def _slot(target: Any) -> str:
    kind = target.kind if hasattr(target, "kind") else target["kind"]
    if kind == "category":
        return "category"
    if kind == "facet":
        facet = target.facet if hasattr(target, "facet") else target["facet"]
        return f"facet.{facet}"
    scope = target.scope if hasattr(target, "scope") else target["scope"]
    return f"constraint.{scope}"


def _sets_by_slot(candidates: list[Any]) -> dict[str, set[str]]:
    output: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        target = candidate.target if hasattr(candidate, "target") else candidate["target"]
        canonical_id = (
            candidate.canonical_id
            if hasattr(candidate, "canonical_id")
            else candidate["canonical_id"]
        )
        slot = _slot(target)
        output[slot].add(canonical_id)
        output["all"].add(canonical_id)
        if slot.startswith("facet."):
            output["facet.all"].add(canonical_id)
    return output


def _micro_slot_metrics(
    pairs: list[tuple[dict[str, set[str]], dict[str, set[str]]]],
) -> dict[str, dict[str, float | int | None]]:
    slots = sorted(
        {
            slot
            for expected, predicted in pairs
            for slot in {*expected, *predicted}
        }
    )
    result = {}
    for slot in slots:
        tp = fp = fn = 0
        for expected, predicted in pairs:
            expected_ids = expected.get(slot, set())
            predicted_ids = predicted.get(slot, set())
            tp += len(expected_ids & predicted_ids)
            fp += len(predicted_ids - expected_ids)
            fn += len(expected_ids - predicted_ids)
        precision = _rate(tp, tp + fp)
        recall = _rate(tp, tp + fn)
        result[slot] = {
            "true_positive": tp,
            "false_positive": fp,
            "false_negative": fn,
            "precision": precision,
            "recall": recall,
            "f1": _f1(precision, recall),
        }
    return result


def analyze(raw_path: Path, dataset_path: Path) -> dict[str, object]:
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    dataset = load_poster_scenario_dataset(dataset_path)
    scenario_by_id = {item.id: item for item in dataset.scenarios}
    records = raw["scenarios"]
    completed = [item for item in records if item["metrics"]["status"] == "completed"]

    completed_pairs = []
    all_pairs = []
    completed_over_extraction = 0
    completed_extra_id_count = 0
    category_tp_all = 0
    category_expected_all = 0
    category_tp_completed = 0
    category_expected_completed = 0
    for record in records:
        scenario = scenario_by_id[record["scenario_id"]]
        expected = _sets_by_slot(scenario.state_candidates)
        predicted_candidates = [
            candidate
            for turn in record["turns"]
            for candidate in turn["understanding"]["candidates"]
        ]
        predicted = _sets_by_slot(predicted_candidates)
        all_pairs.append((expected, predicted))
        expected_category = expected.get("category", set())
        predicted_category = predicted.get("category", set())
        category_expected_all += len(expected_category)
        category_tp_all += len(expected_category & predicted_category)
        if record["metrics"]["status"] == "completed":
            completed_pairs.append((expected, predicted))
            category_expected_completed += len(expected_category)
            category_tp_completed += len(expected_category & predicted_category)
            extra_ids = predicted.get("all", set()) - expected.get("all", set())
            if extra_ids:
                completed_over_extraction += 1
                completed_extra_id_count += len(extra_ids)

    reached = [
        item for item in records if item["metrics"]["final_lane"] == "recommend-lane"
    ]
    hard_filter_correct = [
        item for item in reached if item["metrics"]["hard_filter_exact"]
    ]
    expected_products = sum(
        item["metrics"]["expected_filter_product_count"] for item in records
    )
    expected_violations = sum(
        item["metrics"]["expected_filter_violation_count"] for item in records
    )

    node_values: dict[str, list[float]] = defaultdict(list)
    wall_values = []
    trace_values = []
    unattributed_values = []
    for record in records:
        latencies = record["metrics"]["turn_wall_latency_ms"]
        for turn, wall_latency in zip(record["turns"], latencies, strict=True):
            wall_values.append(float(wall_latency))
            trace_total = sum(float(trace["latency_ms"]) for trace in turn["trace"])
            trace_values.append(trace_total)
            unattributed_values.append(max(0.0, float(wall_latency) - trace_total))
            for group, node_ids in LATENCY_GROUPS.items():
                group_latency = sum(
                    float(trace["latency_ms"])
                    for trace in turn["trace"]
                    if trace["node_id"] in node_ids
                )
                if group_latency:
                    node_values[group].append(group_latency)

    slot_metrics = _micro_slot_metrics(completed_pairs)
    analysis = {
        "schema_version": "poster-live-replay-v1-analysis",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_report_sha256": _sha256(raw_path),
        "dataset_sha256": _sha256(dataset_path),
        "evaluation_population": {
            "scenario_count": len(records),
            "completed_scenario_count": len(completed),
            "expected_turn_count": sum(
                item["metrics"]["expected_turn_count"] for item in records
            ),
            "completed_turn_count": sum(
                item["metrics"]["completed_turn_count"] for item in records
            ),
        },
        "routing_and_completion": {
            "recommend_lane_reach_count": len(reached),
            "recommend_lane_reach_rate": _rate(len(reached), len(records)),
            "schema_failure_scenario_count": len(records) - len(completed),
            "schema_failure_scenario_rate": _rate(
                len(records) - len(completed), len(records)
            ),
            "schema_failure_turn_rate": _rate(
                sum(item["metrics"]["expected_turn_count"] for item in records)
                - sum(item["metrics"]["completed_turn_count"] for item in records),
                sum(item["metrics"]["expected_turn_count"] for item in records),
            ),
        },
        "hard_filter": {
            "end_to_end_completion_count": len(hard_filter_correct),
            "end_to_end_completion_rate": _rate(
                len(hard_filter_correct), len(records)
            ),
            "conditional_correct_count": len(hard_filter_correct),
            "conditional_recommend_count": len(reached),
            "conditional_correctness": _rate(len(hard_filter_correct), len(reached)),
            "recommended_product_count": expected_products,
            "expected_filter_violation_count": expected_violations,
            "expected_filter_violation_rate": _rate(
                expected_violations, expected_products
            ),
        },
        "category_extraction": {
            "all_scenario_true_positive": category_tp_all,
            "all_scenario_expected": category_expected_all,
            "all_scenario_recall": _rate(category_tp_all, category_expected_all),
            "completed_scenario_true_positive": category_tp_completed,
            "completed_scenario_expected": category_expected_completed,
            "completed_scenario_recall": _rate(
                category_tp_completed, category_expected_completed
            ),
        },
        "completed_scenario_slot_metrics": slot_metrics,
        "completed_scenario_over_extraction": {
            "scenario_count": completed_over_extraction,
            "scenario_rate": _rate(completed_over_extraction, len(completed)),
            "extra_canonical_id_count": completed_extra_id_count,
        },
        "latency_ms": {
            "turn_wall": _latency_summary(wall_values),
            "trace_total": _latency_summary(trace_values),
            "unattributed_wrapper_overhead": _latency_summary(unattributed_values),
            "groups": {
                group: _latency_summary(values)
                for group, values in node_values.items()
            },
            "instrumentation_limit": (
                "v1 records semantic retrieval and Cross-Encoder together inside "
                "spn-browsing-actions; v2 must time them separately."
            ),
        },
        "interpretation": [
            "recommend-lane reach is a completion metric, not recommendation relevance.",
            "Conditional hard-filter correctness is evaluated only after recommend-lane is reached.",
            "Canonical exact is secondary because any additional subjective ID makes the whole scenario non-exact.",
            "The v1 category metric is diagnostic only; v2 fixes tablet as environment state and evaluates unsupported routing separately.",
        ],
    }
    return analysis


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() and not args.force:
        raise RuntimeError(f"refusing to overwrite existing analysis: {output}")
    analysis = analyze(args.raw.resolve(), args.dataset.resolve())
    write_report(output, analysis)
    print(json.dumps(analysis, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
