"""Verify aggregation and contracts for the live poster-scenario replay."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.poster_live_replay import (  # noqa: E402
    PosterLiveScenarioMetrics,
    aggregate_live_replay_metrics,
)
from app.models.actual_demo import ActualHardFilters  # noqa: E402

DATASET_PATH = BACKEND_ROOT / "data" / "poster_recommendation_scenarios_v1.json"
MANIFEST_PATH = (
    BACKEND_ROOT / "data" / "manifests" / "poster_live_replay_luxia_v1.json"
)
ANALYSIS_PATH = (
    BACKEND_ROOT / "data" / "manifests" / "poster_live_replay_v1_analysis.json"
)
RAW_REPORT_PATH = BACKEND_ROOT / "reports" / "poster_scenarios_live_v1.json"
RUNNER_PATH = BACKEND_ROOT / "scripts" / "run_poster_scenarios_live.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(condition: bool, label: str, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail!r}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def _metric(
    scenario_id: str,
    *,
    exact: bool,
    tp: int,
    fp: int,
    fn: int,
    expected_violation: int = 0,
) -> PosterLiveScenarioMetrics:
    filters = ActualHardFilters(max_price_usd=300)
    return PosterLiveScenarioMetrics(
        scenario_id=scenario_id,
        status="completed",
        expected_turn_count=4,
        completed_turn_count=4,
        turn_wall_latency_ms=[100, 200, 300, 400],
        expected_candidate_ids=["budget", "category_tablet"],
        extracted_candidate_ids=["budget", "category_tablet"],
        canonical_id_exact=exact,
        canonical_true_positive=tp,
        canonical_false_positive=fp,
        canonical_false_negative=fn,
        expected_hard_filters=filters,
        actual_hard_filters=filters,
        hard_filter_exact=True,
        final_lane="recommend-lane",
        final_lane_expected=True,
        final_recommendation_count=3,
        response_fallback_count=0,
        recommend_turn_count=3,
        retrieval_fallback_count=0,
        actual_filter_product_count=3,
        actual_filter_violation_count=0,
        expected_filter_product_count=3,
        expected_filter_violation_count=expected_violation,
        evidence_card_count=3,
        evidence_mismatch_count=0,
    )


def main() -> None:
    summary = aggregate_live_replay_metrics(
        [
            _metric("ph01", exact=True, tp=2, fp=0, fn=0),
            _metric(
                "ph02",
                exact=False,
                tp=1,
                fp=1,
                fn=1,
                expected_violation=1,
            ),
        ]
    )
    _check(summary["scenario_completion_rate"] == 1.0, "scenario completion")
    _check(summary["turn_completion_rate"] == 1.0, "turn completion")
    _check(summary["schema_failure_rate"] == 0.0, "schema failure rate")
    _check(
        summary["end_to_end_recommend_success_rate"] == 1.0,
        "end-to-end recommend success",
    )
    _check(summary["canonical_id_exact_rate"] == 0.5, "canonical exact")
    _check(summary["canonical_id_micro_precision"] == 0.75, "canonical precision")
    _check(summary["canonical_id_micro_recall"] == 0.75, "canonical recall")
    _check(summary["canonical_id_micro_f1"] == 0.75, "canonical F1")
    _check(summary["hard_filter_exact_rate"] == 1.0, "hard-filter exact")
    _check(
        summary["expected_hard_filter_violation_rate"] == 1 / 6,
        "expected hard-filter violations",
    )
    _check(summary["evidence_consistency_rate"] == 1.0, "evidence consistency")
    _check(summary["turn_wall_latency_ms"]["median"] == 250, "latency median")
    _check(summary["turn_wall_latency_ms"]["p95"] == 400, "latency p95")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    frozen = manifest["metrics"]
    _check(
        manifest["schema_version"] == "poster-live-replay-run-manifest-v1",
        "frozen manifest schema",
    )
    _check(manifest["dataset_sha256"] == _sha256(DATASET_PATH), "dataset hash")
    _check(manifest["runner_sha256"] == _sha256(RUNNER_PATH), "runner hash")
    _check(frozen["scenario_count"] == 20, "frozen scenario count")
    _check(frozen["completed_turn_count"] == 79, "frozen completed turns")
    _check(frozen["schema_failure_rate"] == 0.05, "frozen schema failure")
    _check(
        frozen["end_to_end_recommend_success_rate"] == 0.4,
        "frozen end-to-end success",
    )
    _check(frozen["canonical_id_micro_f1"] > 0.65, "frozen canonical F1")
    _check(
        frozen["expected_hard_filter_violation_rate"] == 0.0,
        "frozen expected-filter safety",
    )
    _check(frozen["evidence_consistency_rate"] == 1.0, "frozen evidence safety")
    analysis = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
    _check(
        analysis["raw_report_sha256"] == manifest["raw_report_sha256"],
        "analysis uses frozen raw report",
    )
    _check(
        analysis["routing_and_completion"]["recommend_lane_reach_rate"] == 0.4,
        "recommend-lane reach rate",
    )
    _check(
        analysis["hard_filter"]["conditional_correctness"] == 1.0,
        "conditional hard-filter correctness",
    )
    _check(
        analysis["category_extraction"]["completed_scenario_recall"]
        == 8 / 19,
        "completed-scenario category recall",
    )
    _check(
        analysis["completed_scenario_slot_metrics"]["constraint.hard"]["f1"]
        == 1.0,
        "hard-constraint slot F1",
    )
    _check(
        analysis["completed_scenario_over_extraction"]["extra_canonical_id_count"]
        == 35,
        "over-extracted canonical IDs",
    )
    if RAW_REPORT_PATH.exists():
        _check(
            manifest["raw_report_sha256"] == _sha256(RAW_REPORT_PATH),
            "local raw report hash",
        )
        raw = json.loads(RAW_REPORT_PATH.read_text(encoding="utf-8"))
        raw_metrics = [
            PosterLiveScenarioMetrics.model_validate(item["metrics"])
            for item in raw["scenarios"]
        ]
        _check(
            aggregate_live_replay_metrics(raw_metrics) == frozen,
            "raw report reproduces frozen metrics",
        )
    print("[PASS] poster live replay metrics")


if __name__ == "__main__":
    main()
