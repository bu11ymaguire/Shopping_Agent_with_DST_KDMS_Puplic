"""Deterministic metrics for live replay of the frozen poster scenarios."""

from __future__ import annotations

import math
import statistics
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.actual_recommendation import product_satisfies_hard_filters
from app.evaluation.poster_annotation import PosterRecommendationScenario
from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models.actual_demo import ActualHardFilters, ActualPipelineTurn


class PosterLiveReplayContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PosterLiveScenarioMetrics(PosterLiveReplayContract):
    scenario_id: str = Field(pattern=r"^ph\d{2}$")
    status: Literal["completed", "error"]
    expected_turn_count: int = Field(ge=1)
    completed_turn_count: int = Field(ge=0)
    turn_wall_latency_ms: list[float]
    expected_candidate_ids: list[str]
    extracted_candidate_ids: list[str]
    canonical_id_exact: bool
    canonical_true_positive: int = Field(ge=0)
    canonical_false_positive: int = Field(ge=0)
    canonical_false_negative: int = Field(ge=0)
    expected_hard_filters: ActualHardFilters
    actual_hard_filters: ActualHardFilters | None = None
    hard_filter_exact: bool
    final_lane: str | None = None
    final_lane_expected: bool
    final_recommendation_count: int = Field(ge=0)
    response_fallback_count: int = Field(ge=0)
    recommend_turn_count: int = Field(ge=0)
    retrieval_fallback_count: int = Field(ge=0)
    actual_filter_product_count: int = Field(ge=0)
    actual_filter_violation_count: int = Field(ge=0)
    expected_filter_product_count: int = Field(ge=0)
    expected_filter_violation_count: int = Field(ge=0)
    evidence_card_count: int = Field(ge=0)
    evidence_mismatch_count: int = Field(ge=0)
    error_type: str | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def completion_and_counts_are_consistent(self) -> PosterLiveScenarioMetrics:
        if len(self.turn_wall_latency_ms) != self.completed_turn_count:
            raise ValueError("turn latency count must equal completed_turn_count")
        if self.completed_turn_count > self.expected_turn_count:
            raise ValueError("completed turns cannot exceed expected turns")
        if self.status == "completed":
            if self.completed_turn_count != self.expected_turn_count:
                raise ValueError("completed scenario must contain every expected turn")
            if self.error_type is not None or self.error_message is not None:
                raise ValueError("completed scenario cannot contain an error")
        elif self.error_type is None:
            raise ValueError("error scenario must identify its error type")
        for violations, total, label in (
            (
                self.actual_filter_violation_count,
                self.actual_filter_product_count,
                "actual filter",
            ),
            (
                self.expected_filter_violation_count,
                self.expected_filter_product_count,
                "expected filter",
            ),
            (self.evidence_mismatch_count, self.evidence_card_count, "evidence"),
        ):
            if violations > total:
                raise ValueError(f"{label} violations cannot exceed evaluated count")
        return self


