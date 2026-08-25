"""Deterministic metrics and predeclared selection for Extended_Experiment."""

from __future__ import annotations

import statistics
from typing import Any

from app.evaluation.tablet_domain_holdout import TabletHoldoutTurn
from app.evaluation.tablet_domain_official import (
    aggregate_condition_metrics,
    normalized_state_diff_operations,
    score_holdout_turn,
)
from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models.actual_demo import ActualPipelineTurn


CORRECTION_TURNS = frozenset({("th03", 4), ("th17", 3), ("th18", 3)})

SELECTION_THRESHOLDS = {
    "final_state_f1_non_inferiority_margin": 0.02,
    "correction_recall_non_inferiority_margin": 0.05,
    "hard_filter_completion_non_inferiority_margin": 0.02,
    "turn_output_completion_non_inferiority_margin": 0.02,
}


def _set_counts(expected: set[str], actual: set[str]) -> dict[str, int | bool]:
    return {
        "exact": expected == actual,
        "true_positive": len(expected & actual),
        "false_positive": len(actual - expected),
        "false_negative": len(expected - actual),
    }


def _micro(tp: int, fp: int, fn: int) -> dict[str, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def extended_state_diff_operations(
    turn: ActualPipelineTurn,
    *,
    prior_active_ids: set[str],
) -> set[str]:
    operations = normalized_state_diff_operations(turn)
    delete_ids = set(
        getattr(turn.understanding, "experimental_delete_ids", [])
    )
    operations.update(
        f"delete:{canonical_id}"
        for canonical_id in delete_ids & prior_active_ids
    )
    return operations


def score_extended_turn(
    *,
    scenario_id: str,
    gold: TabletHoldoutTurn,
    turn: ActualPipelineTurn,
    catalog: ExperimentalAmazonCatalog,
    wall_latency_ms: float,
    gold_active_ids: set[str],
    prior_active_ids: set[str],
) -> dict[str, Any]:
    result = score_holdout_turn(
        gold,
        turn,
        catalog,
        wall_latency_ms=wall_latency_ms,
        gold_active_ids=gold_active_ids,
        condition="full",
    )
    expected_ids = set(gold.gold_candidate_ids)
    actual_ids = {candidate.canonical_id for candidate in turn.understanding.candidates}
    expected_diff = set(gold.gold_state_diff)
    actual_diff = extended_state_diff_operations(
        turn, prior_active_ids=prior_active_ids
    )
    diff_counts = _set_counts(expected_diff, actual_diff)
    result["state_diff_actual"] = sorted(actual_diff)
    result["state_diff_counts"] = diff_counts
    result["candidate_false_positive_ids"] = sorted(actual_ids - expected_ids)
    result["carryover_to_update_false_positive_ids"] = sorted(
        (actual_ids & prior_active_ids) - expected_ids
    )
    result["prior_active_id_count"] = len(prior_active_ids - {"category_tablet"})
    result["material_state_diff_false_positive_operations"] = sorted(
        actual_diff - expected_diff
    )
    result["experimental_delete_ids"] = sorted(
        getattr(turn.understanding, "experimental_delete_ids", [])
    )
    result["experimental_carryover_ids"] = sorted(
        getattr(turn.understanding, "experimental_carryover_ids", [])
    )
    result["experimental_contract_violations"] = list(
        getattr(turn.understanding, "experimental_contract_violations", [])
    )
    result["is_correction_turn"] = (scenario_id, gold.turn) in CORRECTION_TURNS
    return result


def aggregate_extended_metrics(
    scenario_records: list[dict[str, Any]],
) -> dict[str, Any]:
    official = aggregate_condition_metrics(scenario_records)
    turns = [
        turn
        for scenario in scenario_records
        for turn in scenario.get("turn_metrics", [])
    ]
    missing_turns = [
        turn
        for scenario in scenario_records
        for turn in scenario.get("missing_gold_turns", [])
    ]
    correction_turns = [turn for turn in turns if turn["is_correction_turn"]]
    correction_tp = sum(
        turn["candidate_counts"]["true_positive"] for turn in correction_turns
    )
    correction_expected = sum(
        len(turn["candidate_ids_expected"]) for turn in correction_turns
    ) + sum(
        len(turn["gold_candidate_ids"])
        for turn in missing_turns
        if turn["is_correction_turn"]
    )
    exposure = sum(turn["prior_active_id_count"] for turn in turns)
    echo_fp = sum(
        len(turn["carryover_to_update_false_positive_ids"]) for turn in turns
    )
    understanding_latencies = [
        turn["phase_latency_ms"]["understanding_llm"] for turn in turns
    ]
    candidate_tp = sum(turn["candidate_counts"]["true_positive"] for turn in turns)
    candidate_fp = sum(turn["candidate_counts"]["false_positive"] for turn in turns)
    candidate_fn = sum(
        turn["candidate_counts"]["false_negative"] for turn in turns
    ) + sum(len(turn["gold_candidate_ids"]) for turn in missing_turns)
    diff_tp = sum(turn["state_diff_counts"]["true_positive"] for turn in turns)
    diff_fp = sum(turn["state_diff_counts"]["false_positive"] for turn in turns)
    diff_fn = sum(
        turn["state_diff_counts"]["false_negative"] for turn in turns
    ) + sum(len(turn["gold_state_diff"]) for turn in missing_turns)
    expected_turn_count = sum(
        scenario["expected_turn_count"] for scenario in scenario_records
    )
    candidate_exact_count = sum(
        bool(turn["candidate_counts"]["exact"]) for turn in turns
    )
    diff_exact_count = sum(bool(turn["state_diff_counts"]["exact"]) for turn in turns)
    hard_filter_exact_count = sum(bool(turn["hard_filter_exact"]) for turn in turns)
    completed_only = {
        "canonical_id_micro": official["canonical_id_micro"],
        "state_diff_micro": official["state_diff_micro"],
        "hard_constraint_completion_rate": official[
            "hard_constraint_completion_rate"
        ],
    }
    official["canonical_id_micro"] = _micro(candidate_tp, candidate_fp, candidate_fn)
    official["canonical_id_exact_accuracy"] = (
        candidate_exact_count / expected_turn_count if expected_turn_count else None
    )
    official["state_diff_micro"] = _micro(diff_tp, diff_fp, diff_fn)
    official["state_diff_exact_accuracy"] = (
        diff_exact_count / expected_turn_count if expected_turn_count else None
    )
    official["hard_constraint_completion_rate"] = (
        hard_filter_exact_count / expected_turn_count if expected_turn_count else None
    )
    return {
        **official,
        "extended_diagnostics": {
            "missing_turns_counted_as_failures": len(missing_turns),
            "completed_output_metrics": completed_only,
            "candidate_micro": _micro(candidate_tp, candidate_fp, candidate_fn),
            "candidate_false_positive_count": candidate_fp,
            "carryover_to_update_false_positive_count": echo_fp,
            "carryover_false_update_rate_per_active_slot": (
                echo_fp / exposure if exposure else 0.0
            ),
            "prior_active_slot_exposure": exposure,
            "material_state_diff_false_positive_count": sum(
                turn["state_diff_counts"]["false_positive"] for turn in turns
            ),
            "delete_operation_count": sum(
                len(turn["experimental_delete_ids"]) for turn in turns
            ),
            "operation_contract_violation_count": sum(
                len(turn["experimental_contract_violations"]) for turn in turns
            ),
            "correction_turn_count": len(correction_turns),
            "correction_candidate_recall": (
                correction_tp / correction_expected
                if correction_expected
                else 0.0
            ),
            "understanding_latency_ms_median": (
                statistics.median(understanding_latencies)
                if understanding_latencies
                else None
            ),
        },
    }


def select_exploratory_strategy(
    strategy_metrics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Apply the protocol's lexicographic gate; never label this post-hoc winner final."""

    baseline = strategy_metrics["m0_baseline"]
    base_diag = baseline["extended_diagnostics"]
    gates: dict[str, dict[str, Any]] = {}
    eligible: list[str] = []
    for strategy, metrics in strategy_metrics.items():
        diag = metrics["extended_diagnostics"]
        checks = {
            "final_state_f1": metrics["final_state_micro"]["f1"]
            >= baseline["final_state_micro"]["f1"]
            - SELECTION_THRESHOLDS["final_state_f1_non_inferiority_margin"],
            "correction_recall": diag["correction_candidate_recall"]
            >= base_diag["correction_candidate_recall"]
            - SELECTION_THRESHOLDS["correction_recall_non_inferiority_margin"],
            "hard_filter_completion": metrics["hard_constraint_completion_rate"]
            >= baseline["hard_constraint_completion_rate"]
            - SELECTION_THRESHOLDS[
                "hard_filter_completion_non_inferiority_margin"
            ],
            "turn_output_completion": metrics["turn_output_completion_rate"]
            >= baseline["turn_output_completion_rate"]
            - SELECTION_THRESHOLDS[
                "turn_output_completion_non_inferiority_margin"
            ],
        }
        passed = all(checks.values())
        gates[strategy] = {"passed": passed, "checks": checks}
        if passed:
            eligible.append(strategy)

    ranked = sorted(
        eligible,
        key=lambda strategy: (
            -strategy_metrics[strategy]["state_diff_micro"]["f1"],
            strategy_metrics[strategy]["extended_diagnostics"][
                "carryover_to_update_false_positive_count"
            ],
            strategy_metrics[strategy]["extended_diagnostics"][
                "understanding_latency_ms_median"
            ]
            or float("inf"),
            strategy,
        ),
    )
    return {
        "status": "exploratory_posthoc_only",
        "thresholds": SELECTION_THRESHOLDS,
        "baseline": "m0_baseline",
        "gates": gates,
        "eligible_strategies_in_rank_order": ranked,
        "exploratory_selected_strategy": ranked[0] if ranked else None,
        "claim_boundary": (
            "Selection uses a previously observed holdout and requires confirmation "
            "on a newly frozen untouched holdout."
        ),
    }
