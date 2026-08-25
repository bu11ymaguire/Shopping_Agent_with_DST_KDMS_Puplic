"""Deterministic correction-recovery iteration over SEGSE v1.3.

v1.4 reuses the v1.3 system prompt and the v1.2 output schema without a single
character of change.  Only deterministic interpretation between the raw proposal
and the State Manager moves:

1. a facet event whose ``value_after`` is missing is repaired from its current
   evidence anchor when that anchor lexically touches the facet's canonical
   dimension, and facet scope/relation are always normalized to null;
2. hard value dimension validation may read the complete current utterance when
   the exact anchor omits the dimension word, provided no competing canonical
   dimension is present in the same utterance;
3. an explicit hard-to-soft relaxation that the model typed as RETRACT is
   re-typed as ``assert(scope_after="soft")`` when the same utterance also states
   a positive preference for the same canonical dimension and the closed
   vocabulary actually admits a soft scope for that ID.

Nothing here relaxes the v1.3 sparse/grounded defenses.  Previous state is still
read-only reference memory, every event still needs an exact current-utterance
anchor, and the deterministic semantic no-op suppression (C) inside the State
Manager is untouched.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from app.llm import LLMClient, system, user
from app.models.actual_demo import (
    ACTUAL_PREFERENCE_IDS,
    ActualContract,
    TabletDomainCanonicalId,
)
from app.segse_experiment import (
    _ALLOWED_SCOPES,
    SEGSEEventRejection,
    SEGSEProposalOutput,
    SEGSEStateEvent,
    _event_candidate,
    _facets_for_candidates,
    _previous_records,
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
    _normalize_events,
    _raw_to_v1,
)
from app.segse_experiment_v13 import (
    SEGSE_V13_SYSTEM_PROMPT,
    SEGSEV13UnderstandingOutput,
    _NUMBER,
    _normalize_soft_only_scopes,
)


SEGSE_V14_PROMPT_VERSION = "segse-lite-tablet-domain-en-v1.4-dev"

#: v1.4 sends the frozen v1.3 instruction text. Only deterministic code changed.
SEGSE_V14_SYSTEM_PROMPT = SEGSE_V13_SYSTEM_PROMPT

_OS_LABELS = ("android", "ipados", "fire os", "windows", "chrome os")

# ---------------------------------------------------------------------------
# Canonical dimension vocabulary
#
# These tables are versioned closed-vocabulary metadata, not fixture phrases.
# Each entry lists generic English terms that name the canonical dimension the
# ID represents.  They answer one question only: does the current utterance talk
# about this dimension at all?  They never decide the value, the scope, or the
# operation.
# ---------------------------------------------------------------------------

_FACET_DIMENSION_TERMS: dict[str, str] = {
    "event_device_failure": (
        r"broke|broken|break|cracked|crack|shattered|died|dead|dying"
        r"|stopped working|no longer works|failed|failing|damaged|damage"
        r"|unusable|malfunction\w*"
    ),
    "event_first_purchase": (
        r"first tablet|first device|first one|my first|first time|first-time"
        r"|never owned|never had|new to tablets|starting out"
    ),
    "event_replacement": (
        r"replace\w*|upgrad\w*|old one|old tablet|current one|existing one"
        r"|aging|ageing|outdated|hand-me-down|trade in|trade-in"
    ),
    "event_gift": (
        r"gift|gifts|gifting|present for|birthday|christmas|holiday"
        r"|anniversary|graduation|surprise for"
    ),
    "activity_note_taking": (
        r"note|notes|notetaking|note-taking|handwrit\w*|hand-writ\w*"
        r"|annotat\w*|stylus|pen|pencil|sketch\w*|draw\w*|writ\w*"
        r"|scribbl\w*|markup|mark up"
    ),
    "activity_gaming": r"game|games|gaming|gamer|play|playing|emulat\w*",
    "activity_video": (
        r"video|videos|movie|movies|film|films|cinema|watch|watching"
        r"|stream\w*|series|episode|episodes|netflix|youtube|lecture|lectures"
    ),
    "activity_reading": (
        r"read|reads|reading|reader|book|books|ebook|ebooks|e-book|pdf|pdfs"
        r"|magazine|magazines|comic|comics|manga|article|articles"
        r"|document|documents|blueprint|blueprints|journal|journals"
        r"|sheet music|score|scores|textbook|textbooks|novel|novels"
    ),
    "activity_general": (
        r"general|generally|casual|casually|browse|browsing|web|internet"
        r"|surf\w*|everyday|every day|day-to-day|basic|light use|mixed use"
        r"|email|e-mail|social media|messaging"
    ),
    "goal_replace_device": (
        r"replace\w*|upgrad\w*|swap|retire|retiring|move on from|old device"
    ),
    "goal_long_term_use": (
        r"long term|long-term|years|longevity|future proof|future-proof"
        r"|futureproof|last for|lasting|keep it for|keep using"
    ),
    "goal_work_study": (
        r"work|working|office|business|professional|job|career|client|clients"
        r"|report|reports|presentation|presentations|meeting|meetings"
        r"|study|studying|student|students|school|college|university"
        r"|class|classes|course|courses|lecture|lectures|homework"
        r"|assignment|assignments|research|exam|exams"
    ),
    "goal_entertainment": (
        r"entertain\w*|fun|leisure|relax\w*|hobby|hobbies|media"
        r"|movie|movies|music|game|games|watch|watching"
    ),
    "audience_self": r"myself|my own|for me|personal use|personally|just me",
    "audience_child": (
        r"child|children|kid|kids|son|daughter|toddler|teen|teens"
        r"|teenager|teenagers|grandchild|grandchildren|grandson"
        r"|granddaughter|baby|school-age|classroom"
    ),
    "audience_family": (
        r"family|families|wife|husband|spouse|partner|parent|parents"
        r"|mother|father|mom|mum|dad|household|shared|we all|everyone at home"
    ),
    "audience_other": (
        r"colleague|colleagues|coworker|coworkers|co-worker|co-workers"
        r"|friend|friends|employee|employees|team|staff|student|students"
        r"|patient|patients|client|clients"
    ),
}


@dataclass(frozen=True)
class _HardValueContract:
    """Per-ID hard value requirements split into anchor-local and utterance-wide."""

    reason: str
    #: Unit words that must sit next to the number, inside the anchor.
    unit_pattern: str | None
    #: v1.3 dimension pattern, still evaluated against the anchor first.
    anchor_dimension: str
    #: Narrower, unambiguous dimension pattern allowed anywhere in the utterance.
    utterance_dimension: str
    #: IDs whose utterance-level presence blocks the utterance-wide fallback.
    competing_ids: tuple[str, ...]
    numeric_range: tuple[float, float] | None = None


_HARD_VALUE_CONTRACTS: dict[str, _HardValueContract] = {
    "budget": _HardValueContract(
        reason="budget_requires_price_dimension",
        unit_pattern=None,
        anchor_dimension=(
            r"\b(?:price|cost|spend|budget|ceiling|cap|total|maximum|under|below)\b|\$"
        ),
        utterance_dimension=(
            r"\b(?:price|prices|cost|costs|spend|spending|budget|dollar|dollars)\b|\$"
        ),
        competing_ids=(),
    ),
    "storage_capacity": _HardValueContract(
        reason="storage_requires_internal_capacity_dimension",
        unit_pattern=r"\b(?:gb|tb|gigabyte|terabyte)s?\b",
        anchor_dimension=r"\b(?:storage|built[ -]?in|internal|capacity)\b",
        utterance_dimension=r"\b(?:storage|built[ -]?in|internal|capacity)\b",
        competing_ids=("memory_capacity",),
    ),
    "memory_capacity": _HardValueContract(
        reason="memory_requires_ram_dimension",
        unit_pattern=r"\b(?:gb|gigabyte)s?\b",
        anchor_dimension=r"\b(?:ram|memory)\b",
        utterance_dimension=r"\bram\b",
        competing_ids=("storage_capacity",),
    ),
    "max_weight": _HardValueContract(
        reason="max_weight_requires_weight_dimension",
        unit_pattern=None,
        anchor_dimension=(
            r"\b(?:gram|grams|g|kg|kilogram|kilograms|lb|lbs|pound|pounds"
            r"|oz|ounce|ounces)\b"
        ),
        utterance_dimension=(
            r"\b(?:gram|grams|kg|kilogram|kilograms|lb|lbs|pound|pounds"
            r"|oz|ounce|ounces)\b"
        ),
        competing_ids=(),
    ),
    "min_rating": _HardValueContract(
        reason="min_rating_requires_zero_to_five_star_dimension",
        unit_pattern=None,
        anchor_dimension=r"\b(?:star|stars|rating|rated)\b",
        utterance_dimension=r"\b(?:star|stars|rating|rated)\b",
        competing_ids=("display",),
        numeric_range=(0.0, 5.0),
    ),
    "display": _HardValueContract(
        reason="hard_display_requires_screen_size_dimension",
        unit_pattern=None,
        anchor_dimension=r"\b(?:screen|display|inch|inches|in)\b",
        utterance_dimension=r"\b(?:screen|display|inch|inches)\b",
        competing_ids=("min_rating",),
    ),
}

# A requirement is being downgraded, not deleted, when the user negates the
# obligation while keeping the dimension desirable in the same utterance.
_REQUIREMENT_RELAXATION = re.compile(
    r"\bno longer\s+(?:a\s+|an\s+)?(?:strict\s+|hard\s+|firm\s+|absolute\s+|real\s+)?"
    r"(?:requirement|required|require|mandatory|must|necessary|deal ?breaker)\b"
    r"|\b(?:does not|doesn't|do not|don't|did not|didn't)\s+have to\b"
    r"|\bis(?:n't|\s+not)\s+(?:a\s+|an\s+)?(?:strict\s+|hard\s+|firm\s+|absolute\s+)?"
    r"(?:requirement|required|mandatory|necessary|must|deal ?breaker)\b"
    r"|\bnot\s+(?:a\s+|an\s+)?(?:strict\s+|hard\s+|firm\s+|absolute\s+)?"
    r"(?:requirement|required|mandatory|necessary|must have)\b"
    r"|\bnot\s+set in stone\b"
    r"|\b(?:flexible|lenient)\s+(?:on|about)\b",
    re.IGNORECASE,
)
_POSITIVE_PREFERENCE = re.compile(
    r"\bwould be\s+(?:really\s+|very\s+|quite\s+)?"
    r"(?:nice|great|good|ideal|preferable|preferred|helpful|welcome|handy)\b"
    r"|\bnice to have\b"
    r"|\bwould\s+(?:still\s+)?(?:like|prefer|love|appreciate|enjoy)\b"
    r"|\bprefer(?:s|red|ably)?\b"
    r"|\bideally\b"
    r"|\bif possible\b"
    r"|\bstill\s+(?:want|like|prefer|care about)\b",
    re.IGNORECASE,
)
_CLAUSE_SPLIT = re.compile(
    r",\s*|;\s*|\.\s+|\s+but\s+|\s+though\s+|\s+although\s+|\s+however\s+|\s+while\s+",
    re.IGNORECASE,
)


class SEGSEV14DimensionEvidence(ActualContract):
    """Where the dimension word for an accepted hard value was found."""

    event_index: int = Field(ge=0)
    canonical_id: TabletDomainCanonicalId
    dimension_evidence_scope: Literal["anchor", "current_utterance"]


class SEGSEV14UnderstandingOutput(SEGSEV13UnderstandingOutput):
    segse_v14_facet_repairs: list[SEGSEV11Normalization] = Field(default_factory=list)
    segse_v14_scope_relaxations: list[SEGSEV11Normalization] = Field(
        default_factory=list
    )
    segse_v14_dimension_evidence: list[SEGSEV14DimensionEvidence] = Field(
        default_factory=list
    )


def _clean_value_text(text: str) -> str:
    return " ".join(text.split()).strip(" .;,:!?-")


def _touches_facet_dimension(canonical_id: str, text: str) -> bool:
    """A facet ID needs its own dimension vocabulary in the given text."""

    pattern = _FACET_DIMENSION_TERMS.get(canonical_id)
    if pattern is None:
        return False
    return bool(re.search(rf"\b(?:{pattern})\b", text, re.IGNORECASE))


def repair_facet_events(
    events: list[SEGSEStateEvent],
) -> tuple[list[SEGSEStateEvent], list[SEGSEV11Normalization]]:
    """Give facet events a legal null scope and a current-evidence value.

    A facet ID never carries hard/soft scope, so a non-null scope is always a
    contract error and is cleared.  A missing value is only synthesized when the
    evidence anchor itself names the facet's canonical dimension; otherwise the
    event is left incomplete so the frozen authorization contract rejects it.
    """

    repaired: list[SEGSEStateEvent] = []
    audits: list[SEGSEV11Normalization] = []
    for index, event in enumerate(events):
        current = event
        if (
            event.act not in {"assert", "refine"}
            or event.canonical_id in ACTUAL_PREFERENCE_IDS
        ):
            repaired.append(current)
            continue
        if current.scope_after is not None or current.relation is not None:
            current = current.model_copy(
                update={"scope_after": None, "relation": None}
            )
            audits.append(
                SEGSEV11Normalization(
                    event_index=index,
                    canonical_id=event.canonical_id,
                    raw_act=event.act,
                    normalized_act=current.act,
                    reason="facet_scope_and_relation_cleared",
                )
            )
        if not (current.value_after or "").strip():
            anchor = current.trigger_evidence_text
            if _touches_facet_dimension(current.canonical_id, anchor):
                current = current.model_copy(
                    update={"value_after": _clean_value_text(anchor)}
                )
                audits.append(
                    SEGSEV11Normalization(
                        event_index=index,
                        canonical_id=event.canonical_id,
                        raw_act=event.act,
                        normalized_act=current.act,
                        reason="facet_value_derived_from_current_dimension_evidence",
                    )
                )
            else:
                audits.append(
                    SEGSEV11Normalization(
                        event_index=index,
                        canonical_id=event.canonical_id,
                        raw_act=event.act,
                        normalized_act=current.act,
                        reason="facet_value_absent_without_current_dimension_evidence",
                    )
                )
        repaired.append(current)
    return repaired, audits


def _relaxation_segment(canonical_id: str, current_utterance: str) -> str | None:
    """Find a clause that keeps the dimension desirable in the same utterance."""

    if not _REQUIREMENT_RELAXATION.search(current_utterance):
        return None
    contract = _HARD_VALUE_CONTRACTS.get(canonical_id)
    if contract is None:
        return None
    for raw_segment in _CLAUSE_SPLIT.split(current_utterance):
        segment = raw_segment.strip()
        if not segment or segment not in current_utterance:
            continue
        if _REQUIREMENT_RELAXATION.search(segment):
            continue
        if not _POSITIVE_PREFERENCE.search(segment):
            continue
        if re.search(contract.anchor_dimension, segment, re.IGNORECASE):
            return segment
    return None


def repair_scope_relaxations(
    events: list[SEGSEStateEvent],
    *,
    current_utterance: str,
    previous_state_summary: Mapping[str, Any],
) -> tuple[list[SEGSEStateEvent], list[SEGSEV11Normalization]]:
    """Re-type an explicit hard-to-soft downgrade that arrived as RETRACT.

    A retraction deletes the fact.  A relaxation keeps the same canonical fact and
    only lowers its decision authority.  The conversion needs all of: an active
    hard prior fact, an obligation-negating phrase, a positive-preference clause
    naming the same canonical dimension, and a closed vocabulary that admits a
    soft scope for that ID.  Hard-only IDs stay retractions because the vocabulary
    cannot express a soft version of the same dimension.
    """

    previous = _previous_records(previous_state_summary)
    converted: list[SEGSEStateEvent] = []
    audits: list[SEGSEV11Normalization] = []
    for index, event in enumerate(events):
        prior = previous.get(event.canonical_id)
        if (
            event.act != "retract"
            or prior is None
            or prior.get("status") == "superseded"
            or prior.get("scope") != "hard"
            or "soft" not in _ALLOWED_SCOPES.get(event.canonical_id, frozenset())
        ):
            converted.append(event)
            continue
        segment = _relaxation_segment(event.canonical_id, current_utterance)
        if segment is None:
            converted.append(event)
            continue
        converted.append(
            event.model_copy(
                update={
                    "act": "assert",
                    "scope_after": "soft",
                    "relation": "prefer",
                    "value_after": _clean_value_text(segment),
                    "trigger_evidence_text": segment,
                    "value_source": "current_utterance",
                    "source_ref": None,
                }
            )
        )
        audits.append(
            SEGSEV11Normalization(
                event_index=index,
                canonical_id=event.canonical_id,
                raw_act="retract",
                normalized_act="assert",
                reason="explicit_hard_to_soft_relaxation_retyped_as_scope_correction",
            )
        )
    return converted, audits


def _number(event: SEGSEStateEvent) -> float | None:
    match = _NUMBER.search(f"{event.value_after or ''} {event.trigger_evidence_text}")
    return float(match.group().replace(",", "")) if match else None


def dimension_evidence_scope(
    canonical_id: str, *, anchor: str, current_utterance: str
) -> Literal["anchor", "current_utterance"] | None:
    """Locate the canonical dimension word for a hard value.

    The exact anchor is preferred.  The complete current utterance is a legal
    fallback because a correction such as an explicit new minimum often puts the
    dimension word outside the numeric span the model anchored on.  The fallback
    is refused when a competing canonical dimension shares the utterance, so an
    ambiguous compound cannot silently reassign a value to the wrong ID.
    """

    contract = _HARD_VALUE_CONTRACTS.get(canonical_id)
    if contract is None:
        return None
    if re.search(contract.anchor_dimension, anchor, re.IGNORECASE):
        return "anchor"
    if not re.search(contract.utterance_dimension, current_utterance, re.IGNORECASE):
        return None
    for competitor in contract.competing_ids:
        rival = _HARD_VALUE_CONTRACTS[competitor].utterance_dimension
        if re.search(rival, current_utterance, re.IGNORECASE):
            return None
    return "current_utterance"


def hard_value_decision(
    event: SEGSEStateEvent, *, current_utterance: str
) -> tuple[str | None, Literal["anchor", "current_utterance"] | None]:
    """Return a rejection reason, or the dimension-evidence scope that accepted it."""

    if event.act not in {"assert", "refine"} or event.scope_after != "hard":
        return None, None
    anchor = f"{event.value_after or ''} {event.trigger_evidence_text}"
    if event.canonical_id == "operating_system":
        combined = anchor.casefold()
        if not any(label in combined for label in _OS_LABELS):
            return "operating_system_requires_supported_label", None
        return None, "anchor"
    contract = _HARD_VALUE_CONTRACTS.get(event.canonical_id)
    if contract is None:
        return None, None
    number = _number(event)
    if number is None:
        return contract.reason, None
    if contract.numeric_range is not None and not (
        contract.numeric_range[0] <= number <= contract.numeric_range[1]
    ):
        return contract.reason, None
    if contract.unit_pattern is not None and not re.search(
        contract.unit_pattern, anchor, re.IGNORECASE
    ):
        return contract.reason, None
    scope = dimension_evidence_scope(
        event.canonical_id, anchor=anchor, current_utterance=current_utterance
    )
    if scope is None:
        return contract.reason, None
    return None, scope


def filter_hard_value_contracts(
    events: list[SEGSEStateEvent],
    prior_rejections: list[SEGSEEventRejection],
    *,
    current_utterance: str,
) -> tuple[
    list[SEGSEStateEvent],
    list[SEGSEEventRejection],
    list[SEGSEEventRejection],
    list[SEGSEV14DimensionEvidence],
]:
    accepted: list[SEGSEStateEvent] = []
    value_rejections: list[SEGSEEventRejection] = []
    dimension_evidence: list[SEGSEV14DimensionEvidence] = []
    for index, event in enumerate(events):
        reason, scope = hard_value_decision(
            event, current_utterance=current_utterance
        )
        if reason is None:
            accepted.append(event)
            if scope is not None:
                dimension_evidence.append(
                    SEGSEV14DimensionEvidence(
                        event_index=index,
                        canonical_id=event.canonical_id,
                        dimension_evidence_scope=scope,
                    )
                )
        else:
            value_rejections.append(
                SEGSEEventRejection(
                    event_index=index,
                    canonical_id=event.canonical_id,
                    reason=reason,
                )
            )
    return (
        accepted,
        [*prior_rejections, *value_rejections],
        value_rejections,
        dimension_evidence,
    )


class SEGSEV14UnderstandingProvider:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> SEGSEV14UnderstandingOutput:
        clean = utterance.strip()
        if not clean:
            raise ValueError("utterance cannot be empty")
        compact = compact_segse_previous_state(previous_state_summary)
        raw = await self.client.generate_structured(
            messages=[
                system(SEGSE_V14_SYSTEM_PROMPT),
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
            prompt_version=SEGSE_V14_PROMPT_VERSION,
            conversation_id=conversation_id,
            turn=turn,
        )
        controls, control_violations = _sanitize_controls(raw, clean_utterance=clean)
        raw_events = [_raw_to_v1(event) for event in raw.state_events]
        normalized, normalizations = _normalize_events(
            raw_events, _active_previous_ids(previous_state_summary)
        )
        normalized, scope_normalizations = _normalize_soft_only_scopes(normalized)
        normalized, facet_repairs = repair_facet_events(normalized)
        normalized, scope_relaxations = repair_scope_relaxations(
            normalized,
            current_utterance=clean,
            previous_state_summary=previous_state_summary,
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
        (
            authorized,
            rejections,
            value_rejections,
            dimension_evidence,
        ) = filter_hard_value_contracts(
            authorized, rejections, current_utterance=clean
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
        return SEGSEV14UnderstandingOutput(
            utterance=clean,
            domain_route=controls["domain_route"],
            unsupported_category_text=controls["unsupported_category_text"],
            unsupported_category_evidence=controls["unsupported_category_evidence"],
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
            segse_v14_facet_repairs=facet_repairs,
            segse_v14_scope_relaxations=scope_relaxations,
            segse_v14_dimension_evidence=dimension_evidence,
        )


def build_segse_v14_experiment_service(
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
        understand=SEGSEV14UnderstandingProvider(client),
        response_composer=ActualTemplateResponseComposer(),
        review_retriever=build_review_retriever(load_review_retrieval_settings()),
        state_updater=update_segse_dialogue_state,  # type: ignore[arg-type]
    )
    return ActualDemoService(
        workflow,
        current_catalog,
        llm_provider=f"{settings.provider}:segse_lite_v14_dev",
        initial_state_factory=create_tablet_environment_state,
        experiment_condition="full",
        close_callback=client.aclose,
    )


__all__ = [
    "SEGSE_V14_PROMPT_VERSION",
    "SEGSE_V14_SYSTEM_PROMPT",
    "SEGSEV14DimensionEvidence",
    "SEGSEV14UnderstandingOutput",
    "SEGSEV14UnderstandingProvider",
    "build_segse_v14_experiment_service",
    "dimension_evidence_scope",
    "filter_hard_value_contracts",
    "hard_value_decision",
    "repair_facet_events",
    "repair_scope_relaxations",
]
