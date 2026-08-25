"""Real-demo MEMORY wrapper: generic merge plus explicit trade-off storage."""

from __future__ import annotations

from app.models import DialogueState, PreferenceValue, RankedProduct, StateDiff
from app.models.actual_demo import (
    ActualUnderstandingOutput,
    TabletDomainUnderstandingOutput,
)
from app.nodes.state_manager import update_dialogue_state


def create_tablet_environment_state() -> DialogueState:
    """Create v2 state for a user already inside the bounded tablet store."""
    return DialogueState(
        domain_route="in_domain",
        category=PreferenceValue(
            canonical_id="category_tablet",
            value_text="tablet shopping environment",
            origin="environment",
            confidence=1.0,
            status="confirmed",
            evidence_turn_ids=[],
            updated_at_turn_id="environment-init",
        ),
    )


def update_actual_dialogue_state(
    previous: DialogueState,
    understanding: ActualUnderstandingOutput | TabletDomainUnderstandingOutput,
    previous_rankings: list[RankedProduct],
    *,
    turn_id: str,
) -> tuple[DialogueState, StateDiff]:
    # ActualUnderstandingOutput intentionally follows the same candidate/action shape.
    # The generic manager never parses the utterance and does not use products unless
    # residual_color_choice is true, which the actual schema forbids.
    state, diff = update_dialogue_state(  # type: ignore[arg-type]
        previous,
        understanding,
        previous_rankings,
        turn_id=turn_id,
        products=[],
    )
    if isinstance(understanding, TabletDomainUnderstandingOutput):
        domain_changed = (
            state.domain_route != understanding.domain_route
            or state.unsupported_category_text
            != understanding.unsupported_category_text
        )
        state.domain_route = understanding.domain_route
        state.unsupported_category_text = understanding.unsupported_category_text
        if domain_changed:
            domain_paths = ["domain_route", "unsupported_category_text"]
            changed_paths = list(dict.fromkeys([*diff.changed_paths, *domain_paths]))
            if state.preference_history:
                state.preference_history[-1] = state.preference_history[-1].model_copy(
                    update={"changed_paths": changed_paths}
                )
            diff = StateDiff(
                changed_paths=changed_paths,
                summary=[f"{path} updated" for path in changed_paths],
            )

    tradeoff = understanding.tradeoff
    if tradeoff is None:
        return state, diff

    canonical_id = "tradeoff_" + "_".join(tradeoff.prioritized_ids)
    canonical_id += "_over_" + "_".join(tradeoff.compromised_ids)
    status = "unconfirmed" if tradeoff.origin == "inferred" else "confirmed"
    value = PreferenceValue(
        canonical_id=canonical_id,
        value_text=tradeoff.value_text,
        origin=tradeoff.origin,
        confidence=tradeoff.confidence,
        status=status,
        evidence_turn_ids=[turn_id],
        updated_at_turn_id=turn_id,
    )
    previous_index = next(
        (
            index
            for index, item in enumerate(state.tradeoffs)
            if item.canonical_id == canonical_id
        ),
        None,
    )
    if previous_index is None:
        state.tradeoffs.append(value)
    else:
        previous_value = state.tradeoffs[previous_index]
        evidence = list(
            dict.fromkeys([*previous_value.evidence_turn_ids, *value.evidence_turn_ids])
        )
        state.tradeoffs[previous_index] = value.model_copy(
            update={"evidence_turn_ids": evidence}
        )

    changed_paths = list(dict.fromkeys([*diff.changed_paths, "tradeoffs"]))
    if state.preference_history:
        state.preference_history[-1] = state.preference_history[-1].model_copy(
            update={"changed_paths": changed_paths}
        )
    return state, StateDiff(
        changed_paths=changed_paths,
        summary=[f"{path} updated" for path in changed_paths],
    )
