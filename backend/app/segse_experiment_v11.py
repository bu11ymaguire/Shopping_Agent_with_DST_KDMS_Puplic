"""SEGSE-lite v1.1 development adapter with a smaller LLM write surface.

The frozen v1 module and first-run artifacts remain untouched.  V1.1 removes
redundant source/relation fields from the model output, isolates malformed control
fields from state events, and records every deterministic normalization explicitly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, ClassVar, Literal, TypeAlias

from pydantic import Field, ValidationError

from app.llm import LLMClient, system, user
from app.models.actual_demo import (
    ACTUAL_PREFERENCE_IDS,
    ActualContract,
    ActualItemActionCandidate,
    ActualPreferenceId,
    ActualRejectionReason,
    ActualTradeoffCandidate,
    TabletDomainCanonicalId,
)
from app.models.understanding import EvidenceOrigin, IntentName, ItemActionName
from app.segse_experiment import (
    SEGSEEventRejection,
    SEGSEProposalOutput,
    SEGSEStateEvent,
    SEGSETabletDomainUnderstandingOutput,
    _event_candidate,
    _facets_for_candidates,
    authorize_segse_events,
    compact_segse_previous_state,
    update_segse_dialogue_state,
)


SEGSE_V11_PROMPT_VERSION = "segse-lite-tablet-domain-en-v1.1-dev"


class _V11EventBase(ActualContract):
    canonical_id: TabletDomainCanonicalId
    trigger_evidence_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    confidence: float = Field(ge=0, le=1)


class SEGSEV11AssertEvent(_V11EventBase):
    act: Literal["assert"]
    scope_after: Literal["hard", "soft"] | None = None
    value_after: str = Field(min_length=1)


class SEGSEV11ConfirmEvent(_V11EventBase):
    act: Literal["confirm"]


class SEGSEV11RetractEvent(_V11EventBase):
    act: Literal["retract"]


class SEGSEV11RefineEvent(_V11EventBase):
    act: Literal["refine"]
    scope_after: Literal["soft"] | None = None
    value_after: str = Field(min_length=1)


SEGSEV11Event: TypeAlias = Annotated[
    SEGSEV11AssertEvent
    | SEGSEV11ConfirmEvent
    | SEGSEV11RetractEvent
    | SEGSEV11RefineEvent,
    Field(discriminator="act"),
]


class SEGSEV11ItemActionProposal(ActualContract):
    """Tolerant raw action; downstream conversion validates action-specific fields."""

    name: ItemActionName
    target_rank: int | None = Field(default=None, ge=1, le=3)
    compare_rank: int | None = Field(default=None, ge=1, le=3)
    rejection_reason: ActualRejectionReason | None = None


class SEGSEV11TradeoffProposal(ActualContract):
    prioritized_ids: list[ActualPreferenceId] = Field(min_length=1, max_length=4)
    compromised_ids: list[ActualPreferenceId] = Field(min_length=1, max_length=4)
    value_text: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    confidence: float = Field(ge=0, le=1)


class SEGSEV11ProposalOutput(ActualContract):
    schema_version: ClassVar[str] = "segse-proposal-output-v1.1"

    utterance: str = Field(min_length=1)
    domain_route: Literal["in_domain", "unsupported_category"]
    unsupported_category_text: str | None = None
    unsupported_category_evidence: str | None = None
    intents: list[IntentName] = Field(min_length=1)
    state_events: list[SEGSEV11Event]
    item_action: SEGSEV11ItemActionProposal | None = None
    tradeoff: SEGSEV11TradeoffProposal | None = None


class SEGSEV11Normalization(ActualContract):
    event_index: int = Field(ge=0)
    canonical_id: TabletDomainCanonicalId
    raw_act: Literal["assert", "confirm", "retract", "refine"]
    normalized_act: Literal["assert", "confirm", "retract", "refine"]
    reason: str = Field(min_length=1)


class SEGSEV11UnderstandingOutput(SEGSETabletDomainUnderstandingOutput):
    segse_v11_raw_proposal_events: list[SEGSEV11Event]
    segse_v11_normalized_events: list[SEGSEStateEvent]
    segse_v11_normalizations: list[SEGSEV11Normalization] = Field(default_factory=list)
    segse_v11_control_violations: list[str] = Field(default_factory=list)


SEGSE_V11_SYSTEM_PROMPT = """You are the SPN Understanding (PLAN) node in a tablet-only shopping workflow.
Return only the strict JSON object described by the response schema.

