"""Recall-recovery and value-safety iteration over provider-compatible SEGSE v1.2."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from app.llm import LLMClient, system, user
from app.models.actual_demo import TabletDomainCanonicalId
from app.segse_experiment import (
    SEGSEEventRejection,
    SEGSEProposalOutput,
    SEGSEStateEvent,
    _event_candidate,
    _facets_for_candidates,
    authorize_segse_events,
    compact_segse_previous_state,
)
from app.segse_experiment_v11 import (
    SEGSEV11Normalization,
    _active_previous_ids,
    _sanitize_controls,
    _v11_post_authorize,
)
from app.segse_experiment_v12 import (
    SEGSEV12ProposalOutput,
    SEGSEV12UnderstandingOutput,
    _normalize_events,
    _raw_to_v1,
)


SEGSE_V13_PROMPT_VERSION = "segse-lite-tablet-domain-en-v1.3-dev"

_SOFT_ONLY_IDS = frozenset(
    {
        "budget_flexibility",
        "price_value",
        "portability",
        "note_taking",
        "performance",
        "battery",
        "audio",
        "durability",
        "review_signal",
        "child_friendly",
    }
)
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


class SEGSEV13UnderstandingOutput(SEGSEV12UnderstandingOutput):
    segse_v13_scope_normalizations: list[SEGSEV11Normalization]
    segse_v13_value_rejections: list[SEGSEEventRejection]


SEGSE_V13_SYSTEM_PROMPT = """You are the SPN Understanding (PLAN) node in a tablet-only shopping workflow.
Return only the strict JSON object described by the response schema.

Previous state is READ-ONLY reference memory. Output only state events expressed by the CURRENT user
utterance. Omit untouched state; absence means carryover. trigger_evidence_text must be an exact current
utterance substring. Never copy a previous fact merely because it is visible in memory.

Extract every independent explicit fact in a compound current utterance. A use case and a budget in the
same sentence are two events, not a choice between them. Use these facet mappings with scope_after=null:
- activity_reading: books, PDFs, sheet music, blueprints, journals, or other reading;
- activity_video: films, movie nights, recorded lectures, or video watching;
- activity_gaming: games or gaming;
- activity_note_taking: handwriting notes or annotating notes;
- activity_general: casual browsing and general mixed use;
- goal_work_study: explicit office, work, or study purpose.
Facet IDs never use hard or soft scope.

For every event choose one act:
- assert: new fact, replacement value, hard/soft scope change, or reactivation;
- confirm: explicit keep/affirmation of an ACTIVE prior fact;
- retract: removal of the entire ACTIVE prior fact. Softening is assert(scope_after=soft), not retract;
- refine: more specific qualitative meaning for an ACTIVE soft/facet fact only.

For assert/refine fill scope_after and value_after. For confirm/retract set both to null. Hard filter IDs
are budget, storage_capacity, memory_capacity, max_weight, min_rating, operating_system, and numeric
display. Battery, portability, performance, note_taking, audio, durability, review_signal, price_value,
budget_flexibility, and child_friendly always use scope_after=soft. A preference word such as matters,
priority, nice, or convenient is soft unless it names a supported numeric hard filter.

Value dimensions are strict:
- screen/display size in inches maps to display, never min_rating;
- star/rating values from 0 to 5 map to min_rating;
- built-in/internal GB or TB maps to storage_capacity;
- explicit RAM/memory GB maps to memory_capacity;
- microSD/removable expansion is not internal storage and emits no storage event;
- a numeric weight cap with a weight unit maps to max_weight;
- negative OS cannot be represented as a positive requirement, so emit no OS event for 'not Windows'.

