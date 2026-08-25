"""Independent English holdout contracts and scorer for ActualUnderstandingOutput.

Natural-language wording is not graded verbatim. The scorer focuses on fields that
change deterministic state or retrieval, while also checking that normalized numeric
values remain usable and evidence text is grounded in the current utterance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.understanding import aggregate_understanding_scores
from app.models.actual_demo import (
    ACTUAL_FACET_IDS,
    ACTUAL_PREFERENCE_IDS,
    ActualCanonicalId,
    ActualPreferenceId,
    ActualRejectionReasonId,
    ActualUnderstandingOutput,
)
from app.models.understanding import EvidenceOrigin, IntentName, ItemActionName, SPNFacetName


class ActualEvalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActualGoldCandidate(ActualEvalContract):
    canonical_id: ActualCanonicalId
    target_kind: Literal["category", "facet", "constraint"]
    facet: SPNFacetName | None = None
    scope: Literal["hard", "soft"] | None = None
    origin: EvidenceOrigin
    # Every inner group is an OR-list; all groups must match value_text.
    # Example: [["64"], ["storage", "rom"]] means numeric 64 and a storage term.
    value_must_contain: list[list[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def target_matches_id(self) -> ActualGoldCandidate:
        if any(not group or any(not token.strip() for token in group) for group in self.value_must_contain):
            raise ValueError("value_must_contain groups must contain non-empty tokens")
        if self.target_kind == "category":
            if self.canonical_id != "category_tablet" or self.facet or self.scope:
                raise ValueError("category gold target does not match category_tablet")
        elif self.target_kind == "facet":
            if self.facet is None or self.scope is not None:
                raise ValueError("facet gold target requires only facet")
            if self.canonical_id not in ACTUAL_FACET_IDS[self.facet]:
                raise ValueError("facet gold target does not match canonical_id")
        else:
            if self.scope is None or self.facet is not None:
                raise ValueError("constraint gold target requires only scope")
            if self.canonical_id not in ACTUAL_PREFERENCE_IDS:
                raise ValueError("constraint gold target does not match canonical_id")
        return self


class ActualGoldItemAction(ActualEvalContract):
    name: ItemActionName
    target_rank: int | None = Field(default=None, ge=1, le=3)
    compare_rank: int | None = Field(default=None, ge=1, le=3)
    rejection_reason_id: ActualRejectionReasonId | None = None
    reason_type: Literal["situational_constraint", "product_attribute"] | None = None

    @model_validator(mode="after")
    def fields_match_action(self) -> ActualGoldItemAction:
        is_rejection = self.name == "reject_first"
        if is_rejection != (self.rejection_reason_id is not None):
            raise ValueError("reject_first alone requires rejection_reason_id")
        if is_rejection != (self.reason_type is not None):
            raise ValueError("reject_first alone requires reason_type")
        if self.name == "compare_first_second":
            if self.compare_rank is None:
                raise ValueError("comparison requires compare_rank")
            if (self.target_rank or 1) == self.compare_rank:
                raise ValueError("comparison ranks must differ")
        elif self.compare_rank is not None:
            raise ValueError("compare_rank is reserved for comparison")
        return self


class ActualGoldTradeoff(ActualEvalContract):
    prioritized_ids: list[ActualPreferenceId] = Field(min_length=1, max_length=4)
    compromised_ids: list[ActualPreferenceId] = Field(min_length=1, max_length=4)
    origin: EvidenceOrigin

    @model_validator(mode="after")
    def sides_are_sets(self) -> ActualGoldTradeoff:
        if len(self.prioritized_ids) != len(set(self.prioritized_ids)):
            raise ValueError("prioritized_ids must be unique")
        if len(self.compromised_ids) != len(set(self.compromised_ids)):
            raise ValueError("compromised_ids must be unique")
        if set(self.prioritized_ids) & set(self.compromised_ids):
            raise ValueError("trade-off sides must not overlap")
        return self


class ActualUnderstandingGold(ActualEvalContract):
    intents: list[IntentName] = Field(min_length=1)
    candidates: list[ActualGoldCandidate]
    item_action: ActualGoldItemAction | None = None
    tradeoff: ActualGoldTradeoff | None = None
    supersedes: list[ActualPreferenceId]
    residual_color_choice: Literal[False] = False
    forbidden_candidate_ids: list[ActualCanonicalId] = Field(default_factory=list)

    @model_validator(mode="after")
    def values_are_unique(self) -> ActualUnderstandingGold:
        ids = [candidate.canonical_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("gold candidate canonical IDs must be unique")
        if len(self.intents) != len(set(self.intents)):
            raise ValueError("gold intents must be unique")
        if len(self.supersedes) != len(set(self.supersedes)):
            raise ValueError("gold supersedes values must be unique")
        return self


class ActualUnderstandingEvalCase(ActualEvalContract):
    id: str = Field(pattern=r"^a\d{2}$")
    tags: list[str] = Field(min_length=1)
    utterance: str = Field(min_length=1)
    previous_state_summary: dict[str, Any] = Field(default_factory=dict)
    gold: ActualUnderstandingGold


class ActualUnderstandingEvalDataset(ActualEvalContract):
    dataset_version: str
    schema_version: Literal["understanding-v2-amazon-tablet-en"]
    prompt_version_frozen_before_first_run: str
    description: str
    cases: list[ActualUnderstandingEvalCase] = Field(min_length=20, max_length=40)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> ActualUnderstandingEvalDataset:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("actual holdout case IDs must be unique")
        return self


def load_actual_understanding_eval_dataset(
    path: Path | str,
) -> ActualUnderstandingEvalDataset:
    return ActualUnderstandingEvalDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _target_signature(candidate: Any) -> tuple[str, str | None, str | None]:
    return (
        candidate.target.kind,
        getattr(candidate.target, "facet", None),
        getattr(candidate.target, "scope", None),
    )


def _gold_target_signature(
    candidate: ActualGoldCandidate,
) -> tuple[str, str | None, str | None]:
    return candidate.target_kind, candidate.facet, candidate.scope


def _action_core(action: Any | None) -> tuple[str, str | None, str | None] | None:
    if action is None:
        return None
    reason = action.rejection_reason
    return (
        action.name,
        reason.canonical_id if reason else None,
        reason.reason_type if reason else None,
    )


def _gold_action_core(
    action: ActualGoldItemAction | None,
) -> tuple[str, str | None, str | None] | None:
    if action is None:
        return None
    return action.name, action.rejection_reason_id, action.reason_type


def _action_ranks(action: Any | None) -> tuple[int | None, int | None] | None:
    if action is None:
        return None
    return action.target_rank, action.compare_rank


def _gold_action_ranks(
    action: ActualGoldItemAction | None,
) -> tuple[int | None, int | None] | None:
    if action is None:
        return None
    return action.target_rank, action.compare_rank


def _tradeoff_signature(
    tradeoff: Any | None,
) -> tuple[frozenset[str], frozenset[str], str] | None:
    if tradeoff is None:
        return None
    return (
        frozenset(tradeoff.prioritized_ids),
        frozenset(tradeoff.compromised_ids),
        tradeoff.origin,
    )


def _gold_tradeoff_signature(
    tradeoff: ActualGoldTradeoff | None,
) -> tuple[frozenset[str], frozenset[str], str] | None:
    if tradeoff is None:
        return None
    return (
        frozenset(tradeoff.prioritized_ids),
        frozenset(tradeoff.compromised_ids),
        tradeoff.origin,
    )


def _value_semantics_match(gold: ActualGoldCandidate, value_text: str) -> bool:
    normalized = value_text.casefold()
    return all(
        any(token.casefold() in normalized for token in alternatives)
        for alternatives in gold.value_must_contain
    )


def _is_evidence_span(utterance: str, evidence_text: str) -> bool:
    return evidence_text.strip().casefold() in utterance.casefold()


def score_actual_understanding_prediction(
    case: ActualUnderstandingEvalCase,
    prediction: ActualUnderstandingOutput,
    *,
    latency_ms: float | None = None,
) -> dict[str, Any]:
    gold_ids = {candidate.canonical_id for candidate in case.gold.candidates}
    predicted_ids = {candidate.canonical_id for candidate in prediction.candidates}
    shared_ids = gold_ids & predicted_ids
    gold_by_id = {candidate.canonical_id: candidate for candidate in case.gold.candidates}
    predicted_by_id = {
        candidate.canonical_id: candidate for candidate in prediction.candidates
    }

    target_correct = sum(
        _target_signature(predicted_by_id[canonical_id])
        == _gold_target_signature(gold_by_id[canonical_id])
        for canonical_id in shared_ids
    )
    origin_correct = sum(
        predicted_by_id[canonical_id].origin == gold_by_id[canonical_id].origin
        for canonical_id in shared_ids
    )
    value_checked = [
        canonical_id
        for canonical_id in shared_ids
        if gold_by_id[canonical_id].value_must_contain
    ]
    value_correct = sum(
        _value_semantics_match(
            gold_by_id[canonical_id], predicted_by_id[canonical_id].value_text
        )
        for canonical_id in value_checked
    )
    evidence_correct = sum(
        _is_evidence_span(
            case.utterance, predicted_by_id[canonical_id].evidence_text
        )
        for canonical_id in shared_ids
    )

    gold_intents = set(case.gold.intents)
    predicted_intents = set(prediction.intents)
    action_core_exact = _action_core(prediction.item_action) == _gold_action_core(
        case.gold.item_action
    )
    action_rank_exact = _action_ranks(prediction.item_action) == _gold_action_ranks(
        case.gold.item_action
    )
    predicted_reason = (
        prediction.item_action.rejection_reason
        if prediction.item_action is not None
        else None
    )
    gold_requires_reason = (
        case.gold.item_action is not None
        and case.gold.item_action.rejection_reason_id is not None
    )
    action_evidence_correct = (
        predicted_reason is not None
        and _is_evidence_span(case.utterance, predicted_reason.evidence_text)
        if gold_requires_reason
        else predicted_reason is None
    )
    tradeoff_evidence_correct = (
        prediction.tradeoff is not None
        and _is_evidence_span(case.utterance, prediction.tradeoff.evidence_text)
        if case.gold.tradeoff is not None
        else prediction.tradeoff is None
    )

    all_known_ids = (
        {"category_tablet"}
        | set(ACTUAL_PREFERENCE_IDS)
        | set().union(*ACTUAL_FACET_IDS.values())
    )
    raw_prediction_ids = [str(candidate.canonical_id) for candidate in prediction.candidates]
    forbidden_hits = sorted(
        predicted_ids & set(case.gold.forbidden_candidate_ids)
    )
    return {
        "validation_success": True,
        "intent_exact": predicted_intents == gold_intents,
        "intent_tp": len(predicted_intents & gold_intents),
        "intent_fp": len(predicted_intents - gold_intents),
        "intent_fn": len(gold_intents - predicted_intents),
        "canonical_id_exact": predicted_ids == gold_ids,
        "candidate_tp": len(shared_ids),
        "candidate_fp": len(predicted_ids - gold_ids),
        "candidate_fn": len(gold_ids - predicted_ids),
        "target_correct": target_correct,
        "origin_correct": origin_correct,
        "shared_candidate_count": len(shared_ids),
        "value_checked_count": len(value_checked),
        "value_correct": value_correct,
        "evidence_checked_count": len(shared_ids),
        "evidence_correct": evidence_correct,
        "item_action_core_exact": action_core_exact,
        "item_action_rank_exact": action_rank_exact,
        "item_action_exact": action_core_exact and action_rank_exact,
        "action_evidence_span_correct": action_evidence_correct,
        "tradeoff_exact": _tradeoff_signature(prediction.tradeoff)
        == _gold_tradeoff_signature(case.gold.tradeoff),
        "tradeoff_evidence_span_correct": tradeoff_evidence_correct,
        "supersedes_exact": set(prediction.supersedes) == set(case.gold.supersedes),
        "residual_color_choice_exact": prediction.residual_color_choice is False,
        "forbidden_hits": forbidden_hits,
        "unmapped_ids": sorted(set(raw_prediction_ids) - all_known_ids),
        "duplicate_candidate_count": len(raw_prediction_ids)
        - len(set(raw_prediction_ids)),
        "predicted_candidate_count": len(raw_prediction_ids),
        "latency_ms": latency_ms,
    }


def aggregate_actual_understanding_scores(
    case_scores: list[dict[str, Any]],
    *,
    total_cases: int,
) -> dict[str, Any]:
    metrics = aggregate_understanding_scores(case_scores, total_cases=total_cases)
    successes = [score for score in case_scores if score.get("validation_success")]

    def total(key: str) -> int:
        return sum(int(score.get(key, 0)) for score in case_scores)

    def accuracy(key: str) -> float:
        return (
            sum(bool(score.get(key)) for score in successes) / total_cases
            if total_cases
            else 1.0
        )

    value_checked = total("value_checked_count")
    evidence_checked = total("evidence_checked_count")
    metrics.update(
        {
            "item_action_core_exact_accuracy": accuracy("item_action_core_exact"),
            "item_action_rank_exact_accuracy": accuracy("item_action_rank_exact"),
            "tradeoff_exact_accuracy": accuracy("tradeoff_exact"),
            "candidate_value_semantics_accuracy": (
                total("value_correct") / value_checked if value_checked else 1.0
            ),
            "candidate_evidence_span_accuracy": (
                total("evidence_correct") / evidence_checked
                if evidence_checked
                else 1.0
            ),
            "action_evidence_span_accuracy": accuracy(
                "action_evidence_span_correct"
            ),
            "tradeoff_evidence_span_accuracy": accuracy(
                "tradeoff_evidence_span_correct"
            ),
        }
    )
    return metrics
