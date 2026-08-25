"""Verify the frozen actual Understanding holdout and its semantic scorer offline."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.actual_understanding import (  # noqa: E402
    ActualUnderstandingEvalCase,
    aggregate_actual_understanding_scores,
    load_actual_understanding_eval_dataset,
    score_actual_understanding_prediction,
)
from app.models.actual_demo import (  # noqa: E402
    ActualFacetCandidates,
    ActualItemActionCandidate,
    ActualRejectionReason,
    ActualStateUpdateCandidate,
    ActualTradeoffCandidate,
    ActualUnderstandingOutput,
)
from app.nodes.actual_understanding import (  # noqa: E402
    ACTUAL_UNDERSTANDING_PROMPT_VERSION,
)

DATASET_PATH = BACKEND_ROOT / "data" / "actual_understanding_holdout_v1.json"


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def prediction_from_gold(
    case: ActualUnderstandingEvalCase,
) -> ActualUnderstandingOutput:
    candidates = []
    for gold in case.gold.candidates:
        if gold.target_kind == "category":
            target = {"kind": "category"}
        elif gold.target_kind == "facet":
            target = {"kind": "facet", "facet": gold.facet}
        else:
            target = {
                "kind": "constraint",
                "scope": gold.scope,
                "key": gold.canonical_id,
            }
        value_text = " ".join(group[0] for group in gold.value_must_contain)
        candidates.append(
            ActualStateUpdateCandidate.model_validate(
                {
                    "target": target,
                    "canonical_id": gold.canonical_id,
                    "value_text": value_text or str(gold.canonical_id),
                    "evidence_text": case.utterance,
                    "origin": gold.origin,
                    "confidence": 1,
                }
            )
        )

    action = None
    if gold_action := case.gold.item_action:
        rejection = None
        if gold_action.rejection_reason_id:
            rejection = ActualRejectionReason(
                canonical_id=gold_action.rejection_reason_id,
                value_text="gold rejection reason",
                evidence_text=case.utterance,
                reason_type=gold_action.reason_type,
            )
        action = ActualItemActionCandidate(
            name=gold_action.name,
            target_rank=gold_action.target_rank,
            compare_rank=gold_action.compare_rank,
            rejection_reason=rejection,
        )

    tradeoff = None
    if gold_tradeoff := case.gold.tradeoff:
        tradeoff = ActualTradeoffCandidate(
            prioritized_ids=gold_tradeoff.prioritized_ids,
            compromised_ids=gold_tradeoff.compromised_ids,
            value_text="gold trade-off",
            evidence_text=case.utterance,
            origin=gold_tradeoff.origin,
            confidence=1,
        )

    return ActualUnderstandingOutput(
        utterance=case.utterance,
        intents=case.gold.intents,
        facets=ActualFacetCandidates(),
        candidates=candidates,
        item_action=action,
        tradeoff=tradeoff,
        supersedes=case.gold.supersedes,
        residual_color_choice=False,
    )


def main() -> None:
    dataset = load_actual_understanding_eval_dataset(DATASET_PATH)
    check("독립 holdout 30개", len(dataset.cases) == 30)
    check(
        "첫 실행 전에 동결한 prompt version",
        dataset.prompt_version_frozen_before_first_run
        == ACTUAL_UNDERSTANDING_PROMPT_VERSION,
    )
    required_tags = {
        "hard-constraints",
        "tradeoff",
        "supersedes",
        "rank-reference",
        "named-reference",
        "reject",
        "purchase",
        "unsupported-category",
        "unsupported-fields",
        "unsupported-currency",
    }
    tags = {tag for case in dataset.cases for tag in case.tags}
    check("요구된 일반화 유형 포함", required_tags <= tags, sorted(required_tags - tags))
    check(
        "gold와 forbidden candidate가 겹치지 않음",
        all(
            not (
                {candidate.canonical_id for candidate in case.gold.candidates}
                & set(case.gold.forbidden_candidate_ids)
            )
            for case in dataset.cases
        ),
    )

    scores = [
        score_actual_understanding_prediction(case, prediction_from_gold(case))
        for case in dataset.cases
    ]
    metrics = aggregate_actual_understanding_scores(
        scores,
        total_cases=len(dataset.cases),
    )
    check("gold 자기 채점 canonical exact", metrics["canonical_id_exact_accuracy"] == 1)
    check("gold 자기 채점 action rank exact", metrics["item_action_rank_exact_accuracy"] == 1)
    check("gold 자기 채점 trade-off exact", metrics["tradeoff_exact_accuracy"] == 1)
    check("gold numeric normalization 자기 채점", metrics["candidate_value_semantics_accuracy"] == 1)
    check("gold evidence span 자기 채점", metrics["candidate_evidence_span_accuracy"] == 1)

    action_case = next(case for case in dataset.cases if case.id == "a26")
    wrong_rank = prediction_from_gold(action_case)
    wrong_rank.item_action = wrong_rank.item_action.model_copy(update={"target_rank": 1})
    rank_score = score_actual_understanding_prediction(action_case, wrong_rank)
    check(
        "action core와 rank 오류를 분리",
        rank_score["item_action_core_exact"]
        and not rank_score["item_action_rank_exact"]
        and not rank_score["item_action_exact"],
    )

    value_case = next(case for case in dataset.cases if case.id == "a03")
    wrong_value = prediction_from_gold(value_case)
    budget = next(
        candidate
        for candidate in wrong_value.candidates
        if candidate.canonical_id == "budget"
    )
    budget.value_text = "$999 maximum"
    value_score = score_actual_understanding_prediction(value_case, wrong_value)
    check(
        "canonical ID가 맞아도 숫자 정규화 오류 감지",
        value_score["canonical_id_exact"]
        and value_score["value_correct"] < value_score["value_checked_count"],
    )

    evidence_case = next(case for case in dataset.cases if case.id == "a02")
    wrong_evidence = prediction_from_gold(evidence_case)
    wrong_evidence.candidates[0].evidence_text = "not in the utterance"
    evidence_score = score_actual_understanding_prediction(
        evidence_case, wrong_evidence
    )
    check(
        "현재 발화 밖 evidence span 감지",
        evidence_score["evidence_correct"]
        < evidence_score["evidence_checked_count"],
    )

    rejection_case = next(
        case
        for case in dataset.cases
        if case.gold.item_action is not None
        and case.gold.item_action.rejection_reason_id is not None
    )
    missing_rejection = prediction_from_gold(rejection_case)
    missing_rejection.item_action = None
    rejection_score = score_actual_understanding_prediction(
        rejection_case, missing_rejection
    )
    check(
        "필수 거절 근거 누락을 evidence 성공으로 세지 않음",
        not rejection_score["action_evidence_span_correct"],
    )

    tradeoff_case = next(
        case for case in dataset.cases if case.gold.tradeoff is not None
    )
    missing_tradeoff = prediction_from_gold(tradeoff_case)
    missing_tradeoff.tradeoff = None
    tradeoff_score = score_actual_understanding_prediction(
        tradeoff_case, missing_tradeoff
    )
    check(
        "필수 trade-off 근거 누락을 evidence 성공으로 세지 않음",
        not tradeoff_score["tradeoff_evidence_span_correct"],
    )

    print("\nActual Understanding holdout/scorer 검증 통과")


if __name__ == "__main__":
    main()
