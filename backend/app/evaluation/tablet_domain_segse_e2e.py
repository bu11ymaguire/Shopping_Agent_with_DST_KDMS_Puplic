"""Case-local multi-turn scoring for the SEGSE v1.2 development guardrail."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.actual_recommendation import product_satisfies_hard_filters
from app.evaluation.tablet_domain_segse import (
    _transition_operations,
    active_final_tokens,
    expected_state,
    gold_candidate_ids,
    gold_event_pairs,
    gold_material_operations,
)
from app.models import DialogueState
from app.models.actual_demo import (
    ACTUAL_FACET_IDS,
    ACTUAL_PREFERENCE_IDS,
    ActualHardFilters,
    ActualPipelineTurn,
    TabletDomainCanonicalId,
)
from app.models.understanding import IntentName
from app.nodes.actual_policy import select_actual_policy
from app.nodes.actual_recommendation import generate_actual_query
from app.nodes.actual_state_manager import create_tablet_environment_state


ARM_ORDER = ("b0_c_semantic_noop", "d4_segse_v12")


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SEGSEE2EGoldEvent(_Contract):
    canonical_id: TabletDomainCanonicalId
    act: Literal["assert", "confirm", "retract", "refine"]
    scope_after: Literal["hard", "soft"] | None = None
    value_after: str | None = None
    evidence_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def event_contract(self) -> "SEGSEE2EGoldEvent":
        if self.act in {"confirm", "retract"}:
            if self.scope_after is not None or self.value_after is not None:
                raise ValueError("confirm/retract cannot carry a replacement state")
        elif self.value_after is None:
            raise ValueError("assert/refine require value_after")
        is_preference = self.canonical_id in ACTUAL_PREFERENCE_IDS
        if self.act in {"assert", "refine"} and is_preference != (
            self.scope_after is not None
        ):
            raise ValueError("preference IDs require scope; facet IDs require null scope")
        return self


class SEGSEE2ETurn(_Contract):
    turn: int = Field(ge=1, le=4)
    utterance: str = Field(min_length=1)
    gold_route: Literal["in_domain", "unsupported_category"]
    gold_intents: list[IntentName] = Field(min_length=1)
    gold_events: list[SEGSEE2EGoldEvent]
    forbidden_event_ids: list[TabletDomainCanonicalId] = Field(default_factory=list)
    gold_policy_lane: Literal["clarify-lane", "recommend-lane"]
    gold_question_target: str | None = None
    expected_hard_filters_after_turn: ActualHardFilters

    @model_validator(mode="after")
    def turn_contract(self) -> "SEGSEE2ETurn":
        pairs = [(item.canonical_id, item.act) for item in self.gold_events]
        if len(pairs) != len(set(pairs)):
            raise ValueError("gold event pairs must be unique within a turn")
        if set(item.canonical_id for item in self.gold_events) & set(
            self.forbidden_event_ids
        ):
            raise ValueError("gold and forbidden IDs must not overlap")
        if self.gold_policy_lane == "clarify-lane" and not self.gold_question_target:
            raise ValueError("clarify lane requires a question target")
        if self.gold_policy_lane == "recommend-lane" and self.gold_question_target:
            raise ValueError("recommend lane cannot have a question target")
        for item in self.gold_events:
            if item.evidence_text not in self.utterance:
                raise ValueError("gold evidence must be an exact current-turn substring")
        return self

    def as_case(self, scenario_id: str) -> dict[str, Any]:
        return {
            "id": f"{scenario_id}t{self.turn}",
            "family": scenario_id,
            "utterance": self.utterance,
            "gold_route": self.gold_route,
            "gold_events": [item.model_dump(mode="json") for item in self.gold_events],
            "forbidden_event_ids": list(self.forbidden_event_ids),
        }


class SEGSEE2EScenario(_Contract):
    id: str = Field(pattern=r"^se\d{2}$")
    title: str = Field(min_length=1)
    tags: list[str] = Field(min_length=2)
    turns: list[SEGSEE2ETurn] = Field(min_length=4, max_length=4)
    final_gold_active_tokens: list[str]

    @model_validator(mode="after")
    def scenario_contract(self) -> "SEGSEE2EScenario":
        if [item.turn for item in self.turns] != [1, 2, 3, 4]:
            raise ValueError("scenario turns must be contiguous from one")
        if len(self.final_gold_active_tokens) != len(
            set(self.final_gold_active_tokens)
        ):
            raise ValueError("final gold tokens must be unique")
        return self


class SEGSEE2EDataset(_Contract):
    dataset_version: Literal["tablet-domain-segse-e2e-dev-v1.0"]
    split: Literal["development"]
    status: Literal["frozen_development_fixture_not_confirmatory_evidence"]
    schema_version: Literal["tablet-domain-segse-e2e-dev-v1"]
    design_note: str = Field(min_length=1)
    scenarios: list[SEGSEE2EScenario] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def dataset_contract(self) -> "SEGSEE2EDataset":
        scenario_ids = [item.id for item in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario IDs must be unique")
        utterances = [
            turn.utterance.casefold().strip()
            for scenario in self.scenarios
            for turn in scenario.turns
        ]
        if len(utterances) != len(set(utterances)):
            raise ValueError("utterances must be unique")
        return self


def load_segse_e2e_dataset(path: Path | str) -> SEGSEE2EDataset:
    return SEGSEE2EDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def verify_gold_trajectory(dataset: SEGSEE2EDataset) -> None:
    """Reject gold whose declared downstream effects disagree with deterministic code."""

    for scenario in dataset.scenarios:
        gold_state = create_tablet_environment_state()
        for gold_turn in scenario.turns:
            case = gold_turn.as_case(scenario.id)
            gold_state = expected_state(case, gold_state)
            actual_filters = generate_actual_query(gold_state).hard_filters
            if actual_filters != gold_turn.expected_hard_filters_after_turn:
                raise ValueError(
                    f"{scenario.id}t{gold_turn.turn} hard-filter gold disagrees "
                    "with the deterministic query generator"
                )
            policy = select_actual_policy(gold_state)
            question = policy.question_target.field if policy.question_target else None
            if (
                policy.lane != gold_turn.gold_policy_lane
                or question != gold_turn.gold_question_target
            ):
                raise ValueError(
                    f"{scenario.id}t{gold_turn.turn} policy gold disagrees with "
                    "the deterministic policy"
                )
        if active_final_tokens(gold_state) != set(
            scenario.final_gold_active_tokens
        ):
            raise ValueError(f"{scenario.id} final token declaration is inconsistent")


def _set_counts(gold: set[str], predicted: set[str]) -> dict[str, Any]:
    return {
        "gold": sorted(gold),
        "predicted": sorted(predicted),
        "true_positive": len(gold & predicted),
        "false_positive": len(predicted - gold),
        "false_negative": len(gold - predicted),
        "exact": gold == predicted,
    }


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = _safe_div(tp, tp + fp)
    recall = _safe_div(tp, tp + fn)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(_safe_div(2 * precision * recall, precision + recall), 6),
    }


def _aggregate_set(records: Iterable[Mapping[str, Any]], field: str) -> dict[str, Any]:
    tp = fp = fn = 0
    for item in records:
        counts = item[field]
        if counts is None:
            continue
        tp += int(counts["true_positive"])
        fp += int(counts["false_positive"])
        fn += int(counts["false_negative"])
    return _prf(tp, fp, fn)


def _aggregate_pairs(
    records: Iterable[Mapping[str, Any]], predicted: str, gold: str
) -> dict[str, Any] | None:
    tp = fp = fn = 0
    supported = False
    for item in records:
        raw_predicted = item[predicted]
        if raw_predicted is None:
            continue
        supported = True
        predicted_set = set(raw_predicted)
        gold_set = set(item[gold])
        tp += len(predicted_set & gold_set)
        fp += len(predicted_set - gold_set)
        fn += len(gold_set - predicted_set)
    return _prf(tp, fp, fn) if supported else None


def _event_pair(event: Mapping[str, Any]) -> str:
    return f"{event['canonical_id']}|{event['act']}"


def _token_id(token: str) -> str:
    return token.split("|", 1)[0]


def _active_ids(state: DialogueState) -> set[str]:
    return {_token_id(item) for item in active_final_tokens(state)}


def _operation_pairs(operations: Mapping[str, str]) -> set[str]:
    return {
        f"{canonical_id}|{operation}"
        for canonical_id, operation in operations.items()
    }


def score_e2e_turn(
    *,
    arm: str,
    scenario_id: str,
    gold: SEGSEE2ETurn,
    actual_before: DialogueState,
    actual_after: DialogueState,
    gold_before: DialogueState,
    turn: ActualPipelineTurn | None,
    prior_final_fp_tokens: set[str],
    wall_latency_ms: float | None,
    error: Exception | None = None,
) -> tuple[dict[str, Any], DialogueState, set[str]]:
    case = gold.as_case(scenario_id)
    gold_after = expected_state(case, gold_before)
    gold_candidates = gold_candidate_ids(case)
    gold_typed_pairs = gold_event_pairs(case)
    gold_operations = gold_material_operations(case, gold_before)
    prior_active = _active_ids(actual_before)

    raw_typed_pairs: set[str] | None = None
    authorized_typed_pairs: set[str] | None = None
    authorized_candidates: set[str] | None = None
    metadata_confirmation_ids: set[str] | None = None
    rejected_events: list[dict[str, Any]] = []
    evidence_values: list[str] = []
    route_actual = None
    intents_actual: list[str] = []
    forbidden_predicted: set[str] = set()
    if turn is None:
        raw_candidates: set[str] = set()
        actual_operations: dict[str, str] = {}
    elif arm == "d4_segse_v12":
        understanding = turn.understanding
        raw_events = [
            item.model_dump(mode="json") for item in understanding.segse_raw_events
        ]
        authorized_events = [
            item.model_dump(mode="json")
            for item in understanding.segse_authorized_events
        ]
        raw_typed_pairs = {_event_pair(item) for item in raw_events}
        authorized_typed_pairs = {_event_pair(item) for item in authorized_events}
        raw_candidates = {
            str(item["canonical_id"])
            for item in raw_events
            if item["act"] in {"assert", "refine"}
        }
        authorized_candidates = {
            str(item["canonical_id"])
            for item in authorized_events
            if item["act"] in {"assert", "refine"}
        }
        metadata_confirmation_ids = {
            item.canonical_id for item in understanding.segse_metadata_deltas
        }
        rejected_events = [
            item.model_dump(mode="json")
            for item in understanding.segse_event_rejections
        ]
        evidence_values = [str(item["trigger_evidence_text"]) for item in raw_events]
        actual_operations = {
            item.canonical_id: item.operation
            for item in understanding.segse_material_operations
        }
        route_actual = understanding.domain_route
        intents_actual = list(understanding.intents)
        forbidden_predicted = {
            str(item["canonical_id"]) for item in raw_events
        } & set(gold.forbidden_event_ids)
    else:
        understanding = turn.understanding
        raw_candidates = {
            item.canonical_id for item in understanding.candidates
        }
        evidence_values = [item.evidence_text for item in understanding.candidates]
        actual_operations = _transition_operations(actual_before, actual_after)
        route_actual = understanding.domain_route
        intents_actual = list(understanding.intents)
        forbidden_predicted = raw_candidates & set(gold.forbidden_event_ids)

    raw_candidate_counts = _set_counts(gold_candidates, raw_candidates)
    authorized_candidate_counts = (
        _set_counts(gold_candidates, authorized_candidates)
        if authorized_candidates is not None
        else None
    )
    raw_candidate_fp = raw_candidates - gold_candidates
    c2u_fp = (raw_candidate_fp & prior_active)
    novel_fp = raw_candidate_fp - prior_active
    gold_operation_pairs = _operation_pairs(gold_operations)
    actual_operation_pairs = _operation_pairs(actual_operations)

    gold_final = active_final_tokens(gold_after)
    actual_final = active_final_tokens(actual_after)
    final_fp = actual_final - gold_final
    new_final_fp = final_fp - prior_final_fp_tokens
    gold_retract_ids = {
        item.canonical_id for item in gold.gold_events if item.act == "retract"
    }
    origins = []
    for token in sorted(new_final_fp):
        canonical_id = _token_id(token)
        if canonical_id in gold_retract_ids:
            cause = "failed_retract"
        elif canonical_id in novel_fp:
            cause = "novel_extraction"
        elif canonical_id in c2u_fp:
            cause = "carryover_to_update"
        elif canonical_id in {item.canonical_id for item in gold.gold_events}:
            cause = "incorrect_value_or_scope"
        else:
            cause = "unattributed_state_transition"
        origins.append(
            {
                "scenario_id": scenario_id,
                "turn": gold.turn,
                "token": token,
                "canonical_id": canonical_id,
                "cause": cause,
            }
        )

    expected_filters = gold.expected_hard_filters_after_turn
    actual_filters = generate_actual_query(actual_after).hard_filters
    policy_lane = turn.policy.lane if turn is not None else None
    question_target = (
        turn.policy.question_target.field
        if turn is not None and turn.policy.question_target
        else None
    )
    recommendation_completed = bool(
        turn is not None
        and turn.policy.lane == "recommend-lane"
        and turn.query is not None
        and turn.browse_result is not None
        and turn.recommendation is not None
    )
    # Product checks are attached by the runner, which owns the catalog adapter.
    product_count = 0
    hard_violations = 0

    record = {
        "scenario_id": scenario_id,
        "turn": gold.turn,
        "utterance": gold.utterance,
        "completed": turn is not None and error is None,
        "error_type": type(error).__name__ if error else None,
        "error_message": str(error)[:500] if error else None,
        "wall_latency_ms": wall_latency_ms,
        "route_expected": gold.gold_route,
        "route_actual": route_actual,
        "route_exact": route_actual == gold.gold_route,
        "intents_expected": list(gold.gold_intents),
        "intents_actual": intents_actual,
        "intent_exact": set(intents_actual) == set(gold.gold_intents),
        "gold_event_pairs": sorted(gold_typed_pairs),
        "raw_event_pairs": sorted(raw_typed_pairs) if raw_typed_pairs is not None else None,
        "authorized_event_pairs": (
            sorted(authorized_typed_pairs)
            if authorized_typed_pairs is not None
            else None
        ),
        "raw_candidate": raw_candidate_counts,
        "authorized_candidate": authorized_candidate_counts,
        "candidate_fp_ids": sorted(raw_candidate_fp),
        "c2u_fp_ids": sorted(c2u_fp),
        "novel_candidate_fp_ids": sorted(novel_fp),
        "forbidden_predicted_ids": sorted(forbidden_predicted),
        "gold_has_no_typed_event": not gold.gold_events,
        "gold_has_no_material_delta": not gold_operations,
        "false_assertion_on_no_event": bool(not gold.gold_events and raw_candidates),
        "false_assertion_on_no_material_delta": bool(
            not gold_operations and raw_candidates
        ),
        "evidence": {
            "predicted_count": len(evidence_values),
            "exact_current_substring_count": sum(
                item in gold.utterance for item in evidence_values
            ),
        },
        "rejected_events": rejected_events,
        "gold_material_operations": sorted(gold_operation_pairs),
        "actual_material_operations": sorted(actual_operation_pairs),
        "material_delta": _set_counts(
            set(gold_operations), set(actual_operations)
        ),
        "material_operation": _set_counts(
            gold_operation_pairs, actual_operation_pairs
        ),
        "turnwise_final_state": _set_counts(gold_final, actual_final),
        "new_final_fp_origins": origins,
        "gold_confirmation_ids": sorted(
            item.canonical_id for item in gold.gold_events if item.act == "confirm"
        ),
        "metadata_confirmation_ids": (
            sorted(metadata_confirmation_ids)
            if metadata_confirmation_ids is not None
            else None
        ),
        "policy_lane_expected": gold.gold_policy_lane,
        "policy_lane_actual": policy_lane,
        "policy_lane_exact": policy_lane == gold.gold_policy_lane,
        "question_target_expected": gold.gold_question_target,
        "question_target_actual": question_target,
        "question_target_exact": turn is not None
        and question_target == gold.gold_question_target,
        "hard_filters_expected": expected_filters.model_dump(
            mode="json", exclude_none=True
        ),
        "hard_filters_actual": actual_filters.model_dump(
            mode="json", exclude_none=True
        ),
        "hard_filter_exact": turn is not None and actual_filters == expected_filters,
        "recommendation_pipeline_completed": recommendation_completed,
        "top3_product_count": product_count,
        "top3_hard_constraint_violations": hard_violations,
    }
    return record, gold_after, final_fp


def attach_catalog_hard_filter_checks(
    record: dict[str, Any],
    turn: ActualPipelineTurn | None,
    catalog: Any,
) -> None:
    """Score ranked products against gold filters without coupling the core scorer."""

    if turn is None:
        return
    expected = ActualHardFilters.model_validate(record["hard_filters_expected"])
    count = violations = 0
    for ranking in turn.rankings[:3]:
        count += 1
        product = catalog.get_product(ranking.product_id)
        if not product_satisfies_hard_filters(
            product, expected, allow_budget_overrun=False
        ):
            violations += 1
    record["top3_product_count"] = count
    record["top3_hard_constraint_violations"] = violations


def _operation_recall(
    records: list[Mapping[str, Any]], labels: set[str]
) -> dict[str, Any]:
    gold: list[str] = []
    matched = 0
    for item in records:
        predicted = set(item["actual_material_operations"])
        targets = [
            pair
            for pair in item["gold_material_operations"]
            if pair.rsplit("|", 1)[1] in labels
        ]
        gold.extend(targets)
        matched += sum(pair in predicted for pair in targets)
    return {
        "target_count": len(gold),
        "true_positive": matched,
        "recall": round(_safe_div(matched, len(gold)), 6),
    }


def aggregate_e2e_arm(
    scenario_records: list[Mapping[str, Any]],
) -> dict[str, Any]:
    turns = [
        turn for scenario in scenario_records for turn in scenario["turn_metrics"]
    ]
    expected_turns = sum(int(item["expected_turn_count"]) for item in scenario_records)
    completed = sum(bool(item["completed"]) for item in turns)
    final_counts = [item["final_state_counts"] for item in scenario_records]
    final_tp = sum(int(item["true_positive"]) for item in final_counts)
    final_fp = sum(int(item["false_positive"]) for item in final_counts)
    final_fn = sum(int(item["false_negative"]) for item in final_counts)
    evidence_predicted = sum(item["evidence"]["predicted_count"] for item in turns)
    evidence_valid = sum(
        item["evidence"]["exact_current_substring_count"] for item in turns
    )
    recommendation_gold = [
        item for item in turns if item["policy_lane_expected"] == "recommend-lane"
    ]
    ranked_products = sum(item["top3_product_count"] for item in turns)
    confirmation_gold = sum(len(item["gold_confirmation_ids"]) for item in turns)
    confirmation_tp = sum(
        len(
            set(item["gold_confirmation_ids"])
            & set(item["metadata_confirmation_ids"] or [])
        )
        for item in turns
    )
    origins = [
        origin for item in turns for origin in item["new_final_fp_origins"]
    ]
    return {
        "scenario_count": len(scenario_records),
        "expected_turn_count": expected_turns,
        "completed_turn_count": completed,
        "turn_output_completion_rate": round(
            _safe_div(completed, expected_turns), 6
        ),
        "route_accuracy": round(
            _safe_div(sum(bool(item["route_exact"]) for item in turns), expected_turns),
            6,
        ),
        "intent_exact_accuracy": round(
            _safe_div(sum(bool(item["intent_exact"]) for item in turns), expected_turns),
            6,
        ),
        "raw_candidate": _aggregate_set(turns, "raw_candidate"),
        "authorized_candidate": (
            _aggregate_set(turns, "authorized_candidate")
            if any(item["authorized_candidate"] is not None for item in turns)
            else None
        ),
        "raw_typed_event": _aggregate_pairs(
            turns, "raw_event_pairs", "gold_event_pairs"
        ),
        "authorized_typed_event": _aggregate_pairs(
            turns, "authorized_event_pairs", "gold_event_pairs"
        ),
        "material_delta": _aggregate_set(turns, "material_delta"),
        "material_operation": _aggregate_set(turns, "material_operation"),
        "turnwise_accumulated_state": _aggregate_set(
            turns, "turnwise_final_state"
        ),
        "scenario_final_state": _prf(final_tp, final_fp, final_fn),
        "candidate_fp_count": sum(len(item["candidate_fp_ids"]) for item in turns),
        "c2u_fp_count": sum(len(item["c2u_fp_ids"]) for item in turns),
        "novel_candidate_fp_count": sum(
            len(item["novel_candidate_fp_ids"]) for item in turns
        ),
        "forbidden_prediction_count": sum(
            len(item["forbidden_predicted_ids"]) for item in turns
        ),
        "no_event_turn_count": sum(bool(item["gold_has_no_typed_event"]) for item in turns),
        "false_assertion_on_no_event_turn_count": sum(
            bool(item["false_assertion_on_no_event"]) for item in turns
        ),
        "no_material_turn_count": sum(
            bool(item["gold_has_no_material_delta"]) for item in turns
        ),
        "false_assertion_on_no_material_turn_count": sum(
            bool(item["false_assertion_on_no_material_delta"]) for item in turns
        ),
        "first_final_fp_origins": origins,
        "first_final_fp_origin_counts": {
            cause: sum(item["cause"] == cause for item in origins)
            for cause in sorted({item["cause"] for item in origins})
        },
        "lexical_evidence_validity": {
            "predicted_count": evidence_predicted,
            "exact_current_substring_count": evidence_valid,
            "rate": round(_safe_div(evidence_valid, evidence_predicted), 6),
        },
        "correction_recall": _operation_recall(
            turns, {"update_value", "update_scope", "refine"}
        ),
        "retract_recall": _operation_recall(turns, {"retract"}),
        "reactivation_recall": _operation_recall(turns, {"reactivate"}),
        "confirmation_metadata_recall": (
            {
                "target_count": confirmation_gold,
                "true_positive": confirmation_tp,
                "recall": round(_safe_div(confirmation_tp, confirmation_gold), 6),
            }
            if any(item["metadata_confirmation_ids"] is not None for item in turns)
            else {"status": "not_supported_by_arm"}
        ),
        "event_rejection_count": sum(len(item["rejected_events"]) for item in turns),
        "policy_lane_accuracy": round(
            _safe_div(
                sum(bool(item["policy_lane_exact"]) for item in turns), expected_turns
            ),
            6,
        ),
        "question_target_accuracy": round(
            _safe_div(
                sum(bool(item["question_target_exact"]) for item in turns),
                expected_turns,
            ),
            6,
        ),
        "recommendation_pipeline_completion_rate": round(
            _safe_div(
                sum(
                    bool(item["recommendation_pipeline_completed"])
                    for item in recommendation_gold
                ),
                len(recommendation_gold),
            ),
            6,
        ),
        "hard_filter_completion_rate": round(
            _safe_div(sum(bool(item["hard_filter_exact"]) for item in turns), expected_turns),
            6,
        ),
        "top3_hard_constraint_violation_rate": round(
            _safe_div(
                sum(item["top3_hard_constraint_violations"] for item in turns),
                ranked_products,
            ),
            6,
        ),
    }


def evaluate_e2e_development_gate(
    baseline: Mapping[str, Any], treatment: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply FP improvement and downstream non-inferiority development guards."""

    def reduction(field: str) -> float | None:
        before = int(baseline[field])
        after = int(treatment[field])
        return _safe_div(before - after, before) if before else None

    candidate_reduction = reduction("candidate_fp_count")
    c2u_reduction = reduction("c2u_fp_count")
    deltas = {
        "raw_candidate_recall": round(
            treatment["raw_candidate"]["recall"]
            - baseline["raw_candidate"]["recall"],
            6,
        ),
        "correction_recall": round(
            treatment["correction_recall"]["recall"]
            - baseline["correction_recall"]["recall"],
            6,
        ),
        "retract_recall": round(
            treatment["retract_recall"]["recall"]
            - baseline["retract_recall"]["recall"],
            6,
        ),
        "reactivation_recall": round(
            treatment["reactivation_recall"]["recall"]
            - baseline["reactivation_recall"]["recall"],
            6,
        ),
        "turnwise_state_f1": round(
            treatment["turnwise_accumulated_state"]["f1"]
            - baseline["turnwise_accumulated_state"]["f1"],
            6,
        ),
        "scenario_final_state_f1": round(
            treatment["scenario_final_state"]["f1"]
            - baseline["scenario_final_state"]["f1"],
            6,
        ),
        "hard_filter_completion": round(
            treatment["hard_filter_completion_rate"]
            - baseline["hard_filter_completion_rate"],
            6,
        ),
        "policy_lane_accuracy": round(
            treatment["policy_lane_accuracy"] - baseline["policy_lane_accuracy"],
            6,
        ),
        "recommendation_pipeline_completion": round(
            treatment["recommendation_pipeline_completion_rate"]
            - baseline["recommendation_pipeline_completion_rate"],
            6,
        ),
        "turn_output_completion": round(
            treatment["turn_output_completion_rate"]
            - baseline["turn_output_completion_rate"],
            6,
        ),
    }
    checks: dict[str, bool | None] = {
        "baseline_turn_output_completion_at_least_95_percent": baseline[
            "turn_output_completion_rate"
        ]
        >= 0.95,
        "treatment_turn_output_completion_at_least_95_percent": treatment[
            "turn_output_completion_rate"
        ]
        >= 0.95,
        "candidate_fp_reduction_at_least_30_percent": (
            candidate_reduction >= 0.30
            if candidate_reduction is not None
            else treatment["candidate_fp_count"] == 0
        ),
        "c2u_fp_reduction_at_least_50_percent": (
            c2u_reduction >= 0.50
            if c2u_reduction is not None
            else treatment["c2u_fp_count"] == 0
        ),
        "no_event_false_assertions_not_increased": treatment[
            "false_assertion_on_no_event_turn_count"
        ]
        <= baseline["false_assertion_on_no_event_turn_count"],
        "turnwise_final_fp_not_increased": treatment[
            "turnwise_accumulated_state"
        ]["false_positive"]
        <= baseline["turnwise_accumulated_state"]["false_positive"],
        "scenario_final_fp_not_increased": treatment["scenario_final_state"][
            "false_positive"
        ]
        <= baseline["scenario_final_state"]["false_positive"],
        "raw_candidate_recall_drop_at_most_2pp": deltas[
            "raw_candidate_recall"
        ]
        >= -0.02,
        "correction_recall_drop_at_most_2pp": deltas["correction_recall"] >= -0.02,
        "retract_recall_drop_at_most_2pp": deltas["retract_recall"] >= -0.02,
        "reactivation_recall_drop_at_most_2pp": deltas["reactivation_recall"] >= -0.02,
        "turnwise_state_f1_drop_at_most_001": deltas["turnwise_state_f1"] >= -0.01,
        "scenario_final_state_f1_drop_at_most_001": deltas[
            "scenario_final_state_f1"
        ]
        >= -0.01,
        "hard_filter_completion_drop_at_most_1pp": deltas[
            "hard_filter_completion"
        ]
        >= -0.01,
        "policy_lane_accuracy_drop_at_most_2pp": deltas["policy_lane_accuracy"]
        >= -0.02,
        "recommendation_completion_drop_at_most_2pp": deltas[
            "recommendation_pipeline_completion"
        ]
        >= -0.02,
        "turn_output_completion_drop_at_most_05pp": deltas[
            "turn_output_completion"
        ]
        >= -0.005,
    }
    execution_valid = bool(
        checks["baseline_turn_output_completion_at_least_95_percent"]
        and checks["treatment_turn_output_completion_at_least_95_percent"]
    )
    return {
        "status": (
            "development_run_invalid_insufficient_output"
            if not execution_valid
            else "development_gate_passed_confirmatory_pending"
            if all(checks.values())
            else "development_gate_failed"
        ),
        "execution_valid": execution_valid,
        "candidate_fp_reduction_fraction": (
            round(candidate_reduction, 6) if candidate_reduction is not None else None
        ),
        "c2u_fp_reduction_fraction": (
            round(c2u_reduction, 6) if c2u_reduction is not None else None
        ),
        "metric_deltas": deltas,
        "checks": checks,
        "claim_boundary": (
            "This frozen development guardrail can reject or revise v1.2, but cannot "
            "serve as untouched confirmatory evidence."
        ),
    }


def facet_ids() -> set[str]:
    return set().union(*ACTUAL_FACET_IDS.values())


__all__ = [
    "ARM_ORDER",
    "SEGSEE2EDataset",
    "SEGSEE2EScenario",
    "SEGSEE2ETurn",
    "aggregate_e2e_arm",
    "attach_catalog_hard_filter_checks",
    "evaluate_e2e_development_gate",
    "load_segse_e2e_dataset",
    "score_e2e_turn",
    "verify_gold_trajectory",
]
