"""Amazon Reviews 2023 Electronics 표본의 결정론적 정규화 규칙."""

from __future__ import annotations

import hashlib
import json
import math
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from typing import Any

SPACE_RE = re.compile(r"\s+")
TABLET_TITLE_RE = re.compile(
    r"(?:^(?:[\w+.-]+\s+){0,4}(?:android\s+|windows\s+|kids?\s+)?tablet\b|"
    r"\b(?:apple\s+[^,]{0,30}\s)?ipad(?:\s+(?:air|pro|mini))?\b|"
    r"\bgalaxy\s+tab\b|\bpixel\s+slate\b|\bsurface\s+(?:go|pro)\b|"
    r"\bkindle\s+fire\b|\bfire\s+hd\s+\d+[^,]{0,40}\btablet\b)",
    re.IGNORECASE,
)
ACCESSORY_RE = re.compile(
    r"\b(?:case\s+for|cover\s+for|screen\s+protector|keyboard\s+case|"
    r"tablet\s+(?:stand|holder|mount|case|cover|sleeve|bag)|replacement\s+(?:screen|battery)|"
    r"charger|charging\s+cable|digitizer|lcd\s+screen|stylus\s+(?:pen\s+)?for|"
    r"compatible\s+with\s+[^,]{0,80}(?:ipad|tablet)|reusable\s+writing\s+pad|"
    r"drawing\s+tablet|graphics\s+tablet|protection\s+plan|teleprompter|"
    r"(?:mouse|earbuds?|earphones?|headphones?|speakers?|sound\s+bar|adapter|cable|"
    r"card\s+reader|digital\s+pen|sticker|decal)\s+(?:for|with))\b",
    re.IGNORECASE,
)
ACCESSORY_CATEGORY_RE = re.compile(
    r"\b(?:tablet accessories|cases|covers|screen protectors|stands|mounts|chargers)\b",
    re.IGNORECASE,
)
PRIMARY_NON_TABLET_RE = re.compile(
    r"\b(?:macbook|chromebook)\b|^(?:[\w+.-]+\s+){0,4}(?:laptop|notebook)\b",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return SPACE_RE.sub(" ", value).strip()
    if isinstance(value, Mapping):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, Iterable):
        return " ".join(_text(item) for item in value)
    return str(value)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [cleaned for item in value if (cleaned := _text(item))]


def _details(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, Mapping):
            return {str(key): item for key, item in parsed.items()}
    return {}


def classify_tablet_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    """태블릿 본체 후보와 액세서리 신호를 분리한다."""

    title = _text(row.get("title"))
    categories = _string_list(row.get("categories"))
    category_text = " > ".join(categories)
    exact_device_category = any(
        category.casefold().strip() in {"tablets", "tablet computers"}
        for category in categories
    )
    leaf_category = categories[-1].casefold().strip() if categories else ""
    category_allows_title_fallback = not categories or leaf_category in {
        "computers & tablets",
        "tablet computers",
        "tablets",
    }
    category_tablet_signal = any("tablet" in category.casefold() for category in categories)
    title_tablet_signal = bool(TABLET_TITLE_RE.search(title))
    accessory_signal = bool(
        ACCESSORY_RE.search(title) or ACCESSORY_CATEGORY_RE.search(category_text)
    )
    competing_device_signal = bool(PRIMARY_NON_TABLET_RE.search(title))
    is_candidate = bool(
        not accessory_signal
        and not competing_device_signal
        and title_tablet_signal
        and (exact_device_category or category_allows_title_fallback)
    )
    return {
        "is_tablet_candidate": is_candidate,
        "exact_device_category": exact_device_category,
        "category_tablet_signal": category_tablet_signal,
        "title_tablet_signal": title_tablet_signal,
        "category_allows_title_fallback": category_allows_title_fallback,
        "accessory_signal": accessory_signal,
        "competing_device_signal": competing_device_signal,
        "classification_evidence": {
            "title": title,
            "categories": categories,
        },
    }


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _extract_capacity_gb(
    text: str,
    label: str,
    *,
    prefer_smallest: bool = False,
    exclude_expansion: bool = False,
) -> int | None:
    patterns = (
        rf"\b(\d+(?:\.\d+)?)\s*(TB|GB)\s*(?:of\s+)?{label}\b",
        rf"\b{label}\D{{0,24}}(\d+(?:\.\d+)?)\s*(TB|GB)\b",
    )
    values: list[int] = []
    for pattern_index, pattern in enumerate(patterns):
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            amount, unit = match.group(1), match.group(2)
            context = text[max(0, match.start() - 12) : match.end()]
            prefix = text[max(0, match.start() - 16) : match.start()]
            if pattern_index == 1 and re.search(
                r"\d+(?:\.\d+)?\s*(?:TB|GB)\s*$",
                prefix,
                flags=re.IGNORECASE,
            ):
                continue
            if exclude_expansion and re.search(
                r"\b(?:expand(?:able|ed)?|micro\s*sd|sd\s*card|up\s+to)\b",
                context,
                flags=re.IGNORECASE,
            ):
                continue
            value = float(amount) * (1024 if unit.casefold() == "tb" else 1)
            values.append(round(value))
    if not values:
        return None
    return min(values) if prefer_smallest else max(values)


