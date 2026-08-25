"""RA-Rec State Manager (MEMORY): 후보 병합과 provenance만 담당한다."""

from __future__ import annotations

from collections.abc import Iterable

from app.models import (
    DialogueState,
    PreferenceHistoryEntry,
    PreferenceValue,
    Product,
    RankedProduct,
    RejectedItem,
    StateDiff,
    UnderstandingOutput,
)
from app.models.understanding import StateUpdateCandidate


def create_initial_dialogue_state() -> DialogueState:
    return DialogueState()


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _to_preference(
    *,
    canonical_id: str,
    value_text: str,
    origin: str,
    confidence: float,
    turn_id: str,
) -> PreferenceValue:
    return PreferenceValue(
        canonical_id=canonical_id,
        value_text=value_text,
        origin=origin,
        confidence=confidence,
        status="unconfirmed" if origin == "inferred" else "confirmed",
        evidence_turn_ids=[turn_id],
        updated_at_turn_id=turn_id,
    )


def _candidate_preference(
    candidate: StateUpdateCandidate, turn_id: str
) -> PreferenceValue:
    return _to_preference(
        canonical_id=candidate.canonical_id,
        value_text=candidate.value_text,
        origin=candidate.origin,
        confidence=candidate.confidence,
        turn_id=turn_id,
    )


def _merge_preference(
    previous: PreferenceValue | None,
    candidate: PreferenceValue,
) -> PreferenceValue:
    if previous is None:
        return candidate

    evidence = _unique([*previous.evidence_turn_ids, *candidate.evidence_turn_ids])
    # 확인된 값을 뒤이은 추론 하나로 강등하지 않는다.
    if previous.status == "confirmed" and candidate.status == "unconfirmed":
        return previous.model_copy(
            update={
                "evidence_turn_ids": evidence,
                "updated_at_turn_id": candidate.updated_at_turn_id,
            }
        )
    return candidate.model_copy(update={"evidence_turn_ids": evidence})


def _ranked_product_id(rankings: list[RankedProduct], rank: int) -> str | None:
    return next(
        (item.product_id for item in rankings if item.rank == rank),
        None,
    )


def _topic_particle(word: str) -> str:
    last = word.strip()[-1:] or ""
    if not last:
        return "은(는)"
    code = ord(last)
    if 0xAC00 <= code <= 0xD7A3:
        return "는" if (code - 0xAC00) % 28 == 0 else "은"
    return "은(는)"


