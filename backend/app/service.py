"""MVP workflow를 대화 세션 단위로 직렬화하는 애플리케이션 서비스."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from uuid import uuid4

from app.catalog import DemoCatalog, load_demo_catalog
from app.config import load_llm_settings
from app.llm import build_client
from app.models import ConversationSnapshot, DialogueState, PipelineTurn, RankedProduct
from app.nodes.response import LLMResponseComposer, TemplateResponseComposer
from app.nodes.state_manager import create_initial_dialogue_state
from app.workflow import (
    LLMUnderstandingProvider,
    MVPWorkflow,
    RegexUnderstandingProvider,
)


@dataclass
class ConversationRecord:
    conversation_id: str
    dialogue_state: DialogueState = field(default_factory=create_initial_dialogue_state)
    turns: list[PipelineTurn] = field(default_factory=list)
    rankings: list[RankedProduct] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class ConversationNotFoundError(KeyError):
    pass


class MVPService:
    def __init__(
        self,
        workflow: MVPWorkflow,
        catalog: DemoCatalog,
        *,
        close_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.workflow = workflow
        self.catalog = catalog
        self._close_callback = close_callback
        self._records: dict[str, ConversationRecord] = {}
        self._records_lock = asyncio.Lock()

    async def create_conversation(self) -> ConversationSnapshot:
        conversation_id = f"conversation-{uuid4().hex[:12]}"
        record = ConversationRecord(conversation_id=conversation_id)
        async with self._records_lock:
            self._records[conversation_id] = record
        return self._snapshot(record)

    async def _get_record(self, conversation_id: str) -> ConversationRecord:
        async with self._records_lock:
            record = self._records.get(conversation_id)
        if record is None:
            raise ConversationNotFoundError(conversation_id)
        return record

    async def run_turn(self, conversation_id: str, utterance: str) -> PipelineTurn:
        record = await self._get_record(conversation_id)
        clean_utterance = utterance.strip()
        if not clean_utterance:
            raise ValueError("utterance는 비어 있을 수 없습니다.")
        async with record.lock:
            turn = await self.workflow.run_turn(
                conversation_id=conversation_id,
                turn_number=len(record.turns) + 1,
                utterance=clean_utterance,
                dialogue_state=record.dialogue_state,
                previous_rankings=record.rankings,
            )
            record.dialogue_state = turn.dialogue_state
            record.rankings = turn.rankings or record.rankings
            record.turns.append(turn)
            return turn

    async def get_conversation(self, conversation_id: str) -> ConversationSnapshot:
        record = await self._get_record(conversation_id)
        async with record.lock:
            return self._snapshot(record)

    async def delete_conversation(self, conversation_id: str) -> None:
        async with self._records_lock:
            removed = self._records.pop(conversation_id, None)
        if removed is None:
            raise ConversationNotFoundError(conversation_id)

    @staticmethod
    def _snapshot(record: ConversationRecord) -> ConversationSnapshot:
        return ConversationSnapshot(
            conversation_id=record.conversation_id,
            dialogue_state=record.dialogue_state.model_copy(deep=True),
            turns=[turn.model_copy(deep=True) for turn in record.turns],
        )

    async def aclose(self) -> None:
        if self._close_callback:
            await self._close_callback()


def build_runtime_service() -> MVPService:
    settings = load_llm_settings()
    if settings.provider == "mock":
        return build_demo_test_service()
    if settings.provider == "luxia":
        settings.require_api_key()
    client = build_client(settings, trace=True, trace_filename="mvp_llm_trace.jsonl")
    catalog = load_demo_catalog()
    workflow = MVPWorkflow(
        catalog=catalog,
        understand=LLMUnderstandingProvider(client),
        response_composer=LLMResponseComposer(client),
    )
    return MVPService(workflow, catalog, close_callback=client.aclose)


def build_demo_test_service() -> MVPService:
    """API·6턴 회귀용. 실제 앱의 기본 provider는 build_runtime_service다."""
    catalog = load_demo_catalog()
    workflow = MVPWorkflow(
        catalog=catalog,
        understand=RegexUnderstandingProvider(),
        response_composer=TemplateResponseComposer(),
    )
    return MVPService(workflow, catalog)