def _extract_storage_gb(text: str, title: str) -> int | None:
    # Parent metadata의 details/features에는 다른 용량 variant가 섞일 수 있으므로
    # 사용자가 보는 title을 먼저 확정 근거로 사용한다.
    labeled = _extract_capacity_gb(
        title,
        r"(?:storage|rom|ssd|capacity)",
        exclude_expansion=True,
    )
    if labeled is not None:
        return labeled
    values: list[int] = []
    for match in re.finditer(r"\b(\d+(?:\.\d+)?)\s*(TB|GB)\b", title, re.IGNORECASE):
        context = title[max(0, match.start() - 20) : match.end() + 20]
        if re.search(
            r"\b(?:ram|memory|expand(?:able|ed)?|micro\s*sd|sd\s*card|up\s+to)\b",
            context,
            re.IGNORECASE,
        ):
            continue
        value = float(match.group(1)) * (1024 if match.group(2).casefold() == "tb" else 1)
        values.append(round(value))
    if values:
        return max(values)
    return _extract_capacity_gb(
        text,
        r"(?:storage|rom|ssd|capacity)",
        exclude_expansion=True,
    )


def _extract_memory_gb(text: str) -> int | None:
    """RAM으로 명시된 용량만 추출하고 `Memory Storage Capacity`는 제외한다."""

    label = (
        r"(?:ram(?:\s+memory)?(?:\s+installed\s+size)?|installed\s+ram|"
        r"system\s+memory)"
    )
    patterns = (
        rf"\b(\d+(?:\.\d+)?)\s*(TB|GB)\s*(?:of\s+)?{label}\b",
        rf"\b{label}\D{{0,24}}(\d+(?:\.\d+)?)\s*(TB|GB)\b",
    )
    values: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = float(match.group(1)) * (
                1024 if match.group(2).casefold() == "tb" else 1
            )
            rounded = round(value)
            if 1 <= rounded <= 64:
                values.append(rounded)
    return min(values) if values else None


def _extract_screen_inches(text: str) -> float | None:
    values = [
        float(value)
        for value in re.findall(
            r"\b(\d{1,2}(?:\.\d+)?)\s*(?:-|\s)?(?:inch(?:es)?|in\.|[\"″])",
            text,
            flags=re.IGNORECASE,
        )
    ]
    plausible = [value for value in values if 5 <= value <= 20]
    return plausible[0] if plausible else None


def _extract_weight_grams(details: Mapping[str, Any]) -> int | None:
    weight_text = " ".join(
        _text(value)
        for key, value in details.items()
        if "weight" in key.casefold() or "dimensions" in key.casefold()
    )
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(pounds?|lbs?|ounces?|oz|kilograms?|kg|grams?|g)\b",
        weight_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).casefold()
    if unit.startswith(("pound", "lb")):
        value *= 453.59237
    elif unit.startswith(("ounce", "oz")):
        value *= 28.349523
    elif unit.startswith(("kilogram", "kg")):
        value *= 1000
    rounded = round(value)
    # 110g처럼 명백한 metadata 오기나 단위 오류를 점수로 사용하지 않는다.
    return rounded if 150 <= rounded <= 2500 else None


def _main_image(images: Any) -> str | None:
    if isinstance(images, Mapping):
        variants = images.get("variant") if isinstance(images.get("variant"), list) else []
        preferred_index = next(
            (index for index, variant in enumerate(variants) if str(variant).upper() == "MAIN"),
            0,
        )
        for key in ("large", "hi_res", "thumb"):
            urls = images.get(key)
            if isinstance(urls, list) and urls:
                index = min(preferred_index, len(urls) - 1)
                if url := _text(urls[index]):
                    return url
    if not isinstance(images, list):
        return None
    mappings = [image for image in images if isinstance(image, Mapping)]
    mappings.sort(key=lambda item: 0 if str(item.get("variant", "")).upper() == "MAIN" else 1)
    for image in mappings:
        for key in ("large", "hi_res", "thumb"):
            if url := _text(image.get(key)):
                return url
    return None