def update_dialogue_state(
    previous: DialogueState,
    understanding: UnderstandingOutput,
    previous_rankings: list[RankedProduct],
    *,
    turn_id: str,
    products: list[Product],
) -> tuple[DialogueState, StateDiff]:
    """발화를 다시 파싱하지 않고 검증된 후보와 행동만 상태에 병합한다."""
    state = previous.model_copy(deep=True)
    changed: list[str] = []

    previous_recommended = [item.product_id for item in previous_rankings]
    if previous_recommended and previous_recommended != state.recommended_items:
        state.recommended_items = previous_recommended
        changed.append("recommended_items")

    for candidate in understanding.candidates:
        next_value = _candidate_preference(candidate, turn_id)
        target = candidate.target
        if target.kind == "category":
            state.category = _merge_preference(state.category, next_value)
            changed.append("category")
        elif target.kind == "facet":
            current = state.subjective_needs.get_facet(target.facet)
            state.subjective_needs.set_facet(
                target.facet, _merge_preference(current, next_value)
            )
            changed.append(f"subjective_needs.{target.facet}")
        else:
            record = (
                state.hard_constraints
                if target.scope == "hard"
                else state.soft_constraints
            )
            record[target.key] = _merge_preference(record.get(target.key), next_value)
            prefix = "hard_constraints" if target.scope == "hard" else "soft_constraints"
            changed.append(f"{prefix}.{target.key}")

    for key in understanding.supersedes:
        existing = state.soft_constraints.get(key)
        if (
            existing is None
            or existing.origin != "inferred"
            or existing.status != "unconfirmed"
        ):
            continue
        state.soft_constraints[key] = existing.model_copy(
            update={
                "status": "superseded",
                "updated_at_turn_id": turn_id,
                "evidence_turn_ids": _unique(
                    [*existing.evidence_turn_ids, turn_id]
                ),
            }
        )
        changed.append(f"soft_constraints.{key}")

    action = understanding.item_action
    primary_rank = getattr(action, "target_rank", None) or 1
    comparison_rank = getattr(action, "compare_rank", None) or 2
    first = _ranked_product_id(previous_rankings, primary_rank)
    second = _ranked_product_id(previous_rankings, comparison_rank)

    if (
        action is not None
        and action.name == "reject_first"
        and action.rejection_reason is not None
        and first
    ):
        reason = _to_preference(
            canonical_id=action.rejection_reason.canonical_id,
            value_text=action.rejection_reason.value_text,
            origin="explicit",
            confidence=1.0,
            turn_id=turn_id,
        )
        state.rejected_items = [
            item for item in state.rejected_items if item.product_id != first
        ]
        state.rejected_items.append(
            RejectedItem(
                product_id=first,
                reason=reason,
                reason_type=action.rejection_reason.reason_type,
            )
        )
        changed.append("rejected_items")

    if action is not None and action.name == "compare_first_second" and first and second:
        state.shortlisted_items = _unique(
            [*state.shortlisted_items, first, second]
        )
        changed.append("shortlisted_items")

    if action is not None and action.name == "inspect_current" and first:
        state.inspected_items = _unique([*state.inspected_items, first])
        state.shortlisted_items = _unique([*state.shortlisted_items, first])
        state.current_item = first
        changed.extend(["inspected_items", "shortlisted_items", "current_item"])

    explicit_purchase_rank = getattr(action, "target_rank", None)
    purchase_target = (
        first if explicit_purchase_rank is not None else state.current_item or first
    )
    if understanding.residual_color_choice:
        product = next((item for item in products if item.id == purchase_target), None)
        color = product.metadata.available_colors[0] if product and product.metadata.available_colors else None
        value_text = (
            f"{color}{_topic_particle(color)} 선호가 아니라 즉시 수령 가능한 잔여 색상"
            if color
            else "즉시 수령 가능한 잔여 색상 수용"
        )
        color_value = _to_preference(
            canonical_id="color_residual",
            value_text=value_text,
            origin="implicit",
            confidence=0.95,
            turn_id=turn_id,
        )
        state.soft_constraints["color_residual"] = _merge_preference(
            state.soft_constraints.get("color_residual"), color_value
        )
        changed.append("soft_constraints.color_residual")

    if action is not None and action.name == "purchase_current" and purchase_target:
        state.current_item = purchase_target
        state.purchased_items = _unique([*state.purchased_items, purchase_target])
        changed.extend(["current_item", "purchased_items"])
        if understanding.residual_color_choice:
            tradeoff = _to_preference(
                canonical_id="tradeoff_delivery_over_price_color",
                value_text="빠른 수령과 장기 사용 가치를 우선하고 가격·색상 선택을 양보",
                origin="explicit",
                confidence=1.0,
                turn_id=turn_id,
            )
            existing_index = next(
                (
                    index
                    for index, item in enumerate(state.tradeoffs)
                    if item.canonical_id == tradeoff.canonical_id
                ),
                None,
            )
            if existing_index is None:
                state.tradeoffs.append(tradeoff)
            else:
                state.tradeoffs[existing_index] = _merge_preference(
                    state.tradeoffs[existing_index], tradeoff
                )
            changed.append("tradeoffs")

    changed_paths = _unique(changed)
    state.preference_history.append(
        PreferenceHistoryEntry(turn_id=turn_id, changed_paths=changed_paths)
    )
    summary = (
        [f"{path} 갱신" for path in changed_paths]
        if changed_paths
        else ["인식 가능한 상태 변경 없음"]
    )
    return state, StateDiff(changed_paths=changed_paths, summary=summary)
