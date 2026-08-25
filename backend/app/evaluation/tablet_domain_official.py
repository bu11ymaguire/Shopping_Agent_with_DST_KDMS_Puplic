"""Metrics for the frozen tablet-domain multi-turn official holdout run.

The scorer is deterministic. It evaluates the live workflow output against the
pre-run gold state/action contract and never supplies a product ranking gold.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any

from app.evaluation.actual_recommendation import product_satisfies_hard_filters
from app.evaluation.tablet_domain_holdout import TabletHoldoutTurn
from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models import DialogueState
from app.models.actual_demo import ActualHardFilters, ActualPipelineTurn
from app.models.understanding import SPNFacetName
from app.nodes.actual_recommendation import generate_actual_query


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _set_counts(expected: set[str], actual: set[str]) -> dict[str, int | bool]:
    return {
        "exact": expected == actual,
        "true_positive": len(expected & actual),
        "false_positive": len(actual - expected),
        "false_negative": len(expected - actual),
    }


def _micro_scores(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    precision = _rate(tp, tp + fp)
    recall = _rate(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def active_state_ids(state: DialogueState) -> set[str]:
    """Return canonical IDs stored as active dialogue preferences."""
    values = []
    if state.category is not None:
        values.append(state.category)
    values.extend(state.hard_constraints.values())
    values.extend(state.soft_constraints.values())
    values.extend(
        value
        for facet in SPNFacetName.__args__
        if (value := state.subjective_needs.get_facet(facet)) is not None
    )
    return {
        value.canonical_id
        for value in values
        if value.status != "superseded"
    }


def _candidate_path(candidate: Any) -> str:
    target = candidate.target
    if target.kind == "facet":
        return f"subjective_needs.{target.facet}"
    prefix = "hard_constraints" if target.scope == "hard" else "soft_constraints"
    return f"{prefix}.{target.key}"


def normalized_state_diff_operations(turn: ActualPipelineTurn) -> set[str]:
    """Map concrete StateDiff paths to the frozen semantic operation vocabulary."""
    changed = set(turn.state_diff.changed_paths)
    operations = {
        f"upsert:{candidate.canonical_id}"
        for candidate in turn.understanding.candidates
        if _candidate_path(candidate) in changed
    }
    if "domain_route" in changed:
        operations.add(f"route:{turn.dialogue_state.domain_route}")

    action = turn.understanding.item_action
    if action is not None:
        action_paths = {
            "reject_first": {"rejected_items"},
            "compare_first_second": {"shortlisted_items"},
            "inspect_current": {"inspected_items", "current_item"},
            "purchase_current": {"purchased_items"},
        }[action.name]
        if changed & action_paths:
            operations.add(f"action:{action.name}")

    tradeoff = turn.understanding.tradeoff
    if tradeoff is not None and "tradeoffs" in changed:
        prioritized = "+".join(tradeoff.prioritized_ids)
        compromised = "+".join(tradeoff.compromised_ids)
        operations.add(f"tradeoff:{prioritized}>{compromised}")
    return operations


def _candidate_scope(candidate: Any) -> str:
    return "facet" if candidate.target.kind == "facet" else candidate.target.scope


def _action_signature(action: Any | None) -> tuple[Any, ...] | None:
    if action is None:
        return None
    reason = action.rejection_reason
    return (
        action.name,
        action.target_rank,
        action.compare_rank,
        reason.canonical_id if reason else None,
        reason.reason_type if reason else None,
    )


def _gold_action_signature(action: Any | None) -> tuple[Any, ...] | None:
    if action is None:
        return None
    return (
        action.name,
        action.target_rank,
        action.compare_rank,
        action.rejection_reason_id,
        action.reason_type,
    )


def _tradeoff_signature(tradeoff: Any | None) -> tuple[Any, ...] | None:
    if tradeoff is None:
        return None
    return (
        frozenset(tradeoff.prioritized_ids),
        frozenset(tradeoff.compromised_ids),
        tradeoff.origin,
    )


def _phase_latency(turn: ActualPipelineTurn) -> dict[str, float]:
    by_node = {trace.node_id: trace.latency_ms for trace in turn.trace}
    response = sum(
        latency
        for node, latency in by_node.items()
        if node.startswith("spn-response-")
    )
    return {
        "understanding_llm": by_node.get("spn-understanding", 0.0),
        "state_and_policy": by_node.get("ra-state-manager", 0.0)
        + by_node.get("spn-policy", 0.0),
        "query_generation": by_node.get("ra-query-generator", 0.0),
        "retrieval_and_cross_encoder": by_node.get("spn-browsing-actions", 0.0),
        "product_ranking": by_node.get("ra-recommendation-engine", 0.0),
        "response_composer_llm": response,
    }


def score_holdout_turn(
    gold: TabletHoldoutTurn,
    turn: ActualPipelineTurn,
    catalog: ExperimentalAmazonCatalog,
    *,
    wall_latency_ms: float,
    gold_active_ids: set[str],
    condition: str,
) -> dict[str, Any]:
    expected_ids = set(gold.gold_candidate_ids)
    actual_ids = {candidate.canonical_id for candidate in turn.understanding.candidates}
    candidate_counts = _set_counts(expected_ids, actual_ids)
    expected_diff = set(gold.gold_state_diff)
    actual_diff = normalized_state_diff_operations(turn)
    diff_counts = _set_counts(expected_diff, actual_diff)

    predicted_scopes = {
        candidate.canonical_id: _candidate_scope(candidate)
        for candidate in turn.understanding.candidates
    }
    matched = expected_ids & actual_ids
    scope_correct = sum(
        predicted_scopes[canonical_id] == gold.gold_candidate_scopes[canonical_id]
        for canonical_id in matched
    )

    current_filters = generate_actual_query(turn.dialogue_state).hard_filters
    expected_filters = gold.expected_hard_filters_after_turn
    allow_budget_overrun = "budget_flexibility" in gold_active_ids
    evaluated_products = 0
    hard_violations = 0
    for ranking in turn.rankings[:3]:
        evaluated_products += 1
        product = catalog.get_product(ranking.product_id)
        if not product_satisfies_hard_filters(
            product,
            expected_filters,
            allow_budget_overrun=allow_budget_overrun,
        ):
            hard_violations += 1

    evidence_cards = len(turn.final_response.product_cards)
    evidence_mismatches = 0
    for card in turn.final_response.product_cards:
        if set(card.ranking.evidence_review_ids) != {
            review.review_id for review in card.evidence_reviews
        }:
            evidence_mismatches += 1

    response_fallback = any(
        trace.node_id.startswith("spn-response-")
        and trace.output_summary.get("composer") == "template-fallback"
        for trace in turn.trace
    )
    retrieval_fallback = bool(
        turn.browse_result
        and turn.browse_result.review_retrieval_fallback_reason
    )
    no_review_contract = None
    if condition == "no_review":
        no_review_contract = all(
            not ranking.evidence_review_ids
            and ranking.score.review_evidence_score == 0
            and ranking.score.evidence_reliability == 0
            for ranking in turn.rankings
        ) and all(not card.evidence_reviews for card in turn.final_response.product_cards)

    predicted_question = (
        turn.policy.question_target.field if turn.policy.question_target else None
    )
    return {
        "turn": gold.turn,
        "utterance": gold.utterance,
        "wall_latency_ms": wall_latency_ms,
        "phase_latency_ms": _phase_latency(turn),
        "domain_route_expected": gold.gold_domain_route,
        "domain_route_actual": turn.understanding.domain_route,
        "domain_route_exact": turn.understanding.domain_route
        == gold.gold_domain_route,
        "intents_expected": gold.gold_intents,
        "intents_actual": turn.understanding.intents,
        "intent_exact": set(turn.understanding.intents) == set(gold.gold_intents),
        "candidate_ids_expected": sorted(expected_ids),
        "candidate_ids_actual": sorted(actual_ids),
        "candidate_counts": candidate_counts,
        "matched_scope_correct": scope_correct,
        "matched_scope_total": len(matched),
        "forbidden_candidate_violations": sorted(
            actual_ids & set(gold.forbidden_candidate_ids)
        ),
        "item_action_exact": _action_signature(turn.understanding.item_action)
        == _gold_action_signature(gold.gold_item_action),
        "tradeoff_exact": _tradeoff_signature(turn.understanding.tradeoff)
        == _tradeoff_signature(gold.gold_tradeoff),
        "state_diff_expected": sorted(expected_diff),
        "state_diff_actual": sorted(actual_diff),
        "state_diff_counts": diff_counts,
        "policy_lane_expected": gold.gold_policy_lane,
        "policy_lane_actual": turn.policy.lane,
        "policy_lane_exact": turn.policy.lane == gold.gold_policy_lane,
        "question_target_expected": gold.gold_question_target,
        "question_target_actual": predicted_question,
        "question_target_exact": predicted_question == gold.gold_question_target,
        "hard_filters_expected": expected_filters.model_dump(mode="json", exclude_none=True),
        "hard_filters_actual": current_filters.model_dump(mode="json", exclude_none=True),
        "hard_filter_exact": current_filters == expected_filters,
        "recommendation_pipeline_completed": bool(
            turn.policy.lane == "recommend-lane"
            and turn.query is not None
            and turn.browse_result is not None
            and turn.recommendation is not None
        ),
        "top3_product_count": evaluated_products,
        "top3_hard_constraint_violations": hard_violations,
        "evidence_card_count": evidence_cards,
        "evidence_mismatch_count": evidence_mismatches,
        "response_template_fallback": response_fallback,
        "review_retrieval_fallback": retrieval_fallback,
        "no_review_contract_exact": no_review_contract,
        "top_product_ids": [item.product_id for item in turn.rankings[:3]],
    }


def aggregate_condition_metrics(
    scenario_records: list[dict[str, Any]],
) -> dict[str, Any]:
    turn_metrics = [
        turn
        for scenario in scenario_records
        for turn in scenario.get("turn_metrics", [])
    ]
    expected_turns = sum(item["expected_turn_count"] for item in scenario_records)
    completed_turns = len(turn_metrics)
    completed_scenarios = sum(item["status"] == "completed" for item in scenario_records)

    def total(path: str) -> int:
        parent, key = path.split(".")
        return sum(int(item[parent][key]) for item in turn_metrics)

    candidate_tp = total("candidate_counts.true_positive")
    candidate_fp = total("candidate_counts.false_positive")
    candidate_fn = total("candidate_counts.false_negative")
    diff_tp = total("state_diff_counts.true_positive")
    diff_fp = total("state_diff_counts.false_positive")
    diff_fn = total("state_diff_counts.false_negative")
    final_tp = sum(item["final_state_counts"]["true_positive"] for item in scenario_records)
    final_fp = sum(item["final_state_counts"]["false_positive"] for item in scenario_records)
    final_fn = sum(item["final_state_counts"]["false_negative"] for item in scenario_records)

    wall_latencies = [item["wall_latency_ms"] for item in turn_metrics]
    phase_names = (
        "understanding_llm",
        "state_and_policy",
        "query_generation",
        "retrieval_and_cross_encoder",
        "product_ranking",
        "response_composer_llm",
    )
    phase_summary = {}
    for phase in phase_names:
        values = [item["phase_latency_ms"][phase] for item in turn_metrics]
        phase_summary[phase] = {
            "median": statistics.median(values) if values else None,
            "p95": _percentile(values, 0.95),
        }

    gold_recommend = [
        item for item in turn_metrics if item["policy_lane_expected"] == "recommend-lane"
    ]
    recommendation_outputs = [
        item for item in turn_metrics if item["recommendation_pipeline_completed"]
    ]
    hard_products = sum(item["top3_product_count"] for item in turn_metrics)
    evidence_cards = sum(item["evidence_card_count"] for item in turn_metrics)
    scope_total = sum(item["matched_scope_total"] for item in turn_metrics)
    no_review_checks = [
        item["no_review_contract_exact"]
        for item in turn_metrics
        if item["no_review_contract_exact"] is not None
    ]

    return {
        "scenario_count": len(scenario_records),
        "scenario_completion_rate": _rate(completed_scenarios, len(scenario_records)),
        "completed_scenario_count": completed_scenarios,
        "turn_output_completion_rate": _rate(completed_turns, expected_turns),
        "completed_turn_count": completed_turns,
        "expected_turn_count": expected_turns,
        "domain_route_accuracy": _rate(
            sum(item["domain_route_exact"] for item in turn_metrics), completed_turns
        ),
        "intent_exact_accuracy": _rate(
            sum(item["intent_exact"] for item in turn_metrics), completed_turns
        ),
        "canonical_id_exact_accuracy": _rate(
            sum(item["candidate_counts"]["exact"] for item in turn_metrics),
            completed_turns,
        ),
        "canonical_id_micro": _micro_scores(candidate_tp, candidate_fp, candidate_fn),
        "matched_scope_accuracy": _rate(
            sum(item["matched_scope_correct"] for item in turn_metrics), scope_total
        ),
        "forbidden_candidate_violation_rate": _rate(
            sum(bool(item["forbidden_candidate_violations"]) for item in turn_metrics),
            completed_turns,
        ),
        "item_action_exact_accuracy": _rate(
            sum(item["item_action_exact"] for item in turn_metrics), completed_turns
        ),
        "tradeoff_exact_accuracy": _rate(
            sum(item["tradeoff_exact"] for item in turn_metrics), completed_turns
        ),
        "state_diff_exact_accuracy": _rate(
            sum(item["state_diff_counts"]["exact"] for item in turn_metrics),
            completed_turns,
        ),
        "state_diff_micro": _micro_scores(diff_tp, diff_fp, diff_fn),
        "final_state_micro": _micro_scores(final_tp, final_fp, final_fn),
        "policy_lane_accuracy": _rate(
            sum(item["policy_lane_exact"] for item in turn_metrics), completed_turns
        ),
        "question_target_accuracy": _rate(
            sum(item["question_target_exact"] for item in turn_metrics), completed_turns
        ),
        "recommendation_lane_reach_rate": _rate(
            sum(item["policy_lane_actual"] == "recommend-lane" for item in gold_recommend),
            len(gold_recommend),
        ),
        "recommendation_pipeline_completion_rate": _rate(
            len(recommendation_outputs), len(gold_recommend)
        ),
        "hard_constraint_completion_rate": _rate(
            sum(item["hard_filter_exact"] for item in turn_metrics), completed_turns
        ),
        "hard_constraint_violation_rate": _rate(
            sum(item["top3_hard_constraint_violations"] for item in turn_metrics),
            hard_products,
        ),
        "response_template_fallback_rate": _rate(
            sum(item["response_template_fallback"] for item in turn_metrics),
            completed_turns,
        ),
        "review_retrieval_fallback_rate": _rate(
            sum(item["review_retrieval_fallback"] for item in recommendation_outputs),
            len(recommendation_outputs),
        ),
        "evidence_consistency_rate": (
            1
            - sum(item["evidence_mismatch_count"] for item in turn_metrics)
            / evidence_cards
            if evidence_cards
            else None
        ),
        "no_review_contract_accuracy": _rate(sum(no_review_checks), len(no_review_checks)),
        "turn_wall_latency_ms": {
            "median": statistics.median(wall_latencies) if wall_latencies else None,
            "p95": _percentile(wall_latencies, 0.95),
            "max": max(wall_latencies) if wall_latencies else None,
        },
        "phase_latency_ms": phase_summary,
        "error_scenarios": [
            {
                "scenario_id": item["scenario_id"],
                "error_type": item.get("error_type"),
                "error_message": item.get("error_message"),
            }
            for item in scenario_records
            if item["status"] != "completed"
        ],
    }


def compare_condition_outputs(report: dict[str, Any]) -> dict[str, Any]:
    """Return paired diagnostics after every condition has completed."""
    indexed: dict[str, dict[tuple[str, int], dict[str, Any]]] = {}
    for condition, payload in report["conditions"].items():
        indexed[condition] = {
            (scenario["scenario_id"], turn["turn"]): turn
            for scenario in payload["scenarios"]
            for turn in scenario.get("turn_metrics", [])
        }

    shared_full_memory = sorted(set(indexed["full"]) & set(indexed["no_memory"]))
    shared_full_review = sorted(set(indexed["full"]) & set(indexed["no_review"]))
    scenario_index = {
        condition: {
            item["scenario_id"]: item for item in payload["scenarios"]
        }
        for condition, payload in report["conditions"].items()
    }

    def paired_micro(
        keys: list[tuple[str, int]], condition: str, field: str
    ) -> dict[str, float | None]:
        tp = sum(indexed[condition][key][field]["true_positive"] for key in keys)
        fp = sum(indexed[condition][key][field]["false_positive"] for key in keys)
        fn = sum(indexed[condition][key][field]["false_negative"] for key in keys)
        return _micro_scores(tp, fp, fn)

    def paired_accuracy(
        keys: list[tuple[str, int]], condition: str, field: str
    ) -> float | None:
        return _rate(
            sum(bool(indexed[condition][key][field]) for key in keys), len(keys)
        )

    full_diff = paired_micro(shared_full_memory, "full", "state_diff_counts")
    memory_diff = paired_micro(
        shared_full_memory, "no_memory", "state_diff_counts"
    )
    recommend_keys = [
        key
        for key in shared_full_memory
        if indexed["full"][key]["policy_lane_expected"] == "recommend-lane"
    ]
    full_policy = paired_accuracy(
        shared_full_memory, "full", "policy_lane_exact"
    )
    memory_policy = paired_accuracy(
        shared_full_memory, "no_memory", "policy_lane_exact"
    )
    full_reach = _rate(
        sum(
            indexed["full"][key]["policy_lane_actual"] == "recommend-lane"
            for key in recommend_keys
        ),
        len(recommend_keys),
    )
    memory_reach = _rate(
        sum(
            indexed["no_memory"][key]["policy_lane_actual"] == "recommend-lane"
            for key in recommend_keys
        ),
        len(recommend_keys),
    )
    full_filters = paired_accuracy(
        shared_full_memory, "full", "hard_filter_exact"
    )
    memory_filters = paired_accuracy(
        shared_full_memory, "no_memory", "hard_filter_exact"
    )
    shared_completed_scenarios = sorted(
        scenario_id
        for scenario_id in set(scenario_index["full"])
        & set(scenario_index["no_memory"])
        if scenario_index["full"][scenario_id]["status"] == "completed"
        and scenario_index["no_memory"][scenario_id]["status"] == "completed"
    )

    def scenario_final_micro(condition: str) -> dict[str, float | None]:
        tp = sum(
            scenario_index[condition][scenario_id]["final_state_counts"][
                "true_positive"
            ]
            for scenario_id in shared_completed_scenarios
        )
        fp = sum(
            scenario_index[condition][scenario_id]["final_state_counts"][
                "false_positive"
            ]
            for scenario_id in shared_completed_scenarios
        )
        fn = sum(
            scenario_index[condition][scenario_id]["final_state_counts"][
                "false_negative"
            ]
            for scenario_id in shared_completed_scenarios
        )
        return _micro_scores(tp, fp, fn)

    full_final = scenario_final_micro("full")
    memory_final = scenario_final_micro("no_memory")

    upstream_equal = 0
    lane_equal = 0
    filter_equal = 0
    top3_exact = 0
    top1_changed = 0
    jaccards = []
    for key in shared_full_review:
        full = indexed["full"][key]
        other = indexed["no_review"][key]
        upstream_equal += (
            full["candidate_ids_actual"] == other["candidate_ids_actual"]
            and full["intents_actual"] == other["intents_actual"]
            and full["domain_route_actual"] == other["domain_route_actual"]
        )
        lane_equal += full["policy_lane_actual"] == other["policy_lane_actual"]
        filter_equal += full["hard_filters_actual"] == other["hard_filters_actual"]
        full_top = full["top_product_ids"]
        other_top = other["top_product_ids"]
        top3_exact += full_top == other_top
        top1_changed += bool(full_top and other_top and full_top[0] != other_top[0])
        union = set(full_top) | set(other_top)
        if union:
            jaccards.append(len(set(full_top) & set(other_top)) / len(union))

    return {
        "full_minus_no_memory": {
            "paired_completed_turn_count": len(shared_full_memory),
            "paired_completed_scenario_count": len(shared_completed_scenarios),
            "full_state_diff_micro_f1": full_diff["f1"],
            "no_memory_state_diff_micro_f1": memory_diff["f1"],
            "state_diff_micro_f1_delta": (
                full_diff["f1"] - memory_diff["f1"]
                if full_diff["f1"] is not None and memory_diff["f1"] is not None
                else None
            ),
            "full_policy_lane_accuracy": full_policy,
            "no_memory_policy_lane_accuracy": memory_policy,
            "policy_lane_accuracy_delta": (
                full_policy - memory_policy
                if full_policy is not None and memory_policy is not None
                else None
            ),
            "full_recommendation_lane_reach": full_reach,
            "no_memory_recommendation_lane_reach": memory_reach,
            "recommendation_lane_reach_delta": (
                full_reach - memory_reach
                if full_reach is not None and memory_reach is not None
                else None
            ),
            "full_hard_constraint_completion": full_filters,
            "no_memory_hard_constraint_completion": memory_filters,
            "hard_constraint_completion_delta": (
                full_filters - memory_filters
                if full_filters is not None and memory_filters is not None
                else None
            ),
            "full_final_state_micro_f1": full_final["f1"],
            "no_memory_final_state_micro_f1": memory_final["f1"],
            "final_state_micro_f1_delta": (
                full_final["f1"] - memory_final["f1"]
                if full_final["f1"] is not None
                and memory_final["f1"] is not None
                else None
            ),
        },
        "full_vs_no_review": {
            "paired_completed_turn_count": len(shared_full_review),
            "upstream_understanding_identity_rate": _rate(
                upstream_equal, len(shared_full_review)
            ),
            "policy_lane_identity_rate": _rate(lane_equal, len(shared_full_review)),
            "hard_filter_identity_rate": _rate(filter_equal, len(shared_full_review)),
            "top3_order_identity_rate": _rate(top3_exact, len(shared_full_review)),
            "top1_change_rate": _rate(top1_changed, len(shared_full_review)),
            "mean_top3_jaccard": statistics.mean(jaccards) if jaccards else None,
        },
    }


def trace_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    understanding = [item for item in records if item.get("node") == "spn-understanding"]
    return {
        "record_count": len(records),
        "understanding_call_count": len(understanding),
        "understanding_schema_validation_rate": _rate(
            sum(item.get("validation_success", False) for item in understanding),
            len(understanding),
        ),
        "schema_repair_count": sum(int(item.get("retry_count", 0)) for item in records),
        "transport_retry_count": sum(
            int(item.get("transport_retry_count", 0)) for item in records
        ),
        "fallback_count": sum(bool(item.get("fallback_used")) for item in records),
        "reported_models": dict(
            Counter(
                item.get("reported_model") or "not_reported"
                for item in records
            )
        ),
        "structured_modes": dict(
            Counter(item.get("structured_mode") or "none" for item in records)
        ),
    }
