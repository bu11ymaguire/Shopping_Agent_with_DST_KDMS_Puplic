"""Automatic process benchmark for the frozen tablet-domain official holdout.

This module is evaluator-only. It never calls an LLM and never mutates the frozen
system, prompt, holdout, official raw result, or human annotation packet.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

from app.evaluation.actual_recommendation import product_satisfies_hard_filters
from app.evaluation.tablet_domain_holdout import (
    TabletHoldoutDataset,
    TabletHoldoutScenario,
    TabletHoldoutTurn,
)
from app.evaluation.tablet_domain_official import active_state_ids
from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models import DialogueState, PreferenceValue
from app.models.actual_demo import ACTUAL_FACET_IDS, ActualHardFilters, ActualPipelineTurn
from app.nodes.actual_policy import select_actual_policy
from app.nodes.actual_recommendation import generate_actual_query

AUTOMATIC_SCHEMA_VERSION = "tablet-domain-automatic-benchmark-v1"
BOOTSTRAP_SEED = 20260809
BOOTSTRAP_RESAMPLES = 10_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rate(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def set_counts(expected: Iterable[str], actual: Iterable[str]) -> dict[str, int]:
    expected_set = set(expected)
    actual_set = set(actual)
    return {
        "tp": len(expected_set & actual_set),
        "fp": len(actual_set - expected_set),
        "fn": len(expected_set - actual_set),
    }


def micro_scores(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    precision = rate(tp, tp + fp)
    recall = rate(tp, tp + fn)
    if precision is None or recall is None:
        f1 = None
    elif precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    else:
        f1 = 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _add_counts(target: dict[str, int], counts: dict[str, int]) -> None:
    for key in ("tp", "fp", "fn"):
        target[key] += counts[key]


def _filter_signature(payload: dict[str, Any] | ActualHardFilters) -> set[str]:
    if isinstance(payload, ActualHardFilters):
        values = payload.model_dump(mode="json", exclude_none=True)
    else:
        values = {key: value for key, value in payload.items() if value is not None}
    return {
        f"{key}={json.dumps(value, ensure_ascii=False, sort_keys=True)}"
        for key, value in values.items()
    }


def _candidate_scope(candidate: Any) -> str:
    target = candidate.target
    return "facet" if target.kind == "facet" else target.scope


def _action_payload(turn: ActualPipelineTurn | None) -> Any | None:
    return turn.understanding.item_action if turn is not None else None


def _tradeoff_exact(gold: Any, actual: Any | None) -> bool:
    if actual is None:
        return False
    return (
        set(actual.prioritized_ids) == set(gold.prioritized_ids)
        and set(actual.compromised_ids) == set(gold.compromised_ids)
        and actual.origin == gold.origin
    )


def _turn_number(turn: ActualPipelineTurn) -> int:
    return int(turn.turn_id.rsplit("-", 1)[1])


def _scenario_turns(payload: dict[str, Any]) -> dict[int, ActualPipelineTurn]:
    return {
        _turn_number(turn): turn
        for item in payload.get("turns", [])
        for turn in [ActualPipelineTurn.model_validate(item)]
    }


def _extract_trace_utterance(record: dict[str, Any]) -> str | None:
    if record.get("node") != "spn-understanding":
        return None
    messages = record.get("messages") or []
    if not messages:
        return None
    content = messages[-1].get("content")
    if not isinstance(content, str):
        return None
    try:
        request = json.loads(content)
    except json.JSONDecodeError:
        return None
    utterance = request.get("current_utterance_to_extract")
    return utterance if isinstance(utterance, str) else None


def load_trace_index(
    trace_path: Path,
    gold: TabletHoldoutDataset,
) -> tuple[dict[str, dict[str, int]], dict[str, Any]]:
    """Index call diagnostics by scenario without retaining raw model content."""
    utterance_index = {
        turn.utterance: scenario.id
        for scenario in gold.scenarios
        for turn in scenario.turns
    }
    records = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    conversation_to_scenario: dict[str, str] = {}
    for record in records:
        utterance = _extract_trace_utterance(record)
        if utterance in utterance_index:
            conversation_to_scenario[record["conversation_id"]] = utterance_index[utterance]

    counters = {
        scenario.id: {
            "understanding_calls": 0,
            "validation_successes": 0,
            "schema_repair_calls": 0,
            "schema_repair_events": 0,
            "transport_retry_calls": 0,
            "transport_retry_events": 0,
            "llm_fallback_calls": 0,
            "llm_calls": 0,
        }
        for scenario in gold.scenarios
    }
    unmapped_calls = 0
    for record in records:
        scenario_id = conversation_to_scenario.get(record.get("conversation_id"))
        if scenario_id is None:
            unmapped_calls += 1
            continue
        row = counters[scenario_id]
        row["llm_calls"] += 1
        row["llm_fallback_calls"] += int(bool(record.get("fallback_used")))
        if record.get("node") != "spn-understanding":
            continue
        row["understanding_calls"] += 1
        row["validation_successes"] += int(bool(record.get("validation_success")))
        repairs = int(record.get("retry_count") or 0)
        transport = int(record.get("transport_retry_count") or 0)
        row["schema_repair_calls"] += int(repairs > 0)
        row["schema_repair_events"] += repairs
        row["transport_retry_calls"] += int(transport > 0)
        row["transport_retry_events"] += transport
    return counters, {
        "trace_record_count": len(records),
        "unmapped_trace_record_count": unmapped_calls,
        "trace_sha256": sha256_file(trace_path),
    }


def _empty_scenario_record(
    scenario: TabletHoldoutScenario,
    condition: str,
    raw_scenario: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scenario_id": scenario.id,
        "condition": condition,
        "expected_turns": len(scenario.turns),
        "completed_turns": int(raw_scenario.get("completed_turn_count", 0)),
        "scenario_completed": int(raw_scenario.get("status") == "completed"),
        "canonical_tp": 0,
        "canonical_fp": 0,
        "canonical_fn": 0,
        "state_diff_tp": 0,
        "state_diff_fp": 0,
        "state_diff_fn": 0,
        "final_state_tp": 0,
        "final_state_fp": 0,
        "final_state_fn": 0,
        "scope_correct": 0,
        "scope_expected": 0,
        "forbidden_candidate_turn_violations": 0,
        "item_action_correct": 0,
        "item_action_expected": 0,
        "rejection_target_correct": 0,
        "rejection_target_expected": 0,
        "rejection_reason_id_correct": 0,
        "rejection_reason_type_correct": 0,
        "tradeoff_relation_correct": 0,
        "tradeoff_relation_expected": 0,
        "spurious_tradeoff_count": 0,
        "policy_correct": 0,
        "policy_total": len(scenario.turns),
        "question_target_correct": 0,
        "question_target_expected": 0,
        "premature_recommendations": 0,
        "gold_clarify_turns": 0,
        "unnecessary_clarifications": 0,
        "gold_recommend_turns": 0,
        "recommend_reach_hits": 0,
        "recommend_completion_hits": 0,
        "hard_filter_exact": 0,
        "hard_filter_total": len(scenario.turns),
        "hard_filter_tp": 0,
        "hard_filter_fp": 0,
        "hard_filter_fn": 0,
        "displayed_products": 0,
        "hard_constraint_violations": 0,
        "rejection_retained": 0,
        "rejection_retention_eligible": 0,
        "rejected_item_reappearances": 0,
        "reappearance_opportunities": 0,
        "feedback_state_reflected": 0,
        "feedback_state_expected": 0,
        "feedback_query_reflected": 0,
        "feedback_query_expected": 0,
        "evidence_cards": 0,
        "evidence_mismatches": 0,
        "response_fallback_turns": 0,
        "retrieval_fallback_turns": 0,
        "recommendation_output_turns": 0,
        "inferred_candidate_count": 0,
        "unsupported_inferred_candidate_count": 0,
    }


def score_condition_scenario(
    scenario: TabletHoldoutScenario,
    condition: str,
    raw_scenario: dict[str, Any],
    trace_counts: dict[str, int],
) -> dict[str, Any]:
    """Score one scenario on the full frozen denominator, including missing outputs."""
    record = _empty_scenario_record(scenario, condition, raw_scenario)
    turns = _scenario_turns(raw_scenario)
    metric_by_turn = {
        int(item["turn"]): item for item in raw_scenario.get("turn_metrics", [])
    }

    final_counts = set_counts(
        scenario.final_gold_state_ids,
        raw_scenario.get("final_state_ids_actual", []),
    )
    record["final_state_tp"] = final_counts["tp"]
    record["final_state_fp"] = final_counts["fp"]
    record["final_state_fn"] = final_counts["fn"]

    for gold_turn in scenario.turns:
        actual_turn = turns.get(gold_turn.turn)
        metric = metric_by_turn.get(gold_turn.turn)
        actual_ids = (
            {candidate.canonical_id for candidate in actual_turn.understanding.candidates}
            if actual_turn is not None
            else set()
        )
        candidate_counts = set_counts(gold_turn.gold_candidate_ids, actual_ids)
        for key in ("tp", "fp", "fn"):
            record[f"canonical_{key}"] += candidate_counts[key]

        actual_diff = set(metric["state_diff_actual"]) if metric else set()
        diff_counts = set_counts(gold_turn.gold_state_diff, actual_diff)
        for key in ("tp", "fp", "fn"):
            record[f"state_diff_{key}"] += diff_counts[key]

        actual_candidates = (
            {item.canonical_id: item for item in actual_turn.understanding.candidates}
            if actual_turn is not None
            else {}
        )
        for canonical_id, expected_scope in gold_turn.gold_candidate_scopes.items():
            record["scope_expected"] += 1
            candidate = actual_candidates.get(canonical_id)
            record["scope_correct"] += int(
                candidate is not None and _candidate_scope(candidate) == expected_scope
            )
        record["forbidden_candidate_turn_violations"] += int(
            bool(actual_ids & set(gold_turn.forbidden_candidate_ids))
        )

        actual_action = _action_payload(actual_turn)
        if gold_turn.gold_item_action is not None:
            gold_action = gold_turn.gold_item_action
            record["item_action_expected"] += 1
            record["item_action_correct"] += int(
                metric is not None and bool(metric["item_action_exact"])
            )
            if gold_action.name == "reject_first":
                record["rejection_target_expected"] += 1
                record["rejection_target_correct"] += int(
                    actual_action is not None
                    and actual_action.name == "reject_first"
                    and (actual_action.target_rank or 1) == (gold_action.target_rank or 1)
                )
                reason = getattr(actual_action, "rejection_reason", None)
                record["rejection_reason_id_correct"] += int(
                    reason is not None
                    and reason.canonical_id == gold_action.rejection_reason_id
                )
                record["rejection_reason_type_correct"] += int(
                    reason is not None and reason.reason_type == gold_action.reason_type
                )

                previous_turn = turns.get(gold_turn.turn - 1)
                target_rank = gold_action.target_rank or 1
                target_id = None
                if previous_turn is not None:
                    target_id = next(
                        (
                            item.product_id
                            for item in previous_turn.rankings
                            if item.rank == target_rank
                        ),
                        None,
                    )
                if target_id is not None:
                    record["rejection_retention_eligible"] += 1
                    rejected_ids = (
                        {item.product_id for item in actual_turn.dialogue_state.rejected_items}
                        if actual_turn is not None
                        else set()
                    )
                    record["rejection_retained"] += int(target_id in rejected_ids)
                    if metric is not None and metric["recommendation_pipeline_completed"]:
                        record["reappearance_opportunities"] += 1
                        record["rejected_item_reappearances"] += int(
                            target_id in set(metric["top_product_ids"])
                        )

        actual_tradeoff = (
            actual_turn.understanding.tradeoff if actual_turn is not None else None
        )
        if gold_turn.gold_tradeoff is not None:
            record["tradeoff_relation_expected"] += 1
            record["tradeoff_relation_correct"] += int(
                _tradeoff_exact(gold_turn.gold_tradeoff, actual_tradeoff)
            )
        elif actual_tradeoff is not None:
            record["spurious_tradeoff_count"] += 1

        actual_lane = actual_turn.policy.lane if actual_turn is not None else None
        record["policy_correct"] += int(actual_lane == gold_turn.gold_policy_lane)
        if gold_turn.gold_policy_lane == "clarify-lane":
            record["gold_clarify_turns"] += 1
            record["question_target_expected"] += 1
            actual_question = (
                actual_turn.policy.question_target.field
                if actual_turn is not None and actual_turn.policy.question_target
                else None
            )
            record["question_target_correct"] += int(
                actual_question == gold_turn.gold_question_target
            )
            record["premature_recommendations"] += int(actual_lane == "recommend-lane")
        else:
            record["gold_recommend_turns"] += 1
            record["recommend_reach_hits"] += int(actual_lane == "recommend-lane")
            record["unnecessary_clarifications"] += int(actual_lane == "clarify-lane")
            record["recommend_completion_hits"] += int(
                metric is not None and metric["recommendation_pipeline_completed"]
            )

        expected_filters = gold_turn.expected_hard_filters_after_turn
        actual_filters = metric["hard_filters_actual"] if metric else {}
        filter_counts = set_counts(
            _filter_signature(expected_filters), _filter_signature(actual_filters)
        )
        for key in ("tp", "fp", "fn"):
            record[f"hard_filter_{key}"] += filter_counts[key]
        record["hard_filter_exact"] += int(
            metric is not None and bool(metric["hard_filter_exact"])
        )
        if metric is not None:
            record["displayed_products"] += int(metric["top3_product_count"])
            record["hard_constraint_violations"] += int(
                metric["top3_hard_constraint_violations"]
            )
            record["evidence_cards"] += int(metric["evidence_card_count"])
            record["evidence_mismatches"] += int(metric["evidence_mismatch_count"])
            record["response_fallback_turns"] += int(
                metric["response_template_fallback"]
            )
            record["recommendation_output_turns"] += int(
                metric["recommendation_pipeline_completed"]
            )
            record["retrieval_fallback_turns"] += int(
                metric["recommendation_pipeline_completed"]
                and metric["review_retrieval_fallback"]
            )

        if actual_turn is not None:
            expected_set = set(gold_turn.gold_candidate_ids)
            inferred = [
                item
                for item in actual_turn.understanding.candidates
                if item.origin == "inferred"
            ]
            record["inferred_candidate_count"] += len(inferred)
            record["unsupported_inferred_candidate_count"] += sum(
                item.canonical_id not in expected_set for item in inferred
            )

        if gold_turn.turn > 1:
            feedback_ids = {
                canonical_id
                for canonical_id, scope in gold_turn.gold_candidate_scopes.items()
                if scope in {"hard", "soft"}
            }
            if feedback_ids:
                state_ids = (
                    active_state_ids(actual_turn.dialogue_state)
                    if actual_turn is not None
                    else set()
                )
                record["feedback_state_expected"] += len(feedback_ids)
                record["feedback_state_reflected"] += len(feedback_ids & state_ids)
                if gold_turn.gold_policy_lane == "recommend-lane":
                    query_ids = (
                        set(actual_turn.query.active_preference_ids)
                        if actual_turn is not None and actual_turn.query is not None
                        else set()
                    )
                    record["feedback_query_expected"] += len(feedback_ids)
                    record["feedback_query_reflected"] += len(feedback_ids & query_ids)

    record.update(trace_counts)
    return record


def _sum(records: list[dict[str, Any]], field: str) -> int | float:
    return sum(record.get(field, 0) for record in records)


def aggregate_condition(records: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = micro_scores(
        int(_sum(records, "canonical_tp")),
        int(_sum(records, "canonical_fp")),
        int(_sum(records, "canonical_fn")),
    )
    state_diff = micro_scores(
        int(_sum(records, "state_diff_tp")),
        int(_sum(records, "state_diff_fp")),
        int(_sum(records, "state_diff_fn")),
    )
    final_state = micro_scores(
        int(_sum(records, "final_state_tp")),
        int(_sum(records, "final_state_fp")),
        int(_sum(records, "final_state_fn")),
    )
    hard_filter_fields = micro_scores(
        int(_sum(records, "hard_filter_tp")),
        int(_sum(records, "hard_filter_fp")),
        int(_sum(records, "hard_filter_fn")),
    )
    evidence_cards = int(_sum(records, "evidence_cards"))
    recommendation_outputs = int(_sum(records, "recommendation_output_turns"))
    understanding_calls = int(_sum(records, "understanding_calls"))
    inferred_count = int(_sum(records, "inferred_candidate_count"))
    return {
        "scenario_completion_rate": rate(_sum(records, "scenario_completed"), len(records)),
        "scenario_completion": f"{int(_sum(records, 'scenario_completed'))}/{len(records)}",
        "turn_completion_rate": rate(
            _sum(records, "completed_turns"), _sum(records, "expected_turns")
        ),
        "turn_completion": (
            f"{int(_sum(records, 'completed_turns'))}/"
            f"{int(_sum(records, 'expected_turns'))}"
        ),
        "canonical_id_micro": canonical,
        "final_state_micro": final_state,
        "state_diff_micro": state_diff,
        "scope_accuracy_on_gold_candidates": rate(
            _sum(records, "scope_correct"), _sum(records, "scope_expected")
        ),
        "forbidden_candidate_turn_violation_rate": rate(
            _sum(records, "forbidden_candidate_turn_violations"),
            _sum(records, "expected_turns"),
        ),
        "item_action_exact_accuracy": rate(
            _sum(records, "item_action_correct"),
            _sum(records, "item_action_expected"),
        ),
        "rejection_target_accuracy": rate(
            _sum(records, "rejection_target_correct"),
            _sum(records, "rejection_target_expected"),
        ),
        "rejection_reason_id_accuracy": rate(
            _sum(records, "rejection_reason_id_correct"),
            _sum(records, "rejection_target_expected"),
        ),
        "rejection_reason_type_accuracy": rate(
            _sum(records, "rejection_reason_type_correct"),
            _sum(records, "rejection_target_expected"),
        ),
        "tradeoff_relation_accuracy": rate(
            _sum(records, "tradeoff_relation_correct"),
            _sum(records, "tradeoff_relation_expected"),
        ),
        "spurious_tradeoff_count": int(_sum(records, "spurious_tradeoff_count")),
        "policy_lane_accuracy": rate(
            _sum(records, "policy_correct"), _sum(records, "policy_total")
        ),
        "question_target_exact_accuracy": rate(
            _sum(records, "question_target_correct"),
            _sum(records, "question_target_expected"),
        ),
        "premature_recommendation_rate": rate(
            _sum(records, "premature_recommendations"),
            _sum(records, "gold_clarify_turns"),
        ),
        "unnecessary_clarification_rate": rate(
            _sum(records, "unnecessary_clarifications"),
            _sum(records, "gold_recommend_turns"),
        ),
        "recommendation_reach_rate": rate(
            _sum(records, "recommend_reach_hits"),
            _sum(records, "gold_recommend_turns"),
        ),
        "recommendation_completion_rate": rate(
            _sum(records, "recommend_completion_hits"),
            _sum(records, "gold_recommend_turns"),
        ),
        "strict_schema_validation_rate": rate(
            _sum(records, "validation_successes"), understanding_calls
        ),
        "understanding_attempt_coverage": rate(
            understanding_calls, _sum(records, "expected_turns")
        ),
        "schema_repair_call_rate": rate(
            _sum(records, "schema_repair_calls"), understanding_calls
        ),
        "schema_repair_event_count": int(_sum(records, "schema_repair_events")),
        "transport_retry_call_rate": rate(
            _sum(records, "transport_retry_calls"), understanding_calls
        ),
        "transport_retry_event_count": int(_sum(records, "transport_retry_events")),
        "llm_fallback_call_rate": rate(
            _sum(records, "llm_fallback_calls"), _sum(records, "llm_calls")
        ),
        "response_template_fallback_rate": rate(
            _sum(records, "response_fallback_turns"), _sum(records, "completed_turns")
        ),
        "review_retrieval_fallback_rate": rate(
            _sum(records, "retrieval_fallback_turns"), recommendation_outputs
        ),
        "hard_filter_completion_rate": rate(
            _sum(records, "hard_filter_exact"), _sum(records, "hard_filter_total")
        ),
        "hard_filter_field_micro": hard_filter_fields,
        "hard_constraint_violation_rate": rate(
            _sum(records, "hard_constraint_violations"),
            _sum(records, "displayed_products"),
        ),
        "hard_constraint_violations": (
            f"{int(_sum(records, 'hard_constraint_violations'))}/"
            f"{int(_sum(records, 'displayed_products'))}"
        ),
        "rejection_retention_rate": rate(
            _sum(records, "rejection_retained"),
            _sum(records, "rejection_retention_eligible"),
        ),
        "rejection_retention_support": (
            f"{int(_sum(records, 'rejection_retention_eligible'))} resolvable gold events"
        ),
        "rejected_item_reappearance_rate": rate(
            _sum(records, "rejected_item_reappearances"),
            _sum(records, "reappearance_opportunities"),
        ),
        "feedback_constraint_state_reflection_rate": rate(
            _sum(records, "feedback_state_reflected"),
            _sum(records, "feedback_state_expected"),
        ),
        "feedback_constraint_query_reflection_rate": rate(
            _sum(records, "feedback_query_reflected"),
            _sum(records, "feedback_query_expected"),
        ),
        "evidence_id_consistency_rate": (
            1 - _sum(records, "evidence_mismatches") / evidence_cards
            if evidence_cards
            else None
        ),
        "inferred_candidate_count": inferred_count,
        "unsupported_inferred_candidate_rate": rate(
            _sum(records, "unsupported_inferred_candidate_count"), inferred_count
        ),
    }


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _f1_from_records(records: list[dict[str, Any]], prefix: str) -> float | None:
    return micro_scores(
        int(_sum(records, f"{prefix}_tp")),
        int(_sum(records, f"{prefix}_fp")),
        int(_sum(records, f"{prefix}_fn")),
    )["f1"]


def _ratio_from_records(
    records: list[dict[str, Any]], numerator: str, denominator: str
) -> float | None:
    return rate(_sum(records, numerator), _sum(records, denominator))


def paired_bootstrap_metric(
    full: dict[str, dict[str, Any]],
    ablation: dict[str, dict[str, Any]],
    scorer: Callable[[list[dict[str, Any]]], float | None],
    *,
    seed_offset: int,
    minimum_supported_scenarios: int = 2,
    support_field: str | None = None,
) -> dict[str, Any]:
    scenario_ids = sorted(set(full) & set(ablation))
    full_records = [full[item] for item in scenario_ids]
    ablation_records = [ablation[item] for item in scenario_ids]
    full_score = scorer(full_records)
    ablation_score = scorer(ablation_records)
    difference = (
        full_score - ablation_score
        if full_score is not None and ablation_score is not None
        else None
    )
    eligible = (
        sum(
            full[item].get(support_field, 0) > 0
            and ablation[item].get(support_field, 0) > 0
            for item in scenario_ids
        )
        if support_field
        else len(scenario_ids)
    )
    result = {
        "full": full_score,
        "ablation": ablation_score,
        "difference": difference,
        "paired_scenario_count": len(scenario_ids),
        "eligible_scenario_count": eligible,
        "bootstrap_unit": "scenario",
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": BOOTSTRAP_SEED + seed_offset,
    }
    if difference is None or eligible < minimum_supported_scenarios:
        result.update(
            {
                "ci95": None,
                "status": "insufficient_support",
                "reason": (
                    f"only {eligible} paired scenarios contain an evaluable event"
                ),
            }
        )
        return result

    rng = random.Random(BOOTSTRAP_SEED + seed_offset)
    differences: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sampled = [rng.randrange(len(scenario_ids)) for _ in scenario_ids]
        sampled_full = [full_records[index] for index in sampled]
        sampled_ablation = [ablation_records[index] for index in sampled]
        left = scorer(sampled_full)
        right = scorer(sampled_ablation)
        if left is not None and right is not None:
            differences.append(left - right)
    result.update(
        {
            "ci95": [
                _quantile(differences, 0.025),
                _quantile(differences, 0.975),
            ],
            "status": "computed",
        }
    )
    return result


def paired_full_vs_no_memory(
    records_by_condition: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    full = {item["scenario_id"]: item for item in records_by_condition["full"]}
    ablation = {
        item["scenario_id"]: item for item in records_by_condition["no_memory"]
    }
    specs: list[tuple[str, Callable[[list[dict[str, Any]]], float | None], str | None]] = [
        ("final_state_micro_f1", lambda rows: _f1_from_records(rows, "final_state"), None),
        ("state_diff_micro_f1", lambda rows: _f1_from_records(rows, "state_diff"), None),
        ("policy_accuracy", lambda rows: _ratio_from_records(rows, "policy_correct", "policy_total"), None),
        ("recommendation_reach", lambda rows: _ratio_from_records(rows, "recommend_reach_hits", "gold_recommend_turns"), None),
        ("hard_filter_completion", lambda rows: _ratio_from_records(rows, "hard_filter_exact", "hard_filter_total"), None),
        ("rejection_retention", lambda rows: _ratio_from_records(rows, "rejection_retained", "rejection_retention_eligible"), "rejection_retention_eligible"),
    ]
    return {
        name: paired_bootstrap_metric(
            full,
            ablation,
            scorer,
            seed_offset=index,
            support_field=support,
        )
        for index, (name, scorer, support) in enumerate(specs, start=1)
    }


def _jaccard(left: list[str], right: list[str]) -> float | None:
    union = set(left) | set(right)
    return len(set(left) & set(right)) / len(union) if union else None


def _kendall_tau_on_intersection(left: list[str], right: list[str]) -> float | None:
    common = [item for item in left if item in set(right)]
    if len(common) < 2:
        return None
    right_rank = {item: rank for rank, item in enumerate(right)}
    concordant = 0
    discordant = 0
    for first in range(len(common)):
        for second in range(first + 1, len(common)):
            if right_rank[common[first]] < right_rank[common[second]]:
                concordant += 1
            else:
                discordant += 1
    return (concordant - discordant) / (concordant + discordant)


def _mean_rank_shift(left: list[str], right: list[str], cutoff: int = 10) -> float | None:
    union = set(left) | set(right)
    if not union:
        return None
    missing_rank = cutoff + 1
    left_rank = {item: rank for rank, item in enumerate(left, start=1)}
    right_rank = {item: rank for rank, item in enumerate(right, start=1)}
    return sum(
        abs(left_rank.get(item, missing_rank) - right_rank.get(item, missing_rank))
        for item in union
    ) / len(union)


def analyze_fixed_upstream_no_review(
    fixed_payload: dict[str, Any],
    scenario_ids: list[str],
    full_evidence_consistency: float | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in fixed_payload["records"]:
        full_top = item["full_top10"]
        no_review_top = item["fixed_upstream_no_review_top10"]
        grouped[item["scenario_id"]].append(
            {
                "top1_changed": int(bool(full_top and no_review_top and full_top[0] != no_review_top[0])),
                "top3_order_changed": int(full_top[:3] != no_review_top[:3]),
                "top3_overlap_count": len(set(full_top[:3]) & set(no_review_top[:3])),
                "top3_jaccard": _jaccard(full_top[:3], no_review_top[:3]),
                "mean_rank_shift": _mean_rank_shift(full_top, no_review_top),
                "kendall_tau": _kendall_tau_on_intersection(full_top, no_review_top),
            }
        )

    scenario_rows = []
    for scenario_id in scenario_ids:
        turns = grouped.get(scenario_id, [])
        kendalls = [item["kendall_tau"] for item in turns if item["kendall_tau"] is not None]
        row = {
            "scenario_id": scenario_id,
            "condition": "fixed_upstream_no_review",
            "ranking_turns": len(turns),
            "top1_changes": sum(item["top1_changed"] for item in turns),
            "top3_order_changes": sum(item["top3_order_changed"] for item in turns),
            "top3_overlap_sum": sum(item["top3_overlap_count"] for item in turns),
            "top3_jaccard_sum": sum(item["top3_jaccard"] or 0 for item in turns),
            "rank_shift_sum": sum(item["mean_rank_shift"] or 0 for item in turns),
            "kendall_tau_sum": sum(kendalls),
            "kendall_tau_turns": len(kendalls),
        }
        scenario_rows.append(row)

    def boot_metric(
        numerator: str, denominator: str, *, seed_offset: int
    ) -> dict[str, Any]:
        point = _ratio_from_records(scenario_rows, numerator, denominator)
        rng = random.Random(BOOTSTRAP_SEED + seed_offset)
        values = []
        for _ in range(BOOTSTRAP_RESAMPLES):
            sample = [scenario_rows[rng.randrange(len(scenario_rows))] for _ in scenario_rows]
            value = _ratio_from_records(sample, numerator, denominator)
            if value is not None:
                values.append(value)
        return {
            "value": point,
            "ci95": [_quantile(values, 0.025), _quantile(values, 0.975)],
            "bootstrap_unit": "scenario",
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        }

    summary = {
        "contract": fixed_payload["contract"],
        "recommendation_turn_count": int(_sum(scenario_rows, "ranking_turns")),
        "candidate_count_identity_rate": fixed_payload["candidate_count_identity_rate"],
        "top1_change_rate": boot_metric("top1_changes", "ranking_turns", seed_offset=101),
        "top3_order_change_rate": boot_metric("top3_order_changes", "ranking_turns", seed_offset=102),
        "mean_top3_overlap_count": boot_metric("top3_overlap_sum", "ranking_turns", seed_offset=103),
        "mean_top3_jaccard": boot_metric("top3_jaccard_sum", "ranking_turns", seed_offset=104),
        "mean_absolute_rank_shift_at_10": boot_metric("rank_shift_sum", "ranking_turns", seed_offset=105),
        "mean_kendall_tau_common_at_10": boot_metric("kendall_tau_sum", "kendall_tau_turns", seed_offset=106),
        "hard_constraint_violation_rate": fixed_payload["hard_constraint_violation_rate"],
        "full_evidence_id_consistency_rate": full_evidence_consistency,
        "no_review_evidence_contract": "no evidence IDs or review contribution by construction",
        "quality_claim_allowed": False,
    }
    return summary, scenario_rows


_HARD_VALUE_FIELDS = {
    "budget": "max_price_usd",
    "storage_capacity": "min_storage_gb",
    "memory_capacity": "min_memory_gb",
    "max_weight": "max_weight_grams",
    "min_rating": "min_rating",
    "display": "min_screen_inches",
    "operating_system": "operating_system",
}


def _oracle_value_text(
    canonical_id: str, expected_filters: ActualHardFilters
) -> str:
    field = _HARD_VALUE_FIELDS.get(canonical_id)
    value = getattr(expected_filters, field) if field else None
    if value is None:
        return canonical_id.replace("_", " ")
    suffix = {
        "budget": " USD budget",
        "storage_capacity": " GB storage",
        "memory_capacity": " GB RAM",
        "max_weight": " grams maximum weight",
        "min_rating": " minimum rating",
        "display": " inch display",
        "operating_system": " operating system",
    }[canonical_id]
    return f"{value}{suffix}"


def _preference(canonical_id: str, value_text: str, turn: int) -> PreferenceValue:
    return PreferenceValue(
        canonical_id=canonical_id,
        value_text=value_text,
        origin="explicit",
        confidence=1.0,
        status="confirmed",
        evidence_turn_ids=[f"gold-turn-{turn}"],
        updated_at_turn_id=f"gold-turn-{turn}",
    )


def _apply_gold_turn_to_oracle_state(
    state: DialogueState, gold_turn: TabletHoldoutTurn
) -> DialogueState:
    state = state.model_copy(deep=True)
    state.domain_route = gold_turn.gold_domain_route
    state.unsupported_category_text = (
        "gold unsupported category"
        if gold_turn.gold_domain_route == "unsupported_category"
        else None
    )
    expected_filters = gold_turn.expected_hard_filters_after_turn
    for canonical_id, scope in gold_turn.gold_candidate_scopes.items():
        value = _preference(
            canonical_id,
            _oracle_value_text(canonical_id, expected_filters),
            gold_turn.turn,
        )
        if scope == "hard":
            state.soft_constraints.pop(canonical_id, None)
            state.hard_constraints[canonical_id] = value
        elif scope == "soft":
            state.hard_constraints.pop(canonical_id, None)
            state.soft_constraints[canonical_id] = value
        else:
            facet = next(
                name for name, ids in ACTUAL_FACET_IDS.items() if canonical_id in ids
            )
            state.subjective_needs.set_facet(facet, value)

    # Synchronize numeric hard values with the existing per-turn Gold State contract.
    for canonical_id, field in _HARD_VALUE_FIELDS.items():
        if canonical_id not in state.hard_constraints:
            continue
        if getattr(expected_filters, field) is None:
            continue
        state.hard_constraints[canonical_id] = _preference(
            canonical_id,
            _oracle_value_text(canonical_id, expected_filters),
            gold_turn.turn,
        )
    return state


def oracle_policy_and_constraint_analysis(
    gold: TabletHoldoutDataset,
    catalog: ExperimentalAmazonCatalog,
    full_records: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run only deterministic Policy/query/catalog filtering from annotated gold state."""
    from app.nodes.actual_state_manager import create_tablet_environment_state

    rows = []
    for scenario in gold.scenarios:
        state = create_tablet_environment_state()
        row = {
            "scenario_id": scenario.id,
            "condition": "gold_state_oracle",
            "policy_correct": 0,
            "policy_total": len(scenario.turns),
            "recommend_reach_hits": 0,
            "gold_recommend_turns": 0,
            "hard_filter_exact": 0,
            "hard_filter_total": len(scenario.turns),
            "oracle_candidate_available": 0,
            "oracle_candidate_expected": 0,
            "oracle_candidate_hard_violations": 0,
            "oracle_candidate_products_checked": 0,
        }
        for gold_turn in scenario.turns:
            state = _apply_gold_turn_to_oracle_state(state, gold_turn)
            policy = select_actual_policy(state)
            query = generate_actual_query(state)
            row["policy_correct"] += int(policy.lane == gold_turn.gold_policy_lane)
            row["hard_filter_exact"] += int(
                query.hard_filters == gold_turn.expected_hard_filters_after_turn
            )
            if gold_turn.gold_policy_lane == "recommend-lane":
                row["gold_recommend_turns"] += 1
                row["recommend_reach_hits"] += int(policy.lane == "recommend-lane")
                row["oracle_candidate_expected"] += 1
                filters = query.hard_filters
                products = catalog.search_products(
                    max_price_usd=(None if query.allow_budget_overrun else filters.max_price_usd),
                    min_storage_gb=filters.min_storage_gb,
                    min_memory_gb=filters.min_memory_gb,
                    max_weight_grams=filters.max_weight_grams,
                    min_rating=filters.min_rating,
                    min_screen_inches=filters.min_screen_inches,
                    operating_system=filters.operating_system,
                    sort_by="review_count",
                    limit=300,
                ).products
                row["oracle_candidate_available"] += int(bool(products))
                for product in products:
                    row["oracle_candidate_products_checked"] += 1
                    row["oracle_candidate_hard_violations"] += int(
                        not product_satisfies_hard_filters(
                            product,
                            gold_turn.expected_hard_filters_after_turn,
                            allow_budget_overrun=query.allow_budget_overrun,
                        )
                    )
        rows.append(row)

    actual_policy = _ratio_from_records(full_records, "policy_correct", "policy_total")
    actual_hard = _ratio_from_records(full_records, "hard_filter_exact", "hard_filter_total")
    actual_reach = _ratio_from_records(full_records, "recommend_reach_hits", "gold_recommend_turns")
    oracle_policy = _ratio_from_records(rows, "policy_correct", "policy_total")
    oracle_hard = _ratio_from_records(rows, "hard_filter_exact", "hard_filter_total")
    oracle_reach = _ratio_from_records(rows, "recommend_reach_hits", "gold_recommend_turns")
    summary = {
        "status": "secondary_evaluator_side_oracle",
        "additional_llm_calls": 0,
        "actual_input": "frozen Full Understanding and accumulated state output",
        "oracle_input": "pre-run Gold State Diff/Final State IDs and expected hard filters",
        "policy_accuracy": {
            "actual_full": actual_policy,
            "gold_state_oracle": oracle_policy,
            "recovery": oracle_policy - actual_policy,
        },
        "recommendation_reach": {
            "actual_full": actual_reach,
            "gold_state_oracle": oracle_reach,
            "recovery": oracle_reach - actual_reach,
        },
        "hard_filter_completion": {
            "actual_full": actual_hard,
            "gold_state_oracle": oracle_hard,
            "recovery": oracle_hard - actual_hard,
        },
        "oracle_candidate_availability_rate": rate(
            _sum(rows, "oracle_candidate_available"),
            _sum(rows, "oracle_candidate_expected"),
        ),
        "oracle_candidate_hard_violation_rate": rate(
            _sum(rows, "oracle_candidate_hard_violations"),
            _sum(rows, "oracle_candidate_products_checked"),
        ),
        "guardrail": (
            "This is a deterministic diagnostic upper bound, not a recommendation relevance or end-to-end system score."
        ),
    }
    return summary, rows


