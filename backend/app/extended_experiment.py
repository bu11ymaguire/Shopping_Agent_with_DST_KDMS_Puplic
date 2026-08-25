"""Post-hoc Full-Memory false-update strategy comparison.

The official frozen pipeline remains the control.  This module supplies isolated
Understanding contracts and deterministic merge policies for the Extended_Experiment
branch.  It deliberately does not change the production/default service builders.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, ClassVar, Literal, TypeAlias

from pydantic import Field, model_validator

from app.llm import LLMClient, system, user
from app.models import DialogueState, RankedProduct, StateDiff
from app.models.actual_demo import (
    ACTUAL_FACET_IDS,
    ACTUAL_PREFERENCE_IDS,
    ActualContract,
    ActualItemActionCandidate,
    ActualPreferenceId,
    ActualTradeoffCandidate,
    TabletDomainCanonicalId,
    TabletDomainFacetCandidates,
    TabletDomainStateUpdateCandidate,
    TabletDomainUnderstandingOutput,
)
from app.models.understanding import EvidenceOrigin, IntentName, SPNFacetName
from app.nodes.actual_state_manager import update_actual_dialogue_state
from app.nodes.tablet_domain_understanding import (
    TABLET_DOMAIN_SYSTEM_PROMPT,
    understand_tablet_domain_utterance,
)


ExtendedStrategy: TypeAlias = Literal[
    "m0_baseline",
    "a_som_operation",
    "b_sparse_delta",
    "c_semantic_noop",
    "d_full_state_diff",
    "e_compact_context",
]

EXTENDED_STRATEGY_ORDER: tuple[ExtendedStrategy, ...] = (
    "m0_baseline",
    "a_som_operation",
    "b_sparse_delta",
    "c_semantic_noop",
    "d_full_state_diff",
    "e_compact_context",
)

EXTENDED_PROMPT_VERSIONS: dict[ExtendedStrategy, str] = {
    "m0_baseline": "spn-understanding-tablet-domain-en-v2.3-frozen",
    "a_som_operation": "extended-a-som-operation-v1",
    "b_sparse_delta": "extended-b-sparse-delta-v1",
    "c_semantic_noop": "spn-understanding-tablet-domain-en-v2.3-frozen",
    "d_full_state_diff": "extended-d-full-state-diff-v1",
    "e_compact_context": "extended-e-compact-context-v1",
}


class ExtendedControlOutput(ActualContract):
    """Control/action fields shared by the alternative state representations."""

    utterance: str = Field(min_length=1)
    domain_route: Literal["in_domain", "unsupported_category"]
    unsupported_category_text: str | None = None
    unsupported_category_evidence: str | None = None
    intents: list[IntentName] = Field(min_length=1)
    item_action: ActualItemActionCandidate | None = None
    tradeoff: ActualTradeoffCandidate | None = None
    supersedes: list[ActualPreferenceId]
    residual_color_choice: Literal[False] = False

    @model_validator(mode="after")
    def routing_contract(self) -> ExtendedControlOutput:
        if len(self.intents) != len(set(self.intents)):
            raise ValueError("intents must not contain duplicates")
        if len(self.supersedes) != len(set(self.supersedes)):
            raise ValueError("supersedes must not contain duplicates")
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
            or self.supersedes
        ):
            raise ValueError("unsupported-category output must not mutate tablet state")
        return self


class ExtendedStateOperation(ActualContract):
    canonical_id: TabletDomainCanonicalId
    operation: Literal["carryover", "upsert", "delete"]
    scope: Literal["hard", "soft"] | None = None
    value_text: str | None = None
    evidence_text: str | None = None
    origin: EvidenceOrigin | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def operation_payload(self) -> ExtendedStateOperation:
        payload = (
            self.value_text,
            self.evidence_text,
            self.origin,
            self.confidence,
        )
        if self.operation == "upsert":
            if any(value is None for value in payload):
                raise ValueError("upsert requires value, evidence, origin, and confidence")
            is_preference = self.canonical_id in ACTUAL_PREFERENCE_IDS
            if is_preference != (self.scope is not None):
                raise ValueError("preference upserts require scope; facet upserts require null")
        elif any(value is not None for value in (*payload, self.scope)):
            raise ValueError("carryover/delete operations must not include update payload")
        return self

    def to_candidate(self) -> TabletDomainStateUpdateCandidate:
        if self.operation != "upsert":
            raise ValueError("only upsert operations convert to candidates")
        return TabletDomainStateUpdateCandidate(
            canonical_id=self.canonical_id,
            scope=self.scope,
            value_text=self.value_text or "invalid",
            evidence_text=self.evidence_text or "invalid",
            origin=self.origin or "explicit",
            confidence=self.confidence if self.confidence is not None else 0,
        )


class ExtendedStateSnapshotValue(ActualContract):
    canonical_id: TabletDomainCanonicalId
    scope: Literal["hard", "soft"] | None = None
    value_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def scope_matches_id(self) -> ExtendedStateSnapshotValue:
        is_preference = self.canonical_id in ACTUAL_PREFERENCE_IDS
        if is_preference != (self.scope is not None):
            raise ValueError("preference values require scope; facet values require null")
        return self

    def to_candidate(self, evidence_text: str) -> TabletDomainStateUpdateCandidate:
        return TabletDomainStateUpdateCandidate(
            canonical_id=self.canonical_id,
            scope=self.scope,
            value_text=self.value_text,
            evidence_text=evidence_text,
            origin=self.origin,
            confidence=self.confidence,
        )


class SOMOperationOutput(ExtendedControlOutput):
    schema_version: ClassVar[str] = "extended-som-operation-output-v1"
    slot_operations: list[ExtendedStateOperation]

    @model_validator(mode="after")
    def unique_slots_and_route(self) -> SOMOperationOutput:
        ids = [item.canonical_id for item in self.slot_operations]
        if len(ids) != len(set(ids)):
            raise ValueError("slot_operations must contain each canonical ID at most once")
        if self.domain_route == "unsupported_category" and self.slot_operations:
            raise ValueError("unsupported-category output requires no slot operations")
        return self


class SparseDeltaOutput(ExtendedControlOutput):
    schema_version: ClassVar[str] = "extended-sparse-delta-output-v1"
    state_operations: list[ExtendedStateOperation]

    @model_validator(mode="after")
    def sparse_operations_only(self) -> SparseDeltaOutput:
        if any(item.operation == "carryover" for item in self.state_operations):
            raise ValueError("sparse delta must not emit carryover operations")
        ids = [item.canonical_id for item in self.state_operations]
        if len(ids) != len(set(ids)):
            raise ValueError("state_operations must contain each canonical ID at most once")
        if self.domain_route == "unsupported_category" and self.state_operations:
            raise ValueError("unsupported-category output requires no state operations")
        return self


class FullStateOutput(ExtendedControlOutput):
    schema_version: ClassVar[str] = "extended-full-state-output-v1"
    full_state: list[ExtendedStateSnapshotValue]

    @model_validator(mode="after")
    def unique_state(self) -> FullStateOutput:
        ids = [item.canonical_id for item in self.full_state]
        if len(ids) != len(set(ids)):
            raise ValueError("full_state must contain each canonical ID at most once")
        if self.domain_route == "unsupported_category" and self.full_state:
            raise ValueError("unsupported-category output requires an empty full_state")
        return self


class ExtendedTabletDomainUnderstandingOutput(TabletDomainUnderstandingOutput):
    """Internal adapter output consumed by the unchanged downstream workflow."""

    experimental_strategy: ExtendedStrategy
    experimental_delete_ids: list[TabletDomainCanonicalId] = Field(default_factory=list)
    experimental_carryover_ids: list[TabletDomainCanonicalId] = Field(
        default_factory=list
    )
    experimental_contract_violations: list[str] = Field(default_factory=list)
    experimental_full_state: list[ExtendedStateSnapshotValue] = Field(
        default_factory=list
    )


_ARM_DIRECTIVES: dict[ExtendedStrategy, str] = {
    "a_som_operation": """
