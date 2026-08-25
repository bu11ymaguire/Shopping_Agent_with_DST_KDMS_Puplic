"""Deterministic DECIDE policy for the bounded real-tablet catalog."""

from __future__ import annotations

from app.models import (
    DialogueState,
    PolicyDecision,
    PolicySnapshot,
    UnresolvedPreference,
    VaguenessBreakdown,
)

ACTUAL_ASK_USER_THRESHOLD = 45

_DECISION_CRITERIA = {
    "storage_capacity",
    "memory_capacity",
    "max_weight",
    "min_rating",
    "operating_system",
    "price_value",
    "portability",
    "note_taking",
    "display",
    "performance",
    "battery",
    "audio",
    "durability",
    "review_signal",
    "child_friendly",
}


def _is_confirmed(value: object | None) -> bool:
    return value is not None and getattr(value, "status", None) == "confirmed"


def _has_decision_criterion(state: DialogueState) -> bool:
    if any(
        value.status == "confirmed" and key in _DECISION_CRITERIA
        for key, value in [
            *state.hard_constraints.items(),
            *state.soft_constraints.items(),
        ]
    ):
        return True
    return any(
        value is not None and value.status == "confirmed"
        for value in (
            state.subjective_needs.subjective_property,
            state.subjective_needs.activity,
            state.subjective_needs.goal_purpose,
            state.subjective_needs.goal_audience,
        )
    )


def actual_unresolved_preferences(state: DialogueState) -> list[UnresolvedPreference]:
    missing: list[UnresolvedPreference] = []
    if state.domain_route == "unsupported_category":
        requested = state.unsupported_category_text or "another product category"
        missing.append(
            UnresolvedPreference(
                field="supported category",
                reason=(
                    f"{requested} is outside this tablet-shopping environment. "
                    "Only the installed tablet catalog can be recommended."
                ),
                priority=100,
            )
        )
    if not _is_confirmed(state.category):
        missing.append(
            UnresolvedPreference(
                field="supported category",
                reason="This local demo can recommend tablets from the installed dataset.",
                priority=30,
            )
        )
    if not _is_confirmed(state.hard_constraints.get("budget")):
        missing.append(
            UnresolvedPreference(
                field="budget",
                reason="A USD ceiling removes products that cannot be compared reliably.",
                priority=25,
            )
        )
    if not _has_decision_criterion(state):
        missing.append(
            UnresolvedPreference(
                field="main use or priority",
                reason="A use case or product priority is needed to rank review evidence.",
                priority=25,
            )
        )
    return sorted(missing, key=lambda item: item.priority, reverse=True)


def compute_actual_vagueness(state: DialogueState) -> VaguenessBreakdown:
    reasons: list[str] = []
    category_breadth = 0
    missing_required_info = 0
    unresolved_spn = 0
    contradiction_penalty = 0
    if state.domain_route == "unsupported_category":
        category_breadth = 100
        reasons.append("explicit request is outside the tablet domain: +100")
    elif not _is_confirmed(state.category):
        category_breadth = 30
        reasons.append("supported tablet category is not established: +30")
    if not _is_confirmed(state.hard_constraints.get("budget")):
        missing_required_info += 25
        reasons.append("USD budget is not established: +25")
    if not _has_decision_criterion(state):
        unresolved_spn += 25
        reasons.append("main use or ranking priority is not established: +25")
    review_signal = state.soft_constraints.get("review_signal")
    if review_signal is not None and review_signal.status == "confirmed":
        unresolved_spn -= 8
        reasons.append("review evidence preference is established: -8")
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
        threshold=ACTUAL_ASK_USER_THRESHOLD,
        reasons=reasons,
    )


def select_actual_policy(state: DialogueState) -> PolicyDecision:
    vagueness = compute_actual_vagueness(state)
    missing = actual_unresolved_preferences(state)
    snapshot = PolicySnapshot(
        facets=state.subjective_needs.model_copy(deep=True),
        vagueness=vagueness,
    )
    if (
        state.domain_route == "unsupported_category"
        or not _is_confirmed(state.category)
        or vagueness.total > ACTUAL_ASK_USER_THRESHOLD
    ) and missing:
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
        reasons=["The confirmed state is specific enough for bounded catalog search."],
        snapshot=snapshot,
    )
