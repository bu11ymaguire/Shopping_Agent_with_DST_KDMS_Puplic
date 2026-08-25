"""SPN Understanding 노드의 구조화 출력 계약.

Understanding은 발화를 읽어 상태 갱신 *후보*만 만든다. 이 모델은 상태를
병합하거나 확정하지 않는다. 실제 병합, provenance 부착, status 전이는 다음
RA-Rec State Manager의 책임이다.

랭킹에 쓰일 식별자는 closed vocabulary다. 자연어 ``value_text``와 원문 근거
``evidence_text``는 자유롭게 쓰되 ``canonical_id``는 아래 Literal에서만 고른다.
카테고리 프로파일링 전의 초기 범용 어휘이므로 schema_version을 바꾸지 않고
항목을 추가하거나 의미를 변경하면 안 된다.
"""

from __future__ import annotations

from typing import ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

SPNFacetName: TypeAlias = Literal[
    "subjective_property",
    "event",
    "activity",
    "goal_purpose",
    "goal_audience",
]

EvidenceOrigin: TypeAlias = Literal["explicit", "implicit", "inferred"]

IntentName: TypeAlias = Literal[
    "search",
    "refine",
    "reject",
    "inspect",
    "compare",
    "purchase",
    "unknown",
]

CategoryId: TypeAlias = Literal[
    "category_smartphone",
    "category_tablet",
    "category_laptop",
    "category_headphones",
]

FacetValueId: TypeAlias = Literal[
    # subjective_property
    "subjective_longevity",
    "subjective_performance",
    "subjective_portability",
    "subjective_value",
    "subjective_display_quality",
    "subjective_audio_quality",
    "subjective_durability",
    # event
    "event_device_failure",
    "event_first_purchase",
    "event_replacement",
    "event_gift",
    # activity
    "activity_note_taking",
    "activity_gaming",
    "activity_video",
    "activity_reading",
    "activity_general",
    # goal_purpose
    "goal_replace_device",
    "goal_long_term_use",
    "goal_work_study",
    "goal_entertainment",
    # goal_audience
    "audience_self",
    "audience_child",
    "audience_family",
    "audience_other",
]

PreferenceId: TypeAlias = Literal[
    "budget",
    "budget_flexibility",
    "delivery_deadline",
    "urgency_pressure",
    "storage_capacity",
    "battery",
    "audio",
    "portability",
    "note_taking",
    "price_value",
    "display",
    "durability",
    "performance",
    "longevity_value",
    "review_signal",
    "color_residual",
]

CanonicalId: TypeAlias = CategoryId | FacetValueId | PreferenceId

RejectionReasonId: TypeAlias = Literal[
    "reject_stock_delay",
    "reject_audio_battery",
    "reject_storage",
    "reject_price",
    "reject_display",
    "reject_performance",
    "reject_portability",
    "reject_other",
]

ItemActionName: TypeAlias = Literal[
    "inspect_current",
    "reject_first",
    "compare_first_second",
    "purchase_current",
]


class ContractModel(BaseModel):
    """모든 노드 계약에 알 수 없는 필드를 금지한다."""

    model_config = ConfigDict(extra="forbid")


class CategoryTarget(ContractModel):
    kind: Literal["category"]


class FacetTarget(ContractModel):
    kind: Literal["facet"]
    facet: SPNFacetName


class ConstraintTarget(ContractModel):
    kind: Literal["constraint"]
    scope: Literal["hard", "soft"]
    key: str = Field(min_length=1)


CandidateTarget: TypeAlias = CategoryTarget | FacetTarget | ConstraintTarget


