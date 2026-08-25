"""Sparse evidence-grounded semantic-event experiment for Full-Memory DST.

This module is intentionally isolated from the frozen official and M0/C paths.
The LLM proposes user semantic acts; deterministic code authorizes those acts and
derives state-relative ADD/UPDATE/no-op effects.  Persistent state is reference-only
input and never becomes evidence for a current-turn event.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, ClassVar, Literal, TypeAlias

from pydantic import Field, model_validator

from app.llm import LLMClient, system, user
from app.models import DialogueState, PreferenceValue, RankedProduct, StateDiff
from app.models.actual_demo import (
    ACTUAL_FACET_IDS,
    ACTUAL_PREFERENCE_IDS,
    ActualContract,
    ActualItemActionCandidate,
    ActualTradeoffCandidate,
    TabletDomainCanonicalId,
    TabletDomainFacetCandidates,
    TabletDomainStateUpdateCandidate,
    TabletDomainUnderstandingOutput,
)
from app.models.understanding import EvidenceOrigin, IntentName, SPNFacetName
from app.nodes.actual_state_manager import (
    create_tablet_environment_state,
    update_actual_dialogue_state,
)


SEGSE_PROMPT_VERSION = "segse-lite-tablet-domain-en-v1-dev"

SEGSEAct: TypeAlias = Literal["assert", "confirm", "retract", "refine"]
SEGSEValueSource: TypeAlias = Literal[
    "current_utterance",
    "prior_state_reference",
]
SEGSERelation: TypeAlias = Literal["require", "prefer"]

_ALLOWED_SCOPES: dict[str, frozenset[str]] = {
    "budget": frozenset({"hard"}),
    "budget_flexibility": frozenset({"soft"}),
    "storage_capacity": frozenset({"hard"}),
    "memory_capacity": frozenset({"hard"}),
    "max_weight": frozenset({"hard"}),
    "min_rating": frozenset({"hard"}),
    "operating_system": frozenset({"hard"}),
    "price_value": frozenset({"soft"}),
    "portability": frozenset({"soft"}),
    "note_taking": frozenset({"soft"}),
    "display": frozenset({"hard", "soft"}),
    "performance": frozenset({"soft"}),
    "battery": frozenset({"soft"}),
    "audio": frozenset({"soft"}),
    "durability": frozenset({"soft"}),
    "review_signal": frozenset({"soft"}),
    "child_friendly": frozenset({"soft"}),
}

_NEGATED_POSITIVE_PATTERNS = (
    re.compile(
        r"\b(?:do not|don't|does not|doesn't)\s+"
        r"(?:want|need|care(?:\s+about)?|require|matter)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:not setting|no)\s+(?:a\s+)?minimum\b", re.IGNORECASE),
    re.compile(
        r"\bnot\s+(?:windows|android|ipados|fire\s+os|chrome\s+os)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:anything|everything)\s+except\b", re.IGNORECASE),
    re.compile(r"\bwithout\b", re.IGNORECASE),
)
_RETAIN_PATTERNS = re.compile(
    r"\b(?:keep|retain|unchanged|same as before|still the same)\b",
    re.IGNORECASE,
)


SEGSE_SYSTEM_PROMPT = """You are the SPN Understanding (PLAN) node in a tablet-only shopping workflow.
The previous dialogue state is READ-ONLY memory for resolving references. It is never current-turn
evidence and must never be copied into the output.

Return only sparse state_events that the current user utterance actually triggers. If no state fact
is touched, return state_events=[]. Absence means deterministic carryover.

Semantic acts:
- assert: the user states a new fact or a replacement value. Do not decide whether this is ADD or
  UPDATE; the State Manager compares it with memory.
- confirm: the user explicitly keeps or confirms an existing fact. Use prior_state_reference and
  source_ref equal to that canonical ID. A confirmation is not a value update.
- retract: the user explicitly removes or waives an existing fact. Use prior_state_reference.
- refine: the user gives a more specific qualitative meaning for an existing soft preference or
  facet. It requires new current-utterance evidence. Hard numeric corrections use assert.

Evidence and source rules:
- trigger_evidence_text must be an exact substring of the CURRENT user utterance.
- assert/refine use value_source=current_utterance.
- confirm/retract use value_source=prior_state_reference and a resolvable source_ref.
- The current workflow does not persist a last-system-proposal value source. Do not invent one.
- Do not output a positive assertion for negation, no-minimum/dontcare language, microSD expansion,
  a retained existing value, or the compromised side of a trade-off.