def build_automatic_benchmark(
    *,
    gold: TabletHoldoutDataset,
    raw: dict[str, Any],
    prior_analysis: dict[str, Any],
    trace_paths: dict[str, Path],
    catalog: ExperimentalAmazonCatalog,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if raw.get("status") != "completed":
        raise ValueError("official raw report must be completed")
    records_by_condition: dict[str, list[dict[str, Any]]] = {}
    trace_provenance = {}
    for condition in ("full", "no_memory", "no_review"):
        trace_index, provenance = load_trace_index(trace_paths[condition], gold)
        expected_hash = raw["conditions"][condition]["trace_sha256"]
        if provenance["trace_sha256"] != expected_hash:
            raise ValueError(f"trace hash mismatch: {condition}")
        trace_provenance[condition] = provenance
        raw_scenarios = {
            item["scenario_id"]: item
            for item in raw["conditions"][condition]["scenarios"]
        }
        records_by_condition[condition] = [
            score_condition_scenario(
                scenario,
                condition,
                raw_scenarios[scenario.id],
                trace_index[scenario.id],
            )
            for scenario in gold.scenarios
        ]

    condition_metrics = {
        condition: aggregate_condition(records)
        for condition, records in records_by_condition.items()
    }
    fixed_summary, fixed_rows = analyze_fixed_upstream_no_review(
        prior_analysis["fixed_upstream_no_review"],
        [scenario.id for scenario in gold.scenarios],
        condition_metrics["full"]["evidence_id_consistency_rate"],
    )
    oracle_summary, oracle_rows = oracle_policy_and_constraint_analysis(
        gold, catalog, records_by_condition["full"]
    )

    summary = {
        "schema_version": AUTOMATIC_SCHEMA_VERSION,
        "status": "primary_automatic_benchmark_complete",
        "run_id": raw["run_id"],
        "source_run_completed_at": raw["completed_at"],
        "scope": {
            "holdout_scenarios": len(gold.scenarios),
            "holdout_turns": sum(len(item.turns) for item in gold.scenarios),
            "full_denominator_includes_missing_outputs": True,
            "official_run_reexecuted": False,
            "new_gold_labels_added": False,
            "human_relevance_primary": False,
        },
        "condition_metrics": condition_metrics,
        "paired_full_vs_no_memory": paired_full_vs_no_memory(records_by_condition),
        "fixed_upstream_no_review": fixed_summary,
        "oracle_analysis": oracle_summary,
        "trace_provenance": trace_provenance,
        "hidden_intent_analysis": {
            "situational_constraint_promotion_error": {
                "status": "not_evaluable_with_current_holdout",
                "reason": "the two gold rejection events are product_attribute cases; there is no situational_constraint positive case",
            },
            "unsupported_inference_rate": {
                "status": "not_evaluable_with_current_holdout",
                "observed_inferred_candidate_count": {
                    condition: condition_metrics[condition]["inferred_candidate_count"]
                    for condition in condition_metrics
                },
                "reason": "v2 disables latent subjective generation and the holdout has no hypothesis-level support gold",
            },
            "inference_restraint_rate": {
                "status": "not_evaluable_with_current_holdout",
                "reason": "the holdout has no candidate/inferred status gold or insufficient-evidence opportunity labels",
            },
            "current_to_persistent_scope_error": {
                "status": "not_evaluable_with_current_holdout",
                "reason": "gold_candidate_scopes distinguish facet/hard/soft targets, not temporal current-purchase versus persistent scope",
            },
        },
        "not_officially_computed": {
            "product_ndcg_at_3": "excluded_without_human_relevance_labels",
            "review_ndcg_at_3": "excluded_without_human_relevance_labels",
            "provenance_accuracy": "no per-candidate provenance gold in the frozen holdout",
            "tradeoff_direction_compliance": "relation gold exists, but no gold product ranking direction exists",
        },
        "interpretation_guardrails": [
            "This benchmark measures process fidelity against pre-run multi-turn gold annotations, not subjective product satisfaction or human relevance.",
            "The fixed-upstream review ablation identifies ranking changes causally attributable to the frozen review score contribution, but does not establish a quality improvement.",
            "The independent live No-review condition is retained only as an operational replay because its upstream Understanding output was not identical to Full.",
            "Product NDCG@3 and Review NDCG@3 are not official automatic results.",
            "Metrics unsupported by the frozen gold are explicitly marked not_evaluable_with_current_holdout.",
        ],
    }
    csv_rows = [
        *records_by_condition["full"],
        *records_by_condition["no_memory"],
        *records_by_condition["no_review"],
        *fixed_rows,
        *oracle_rows,
    ]
    return summary, csv_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _format_metric(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _format_ci(value: list[float] | None) -> str:
    return "N/A" if value is None else f"[{value[0]:.3f}, {value[1]:.3f}]"


def render_poster_markdown(summary: dict[str, Any]) -> str:
    """Render the compact tracked poster table from the machine summary."""
    conditions = summary["condition_metrics"]
    paired = summary["paired_full_vs_no_memory"]
    fixed = summary["fixed_upstream_no_review"]
    oracle = summary["oracle_analysis"]
    rows = []
    for label, key in (
        ("Final State micro-F1", "final_state_micro_f1"),
        ("State Diff micro-F1", "state_diff_micro_f1"),
        ("Policy accuracy", "policy_accuracy"),
        ("Recommendation reach", "recommendation_reach"),
        ("Hard-filter completion", "hard_filter_completion"),
        ("Rejection retention", "rejection_retention"),
    ):
        metric = paired[key]
        rows.append(
            f"| {label} | {_format_metric(metric['full'])} | "
            f"{_format_metric(metric['ablation'])} | "
            f"{_format_metric(metric['difference'])} | {_format_ci(metric['ci95'])} |"
        )

    condition_rows = []
    for label, key in (("Full", "full"), ("No-memory", "no_memory"), ("No-review live (secondary)", "no_review")):
        metric = conditions[key]
        condition_rows.append(
            f"| {label} | {metric['scenario_completion']} | {metric['turn_completion']} | "
            f"{_format_metric(metric['canonical_id_micro']['f1'])} | "
            f"{_format_metric(metric['final_state_micro']['f1'])} | "
            f"{_format_metric(metric['state_diff_micro']['f1'])} | "
            f"{_format_metric(metric['strict_schema_validation_rate'])} |"
        )

    no_review_rows = []
    for label, key in (
        ("Top-1 change rate", "top1_change_rate"),
        ("Top-3 order change rate", "top3_order_change_rate"),
        ("Mean top-3 overlap count", "mean_top3_overlap_count"),
        ("Mean top-3 Jaccard", "mean_top3_jaccard"),
        ("Mean absolute rank shift@10", "mean_absolute_rank_shift_at_10"),
        ("Mean Kendall tau on common@10", "mean_kendall_tau_common_at_10"),
    ):
        metric = fixed[key]
        no_review_rows.append(
            f"| {label} | {_format_metric(metric['value'])} | {_format_ci(metric['ci95'])} |"
        )

    return "\n".join(
        [
            "# Tablet-domain automatic benchmark v1",
            "",
            "상태: **포스터 primary automatic benchmark 완료**",
            "",
            f"정본 실행: `{summary['run_id']}` · frozen holdout 20개 시나리오/81턴 · "
            "official run 재실행 없음",
            "",
            "이번 평가는 추천 상품의 주관적 만족도나 인간 relevance를 직접 측정하지 않는다. 대신 "
            "사전에 동결된 multi-turn gold annotation을 기준으로 사용자 조건과 피드백이 Dialogue State에 "
            "보존되고, Policy·검색 조건·추천 순위에 일관되게 전달되는지를 process-level automatic "
            "evaluation으로 측정한다.",
            "",
            "## End-to-end automatic overview",
            "",
            "모든 81턴을 분모에 포함한다. schema failure 이후 미출력 turn은 end-to-end 실패로 처리한다.",
            "",
            "| Condition | Scenario completion | Turn completion | Canonical F1 | Final State F1 | State Diff F1 | Strict schema validation |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            *condition_rows,
            "",
            "## Full vs No-memory: paired scenario bootstrap",
            "",
            "Difference는 `Full - No-memory`이며 20개 시나리오를 10,000회 paired bootstrap했다.",
            "",
            "| Metric | Full | No-memory | Difference | 95% CI |",
            "| --- | ---: | ---: | ---: | ---: |",
            *rows,
            "",
            "Rejection retention은 두 조건에서 함께 target을 복원할 수 있는 paired scenario가 없어 "
            "`insufficient_support`다. 현재 holdout에서 CI를 만들지 않는다.",
            "",
            "## Fixed-upstream No-review ranking behavior",
            "",
            "Full의 validated state·query·candidate set을 그대로 두고 review score와 reliability만 제거했다. "
            "추가 LLM 호출은 0회이고 candidate-set identity는 1.000이다.",
            "",
            "| Metric | Value | Scenario-bootstrap 95% CI |",
            "| --- | ---: | ---: |",
            *no_review_rows,
            "",
            f"- Full evidence-ID consistency: {_format_metric(fixed['full_evidence_id_consistency_rate'])}",
            f"- Fixed No-review hard-constraint violation rate: {_format_metric(fixed['hard_constraint_violation_rate'])}",
            "",
            "Review ablation에서 ranking 변화는 review evidence의 causal contribution을 보여주지만, "
            "human relevance judgment가 없으므로 추천 품질 향상을 의미하지는 않는다.",
            "",
            "## Secondary Gold-State oracle diagnostic",
            "",
            "| Metric | Actual Full | Gold-State oracle | Recovery |",
            "| --- | ---: | ---: | ---: |",
            f"| Policy accuracy | {_format_metric(oracle['policy_accuracy']['actual_full'])} | {_format_metric(oracle['policy_accuracy']['gold_state_oracle'])} | {_format_metric(oracle['policy_accuracy']['recovery'])} |",
            f"| Recommendation reach | {_format_metric(oracle['recommendation_reach']['actual_full'])} | {_format_metric(oracle['recommendation_reach']['gold_state_oracle'])} | {_format_metric(oracle['recommendation_reach']['recovery'])} |",
            f"| Hard-filter completion | {_format_metric(oracle['hard_filter_completion']['actual_full'])} | {_format_metric(oracle['hard_filter_completion']['gold_state_oracle'])} | {_format_metric(oracle['hard_filter_completion']['recovery'])} |",
            "",
            f"Oracle candidate availability는 {_format_metric(oracle['oracle_candidate_availability_rate'])}, "
            f"candidate hard-violation rate는 {_format_metric(oracle['oracle_candidate_hard_violation_rate'])}다. "
            "이는 evaluator-side deterministic upper-bound 진단이며 추천 relevance 점수가 아니다.",
            "",
            "## 제외·보류 지표",
            "",
            "- Product NDCG@3 / Review NDCG@3: human relevance label이 없어 official result에서 제외",
            "- provenance accuracy: frozen holdout에 candidate-level provenance gold가 없음",
            "- trade-off direction compliance: relation gold는 있으나 expected product ranking direction이 없음",
            "- situational→persistent promotion error: situational rejection positive case가 없음",
            "- inference restraint와 temporal scope error: hypothesis/status 및 temporal-scope gold가 없음",
            "",
            "기존 3인용 blind packet과 agreement/NDCG 코드는 삭제하지 않고 **Optional / Future Human "
            "Relevance Evaluation** artifact로 보존한다.",
            "",
        ]
    )
