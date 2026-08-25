"""SPN Policy (DECIDE): 상태 하나만 읽는 결정론적 lane 선택."""

from __future__ import annotations

from app.models import (
    DialogueState,
    PolicyDecision,
    PolicySnapshot,
    UnresolvedPreference,
    VaguenessBreakdown,
)

ASK_USER_THRESHOLD = 45


def _unresolved_rejection_count(state: DialogueState) -> int:
    constraint_turn_ids = {
        turn_id
        for value in [
            *state.hard_constraints.values(),
            *state.soft_constraints.values(),
        ]
        for turn_id in value.evidence_turn_ids
    }
    return sum(
        not any(
            turn_id in constraint_turn_ids
            for turn_id in item.reason.evidence_turn_ids
        )
        for item in state.rejected_items
    )


def compute_vagueness(state: DialogueState) -> VaguenessBreakdown:
    reasons: list[str] = []
    category_breadth = 0
    missing_required_info = 0
    unresolved_spn = 0
    contradiction_penalty = 0

    if state.category is None:
        category_breadth = 18
        reasons.append("상품 범위가 확인되지 않음: +18")
    if "budget" not in state.hard_constraints:
        missing_required_info += 25
        reasons.append("가격 기준이 확인되지 않음: +25")
    if state.subjective_needs.subjective_property is None:
        missing_required_info += 20
        reasons.append("사용 기간 기준이 확인되지 않음: +20")
    if "delivery_deadline" not in state.hard_constraints:
        unresolved_spn += 12
        reasons.append("수령 시점이 확인되지 않음: +12")
    review_signal = state.soft_constraints.get("review_signal")
    if review_signal is not None and review_signal.status != "superseded":
        unresolved_spn -= 8
        reasons.append("리뷰 선호가 확인됨: -8")

    unresolved_rejections = _unresolved_rejection_count(state)
    if unresolved_rejections:
        contradiction_penalty = unresolved_rejections * 12
        reasons.append(
            f"거절 이유가 제약으로 정리되지 않음: +{contradiction_penalty}"
        )

    total = max(
        0,
        min(
            100,
            category_breadth
            + missing_required_info
            + unresolved_spn
            + contradiction_penalty,
        ),
    )
    return VaguenessBreakdown(
        category_breadth=category_breadth,
        missing_required_info=missing_required_info,
        unresolved_spn=unresolved_spn,
        contradiction_penalty=contradiction_penalty,
        total=total,
        threshold=ASK_USER_THRESHOLD,
        reasons=reasons,
    )


def unresolved_preferences(state: DialogueState) -> list[UnresolvedPreference]:
    missing: list[UnresolvedPreference] = []
    if "budget" not in state.hard_constraints:
        missing.append(
            UnresolvedPreference(
                field="가격 기준",
                reason="가격 범위가 후보군을 가장 크게 바꿉니다.",
                priority=25,
            )
        )
    if state.subjective_needs.subjective_property is None:
        missing.append(
            UnresolvedPreference(
                field="사용 기간 기준",
                reason="사용 기간에 따라 성능과 지원 기간의 비중이 달라집니다.",
                priority=20,
            )
        )
    if "delivery_deadline" not in state.hard_constraints:
        missing.append(
            UnresolvedPreference(
                field="수령 시점",
                reason="모델별 재고 상황에 따라 수령까지 걸리는 시간이 다릅니다.",
                priority=12,
            )
        )
    return sorted(missing, key=lambda item: item.priority, reverse=True)


def select_policy(state: DialogueState) -> PolicyDecision:
    """상태 외의 입력 없이 명시적인 clarify/recommend lane을 고른다."""
    vagueness = compute_vagueness(state)
    missing = unresolved_preferences(state)
    snapshot = PolicySnapshot(
        facets=state.subjective_needs.model_copy(deep=True),
        vagueness=vagueness,
    )
    if vagueness.total > ASK_USER_THRESHOLD and missing:
        return PolicyDecision(
            action="ask_user",
            lane="clarify-lane",
            question_target=missing[0],
            reasons=vagueness.reasons,
            snapshot=snapshot,
        )
    return PolicyDecision(
        action="recommend",
        lane="recommend-lane",
        reasons=["현재 상태가 모호성 임계값 이하이므로 검색과 추천을 진행합니다."],
        snapshot=snapshot,
    )
