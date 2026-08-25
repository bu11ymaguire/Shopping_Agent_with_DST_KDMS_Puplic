"""Contracts and deterministic metrics for the real-data recommendation lane."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import DialogueState
from app.models.actual_demo import (
    ActualFacetCandidates,
    ActualHardFilters,
    ActualStateUpdateCandidate,
    ActualUnderstandingOutput,
)
from app.models.experimental import ExperimentalProduct
from app.nodes.actual_state_manager import update_actual_dialogue_state
from app.nodes.state_manager import create_initial_dialogue_state


class RecommendationEvalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecommendationAnnotationProtocol(RecommendationEvalContract):
    annotator: str = Field(min_length=1)
    annotated_at: str = Field(min_length=1)
    pool_method: str = Field(min_length=1)
    product_relevance_scale: dict[str, str]
    review_relevance_scale: dict[str, str]
    limitations: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def relevance_scales_are_complete(self) -> RecommendationAnnotationProtocol:
        expected = {"0", "1", "2", "3"}
        if set(self.product_relevance_scale) != expected:
            raise ValueError("product relevance scale must define grades 0 through 3")
        if set(self.review_relevance_scale) != expected:
            raise ValueError("review relevance scale must define grades 0 through 3")
        return self


class ProductRelevanceJudgment(RecommendationEvalContract):
    parent_asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    relevance: int = Field(ge=0, le=3)
    rationale: str = Field(min_length=1)


class ReviewRelevanceJudgment(RecommendationEvalContract):
    review_id: str = Field(pattern=r"^ar23-[0-9a-f]{20}$")
    parent_asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    relevance: int = Field(ge=0, le=3)
    rationale: str = Field(min_length=1)


class ActualProductRecommendationEvalCase(RecommendationEvalContract):
    id: str = Field(pattern=r"^rp\d{2}$")
    description: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    state_candidates: list[ActualStateUpdateCandidate] = Field(min_length=2)
    expected_hard_filters: ActualHardFilters
    product_judgments: list[ProductRelevanceJudgment] = Field(default_factory=list)

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> ActualProductRecommendationEvalCase:
        candidate_ids = [item.canonical_id for item in self.state_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("state_candidates must not duplicate canonical_id")
        if "category_tablet" not in candidate_ids:
            raise ValueError("every product case must establish category_tablet")
        judged = [item.parent_asin for item in self.product_judgments]
        if len(judged) != len(set(judged)):
            raise ValueError("product judgments must use unique parent_asin values")
        return self


class ActualReviewRetrievalEvalCase(RecommendationEvalContract):
    id: str = Field(pattern=r"^rr\d{2}$")
    description: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1)
    query: str = Field(min_length=1)
    parent_asin: str = Field(min_length=1)
    review_judgments: list[ReviewRelevanceJudgment] = Field(default_factory=list)

    @model_validator(mode="after")
    def judgments_match_product(self) -> ActualReviewRetrievalEvalCase:
        review_ids = [item.review_id for item in self.review_judgments]
        if len(review_ids) != len(set(review_ids)):
            raise ValueError("review judgments must use unique review_id values")
        if any(item.parent_asin != self.parent_asin for item in self.review_judgments):
            raise ValueError("review judgment parent_asin must match its case")
        return self


class ActualRecommendationEvalDataset(RecommendationEvalContract):
    dataset_version: Literal["actual-recommendation-eval-v1.0"]
    catalog_schema_version: Literal["amazon-tablet-pilot-v2"]
    dataset_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    frozen_before_baseline_run: Literal[True]
    annotation_protocol: RecommendationAnnotationProtocol
    product_cases: list[ActualProductRecommendationEvalCase] = Field(
        min_length=4, max_length=12
    )
    review_cases: list[ActualReviewRetrievalEvalCase] = Field(
        min_length=4, max_length=20
    )

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> ActualRecommendationEvalDataset:
        ids = [case.id for case in [*self.product_cases, *self.review_cases]]
        if len(ids) != len(set(ids)):
            raise ValueError("recommendation evaluation case IDs must be unique")
        return self


def load_actual_recommendation_eval_dataset(
    path: Path | str,
) -> ActualRecommendationEvalDataset:
    return ActualRecommendationEvalDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def build_recommendation_eval_state(
    case: ActualProductRecommendationEvalCase,
) -> DialogueState:
    facet_values: dict[str, ActualStateUpdateCandidate] = {}
    for candidate in case.state_candidates:
        if candidate.target.kind == "facet":
            facet_values[candidate.target.facet] = candidate
    understanding = ActualUnderstandingOutput(
        utterance=f"evaluation seed {case.id}",
        intents=["search"],
        facets=ActualFacetCandidates.model_validate(facet_values),
        candidates=[item.model_copy(deep=True) for item in case.state_candidates],
        supersedes=[],
        residual_color_choice=False,
    )
    state, _ = update_actual_dialogue_state(
        create_initial_dialogue_state(),
        understanding,
        [],
        turn_id=f"eval-{case.id}",
    )
    return state


def _dcg(grades: list[int | float]) -> float:
    return sum(
        (2**grade - 1) / math.log2(index + 2)
        for index, grade in enumerate(grades)
    )


def score_graded_ranking(
    ranked_ids: list[str],
    judgments: dict[str, int | float],
    *,
    k: int,
) -> dict[str, float | int]:
    selected = ranked_ids[:k]
    grades = [judgments.get(item_id, 0) for item_id in selected]
    ideal = sorted(judgments.values(), reverse=True)[:k]
    ideal_dcg = _dcg(ideal)
    relevant = {item_id for item_id, grade in judgments.items() if grade >= 2}
    retrieved_relevant = sum(item_id in relevant for item_id in selected)
    return {
        f"ndcg_at_{k}": _dcg(grades) / ideal_dcg if ideal_dcg else 1.0,
        f"recall_at_{k}": (
            retrieved_relevant / len(relevant) if relevant else 1.0
        ),
        f"precision_at_{k}": (
            retrieved_relevant / len(selected) if selected else 0.0
        ),
        f"judgment_coverage_at_{k}": (
            sum(item_id in judgments for item_id in selected) / len(selected)
            if selected
            else 1.0
        ),
        f"relevant_retrieved_at_{k}": retrieved_relevant,
        "judged_relevant_count": len(relevant),
    }


def product_satisfies_hard_filters(
    product: ExperimentalProduct,
    hard_filters: ActualHardFilters,
    *,
    allow_budget_overrun: bool,
) -> bool:
    if (
        hard_filters.max_price_usd is not None
        and not allow_budget_overrun
        and (
            product.price_usd is None
            or product.price_usd > hard_filters.max_price_usd
        )
    ):
        return False
    for value, threshold, comparator in (
        (product.storage_gb, hard_filters.min_storage_gb, "min"),
        (product.memory_gb, hard_filters.min_memory_gb, "min"),
        (product.weight_grams, hard_filters.max_weight_grams, "max"),
        (product.average_rating, hard_filters.min_rating, "min"),
        (product.screen_inches, hard_filters.min_screen_inches, "min"),
    ):
        if threshold is None:
            continue
        if value is None:
            return False
        if comparator == "min" and value < threshold:
            return False
        if comparator == "max" and value > threshold:
            return False
    if hard_filters.operating_system and (
        not product.operating_system
        or hard_filters.operating_system.casefold()
        not in product.operating_system.casefold()
    ):
        return False
    return True


def aggregate_recommendation_case_metrics(
    case_results: list[dict[str, object]],
) -> dict[str, float | int]:
    if not case_results:
        return {"case_count": 0}

    def mean(key: str) -> float:
        return sum(float(result[key]) for result in case_results) / len(case_results)

    numeric_keys = (
        "ndcg_at_3",
        "ndcg_at_10",
        "recall_at_3",
        "recall_at_10",
        "precision_at_3",
        "judgment_coverage_at_3",
        "judgment_coverage_at_10",
        "hard_filter_violation_rate",
        "evidence_consistency_rate",
    )
    return {
        "case_count": len(case_results),
        **{key: mean(key) for key in numeric_keys},
    }


def aggregate_review_case_metrics(
    case_results: list[dict[str, object]],
) -> dict[str, float | int]:
    if not case_results:
        return {"case_count": 0}

    def mean(key: str) -> float:
        return sum(float(result[key]) for result in case_results) / len(case_results)

    return {
        "case_count": len(case_results),
        "ndcg_at_3": mean("ndcg_at_3"),
        "recall_at_3": mean("recall_at_3"),
        "precision_at_3": mean("precision_at_3"),
        "judgment_coverage_at_3": mean("judgment_coverage_at_3"),
    }
