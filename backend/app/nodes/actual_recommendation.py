"""Deterministic QUERY, ACT, review retrieval, and RANK for real tablet data."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable

from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models import DialogueState, PreferenceValue, RankedProduct, ScoreBreakdown
from app.models.actual_demo import (
    ActualBrowseSummary,
    ActualHardFilters,
    ActualRankedReview,
    ActualRecommendationQuery,
    ActualRecommendationResponse,
)
from app.models.experimental import ExperimentalProduct, ExperimentalReview
from app.nodes.actual_policy import actual_unresolved_preferences
from app.review_retrieval import ReviewEvidenceRetriever, TokenReviewRetriever

ACTUAL_DATA_DISCLOSURE = (
    "Products and review excerpts come from the pinned Amazon Reviews 2023 sample. "
    "Prices are recorded USD values; missing fields are not imputed. Delivery, stock, "
    "pickup, and available colors are not present and are not ranked."
)

ACTUAL_ATTRIBUTE_MAP: dict[str, tuple[str, ...]] = {
    "budget": ("price_value",),
    "price_value": ("price_value",),
    "storage_capacity": ("storage",),
    "memory_capacity": ("memory",),
    "max_weight": ("portability",),
    "portability": ("portability",),
    "note_taking": ("note_taking", "display", "portability"),
    "display": ("display",),
    "performance": ("memory",),
    "subjective_value": ("price_value",),
    "subjective_performance": ("memory",),
    "subjective_portability": ("portability",),
    "subjective_display_quality": ("display",),
    "goal_work_study": ("memory", "portability", "display"),
    "goal_entertainment": ("display",),
    "activity_note_taking": ("note_taking", "display", "portability"),
    "activity_gaming": ("memory", "display"),
    "activity_video": ("display",),
    "activity_reading": ("display", "portability"),
}

REVIEW_KEYWORDS: dict[str, set[str]] = {
    "battery": {"battery", "charge", "charging", "hours", "life"},
    "audio": {"audio", "speaker", "speakers", "sound", "volume"},
    "durability": {"durable", "durability", "build", "broke", "broken", "quality"},
    "performance": {"performance", "fast", "slow", "lag", "responsive", "speed"},
    "portability": {"portable", "light", "lightweight", "heavy", "weight"},
    "note_taking": {"note", "notes", "writing", "stylus", "pen", "pencil"},
    "display": {"display", "screen", "bright", "resolution", "video"},
    "review_signal": {"recommend", "reliable", "issue", "problem", "experience"},
    "child_friendly": {"child", "children", "kid", "kids", "parental", "school"},
    "activity_note_taking": {"note", "notes", "writing", "stylus", "pen"},
    "activity_gaming": {"game", "games", "gaming", "lag", "performance"},
    "activity_video": {"video", "streaming", "movie", "screen", "sound"},
    "activity_reading": {"read", "reading", "book", "books", "screen"},
    "goal_work_study": {"work", "school", "study", "document", "productivity"},
    "goal_entertainment": {"movie", "video", "game", "streaming", "entertainment"},
    "audience_child": {"child", "children", "kid", "kids", "parental"},
}

KNOWN_NON_RANKING_IDS = {
    "category_tablet",
    "budget_flexibility",
    "min_rating",
    "operating_system",
    "event_device_failure",
    "event_first_purchase",
    "event_replacement",
    "event_gift",
    "activity_general",
    "goal_replace_device",
    "audience_self",
    "audience_family",
    "audience_other",
    "subjective_longevity",
    "subjective_audio_quality",
    "subjective_durability",
}


def _clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def _average(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _confirmed_values(state: DialogueState) -> list[PreferenceValue]:
    values = [
        state.category,
        *state.hard_constraints.values(),
        *state.soft_constraints.values(),
        state.subjective_needs.subjective_property,
        state.subjective_needs.event,
        state.subjective_needs.activity,
        state.subjective_needs.goal_purpose,
        state.subjective_needs.goal_audience,
    ]
    return [value for value in values if value and value.status == "confirmed"]


def _number(value: PreferenceValue | None) -> float | None:
    if value is None or value.status != "confirmed":
        return None
    match = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", value.value_text)
    return float(match.group().replace(",", "")) if match else None


def _weight_grams(value: PreferenceValue | None) -> int | None:
    amount = _number(value)
    if amount is None or value is None:
        return None
    text = value.value_text.casefold()
    if re.search(r"\b(?:lb|lbs|pound|pounds)\b", text):
        amount *= 453.59237
    elif re.search(r"\b(?:kg|kilogram|kilograms)\b", text):
        amount *= 1000
    elif re.search(r"\b(?:oz|ounce|ounces)\b", text):
        amount *= 28.349523
    return round(amount)


def _operating_system(value: PreferenceValue | None) -> str | None:
    if value is None or value.status != "confirmed":
        return None
    text = value.value_text.casefold()
    for label in ("android", "ipados", "fire os", "windows", "chrome os"):
        if label in text:
            return label
    return value.value_text.strip() or None


def _unmapped_ids(state: DialogueState) -> list[str]:
    known = set(ACTUAL_ATTRIBUTE_MAP) | set(REVIEW_KEYWORDS) | KNOWN_NON_RANKING_IDS
    return sorted(
        {
            value.canonical_id
            for value in _confirmed_values(state)
            if value.canonical_id not in known
        }
    )


def generate_actual_query(state: DialogueState) -> ActualRecommendationQuery:
    hard = ActualHardFilters(
        max_price_usd=_number(state.hard_constraints.get("budget")),
        min_storage_gb=(
            round(value)
            if (value := _number(state.hard_constraints.get("storage_capacity")))
            is not None
            else None
        ),
        min_memory_gb=(
            round(value)
            if (value := _number(state.hard_constraints.get("memory_capacity")))
            is not None
            else None
        ),
        max_weight_grams=_weight_grams(state.hard_constraints.get("max_weight")),
        min_rating=_number(state.hard_constraints.get("min_rating")),
        min_screen_inches=_number(state.hard_constraints.get("display")),
        operating_system=_operating_system(
            state.hard_constraints.get("operating_system")
        ),
    )
    confirmed = _confirmed_values(state)
    active_ids = list(dict.fromkeys(value.canonical_id for value in confirmed))
    review_terms: list[str] = []
    for canonical_id in active_ids:
        review_terms.extend(sorted(REVIEW_KEYWORDS.get(canonical_id, ())))
    soft_signals = [
        value.value_text
        for value in [
            *state.soft_constraints.values(),
            state.subjective_needs.subjective_property,
            state.subjective_needs.activity,
            state.subjective_needs.goal_purpose,
            state.subjective_needs.goal_audience,
        ]
        if value is not None and value.status == "confirmed"
    ]
    # Keyword expansions come first because the store intentionally caps query tokens.
    query_text = " ".join(dict.fromkeys([*review_terms, *soft_signals]))
    flexible = (
        (value := state.soft_constraints.get("budget_flexibility")) is not None
        and value.status == "confirmed"
    )
    return ActualRecommendationQuery(
        text=query_text,
        hard_filters=hard,
        allow_budget_overrun=flexible,
        soft_signals=soft_signals,
        active_preference_ids=active_ids,
        unmapped_preference_ids=_unmapped_ids(state),
    )


def browse_actual_catalog(
    query: ActualRecommendationQuery,
    catalog: ExperimentalAmazonCatalog,
    *,
    rejected_product_ids: set[str],
    review_retriever: ReviewEvidenceRetriever | None = None,
) -> tuple[list[ExperimentalProduct], list[ExperimentalReview], ActualBrowseSummary]:
    filters = query.hard_filters
    result = catalog.search_products(
        max_price_usd=(
            None if query.allow_budget_overrun else filters.max_price_usd
        ),
        min_storage_gb=filters.min_storage_gb,
        min_memory_gb=filters.min_memory_gb,
        max_weight_grams=filters.max_weight_grams,
        min_rating=filters.min_rating,
        min_screen_inches=filters.min_screen_inches,
        operating_system=filters.operating_system,
        sort_by="review_count",
        limit=300,
    )
    products = [
        product
        for product in result.products
        if product.parent_asin not in rejected_product_ids
    ]
    retrieval = (review_retriever or TokenReviewRetriever()).retrieve(
        catalog,
        [product.parent_asin for product in products],
        query.text,
    )
    return products, retrieval.reviews, ActualBrowseSummary(
        candidate_product_count=len(products),
        retrieved_review_count=len(retrieval.reviews),
        review_retrieval_method=retrieval.method,
        review_retrieval_fallback_reason=retrieval.fallback_reason,
        coverage=result.coverage,
        data_disclosure=ACTUAL_DATA_DISCLOSURE,
    )


def _review_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def rank_actual_reviews(
    state: DialogueState,
    reviews: list[ExperimentalReview],
) -> list[ActualRankedReview]:
    active = {
        value.canonical_id: REVIEW_KEYWORDS[value.canonical_id]
        for value in _confirmed_values(state)
        if value.canonical_id in REVIEW_KEYWORDS
    }
    ranked: list[ActualRankedReview] = []
    for review in reviews:
        tokens = _review_tokens(f"{review.title or ''} {review.text}")
        matched = [
            canonical_id
            for canonical_id, keywords in active.items()
            if tokens & keywords
        ]
        if active:
            similarity = (
                review.retrieval_score
                if review.retrieval_method == "semantic_cross_encoder"
                else min(100, 20 + review.retrieval_score * 20)
            )
            coverage = _clamp(len(matched) / max(1, len(active)) * 100)
        else:
            similarity = _clamp(review.rating / 5 * 100)
            coverage = 25
        reliability = _clamp(
            (70 if review.verified_purchase else 35)
            + min(30, math.log1p(review.helpful_vote) * 10)
        )
        total = _clamp(0.65 * similarity + 0.25 * coverage + 0.10 * reliability)
        ranked.append(
            ActualRankedReview(
                **review.model_dump(),
                similarity_score=similarity,
                preference_coverage_score=coverage,
                reliability_score=reliability,
                total_score=total,
                matched_preference_ids=matched,
            )
        )
    return ranked


def _attribute_score(product: ExperimentalProduct, attribute: str) -> int:
    value = getattr(product.attribute_scores, attribute, None)
    return int(value) if value is not None else 0


def _active_attributes(
    values: Iterable[PreferenceValue | None],
) -> tuple[list[str], list[str]]:
    attributes: list[str] = []
    ids: list[str] = []
    for value in values:
        if value is None or value.status != "confirmed":
            continue
        mapped = ACTUAL_ATTRIBUTE_MAP.get(value.canonical_id, ())
        if mapped:
            attributes.extend(mapped)
            ids.append(value.canonical_id)
    return list(dict.fromkeys(attributes)), list(dict.fromkeys(ids))


def _hard_constraint_score(
    product: ExperimentalProduct,
    query: ActualRecommendationQuery,
) -> tuple[int, list[str]]:
    filters = query.hard_filters
    checks: list[float] = []
    matched: list[str] = []
    if filters.max_price_usd is not None:
        if product.price_usd is None:
            checks.append(0)
        elif product.price_usd <= filters.max_price_usd:
            checks.append(100)
        elif query.allow_budget_overrun:
            checks.append(
                _clamp(
                    100
                    - (product.price_usd - filters.max_price_usd)
                    / filters.max_price_usd
                    * 250
                )
            )
        else:
            checks.append(0)
        matched.append("budget")
    for field, threshold, canonical_id, comparator in (
        (product.storage_gb, filters.min_storage_gb, "storage_capacity", "min"),
        (product.memory_gb, filters.min_memory_gb, "memory_capacity", "min"),
        (product.weight_grams, filters.max_weight_grams, "max_weight", "max"),
        (product.average_rating, filters.min_rating, "min_rating", "min"),
        (product.screen_inches, filters.min_screen_inches, "display", "min"),
    ):
        if threshold is None:
            continue
        checks.append(
            100
            if field is not None
            and ((field >= threshold) if comparator == "min" else (field <= threshold))
            else 0
        )
        matched.append(canonical_id)
    if filters.operating_system:
        checks.append(
            100
            if product.operating_system
            and filters.operating_system.casefold() in product.operating_system.casefold()
            else 0
        )
        matched.append("operating_system")
    return (_clamp(_average(checks)) if checks else 70), matched


def rank_actual_products(
    state: DialogueState,
    query: ActualRecommendationQuery,
    products: list[ExperimentalProduct],
    reviews: list[ExperimentalReview],
    *,
    result_limit: int = 10,
) -> tuple[list[RankedProduct], list[ActualRankedReview]]:
    ranked_reviews = rank_actual_reviews(state, reviews)
    reviews_by_product: dict[str, list[ActualRankedReview]] = defaultdict(list)
    for review in ranked_reviews:
        reviews_by_product[review.parent_asin].append(review)

    constraint_attributes, constraint_ids = _active_attributes(
        [*state.hard_constraints.values(), *state.soft_constraints.values()]
    )
    need_attributes, need_ids = _active_attributes(
        [
            state.subjective_needs.subjective_property,
            state.subjective_needs.activity,
            state.subjective_needs.goal_purpose,
            state.subjective_needs.goal_audience,
        ]
    )
    maximum_review_count = max(
        (product.selected_review_count for product in products), default=1
    )
    unranked: list[RankedProduct] = []
    for product in products:
        hard_match, hard_ids = _hard_constraint_score(product, query)
        metadata_match = (
            _clamp(
                _average(
                    _attribute_score(product, attribute)
                    for attribute in constraint_attributes
                )
            )
            if constraint_attributes
            else 65
        )
        subjective_match = (
            _clamp(
                _average(
                    _attribute_score(product, attribute)
                    for attribute in need_attributes
                )
            )
            if need_attributes
            else 55
        )
        evidence = sorted(
            reviews_by_product.get(product.parent_asin, []),
            key=lambda item: (-item.total_score, item.review_id),
        )[:3]
        review_score = _clamp(_average(item.total_score for item in evidence))
        review_reliability = _average(item.reliability_score for item in evidence)
        count_reliability = (
            math.log1p(product.selected_review_count)
            / math.log1p(maximum_review_count)
            * 100
        )
        reliability = _clamp(0.7 * review_reliability + 0.3 * count_reliability)
        total = _clamp(
            0.30 * hard_match
            + 0.30 * metadata_match
            + 0.20 * subjective_match
            + 0.15 * review_score
            + 0.05 * reliability
        )
        matched_ids = list(
            dict.fromkeys(
                [
                    *hard_ids,
                    *constraint_ids,
                    *need_ids,
                    *(item for review in evidence for item in review.matched_preference_ids),
                ]
            )
        )
        unranked.append(
            RankedProduct(
                product_id=product.parent_asin,
                rank=0,
                score=ScoreBreakdown(
                    hard_constraint_match=hard_match,
                    metadata_match=metadata_match,
                    subjective_need_match=subjective_match,
                    review_evidence_score=review_score,
                    evidence_reliability=reliability,
                    total=total,
                ),
                evidence_review_ids=[item.review_id for item in evidence],
                matched_preference_ids=matched_ids,
            )
        )
    ordered = sorted(unranked, key=lambda item: (-item.score.total, item.product_id))
    rankings = [
        item.model_copy(update={"rank": rank})
        for rank, item in enumerate(ordered[:result_limit], start=1)
    ]
    used_review_ids = {
        review_id for ranking in rankings for review_id in ranking.evidence_review_ids
    }
    visible_reviews = [
        review for review in ranked_reviews if review.review_id in used_review_ids
    ]
    return rankings, visible_reviews


def create_actual_recommendation_response(
    state: DialogueState,
    rankings: list[RankedProduct],
    reviews: list[ActualRankedReview],
    query: ActualRecommendationQuery,
) -> ActualRecommendationResponse:
    top = rankings[0] if rankings else None
    explanation = (
        "No catalog item satisfies the confirmed hard filters."
        if top is None
        else (
            f"{top.product_id} ranks first at {top.score.total}: hard constraints "
            f"{top.score.hard_constraint_match}, metadata {top.score.metadata_match}, "
            f"subjective need {top.score.subjective_need_match}, and review evidence "
            f"{top.score.review_evidence_score}."
        )
    )
    return ActualRecommendationResponse(
        updated_dialogue_state=state,
        ranked_products=rankings,
        review_evidence=reviews,
        explanation=explanation,
        unresolved_preferences=actual_unresolved_preferences(state),
        unmapped_preference_ids=query.unmapped_preference_ids,
    )