def normalize_metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    """원본 메타데이터를 분석용 column schema로 변환한다."""

    details = _details(row.get("details"))
    title = _text(row.get("title"))
    features = _string_list(row.get("features"))
    description = _string_list(row.get("description"))
    categories = _string_list(row.get("categories"))
    descriptive_text = " ".join([title, *features, *description])
    details_text = _text(details)
    joined = " ".join([descriptive_text, details_text])
    classification = classify_tablet_metadata(row)
    brand = next(
        (
            _text(value)
            for key, value in details.items()
            if key.casefold() in {"brand", "manufacturer"} and _text(value)
        ),
        _text(row.get("store")) or None,
    )
    operating_system = next(
        (
            _text(value)
            for key, value in details.items()
            if "operating system" in key.casefold() and _text(value)
        ),
        None,
    )
    storage_gb = _extract_storage_gb(joined, title)
    descriptive_memory_gb = _extract_memory_gb(descriptive_text)
    details_memory_gb = _extract_memory_gb(details_text)
    memory_gb = descriptive_memory_gb or details_memory_gb
    # Amazon metadata can copy storage into the `RAM` field (for example a 32GB
    # iPad whose title and Hard Drive are also 32GB). If no independent title,
    # feature, or description RAM statement exists, an exact storage duplicate is
    # not reliable enough to rank and is left missing.
    if (
        descriptive_memory_gb is None
        and memory_gb is not None
        and memory_gb == storage_gb
    ):
        memory_gb = None
    screen_inches = _extract_screen_inches(joined)
    weight_grams = _extract_weight_grams(details)
    attribute_evidence = {
        "storage_gb": "title/features/description/details regex" if storage_gb else None,
        "memory_gb": (
            "title/features/description explicit RAM context"
            if descriptive_memory_gb is not None and memory_gb is not None
            else "details explicit RAM context"
            if memory_gb is not None
            else None
        ),
        "screen_inches": "title/features/description/details regex" if screen_inches else None,
        "weight_grams": "details weight conversion" if weight_grams else None,
        "stylus_mentioned": "title/features/description/details keyword",
    }
    price = _number(row.get("price"))
    rating = _number(row.get("average_rating"))
    rating_number = _number(row.get("rating_number"))
    return {
        "parent_asin": _text(row.get("parent_asin")),
        "title": title,
        "main_category": _text(row.get("main_category")),
        "categories": categories,
        "brand": brand,
        "store": _text(row.get("store")) or None,
        "price_usd": price,
        "average_rating": rating,
        "rating_number": round(rating_number) if rating_number is not None else 0,
        "features": features,
        "description": description,
        "image_url": _main_image(row.get("images")),
        "storage_gb": storage_gb,
        "memory_gb": memory_gb,
        "screen_inches": screen_inches,
        "weight_grams": weight_grams,
        "operating_system": operating_system,
        "stylus_mentioned": bool(re.search(r"\b(?:stylus|active pen|s pen|apple pencil)\b", joined, re.IGNORECASE)),
        "is_tablet_candidate": classification["is_tablet_candidate"],
        "classification_json": json.dumps(classification, ensure_ascii=False, sort_keys=True),
        "attribute_evidence_json": json.dumps(attribute_evidence, ensure_ascii=False, sort_keys=True),
        "source": "amazon_reviews_2023",
    }


def normalize_review(row: Mapping[str, Any], *, minimum_text_length: int = 40) -> dict[str, Any] | None:
    """서비스에 필요 없는 user_id를 버리고 실제 리뷰 근거만 정규화한다."""

    parent_asin = _text(row.get("parent_asin"))
    text = _text(row.get("text"))
    rating = _number(row.get("rating"))
    if not parent_asin or rating is None or len(text) < minimum_text_length:
        return None
    timestamp = row.get("timestamp", row.get("sort_timestamp"))
    helpful = row.get("helpful_vote", row.get("helpful_votes", 0))
    identity = "|".join(
        [
            parent_asin,
            _text(row.get("asin")),
            _text(timestamp),
            _text(row.get("title")),
            text,
        ]
    )
    review_id = f"ar23-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"
    sentiment = "negative" if rating <= 2 else "neutral" if rating == 3 else "positive"
    return {
        "review_id": review_id,
        "parent_asin": parent_asin,
        "asin": _text(row.get("asin")) or None,
        "rating": rating,
        "title": _text(row.get("title")),
        "text": text,
        "timestamp": int(timestamp) if timestamp is not None else None,
        "verified_purchase": bool(row.get("verified_purchase", False)),
        "helpful_vote": max(0, int(helpful or 0)),
        "sentiment_bucket": sentiment,
        "source": "amazon_reviews_2023",
    }


QUANTILE_ATTRIBUTES: dict[str, tuple[str, bool]] = {
    "price_value": ("price_usd", False),
    "portability": ("weight_grams", False),
    "storage": ("storage_gb", True),
    "memory": ("memory_gb", True),
    "display": ("screen_inches", True),
}


