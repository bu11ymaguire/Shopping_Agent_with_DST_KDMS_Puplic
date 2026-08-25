"""MVP LangGraph 노드 사이에서 공유하는 상태·검색·응답 계약."""

from __future__ import annotations

from typing import Any, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from app.models.understanding import EvidenceOrigin, SPNFacetName, UnderstandingOutput


class PipelineContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


EvidenceStatus = Literal["confirmed", "unconfirmed", "superseded"]
StateValueOrigin: TypeAlias = EvidenceOrigin | Literal["environment"]
DomainRoute: TypeAlias = Literal["in_domain", "unsupported_category"]


class PreferenceValue(PipelineContract):
    canonical_id: str = Field(min_length=1)
    value_text: str = Field(min_length=1)
    origin: StateValueOrigin
    confidence: float = Field(ge=0.0, le=1.0)
    status: EvidenceStatus
    evidence_turn_ids: list[str]
    updated_at_turn_id: str


class SPNState(PipelineContract):
    subjective_property: PreferenceValue | None = None
    event: PreferenceValue | None = None
    activity: PreferenceValue | None = None
    goal_purpose: PreferenceValue | None = None
    goal_audience: PreferenceValue | None = None

    def get_facet(self, facet: SPNFacetName) -> PreferenceValue | None:
        return getattr(self, facet)

    def set_facet(self, facet: SPNFacetName, value: PreferenceValue) -> None:
        setattr(self, facet, value)


class RejectedItem(PipelineContract):
    product_id: str
    reason: PreferenceValue
    reason_type: Literal["situational_constraint", "product_attribute"]


class PreferenceHistoryEntry(PipelineContract):
    turn_id: str
    changed_paths: list[str]


class UnresolvedPreference(PipelineContract):
    field: str
    reason: str
    priority: int


class DialogueState(PipelineContract):
    domain_route: DomainRoute = "in_domain"
    unsupported_category_text: str | None = None
    category: PreferenceValue | None = None
    hard_constraints: dict[str, PreferenceValue] = Field(default_factory=dict)
    soft_constraints: dict[str, PreferenceValue] = Field(default_factory=dict)
    subjective_needs: SPNState = Field(default_factory=SPNState)
    tradeoffs: list[PreferenceValue] = Field(default_factory=list)
    recommended_items: list[str] = Field(default_factory=list)
    shortlisted_items: list[str] = Field(default_factory=list)
    inspected_items: list[str] = Field(default_factory=list)
    rejected_items: list[RejectedItem] = Field(default_factory=list)
    carted_items: list[str] = Field(default_factory=list)
    purchased_items: list[str] = Field(default_factory=list)
    current_item: str | None = None
    unresolved_preferences: list[UnresolvedPreference] = Field(default_factory=list)
    preference_history: list[PreferenceHistoryEntry] = Field(default_factory=list)


class StateDiff(PipelineContract):
    changed_paths: list[str]
    summary: list[str]


class ProductMetadata(PipelineContract):
    category_id: str
    display: str
    chipset: str
    storage: str
    storage_gb: int
    weight_grams: int
    battery_hours: int
    speaker_tier: int = Field(ge=1, le=5)
    performance_tier: int = Field(ge=1, le=5)
    longevity_years: int
    delivery_days: int
    available_colors: list[str]
    use_cases: list[str]


class Product(PipelineContract):
    id: str
    title: str
    image: str
    brand: str
    price: int
    rating: float
    review_count: int
    description: str
    shipping: str
    stock: str
    source: Literal["controlled_inventory"] = "controlled_inventory"
    metadata: ProductMetadata


class Review(PipelineContract):
    id: str
    product_id: str
    text: str
    helpfulness: float | None = Field(default=None, ge=0.0, le=1.0)
    source: Literal["controlled_mock_review"] = "controlled_mock_review"


class RankedReview(Review):
    similarity_score: int
    preference_coverage_score: int
    reliability_score: int
    total_score: int
    matched_preference_ids: list[str]


class ScoreBreakdown(PipelineContract):
    hard_constraint_match: int
    metadata_match: int
    subjective_need_match: int
    review_evidence_score: int
    evidence_reliability: int
    total: int


class RankedProduct(PipelineContract):
    product_id: str
    rank: int
    score: ScoreBreakdown
    evidence_review_ids: list[str]
    matched_preference_ids: list[str]


class VaguenessBreakdown(PipelineContract):
    category_breadth: int
    missing_required_info: int
    unresolved_spn: int
    contradiction_penalty: int
    total: int
    threshold: int = 45
    reasons: list[str]


PolicyLane = Literal["clarify-lane", "recommend-lane"]


class PolicySnapshot(PipelineContract):
    facets: SPNState
    vagueness: VaguenessBreakdown


class PolicyDecision(PipelineContract):
    action: Literal["ask_user", "recommend"]
    lane: PolicyLane
    question_target: UnresolvedPreference | None = None
    reasons: list[str]
    snapshot: PolicySnapshot


class RecommendationQuery(PipelineContract):
    text: str
    category_id: str | None
    hard_filters: dict[str, str]
    soft_signals: list[str]
    unmapped_preference_ids: list[str]


class BrowseResult(PipelineContract):
    products: list[Product]
    reviews: list[Review]
    price_evidence: list[str]
    delivery_evidence: list[str]
    images: list[str]
    inventory_disclosure: str


class RecommendationResponse(PipelineContract):
    updated_dialogue_state: DialogueState
    ranked_products: list[RankedProduct]
    review_evidence: list[RankedReview]
    explanation: str
    unresolved_preferences: list[UnresolvedPreference]
    unmapped_preference_ids: list[str]


class ProductCard(PipelineContract):
    product: Product
    ranking: RankedProduct
    evidence_reviews: list[RankedReview]


class FinalResponse(PipelineContract):
    message: str
    product_cards: list[ProductCard]
    price_evidence: list[str]
    delivery_evidence: list[str]
    image_evidence: list[str]
    review_evidence: list[RankedReview]
    next_actions: list[str]


class ResponseDraft(PipelineContract):
    """LLM Response Composer가 만드는 자연어 부분만의 strict 출력."""

    schema_version: ClassVar[str] = "response-draft-v1"

    message: str = Field(min_length=1)
    next_actions: list[str] = Field(min_length=1, max_length=3)


class NodeTrace(PipelineContract):
    node_id: str
    role: str
    owner: Literal["SPN", "RA-Rec"]
    lane: PolicyLane | None = None
    latency_ms: float
    output_summary: dict[str, Any]


class PipelineTurn(PipelineContract):
    conversation_id: str
    turn_id: str
    utterance: str
    understanding: UnderstandingOutput
    dialogue_state: DialogueState
    state_diff: StateDiff
    policy: PolicyDecision
    query: RecommendationQuery | None = None
    browse_result: BrowseResult | None = None
    recommendation: RecommendationResponse | None = None
    final_response: FinalResponse
    rankings: list[RankedProduct]
    trace: list[NodeTrace]


class ConversationSnapshot(PipelineContract):
    conversation_id: str
    dialogue_state: DialogueState
    turns: list[PipelineTurn]
