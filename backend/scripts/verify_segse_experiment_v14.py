"""Deterministic unit checks for the SEGSE v1.4 correction-recovery contract.

Every check runs offline.  No LLM call, no catalog, no fixture gold is read, so
these assertions describe the contract itself rather than one exposed dataset.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.actual_demo import TabletDomainFacetId  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.segse_experiment import (  # noqa: E402
    SEGSEStateEvent,
    authorize_segse_events,
    update_segse_dialogue_state,
)
from app.segse_experiment_v12 import SEGSEV12Event, SEGSEV12ProposalOutput  # noqa: E402
from app.segse_experiment_v13 import SEGSE_V13_SYSTEM_PROMPT  # noqa: E402
from app.segse_experiment_v14 import (  # noqa: E402
    _FACET_DIMENSION_TERMS,
    SEGSE_V14_SYSTEM_PROMPT,
    SEGSEV14UnderstandingProvider,
    dimension_evidence_scope,
    filter_hard_value_contracts,
    hard_value_decision,
    repair_facet_events,
    repair_scope_relaxations,
)


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def event(
    canonical_id: str,
    act: str,
    evidence: str,
    *,
    scope: str | None = None,
    value: str | None = None,
    relation: str | None = None,
    source: str = "current_utterance",
    source_ref: str | None = None,
) -> SEGSEStateEvent:
    return SEGSEStateEvent.model_validate(
        {
            "canonical_id": canonical_id,
            "act": act,
            "scope_after": scope,
            "value_after": value,
            "relation": relation,
            "trigger_evidence_text": evidence,
            "value_source": source,
            "source_ref": source_ref,
            "origin": "explicit",
            "confidence": 1.0,
        }
    )


def summary_from_state(state: Any) -> dict[str, Any]:
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


class FakeClient:
    def __init__(self, output: SEGSEV12ProposalOutput) -> None:
        self.output = output

    async def generate_structured(self, **kwargs: Any) -> SEGSEV12ProposalOutput:
        return self.output


def proposal(utterance: str, *events: dict[str, Any]) -> SEGSEV12ProposalOutput:
    return SEGSEV12ProposalOutput.model_validate(
        {
            "utterance": utterance,
            "domain_route": "in_domain",
            "unsupported_category_text": None,
            "unsupported_category_evidence": None,
            "intents": ["refine"],
            "state_events": [SEGSEV12Event.model_validate(item) for item in events],
            "item_action": None,
            "tradeoff": None,
        }
    )


def raw_event(
    canonical_id: str,
    act: str,
    evidence: str,
    *,
    scope: str | None = None,
    value: str | None = None,
) -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "act": act,
        "scope_after": scope,
        "value_after": value,
        "trigger_evidence_text": evidence,
        "origin": "explicit",
        "confidence": 1.0,
    }


async def adapt(output: SEGSEV12ProposalOutput, state: Any) -> Any:
    return await SEGSEV14UnderstandingProvider(FakeClient(output))(
        utterance=output.utterance,
        previous_state_summary=summary_from_state(state),
        conversation_id="segse-v14-offline",
        turn=len(state.preference_history) + 1,
    )


def verify_prompt_is_unchanged() -> None:
    check(
        "v1.4 reuses the frozen v1.3 instruction text",
        SEGSE_V14_SYSTEM_PROMPT == SEGSE_V13_SYSTEM_PROMPT,
    )


def verify_facet_vocabulary_coverage() -> None:
    declared = set(TabletDomainFacetId.__args__)
    check(
        "every closed-vocabulary facet ID has dimension terms",
        declared == set(_FACET_DIMENSION_TERMS),
        sorted(declared ^ set(_FACET_DIMENSION_TERMS)),
    )


def verify_null_value_repair() -> None:
    legal = event(
        "activity_video",
        "assert",
        "mainly for movie nights",
    )
    repaired, audits = repair_facet_events([legal])
    check(
        "null facet value repaired from its own dimension evidence",
        repaired[0].value_after == "mainly for movie nights"
        and audits[0].reason
        == "facet_value_derived_from_current_dimension_evidence",
    )

    illegal = event(
        "activity_note_taking",
        "assert",
        "rehearsing from digital sheet music",
    )
    repaired, audits = repair_facet_events([illegal])
    check(
        "null facet value left incomplete when the dimension is untouched",
        repaired[0].value_after is None
        and audits[0].reason
        == "facet_value_absent_without_current_dimension_evidence",
    )
    _, rejected = authorize_segse_events(
        SEGSEV12ProposalOutput.model_validate(
            {
                "utterance": "I need a tablet for rehearsing from digital sheet music.",
                "domain_route": "in_domain",
                "unsupported_category_text": None,
                "unsupported_category_evidence": None,
                "intents": ["refine"],
                "state_events": [],
                "item_action": None,
                "tradeoff": None,
            }
        ).model_copy(update={"state_events": repaired}),
        current_utterance="I need a tablet for rehearsing from digital sheet music.",
        previous_state_summary=summary_from_state(create_tablet_environment_state()),
    )
    check(
        "unsupported facet proposal is still rejected after repair",
        rejected and rejected[0].reason == "assert_requires_value_after",
        [item.reason for item in rejected],
    )

    scoped = event(
        "activity_general",
        "assert",
        "casual web browsing",
        scope="soft",
        relation="prefer",
    )
    repaired, audits = repair_facet_events([scoped])
    check(
        "illegal facet scope is cleared instead of discarded",
        repaired[0].scope_after is None
        and repaired[0].relation is None
        and repaired[0].value_after == "casual web browsing"
        and [item.reason for item in audits]
        == [
            "facet_scope_and_relation_cleared",
            "facet_value_derived_from_current_dimension_evidence",
        ],
    )

    preference = event(
        "battery",
        "assert",
        "Long battery life is a priority.",
        scope="soft",
        value="long battery life",
        relation="prefer",
    )
    repaired, audits = repair_facet_events([preference])
    check(
        "preference events are untouched by the facet repair",
        repaired == [preference] and not audits,
    )


def verify_hard_value_dimension() -> None:
    utterance = "Actually make the internal-storage minimum 256 GB."
    correction = event(
        "storage_capacity",
        "assert",
        "minimum 256 GB.",
        scope="hard",
        value="256",
        relation="require",
    )
    reason, scope = hard_value_decision(correction, current_utterance=utterance)
    check(
        "value correction accepted from utterance-level dimension evidence",
        reason is None and scope == "current_utterance",
        (reason, scope),
    )

    anchored = event(
        "storage_capacity",
        "assert",
        "Built-in internal storage must be at least 128 GB.",
        scope="hard",
        value="128",
        relation="require",
    )
    reason, scope = hard_value_decision(
        anchored, current_utterance="Built-in internal storage must be at least 128 GB."
    )
    check(
        "anchor-local dimension evidence still wins",
        reason is None and scope == "anchor",
        (reason, scope),
    )

    ambiguous = "I want 8 GB of RAM and 256 GB of internal storage."
    misassigned = event(
        "storage_capacity",
        "assert",
        "8 GB of RAM",
        scope="hard",
        value="8",
        relation="require",
    )
    reason, scope = hard_value_decision(misassigned, current_utterance=ambiguous)
    check(
        "competing dimension in the same utterance blocks the fallback",
        reason == "storage_requires_internal_capacity_dimension" and scope is None,
        (reason, scope),
    )

    wrong_dimension = event(
        "min_rating",
        "assert",
        "The screen must be at least 11 inches.",
        scope="hard",
        value="11 inches",
        relation="require",
    )
    reason, _ = hard_value_decision(
        wrong_dimension, current_utterance="The screen must be at least 11 inches."
    )
    check(
        "screen size cannot become a star rating",
        reason == "min_rating_requires_zero_to_five_star_dimension",
        reason,
    )

    expansion = event(
        "storage_capacity",
        "assert",
        "a microSD card of 512 GB would be convenient",
        scope="hard",
        value="512",
        relation="require",
    )
    reason, _ = hard_value_decision(
        expansion,
        current_utterance="A microSD card of 512 GB would be convenient.",
    )
    check(
        "removable expansion still cannot set an internal-storage floor",
        reason == "storage_requires_internal_capacity_dimension",
        reason,
    )

    rating = event(
        "min_rating",
        "assert",
        "at least 4.3 stars",
        scope="hard",
        value="4.3",
        relation="require",
    )
    accepted, rejections, value_rejections, dimensions = filter_hard_value_contracts(
        [rating], [], current_utterance="Only tablets with at least 4.3 stars, please."
    )
    check(
        "in-range star minimum accepted and audited",
        accepted == [rating]
        and not rejections
        and not value_rejections
        and dimensions[0].dimension_evidence_scope == "anchor",
    )

    no_price_word = event(
        "budget",
        "assert",
        "at least 256 GB",
        scope="hard",
        value="256",
        relation="require",
    )
    reason, _ = hard_value_decision(
        no_price_word,
        current_utterance="Storage must be at least 256 GB, and that is my maximum.",
    )
    check(
        "generic quantifiers alone cannot ground a budget value",
        reason == "budget_requires_price_dimension",
        reason,
    )

    check(
        "unknown dimension scope is refused for unmapped IDs",
        dimension_evidence_scope(
            "portability", anchor="light", current_utterance="light"
        )
        is None,
    )


def verify_scope_relaxation() -> None:
    hard_display = {
        "hard_constraints": {
            "display": {
                "canonical_id": "display",
                "value_text": "at least 11 inch screen",
                "origin": "explicit",
                "confidence": 1.0,
                "status": "confirmed",
                "evidence_turn_ids": ["t2"],
                "updated_at_turn_id": "t2",
            }
        },
        "soft_constraints": {},
        "subjective_needs": {},
    }
    utterance = (
        "An 11-inch display would be nice, but screen size is no longer a "
        "strict requirement."
    )
    retract = event(
        "display",
        "retract",
        "screen size is no longer a strict requirement.",
        source="prior_state_reference",
        source_ref="display",
    )
    converted, audits = repair_scope_relaxations(
        [retract], current_utterance=utterance, previous_state_summary=hard_display
    )
    check(
        "explicit hard-to-soft relaxation becomes a scope correction",
        converted[0].act == "assert"
        and converted[0].scope_after == "soft"
        and converted[0].relation == "prefer"
        and converted[0].trigger_evidence_text == "An 11-inch display would be nice"
        and converted[0].trigger_evidence_text in utterance
        and audits[0].reason
        == "explicit_hard_to_soft_relaxation_retyped_as_scope_correction",
        converted[0].model_dump(),
    )

    plain_retract_utterance = "Screen size is no longer a requirement at all."
    converted, audits = repair_scope_relaxations(
        [
            event(
                "display",
                "retract",
                "no longer a requirement",
                source="prior_state_reference",
                source_ref="display",
            )
        ],
        current_utterance=plain_retract_utterance,
        previous_state_summary=hard_display,
    )
    check(
        "relaxation without a positive preference stays a retraction",
        converted[0].act == "retract" and not audits,
    )

    other_dimension = (
        "Long battery life would be nice, but screen size is no longer a requirement."
    )
    converted, audits = repair_scope_relaxations(
        [
            event(
                "display",
                "retract",
                "screen size is no longer a requirement",
                source="prior_state_reference",
                source_ref="display",
            )
        ],
        current_utterance=other_dimension,
        previous_state_summary=hard_display,
    )
    check(
        "a positive clause about another dimension cannot soften this ID",
        converted[0].act == "retract" and not audits,
    )

    hard_storage = {
        "hard_constraints": {
            "storage_capacity": {
                "canonical_id": "storage_capacity",
                "value_text": "at least 256 GB internal storage",
                "origin": "explicit",
                "confidence": 1.0,
                "status": "confirmed",
                "evidence_turn_ids": ["t1"],
                "updated_at_turn_id": "t1",
            }
        },
        "soft_constraints": {},
        "subjective_needs": {},
    }
    converted, audits = repair_scope_relaxations(
        [
            event(
                "storage_capacity",
                "retract",
                "it doesn't have to be a requirement",
                source="prior_state_reference",
                source_ref="storage_capacity",
            )
        ],
        current_utterance=(
            "256 GB of internal storage would be nice, but it doesn't have to be a "
            "requirement."
        ),
        previous_state_summary=hard_storage,
    )
    check(
        "hard-only IDs keep retraction because soft scope is not representable",
        converted[0].act == "retract" and not audits,
    )

    converted, audits = repair_scope_relaxations(
        [
            event(
                "display",
                "retract",
                "screen size is no longer a strict requirement.",
                source="prior_state_reference",
                source_ref="display",
            )
        ],
        current_utterance=utterance,
        previous_state_summary={
            "hard_constraints": {},
            "soft_constraints": {},
            "subjective_needs": {},
        },
    )
    check(
        "no active prior fact means no conversion",
        converted[0].act == "retract" and not audits,
    )


async def verify_end_to_end_paths() -> None:
    state = create_tablet_environment_state()

    facet_and_budget = proposal(
        "It is mainly for movie nights, and the total cost must remain under $420.",
        raw_event("activity_video", "assert", "mainly for movie nights"),
        raw_event(
            "budget",
            "assert",
            "the total cost must remain under $420",
            scope="hard",
            value="420",
        ),
    )
    understanding = await adapt(facet_and_budget, state)
    check(
        "compound turn keeps both the repaired facet and the budget",
        sorted(item.canonical_id for item in understanding.candidates)
        == ["activity_video", "budget"],
        [item.canonical_id for item in understanding.candidates],
    )
    state, _ = update_segse_dialogue_state(state, understanding, [], turn_id="t1")
    check(
        "repaired facet reaches the accumulated state",
        state.subjective_needs.activity is not None
        and state.subjective_needs.activity.canonical_id == "activity_video",
    )

    add_storage = proposal(
        "Set the built-in storage floor at 128 GB.",
        raw_event(
            "storage_capacity",
            "assert",
            "Set the built-in storage floor at 128 GB.",
            scope="hard",
            value="128",
        ),
    )
    storage_understanding = await adapt(add_storage, state)
    state, _ = update_segse_dialogue_state(
        state, storage_understanding, [], turn_id="t2"
    )
    check(
        "hard storage floor added",
        storage_understanding.segse_material_operations[0].operation == "add",
    )

    confirmation = proposal(
        "Keep that 128 GB internal-storage floor exactly as set.",
        raw_event(
            "storage_capacity",
            "confirm",
            "128 GB internal-storage floor exactly as set.",
        ),
    )
    confirm_understanding = await adapt(confirmation, state)
    state, confirm_diff = update_segse_dialogue_state(
        state, confirm_understanding, [], turn_id="t3"
    )
    check(
        "confirmation adds support without a material change",
        not confirm_understanding.segse_material_operations
        and confirm_understanding.segse_metadata_deltas[0].canonical_id
        == "storage_capacity"
        and "hard_constraints.storage_capacity" not in confirm_diff.changed_paths,
    )

    carryover = proposal(
        "Please refresh the tablet choices; none of my requirements have changed."
    )
    carryover_understanding = await adapt(carryover, state)
    state, carryover_diff = update_segse_dialogue_state(
        state, carryover_understanding, [], turn_id="t4"
    )
    check(
        "an event-free turn produces no candidate and no material change",
        not carryover_understanding.candidates
        and not carryover_understanding.segse_material_operations
        and not carryover_diff.changed_paths,
    )

    value_correction = proposal(
        "Actually make the internal-storage minimum 256 GB.",
        raw_event(
            "storage_capacity",
            "assert",
            "minimum 256 GB.",
            scope="hard",
            value="256",
        ),
    )
    correction_understanding = await adapt(value_correction, state)
    state, correction_diff = update_segse_dialogue_state(
        state, correction_understanding, [], turn_id="t5"
    )
    check(
        "value correction survives dimension validation",
        correction_understanding.segse_material_operations[0].operation
        == "update_value"
        and "hard_constraints.storage_capacity" in correction_diff.changed_paths
        and "256" in state.hard_constraints["storage_capacity"].value_text
        and correction_understanding.segse_v14_dimension_evidence[0]
        .dimension_evidence_scope
        == "current_utterance",
    )

    hard_display = proposal(
        "The screen must be at least 11 inches.",
        raw_event(
            "display",
            "assert",
            "The screen must be at least 11 inches.",
            scope="hard",
            value="11",
        ),
    )
    display_understanding = await adapt(hard_display, state)
    state, _ = update_segse_dialogue_state(
        state, display_understanding, [], turn_id="t6"
    )
    check("hard display filter added", "display" in state.hard_constraints)

    softening = proposal(
        "An 11-inch display would be nice, but screen size is no longer a strict "
        "requirement.",
        raw_event(
            "display",
            "retract",
            "screen size is no longer a strict requirement.",
        ),
    )
    softening_understanding = await adapt(softening, state)
    state, softening_diff = update_segse_dialogue_state(
        state, softening_understanding, [], turn_id="t7"
    )
    check(
        "hard-to-soft relaxation is a scope correction, not a deletion",
        softening_understanding.segse_material_operations[0].operation
        == "update_scope"
        and "display" not in state.hard_constraints
        and state.soft_constraints["display"].status != "superseded"
        and {"hard_constraints.display", "soft_constraints.display"}
        <= set(softening_diff.changed_paths),
        softening_understanding.segse_material_operations,
    )

    soft_to_hard = proposal(
        "Make 11 inches a strict screen minimum after all.",
        raw_event(
            "display",
            "assert",
            "11 inches a strict screen minimum",
            scope="hard",
            value="at least 11 inch screen",
        ),
    )
    promote_understanding = await adapt(soft_to_hard, state)
    state, promote_diff = update_segse_dialogue_state(
        state, promote_understanding, [], turn_id="t8"
    )
    check(
        "soft-to-hard promotion is also a scope correction",
        promote_understanding.segse_material_operations[0].operation
        == "update_scope"
        and "display" in state.hard_constraints
        and "display" not in state.soft_constraints,
    )

    retraction = proposal(
        "I no longer require any particular screen size.",
        raw_event(
            "display",
            "retract",
            "no longer require any particular screen size",
        ),
    )
    retract_understanding = await adapt(retraction, state)
    state, retract_diff = update_segse_dialogue_state(
        state, retract_understanding, [], turn_id="t9"
    )
    check(
        "explicit retraction still tombstones the fact",
        retract_understanding.segse_material_operations[0].operation == "retract"
        and state.hard_constraints["display"].status == "superseded"
        and "hard_constraints.display" in retract_diff.changed_paths,
    )

    negative_os = proposal(
        "Windows is the only system I do not want.",
        raw_event(
            "operating_system",
            "assert",
            "Windows",
            scope="hard",
            value="Windows required",
        ),
    )
    negative_understanding = await adapt(negative_os, state)
    check(
        "negative polarity cannot become a positive requirement",
        not negative_understanding.candidates
        and negative_understanding.segse_event_rejections[0].reason
        == "negated_phrase_cannot_authorize_positive_event",
        [item.reason for item in negative_understanding.segse_event_rejections],
    )

    unsupported_facet = proposal(
        "Show me the current tablet choices.",
        raw_event("activity_general", "assert", "Show me the current tablet choices."),
    )
    unsupported_understanding = await adapt(unsupported_facet, state)
    check(
        "an unsupported facet claim on a browse turn stays rejected",
        not unsupported_understanding.candidates
        and unsupported_understanding.segse_event_rejections[0].reason
        == "assert_requires_value_after",
        [item.reason for item in unsupported_understanding.segse_event_rejections],
    )

    repeated = proposal(
        "The internal-storage minimum is still 256 GB.",
        raw_event(
            "storage_capacity",
            "assert",
            "internal-storage minimum is still 256 GB",
            scope="hard",
            value="256",
        ),
    )
    repeated_understanding = await adapt(repeated, state)
    _, repeated_diff = update_segse_dialogue_state(
        state, repeated_understanding, [], turn_id="t10"
    )
    check(
        "C still suppresses a semantically identical hard value",
        "storage_capacity" in repeated_understanding.segse_semantic_noop_ids
        and "hard_constraints.storage_capacity" not in repeated_diff.changed_paths,
    )


async def main() -> None:
    verify_prompt_is_unchanged()
    verify_facet_vocabulary_coverage()
    verify_null_value_repair()
    verify_hard_value_dimension()
    verify_scope_relaxation()
    await verify_end_to_end_paths()
    print("SEGSE v1.4 deterministic verification completed.")


if __name__ == "__main__":
    asyncio.run(main())
