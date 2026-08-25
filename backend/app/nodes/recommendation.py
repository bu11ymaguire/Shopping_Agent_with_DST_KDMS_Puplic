"""결정론적 Query, Browse, Review retrieval, Product ranking 노드."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable

from app.catalog import DemoCatalog
from app.models import (
    BrowseResult,
    DialogueState,
    PreferenceValue,
    Product,
    RankedProduct,
    RankedReview,
    RecommendationQuery,
    RecommendationResponse,
    Review,
    ScoreBreakdown,
)
from app.nodes.policy import unresolved_preferences

AttributeName = str

SYNONYM_GROUPS = (
    {"수령", "배송", "재고", "출고", "입고", "대기", "품절", "빠른"},
    {"장기", "오래", "지속", "수명", "내구", "지원"},
    {"성능", "속도", "칩", "프로세서", "a19", "a18"},
    {"배터리", "사용시간", "충전", "지속시간"},
    {"스피커", "사운드", "음질", "소리", "오디오"},
    {"무게", "가벼운", "가벼워", "휴대", "그립"},
    {"가격", "예산", "비용", "부담", "고가"},
    {"색상", "컬러", "오렌지", "블랙", "화이트"},
    {"후기", "리뷰", "사용기"},
    {"고장", "파손", "교체", "수리"},
    {"저장", "공간", "용량", "스토리지"},
    {"화면", "디스플레이", "선명", "영상"},
)

PREFERENCE_ATTRIBUTES: dict[str, tuple[AttributeName, ...]] = {
    "delivery_deadline": ("delivery_speed",),
    "longevity_value": ("longevity",),
    "battery": ("battery",),
    "audio": ("audio",),
    "portability": ("portability",),
    "price_value": ("price_value",),
    "display": ("display",),
    "performance": ("performance",),
    "storage_capacity": ("storage",),
    "subjective_longevity": ("longevity", "performance"),
    "subjective_performance": ("performance",),
    "subjective_portability": ("portability",),
    "subjective_display_quality": ("display",),
    "subjective_audio_quality": ("audio",),
    "goal_long_term_use": ("longevity", "performance"),
    "goal_work_study": ("performance", "portability"),
    "goal_entertainment": ("display", "battery", "audio"),
    "activity_note_taking": ("portability", "display"),
    "activity_gaming": ("performance", "battery"),
    "activity_video": ("display", "battery", "audio"),
    "activity_reading": ("display", "portability"),
}

# 의도적으로 점수에 연결하지 않고 상태·근거로만 남기는 closed-vocabulary ID.
NON_RANKING_IDS = {
    "budget",
    "budget_flexibility",
    "urgency_pressure",
    "note_taking",
    "durability",
    "review_signal",
    "color_residual",
    "event_device_failure",
    "event_first_purchase",
    "event_replacement",
    "event_gift",
    "activity_general",
    "goal_replace_device",
    "audience_self",
    "audience_child",
    "audience_family",
    "audience_other",
    "subjective_value",
    "subjective_durability",
}


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _average(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[가-힣a-z0-9]+", text.lower()))


def _expand_tokens(tokens: set[str]) -> set[str]:
    expanded = set(tokens)
    for group in SYNONYM_GROUPS:
        if tokens & group:
            expanded.update(group)
    return expanded


def _confirmed_values(state: DialogueState) -> list[PreferenceValue]:
    values = [
        state.category,
        *state.hard_constraints.values(),
        *state.soft_constraints.values(),
        *(
            state.subjective_needs.get_facet(facet)
            for facet in (
                "subjective_property",
                "event",
                "activity",
                "goal_purpose",
                "goal_audience",
            )
        ),
    ]
    return [value for value in values if value is not None and value.status == "confirmed"]


def _unmapped_ids(state: DialogueState) -> list[str]:
    mapped_or_deliberate = set(PREFERENCE_ATTRIBUTES) | NON_RANKING_IDS
    return sorted(
        {
            value.canonical_id
            for value in _confirmed_values(state)
            if not value.canonical_id.startswith("category_")
            and value.canonical_id not in mapped_or_deliberate
        }
    )


def generate_query(state: DialogueState) -> RecommendationQuery:
    parts = [state.category.value_text if state.category else "전자제품"]
    for facet in (
        state.subjective_needs.subjective_property,
        state.subjective_needs.goal_purpose,
    ):
        if facet is not None and facet.status == "confirmed":
            parts.append(facet.value_text)

    hard_filters = {
        key: value.value_text
        for key, value in state.hard_constraints.items()
        if value.status == "confirmed"
    }
    soft_signals = [
        value.value_text
        for value in state.soft_constraints.values()
        if value.status == "confirmed"
    ]
    parts.extend(hard_filters.values())
    parts.extend(soft_signals)
    return RecommendationQuery(
        text=" ".join(dict.fromkeys(parts)),
        category_id=state.category.canonical_id if state.category else None,
        hard_filters=hard_filters,
        soft_signals=soft_signals,
        unmapped_preference_ids=_unmapped_ids(state),
    )


def browse_catalog(query: RecommendationQuery, catalog: DemoCatalog) -> BrowseResult:
    products = [
        product
        for product in catalog.products
        if query.category_id is None
        or product.metadata.category_id == query.category_id
    ][:50]
    product_ids = {product.id for product in products}
    reviews = [review for review in catalog.reviews if review.product_id in product_ids]
    return BrowseResult(
        products=products,
        reviews=reviews,
        price_evidence=[f"{product.title}: {product.price:,}원" for product in products],
        delivery_evidence=[
            f"{product.title}: {product.shipping} / {product.stock}"
            for product in products
        ],
        images=[product.image for product in products],
        inventory_disclosure=catalog.disclosure,
    )


def _preference_token_map(state: DialogueState) -> dict[str, set[str]]:
    return {
        value.canonical_id: _expand_tokens(_tokens(value.value_text))
        for value in _confirmed_values(state)
        # 잔여 색상 수용은 독립 선호가 아니라 구매 시점 trade-off다.
        if value.canonical_id != "color_residual"
    }


def rank_reviews(
    state: DialogueState,
    products: list[Product],
    reviews: list[Review],
    *,
    top_k_per_product: int = 3,
) -> list[RankedReview]:
    product_by_id = {product.id: product for product in products}
    preference_tokens = _preference_token_map(state)
    ranked: list[RankedReview] = []
    for review in reviews:
        product = product_by_id.get(review.product_id)
        if product is None:
            continue
        evidence_tokens = _expand_tokens(
            _tokens(
                " ".join(
                    [
                        review.text,
                        product.title,
                        product.metadata.chipset,
                        product.metadata.storage,
                        *product.metadata.use_cases,
                    ]
                )
            )
        )
        matched_ids = [
            canonical_id
            for canonical_id, tokens in preference_tokens.items()
            if tokens & evidence_tokens
        ]
        match_count = len(matched_ids)
        keyword_count = len(preference_tokens)
        similarity = min(100, 20 + match_count * 20)
        coverage = min(
            100,
            round(
                match_count / max(1, min(keyword_count, 5)) * 100
                if keyword_count
                else 25
            ),
        )
        reliability = round((review.helpfulness or 0.5) * 100)
        total = round(0.65 * similarity + 0.25 * coverage + 0.10 * reliability)
        ranked.append(
            RankedReview(
                **review.model_dump(),
                similarity_score=similarity,
                preference_coverage_score=coverage,
                reliability_score=reliability,
                total_score=total,
                matched_preference_ids=matched_ids,
            )
        )

    grouped: dict[str, list[RankedReview]] = defaultdict(list)
    for review in ranked:
        grouped[review.product_id].append(review)
    limited: list[RankedReview] = []
    for product_id in sorted(grouped):
        limited.extend(
            sorted(
                grouped[product_id],
                key=lambda item: (-item.total_score, item.id),
            )[:top_k_per_product]
        )
    return limited


def _attribute_score(name: AttributeName, product: Product) -> int:
    metadata = product.metadata
    score_functions: dict[str, Callable[[], float]] = {
        "delivery_speed": lambda: 100 - metadata.delivery_days * 12,
        "performance": lambda: metadata.performance_tier * 20,
        "longevity": lambda: metadata.longevity_years / 6 * 100,
        "battery": lambda: (metadata.battery_hours - 18) / 20 * 100,
        "audio": lambda: metadata.speaker_tier * 20,
        "portability": lambda: (240 - metadata.weight_grams) / 80 * 100,
        "price_value": lambda: (2_100_000 - product.price) / 1_300_000 * 100,
        "storage": lambda: metadata.storage_gb / 256 * 100,
        "display": lambda: float(re.search(r"\d+(?:\.\d+)?", metadata.display).group())
        / 6.9
        * 100,
    }
    function = score_functions.get(name)
    return _clamp(function()) if function else 0


def _active_attributes(
    values: Iterable[PreferenceValue | None],
) -> tuple[list[AttributeName], list[str]]:
    attributes: list[str] = []
    preference_ids: list[str] = []
    for value in values:
        if value is None or value.status != "confirmed":
            continue
        mapped = PREFERENCE_ATTRIBUTES.get(value.canonical_id, ())
        if not mapped:
            continue
        attributes.extend(mapped)
        preference_ids.append(value.canonical_id)
    return list(dict.fromkeys(attributes)), list(dict.fromkeys(preference_ids))


def _number_from_value(value: PreferenceValue | None, *, unit: str) -> int | None:
    if value is None or value.status != "confirmed":
        return None
    text = value.value_text.replace(",", "")
    if unit == "won":
        manwon = re.search(r"(\d+(?:\.\d+)?)\s*만", text)
        if manwon:
            return round(float(manwon.group(1)) * 10_000)
    if unit == "days":
        weeks = re.search(r"(\d+)\s*주", text)
        if weeks:
            return int(weeks.group(1)) * 7
    match = re.search(r"\d+", text)
    return int(match.group()) if match else None


def rank_products(
    state: DialogueState,
    browse_result: BrowseResult,
) -> tuple[list[RankedProduct], list[RankedReview], list[str]]:
    rejected = {item.product_id for item in state.rejected_items}
    candidates = [
        product for product in browse_result.products if product.id not in rejected
    ]
    candidate_ids = {product.id for product in candidates}
    candidate_reviews = [
        review
        for review in browse_result.reviews
        if review.product_id in candidate_ids
    ]
    ranked_reviews = rank_reviews(state, candidates, candidate_reviews)
    reviews_by_product: dict[str, list[RankedReview]] = defaultdict(list)
    for review in ranked_reviews:
        reviews_by_product[review.product_id].append(review)

    maximum_review_count = max((product.review_count for product in candidates), default=1)
    budget = _number_from_value(state.hard_constraints.get("budget"), unit="won")
    deadline = _number_from_value(
        state.hard_constraints.get("delivery_deadline"), unit="days"
    )
    storage = _number_from_value(
        state.hard_constraints.get("storage_capacity"), unit="gb"
    )
    flexible = (
        state.soft_constraints.get("budget_flexibility") is not None
        and state.soft_constraints["budget_flexibility"].status == "confirmed"
    )
    constraint_attributes, constraint_ids = _active_attributes(
        [*state.hard_constraints.values(), *state.soft_constraints.values()]
    )
    need_attributes, need_ids = _active_attributes(
        [
            state.subjective_needs.subjective_property,
            state.subjective_needs.event,
            state.subjective_needs.activity,
            state.subjective_needs.goal_purpose,
            state.subjective_needs.goal_audience,
        ]
    )

    unranked: list[RankedProduct] = []
    for product in candidates:
        hard_checks: list[float] = []
        if budget:
            hard_checks.append(
                100
                if product.price <= budget
                else _clamp(100 - (product.price - budget) / budget * 250)
                if flexible
                else 0
            )
        if deadline:
            hard_checks.append(100 if product.metadata.delivery_days <= deadline else 0)
        if storage:
            hard_checks.append(100 if product.metadata.storage_gb >= storage else 0)
        hard_match = _clamp(_average(hard_checks)) if hard_checks else 70

        metadata_match = (
            _clamp(
                _average(
                    _attribute_score(name, product)
                    for name in constraint_attributes
                )
            )
            if constraint_attributes
            else 65
        )
        need_match = (
            _clamp(
                _average(
                    _attribute_score(name, product) for name in need_attributes
                )
            )
            if need_attributes
            else 55
        )
        evidence_reviews = sorted(
            reviews_by_product.get(product.id, []),
            key=lambda item: (-item.total_score, item.id),
        )[:3]
        review_score = (
            _average(review.total_score for review in evidence_reviews)
            if evidence_reviews
            else 0
        )
        helpfulness = (
            _average(review.reliability_score for review in evidence_reviews)
            if evidence_reviews
            else 0
        )
        count_reliability = (
            math.log1p(product.review_count)
            / math.log1p(maximum_review_count)
            * 100
        )
        reliability = _clamp(0.7 * helpfulness + 0.3 * count_reliability)
        total = _clamp(
            0.30 * hard_match
            + 0.30 * metadata_match
            + 0.20 * need_match
            + 0.15 * review_score
            + 0.05 * reliability
        )
        matched_ids = list(
            dict.fromkeys(
                [
                    *(["budget"] if budget else []),
                    *(["delivery_deadline"] if deadline else []),
                    *(["storage_capacity"] if storage else []),
                    *constraint_ids,
                    *need_ids,
                ]
            )
        )
        unranked.append(
            RankedProduct(
                product_id=product.id,
                rank=0,
                score=ScoreBreakdown(
                    hard_constraint_match=hard_match,
                    metadata_match=metadata_match,
                    subjective_need_match=need_match,
                    review_evidence_score=_clamp(review_score),
                    evidence_reliability=reliability,
                    total=total,
                ),
                evidence_review_ids=[review.id for review in evidence_reviews],
                matched_preference_ids=matched_ids,
            )
        )

    ordered = sorted(unranked, key=lambda item: (-item.score.total, item.product_id))
    ranked_products = [
        item.model_copy(update={"rank": index})
        for index, item in enumerate(ordered, start=1)
    ]
    used_review_ids = {
        review_id for item in ranked_products for review_id in item.evidence_review_ids
    }
    review_evidence = [
        review for review in ranked_reviews if review.id in used_review_ids
    ]
    return ranked_products, review_evidence, _unmapped_ids(state)


def create_recommendation_response(
    state: DialogueState,
    ranked_products: list[RankedProduct],
    review_evidence: list[RankedReview],
    unmapped_preference_ids: list[str],
) -> RecommendationResponse:
    top = ranked_products[0] if ranked_products else None
    explanation = (
        "후보가 없습니다. 카테고리·재고·거절 조건을 다시 확인해야 합니다."
        if top is None
        else (
            f"{top.product_id}가 총점 {top.score.total}점으로 1위입니다. "
            f"하드 제약 {top.score.hard_constraint_match}, 메타데이터 "
            f"{top.score.metadata_match}, 주관 요구 {top.score.subjective_need_match}, "
            f"리뷰 근거 {top.score.review_evidence_score}를 결정론적으로 결합했습니다."
        )
    )
    return RecommendationResponse(
        updated_dialogue_state=state,
        ranked_products=ranked_products,
        review_evidence=review_evidence,
        explanation=explanation,
        unresolved_preferences=unresolved_preferences(state),
        unmapped_preference_ids=unmapped_preference_ids,
    )