Experimental arm A (SOM-DST-inspired per-slot operation gate) overrides only the
state-output shape above. Return slot_operations. For every active prior canonical ID
except category_tablet, emit exactly one carryover, upsert, or delete operation. Use
carryover when the current utterance does not change that slot; use upsert only with
direct current-utterance evidence; use delete only for an explicit removal. Also emit
upserts for genuinely new current slots. Never attach value/evidence fields to
carryover or delete. This arm intentionally makes KEEP versus UPDATE explicit.
""",
    "b_sparse_delta": """
Experimental arm B (IC-DST/Diable-inspired sparse change generation) overrides only
the state-output shape above. Return state_operations containing only current-turn
upserts or explicit deletes. Never emit carryover and never restate an already-known
slot unless the current utterance changes its value or scope. Every upsert needs an
exact evidence span from the current utterance.
""",
    "d_full_state_diff": """
Experimental arm D (full-state generation baseline) overrides only the state-output
shape above. Return full_state: the complete active tablet preference/facet snapshot
after applying the current utterance, excluding immutable category_tablet. Preserve
unchanged prior values, replace corrected values, add new values, and omit a value
only when the user explicitly removes it. The application, not you, computes the diff.
For unsupported-category turns return an empty full_state because routing is evaluated
without mutating the remembered tablet state.
""",
    "e_compact_context": """