Payload rules:
- Every preference ID uses a legal hard/soft scope. Facet IDs use scope_after=null.
- Hard assertions use relation=require. Soft assertions/refinements use relation=prefer. Facets use
  relation=null. confirm/retract carry no value, scope, or relation payload.
- Each canonical ID occurs at most once.
- state_events is the sole authority for preference/facet mutation. There is no parallel facet
  output. The application derives convenience facets only after deterministic authorization.

Supported hard facts: budget, storage_capacity, memory_capacity, max_weight, min_rating, a numeric
display minimum, and an explicitly required operating_system. Supported soft facts: price_value,
budget_flexibility, portability, note_taking, qualitative display, performance, battery, audio,
durability, review_signal, and child_friendly. Keep storage and RAM separate. Expansion storage is
not internal storage. A negative OS constraint such as 'not Windows' is currently unrepresentable,
so emit no positive operating_system event.

The tablet category is immutable environment state. An explicitly requested non-tablet category
uses domain_route=unsupported_category, intents=[unknown], no state_events, item action, or tradeoff.
Item actions and explicit trade-offs keep their dedicated structured fields and must not create
additional preference events without direct user evidence.
"""


class SEGSEStateEvent(ActualContract):
    canonical_id: TabletDomainCanonicalId
    act: SEGSEAct
    scope_after: Literal["hard", "soft"] | None = None
    value_after: str | None = None
    relation: SEGSERelation | None = None
    trigger_evidence_text: str = Field(min_length=1)
    value_source: SEGSEValueSource
    source_ref: TabletDomainCanonicalId | None = None
    origin: EvidenceOrigin
    confidence: float = Field(ge=0, le=1)


class SEGSEProposalOutput(ActualContract):
    """Raw LLM proposal before per-event deterministic authorization."""

    schema_version: ClassVar[str] = "segse-proposal-output-v1"

    utterance: str = Field(min_length=1)
    domain_route: Literal["in_domain", "unsupported_category"]
    unsupported_category_text: str | None = None
    unsupported_category_evidence: str | None = None
    intents: list[IntentName] = Field(min_length=1)
    state_events: list[SEGSEStateEvent]
    item_action: ActualItemActionCandidate | None = None
    tradeoff: ActualTradeoffCandidate | None = None

    @model_validator(mode="after")
    def control_contract(self) -> SEGSEProposalOutput:
        if len(self.intents) != len(set(self.intents)):
            raise ValueError("intents must not contain duplicates")
        action_to_intent = {
            "inspect_current": "inspect",
            "reject_first": "reject",
            "compare_first_second": "compare",
            "purchase_current": "purchase",
        }
        if self.item_action is None:
            if set(action_to_intent.values()) & set(self.intents):
                raise ValueError("action intents require item_action")
        elif action_to_intent[self.item_action.name] not in self.intents:
            raise ValueError("item_action requires its matching intent")

        unsupported = self.domain_route == "unsupported_category"
        if unsupported != (self.unsupported_category_text is not None):
            raise ValueError("unsupported route and category text must agree")
        if unsupported != (self.unsupported_category_evidence is not None):
            raise ValueError("unsupported route and category evidence must agree")
        if unsupported and (
            self.intents != ["unknown"]
            or self.item_action is not None
            or self.tradeoff is not None
        ):
            raise ValueError("unsupported-category controls must not mutate tablet state")
        return self


class SEGSEEventRejection(ActualContract):
    event_index: int = Field(ge=0)
    canonical_id: TabletDomainCanonicalId
    reason: str = Field(min_length=1)


class SEGSEAppliedOperation(ActualContract):
    canonical_id: TabletDomainCanonicalId
    operation: Literal[
        "add",
        "reactivate",
        "update_value",
        "update_scope",
        "refine",
        "retract",
    ]
    changed_paths: list[str]


class SEGSEMetadataDelta(ActualContract):
    canonical_id: TabletDomainCanonicalId
    changes: list[Literal["support_added", "provenance_promoted", "status_confirmed"]]
    decision_eligibility_changed: bool


class SEGSETabletDomainUnderstandingOutput(TabletDomainUnderstandingOutput):
    """Authorized adapter consumed by the unchanged explicit workflow."""

    segse_raw_events: list[SEGSEStateEvent]
    segse_authorized_events: list[SEGSEStateEvent]
    segse_event_rejections: list[SEGSEEventRejection]
    segse_contract_violations: list[str] = Field(default_factory=list)
    segse_material_operations: list[SEGSEAppliedOperation] = Field(
        default_factory=list
    )
    segse_metadata_deltas: list[SEGSEMetadataDelta] = Field(default_factory=list)
    segse_semantic_noop_ids: list[TabletDomainCanonicalId] = Field(
        default_factory=list
    )


def _facet_for_id(canonical_id: str) -> str:
    return next(
        name for name, values in ACTUAL_FACET_IDS.items() if canonical_id in values
    )


def _previous_records(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for scope, field in (("hard", "hard_constraints"), ("soft", "soft_constraints")):
        for value in summary.get(field, {}).values():
            if not isinstance(value, Mapping) or value.get("status") == "superseded":
                continue
            canonical_id = value.get("canonical_id")
            if canonical_id:
                records[str(canonical_id)] = {**value, "scope": scope}
    for value in summary.get("subjective_needs", {}).values():
        if not isinstance(value, Mapping) or value.get("status") == "superseded":
            continue
        canonical_id = value.get("canonical_id")
        if canonical_id:
            records[str(canonical_id)] = {**value, "scope": None}
    return records


def compact_segse_previous_state(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Keep correction targets without exposing previous evidence text as write evidence."""

    records = _previous_records(summary)
    hard_facts = [
        {
            "canonical_id": canonical_id,
            "scope": "hard",
            "value_text": value.get("value_text"),
            "status": value.get("status"),
        }
        for canonical_id, value in records.items()
        if value.get("scope") == "hard"
    ]
    qualitative = [
        {
            "canonical_id": canonical_id,
            "scope": value.get("scope"),
            "status": value.get("status"),
        }
        for canonical_id, value in records.items()
        if value.get("scope") != "hard"
    ]
    return {
        "environment_category_id": "category_tablet",
        "active_hard_facts_for_correction": hard_facts,
        "active_qualitative_ids_for_reference": qualitative,
        "current_item_rank_context": summary.get("current_item_rank_context"),
        "visible_ranked_products": summary.get("visible_ranked_products", []),
        "available_value_sources": [
            "current_utterance",
            "prior_state_reference",
        ],
    }


