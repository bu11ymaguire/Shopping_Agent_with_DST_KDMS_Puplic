"""실제 Amazon tablet 데이터로 동작하는 자유 입력 데모 계약."""

from __future__ import annotations

from typing import ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.experimental import ExperimentalProduct, ExperimentalReview
from app.models.pipeline import (
    DialogueState,
    NodeTrace,
    PolicyDecision,
    RankedProduct,
    ScoreBreakdown,
    StateDiff,
    UnresolvedPreference,
)
from app.models.understanding import (
    CategoryTarget,
    ConstraintTarget,
    EvidenceOrigin,
    FacetTarget,
    FacetValueId,
    IntentName,
    ItemActionName,
    SPNFacetName,
)


class ActualContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


ActualPreferenceId: TypeAlias = Literal[
    "budget",
    "budget_flexibility",
    "storage_capacity",
    "memory_capacity",
    "max_weight",
    "min_rating",
    "operating_system",
    "price_value",
    "portability",
    "note_taking",
    "display",
    "performance",
    "battery",
    "audio",
    "durability",
    "review_signal",
    "child_friendly",
]
ActualCanonicalId: TypeAlias = Literal["category_tablet"] | FacetValueId | ActualPreferenceId
ActualCandidateTarget: TypeAlias = CategoryTarget | FacetTarget | ConstraintTarget

ACTUAL_PREFERENCE_IDS = frozenset(ActualPreferenceId.__args__)
ACTUAL_FACET_IDS: dict[str, frozenset[str]] = {
    "subjective_property": frozenset(
        value for value in FacetValueId.__args__ if value.startswith("subjective_")
    ),
    "event": frozenset(
        value for value in FacetValueId.__args__ if value.startswith("event_")
    ),
    "activity": frozenset(
        value for value in FacetValueId.__args__ if value.startswith("activity_")
    ),
    "goal_purpose": frozenset(
        value for value in FacetValueId.__args__ if value.startswith("goal_")
    ),
    "goal_audience": frozenset(
        value for value in FacetValueId.__args__ if value.startswith("audience_")
    ),
}

TabletDomainFacetId: TypeAlias = Literal[
    "event_device_failure",
    "event_first_purchase",
    "event_replacement",
    "event_gift",
    "activity_note_taking",
    "activity_gaming",
    "activity_video",
    "activity_reading",
    "activity_general",
    "goal_replace_device",
    "goal_long_term_use",
    "goal_work_study",
    "goal_entertainment",
    "audience_self",
    "audience_child",
    "audience_family",
    "audience_other",
]
TabletDomainCanonicalId: TypeAlias = TabletDomainFacetId | ActualPreferenceId
TabletDomainCandidateTarget: TypeAlias = FacetTarget | ConstraintTarget


class ActualStateUpdateCandidate(ActualContract):
    target: ActualCandidateTarget
    canonical_id: ActualCanonicalId
    value_text: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def canonical_id_matches_target(self) -> ActualStateUpdateCandidate:
        if isinstance(self.target, CategoryTarget):
            allowed = {"category_tablet"}
        elif isinstance(self.target, FacetTarget):
            allowed = ACTUAL_FACET_IDS[self.target.facet]
        else:
            allowed = ACTUAL_PREFERENCE_IDS
            if self.target.key != self.canonical_id:
                raise ValueError("constraint key must equal canonical_id")
        if self.canonical_id not in allowed:
            raise ValueError(
                f"canonical_id {self.canonical_id!r} is invalid for {self.target!r}"
            )
        return self


class TabletDomainStateUpdateCandidate(ActualContract):
    """v2 LLM candidate with a deterministic target derived from its closed ID."""

    canonical_id: TabletDomainCanonicalId
    scope: Literal["hard", "soft"] | None = None
    value_text: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def scope_matches_id(self) -> TabletDomainStateUpdateCandidate:
        is_preference = self.canonical_id in ACTUAL_PREFERENCE_IDS
        if is_preference != (self.scope is not None):
            raise ValueError(
                "preference IDs require scope and facet IDs require null scope"
            )
        return self

    @property
    def target(self) -> TabletDomainCandidateTarget:
        if self.canonical_id in ACTUAL_PREFERENCE_IDS:
            return ConstraintTarget(
                kind="constraint",
                scope=self.scope,
                key=self.canonical_id,
            )
        facet = next(
            name
            for name, values in ACTUAL_FACET_IDS.items()
            if self.canonical_id in values
        )
        return FacetTarget(kind="facet", facet=facet)


