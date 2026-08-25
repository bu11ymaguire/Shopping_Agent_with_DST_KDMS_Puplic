"""Single-agent explicit LangGraph workflow for the end-to-end MVP."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.baselines import regex_understand_utterance
from app.catalog import DemoCatalog
from app.llm import LLMClient
from app.models import (
    BrowseResult,
    DialogueState,
    FinalResponse,
    NodeTrace,
    PipelineTurn,
    PolicyDecision,
    RankedProduct,
    RecommendationQuery,
    RecommendationResponse,
    StateDiff,
    UnderstandingOutput,
)
from app.nodes.policy import select_policy
from app.nodes.recommendation import (
    browse_catalog,
    create_recommendation_response,
    generate_query,
    rank_products,
)
from app.nodes.response import (
    ResponseComposer,
    build_clarify_final_response,
    build_recommend_final_response,
)
from app.nodes.state_manager import update_dialogue_state
from app.nodes.understanding import understand_utterance


class UnderstandingProvider(Protocol):
    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> UnderstandingOutput: ...


class LLMUnderstandingProvider:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> UnderstandingOutput:
        return await understand_utterance(
            self.client,
            utterance=utterance,
            previous_state_summary=previous_state_summary,
            conversation_id=conversation_id,
            turn=turn,
        )


class RegexUnderstandingProvider:
    """키 없는 회귀 검증용 provider. 런타임 기본 경로가 아니다."""

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> UnderstandingOutput:
        del conversation_id, turn
        return regex_understand_utterance(utterance, previous_state_summary)


class WorkflowState(TypedDict, total=False):
    conversation_id: str
    turn_number: int
    turn_id: str
    utterance: str
    dialogue_state: DialogueState
    previous_rankings: list[RankedProduct]
    understanding: UnderstandingOutput
    state_diff: StateDiff
    policy: PolicyDecision
    query: RecommendationQuery
    browse_result: BrowseResult
    recommendation: RecommendationResponse
    final_response: FinalResponse
    rankings: list[RankedProduct]
    trace: list[NodeTrace]


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _append_trace(
    state: WorkflowState,
    *,
    node_id: str,
    role: str,
    owner: str,
    latency_ms: float,
    output_summary: dict[str, Any],
    lane: str | None = None,
) -> list[NodeTrace]:
    trace = list(state.get("trace", []))
    trace.append(
        NodeTrace(
            node_id=node_id,
            role=role,
            owner=owner,
            lane=lane,
            latency_ms=latency_ms,
            output_summary=output_summary,
        )
    )
    return trace


def _state_summary(state: WorkflowState) -> dict[str, Any]:
    dialogue = state["dialogue_state"]
    previous_rankings = state.get("previous_rankings", [])
    return {
        "category": dialogue.category.model_dump(mode="json")
        if dialogue.category
        else None,
        "hard_constraints": {
            key: value.model_dump(mode="json")
            for key, value in dialogue.hard_constraints.items()
        },
        "soft_constraints": {
            key: value.model_dump(mode="json")
            for key, value in dialogue.soft_constraints.items()
        },
        "current_item": dialogue.current_item,
        "ranking_context": {
            "first": next(
                (item.product_id for item in previous_rankings if item.rank == 1),
                None,
            ),
            "second": next(
                (item.product_id for item in previous_rankings if item.rank == 2),
                None,
            ),
        },
    }


class MVPWorkflow:
    def __init__(
        self,
        *,
        catalog: DemoCatalog,
        understand: UnderstandingProvider,
        response_composer: ResponseComposer,
    ) -> None:
        self.catalog = catalog
        self.understand = understand
        self.response_composer = response_composer
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("spn-understanding", self._understanding_node)
        graph.add_node("ra-state-manager", self._state_manager_node)
        graph.add_node("spn-policy", self._policy_node)
        graph.add_node("spn-response-clarify", self._clarify_node)
        graph.add_node("ra-query-generator", self._query_node)
        graph.add_node("spn-browsing-actions", self._browse_node)
        graph.add_node("ra-recommendation-engine", self._rank_node)
        graph.add_node("spn-response-recommend", self._recommend_node)

        graph.add_edge(START, "spn-understanding")
        graph.add_edge("spn-understanding", "ra-state-manager")
        graph.add_edge("ra-state-manager", "spn-policy")
        graph.add_conditional_edges(
            "spn-policy",
            lambda state: state["policy"].lane,
            {
                "clarify-lane": "spn-response-clarify",
                "recommend-lane": "ra-query-generator",
            },
        )
        graph.add_edge("spn-response-clarify", END)
        graph.add_edge("ra-query-generator", "spn-browsing-actions")
        graph.add_edge("spn-browsing-actions", "ra-recommendation-engine")
        graph.add_edge("ra-recommendation-engine", "spn-response-recommend")
        graph.add_edge("spn-response-recommend", END)
        return graph.compile()

    async def _understanding_node(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        understanding = await self.understand(
            utterance=state["utterance"],
            previous_state_summary=_state_summary(state),
            conversation_id=state["conversation_id"],
            turn=state["turn_number"],
        )
        return {
            "understanding": understanding,
            "trace": _append_trace(
                state,
                node_id="spn-understanding",
                role="PLAN",
                owner="SPN",
                latency_ms=_elapsed_ms(started),
                output_summary={
                    "intents": understanding.intents,
                    "candidate_ids": [
                        candidate.canonical_id
                        for candidate in understanding.candidates
                    ],
                    "item_action": understanding.item_action.name
                    if understanding.item_action
                    else None,
                },
            ),
        }

    def _state_manager_node(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        dialogue, diff = update_dialogue_state(
            state["dialogue_state"],
            state["understanding"],
            state.get("previous_rankings", []),
            turn_id=state["turn_id"],
            products=list(self.catalog.products),
        )
        return {
            "dialogue_state": dialogue,
            "state_diff": diff,
            "trace": _append_trace(
                state,
                node_id="ra-state-manager",
                role="MEMORY",
                owner="RA-Rec",
                latency_ms=_elapsed_ms(started),
                output_summary={"changed_paths": diff.changed_paths},
            ),
        }

    def _policy_node(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        policy = select_policy(state["dialogue_state"])
        return {
            "policy": policy,
            "trace": _append_trace(
                state,
                node_id="spn-policy",
                role="DECIDE",
                owner="SPN",
                lane=policy.lane,
                latency_ms=_elapsed_ms(started),
                output_summary={
                    "lane": policy.lane,
                    "vagueness": policy.snapshot.vagueness.total,
                    "question_target": policy.question_target.field
                    if policy.question_target
                    else None,
                },
            ),
        }

    async def _clarify_node(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        result = await self.response_composer.clarify(
            state["policy"],
            state["dialogue_state"],
            conversation_id=state["conversation_id"],
            turn=state["turn_number"],
        )
        final_response = build_clarify_final_response(result)
        return {
            "final_response": final_response,
            "rankings": [],
            "trace": _append_trace(
                state,
                node_id="spn-response-clarify",
                role="RESPOND",
                owner="SPN",
                lane="clarify-lane",
                latency_ms=_elapsed_ms(started),
                output_summary={
                    "composer": result.source,
                    "fallback_error": result.fallback_error,
                },
            ),
        }

    def _query_node(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        query = generate_query(state["dialogue_state"])
        return {
            "query": query,
            "trace": _append_trace(
                state,
                node_id="ra-query-generator",
                role="QUERY",
                owner="RA-Rec",
                lane="recommend-lane",
                latency_ms=_elapsed_ms(started),
                output_summary={
                    "query": query.text,
                    "unmapped_preference_ids": query.unmapped_preference_ids,
                },
            ),
        }

    def _browse_node(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        result = browse_catalog(state["query"], self.catalog)
        return {
            "browse_result": result,
            "trace": _append_trace(
                state,
                node_id="spn-browsing-actions",
                role="ACT",
                owner="SPN",
                lane="recommend-lane",
                latency_ms=_elapsed_ms(started),
                output_summary={
                    "product_count": len(result.products),
                    "review_count": len(result.reviews),
                    "inventory_source": "controlled_inventory",
                },
            ),
        }

    def _rank_node(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        rankings, reviews, unmapped = rank_products(
            state["dialogue_state"], state["browse_result"]
        )
        recommendation = create_recommendation_response(
            state["dialogue_state"], rankings, reviews, unmapped
        )
        return {
            "rankings": rankings,
            "recommendation": recommendation,
            "trace": _append_trace(
                state,
                node_id="ra-recommendation-engine",
                role="RANK",
                owner="RA-Rec",
                lane="recommend-lane",
                latency_ms=_elapsed_ms(started),
                output_summary={
                    "top_products": [
                        {
                            "product_id": item.product_id,
                            "score": item.score.total,
                        }
                        for item in rankings[:3]
                    ],
                    "unmapped_preference_ids": unmapped,
                },
            ),
        }

    async def _recommend_node(self, state: WorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        result = await self.response_composer.recommend(
            state["recommendation"],
            state["browse_result"],
            state["understanding"],
            list(self.catalog.products),
            conversation_id=state["conversation_id"],
            turn=state["turn_number"],
        )
        final_response = build_recommend_final_response(
            result, state["recommendation"], state["browse_result"]
        )
        return {
            "final_response": final_response,
            "trace": _append_trace(
                state,
                node_id="spn-response-recommend",
                role="RESPOND",
                owner="SPN",
                lane="recommend-lane",
                latency_ms=_elapsed_ms(started),
                output_summary={
                    "composer": result.source,
                    "fallback_error": result.fallback_error,
                    "card_count": len(final_response.product_cards),
                },
            ),
        }

    async def run_turn(
        self,
        *,
        conversation_id: str,
        turn_number: int,
        utterance: str,
        dialogue_state: DialogueState,
        previous_rankings: list[RankedProduct],
    ) -> PipelineTurn:
        turn_id = f"turn-{turn_number}"
        result = await self.graph.ainvoke(
            {
                "conversation_id": conversation_id,
                "turn_number": turn_number,
                "turn_id": turn_id,
                "utterance": utterance,
                "dialogue_state": dialogue_state,
                "previous_rankings": previous_rankings,
                "trace": [],
            }
        )
        return PipelineTurn(
            conversation_id=conversation_id,
            turn_id=turn_id,
            utterance=utterance,
            understanding=result["understanding"],
            dialogue_state=result["dialogue_state"],
            state_diff=result["state_diff"],
            policy=result["policy"],
            query=result.get("query"),
            browse_result=result.get("browse_result"),
            recommendation=result.get("recommendation"),
            final_response=result["final_response"],
            rankings=result.get("rankings", []),
            trace=result["trace"],
        )