def _rejection(
    index: int, event: SEGSEStateEvent, reason: str
) -> SEGSEEventRejection:
    return SEGSEEventRejection(
        event_index=index,
        canonical_id=event.canonical_id,
        reason=reason,
    )


def _negates_positive_assertion(text: str) -> bool:
    return any(pattern.search(text) for pattern in _NEGATED_POSITIVE_PATTERNS)


def _event_is_negated(event: SEGSEStateEvent, current_utterance: str) -> bool:
    """Catch supported high-risk polarity inversions without pretending to be NLI."""

    if _negates_positive_assertion(event.trigger_evidence_text):
        return True
    if event.canonical_id == "operating_system":
        os_names = r"(?:windows|android|ipados|fire\s+os|chrome\s+os)"
        negative_os = (
            re.compile(rf"\bnot\s+{os_names}\b", re.IGNORECASE),
            re.compile(rf"\b(?:anything|everything)\s+except\s+{os_names}\b", re.IGNORECASE),
            re.compile(
                rf"\b(?:do not|don't)\s+want\b.{{0,50}}\b{os_names}\b",
                re.IGNORECASE,
            ),
            re.compile(
                rf"\b{os_names}\b.{{0,70}}\b(?:do not|don't)\s+want\b",
                re.IGNORECASE,
            ),
        )
        return any(pattern.search(current_utterance) for pattern in negative_os)
    if event.canonical_id in {
        "storage_capacity",
        "memory_capacity",
        "min_rating",
        "display",
    }:
        return bool(
            re.search(
                r"\b(?:not setting|no)\s+(?:a\s+)?minimum\b",
                current_utterance,
                re.IGNORECASE,
            )
        )
    return False