class ActualFacetCandidates(ActualContract):
    subjective_property: ActualStateUpdateCandidate | None = None
    event: ActualStateUpdateCandidate | None = None
    activity: ActualStateUpdateCandidate | None = None
    goal_purpose: ActualStateUpdateCandidate | None = None
    goal_audience: ActualStateUpdateCandidate | None = None

    @model_validator(mode="after")
    def values_match_fields(self) -> ActualFacetCandidates:
        for facet_name in SPNFacetName.__args__:
            candidate = getattr(self, facet_name)
            if candidate is None:
                continue
            if not isinstance(candidate.target, FacetTarget):
                raise ValueError(f"facets.{facet_name} must target a facet")
            if candidate.target.facet != facet_name:
                raise ValueError(f"facets.{facet_name} does not match target.facet")
        return self


class TabletDomainFacetCandidates(ActualFacetCandidates):
    """v2 keeps latent subjective generation out of the evaluated pipeline."""

    subjective_property: Literal[None] = None
    event: TabletDomainStateUpdateCandidate | None = None
    activity: TabletDomainStateUpdateCandidate | None = None
    goal_purpose: TabletDomainStateUpdateCandidate | None = None
    goal_audience: TabletDomainStateUpdateCandidate | None = None


ActualRejectionReasonId: TypeAlias = Literal[
    "reject_price",
    "reject_storage",
    "reject_memory",
    "reject_display",
    "reject_performance",
    "reject_portability",
    "reject_battery",
    "reject_audio",
    "reject_durability",
    "reject_reviews",
    "reject_operating_system",
    "reject_other",
]


class ActualRejectionReason(ActualContract):
    canonical_id: ActualRejectionReasonId
    value_text: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    reason_type: Literal["situational_constraint", "product_attribute"]


class ActualItemActionCandidate(ActualContract):
    name: ItemActionName
    target_rank: int | None = Field(default=None, ge=1, le=3)
    compare_rank: int | None = Field(default=None, ge=1, le=3)
    rejection_reason: ActualRejectionReason | None = None

    @model_validator(mode="after")
    def action_fields_match(self) -> ActualItemActionCandidate:
        if self.name == "reject_first" and self.rejection_reason is None:
            raise ValueError("reject_first requires rejection_reason")
        if self.name != "reject_first" and self.rejection_reason is not None:
            raise ValueError("only reject_first accepts rejection_reason")
        if self.name == "compare_first_second":
            if self.compare_rank is None:
                raise ValueError("compare_first_second requires compare_rank")
            if (self.target_rank or 1) == self.compare_rank:
                raise ValueError("comparison ranks must be different")
        elif self.compare_rank is not None:
            raise ValueError("compare_rank is reserved for compare_first_second")
        return self


class ActualTradeoffCandidate(ActualContract):
    prioritized_ids: list[ActualPreferenceId] = Field(min_length=1, max_length=4)
    compromised_ids: list[ActualPreferenceId] = Field(min_length=1, max_length=4)
    value_text: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def sides_do_not_overlap(self) -> ActualTradeoffCandidate:
        if len(self.prioritized_ids) != len(set(self.prioritized_ids)):
            raise ValueError("prioritized_ids must not contain duplicates")
        if len(self.compromised_ids) != len(set(self.compromised_ids)):
            raise ValueError("compromised_ids must not contain duplicates")
        if set(self.prioritized_ids) & set(self.compromised_ids):
            raise ValueError("a trade-off ID cannot be on both sides")
        return self


