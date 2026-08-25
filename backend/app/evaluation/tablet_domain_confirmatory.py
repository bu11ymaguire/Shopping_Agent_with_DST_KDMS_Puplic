"""Prespecified scoring for the untouched M0-versus-C confirmatory experiment."""

from __future__ import annotations

import random
from typing import Any

from app.evaluation.tablet_domain_extended import (
    aggregate_extended_metrics,
    score_extended_turn,
)
from app.evaluation.tablet_domain_holdout import TabletHoldoutTurn
from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models.actual_demo import ActualPipelineTurn

CONFIRMATORY_CORRECTION_TURNS = frozenset(
    {
        ("th21", 4),
        ("th22", 4),
        ("th23", 4),
        ("th24", 4),
        ("th25", 4),
        ("th26", 4),
        ("th27", 4),
        ("th28", 4),
    }
)

NON_INFERIORITY_MARGINS = {
    "final_state_f1": 0.02,
    "correction_recall": 0.05,
    "hard_filter_completion": 0.02,
    "turn_output_completion": 0.02,
}

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20_260_817


def score_confirmatory_turn(
    *,
    scenario_id: str,
    gold: TabletHoldoutTurn,
    turn: ActualPipelineTurn,
    catalog: ExperimentalAmazonCatalog,
    wall_latency_ms: float,
    gold_active_ids: set[str],
    prior_active_ids: set[str],
) -> dict[str, Any]:
    result = score_extended_turn(
        scenario_id=scenario_id,
        gold=gold,
        turn=turn,
        catalog=catalog,
        wall_latency_ms=wall_latency_ms,
        gold_active_ids=gold_active_ids,
        prior_active_ids=prior_active_ids,
    )
    result["is_correction_turn"] = (
        scenario_id,
        gold.turn,
    ) in CONFIRMATORY_CORRECTION_TURNS
    return result


def aggregate_confirmatory_metrics(
    scenario_records: list[dict[str, Any]],
) -> dict[str, Any]:
    return aggregate_extended_metrics(scenario_records)


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    denominator = 2 * true_positive + false_positive + false_negative
    return 2 * true_positive / denominator if denominator else 1.0


def _scenario_state_diff_f1(record: dict[str, Any]) -> float:
    true_positive = sum(
        turn["state_diff_counts"]["true_positive"]
        for turn in record.get("turn_metrics", [])
    )
    false_positive = sum(
        turn["state_diff_counts"]["false_positive"]
        for turn in record.get("turn_metrics", [])
    )
    false_negative = sum(
        turn["state_diff_counts"]["false_negative"]
        for turn in record.get("turn_metrics", [])
    ) + sum(
        len(turn["gold_state_diff"])
        for turn in record.get("missing_gold_turns", [])
    )
    return _f1(true_positive, false_positive, false_negative)


def paired_bootstrap_state_diff(
    baseline_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    baseline = {
        record["scenario_id"]: _scenario_state_diff_f1(record)
        for record in baseline_records
    }
    candidate = {
        record["scenario_id"]: _scenario_state_diff_f1(record)
        for record in candidate_records
    }
    if set(baseline) != set(candidate):
        raise ValueError("paired bootstrap requires identical scenario IDs")
    scenario_ids = sorted(baseline)
    differences = [candidate[item] - baseline[item] for item in scenario_ids]
    observed = sum(differences) / len(differences)
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        sample = [differences[rng.randrange(len(differences))] for _ in scenario_ids]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    lower_index = int(0.025 * resamples)
    upper_index = min(resamples - 1, int(0.975 * resamples))
    return {
        "estimand": "paired mean scenario State Diff F1 difference (C minus M0)",
        "scenario_count": len(scenario_ids),
        "resamples": resamples,
        "seed": seed,
        "observed_difference": observed,
        "confidence_interval_95": [draws[lower_index], draws[upper_index]],
        "per_scenario": {
            scenario_id: {
                "m0": baseline[scenario_id],
                "c": candidate[scenario_id],
                "difference": candidate[scenario_id] - baseline[scenario_id],
            }
            for scenario_id in scenario_ids
        },
    }


def _retention_checks(
    baseline: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, bool]:
    baseline_diag = baseline["extended_diagnostics"]
    candidate_diag = candidate["extended_diagnostics"]
    return {
        "final_state_f1": candidate["final_state_micro"]["f1"]
        >= baseline["final_state_micro"]["f1"]
        - NON_INFERIORITY_MARGINS["final_state_f1"],
        "correction_recall": candidate_diag["correction_candidate_recall"]
        >= baseline_diag["correction_candidate_recall"]
        - NON_INFERIORITY_MARGINS["correction_recall"],
        "hard_filter_completion": candidate["hard_constraint_completion_rate"]
        >= baseline["hard_constraint_completion_rate"]
        - NON_INFERIORITY_MARGINS["hard_filter_completion"],
        "turn_output_completion": candidate["turn_output_completion_rate"]
        >= baseline["turn_output_completion_rate"]
        - NON_INFERIORITY_MARGINS["turn_output_completion"],
    }


def evaluate_confirmatory_decision(
    *,
    m0_metrics: dict[str, Any],
    replay_c_metrics: dict[str, Any],
    live_c_metrics: dict[str, Any],
    fixed_upstream_bootstrap: dict[str, Any],
    independent_live_bootstrap: dict[str, Any],
) -> dict[str, Any]:
    fixed_retention = _retention_checks(m0_metrics, replay_c_metrics)
    live_retention = _retention_checks(m0_metrics, live_c_metrics)
    fixed_ci = fixed_upstream_bootstrap["confidence_interval_95"]
    live_ci = independent_live_bootstrap["confidence_interval_95"]
    fixed_checks = {
        "paired_state_diff_f1_superiority": fixed_upstream_bootstrap[
            "observed_difference"
        ]
        > 0,
        "paired_bootstrap_lower_bound_above_zero": fixed_ci[0] > 0,
        "material_state_diff_fp_reduction": replay_c_metrics[
            "extended_diagnostics"
        ]["material_state_diff_false_positive_count"]
        < m0_metrics["extended_diagnostics"][
            "material_state_diff_false_positive_count"
        ],
        **{f"retention_{key}": value for key, value in fixed_retention.items()},
    }
    live_checks = {
        "paired_state_diff_f1_superiority": independent_live_bootstrap[
            "observed_difference"
        ]
        > 0,
        "paired_bootstrap_lower_bound_above_zero": live_ci[0] > 0,
        "material_state_diff_fp_reduction": live_c_metrics[
            "extended_diagnostics"
        ]["material_state_diff_false_positive_count"]
        < m0_metrics["extended_diagnostics"][
            "material_state_diff_false_positive_count"
        ],
        **{f"retention_{key}": value for key, value in live_retention.items()},
    }
    fixed_passed = all(fixed_checks.values())
    live_passed = all(live_checks.values())
    return {
        "status": "confirmatory_prespecified",
        "primary_estimand": fixed_upstream_bootstrap["estimand"],
        "non_inferiority_margins": NON_INFERIORITY_MARGINS,
        "fixed_upstream_replay": {
            "passed": fixed_passed,
            "checks": fixed_checks,
        },
        "independent_live_replication": {
            "passed": live_passed,
            "checks": live_checks,
        },
        "confirmed": fixed_passed and live_passed,
        "decision_rule": (
            "Confirm C only if fixed-upstream causal replay and independent live "
            "replication both show State Diff superiority with a positive paired "
            "bootstrap lower bound, fewer material false positives, and all four "
            "retention gates."
        ),
    }
