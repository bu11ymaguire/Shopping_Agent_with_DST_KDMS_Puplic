"""Provider-compatible flat-schema SEGSE-lite v1.2 development adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar, Literal

from pydantic import Field

from app.llm import LLMClient, system, user
from app.models.actual_demo import ActualContract, TabletDomainCanonicalId
from app.models.understanding import EvidenceOrigin, IntentName
from app.segse_experiment import (
    SEGSEProposalOutput,
    SEGSEStateEvent,
    SEGSETabletDomainUnderstandingOutput,
    _event_candidate,
    _facets_for_candidates,
    authorize_segse_events,
    compact_segse_previous_state,
)
from app.segse_experiment_v11 import (
    SEGSEV11ItemActionProposal,
    SEGSEV11Normalization,
    SEGSEV11TradeoffProposal,
    _active_previous_ids,
    _sanitize_controls,
    _v11_post_authorize,
)


SEGSE_V12_PROMPT_VERSION = "segse-lite-tablet-domain-en-v1.2-dev"


class SEGSEV12Event(ActualContract):
    """Flat event shape because the provider rejects oneOf in array items."""

    canonical_id: TabletDomainCanonicalId
    act: Literal["assert", "confirm", "retract", "refine"]
    scope_after: Literal["hard", "soft"] | None = None
    value_after: str | None = None
    trigger_evidence_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    confidence: float = Field(ge=0, le=1)


class SEGSEV12ProposalOutput(ActualContract):
    schema_version: ClassVar[str] = "segse-proposal-output-v1.2"

    utterance: str = Field(min_length=1)
    domain_route: Literal["in_domain", "unsupported_category"]
    unsupported_category_text: str | None = None
    unsupported_category_evidence: str | None = None
    intents: list[IntentName] = Field(min_length=1)
    state_events: list[SEGSEV12Event]
    item_action: SEGSEV11ItemActionProposal | None = None
    tradeoff: SEGSEV11TradeoffProposal | None = None


class SEGSEV12UnderstandingOutput(SEGSETabletDomainUnderstandingOutput):
    segse_v12_raw_proposal_events: list[SEGSEV12Event]
    segse_v12_normalized_events: list[SEGSEStateEvent]
    segse_v12_normalizations: list[SEGSEV11Normalization] = Field(default_factory=list)
    segse_v12_control_violations: list[str] = Field(default_factory=list)


SEGSE_V12_SYSTEM_PROMPT = """You are the SPN Understanding (PLAN) node in a tablet-only shopping workflow.
Return only the strict JSON object described by the response schema.

Previous state is READ-ONLY reference memory. Output only state events expressed by the CURRENT user
utterance. Omit untouched state; absence means carryover. trigger_evidence_text must be an exact current
utterance substring. Never copy a previous fact merely because it is visible in memory.

For every event choose one act:
- assert: new fact, replacement value, hard/soft scope change, or reactivation.
- confirm: explicit keep/affirmation of an ACTIVE prior fact.
- retract: removal of the entire ACTIVE prior fact. Softening is assert(scope_after=soft), not retract.
- refine: more specific qualitative meaning for an ACTIVE soft/facet fact only.

This provider requires one flat event object. For assert/refine fill scope_after and value_after. For
confirm/retract set scope_after=null and value_after=null. The application derives value source,
source reference, and relation; they are not output fields.

Hard preference IDs are budget, storage_capacity, memory_capacity, max_weight, min_rating,
operating_system, and quantitative display. Soft IDs include portability, battery, performance,
qualitative display, and other listed preferences. Facet IDs use scope_after=null. microSD/removable
expansion is not internal storage. Qualitative 'too heavy' is portability; max_weight requires a number.
Negative OS cannot be represented as a positive requirement, so emit no OS event for 'not Windows'.
When an active prior minimum exists, 'no minimum anymore' is retract.

Item actions and trade-offs are separate controls. compare/remove/guess words are not ranked-item actions
unless the user clearly refers to visible ranked options. 'too heavy for my commute' may ground
portability; simple dislike must not invent a product attribute.

