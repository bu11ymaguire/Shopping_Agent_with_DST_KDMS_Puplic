"""Development-only scoring for the B0 versus SEGSE-lite contrast fixture.

The fixture contains independent one-turn state transitions.  This evaluator keeps
the four layers separate: raw proposal, deterministic authorization, material state
transition, and accumulated state.  It deliberately does not score retrieval,
ranking, or response quality.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.models import DialogueState, PreferenceValue
from app.models.actual_demo import ACTUAL_FACET_IDS
from app.models.understanding import SPNFacetName
from app.nodes.actual_state_manager import create_tablet_environment_state


ARM_ORDER = ("b0_c_semantic_noop", "d4_segse_lite")


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _prf(true_positive: int, false_positive: int, false_negative: int) -> dict[str, Any]:
    precision = _safe_div(true_positive, true_positive + false_positive)
    recall = _safe_div(true_positive, true_positive + false_negative)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(_safe_div(2 * precision * recall, precision + recall), 6),
    }


def _set_counts(gold: set[str], predicted: set[str]) -> dict[str, Any]:
    return {
        "gold": sorted(gold),
        "predicted": sorted(predicted),
        "true_positive": len(gold & predicted),
        "false_positive": len(predicted - gold),
        "false_negative": len(gold - predicted),
        "exact": gold == predicted,
    }


def _facet_for_id(canonical_id: str) -> str:
    return next(
        facet
        for facet, canonical_ids in ACTUAL_FACET_IDS.items()
        if canonical_id in canonical_ids
    )


def seed_dialogue_state(case: Mapping[str, Any]) -> DialogueState:
    """Build exactly the prior state declared by one development case."""

    state = create_tablet_environment_state()
    for fact in case.get("prior_facts", []):
        origin = str(fact["origin"])
        value = PreferenceValue(
            canonical_id=str(fact["canonical_id"]),
            value_text=str(fact["value_text"]),
            origin=origin,  # type: ignore[arg-type]
            confidence=0.68 if origin == "inferred" else 0.96,
            status=str(fact["status"]),  # type: ignore[arg-type]
            evidence_turn_ids=["fixture-prior"],
            updated_at_turn_id="fixture-prior",
        )
        scope = fact.get("scope")
        if scope == "hard":
            state.hard_constraints[value.canonical_id] = value
        elif scope == "soft":
            state.soft_constraints[value.canonical_id] = value
        else:
            state.subjective_needs.set_facet(  # type: ignore[arg-type]
                _facet_for_id(value.canonical_id), value
            )
    return state


def previous_state_summary(state: DialogueState) -> dict[str, Any]:
    """Mirror the workflow summary without requiring a catalog instance."""

    return {
        "category": state.category.model_dump(mode="json") if state.category else None,
        "hard_constraints": {
            key: value.model_dump(mode="json")
            for key, value in state.hard_constraints.items()
        },
        "soft_constraints": {
            key: value.model_dump(mode="json")
            for key, value in state.soft_constraints.items()
        },
        "subjective_needs": state.subjective_needs.model_dump(mode="json"),
        "current_item_rank_context": state.current_item,
        "visible_ranked_products": [],
    }


def _state_records(
    state: DialogueState, *, include_superseded: bool = True
) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for scope, values in (
        ("hard", state.hard_constraints),
        ("soft", state.soft_constraints),
    ):
        for canonical_id, value in values.items():
            if include_superseded or value.status != "superseded":
                records[canonical_id] = {
                    "scope": scope,
                    "value_text": value.value_text,
                    "origin": value.origin,
                    "status": value.status,
                }
    for facet in SPNFacetName.__args__:
        value = state.subjective_needs.get_facet(facet)
        if value is not None and (
            include_superseded or value.status != "superseded"
        ):
            records[value.canonical_id] = {
                "scope": None,
                "value_text": value.value_text,
                "origin": value.origin,
                "status": value.status,
            }
    return records


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalized_hard(canonical_id: str, value_text: str) -> str:
    text = _normalized_text(value_text)
    if canonical_id == "operating_system":
        for label in ("android", "ipados", "fire os", "windows", "chrome os"):
            if label in text:
                return label
        return text
    match = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", text)
    if not match:
        return text
    number = float(match.group().replace(",", ""))
    if canonical_id == "max_weight":
        if re.search(r"\b(?:lb|lbs|pound|pounds)\b", text):
            number *= 453.59237
        elif re.search(r"\b(?:kg|kilogram|kilograms)\b", text):
            number *= 1000
        elif re.search(r"\b(?:oz|ounce|ounces)\b", text):
            number *= 28.349523
        number = round(number)
    return f"{number:g}"


def _material_signature(canonical_id: str, record: Mapping[str, Any]) -> tuple[Any, ...]:
    status = str(record["status"])
    if status == "superseded":
        return ("inactive",)
    scope = record.get("scope")
    value = str(record["value_text"])
    normalized = (
        _normalized_hard(canonical_id, value)
        if scope == "hard"
        else _normalized_text(value)
    )
    return ("active", scope, normalized)


def active_final_tokens(state: DialogueState) -> set[str]:
    """Final-state units: hard values are normalized; qualitative facts use ID/scope."""

    tokens: set[str] = set()
    for canonical_id, record in _state_records(
        state, include_superseded=False
    ).items():
        scope = record.get("scope")
        if scope == "hard":
            value = _normalized_hard(canonical_id, str(record["value_text"]))
            tokens.add(f"{canonical_id}|hard|{value}")
        else:
            tokens.add(f"{canonical_id}|{scope or 'facet'}")
    return tokens


def _gold_events(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in case.get("gold_events", [])]


def gold_event_pairs(case: Mapping[str, Any]) -> set[str]:
    return {
        f"{event['canonical_id']}|{event['act']}" for event in _gold_events(case)
    }


def gold_candidate_ids(case: Mapping[str, Any]) -> set[str]:
    return {
        str(event["canonical_id"])
        for event in _gold_events(case)
        if event["act"] in {"assert", "refine"}
    }


def _gold_operations(
    case: Mapping[str, Any], before: DialogueState
) -> dict[str, str]:
    records = _state_records(before)
    operations: dict[str, str] = {}
    for event in _gold_events(case):
        canonical_id = str(event["canonical_id"])
        act = str(event["act"])
        prior = records.get(canonical_id)
        if act == "confirm":
            continue
        if act == "retract":
            if prior is not None and prior["status"] != "superseded":
                operations[canonical_id] = "retract"
            continue
        if prior is None:
            operations[canonical_id] = "add"
        elif prior["status"] == "superseded":
            operations[canonical_id] = "reactivate"
        elif event.get("scope_after") != prior.get("scope"):
            operations[canonical_id] = "update_scope"
        elif act == "refine" and _normalized_text(str(event["value_after"])) != _normalized_text(
            str(prior["value_text"])
        ):
            operations[canonical_id] = "refine"
        elif event.get("scope_after") == "hard" and _normalized_hard(
            canonical_id, str(event["value_after"])
        ) != _normalized_hard(canonical_id, str(prior["value_text"])):
            operations[canonical_id] = "update_value"
    return operations


def gold_material_operations(
    case: Mapping[str, Any], before: DialogueState
) -> dict[str, str]:
    """Expose the fixture-derived state-relative gold operations for verification."""

    return _gold_operations(case, before)


def _remove_fact(state: DialogueState, canonical_id: str) -> None:
    state.hard_constraints.pop(canonical_id, None)
    state.soft_constraints.pop(canonical_id, None)
    for facet in SPNFacetName.__args__:
        value = state.subjective_needs.get_facet(facet)
        if value is not None and value.canonical_id == canonical_id:
            setattr(state.subjective_needs, facet, None)


def _put_gold_fact(
    state: DialogueState, event: Mapping[str, Any], *, turn_id: str
) -> None:
    canonical_id = str(event["canonical_id"])
    _remove_fact(state, canonical_id)
    value = PreferenceValue(
        canonical_id=canonical_id,
        value_text=str(event["value_after"]),
        origin="explicit",
        confidence=0.98,
        status="confirmed",
        evidence_turn_ids=[turn_id],
        updated_at_turn_id=turn_id,
    )
    scope = event.get("scope_after")
    if scope == "hard":
        state.hard_constraints[canonical_id] = value
    elif scope == "soft":
        state.soft_constraints[canonical_id] = value
    else:
        state.subjective_needs.set_facet(  # type: ignore[arg-type]
            _facet_for_id(canonical_id), value
        )


def expected_state(case: Mapping[str, Any], before: DialogueState) -> DialogueState:
    """Independent gold-state transition used by the accumulated-state score."""

    state = before.model_copy(deep=True)
    state.domain_route = str(case["gold_route"])  # type: ignore[assignment]
    operations = _gold_operations(case, before)
    for event in _gold_events(case):
        canonical_id = str(event["canonical_id"])
        act = str(event["act"])
        if act == "confirm":
            record = _state_records(state).get(canonical_id)
            if record is None:
                continue
            # Confirmation changes support/provenance, not the final semantic unit.
            continue
        if act == "retract":
            if canonical_id not in operations:
                continue
            for collection in (state.hard_constraints, state.soft_constraints):
                value = collection.get(canonical_id)
                if value is not None:
                    collection[canonical_id] = value.model_copy(
                        update={"status": "superseded", "updated_at_turn_id": "gold-turn"}
                    )
            for facet in SPNFacetName.__args__:
                value = state.subjective_needs.get_facet(facet)
                if value is not None and value.canonical_id == canonical_id:
                    setattr(
                        state.subjective_needs,
                        facet,
                        value.model_copy(
                            update={
                                "status": "superseded",
                                "updated_at_turn_id": "gold-turn",
                            }
                        ),
                    )
            continue
        if canonical_id in operations:
            _put_gold_fact(state, event, turn_id="gold-turn")
    return state


def _transition_operations(
    before: DialogueState, after: DialogueState
) -> dict[str, str]:
    prior = _state_records(before)
    current = _state_records(after)
    operations: dict[str, str] = {}
    for canonical_id in set(prior) | set(current):
        old = prior.get(canonical_id)
        new = current.get(canonical_id)
        old_active = old is not None and old["status"] != "superseded"
        new_active = new is not None and new["status"] != "superseded"
        if not old_active and new_active:
            operations[canonical_id] = "reactivate" if old is not None else "add"
        elif old_active and not new_active:
            operations[canonical_id] = "retract"
        elif old_active and new_active and old is not None and new is not None:
            if old.get("scope") != new.get("scope"):
                operations[canonical_id] = "update_scope"
            elif _material_signature(canonical_id, old) != _material_signature(
                canonical_id, new
            ):
                operations[canonical_id] = "update_value"
    return operations


def _event_pair(event: Mapping[str, Any]) -> str:
    return f"{event['canonical_id']}|{event['act']}"


def score_case(
    *,
    arm: str,
    case: Mapping[str, Any],
    before: DialogueState,
    after: DialogueState,
    understanding: Any | None,
    error: Exception | None = None,
) -> dict[str, Any]:
    """Score one completed or missing-output case without dropping failures."""

    gold_pairs = gold_event_pairs(case)
    gold_candidates = gold_candidate_ids(case)
    gold_operations = _gold_operations(case, before)
    expected = expected_state(case, before)
    prior_active = set(_state_records(before, include_superseded=False))

    raw_pairs: set[str] | None = None
    authorized_pairs: set[str] | None = None
    authorized_candidate_ids: set[str] | None = None
    metadata_ids: set[str] | None = None
    rejected: list[dict[str, Any]] = []
    if understanding is None:
        proposed_ids: set[str] = set()
        evidence_values: list[str] = []
        route = None
    elif arm == "d4_segse_lite":
        raw_events = [item.model_dump(mode="json") for item in understanding.segse_raw_events]
        authorized_events = [
            item.model_dump(mode="json")
            for item in understanding.segse_authorized_events
        ]
        raw_pairs = {_event_pair(item) for item in raw_events}
        authorized_pairs = {_event_pair(item) for item in authorized_events}
        proposed_ids = {
            str(item["canonical_id"])
            for item in raw_events
            if item["act"] in {"assert", "refine"}
        }
        authorized_candidate_ids = {
            str(item["canonical_id"])
            for item in authorized_events
            if item["act"] in {"assert", "refine"}
        }
        evidence_values = [str(item["trigger_evidence_text"]) for item in raw_events]
        metadata_ids = {
            item.canonical_id for item in understanding.segse_metadata_deltas
        }
        rejected = [
            item.model_dump(mode="json")
            for item in understanding.segse_event_rejections
        ]
        route = understanding.domain_route
    else:
        proposed_ids = {item.canonical_id for item in understanding.candidates}
        evidence_values = [item.evidence_text for item in understanding.candidates]
        route = understanding.domain_route

    actual_operations = _transition_operations(before, after)
    if arm == "d4_segse_lite" and understanding is not None:
        # The state transition alone cannot distinguish a qualitative REFINE from a
        # generic value update.  SEGSE records the manager-derived typed operation.
        actual_operations = {
            item.canonical_id: item.operation
            for item in understanding.segse_material_operations
        }
    material_ids = set(actual_operations)
    expected_final = active_final_tokens(expected)
    actual_final = active_final_tokens(after)
    forbidden = set(case.get("forbidden_event_ids", []))
    evidence_valid = sum(
        1 for evidence in evidence_values if evidence in str(case["utterance"])
    )
    c2u_ids = (proposed_ids & prior_active) - gold_candidates
    candidate_fp_ids = proposed_ids - gold_candidates
    novel_fp_ids = candidate_fp_ids - prior_active
    gold_operation_pairs = {
        f"{canonical_id}|{operation}"
        for canonical_id, operation in gold_operations.items()
    }
    actual_operation_pairs = {
        f"{canonical_id}|{operation}"
        for canonical_id, operation in actual_operations.items()
    }
    gold_confirm_ids = {
        str(item["canonical_id"])
        for item in _gold_events(case)
        if item["act"] == "confirm"
    }
    return {
        "case_id": case["id"],
        "family": case["family"],
        "completed": understanding is not None and error is None,
        "error_type": type(error).__name__ if error else None,
        "error_message": str(error)[:500] if error else None,
        "route_expected": case["gold_route"],
        "route_actual": route,
        "route_exact": route == case["gold_route"],
        "raw_event_pairs": sorted(raw_pairs) if raw_pairs is not None else None,
        "authorized_event_pairs": (
            sorted(authorized_pairs) if authorized_pairs is not None else None
        ),
        "gold_event_pairs": sorted(gold_pairs),
        "raw_candidate": _set_counts(gold_candidates, proposed_ids),
        "authorized_candidate": (
            _set_counts(gold_candidates, authorized_candidate_ids)
            if authorized_candidate_ids is not None
            else None
        ),
        "c2u_fp_ids": sorted(c2u_ids),
        "novel_candidate_fp_ids": sorted(novel_fp_ids),
        "forbidden_predicted_ids": sorted(proposed_ids & forbidden),
        "evidence": {
            "predicted_count": len(evidence_values),
            "exact_current_substring_count": evidence_valid,
        },
        "rejected_events": rejected,
        "gold_material_operations": sorted(gold_operation_pairs),
        "actual_material_operations": sorted(actual_operation_pairs),
        "material_delta": _set_counts(set(gold_operations), material_ids),
        "final_state": _set_counts(expected_final, actual_final),
        "gold_confirmation_ids": sorted(gold_confirm_ids),
        "metadata_confirmation_ids": (
            sorted(metadata_ids) if metadata_ids is not None else None
        ),
    }


def _aggregate_set_metric(
    records: Iterable[Mapping[str, Any]], field: str
) -> dict[str, Any]:
    tp = fp = fn = 0
    for record in records:
        counts = record[field]
        if counts is None:
            continue
        tp += int(counts["true_positive"])
        fp += int(counts["false_positive"])
        fn += int(counts["false_negative"])
    return _prf(tp, fp, fn)


def _aggregate_pair_metric(
    records: Iterable[Mapping[str, Any]], predicted_field: str, gold_field: str
) -> dict[str, Any] | None:
    tp = fp = fn = 0
    supported = False
    for record in records:
        predicted_raw = record[predicted_field]
        if predicted_raw is None:
            continue
        supported = True
        predicted = set(predicted_raw)
        gold = set(record[gold_field])
        tp += len(predicted & gold)
        fp += len(predicted - gold)
        fn += len(gold - predicted)
    return _prf(tp, fp, fn) if supported else None


def aggregate_arm(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    completed = sum(bool(item["completed"]) for item in records)
    evidence_predicted = sum(item["evidence"]["predicted_count"] for item in records)
    evidence_valid = sum(
        item["evidence"]["exact_current_substring_count"] for item in records
    )
    material_pair_records = [
        {
            "predicted": item["actual_material_operations"],
            "gold": item["gold_material_operations"],
        }
        for item in records
    ]
    confirmation_gold = sum(len(item["gold_confirmation_ids"]) for item in records)
    confirmation_tp = sum(
        len(
            set(item["gold_confirmation_ids"])
            & set(item["metadata_confirmation_ids"] or [])
        )
        for item in records
    )
    gold_operation_pairs = [
        pair for item in records for pair in item["gold_material_operations"]
    ]
    actual_operation_pairs = [
        pair for item in records for pair in item["actual_material_operations"]
    ]

    def operation_recall(labels: set[str]) -> dict[str, Any]:
        gold = [pair for pair in gold_operation_pairs if pair.rsplit("|", 1)[1] in labels]
        actual = set(actual_operation_pairs)
        matched = sum(pair in actual for pair in gold)
        return {
            "target_count": len(gold),
            "true_positive": matched,
            "recall": round(_safe_div(matched, len(gold)), 6),
        }

    return {
        "case_count": len(records),
        "completed_case_count": completed,
        "schema_completion_rate": round(_safe_div(completed, len(records)), 6),
        "route_accuracy": round(
            _safe_div(sum(bool(item["route_exact"]) for item in records), len(records)),
            6,
        ),
        "raw_candidate": _aggregate_set_metric(records, "raw_candidate"),
        "authorized_candidate": (
            _aggregate_set_metric(records, "authorized_candidate")
            if any(item["authorized_candidate"] is not None for item in records)
            else None
        ),
        "raw_typed_event": _aggregate_pair_metric(
            records, "raw_event_pairs", "gold_event_pairs"
        ),
        "authorized_typed_event": _aggregate_pair_metric(
            records, "authorized_event_pairs", "gold_event_pairs"
        ),
        "material_delta": _aggregate_set_metric(records, "material_delta"),
        "material_operation_exact": _aggregate_pair_metric(
            material_pair_records, "predicted", "gold"
        ),
        "final_state": _aggregate_set_metric(records, "final_state"),
        "c2u_fp_count": sum(len(item["c2u_fp_ids"]) for item in records),
        "novel_candidate_fp_count": sum(
            len(item["novel_candidate_fp_ids"]) for item in records
        ),
        "forbidden_prediction_count": sum(
            len(item["forbidden_predicted_ids"]) for item in records
        ),
        "lexical_evidence_validity": {
            "predicted_count": evidence_predicted,
            "exact_current_substring_count": evidence_valid,
            "rate": round(_safe_div(evidence_valid, evidence_predicted), 6),
        },
        "correction_recall": operation_recall(
            {"update_value", "update_scope", "refine"}
        ),
        "retract_recall": operation_recall({"retract"}),
        "reactivation_recall": operation_recall({"reactivate"}),
        "confirmation_metadata_recall": (
            {
                "target_count": confirmation_gold,
                "true_positive": confirmation_tp,
                "recall": round(_safe_div(confirmation_tp, confirmation_gold), 6),
            }
            if any(item["metadata_confirmation_ids"] is not None for item in records)
            else {"status": "not_supported_by_arm"}
        ),
        "event_rejection_count": sum(len(item["rejected_events"]) for item in records),
    }


def evaluate_development_gate(
    baseline: Mapping[str, Any], treatment: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply only gates evaluable in this module-level development fixture."""

    baseline_c2u = int(baseline["c2u_fp_count"])
    treatment_c2u = int(treatment["c2u_fp_count"])
    baseline_fp = int(baseline["raw_candidate"]["false_positive"])
    treatment_fp = int(treatment["raw_candidate"]["false_positive"])
    c2u_reduction = (
        _safe_div(baseline_c2u - treatment_c2u, baseline_c2u)
        if baseline_c2u
        else None
    )
    candidate_fp_reduction = (
        _safe_div(baseline_fp - treatment_fp, baseline_fp) if baseline_fp else None
    )
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
        "final_state_f1": round(
            treatment["final_state"]["f1"] - baseline["final_state"]["f1"], 6
        ),
        "schema_completion_rate": round(
            treatment["schema_completion_rate"] - baseline["schema_completion_rate"],
            6,
        ),
    }
    checks: dict[str, bool | None] = {
        "c2u_fp_reduction_at_least_50_percent": (
            c2u_reduction >= 0.5 if c2u_reduction is not None else None
        ),
        "raw_candidate_fp_reduction_at_least_30_percent": (
            candidate_fp_reduction >= 0.3
            if candidate_fp_reduction is not None
            else None
        ),
        "raw_candidate_recall_drop_at_most_2pp": deltas["raw_candidate_recall"]
        >= -0.02,
        "correction_recall_drop_at_most_2pp": deltas["correction_recall"] >= -0.02,
        "retract_recall_drop_at_most_2pp": deltas["retract_recall"] >= -0.02,
        "final_state_f1_drop_at_most_001": deltas["final_state_f1"] >= -0.01,
        "schema_completion_drop_at_most_05pp": deltas["schema_completion_rate"]
        >= -0.005,
        "hard_filter_completion_drop_at_most_1pp": None,
    }
    evaluable = [value for value in checks.values() if value is not None]
    return {
        "status": (
            "module_gate_passed_downstream_pending"
            if evaluable and all(evaluable)
            else "module_gate_failed"
        ),
        "c2u_fp_reduction_fraction": (
            round(c2u_reduction, 6) if c2u_reduction is not None else None
        ),
        "raw_candidate_fp_reduction_fraction": (
            round(candidate_fp_reduction, 6)
            if candidate_fp_reduction is not None
            else None
        ),
        "metric_deltas": deltas,
        "checks": checks,
        "limitations": [
            "This is a development fixture, not untouched confirmatory evidence.",
            "B0 has no typed confirm/refine event schema, so typed-event metrics are treatment-only.",
            "Hard-filter completion is not evaluable in the module-level 1-turn fixture.",
        ],
    }
