"""Single-agent explicit LangGraph workflow over the real tablet catalog."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.experimental_catalog import ExperimentalAmazonCatalog
from app.llm import LLMClient
from app.models import DialogueState, NodeTrace, PolicyDecision, RankedProduct, StateDiff
from app.models.actual_demo import (
    ActualBrowseSummary,
    ActualFinalResponse,
    ActualPipelineTurn,
    ActualRecommendationQuery,
    ActualRecommendationResponse,
    ActualUnderstandingOutput,
    TabletDomainUnderstandingOutput,
)
from app.models.experimental import ExperimentalProduct, ExperimentalReview
from app.nodes.actual_policy import select_actual_policy
from app.nodes.actual_recommendation import (
    browse_actual_catalog,
    create_actual_recommendation_response,
    generate_actual_query,
    rank_actual_products,
)
from app.nodes.actual_response import (
    ActualResponseComposer,
    build_actual_clarify_response,
    build_actual_recommend_response,
)
from app.nodes.actual_state_manager import update_actual_dialogue_state
from app.nodes.actual_understanding import understand_actual_utterance
from app.nodes.tablet_domain_understanding import understand_tablet_domain_utterance
from app.review_retrieval import ReviewEvidenceRetriever, TokenReviewRetriever

ActualProductRanker = Callable[
    [
        DialogueState,
        ActualRecommendationQuery,
        list[ExperimentalProduct],
        list[ExperimentalReview],
    ],
    tuple[list[RankedProduct], list[Any]],
]


class ActualUnderstandingProvider(Protocol):
    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> ActualUnderstandingOutput | TabletDomainUnderstandingOutput: ...


class ActualLLMUnderstandingProvider:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> ActualUnderstandingOutput:
        return await understand_actual_utterance(
            self.client,
            utterance=utterance,
            previous_state_summary=previous_state_summary,
            conversation_id=conversation_id,
            turn=turn,
        )


class TabletDomainLLMUnderstandingProvider:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> TabletDomainUnderstandingOutput:
        return await understand_tablet_domain_utterance(
            self.client,
            utterance=utterance,
            previous_state_summary=previous_state_summary,
            conversation_id=conversation_id,
            turn=turn,
        )


class ActualWorkflowState(TypedDict, total=False):
    conversation_id: str
    turn_number: int
    turn_id: str
    utterance: str
    dialogue_state: DialogueState
    previous_rankings: list[RankedProduct]
    understanding: ActualUnderstandingOutput | TabletDomainUnderstandingOutput
    state_diff: StateDiff
    policy: PolicyDecision
    query: ActualRecommendationQuery
    candidate_products: list[ExperimentalProduct]
    candidate_reviews: list[ExperimentalReview]
    browse_result: ActualBrowseSummary
    recommendation: ActualRecommendationResponse
    final_response: ActualFinalResponse
    rankings: list[RankedProduct]
    trace: list[NodeTrace]


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


def _append_trace(
    state: ActualWorkflowState,
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
            owner=owner,  # type: ignore[arg-type]
            lane=lane,  # type: ignore[arg-type]
            latency_ms=latency_ms,
            output_summary=output_summary,
        )
    )
    return trace


class ActualCatalogWorkflow:
    def __init__(
        self,
        *,
        catalog: ExperimentalAmazonCatalog,
        understand: ActualUnderstandingProvider,
        response_composer: ActualResponseComposer,
        review_retriever: ReviewEvidenceRetriever | None = None,
        product_ranker: ActualProductRanker = rank_actual_products,
    ) -> None:
        self.catalog = catalog
        self.understand = understand
        self.response_composer = response_composer
        self.review_retriever = review_retriever or TokenReviewRetriever()
        self.product_ranker = product_ranker
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(ActualWorkflowState)
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

    def _previous_state_summary(self, state: ActualWorkflowState) -> dict[str, Any]:
        dialogue = state["dialogue_state"]
        ranked_context = []
        for ranking in state.get("previous_rankings", [])[:3]:
            try:
                product = self.catalog.get_product(ranking.product_id)
            except Exception:
                title = None
            else:
                title = product.title
            ranked_context.append(
                {
                    "rank": ranking.rank,
                    "product_reference_only": title,
                    "score": ranking.score.total,
                }
            )
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
            "subjective_needs": dialogue.subjective_needs.model_dump(mode="json"),
            "current_item_rank_context": dialogue.current_item,
            "visible_ranked_products": ranked_context,
        }

    async def _understanding_node(
        self, state: ActualWorkflowState
    ) -> dict[str, Any]:
        started = time.perf_counter()
        understanding = await self.understand(
            utterance=state["utterance"],
            previous_state_summary=self._previous_state_summary(state),
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
                        candidate.canonical_id for candidate in understanding.candidates
                    ],
                    "item_action": understanding.item_action.name
                    if understanding.item_action
                    else None,
                },
            ),
        }

    def _state_manager_node(self, state: ActualWorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        dialogue, diff = update_actual_dialogue_state(
            state["dialogue_state"],
            state["understanding"],
            state.get("previous_rankings", []),
            turn_id=state["turn_id"],
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

    def _policy_node(self, state: ActualWorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        policy = select_actual_policy(state["dialogue_state"])
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

    async def _clarify_node(self, state: ActualWorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        result = await self.response_composer.clarify(
            state["policy"],
            state["dialogue_state"],
            conversation_id=state["conversation_id"],
            turn=state["turn_number"],
        )
        final = build_actual_clarify_response(result)
        return {
            "final_response": final,
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

    def _query_node(self, state: ActualWorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        query = generate_actual_query(state["dialogue_state"])
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
                    "hard_filters": query.hard_filters.model_dump(mode="json"),
                    "unmapped_preference_ids": query.unmapped_preference_ids,
                },
            ),
        }

    def _browse_node(self, state: ActualWorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        rejected = {
            item.product_id for item in state["dialogue_state"].rejected_items
        }
        products, reviews, summary = browse_actual_catalog(
            state["query"],
            self.catalog,
            rejected_product_ids=rejected,
            review_retriever=self.review_retriever,
        )
        return {
            "candidate_products": products,
            "candidate_reviews": reviews,
            "browse_result": summary,
            "trace": _append_trace(
                state,
                node_id="spn-browsing-actions",
                role="ACT",
                owner="SPN",
                lane="recommend-lane",
                latency_ms=_elapsed_ms(started),
                output_summary={
                    "product_count": summary.candidate_product_count,
                    "review_count": summary.retrieved_review_count,
                    "review_retrieval_method": summary.review_retrieval_method,
                    "review_retrieval_fallback_reason": (
                        summary.review_retrieval_fallback_reason
                    ),
                    "data_source": "amazon_reviews_2023",
                },
            ),
        }

    def _rank_node(self, state: ActualWorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        rankings, reviews = self.product_ranker(
            state["dialogue_state"],
            state["query"],
            state["candidate_products"],
            state["candidate_reviews"],
        )
        recommendation = create_actual_recommendation_response(
            state["dialogue_state"], rankings, reviews, state["query"]
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
                        {"product_id": item.product_id, "score": item.score.total}
                        for item in rankings[:3]
                    ],
                    "unmapped_preference_ids": state[
                        "query"
                    ].unmapped_preference_ids,
                },
            ),
        }

    async def _recommend_node(self, state: ActualWorkflowState) -> dict[str, Any]:
        started = time.perf_counter()
        result = await self.response_composer.recommend(
            state["recommendation"],
            state["browse_result"],
            state["understanding"],
            state["candidate_products"],
            conversation_id=state["conversation_id"],
            turn=state["turn_number"],
        )
        final = build_actual_recommend_response(
            result, state["recommendation"], state["candidate_products"]
        )
        return {
            "final_response": final,
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
                    "card_count": len(final.product_cards),
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
    ) -> ActualPipelineTurn:
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
        return ActualPipelineTurn(
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
