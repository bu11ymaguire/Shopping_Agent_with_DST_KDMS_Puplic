"""Blind human relevance packet contracts for the official v2.3 holdout."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnnotationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OptionalOrdinalGrade(AnnotationContract):
    grade: int | None = Field(default=None, ge=0, le=3)
    rationale: str | None = None

    @model_validator(mode="after")
    def rationale_requires_grade(self) -> OptionalOrdinalGrade:
        if self.rationale is not None and self.grade is None:
            raise ValueError("rationale cannot be filled before grade")
        if self.rationale is not None and not self.rationale.strip():
            raise ValueError("rationale must not be blank when provided")
        return self


class OfficialProductCandidate(AnnotationContract):
    candidate_id: str = Field(pattern=r"^th\d{2}-p\d{2}$")
    title: str
    brand: str | None = None
    price_usd: float | None = None
    average_rating: float | None = None
    rating_number: int | None = None
    selected_review_count: int = Field(ge=0)
    storage_gb: int | None = None
    memory_gb: int | None = None
    screen_inches: float | None = None
    weight_grams: int | None = None
    operating_system: str | None = None
    stylus_mentioned: bool
    features: list[str]
    description: list[str]
    annotation: OptionalOrdinalGrade


class OfficialProductScenario(AnnotationContract):
    scenario_id: str = Field(pattern=r"^th\d{2}$")
    conversation_turns: list[str] = Field(min_length=4, max_length=6)
    evaluation_question: str
    candidates: list[OfficialProductCandidate] = Field(min_length=1)


class OfficialProductPacket(AnnotationContract):
    schema_version: Literal["tablet-domain-product-annotation-packet-v1"]
    packet_id: str
    annotator_id: str = Field(pattern=r"^annotator-\d{2}$")
    official_run_id: str
    official_raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instructions: list[str] = Field(min_length=1)
    scenarios: list[OfficialProductScenario] = Field(min_length=1)


class OfficialReviewCandidate(AnnotationContract):
    review_id: str = Field(pattern=r"^th\d{2}-p\d{2}-r\d{2}$")
    candidate_id: str = Field(pattern=r"^th\d{2}-p\d{2}$")
    product_title: str
    rating: float = Field(ge=0, le=5)
    review_title: str | None = None
    review_text: str = Field(min_length=1)
    verified_purchase: bool
    helpful_vote: int = Field(ge=0)
    annotation: OptionalOrdinalGrade


class OfficialReviewScenario(AnnotationContract):
    scenario_id: str = Field(pattern=r"^th\d{2}$")
    conversation_turns: list[str] = Field(min_length=4, max_length=6)
    evaluation_question: str
    reviews: list[OfficialReviewCandidate] = Field(min_length=1)


class OfficialReviewPacket(AnnotationContract):
    schema_version: Literal["tablet-domain-review-annotation-packet-v1"]
    packet_id: str
    annotator_id: str = Field(pattern=r"^annotator-\d{2}$")
    official_run_id: str
    official_raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    instructions: list[str] = Field(min_length=1)
    scenarios: list[OfficialReviewScenario] = Field(min_length=1)


def load_official_product_packet(path: Path | str) -> OfficialProductPacket:
    return OfficialProductPacket.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def load_official_review_packet(path: Path | str) -> OfficialReviewPacket:
    return OfficialReviewPacket.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