def authorize_segse_events(
    proposal: SEGSEProposalOutput,
    *,
    current_utterance: str,
    previous_state_summary: Mapping[str, Any],
) -> tuple[list[SEGSEStateEvent], list[SEGSEEventRejection]]:
    """Gate each event without failing the entire structured turn."""

    previous = _previous_records(previous_state_summary)
    compromised = set(proposal.tradeoff.compromised_ids) if proposal.tradeoff else set()
    seen: set[str] = set()
    accepted: list[SEGSEStateEvent] = []
    rejected: list[SEGSEEventRejection] = []

    for index, event in enumerate(proposal.state_events):
        reason: str | None = None
        is_preference = event.canonical_id in ACTUAL_PREFERENCE_IDS
        prior = previous.get(event.canonical_id)

        if proposal.domain_route == "unsupported_category":
            reason = "unsupported_category_forbids_state_event"
        elif event.canonical_id in seen:
            reason = "duplicate_canonical_id"
        elif event.trigger_evidence_text not in current_utterance:
            reason = "trigger_evidence_not_exact_current_substring"
        elif event.canonical_id in compromised:
            reason = "tradeoff_compromised_side_is_not_positive_preference"
        elif event.act in {"assert", "refine"} and _event_is_negated(
            event, current_utterance
        ):
            reason = "negated_phrase_cannot_authorize_positive_event"
        elif event.act == "assert":
            if event.value_source != "current_utterance" or event.source_ref is not None:
                reason = "assert_requires_current_utterance_source"
            elif not event.value_after:
                reason = "assert_requires_value_after"
            elif is_preference and event.scope_after not in _ALLOWED_SCOPES[event.canonical_id]:
                reason = "assert_scope_is_illegal_for_canonical_id"
            elif not is_preference and event.scope_after is not None:
                reason = "facet_assert_requires_null_scope"
            elif is_preference and event.relation != (
                "require" if event.scope_after == "hard" else "prefer"
            ):
                reason = "assert_relation_does_not_match_scope"
            elif not is_preference and event.relation is not None:
                reason = "facet_assert_requires_null_relation"
            elif prior is not None and _RETAIN_PATTERNS.search(
                event.trigger_evidence_text
            ):
                reason = "retained_fact_must_use_confirm"
        elif event.act in {"confirm", "retract"}:
            if event.value_source != "prior_state_reference":
                reason = f"{event.act}_requires_prior_state_source"
            elif event.source_ref != event.canonical_id:
                reason = f"{event.act}_source_ref_must_equal_canonical_id"
            elif prior is None:
                reason = f"{event.act}_requires_active_prior_fact"
            elif any(
                value is not None
                for value in (event.scope_after, event.value_after, event.relation)
            ):
                reason = f"{event.act}_must_not_repeat_state_payload"
            elif event.origin != "explicit":
                reason = f"{event.act}_must_be_explicit"
        else:
            if event.value_source != "current_utterance":
                reason = "refine_requires_current_utterance_source"
            elif event.source_ref != event.canonical_id:
                reason = "refine_source_ref_must_equal_canonical_id"
            elif prior is None:
                reason = "refine_requires_active_prior_fact"
            elif not event.value_after:
                reason = "refine_requires_value_after"
            elif prior.get("scope") == "hard":
                reason = "hard_corrections_must_use_assert"
            elif event.scope_after != prior.get("scope"):
                reason = "refine_must_preserve_scope"
            elif is_preference and event.relation != "prefer":
                reason = "soft_refine_requires_prefer_relation"
            elif not is_preference and event.relation is not None:
                reason = "facet_refine_requires_null_relation"

        seen.add(event.canonical_id)
        if reason is None:
            accepted.append(event)
        else:
            rejected.append(_rejection(index, event, reason))
    return accepted, rejected


def _event_candidate(
    event: SEGSEStateEvent,
    previous: Mapping[str, Mapping[str, Any]],
) -> TabletDomainStateUpdateCandidate:
    scope = event.scope_after
    if event.act == "refine" and scope is None and event.canonical_id in ACTUAL_PREFERENCE_IDS:
        scope = previous[event.canonical_id].get("scope")
    return TabletDomainStateUpdateCandidate(
        canonical_id=event.canonical_id,
        scope=scope,
        value_text=event.value_after or "invalid authorized event",
        evidence_text=event.trigger_evidence_text,
        origin=event.origin,
        confidence=event.confidence,
    )