def build_live_scenario_metrics(
    scenario: PosterRecommendationScenario,
    turns: list[ActualPipelineTurn],
    turn_wall_latency_ms: list[float],
    catalog: ExperimentalAmazonCatalog,
    *,
    error: Exception | None = None,
) -> PosterLiveScenarioMetrics:
    expected_ids = {item.canonical_id for item in scenario.state_candidates}
    extracted_ids = {
        item.canonical_id
        for turn in turns
        for item in turn.understanding.candidates
    }
    final = turns[-1] if turns else None
    actual_filters = final.query.hard_filters if final and final.query else None

    response_fallback_count = 0
    recommend_turn_count = 0
    retrieval_fallback_count = 0
    for turn in turns:
        if turn.policy.lane == "recommend-lane":
            recommend_turn_count += 1
        if turn.browse_result and turn.browse_result.review_retrieval_fallback_reason:
            retrieval_fallback_count += 1
        for trace in turn.trace:
            if trace.node_id.startswith("spn-response-") and (
                trace.output_summary.get("composer") == "template-fallback"
            ):
                response_fallback_count += 1

    actual_filter_product_count = 0
    actual_filter_violation_count = 0
    expected_filter_product_count = 0
    expected_filter_violation_count = 0
    if final:
        for ranking in final.rankings[:3]:
            product = catalog.get_product(ranking.product_id)
            if actual_filters is not None:
                actual_filter_product_count += 1
                if not product_satisfies_hard_filters(
                    product,
                    actual_filters,
                    allow_budget_overrun=bool(
                        final.query and final.query.allow_budget_overrun
                    ),
                ):
                    actual_filter_violation_count += 1
            expected_filter_product_count += 1
            if not product_satisfies_hard_filters(
                product,
                scenario.expected_hard_filters,
                allow_budget_overrun=False,
            ):
                expected_filter_violation_count += 1

    evidence_card_count = 0
    evidence_mismatch_count = 0
    if final:
        for card in final.final_response.product_cards:
            evidence_card_count += 1
            scored_ids = set(card.ranking.evidence_review_ids)
            displayed_ids = {review.review_id for review in card.evidence_reviews}
            if scored_ids != displayed_ids:
                evidence_mismatch_count += 1

    return PosterLiveScenarioMetrics(
        scenario_id=scenario.id,
        status="error" if error else "completed",
        expected_turn_count=len(scenario.conversation_turns),
        completed_turn_count=len(turns),
        turn_wall_latency_ms=turn_wall_latency_ms,
        expected_candidate_ids=sorted(expected_ids),
        extracted_candidate_ids=sorted(extracted_ids),
        canonical_id_exact=expected_ids == extracted_ids,
        canonical_true_positive=len(expected_ids & extracted_ids),
        canonical_false_positive=len(extracted_ids - expected_ids),
        canonical_false_negative=len(expected_ids - extracted_ids),
        expected_hard_filters=scenario.expected_hard_filters,
        actual_hard_filters=actual_filters,
        hard_filter_exact=actual_filters == scenario.expected_hard_filters,
        final_lane=final.policy.lane if final else None,
        final_lane_expected=bool(final and final.policy.lane == "recommend-lane"),
        final_recommendation_count=len(final.rankings[:3]) if final else 0,
        response_fallback_count=response_fallback_count,
        recommend_turn_count=recommend_turn_count,
        retrieval_fallback_count=retrieval_fallback_count,
        actual_filter_product_count=actual_filter_product_count,
        actual_filter_violation_count=actual_filter_violation_count,
        expected_filter_product_count=expected_filter_product_count,
        expected_filter_violation_count=expected_filter_violation_count,
        evidence_card_count=evidence_card_count,
        evidence_mismatch_count=evidence_mismatch_count,
        error_type=type(error).__name__ if error else None,
        error_message=str(error)[:500] if error else None,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def aggregate_live_replay_metrics(
    metrics: list[PosterLiveScenarioMetrics],
) -> dict[str, object]:
    completed = [item for item in metrics if item.status == "completed"]
    total_turns = sum(item.completed_turn_count for item in metrics)
    expected_turns = sum(item.expected_turn_count for item in metrics)
    tp = sum(item.canonical_true_positive for item in completed)
    fp = sum(item.canonical_false_positive for item in completed)
    fn = sum(item.canonical_false_negative for item in completed)
    precision = _rate(tp, tp + fp)
    recall = _rate(tp, tp + fn)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else 0.0
    )
    turn_latencies = [
        latency for item in metrics for latency in item.turn_wall_latency_ms
    ]
    recommend_turns = sum(item.recommend_turn_count for item in metrics)
    actual_products = sum(item.actual_filter_product_count for item in metrics)
    expected_products = sum(item.expected_filter_product_count for item in metrics)
    evidence_cards = sum(item.evidence_card_count for item in metrics)
    return {
        "scenario_count": len(metrics),
        "completed_scenario_count": len(completed),
        "scenario_completion_rate": _rate(len(completed), len(metrics)),
        "completed_turn_count": total_turns,
        "expected_turn_count": expected_turns,
        "turn_completion_rate": _rate(total_turns, expected_turns),
        "schema_failure_rate": _rate(len(metrics) - len(completed), len(metrics)),
        "end_to_end_recommend_success_rate": _rate(
            sum(item.final_lane_expected for item in metrics), len(metrics)
        ),
        "end_to_end_hard_filter_success_rate": _rate(
            sum(item.hard_filter_exact for item in metrics), len(metrics)
        ),
        "canonical_id_exact_rate": _rate(
            sum(item.canonical_id_exact for item in completed), len(completed)
        ),
        "canonical_id_micro_precision": precision,
        "canonical_id_micro_recall": recall,
        "canonical_id_micro_f1": f1,
        "final_lane_accuracy": _rate(
            sum(item.final_lane_expected for item in completed), len(completed)
        ),
        "hard_filter_exact_rate": _rate(
            sum(item.hard_filter_exact for item in completed), len(completed)
        ),
        "response_template_fallback_rate": _rate(
            sum(item.response_fallback_count for item in metrics), total_turns
        ),
        "review_retrieval_fallback_rate": _rate(
            sum(item.retrieval_fallback_count for item in metrics), recommend_turns
        ),
        "actual_query_hard_filter_violation_rate": _rate(
            sum(item.actual_filter_violation_count for item in metrics),
            actual_products,
        ),
        "expected_hard_filter_violation_rate": _rate(
            sum(item.expected_filter_violation_count for item in metrics),
            expected_products,
        ),
        "evidence_consistency_rate": (
            1
            - (
                sum(item.evidence_mismatch_count for item in metrics)
                / evidence_cards
            )
            if evidence_cards
            else None
        ),
        "final_recommendation_count": sum(
            item.final_recommendation_count for item in metrics
        ),
        "turn_wall_latency_ms": {
            "median": statistics.median(turn_latencies) if turn_latencies else None,
            "p95": _percentile(turn_latencies, 0.95),
            "max": max(turn_latencies) if turn_latencies else None,
        },
        "error_scenarios": [
            {
                "scenario_id": item.scenario_id,
                "error_type": item.error_type,
                "error_message": item.error_message,
            }
            for item in metrics
            if item.status == "error"
        ],
    }
