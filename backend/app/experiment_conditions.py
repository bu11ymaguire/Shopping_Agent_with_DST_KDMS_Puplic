"""One-variable ablation contracts for the frozen tablet-domain v2 evaluation."""

from __future__ import annotations

from typing import Literal, TypeAlias

from app.models import DialogueState, RankedProduct
from app.models.actual_demo import ActualRankedReview, ActualRecommendationQuery
from app.models.experimental import ExperimentalProduct, ExperimentalReview
from app.nodes.actual_recommendation import rank_actual_products
from app.nodes.actual_state_manager import create_tablet_environment_state

ExperimentCondition: TypeAlias = Literal["full", "no_memory", "no_review"]

CONDITION_CONTRACT: dict[ExperimentCondition, dict[str, object]] = {
    "full": {
        "environment_category": "category_tablet",
        "persistent_dialogue_state": True,
        "review_evidence_contribution": True,
    },
    "no_memory": {
        "environment_category": "category_tablet",
        "persistent_dialogue_state": False,
        "review_evidence_contribution": True,
    },
    "no_review": {
        "environment_category": "category_tablet",
        "persistent_dialogue_state": True,
        "review_evidence_contribution": False,
    },
}


def condition_turn_inputs(
    condition: ExperimentCondition,
    persisted_state: DialogueState,
    persisted_rankings: list[RankedProduct],
) -> tuple[DialogueState, list[RankedProduct]]:
    """Return the state visible to one turn without mutating stored output."""
    if condition == "no_memory":
        return create_tablet_environment_state(), []
    return persisted_state.model_copy(deep=True), [
        item.model_copy(deep=True) for item in persisted_rankings
    ]


def rank_products_without_review_contribution(
    state: DialogueState,
    query: ActualRecommendationQuery,
    products: list[ExperimentalProduct],
    reviews: list[ExperimentalReview],
    *,
    result_limit: int = 10,
) -> tuple[list[RankedProduct], list[ActualRankedReview]]:
    """Apply the frozen ranker with review score and reliability terms removed.

    Review retrieval and the product candidate set remain identical to Full. Every
    candidate is first scored by the frozen ranker with no review rows; the remaining
    5% review-count reliability term is then removed before the complete candidate set
    is re-sorted. Weights are not redistributed.
    """
    base_rankings, _ = rank_actual_products(
        state,
        query,
        products,
        [],
        result_limit=max(result_limit, len(products)),
    )
    adjusted: list[RankedProduct] = []
    for item in base_rankings:
        score = item.score
        total = round(
            0.30 * score.hard_constraint_match
            + 0.30 * score.metadata_match
            + 0.20 * score.subjective_need_match
        )
        adjusted.append(
            item.model_copy(
                update={
                    "rank": 0,
                    "score": score.model_copy(
                        update={
                            "review_evidence_score": 0,
                            "evidence_reliability": 0,
                            "total": max(0, min(100, total)),
                        }
                    ),
                    "evidence_review_ids": [],
                }
            )
        )
    ordered = sorted(
        adjusted,
        key=lambda item: (-item.score.total, item.product_id),
    )[:result_limit]
    rankings = [
        item.model_copy(update={"rank": rank})
        for rank, item in enumerate(ordered, start=1)
    ]
    return rankings, []