Persistent previous state is READ-ONLY reference memory. Output only state events expressed by the
CURRENT user utterance. Omit untouched state: absence means carryover. Never copy a previous fact just
because it appears in reference memory. Each trigger_evidence_text must be an exact substring of the
current user utterance.

Choose exactly one semantic act per touched canonical fact:
- assert: a genuinely new fact, a replacement value, a hard/soft scope change, or reactivation.
- confirm: the user explicitly keeps or affirms an active prior fact; it has no value/scope payload.
- retract: the user removes the entire active fact; it has no value/scope payload. Softening a hard
  requirement is assert with scope_after=soft, not retract.
- refine: the user adds more specific qualitative meaning to an ACTIVE soft/facet fact. It is never
  used for a new fact, a numeric correction, or reactivation.

The application derives source_ref, value_source, and relation. Do not output those fields. For
preference assertions, hard means a required filter and soft means a preference. Facet assertions use
scope_after=null. Hard IDs are budget, storage_capacity, memory_capacity, max_weight, min_rating,
operating_system, and quantitative display. Keep storage and RAM separate. microSD/removable expansion
storage is not internal storage. Qualitative 'too heavy' is portability; max_weight requires a number.

The state represents positive requirements/preferences only. For negative OS ('not Windows'), no
minimum/dont-care without an active prior fact, or unsupported expansion-storage requirements, emit no
positive state event. For an active prior fact, 'no longer/no minimum/not anymore' is retract.

Item actions and trade-offs are separate controls. Words such as compare/remove/guess do not imply an
item action unless the user clearly refers to a visible ranked option. A reason such as 'too heavy for
my commute' may independently ground portability, but simple dislike must not invent an attribute.

