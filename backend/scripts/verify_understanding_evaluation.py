"""Understanding 평가 fixture, scorer, 정규식 베이스라인을 회귀 검증한다."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.baselines import regex_understand_utterance  # noqa: E402
from app.evaluation import (  # noqa: E402
    UnderstandingEvalCase,
    aggregate_understanding_scores,
    load_understanding_eval_dataset,
    score_understanding_prediction,
)
from app.models import (  # noqa: E402
    FacetCandidates,
    ItemActionCandidate,
    RejectionReason,
    StateUpdateCandidate,
    UnderstandingOutput,
)

DATASET_PATH = BACKEND_ROOT / "data" / "understanding_eval_v1.json"


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def prediction_from_gold(case: UnderstandingEvalCase) -> UnderstandingOutput:
    candidates: list[StateUpdateCandidate] = []
    facets: dict[str, StateUpdateCandidate | None] = {
        "subjective_property": None,
        "event": None,
        "activity": None,
        "goal_purpose": None,
        "goal_audience": None,
    }
    for gold in case.gold.candidates:
        if gold.target_kind == "category":
            target: dict[str, Any] = {"kind": "category"}
        elif gold.target_kind == "facet":
            target = {"kind": "facet", "facet": gold.facet}
        else:
            target = {
                "kind": "constraint",
                "scope": gold.scope,
                "key": gold.canonical_id,
            }
        candidate = StateUpdateCandidate.model_validate(
            {
                "target": target,
                "canonical_id": gold.canonical_id,
                "value_text": str(gold.canonical_id),
                "evidence_text": case.utterance,
                "origin": gold.origin,
                "confidence": 1.0,
            }
        )
        candidates.append(candidate)
        if gold.target_kind == "facet" and gold.facet:
            facets[gold.facet] = candidate

    action = None
    if case.gold.item_action:
        gold_action = case.gold.item_action
        reason = None
        if gold_action.rejection_reason_id:
            reason = RejectionReason(
                canonical_id=gold_action.rejection_reason_id,
                value_text=gold_action.rejection_reason_id,
                evidence_text=case.utterance,
                reason_type=gold_action.reason_type,
            )
        action = ItemActionCandidate(name=gold_action.name, rejection_reason=reason)

    return UnderstandingOutput(
        utterance=case.utterance,
        intents=case.gold.intents,
        facets=FacetCandidates.model_validate(facets),
        candidates=candidates,
        item_action=action,
        supersedes=case.gold.supersedes,
        residual_color_choice=case.gold.residual_color_choice,
    )


def main() -> None:
    dataset = load_understanding_eval_dataset(DATASET_PATH)
    check("20~30개 범위의 gold fixture", len(dataset.cases) == 28)
    check(
        "부록 C 시드가 앞의 6개 case",
        all("appendix-c" in case.tags for case in dataset.cases[:6]),
    )
    required_tags = {
        "budget",
        "note-taking",
        "gaming",
        "video",
        "storage",
        "compare",
        "inspect",
        "purchase",
        "vague",
        "contradiction",
    }
    observed_tags = {tag for case in dataset.cases for tag in case.tags}
    check("요구된 확장 발화 유형 포함", required_tags <= observed_tags)

    perfect_scores = []
    baseline_scores = []
    baseline_outputs = {}
    for case in dataset.cases:
        perfect = prediction_from_gold(case)
        perfect_scores.append(score_understanding_prediction(case, perfect))
        baseline = regex_understand_utterance(
            case.utterance, case.previous_state_summary
        )
        baseline_outputs[case.id] = baseline
        baseline_scores.append(score_understanding_prediction(case, baseline))

    perfect_metrics = aggregate_understanding_scores(
        perfect_scores, total_cases=len(dataset.cases)
    )
    check(
        "gold 자기 채점은 canonical ID exact 1.0",
        perfect_metrics["canonical_id_exact_accuracy"] == 1.0,
    )
    check(
        "gold 자기 채점은 모든 의미 필드 exact 1.0",
        all(
            perfect_metrics[key] == 1.0
            for key in (
                "intent_exact_accuracy",
                "item_action_exact_accuracy",
                "supersedes_exact_accuracy",
                "residual_color_choice_accuracy",
            )
        ),
    )
    one_failed = list(perfect_scores)
    first_case = dataset.cases[0]
    one_failed[0] = {
        "validation_success": False,
        "intent_fn": len(first_case.gold.intents),
        "candidate_fn": len(first_case.gold.candidates),
        "latency_ms": 1.0,
    }
    failed_metrics = aggregate_understanding_scores(
        one_failed, total_cases=len(dataset.cases)
    )
    check(
        "검증 실패 case는 recall 분모에서 빠지지 않음",
        failed_metrics["validation_success_rate"] < 1.0
        and failed_metrics["canonical_id_micro_recall"] < 1.0,
    )

    baseline_metrics = aggregate_understanding_scores(
        baseline_scores, total_cases=len(dataset.cases)
    )
    check("정규식 출력은 전 case 스키마 유효", baseline_metrics["validation_success_rate"] == 1.0)
    check(
        "정규식 베이스라인은 최소 비교 신호를 가짐",
        0.4 <= baseline_metrics["canonical_id_micro_f1"] <= 1.0,
        f"F1={baseline_metrics['canonical_id_micro_f1']:.3f}",
    )
    check(
        "리뷰 기반 거절은 review_signal을 켜지 않음",
        "review_signal"
        not in {candidate.canonical_id for candidate in baseline_outputs["u04"].candidates},
    )
    check(
        "상세 보기는 구매로 오인하지 않음",
        baseline_outputs["u05"].item_action is not None
        and baseline_outputs["u05"].item_action.name == "inspect_current",
    )
    check(
        "잔여 색상은 구매 시에만 표시",
        baseline_outputs["u06"].residual_color_choice
        and not baseline_outputs["u23"].residual_color_choice,
    )

    print(
        "\nUnderstanding evaluation 검증 통과 "
        f"(regex canonical F1={baseline_metrics['canonical_id_micro_f1']:.3f})"
    )


if __name__ == "__main__":
    main()