class ActualUnderstandingOutput(ActualContract):
    schema_version: ClassVar[str] = "understanding-v2-amazon-tablet-en"

    utterance: str = Field(min_length=1)
    intents: list[IntentName] = Field(min_length=1)
    facets: ActualFacetCandidates
    candidates: list[ActualStateUpdateCandidate]
    item_action: ActualItemActionCandidate | None = None
    tradeoff: ActualTradeoffCandidate | None = None
    supersedes: list[ActualPreferenceId]
    residual_color_choice: Literal[False] = False

    @model_validator(mode="after")
    def cross_field_contracts(self) -> ActualUnderstandingOutput:
        if len(self.intents) != len(set(self.intents)):
            raise ValueError("intents must not contain duplicates")
        if len(self.supersedes) != len(set(self.supersedes)):
            raise ValueError("supersedes must not contain duplicates")
        candidate_ids = [candidate.canonical_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidates must not duplicate canonical_id")

        signatures = {
            (
                candidate.target.kind,
                getattr(candidate.target, "facet", None),
                candidate.canonical_id,
            )
            for candidate in self.candidates
        }
        normalized = list(self.candidates)
        for facet_name in SPNFacetName.__args__:
            candidate = getattr(self.facets, facet_name)
            if candidate is None:
                continue
            signature = ("facet", facet_name, candidate.canonical_id)
            if signature not in signatures:
                normalized.append(candidate.model_copy(deep=True))
                signatures.add(signature)
        self.candidates = normalized

        action_to_intent = {
            "inspect_current": "inspect",
            "reject_first": "reject",
            "compare_first_second": "compare",
            "purchase_current": "purchase",
        }
        if self.item_action is None:
            if set(action_to_intent.values()) & set(self.intents):
                raise ValueError("action intents require item_action")
        elif action_to_intent[self.item_action.name] not in self.intents:
            raise ValueError("item_action requires its matching intent")
        return self


class TabletDomainUnderstandingOutput(ActualUnderstandingOutput):
    """v2 PLAN output for a tablet store whose category is environment state."""

    schema_version: ClassVar[str] = "understanding-v3.1-tablet-domain-en"

    domain_route: Literal["in_domain", "unsupported_category"]
    unsupported_category_text: str | None = None
    unsupported_category_evidence: str | None = None
    facets: TabletDomainFacetCandidates
    candidates: list[TabletDomainStateUpdateCandidate]

    @model_validator(mode="after")
    def environment_and_routing_contracts(self) -> TabletDomainUnderstandingOutput:
        if any(candidate.target.kind == "category" for candidate in self.candidates):
            raise ValueError(
                "tablet category is environment state and must not be emitted as a candidate"
            )
        unsupported = self.domain_route == "unsupported_category"
        if unsupported != (self.unsupported_category_text is not None):
            raise ValueError(
                "unsupported_category_text is required only for unsupported_category"
            )
        if unsupported != (self.unsupported_category_evidence is not None):
            raise ValueError(
                "unsupported_category_evidence is required only for unsupported_category"
            )
        if unsupported:
            if not self.unsupported_category_text.strip():
                raise ValueError("unsupported_category_text must not be blank")
            if not self.unsupported_category_evidence.strip():
                raise ValueError("unsupported_category_evidence must not be blank")
            if self.candidates or any(
                getattr(self.facets, facet) is not None
                for facet in SPNFacetName.__args__
            ):
                raise ValueError(
                    "unsupported category requests must not mutate tablet preferences"
                )
            if (
                self.item_action is not None
                or self.tradeoff is not None
                or self.supersedes
            ):
                raise ValueError(
                    "unsupported category requests must not mutate tablet state"
                )
            if self.intents != ["unknown"]:
                raise ValueError(
                    "unsupported category requests must use only the unknown intent"
                )
        return self


class ActualHardFilters(ActualContract):
    max_price_usd: float | None = Field(default=None, gt=0)
    min_storage_gb: int | None = Field(default=None, ge=0)
    min_memory_gb: int | None = Field(default=None, ge=0)
    max_weight_grams: int | None = Field(default=None, gt=0)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    min_screen_inches: float | None = Field(default=None, gt=0)
    operating_system: str | None = None


class ActualRecommendationQuery(ActualContract):
    text: str
    hard_filters: ActualHardFilters
    allow_budget_overrun: bool
    soft_signals: list[str]
    active_preference_ids: list[str]
    unmapped_preference_ids: list[str]


class ActualBrowseSummary(ActualContract):
    candidate_product_count: int = Field(ge=0)
    retrieved_review_count: int = Field(ge=0)
    review_retrieval_method: Literal["token", "semantic_cross_encoder"]
    review_retrieval_fallback_reason: str | None = None
    coverage: dict[str, int]
    data_disclosure: str


class ActualRankedReview(ExperimentalReview):
    similarity_score: int = Field(ge=0, le=100)
    preference_coverage_score: int = Field(ge=0, le=100)
    reliability_score: int = Field(ge=0, le=100)
    total_score: int = Field(ge=0, le=100)
    matched_preference_ids: list[str]


class ActualRecommendationResponse(ActualContract):
    updated_dialogue_state: DialogueState
    ranked_products: list[RankedProduct]
    review_evidence: list[ActualRankedReview]
    explanation: str
    unresolved_preferences: list[UnresolvedPreference]
    unmapped_preference_ids: list[str]


class ActualProductCard(ActualContract):
    product: ExperimentalProduct
    ranking: RankedProduct
    evidence_reviews: list[ActualRankedReview]


class ActualFinalResponse(ActualContract):
    message: str
    product_cards: list[ActualProductCard]
    review_evidence: list[ActualRankedReview]
    next_actions: list[str]
    data_disclosure: str


class ActualPipelineTurn(ActualContract):
    conversation_id: str
    turn_id: str
    utterance: str
    understanding: TabletDomainUnderstandingOutput | ActualUnderstandingOutput
    dialogue_state: DialogueState
    state_diff: StateDiff
    policy: PolicyDecision
    query: ActualRecommendationQuery | None = None
    browse_result: ActualBrowseSummary | None = None
    recommendation: ActualRecommendationResponse | None = None
    final_response: ActualFinalResponse
    rankings: list[RankedProduct]
    trace: list[NodeTrace]


class ActualConversationSnapshot(ActualContract):
    conversation_id: str
    dialogue_state: DialogueState
    turns: list[ActualPipelineTurn]
