"""Amazon Reviews 2023 실데이터 카탈로그의 읽기 전용 API 계약."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExperimentalContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


ExperimentalProductSort = Literal[
    "review_count",
    "rating",
    "price_low",
    "price_value",
    "portability",
    "storage",
    "memory",
    "display",
    "note_taking",
]
SentimentBucket = Literal["negative", "neutral", "positive"]
ReviewRetrievalMethod = Literal["token", "semantic_cross_encoder"]


class QuantileScores(ExperimentalContract):
    price_value: int | None = Field(default=None, ge=0, le=100)
    portability: int | None = Field(default=None, ge=0, le=100)
    storage: int | None = Field(default=None, ge=0, le=100)
    memory: int | None = Field(default=None, ge=0, le=100)
    display: int | None = Field(default=None, ge=0, le=100)
    note_taking: int | None = Field(default=None, ge=0, le=100)


class ExperimentalCatalogStatus(ExperimentalContract):
    mode: Literal["amazon-reviews-2023-experimental"] = (
        "amazon-reviews-2023-experimental"
    )
    available: bool
    schema_version: str | None = None
    dataset_revision: str | None = None
    product_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    disclosure: str
    limitations: list[str]
    unavailable_reason: str | None = None


class ExperimentalProduct(ExperimentalContract):
    parent_asin: str
    title: str
    brand: str | None = None
    store: str | None = None
    price_usd: float | None = Field(default=None, ge=0)
    average_rating: float | None = Field(default=None, ge=0, le=5)
    rating_number: int | None = Field(default=None, ge=0)
    image_url: str
    storage_gb: int | None = Field(default=None, ge=0)
    memory_gb: int | None = Field(default=None, ge=0)
    screen_inches: float | None = Field(default=None, ge=0)
    weight_grams: int | None = Field(default=None, ge=0)
    operating_system: str | None = None
    stylus_mentioned: bool
    selected_review_count: int = Field(ge=0)
    attribute_scores: QuantileScores
    missing_attributes: list[str]
    source: Literal["amazon_reviews_2023"]


class ExperimentalProductDetail(ExperimentalProduct):
    categories: list[str]
    features: list[str]
    description: list[str]
    classification: dict[str, object]
    attribute_evidence: dict[str, object]


class ExperimentalProductQuery(ExperimentalContract):
    q: str | None = None
    max_price_usd: float | None = None
    min_storage_gb: int | None = None
    min_memory_gb: int | None = None
    max_weight_grams: int | None = None
    min_rating: float | None = None
    min_screen_inches: float | None = None
    operating_system: str | None = None
    stylus: bool | None = None
    sort_by: ExperimentalProductSort


class ExperimentalProductSearchResponse(ExperimentalContract):
    status: ExperimentalCatalogStatus
    query: ExperimentalProductQuery
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    coverage: dict[str, int]
    products: list[ExperimentalProduct]


class ExperimentalReview(ExperimentalContract):
    review_id: str
    parent_asin: str
    asin: str
    rating: float = Field(ge=0, le=5)
    title: str | None = None
    text: str
    timestamp: int
    verified_purchase: bool
    helpful_vote: int = Field(ge=0)
    sentiment_bucket: SentimentBucket
    retrieval_score: int = Field(ge=0, le=100)
    retrieval_method: ReviewRetrievalMethod = "token"
    source: Literal["amazon_reviews_2023"]


class ExperimentalReviewSearchResponse(ExperimentalContract):
    parent_asin: str
    q: str | None = None
    sentiment: SentimentBucket | None = None
    verified_only: bool
    limit: int = Field(ge=1)
    reviews: list[ExperimentalReview]