Use domain_route=unsupported_category only for an explicit non-tablet request. Keep utterance exactly
equal to the current utterance.
"""


def _raw_to_v1(event: SEGSEV12Event) -> SEGSEStateEvent:
    if event.act in {"confirm", "retract"}:
        value_source = "prior_state_reference"
        source_ref: TabletDomainCanonicalId | None = event.canonical_id
        relation = None
    else:
        value_source = "current_utterance"
        source_ref = event.canonical_id if event.act == "refine" else None
        relation = (
            "require"
            if event.scope_after == "hard"
            else "prefer"
            if event.scope_after == "soft"
            else None
        )
    return SEGSEStateEvent(
        canonical_id=event.canonical_id,
        act=event.act,
        scope_after=event.scope_after,
        value_after=event.value_after,
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
        if event.act in {"confirm", "retract"} and (
            event.scope_after is not None or event.value_after is not None
        ):
            current = event.model_copy(
                update={"scope_after": None, "value_after": None, "relation": None}
            )
            audits.append(
                SEGSEV11Normalization(
                    event_index=index,
                    canonical_id=event.canonical_id,
                    raw_act=event.act,
                    normalized_act=event.act,
                    reason=f"{event.act}_state_payload_cleared",
                )
            )
        if current.act == "refine" and current.canonical_id not in active_ids:
            current = current.model_copy(
                update={
                    "act": "assert",
                    "value_source": "current_utterance",
                    "source_ref": None,
                }
            )
            audits.append(
                SEGSEV11Normalization(
                    event_index=index,
                    canonical_id=current.canonical_id,
                    raw_act="refine",
                    normalized_act="assert",
                    reason="refine_without_active_prior_normalized_to_assert",
                )
            )
        normalized.append(current)
    return normalized, audits


class SEGSEV12UnderstandingProvider:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> SEGSEV12UnderstandingOutput:
        clean = utterance.strip()
        if not clean:
            raise ValueError("utterance cannot be empty")
        compact = compact_segse_previous_state(previous_state_summary)
        raw = await self.client.generate_structured(
            messages=[
                system(SEGSE_V12_SYSTEM_PROMPT),
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
            response_model=SEGSEV12ProposalOutput,
            temperature=0.0,
            node="spn-understanding",
            prompt_version=SEGSE_V12_PROMPT_VERSION,
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
        strict_proposal = SEGSEProposalOutput(**controls, state_events=normalized)
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
        return SEGSEV12UnderstandingOutput(
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
            segse_v12_raw_proposal_events=raw.state_events,
            segse_v12_normalized_events=normalized,
            segse_v12_normalizations=normalizations,
            segse_v12_control_violations=control_violations,
        )


def build_segse_v12_experiment_service(
    *,
    trace_filename: str,
    catalog: Any | None = None,
) -> Any:
    """Build the one-call v1.2 arm inside the complete catalog workflow."""

    from app.actual_service import ActualDemoService
    from app.actual_workflow import ActualCatalogWorkflow
    from app.config import load_llm_settings, load_review_retrieval_settings
    from app.experimental_catalog import (
        ExperimentalAmazonCatalog,
        ExperimentalCatalogUnavailableError,
    )
    from app.llm import build_client
    from app.nodes.actual_response import ActualTemplateResponseComposer
    from app.nodes.actual_state_manager import create_tablet_environment_state
    from app.review_retrieval import build_review_retriever
    from app.segse_experiment import update_segse_dialogue_state

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
        understand=SEGSEV12UnderstandingProvider(client),
        response_composer=ActualTemplateResponseComposer(),
        review_retriever=build_review_retriever(load_review_retrieval_settings()),
        state_updater=update_segse_dialogue_state,  # type: ignore[arg-type]
    )
    return ActualDemoService(
        workflow,
        current_catalog,
        llm_provider=f"{settings.provider}:segse_lite_v12_dev",
        initial_state_factory=create_tablet_environment_state,
        experiment_condition="full",
        close_callback=client.aclose,
    )


__all__ = [
    "SEGSE_V12_PROMPT_VERSION",
    "SEGSEV12Event",
    "SEGSEV12ProposalOutput",
    "SEGSEV12UnderstandingOutput",
    "SEGSEV12UnderstandingProvider",
    "build_segse_v12_experiment_service",
]