def _facets_for_candidates(
    candidates: list[TabletDomainStateUpdateCandidate],
) -> TabletDomainFacetCandidates:
    values: dict[str, TabletDomainStateUpdateCandidate] = {}
    for candidate in candidates:
        if candidate.canonical_id in ACTUAL_PREFERENCE_IDS:
            continue
        values.setdefault(_facet_for_id(candidate.canonical_id), candidate)
    return TabletDomainFacetCandidates(**values)


class SEGSEUnderstandingProvider:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> SEGSETabletDomainUnderstandingOutput:
        clean = utterance.strip()
        if not clean:
            raise ValueError("utterance cannot be empty")
        compact = compact_segse_previous_state(previous_state_summary)
        proposal = await self.client.generate_structured(
            messages=[
                system(SEGSE_SYSTEM_PROMPT),
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
            response_model=SEGSEProposalOutput,
            temperature=0.0,
            node="spn-understanding",
            prompt_version=SEGSE_PROMPT_VERSION,
            conversation_id=conversation_id,
            turn=turn,
        )
        authorized, rejections = authorize_segse_events(
            proposal,
            current_utterance=clean,
            previous_state_summary=previous_state_summary,
        )
        previous = _previous_records(previous_state_summary)
        candidates = [
            _event_candidate(event, previous)
            for event in authorized
            if event.act in {"assert", "refine"}
        ]
        violations = []
        if proposal.utterance != clean:
            violations.append("proposal_utterance_did_not_match_input")
        return SEGSETabletDomainUnderstandingOutput(
            utterance=clean,
            domain_route=proposal.domain_route,
            unsupported_category_text=proposal.unsupported_category_text,
            unsupported_category_evidence=proposal.unsupported_category_evidence,
            intents=proposal.intents,
            facets=_facets_for_candidates(candidates),
            candidates=candidates,
            item_action=proposal.item_action,
            tradeoff=proposal.tradeoff,
            supersedes=[],
            residual_color_choice=False,
            segse_raw_events=proposal.state_events,
            segse_authorized_events=authorized,
            segse_event_rejections=rejections,
            segse_contract_violations=violations,
        )


def _locate(
    state: DialogueState,
    canonical_id: str,
    *,
    include_superseded: bool = False,
) -> tuple[str, PreferenceValue, str | None] | None:
    for scope, record in (
        ("hard", state.hard_constraints),
        ("soft", state.soft_constraints),
    ):
        value = record.get(canonical_id)
        if value is not None and (include_superseded or value.status != "superseded"):
            return f"{scope}_constraints.{canonical_id}", value, scope
    for facet in SPNFacetName.__args__:
        value = state.subjective_needs.get_facet(facet)
        if (
            value is not None
            and value.canonical_id == canonical_id
            and (include_superseded or value.status != "superseded")
        ):
            return f"subjective_needs.{facet}", value, None
    return None


def _target_existing(
    state: DialogueState, event: SEGSEStateEvent
) -> tuple[str, PreferenceValue, str | None] | None:
    if event.canonical_id in ACTUAL_PREFERENCE_IDS:
        return _locate(state, event.canonical_id, include_superseded=True)
    facet = _facet_for_id(event.canonical_id)
    value = state.subjective_needs.get_facet(facet)  # type: ignore[arg-type]
    if value is None:
        return None
    return f"subjective_needs.{facet}", value, None


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalized_hard_value(canonical_id: str, value_text: str) -> Any:
    text = _normalized_text(value_text)
    if canonical_id == "operating_system":
        for label in ("android", "ipados", "fire os", "windows", "chrome os"):
            if label in text:
                return label
        return text
    match = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", text)
    number = float(match.group().replace(",", "")) if match else None
    if canonical_id == "max_weight" and number is not None:
        if re.search(r"\b(?:lb|lbs|pound|pounds)\b", text):
            number *= 453.59237
        elif re.search(r"\b(?:kg|kilogram|kilograms)\b", text):
            number *= 1000
        elif re.search(r"\b(?:oz|ounce|ounces)\b", text):
            number *= 28.349523
        number = round(number)
    return number if number is not None else text


def _classify_event(
    previous: DialogueState,
    event: SEGSEStateEvent,
) -> tuple[bool, str | None, str | None]:
    """Return semantic materiality, operation, and an old scope path to remove."""

    existing = _target_existing(previous, event)
    if existing is None:
        return True, "add", None
    path, value, previous_scope = existing
    if value.status == "superseded":
        return True, "reactivate", None
    if event.scope_after != previous_scope:
        return True, "update_scope", path
    if event.act == "refine":
        changed = _normalized_text(event.value_after or "") != _normalized_text(
            value.value_text
        )
        return changed, "refine" if changed else None, None
    if event.scope_after == "hard":
        changed = _normalized_hard_value(
            event.canonical_id, event.value_after or ""
        ) != _normalized_hard_value(event.canonical_id, value.value_text)
        return changed, "update_value" if changed else None, None
    # C remains the defense for repeated qualitative IDs. A real qualitative
    # enrichment must be explicitly typed as REFINE and carry fresh evidence.
    changed = value.canonical_id != event.canonical_id
    return changed, "update_value" if changed else None, None


def _remove_path(state: DialogueState, path: str) -> None:
    if path.startswith("hard_constraints."):
        state.hard_constraints.pop(path.split(".", 1)[1], None)
    elif path.startswith("soft_constraints."):
        state.soft_constraints.pop(path.split(".", 1)[1], None)
    elif path.startswith("subjective_needs."):
        setattr(state.subjective_needs, path.split(".", 1)[1], None)


def _replace_at_path(state: DialogueState, path: str, value: PreferenceValue) -> None:
    if path.startswith("hard_constraints."):
        state.hard_constraints[path.split(".", 1)[1]] = value
    elif path.startswith("soft_constraints."):
        state.soft_constraints[path.split(".", 1)[1]] = value
    else:
        setattr(state.subjective_needs, path.split(".", 1)[1], value)


def _apply_confirmation(
    state: DialogueState,
    event: SEGSEStateEvent,
    *,
    turn_id: str,
) -> SEGSEMetadataDelta | None:
    located = _locate(state, event.canonical_id)
    if located is None:
        return None
    path, value, _ = located
    changes: list[str] = ["support_added"]
    eligibility_changed = value.status != "confirmed"
    if eligibility_changed:
        changes.append("status_confirmed")
    if value.origin != "explicit":
        changes.append("provenance_promoted")
    evidence = list(dict.fromkeys([*value.evidence_turn_ids, turn_id]))
    _replace_at_path(
        state,
        path,
        value.model_copy(
            update={
                "origin": "explicit",
                "confidence": max(value.confidence, event.confidence),
                "status": "confirmed",
                "evidence_turn_ids": evidence,
                "updated_at_turn_id": turn_id,
            }
        ),
    )
    return SEGSEMetadataDelta(
        canonical_id=event.canonical_id,
        changes=changes,  # type: ignore[arg-type]
        decision_eligibility_changed=eligibility_changed,
    )


def update_segse_dialogue_state(
    previous: DialogueState,
    understanding: SEGSETabletDomainUnderstandingOutput,
    previous_rankings: list[RankedProduct],
    *,
    turn_id: str,
) -> tuple[DialogueState, StateDiff]:
    """Derive state-relative mutation effects without reparsing the utterance."""

    prepared = previous.model_copy(deep=True)
    material_events: list[SEGSEStateEvent] = []
    operations: list[SEGSEAppliedOperation] = []
    metadata_events: list[SEGSEStateEvent] = []
    noop_ids: list[TabletDomainCanonicalId] = []
    extra_paths: list[str] = []

    for event in understanding.segse_authorized_events:
        if event.act in {"confirm", "retract"}:
            continue
        material, operation, old_path = _classify_event(previous, event)
        if material and operation is not None:
            if old_path is not None:
                _remove_path(prepared, old_path)
                extra_paths.append(old_path)
            material_events.append(event)
            candidate = _event_candidate(event, {})
            path = (
                f"{candidate.scope}_constraints.{candidate.canonical_id}"
                if candidate.scope is not None
                else f"subjective_needs.{_facet_for_id(candidate.canonical_id)}"
            )
            operations.append(
                SEGSEAppliedOperation(
                    canonical_id=event.canonical_id,
                    operation=operation,  # type: ignore[arg-type]
                    changed_paths=list(dict.fromkeys([old_path, path]))
                    if old_path
                    else [path],
                )
            )
        else:
            noop_ids.append(event.canonical_id)
            existing = _locate(previous, event.canonical_id)
            if (
                existing is not None
                and event.origin == "explicit"
                and (
                    existing[1].origin != "explicit"
                    or existing[1].status != "confirmed"
                )
            ):
                metadata_events.append(event)

    previous_summary = {
        event.canonical_id: {
            "scope": event.scope_after,
        }
        for event in material_events
    }
    candidates = [
        _event_candidate(event, previous_summary)
        for event in material_events
    ]
    merge_input = TabletDomainUnderstandingOutput(
        utterance=understanding.utterance,
        domain_route=understanding.domain_route,
        unsupported_category_text=understanding.unsupported_category_text,
        unsupported_category_evidence=understanding.unsupported_category_evidence,
        intents=understanding.intents,
        facets=_facets_for_candidates(candidates),
        candidates=candidates,
        item_action=understanding.item_action,
        tradeoff=understanding.tradeoff,
        supersedes=[],
        residual_color_choice=False,
    )
    state, diff = update_actual_dialogue_state(
        prepared,
        merge_input,
        previous_rankings,
        turn_id=turn_id,
    )

    metadata_deltas: list[SEGSEMetadataDelta] = []
    for event in [
        *metadata_events,
        *(
            item
            for item in understanding.segse_authorized_events
            if item.act == "confirm"
        ),
    ]:
        delta = _apply_confirmation(state, event, turn_id=turn_id)
        if delta is not None:
            metadata_deltas.append(delta)

    for event in understanding.segse_authorized_events:
        if event.act != "retract":
            continue
        located = _locate(state, event.canonical_id)
        if located is None:
            continue
        path, value, _ = located
        evidence = list(dict.fromkeys([*value.evidence_turn_ids, turn_id]))
        _replace_at_path(
            state,
            path,
            value.model_copy(
                update={
                    "status": "superseded",
                    "evidence_turn_ids": evidence,
                    "updated_at_turn_id": turn_id,
                }
            ),
        )
        extra_paths.append(path)
        operations.append(
            SEGSEAppliedOperation(
                canonical_id=event.canonical_id,
                operation="retract",
                changed_paths=[path],
            )
        )

    changed_paths = list(dict.fromkeys([*diff.changed_paths, *extra_paths]))
    if state.preference_history:
        state.preference_history[-1] = state.preference_history[-1].model_copy(
            update={"changed_paths": changed_paths}
        )
    understanding.segse_material_operations = operations
    understanding.segse_metadata_deltas = metadata_deltas
    understanding.segse_semantic_noop_ids = list(dict.fromkeys(noop_ids))
    return state, StateDiff(
        changed_paths=changed_paths,
        summary=[f"{path} updated" for path in changed_paths]
        if changed_paths
        else ["no semantic state change"],
    )


def build_segse_experiment_service(
    *,
    trace_filename: str,
    catalog: Any | None = None,
) -> Any:
    """Build the isolated one-call SEGSE development service."""

    from app.actual_service import ActualDemoService
    from app.actual_workflow import ActualCatalogWorkflow
    from app.config import load_llm_settings, load_review_retrieval_settings
    from app.experimental_catalog import (
        ExperimentalAmazonCatalog,
        ExperimentalCatalogUnavailableError,
    )
    from app.llm import build_client
    from app.nodes.actual_response import ActualTemplateResponseComposer
    from app.review_retrieval import build_review_retriever

    current_catalog = catalog or ExperimentalAmazonCatalog()
    if not current_catalog.available:
        raise ExperimentalCatalogUnavailableError(
            current_catalog.status().unavailable_reason
            or "The real tablet catalog is unavailable."
        )
    settings = load_llm_settings()
    if settings.provider == "luxia":
        settings.require_api_key()
    client = build_client(settings, trace=True, trace_filename=trace_filename)
    workflow = ActualCatalogWorkflow(
        catalog=current_catalog,
        understand=SEGSEUnderstandingProvider(client),
        response_composer=ActualTemplateResponseComposer(),
        review_retriever=build_review_retriever(load_review_retrieval_settings()),
        state_updater=update_segse_dialogue_state,  # type: ignore[arg-type]
    )
    return ActualDemoService(
        workflow,
        current_catalog,
        llm_provider=f"{settings.provider}:segse_lite_v1_dev",
        initial_state_factory=create_tablet_environment_state,
        experiment_condition="full",
        close_callback=client.aclose,
    )