CATEGORY_IDS = frozenset(CategoryId.__args__)
PREFERENCE_IDS = frozenset(PreferenceId.__args__)
FACET_IDS: dict[str, frozenset[str]] = {
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


class StateUpdateCandidate(ContractModel):
    target: CandidateTarget
    canonical_id: CanonicalId
    value_text: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    origin: EvidenceOrigin
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def id(self) -> str:
        """삭제된 TypeScript 계약의 ``id``를 위한 읽기 전용 호환 접근자."""
        return self.canonical_id

    @model_validator(mode="after")
    def canonical_id_matches_target(self) -> StateUpdateCandidate:
        target = self.target
        if isinstance(target, CategoryTarget):
            allowed = CATEGORY_IDS
        elif isinstance(target, FacetTarget):
            allowed = FACET_IDS[target.facet]
        else:
            allowed = PREFERENCE_IDS
            if target.key != self.canonical_id:
                raise ValueError(
                    "constraint target.key와 canonical_id는 같은 closed-vocabulary "
                    "식별자여야 합니다."
                )

        if self.canonical_id not in allowed:
            raise ValueError(
                f"canonical_id {self.canonical_id!r}는 target {target!r}에 사용할 수 없습니다."
            )
        return self


class FacetCandidates(ContractModel):
    """strict JSON Schema가 허용하는 고정 키 facet 객체."""

    subjective_property: StateUpdateCandidate | None = None
    event: StateUpdateCandidate | None = None
    activity: StateUpdateCandidate | None = None
    goal_purpose: StateUpdateCandidate | None = None
    goal_audience: StateUpdateCandidate | None = None

    @model_validator(mode="after")
    def values_match_fields(self) -> FacetCandidates:
        for facet_name in SPNFacetName.__args__:
            candidate = getattr(self, facet_name)
            if candidate is None:
                continue
            if not isinstance(candidate.target, FacetTarget):
                raise ValueError(f"facets.{facet_name}의 target.kind는 'facet'이어야 합니다.")
            if candidate.target.facet != facet_name:
                raise ValueError(
                    f"facets.{facet_name}와 target.facet={candidate.target.facet!r}가 다릅니다."
                )
        return self


class RejectionReason(ContractModel):
    canonical_id: RejectionReasonId
    value_text: str = Field(min_length=1)
    evidence_text: str = Field(min_length=1)
    reason_type: Literal["situational_constraint", "product_attribute"]


class ItemActionCandidate(ContractModel):
    name: ItemActionName
    rejection_reason: RejectionReason | None = None

    @model_validator(mode="after")
    def rejection_reason_matches_action(self) -> ItemActionCandidate:
        is_rejection = self.name == "reject_first"
        if is_rejection and self.rejection_reason is None:
            raise ValueError("reject_first에는 rejection_reason이 필요합니다.")
        if not is_rejection and self.rejection_reason is not None:
            raise ValueError("reject_first가 아닌 행동에는 rejection_reason을 넣지 않습니다.")
        return self


class UnderstandingOutput(ContractModel):
    """SPN Understanding(PLAN)의 유일한 출력 객체."""

    schema_version: ClassVar[str] = "understanding-v1-generic-electronics"

    utterance: str = Field(min_length=1)
    intents: list[IntentName] = Field(min_length=1)
    facets: FacetCandidates
    candidates: list[StateUpdateCandidate]
    item_action: ItemActionCandidate | None = None
    supersedes: list[PreferenceId]
    residual_color_choice: bool

    @model_validator(mode="after")
    def cross_field_contracts(self) -> UnderstandingOutput:
        if len(self.intents) != len(set(self.intents)):
            raise ValueError("intents에는 중복 값을 넣지 않습니다.")
        if len(self.supersedes) != len(set(self.supersedes)):
            raise ValueError("supersedes에는 중복 값을 넣지 않습니다.")
        candidate_ids = [candidate.canonical_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidates에는 같은 canonical_id를 중복할 수 없습니다.")

        candidate_signatures = {
            (
                candidate.target.kind,
                getattr(candidate.target, "facet", None),
                candidate.canonical_id,
            )
            for candidate in self.candidates
        }
        normalized_candidates = list(self.candidates)
        for facet_name in SPNFacetName.__args__:
            candidate = getattr(self.facets, facet_name)
            if candidate is None:
                continue
            signature = ("facet", facet_name, candidate.canonical_id)
            if signature not in candidate_signatures:
                # facets와 candidates는 삭제된 TypeScript 계약에서 중복 표현이다.
                # 동일한 검증 객체를 재생성하라고 LLM에 repair를 반복시키지 않고,
                # 이미 검증된 facet 후보를 Understanding 출력에서 정규화한다.
                normalized_candidates.append(candidate.model_copy(deep=True))
                candidate_signatures.add(signature)
        self.candidates = normalized_candidates

        if self.residual_color_choice:
            if self.item_action is None or self.item_action.name != "purchase_current":
                raise ValueError(
                    "residual_color_choice는 purchase_current 상황에서만 true일 수 있습니다."
                )

        action_to_intent = {
            "inspect_current": "inspect",
            "reject_first": "reject",
            "compare_first_second": "compare",
            "purchase_current": "purchase",
        }
        action_intents = frozenset(action_to_intent.values())
        if self.item_action is None:
            invalid = action_intents.intersection(self.intents)
            if invalid:
                raise ValueError(
                    f"행동 intent {sorted(invalid)}에는 대응하는 item_action이 필요합니다."
                )
        else:
            expected_intent = action_to_intent[self.item_action.name]
            if expected_intent not in self.intents:
                raise ValueError(
                    f"item_action={self.item_action.name!r}에는 intent "
                    f"{expected_intent!r}가 필요합니다."
                )
        return self