An explicitly requested non-tablet category uses domain_route=unsupported_category. Otherwise use
in_domain. Keep utterance exactly equal to the current utterance.
"""


def _active_previous_ids(summary: Mapping[str, Any]) -> set[str]:
    compact = compact_segse_previous_state(summary)
    return {
        str(item["canonical_id"])
        for item in [
            *compact["active_hard_facts_for_correction"],
            *compact["active_qualitative_ids_for_reference"],
        ]
    }


def _raw_to_v1(event: SEGSEV11Event) -> SEGSEStateEvent:
    scope = getattr(event, "scope_after", None)
    value = getattr(event, "value_after", None)
    if event.act in {"confirm", "retract"}:
        value_source = "prior_state_reference"
        source_ref: TabletDomainCanonicalId | None = event.canonical_id
        relation = None
    else:
        value_source = "current_utterance"
        source_ref = event.canonical_id if event.act == "refine" else None
        relation = "require" if scope == "hard" else "prefer" if scope == "soft" else None
    return SEGSEStateEvent(
        canonical_id=event.canonical_id,
        act=event.act,
        scope_after=scope,
        value_after=value,
        relation=relation,
        trigger_evidence_text=event.trigger_evidence_text,
        value_source=value_source,  # type: ignore[arg-type]
        source_ref=source_ref,
        origin=event.origin,
        confidence=event.confidence,
    )


def _normalize_events(
    raw_events: list[SEGSEStateEvent], active_ids: set[str]
) -> tuple[list[SEGSEStateEvent], list[SEGSEV11Normalization]]:
    normalized: list[SEGSEStateEvent] = []
    audits: list[SEGSEV11Normalization] = []
    for index, event in enumerate(raw_events):
        current = event
        if event.act == "refine" and event.canonical_id not in active_ids:
            current = event.model_copy(
                update={
                    "act": "assert",
                    "value_source": "current_utterance",
                    "source_ref": None,
                }
            )
            audits.append(
                SEGSEV11Normalization(
                    event_index=index,
                    canonical_id=event.canonical_id,
                    raw_act="refine",
                    normalized_act="assert",
                    reason="refine_without_active_prior_normalized_to_assert",
                )
            )
        normalized.append(current)
    return normalized, audits


def _sanitize_action(
    raw: SEGSEV11ItemActionProposal | None,
) -> tuple[ActualItemActionCandidate | None, list[str]]:
    if raw is None:
        return None, []
    try:
        return ActualItemActionCandidate.model_validate(raw.model_dump()), []
    except ValidationError as error:
        return None, [f"item_action_rejected:{error.errors()[0]['type']}"]


def _sanitize_tradeoff(
    raw: SEGSEV11TradeoffProposal | None,
) -> tuple[ActualTradeoffCandidate | None, list[str]]:
    if raw is None:
        return None, []
    try:
        return ActualTradeoffCandidate.model_validate(raw.model_dump()), []
    except ValidationError as error:
        return None, [f"tradeoff_rejected:{error.errors()[0]['type']}"]


def _sanitize_controls(
    proposal: SEGSEV11ProposalOutput,
    *,
    clean_utterance: str,
) -> tuple[dict[str, Any], list[str]]:
    violations: list[str] = []
    action, action_violations = _sanitize_action(proposal.item_action)
    tradeoff, tradeoff_violations = _sanitize_tradeoff(proposal.tradeoff)
    violations.extend(action_violations)
    violations.extend(tradeoff_violations)
    if proposal.utterance != clean_utterance:
        violations.append("proposal_utterance_did_not_match_input")

    route = proposal.domain_route
    unsupported_text = proposal.unsupported_category_text
    unsupported_evidence = proposal.unsupported_category_evidence
    if route == "unsupported_category" and (
        not unsupported_text or not unsupported_evidence
    ):
        violations.append("incomplete_unsupported_route_demoted_to_in_domain")
        route = "in_domain"
        unsupported_text = None
        unsupported_evidence = None
    elif route == "in_domain":
        if unsupported_text is not None or unsupported_evidence is not None:
            violations.append("in_domain_route_cleared_unsupported_payload")
        unsupported_text = None
        unsupported_evidence = None

    action_intents = {"inspect", "reject", "compare", "purchase"}
    intents = list(dict.fromkeys(proposal.intents))
    if route == "unsupported_category":
        if action is not None or tradeoff is not None:
            violations.append("unsupported_route_cleared_controls")
        action = None
        tradeoff = None
        intents = ["unknown"]
    elif action is None:
        intents = [item for item in intents if item not in action_intents]
    else:
        required = {
            "inspect_current": "inspect",
            "reject_first": "reject",
            "compare_first_second": "compare",
            "purchase_current": "purchase",
        }[action.name]
        intents = [item for item in intents if item not in action_intents or item == required]
        if required not in intents:
            intents.append(required)  # type: ignore[arg-type]
            violations.append("matching_action_intent_added")
    if not intents:
        intents = ["refine" if proposal.state_events else "unknown"]
        violations.append("empty_intents_repaired")
    return {
        "utterance": clean_utterance,
        "domain_route": route,
        "unsupported_category_text": unsupported_text,
        "unsupported_category_evidence": unsupported_evidence,
        "intents": intents,
        "item_action": action,
        "tradeoff": tradeoff,
    }, violations


_EXPANSION_STORAGE = re.compile(
    r"\b(?:micro\s*sd|sd\s*card|removable|expandable|expansion)\b", re.IGNORECASE
)
_NUMERIC_HARD_IDS = {
    "budget",
    "storage_capacity",
    "memory_capacity",
    "max_weight",
    "min_rating",
}


def _v11_post_authorize(
    events: list[SEGSEStateEvent],
    *,
    current_utterance: str,
    prior_rejections: list[SEGSEEventRejection],
) -> tuple[list[SEGSEStateEvent], list[SEGSEEventRejection]]:
    accepted: list[SEGSEStateEvent] = []
    rejections = list(prior_rejections)
    for event in events:
        reason: str | None = None
        evidence = event.trigger_evidence_text
        if event.canonical_id == "storage_capacity" and _EXPANSION_STORAGE.search(
            evidence
        ):
            reason = "expansion_storage_is_not_internal_storage"
        elif (
            event.canonical_id in _NUMERIC_HARD_IDS
            and event.act in {"assert", "refine"}
            and not re.search(r"\d", f"{event.value_after or ''} {evidence}")
        ):
            reason = "numeric_hard_fact_requires_numeric_current_evidence"
        if reason is None:
            accepted.append(event)
        else:
            index = next(
                (
                    idx
                    for idx, candidate in enumerate(events)
                    if candidate is event
                ),
                0,
            )
            rejections.append(
                SEGSEEventRejection(
                    event_index=index,
                    canonical_id=event.canonical_id,
                    reason=reason,
                )
            )
    return accepted, rejections


class SEGSEV11UnderstandingProvider:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> SEGSEV11UnderstandingOutput:
        clean = utterance.strip()
        if not clean:
            raise ValueError("utterance cannot be empty")
        compact = compact_segse_previous_state(previous_state_summary)
        raw = await self.client.generate_structured(
            messages=[
                system(SEGSE_V11_SYSTEM_PROMPT),
                user(
                    json.dumps(
                        {
                            "environment": {
                                "domain": "tablet_shopping",
                                "category": "tablet",
                            },
                            "current_utterance_to_extract": clean,
                            "reference_only_previous_state": compact,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
            ],
            response_model=SEGSEV11ProposalOutput,
            temperature=0.0,
            node="spn-understanding",
            prompt_version=SEGSE_V11_PROMPT_VERSION,
            conversation_id=conversation_id,
            turn=turn,
        )
        controls, control_violations = _sanitize_controls(
            raw, clean_utterance=clean
        )
        raw_events = [_raw_to_v1(event) for event in raw.state_events]
        normalized, normalizations = _normalize_events(
            raw_events, _active_previous_ids(previous_state_summary)
        )
        strict_proposal = SEGSEProposalOutput(
            **controls,
            state_events=normalized,
        )
        authorized, rejections = authorize_segse_events(
            strict_proposal,
            current_utterance=clean,
            previous_state_summary=previous_state_summary,
        )
        authorized, rejections = _v11_post_authorize(
            authorized,
            current_utterance=clean,
            prior_rejections=rejections,
        )
        previous = {
            item["canonical_id"]: item
            for item in [
                *compact["active_hard_facts_for_correction"],
                *compact["active_qualitative_ids_for_reference"],
            ]
        }
        candidates = [
            _event_candidate(event, previous)
            for event in authorized
            if event.act in {"assert", "refine"}
        ]
        return SEGSEV11UnderstandingOutput(
            utterance=clean,
            domain_route=controls["domain_route"],
            unsupported_category_text=controls["unsupported_category_text"],
            unsupported_category_evidence=controls[
                "unsupported_category_evidence"
            ],
            intents=controls["intents"],
            facets=_facets_for_candidates(candidates),
            candidates=candidates,
            item_action=controls["item_action"],
            tradeoff=controls["tradeoff"],
            supersedes=[],
            residual_color_choice=False,
            segse_raw_events=raw_events,
            segse_authorized_events=authorized,
            segse_event_rejections=rejections,
            segse_contract_violations=control_violations,
            segse_v11_raw_proposal_events=raw.state_events,
            segse_v11_normalized_events=normalized,
            segse_v11_normalizations=normalizations,
            segse_v11_control_violations=control_violations,
        )


__all__ = [
    "SEGSE_V11_PROMPT_VERSION",
    "SEGSEV11ProposalOutput",
    "SEGSEV11UnderstandingOutput",
    "SEGSEV11UnderstandingProvider",
    "update_segse_dialogue_state",
]
