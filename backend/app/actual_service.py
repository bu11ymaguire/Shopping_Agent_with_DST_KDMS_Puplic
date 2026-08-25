"""In-memory sessions for the real-data English demo."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from uuid import uuid4

from app.actual_workflow import (
    ActualCatalogWorkflow,
    ActualLLMUnderstandingProvider,
    TabletDomainLLMUnderstandingProvider,
)
from app.config import load_llm_settings, load_review_retrieval_settings
from app.experimental_catalog import (
    ExperimentalAmazonCatalog,
    ExperimentalCatalogUnavailableError,
)
from app.experiment_conditions import (
    ExperimentCondition,
    condition_turn_inputs,
    rank_products_without_review_contribution,
)
from app.llm import build_client
from app.models import DialogueState, RankedProduct
from app.models.actual_demo import ActualConversationSnapshot, ActualPipelineTurn
from app.nodes.actual_response import ActualLLMResponseComposer
from app.nodes.actual_state_manager import create_tablet_environment_state
from app.nodes.state_manager import create_initial_dialogue_state
from app.review_retrieval import build_review_retriever
from app.service import ConversationNotFoundError


@dataclass
class ActualConversationRecord:
    conversation_id: str
    dialogue_state: DialogueState = field(default_factory=create_initial_dialogue_state)
    turns: list[ActualPipelineTurn] = field(default_factory=list)
    rankings: list[RankedProduct] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class ActualDemoService:
    def __init__(
        self,
        workflow: ActualCatalogWorkflow,
        catalog: ExperimentalAmazonCatalog,
        *,
        llm_provider: str,
        initial_state_factory: Callable[[], DialogueState] = create_initial_dialogue_state,
        experiment_condition: ExperimentCondition = "full",
        close_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.workflow = workflow
        self.catalog = catalog
        self.llm_provider = llm_provider
        self._initial_state_factory = initial_state_factory
        self.experiment_condition = experiment_condition
        self._close_callback = close_callback
        self._records: dict[str, ActualConversationRecord] = {}
        self._records_lock = asyncio.Lock()

    async def create_conversation(self) -> ActualConversationSnapshot:
        conversation_id = f"actual-{uuid4().hex[:12]}"
        record = ActualConversationRecord(
            conversation_id=conversation_id,
            dialogue_state=self._initial_state_factory(),
        )
        async with self._records_lock:
            self._records[conversation_id] = record
        return self._snapshot(record)

    async def _get_record(self, conversation_id: str) -> ActualConversationRecord:
        async with self._records_lock:
            record = self._records.get(conversation_id)
        if record is None:
            raise ConversationNotFoundError(conversation_id)
        return record

    async def run_turn(
        self, conversation_id: str, utterance: str
    ) -> ActualPipelineTurn:
        record = await self._get_record(conversation_id)
        clean = utterance.strip()
        if not clean:
            raise ValueError("utterance cannot be empty")
        async with record.lock:
            turn_state, turn_rankings = condition_turn_inputs(
                self.experiment_condition,
                record.dialogue_state,
                record.rankings,
            )
            turn = await self.workflow.run_turn(
                conversation_id=conversation_id,
                turn_number=len(record.turns) + 1,
                utterance=clean,
                dialogue_state=turn_state,
                previous_rankings=turn_rankings,
            )
            record.dialogue_state = turn.dialogue_state
            if turn.policy.lane == "recommend-lane":
                # An empty recommendation result is still the current visible result.
                # Keeping older cards here would let a later rank reference target a
                # product that is no longer on screen.
                record.rankings = turn.rankings
            record.turns.append(turn)
            return turn

    async def get_conversation(
        self, conversation_id: str
    ) -> ActualConversationSnapshot:
        record = await self._get_record(conversation_id)
        async with record.lock:
            return self._snapshot(record)

    async def delete_conversation(self, conversation_id: str) -> None:
        async with self._records_lock:
            removed = self._records.pop(conversation_id, None)
        if removed is None:
            raise ConversationNotFoundError(conversation_id)

    @staticmethod
    def _snapshot(record: ActualConversationRecord) -> ActualConversationSnapshot:
        return ActualConversationSnapshot(
            conversation_id=record.conversation_id,
            dialogue_state=record.dialogue_state.model_copy(deep=True),
            turns=[turn.model_copy(deep=True) for turn in record.turns],
        )

    async def aclose(self) -> None:
        if self._close_callback:
            await self._close_callback()


def build_actual_runtime_service(
    catalog: ExperimentalAmazonCatalog | None = None,
) -> ActualDemoService:
    current_catalog = catalog or ExperimentalAmazonCatalog()
    if not current_catalog.available:
        raise ExperimentalCatalogUnavailableError(
            current_catalog.status().unavailable_reason
            or "The real tablet catalog is unavailable."
        )
    settings = load_llm_settings()
    if settings.provider == "luxia":
        settings.require_api_key()
    client = build_client(
        settings,
        trace=True,
        trace_filename="actual_demo_llm_trace.jsonl",
    )
    workflow = ActualCatalogWorkflow(
        catalog=current_catalog,
        understand=ActualLLMUnderstandingProvider(client),
        response_composer=ActualLLMResponseComposer(client),
        review_retriever=build_review_retriever(load_review_retrieval_settings()),
    )
    return ActualDemoService(
        workflow,
        current_catalog,
        llm_provider=settings.provider,
        close_callback=client.aclose,
    )


def build_tablet_domain_v2_dev_service(
    catalog: ExperimentalAmazonCatalog | None = None,
) -> ActualDemoService:
    """Build an isolated v2 dev service without changing the default v1 UI path."""
    current_catalog = catalog or ExperimentalAmazonCatalog()
    if not current_catalog.available:
        raise ExperimentalCatalogUnavailableError(
            current_catalog.status().unavailable_reason
            or "The real tablet catalog is unavailable."
        )
    settings = load_llm_settings()
    if settings.provider == "luxia":
        settings.require_api_key()
    client = build_client(
        settings,
        trace=True,
        trace_filename="tablet_domain_v2_dev_llm_trace.jsonl",
    )
    workflow = ActualCatalogWorkflow(
        catalog=current_catalog,
        understand=TabletDomainLLMUnderstandingProvider(client),
        response_composer=ActualLLMResponseComposer(client),
        review_retriever=build_review_retriever(load_review_retrieval_settings()),
    )
    return ActualDemoService(
        workflow,
        current_catalog,
        llm_provider=settings.provider,
        initial_state_factory=create_tablet_environment_state,
        close_callback=client.aclose,
    )


def build_tablet_domain_v2_experiment_service(
    condition: ExperimentCondition,
    catalog: ExperimentalAmazonCatalog | None = None,
    *,
    trace_filename: str | None = None,
) -> ActualDemoService:
    """Build one frozen v2 evaluation condition with an explicit ablation label."""
    current_catalog = catalog or ExperimentalAmazonCatalog()
    if not current_catalog.available:
        raise ExperimentalCatalogUnavailableError(
            current_catalog.status().unavailable_reason
            or "The real tablet catalog is unavailable."
        )
    settings = load_llm_settings()
    if settings.provider == "luxia":
        settings.require_api_key()
    client = build_client(
        settings,
        trace=True,
        trace_filename=(
            trace_filename or f"tablet_domain_v2_{condition}_llm_trace.jsonl"
        ),
    )
    product_ranker = (
        rank_products_without_review_contribution
        if condition == "no_review"
        else None
    )
    workflow_kwargs = {
        "catalog": current_catalog,
        "understand": TabletDomainLLMUnderstandingProvider(client),
        "response_composer": ActualLLMResponseComposer(client),
        "review_retriever": build_review_retriever(
            load_review_retrieval_settings()
        ),
    }
    if product_ranker is not None:
        workflow_kwargs["product_ranker"] = product_ranker
    workflow = ActualCatalogWorkflow(**workflow_kwargs)  # type: ignore[arg-type]
    return ActualDemoService(
        workflow,
        current_catalog,
        llm_provider=settings.provider,
        initial_state_factory=create_tablet_environment_state,
        experiment_condition=condition,
        close_callback=client.aclose,
    )
