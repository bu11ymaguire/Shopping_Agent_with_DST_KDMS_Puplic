"""SPN Understanding 예측을 gold 의미 라벨과 비교한다.

LLM이 만드는 ``value_text``나 ``evidence_text``의 문구는 여러 정답이 가능하므로
채점하지 않는다. 상태 병합과 정책에 실제로 영향을 주는 canonical ID, target,
provenance, 상품 행동, supersedes, 잔여 색상 수용 여부만 정량화한다.
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import (
    CanonicalId,
    EvidenceOrigin,
    IntentName,
    ItemActionName,
    PreferenceId,
    RejectionReasonId,
    SPNFacetName,
    UnderstandingOutput,
)
from app.models.understanding import CATEGORY_IDS, FACET_IDS, PREFERENCE_IDS


class EvalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoldCandidate(EvalContract):
    canonical_id: CanonicalId
    target_kind: Literal["category", "facet", "constraint"]
    facet: SPNFacetName | None = None
    scope: Literal["hard", "soft"] | None = None
    origin: EvidenceOrigin

    @model_validator(mode="after")
    def target_matches_id(self) -> GoldCandidate:
        if self.target_kind == "category":
            if self.canonical_id not in CATEGORY_IDS or self.facet or self.scope:
                raise ValueError("category gold target이 canonical_id와 맞지 않습니다.")
        elif self.target_kind == "facet":
            if self.facet is None or self.scope is not None:
                raise ValueError("facet gold target에는 facet만 필요합니다.")
            if self.canonical_id not in FACET_IDS[self.facet]:
                raise ValueError("facet gold target이 canonical_id와 맞지 않습니다.")
        else:
            if self.scope is None or self.facet is not None:
                raise ValueError("constraint gold target에는 scope가 필요합니다.")
            if self.canonical_id not in PREFERENCE_IDS:
                raise ValueError("constraint gold target이 canonical_id와 맞지 않습니다.")
        return self


class GoldItemAction(EvalContract):
    name: ItemActionName
    rejection_reason_id: RejectionReasonId | None = None
    reason_type: Literal["situational_constraint", "product_attribute"] | None = None

    @model_validator(mode="after")
    def reason_matches_action(self) -> GoldItemAction:
        is_rejection = self.name == "reject_first"
        if is_rejection != (self.rejection_reason_id is not None):
            raise ValueError("reject_first에만 rejection_reason_id가 필요합니다.")
        if is_rejection != (self.reason_type is not None):
            raise ValueError("reject_first에만 reason_type이 필요합니다.")
        return self


class UnderstandingGold(EvalContract):
    intents: list[IntentName] = Field(min_length=1)
    candidates: list[GoldCandidate]
    item_action: GoldItemAction | None = None
    supersedes: list[PreferenceId]
    residual_color_choice: bool
    forbidden_candidate_ids: list[CanonicalId] = Field(default_factory=list)

    @model_validator(mode="after")
    def values_are_unique(self) -> UnderstandingGold:
        ids = [candidate.canonical_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("gold candidates의 canonical_id는 중복될 수 없습니다.")
        if len(self.intents) != len(set(self.intents)):
            raise ValueError("gold intents는 중복될 수 없습니다.")
        return self


class UnderstandingEvalCase(EvalContract):
    id: str = Field(pattern=r"^u\d{2}$")
    tags: list[str] = Field(min_length=1)
    utterance: str = Field(min_length=1)
    previous_state_summary: dict[str, Any] = Field(default_factory=dict)
    gold: UnderstandingGold


class UnderstandingEvalDataset(EvalContract):
    dataset_version: str
    schema_version: str
    description: str
    cases: list[UnderstandingEvalCase] = Field(min_length=20, max_length=30)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> UnderstandingEvalDataset:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("평가 case id는 중복될 수 없습니다.")
        return self


def load_understanding_eval_dataset(path: Path | str) -> UnderstandingEvalDataset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return UnderstandingEvalDataset.model_validate(payload)


def _target_signature(candidate: Any) -> tuple[str, str | None, str | None]:
    target = candidate.target
    return (
        target.kind,
        getattr(target, "facet", None),
        getattr(target, "scope", None),
    )


def _gold_target_signature(candidate: GoldCandidate) -> tuple[str, str | None, str | None]:
    return candidate.target_kind, candidate.facet, candidate.scope


def _action_signature(action: Any | None) -> tuple[str, str | None, str | None] | None:
    if action is None:
        return None
    reason = action.rejection_reason
    return (
        action.name,
        reason.canonical_id if reason else None,
        reason.reason_type if reason else None,
    )


def _gold_action_signature(
    action: GoldItemAction | None,
) -> tuple[str, str | None, str | None] | None:
    if action is None:
        return None
    return action.name, action.rejection_reason_id, action.reason_type


def score_understanding_prediction(
    case: UnderstandingEvalCase,
    prediction: UnderstandingOutput,
    *,
    latency_ms: float | None = None,
) -> dict[str, Any]:
    """검증된 예측 하나를 의미 라벨 단위로 채점한다."""
    gold_ids = {candidate.canonical_id for candidate in case.gold.candidates}
    predicted_ids = {candidate.canonical_id for candidate in prediction.candidates}
    shared_ids = gold_ids & predicted_ids
    gold_by_id = {candidate.canonical_id: candidate for candidate in case.gold.candidates}
    predicted_by_id = {
        candidate.canonical_id: candidate for candidate in prediction.candidates
    }

    candidate_tp = len(shared_ids)
    candidate_fp = len(predicted_ids - gold_ids)
    candidate_fn = len(gold_ids - predicted_ids)
    target_correct = sum(
        _target_signature(predicted_by_id[canonical_id])
        == _gold_target_signature(gold_by_id[canonical_id])
        for canonical_id in shared_ids
    )
    origin_correct = sum(
        predicted_by_id[canonical_id].origin == gold_by_id[canonical_id].origin
        for canonical_id in shared_ids
    )

    gold_intents = set(case.gold.intents)
    predicted_intents = set(prediction.intents)
    intent_tp = len(gold_intents & predicted_intents)
    intent_fp = len(predicted_intents - gold_intents)
    intent_fn = len(gold_intents - predicted_intents)

    all_known_ids = CATEGORY_IDS | PREFERENCE_IDS | frozenset().union(*FACET_IDS.values())
    raw_prediction_ids = [str(candidate.canonical_id) for candidate in prediction.candidates]
    unmapped_ids = sorted(set(raw_prediction_ids) - all_known_ids)
    forbidden_hits = sorted(predicted_ids & set(case.gold.forbidden_candidate_ids))

    return {
        "validation_success": True,
        "intent_exact": predicted_intents == gold_intents,
        "intent_tp": intent_tp,
        "intent_fp": intent_fp,
        "intent_fn": intent_fn,
        "canonical_id_exact": predicted_ids == gold_ids,
        "candidate_tp": candidate_tp,
        "candidate_fp": candidate_fp,
        "candidate_fn": candidate_fn,
        "target_correct": target_correct,
        "origin_correct": origin_correct,
        "shared_candidate_count": len(shared_ids),
        "item_action_exact": _action_signature(prediction.item_action)
        == _gold_action_signature(case.gold.item_action),
        "supersedes_exact": set(prediction.supersedes) == set(case.gold.supersedes),
        "residual_color_choice_exact": prediction.residual_color_choice
        == case.gold.residual_color_choice,
        "forbidden_hits": forbidden_hits,
        "unmapped_ids": unmapped_ids,
        "duplicate_candidate_count": len(raw_prediction_ids) - len(set(raw_prediction_ids)),
        "predicted_candidate_count": len(raw_prediction_ids),
        "latency_ms": latency_ms,
    }


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 1.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * percentile))
    return ordered[index]


def aggregate_understanding_scores(
    case_scores: list[dict[str, Any]],
    *,
    total_cases: int,
) -> dict[str, Any]:
    """실패 case도 분모에 포함해 공통 집계값을 만든다."""
    successes = [score for score in case_scores if score.get("validation_success")]

    def total(key: str) -> int:
        return sum(int(score.get(key, 0)) for score in case_scores)

    candidate_tp = total("candidate_tp")
    candidate_fp = total("candidate_fp")
    candidate_fn = total("candidate_fn")
    candidate_precision = _ratio(candidate_tp, candidate_tp + candidate_fp)
    candidate_recall = _ratio(candidate_tp, candidate_tp + candidate_fn)

    intent_tp = total("intent_tp")
    intent_fp = total("intent_fp")
    intent_fn = total("intent_fn")
    intent_precision = _ratio(intent_tp, intent_tp + intent_fp)
    intent_recall = _ratio(intent_tp, intent_tp + intent_fn)

    shared_count = total("shared_candidate_count")
    predicted_count = total("predicted_candidate_count")
    latencies = [
        float(score["latency_ms"])
        for score in case_scores
        if score.get("latency_ms") is not None
    ]

    return {
        "total_cases": total_cases,
        "validated_cases": len(successes),
        "validation_success_rate": _ratio(len(successes), total_cases),
        "intent_exact_accuracy": _ratio(
            sum(bool(score["intent_exact"]) for score in successes), total_cases
        ),
        "intent_micro_precision": intent_precision,
        "intent_micro_recall": intent_recall,
        "intent_micro_f1": _f1(intent_precision, intent_recall),
        "canonical_id_exact_accuracy": _ratio(
            sum(bool(score["canonical_id_exact"]) for score in successes), total_cases
        ),
        "canonical_id_micro_precision": candidate_precision,
        "canonical_id_micro_recall": candidate_recall,
        "canonical_id_micro_f1": _f1(candidate_precision, candidate_recall),
        "target_accuracy_on_matched_ids": _ratio(total("target_correct"), shared_count),
        "origin_accuracy_on_matched_ids": _ratio(total("origin_correct"), shared_count),
        "item_action_exact_accuracy": _ratio(
            sum(bool(score["item_action_exact"]) for score in successes), total_cases
        ),
        "supersedes_exact_accuracy": _ratio(
            sum(bool(score["supersedes_exact"]) for score in successes), total_cases
        ),
        "residual_color_choice_accuracy": _ratio(
            sum(bool(score["residual_color_choice_exact"]) for score in successes),
            total_cases,
        ),
        "forbidden_candidate_violation_rate": _ratio(
            sum(bool(score["forbidden_hits"]) for score in successes), total_cases
        ),
        "unmapped_candidate_rate": _ratio(
            sum(len(score["unmapped_ids"]) for score in successes), predicted_count
        ),
        "duplicate_candidate_rate": _ratio(
            total("duplicate_candidate_count"), predicted_count
        ),
        "latency_ms": {
            "mean": mean(latencies) if latencies else None,
            "median": median(latencies) if latencies else None,
            "p95": _percentile(latencies, 0.95),
        },
    }