Item actions and trade-offs are separate controls. An explicitly requested non-tablet category uses
domain_route=unsupported_category. Keep utterance exactly equal to the current utterance.
"""


def _normalize_soft_only_scopes(
    events: list[SEGSEStateEvent],
) -> tuple[list[SEGSEStateEvent], list[SEGSEV11Normalization]]:
    normalized: list[SEGSEStateEvent] = []
    audits: list[SEGSEV11Normalization] = []
    for index, event in enumerate(events):
        current = event
        if (
            event.act in {"assert", "refine"}
            and event.canonical_id in _SOFT_ONLY_IDS
            and event.scope_after == "hard"
        ):
            current = event.model_copy(
                update={"scope_after": "soft", "relation": "prefer"}
            )
            audits.append(
                SEGSEV11Normalization(
                    event_index=index,
                    canonical_id=event.canonical_id,
                    raw_act=event.act,
                    normalized_act=event.act,
                    reason="soft_only_id_hard_scope_normalized_to_soft",
                )
            )
        normalized.append(current)
    return normalized, audits


def _number(event: SEGSEStateEvent) -> float | None:
    match = _NUMBER.search(
        f"{event.value_after or ''} {event.trigger_evidence_text}"
    )
    return float(match.group().replace(",", "")) if match else None


def _hard_value_reason(event: SEGSEStateEvent) -> str | None:
    if event.act not in {"assert", "refine"} or event.scope_after != "hard":
        return None
    evidence = event.trigger_evidence_text.casefold()
    value = (event.value_after or "").casefold()
    combined = f"{value} {evidence}"
    number = _number(event)
    if event.canonical_id == "budget":
        if number is None or not re.search(
            r"\b(?:price|cost|spend|budget|ceiling|cap|total|maximum|under|below)\b|\$",
            combined,
        ):
            return "budget_requires_price_dimension"
    elif event.canonical_id == "storage_capacity":
        if number is None or not (
            re.search(r"\b(?:storage|built[ -]?in|internal|capacity)\b", combined)
            and re.search(r"\b(?:gb|tb|gigabyte|terabyte)s?\b", combined)
        ):
            return "storage_requires_internal_capacity_dimension"
    elif event.canonical_id == "memory_capacity":
        if number is None or not (
            re.search(r"\b(?:ram|memory)\b", combined)
            and re.search(r"\b(?:gb|gigabyte)s?\b", combined)
        ):
            return "memory_requires_ram_dimension"
    elif event.canonical_id == "max_weight":
        if number is None or not re.search(
            r"\b(?:gram|grams|g|kg|kilogram|kilograms|lb|lbs|pound|pounds|oz|ounce|ounces)\b",
            combined,
        ):
            return "max_weight_requires_weight_dimension"
    elif event.canonical_id == "min_rating":
        if (
            number is None
            or number < 0
            or number > 5
            or not re.search(r"\b(?:star|stars|rating|rated)\b", combined)
        ):
            return "min_rating_requires_zero_to_five_star_dimension"
    elif event.canonical_id == "display":
        if number is None or not re.search(
            r"\b(?:screen|display|inch|inches|in)\b", combined
        ):
            return "hard_display_requires_screen_size_dimension"
    elif event.canonical_id == "operating_system":
        if not any(
            label in combined
            for label in ("android", "ipados", "fire os", "windows", "chrome os")
        ):
            return "operating_system_requires_supported_label"
    return None


def _filter_hard_value_contracts(
    events: list[SEGSEStateEvent],
    prior_rejections: list[SEGSEEventRejection],
) -> tuple[list[SEGSEStateEvent], list[SEGSEEventRejection], list[SEGSEEventRejection]]:
    accepted: list[SEGSEStateEvent] = []
    value_rejections: list[SEGSEEventRejection] = []
    for index, event in enumerate(events):
        reason = _hard_value_reason(event)
        if reason is None:
            accepted.append(event)
        else:
            value_rejections.append(
                SEGSEEventRejection(
                    event_index=index,
                    canonical_id=event.canonical_id,
                    reason=reason,
                )
            )
    return accepted, [*prior_rejections, *value_rejections], value_rejections


class SEGSEV13UnderstandingProvider:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> SEGSEV13UnderstandingOutput:
        clean = utterance.strip()
        if not clean:
            raise ValueError("utterance cannot be empty")
        compact = compact_segse_previous_state(previous_state_summary)
        raw = await self.client.generate_structured(
            messages=[
                system(SEGSE_V13_SYSTEM_PROMPT),
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
            prompt_version=SEGSE_V13_PROMPT_VERSION,
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
        normalized, scope_normalizations = _normalize_soft_only_scopes(normalized)
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
        authorized, rejections, value_rejections = _filter_hard_value_contracts(
            authorized, rejections
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
        return SEGSEV13UnderstandingOutput(
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
            segse_v12_normalizations=[*normalizations, *scope_normalizations],
            segse_v12_control_violations=control_violations,
            segse_v13_scope_normalizations=scope_normalizations,
            segse_v13_value_rejections=value_rejections,
        )


def build_segse_v13_experiment_service(
    *,
    trace_filename: str,
    catalog: Any | None = None,
) -> Any:
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
        understand=SEGSEV13UnderstandingProvider(client),
        response_composer=ActualTemplateResponseComposer(),
        review_retriever=build_review_retriever(load_review_retrieval_settings()),
        state_updater=update_segse_dialogue_state,  # type: ignore[arg-type]
    )
    return ActualDemoService(
        workflow,
        current_catalog,
        llm_provider=f"{settings.provider}:segse_lite_v13_dev",
        initial_state_factory=create_tablet_environment_state,
        experiment_condition="full",
        close_callback=client.aclose,
    )


__all__ = [
    "SEGSE_V13_PROMPT_VERSION",
    "SEGSEV13UnderstandingOutput",
    "SEGSEV13UnderstandingProvider",
    "build_segse_v13_experiment_service",
]
