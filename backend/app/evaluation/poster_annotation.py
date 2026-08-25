"""Contracts for frozen, blinded human annotation of real recommendations."""

from __future__ import annotations

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
from app.nodes.actual_state_manager import update_actual_dialogue_state
from app.nodes.state_manager import create_initial_dialogue_state


class PosterAnnotationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PosterRecommendationScenario(PosterAnnotationContract):
    id: str = Field(pattern=r"^ph\d{2}$")
    title: str = Field(min_length=1)
    tags: list[str] = Field(min_length=2)
    conversation_turns: list[str] = Field(min_length=4, max_length=6)
    final_request_summary: str = Field(min_length=1)
    state_candidates: list[ActualStateUpdateCandidate] = Field(min_length=3)
    expected_hard_filters: ActualHardFilters

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> PosterRecommendationScenario:
        candidate_ids = [item.canonical_id for item in self.state_candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("state_candidates must not duplicate canonical_id")
        if "category_tablet" not in candidate_ids:
            raise ValueError("every poster scenario must establish category_tablet")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("scenario tags must be unique")
        return self


class PosterScenarioDataset(PosterAnnotationContract):
    dataset_version: Literal["poster-recommendation-scenarios-v1.0"]
    catalog_schema_version: Literal["amazon-tablet-pilot-v2"]
    dataset_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    frozen_before_first_packet_build: Literal[True]
    recommended_annotator_count: int = Field(ge=2, le=5)
    scenario_design_note: str = Field(min_length=1)
    scenarios: list[PosterRecommendationScenario] = Field(min_length=20, max_length=30)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> PosterScenarioDataset:
        ids = [case.id for case in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("poster scenario IDs must be unique")
        return self


class BlankOrdinalAnnotation(PosterAnnotationContract):
    relevance: int | None = Field(default=None, ge=0, le=3)
    rationale: str | None = None

    @model_validator(mode="after")
    def grade_and_rationale_are_filled_together(self) -> BlankOrdinalAnnotation:
        if (self.relevance is None) != (self.rationale is None):
            raise ValueError("relevance and rationale must both be blank or both be filled")
        if self.rationale is not None and not self.rationale.strip():
            raise ValueError("a completed rationale must not be empty")
        return self


class BlindedProductCandidate(PosterAnnotationContract):
    candidate_id: str = Field(pattern=r"^ph\d{2}-p\d{2}$")
    title: str
    brand: str | None = None
    price_usd: float | None = None
    average_rating: float | None = None
    selected_review_count: int = Field(ge=0)
    storage_gb: int | None = None
    memory_gb: int | None = None
    screen_inches: float | None = None
    weight_grams: int | None = None
    operating_system: str | None = None
    stylus_mentioned: bool
    features: list[str]
    description: list[str]
    annotation: BlankOrdinalAnnotation


class BlindedProductScenario(PosterAnnotationContract):
    scenario_id: str = Field(pattern=r"^ph\d{2}$")
    conversation_turns: list[str] = Field(min_length=4, max_length=6)
    final_request_summary: str
    candidates: list[BlindedProductCandidate] = Field(min_length=1)


class ProductAnnotationPacket(PosterAnnotationContract):
    schema_version: Literal["poster-product-annotation-packet-v1"]
    packet_id: str = Field(min_length=1)
    annotator_id: str = Field(pattern=r"^annotator-\d{2}$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instructions: list[str] = Field(min_length=1)
    scenarios: list[BlindedProductScenario] = Field(min_length=1)


class BlindedReviewCandidate(PosterAnnotationContract):
    review_id: str = Field(pattern=r"^ph\d{2}-p\d{2}-r\d{2}$")
    candidate_id: str = Field(pattern=r"^ph\d{2}-p\d{2}$")
    product_title: str
    rating: float = Field(ge=0, le=5)
    review_title: str | None = None
    review_text: str = Field(min_length=1)
    verified_purchase: bool
    helpful_vote: int = Field(ge=0)
    annotation: BlankOrdinalAnnotation


class BlindedReviewScenario(PosterAnnotationContract):
    scenario_id: str = Field(pattern=r"^ph\d{2}$")
    conversation_turns: list[str] = Field(min_length=4, max_length=6)
    final_request_summary: str
    reviews: list[BlindedReviewCandidate]


class ReviewAnnotationPacket(PosterAnnotationContract):
    schema_version: Literal["poster-review-annotation-packet-v1"]
    packet_id: str = Field(min_length=1)
    annotator_id: str = Field(pattern=r"^annotator-\d{2}$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instructions: list[str] = Field(min_length=1)
    scenarios: list[BlindedReviewScenario] = Field(min_length=1)


def load_poster_scenario_dataset(path: Path | str) -> PosterScenarioDataset:
    return PosterScenarioDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def build_poster_scenario_state(
    scenario: PosterRecommendationScenario,
) -> DialogueState:
    facet_values: dict[str, ActualStateUpdateCandidate] = {}
    for candidate in scenario.state_candidates:
        if candidate.target.kind == "facet":
            facet_values[candidate.target.facet] = candidate
    understanding = ActualUnderstandingOutput(
        utterance=f"poster evaluation seed {scenario.id}",
        intents=["search"],
        facets=ActualFacetCandidates.model_validate(facet_values),
        candidates=[item.model_copy(deep=True) for item in scenario.state_candidates],
        supersedes=[],
        residual_color_choice=False,
    )
    state, _ = update_actual_dialogue_state(
        create_initial_dialogue_state(),
        understanding,
        [],
        turn_id=f"poster-{scenario.id}",
    )
    return state