def _quantile(sorted_values: list[float], probability: float) -> float | None:
    if not sorted_values:
        return None
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _percentile_score(
    sorted_values: list[float],
    value: float,
    *,
    higher_is_better: bool,
) -> int:
    if len(sorted_values) <= 1:
        return 50
    left = bisect_left(sorted_values, value)
    right = bisect_right(sorted_values, value)
    average_rank = (left + right - 1) / 2
    percentile = average_rank / (len(sorted_values) - 1) * 100
    score = percentile if higher_is_better else 100 - percentile
    return max(0, min(100, round(score)))


def compute_quantile_scores(
    products: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, dict[str, int | None]]]:
    """선택된 실제 상품 분포에서 결측을 보존한 분위수 속성 점수를 만든다."""

    items = [dict(product) for product in products]
    distributions: dict[str, list[float]] = {}
    profile: dict[str, Any] = {}
    for attribute, (field, higher_is_better) in QUANTILE_ATTRIBUTES.items():
        values = sorted(
            number
            for item in items
            if (number := _number(item.get(field))) is not None
        )
        distributions[attribute] = values
        profile[attribute] = {
            "source_field": field,
            "higher_is_better": higher_is_better,
            "non_null_count": len(values),
            "missing_count": len(items) - len(values),
            "minimum": values[0] if values else None,
            "p05": _quantile(values, 0.05),
            "p25": _quantile(values, 0.25),
            "median": _quantile(values, 0.50),
            "p75": _quantile(values, 0.75),
            "p95": _quantile(values, 0.95),
            "maximum": values[-1] if values else None,
        }

    scores: dict[str, dict[str, int | None]] = {}
    for item in items:
        product_id = _text(item.get("parent_asin"))
        item_scores: dict[str, int | None] = {}
        for attribute, (field, higher_is_better) in QUANTILE_ATTRIBUTES.items():
            value = _number(item.get(field))
            values = distributions[attribute]
            item_scores[attribute] = (
                _percentile_score(
                    values,
                    value,
                    higher_is_better=higher_is_better,
                )
                if value is not None and values
                else None
            )
        item_scores["note_taking"] = 100 if item.get("stylus_mentioned") else 0
        scores[product_id] = item_scores
    return profile, scores


def _review_priority(review: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        -int(bool(review.get("verified_purchase"))),
        -int(review.get("helpful_vote") or 0),
        -min(len(_text(review.get("text"))), 2000),
        -int(review.get("timestamp") or 0),
        _text(review.get("review_id")),
    )


def select_balanced_reviews(
    reviews: Iterable[Mapping[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """검증 구매·도움표 우선순위와 평점 극성 균형으로 리뷰를 제한한다."""

    deduplicated: dict[str, dict[str, Any]] = {}
    for review in reviews:
        item = dict(review)
        key = SPACE_RE.sub(" ", _text(item.get("text"))).casefold()
        if not key:
            continue
        current = deduplicated.get(key)
        if current is None or _review_priority(item) < _review_priority(current):
            deduplicated[key] = item

    buckets = {
        label: sorted(
            (item for item in deduplicated.values() if item.get("sentiment_bucket") == label),
            key=_review_priority,
        )
        for label in ("negative", "neutral", "positive")
    }
    quotas = {
        "negative": round(limit * 0.25),
        "neutral": round(limit * 0.10),
        "positive": limit - round(limit * 0.25) - round(limit * 0.10),
    }
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for label in ("negative", "neutral", "positive"):
        for item in buckets[label][: quotas[label]]:
            selected.append(item)
            selected_ids.add(_text(item.get("review_id")))
    remaining = sorted(
        (
            item
            for item in deduplicated.values()
            if _text(item.get("review_id")) not in selected_ids
        ),
        key=_review_priority,
    )
    selected.extend(remaining[: max(0, limit - len(selected))])
    return sorted(selected[:limit], key=_review_priority)


def deduplicate_review_groups(
    groups: Mapping[str, list[dict[str, Any]]],
    *,
    ordered_product_ids: Iterable[str],
    minimum_count: int,
) -> dict[str, list[dict[str, Any]]]:
    """상품별 최소 수를 확인한 뒤 동일 본문을 전체 catalog에서 한 번만 남긴다."""

    seen_texts: set[str] = set()
    retained: dict[str, list[dict[str, Any]]] = {}
    for product_id in ordered_product_ids:
        unique: list[dict[str, Any]] = []
        local_seen: set[str] = set()
        for review in groups.get(product_id, []):
            key = SPACE_RE.sub(" ", _text(review.get("text"))).casefold()
            if not key or key in seen_texts or key in local_seen:
                continue
            local_seen.add(key)
            unique.append(review)
        if len(unique) < minimum_count:
            continue
        retained[product_id] = unique
        seen_texts.update(local_seen)
    return retained