Experimental arm E uses the ordinary candidates output. The prior-state input has
been compacted: hard values are retained for correction resolution, while qualitative
state is represented by IDs only. Emit only updates evidenced by the current utterance;
already_known_ids are a suppression list, not suggestions to repeat.
""",
}


def compact_previous_state_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Remove provenance-heavy value dumps while retaining correction/rank context."""

    hard = []
    for value in summary.get("hard_constraints", {}).values():
        hard.append(
            {
                "canonical_id": value.get("canonical_id"),
                "value_text": value.get("value_text"),
                "status": value.get("status"),
            }
        )
    soft_ids = [
        value.get("canonical_id")
        for value in summary.get("soft_constraints", {}).values()
        if value.get("status") != "superseded"
    ]
    facet_ids = [
        value.get("canonical_id")
        for value in summary.get("subjective_needs", {}).values()
        if isinstance(value, dict) and value.get("status") != "superseded"
    ]
    return {
        "environment_category_id": "category_tablet",
        "hard_constraints_for_correction": hard,
        "already_known_ids": list(dict.fromkeys([*soft_ids, *facet_ids])),
        "current_item_rank_context": summary.get("current_item_rank_context"),
        "visible_ranked_products": summary.get("visible_ranked_products", []),
    }


def _previous_state_records(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for scope, field in (("hard", "hard_constraints"), ("soft", "soft_constraints")):
        for value in summary.get(field, {}).values():
            if value.get("status") == "superseded":
                continue
            records[value["canonical_id"]] = {**value, "scope": scope}
    subjective = summary.get("subjective_needs", {})
    for value in subjective.values():
        if not isinstance(value, dict) or value.get("status") == "superseded":
            continue
        records[value["canonical_id"]] = {**value, "scope": None}
    return records


def _normalized_value(canonical_id: str, scope: str | None, value_text: str) -> Any:
    text = " ".join(value_text.casefold().split())
    if scope != "hard":
        return (canonical_id, scope)
    if canonical_id == "operating_system":
        for label in ("android", "ipados", "fire os", "windows", "chrome os"):
            if label in text:
                return (canonical_id, scope, label)
        return (canonical_id, scope, text)
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
    return (canonical_id, scope, number if number is not None else text)


def _record_signature(record: Mapping[str, Any]) -> tuple[Any, ...]:
    status = record.get("status") or (
        "unconfirmed" if record.get("origin") == "inferred" else "confirmed"
    )
    return (
        _normalized_value(
            str(record["canonical_id"]),
            record.get("scope"),
            str(record["value_text"]),
        ),
        status,
    )


def _snapshot_signature(value: ExtendedStateSnapshotValue) -> tuple[Any, ...]:
    return (
        _normalized_value(value.canonical_id, value.scope, value.value_text),
        "unconfirmed" if value.origin == "inferred" else "confirmed",
    )


def _facets_for_candidates(
    candidates: list[TabletDomainStateUpdateCandidate],
) -> TabletDomainFacetCandidates:
    values: dict[str, TabletDomainStateUpdateCandidate] = {}
    for candidate in candidates:
        if candidate.canonical_id in ACTUAL_PREFERENCE_IDS:
            continue
        facet = next(
            name
            for name, ids in ACTUAL_FACET_IDS.items()
            if candidate.canonical_id in ids
        )
        values.setdefault(facet, candidate)
    return TabletDomainFacetCandidates(**values)


def _adapt(
    control: ExtendedControlOutput,
    *,
    strategy: ExtendedStrategy,
    candidates: list[TabletDomainStateUpdateCandidate],
    delete_ids: list[TabletDomainCanonicalId] | None = None,
    carryover_ids: list[TabletDomainCanonicalId] | None = None,
    violations: list[str] | None = None,
    full_state: list[ExtendedStateSnapshotValue] | None = None,
) -> ExtendedTabletDomainUnderstandingOutput:
    return ExtendedTabletDomainUnderstandingOutput(
        utterance=control.utterance,
        domain_route=control.domain_route,
        unsupported_category_text=control.unsupported_category_text,
        unsupported_category_evidence=control.unsupported_category_evidence,
        intents=control.intents,
        facets=_facets_for_candidates(candidates),
        candidates=candidates,
        item_action=control.item_action,
        tradeoff=control.tradeoff,
        supersedes=control.supersedes,
        residual_color_choice=False,
        experimental_strategy=strategy,
        experimental_delete_ids=delete_ids or [],
        experimental_carryover_ids=carryover_ids or [],
        experimental_contract_violations=violations or [],
        experimental_full_state=full_state or [],
    )


def _adapt_standard(
    output: TabletDomainUnderstandingOutput,
    strategy: ExtendedStrategy,
) -> ExtendedTabletDomainUnderstandingOutput:
    return ExtendedTabletDomainUnderstandingOutput(
        **output.model_dump(mode="python"),
        experimental_strategy=strategy,
    )


def _context(utterance: str, summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "environment": {
            "domain": "tablet_shopping",
            "category": "tablet",
            "unsupported_categories_must_not_be_recommended": True,
        },
        "current_utterance_to_extract": utterance,
        "reference_only_previous_state": dict(summary),
    }


class ExtendedUnderstandingProvider:
    """Strategy-specific LLM adapter with one Understanding call per turn."""

    def __init__(self, client: LLMClient, strategy: ExtendedStrategy) -> None:
        self.client = client
        self.strategy = strategy

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> ExtendedTabletDomainUnderstandingOutput:
        if self.strategy in {"m0_baseline", "c_semantic_noop"}:
            standard = await understand_tablet_domain_utterance(
                self.client,
                utterance=utterance,
                previous_state_summary=previous_state_summary,
                conversation_id=conversation_id,
                turn=turn,
            )
            return _adapt_standard(standard, self.strategy)

        summary = (
            compact_previous_state_summary(previous_state_summary)
            if self.strategy == "e_compact_context"
            else dict(previous_state_summary)
        )
        prompt = TABLET_DOMAIN_SYSTEM_PROMPT + "\n\n" + _ARM_DIRECTIVES[self.strategy]
        response_model: type[ActualContract]
        if self.strategy == "a_som_operation":
            response_model = SOMOperationOutput
        elif self.strategy == "b_sparse_delta":
            response_model = SparseDeltaOutput
        elif self.strategy == "d_full_state_diff":
            response_model = FullStateOutput
        else:
            response_model = TabletDomainUnderstandingOutput

        output = await self.client.generate_structured(
            messages=[
                system(prompt),
                user(
                    json.dumps(
                        _context(utterance, summary),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                ),
            ],
            response_model=response_model,
            temperature=0.0,
            node="spn-understanding",
            prompt_version=EXTENDED_PROMPT_VERSIONS[self.strategy],
            conversation_id=conversation_id,
            turn=turn,
        )
        if isinstance(output, TabletDomainUnderstandingOutput):
            return _adapt_standard(output, self.strategy)

        previous = _previous_state_records(previous_state_summary)
        if isinstance(output, SOMOperationOutput):
            upserts = [
                item.to_candidate()
                for item in output.slot_operations
                if item.operation == "upsert"
            ]
            deletes = [
                item.canonical_id
                for item in output.slot_operations
                if item.operation == "delete"
            ]
            carryovers = [
                item.canonical_id
                for item in output.slot_operations
                if item.operation == "carryover"
            ]
            represented = {item.canonical_id for item in output.slot_operations}
            missing = (
                []
                if output.domain_route == "unsupported_category"
                else sorted(set(previous) - represented)
            )
            violations = [f"missing_prior_operation:{item}" for item in missing]
            return _adapt(
                output,
                strategy=self.strategy,
                candidates=upserts,
                delete_ids=deletes,
                carryover_ids=carryovers,
                violations=violations,
            )
        if isinstance(output, SparseDeltaOutput):
            return _adapt(
                output,
                strategy=self.strategy,
                candidates=[
                    item.to_candidate()
                    for item in output.state_operations
                    if item.operation == "upsert"
                ],
                delete_ids=[
                    item.canonical_id
                    for item in output.state_operations
                    if item.operation == "delete"
                ],
            )

        assert isinstance(output, FullStateOutput)
        if output.domain_route == "unsupported_category":
            return _adapt(
                output,
                strategy=self.strategy,
                candidates=[],
                carryover_ids=sorted(previous),
            )
        snapshot = {item.canonical_id: item for item in output.full_state}
        changed = [
            item.to_candidate(utterance)
            for item in output.full_state
            if item.canonical_id not in previous
            or _snapshot_signature(item) != _record_signature(previous[item.canonical_id])
        ]
        deletes = sorted(set(previous) - set(snapshot))
        return _adapt(
            output,
            strategy=self.strategy,
            candidates=changed,
            delete_ids=deletes,
            carryover_ids=sorted(set(previous) & set(snapshot)),
            full_state=output.full_state,
        )


def _existing_record(state: DialogueState, canonical_id: str) -> tuple[str, Any] | None:
    for scope, record in (
        ("hard", state.hard_constraints),
        ("soft", state.soft_constraints),
    ):
        if canonical_id in record:
            return scope, record[canonical_id]
    for facet in SPNFacetName.__args__:
        value = state.subjective_needs.get_facet(facet)
        if value is not None and value.canonical_id == canonical_id:
            return facet, value
    return None


def _candidate_is_material(
    state: DialogueState, candidate: TabletDomainStateUpdateCandidate
) -> bool:
    existing = _existing_record(state, candidate.canonical_id)
    if existing is None:
        return True
    location, value = existing
    scope = candidate.scope if candidate.scope is not None else None
    existing_scope = location if location in {"hard", "soft"} else None
    if scope != existing_scope:
        return True
    candidate_record = {
        "canonical_id": candidate.canonical_id,
        "scope": scope,
        "value_text": candidate.value_text,
        "origin": candidate.origin,
    }
    existing_record = {
        "canonical_id": value.canonical_id,
        "scope": existing_scope,
        "value_text": value.value_text,
        "origin": value.origin,
        "status": value.status,
    }
    return _record_signature(candidate_record) != _record_signature(existing_record)


def _delete_state_id(state: DialogueState, canonical_id: str) -> str | None:
    if canonical_id in state.hard_constraints:
        del state.hard_constraints[canonical_id]
        return f"hard_constraints.{canonical_id}"
    if canonical_id in state.soft_constraints:
        del state.soft_constraints[canonical_id]
        return f"soft_constraints.{canonical_id}"
    for facet in SPNFacetName.__args__:
        value = state.subjective_needs.get_facet(facet)
        if value is not None and value.canonical_id == canonical_id:
            setattr(state.subjective_needs, facet, None)
            return f"subjective_needs.{facet}"
    return None


def update_extended_dialogue_state(
    previous: DialogueState,
    understanding: TabletDomainUnderstandingOutput,
    previous_rankings: list[RankedProduct],
    *,
    turn_id: str,
) -> tuple[DialogueState, StateDiff]:
    """Apply the selected experimental update without reparsing the utterance."""

    strategy = getattr(understanding, "experimental_strategy", "m0_baseline")
    merge_input = understanding
    if strategy == "c_semantic_noop":
        material = [
            candidate
            for candidate in understanding.candidates
            if _candidate_is_material(previous, candidate)
        ]
        # model_copy intentionally avoids re-running the facet validator, which would
        # reinsert a filtered echo from the facets convenience object.
        merge_input = understanding.model_copy(update={"candidates": material})

    state, diff = update_actual_dialogue_state(
        previous,
        merge_input,
        previous_rankings,
        turn_id=turn_id,
    )
    delete_ids = list(getattr(understanding, "experimental_delete_ids", []))
    deleted_paths = [
        path
        for canonical_id in delete_ids
        if (path := _delete_state_id(state, canonical_id)) is not None
    ]
    if not deleted_paths:
        return state, diff
    changed_paths = list(dict.fromkeys([*diff.changed_paths, *deleted_paths]))
    if state.preference_history:
        state.preference_history[-1] = state.preference_history[-1].model_copy(
            update={"changed_paths": changed_paths}
        )
    return state, StateDiff(
        changed_paths=changed_paths,
        summary=[f"{path} updated" for path in changed_paths],
    )


def build_extended_experiment_service(
    strategy: ExtendedStrategy,
    *,
    trace_filename: str,
    catalog: Any | None = None,
) -> Any:
    """Build an isolated one-LLM-call-per-turn comparison service.

    Response composition is deliberately deterministic so that the experiment changes
    only Understanding/state update behavior and does not pay for an irrelevant second
    LLM call. Retrieval and ranking remain the real frozen implementations.
    """

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
        understand=ExtendedUnderstandingProvider(client, strategy),
        response_composer=ActualTemplateResponseComposer(),
        review_retriever=build_review_retriever(load_review_retrieval_settings()),
        state_updater=update_extended_dialogue_state,
    )
    return ActualDemoService(
        workflow,
        current_catalog,
        llm_provider=f"{settings.provider}:{strategy}",
        initial_state_factory=create_tablet_environment_state,
        experiment_condition="full",
        close_callback=client.aclose,
    )
