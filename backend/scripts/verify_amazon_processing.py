"""Amazon Reviews 2023 정규화 규칙의 오프라인 회귀 검증."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.data.amazon_reviews import (  # noqa: E402
    classify_tablet_metadata,
    compute_quantile_scores,
    deduplicate_review_groups,
    normalize_metadata,
    normalize_review,
    select_balanced_reviews,
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[PASS] {label}")


def main() -> None:
    device = {
        "parent_asin": "B-DEVICE",
        "title": "Acme Android Tablet 11 inch 8GB RAM 256GB Storage with Stylus",
        "main_category": "Computers",
        "categories": ["Electronics", "Computers & Tablets", "Tablets"],
        "average_rating": 4.4,
        "rating_number": 120,
        "price": 329.99,
        "features": ["Portable tablet computer"],
        "description": ["Android tablet for work and video"],
        "details": '{"Brand":"Acme","Item Weight":"1.1 pounds","Operating System":"Android 14"}',
        "images": [{"variant": "MAIN", "large": "https://example.test/tablet.jpg"}],
        "store": "Acme",
    }
    accessory = {
        **device,
        "parent_asin": "B-CASE",
        "title": "Protective Case for Acme Android Tablet 11 inch",
        "categories": ["Electronics", "Tablet Accessories", "Cases"],
    }
    laptop = {
        **device,
        "parent_asin": "B-LAPTOP",
        "title": "Dell Laptop 2TB 17 inch compatible with iPad",
        "categories": ["Electronics", "Computers & Tablets", "Laptops"],
    }
    writing_pad = {
        **device,
        "parent_asin": "B-PAD",
        "title": "Reusable Writing Pad Digital Drawing Tablet with Stylus",
        "categories": ["Electronics", "Computers & Accessories"],
    }
    category_mislabeled = {
        **device,
        "parent_asin": "B-HOTSPOT",
        "title": "FreedomPop Novatel MiFi 500 4G LTE",
        "categories": ["Electronics", "Computers & Tablets", "Tablets"],
    }
    macbook_with_ipad = {
        **device,
        "parent_asin": "B-MACBOOK",
        "title": "Apple MacBook Air Laptop with M2, works with iPhone and iPad",
        "categories": [],
    }
    check("태블릿 본체 후보 분류", classify_tablet_metadata(device)["is_tablet_candidate"])
    check("태블릿 액세서리 제외", not classify_tablet_metadata(accessory)["is_tablet_candidate"])
    check("Computers & Tablets 하위 노트북 제외", not classify_tablet_metadata(laptop)["is_tablet_candidate"])
    check("LCD writing/drawing tablet 제외", not classify_tablet_metadata(writing_pad)["is_tablet_candidate"])
    check("원본 category 오분류도 title 신호로 제외", not classify_tablet_metadata(category_mislabeled)["is_tablet_candidate"])
    check("iPad 호환 문구가 있는 MacBook 제외", not classify_tablet_metadata(macbook_with_ipad)["is_tablet_candidate"])
    product = normalize_metadata(device)
    check("저장 용량 추출", product["storage_gb"] == 256)
    check("메모리 용량 추출", product["memory_gb"] == 8)
    check("화면 크기 추출", product["screen_inches"] == 11)
    check("무게 단위 변환", product["weight_grams"] == 499)
    check("운영체제와 stylus 근거", product["operating_system"] == "Android 14" and product["stylus_mentioned"])
    arrow_image_product = normalize_metadata(
        {
            **device,
            "images": {
                "variant": ["LEFT", "MAIN"],
                "large": ["https://example.test/left.jpg", "https://example.test/main.jpg"],
            },
        }
    )
    check("Arrow struct-of-lists MAIN 이미지 추출", arrow_image_product["image_url"].endswith("main.jpg"))
    expandable = normalize_metadata(
        {
            **device,
            "title": "Android Tablet 10 inch 6GB RAM 64GB ROM 512GB Expandable",
        }
    )
    check("확장 가능 용량을 내부 저장공간에서 제외", expandable["storage_gb"] == 64)
    parent_variant = normalize_metadata(
        {
            **device,
            "title": "Apple iPad 64GB Wi-Fi",
            "features": ["Choose from 64GB or 256GB storage options"],
            "details": '{"Memory Storage Capacity":"64 GB"}',
        }
    )
    check("title 용량을 parent variant 설명보다 우선", parent_variant["storage_gb"] == 64)
    check("Memory Storage Capacity를 RAM으로 오인하지 않음", parent_variant["memory_gb"] is None)
    duplicated_ram_storage = normalize_metadata(
        {
            **device,
            "title": "Apple iPad Wi-Fi 32GB",
            "features": ["9.7 inch tablet"],
            "description": [],
            "details": '{"RAM":"32 GB","Hard Drive":"32 GB Flash Memory","Memory Storage Capacity":"32 GB"}',
        }
    )
    check(
        "details RAM이 storage와 중복되면 독립 근거 없이 RAM으로 쓰지 않음",
        duplicated_ram_storage["storage_gb"] == 32
        and duplicated_ram_storage["memory_gb"] is None,
    )
    invalid_weight = normalize_metadata(
        {
            **device,
            "details": '{"Brand":"Acme","Item Weight":"3.8 ounces"}',
        }
    )
    check("비현실적인 태블릿 무게는 결측 처리", invalid_weight["weight_grams"] is None)
    fire = normalize_metadata(
        {
            **device,
            "title": 'Fire HD 8 Tablet (8" HD Display, 16 GB)',
            "details": '{"Product Dimensions":"8.4 x 5 x 0.4 inches; 12.8 Ounces"}',
        }
    )
    check("따옴표 화면 크기 추출", fire["screen_inches"] == 8)
    check("Product Dimensions의 무게 추출", fire["weight_grams"] == 363)

    rows = [
        {
            "parent_asin": "B-DEVICE",
            "asin": f"ASIN-{index}",
            "rating": rating,
            "title": f"review {index}",
            "text": ("actual review body with enough information " * 2) + str(index),
            "sort_timestamp": 1_700_000_000_000 + index,
            "verified_purchase": index % 2 == 0,
            "helpful_votes": index,
            "user_id": f"USER-{index}",
        }
        for index, rating in enumerate([1, 2, 3, 4, 5, 5, 4, 1, 5, 3])
    ]
    normalized = [normalize_review(row) for row in rows]
    check("실제 review 대체 필드명 수용", all(normalized))
    check("processed review에서 user_id 제거", all("user_id" not in row for row in normalized if row))
    selected = select_balanced_reviews((row for row in normalized if row), limit=8)
    buckets = {row["sentiment_bucket"] for row in selected}
    check("긍정·중립·부정 리뷰 균형", buckets == {"negative", "neutral", "positive"})
    check("top-k 제한", len(selected) == 8)
    quantile_products = [
        {
            "parent_asin": "cheap-light",
            "price_usd": 100,
            "weight_grams": 300,
            "storage_gb": 64,
            "memory_gb": 4,
            "screen_inches": 8,
            "stylus_mentioned": False,
        },
        {
            "parent_asin": "mid",
            "price_usd": 200,
            "weight_grams": None,
            "storage_gb": 128,
            "memory_gb": 8,
            "screen_inches": 10,
            "stylus_mentioned": True,
        },
        {
            "parent_asin": "expensive-heavy",
            "price_usd": 300,
            "weight_grams": 900,
            "storage_gb": 256,
            "memory_gb": 16,
            "screen_inches": 13,
            "stylus_mentioned": False,
        },
    ]
    profile, scores = compute_quantile_scores(quantile_products)
    check("가격·무게는 낮을수록 높은 분위수 점수", scores["cheap-light"]["price_value"] == 100 and scores["cheap-light"]["portability"] == 100)
    check("저장공간은 높을수록 높은 분위수 점수", scores["expensive-heavy"]["storage"] == 100)
    check("결측 분위수는 평균 대체하지 않음", scores["mid"]["portability"] is None)
    check("분위수 profile 결측 수 기록", profile["portability"]["missing_count"] == 1)
    check("stylus 신호는 note-taking 속성으로 분리", scores["mid"]["note_taking"] == 100)
    duplicate_text = "same actual review text with enough information"
    grouped = {
        "popular": [
            {"review_id": "a1", "text": duplicate_text},
            {"review_id": "a2", "text": "popular unique review"},
        ],
        "other": [
            {"review_id": "b1", "text": duplicate_text},
            {"review_id": "b2", "text": "other unique one"},
            {"review_id": "b3", "text": "other unique two"},
        ],
    }
    deduplicated = deduplicate_review_groups(
        grouped,
        ordered_product_ids=["popular", "other"],
        minimum_count=2,
    )
    check("전역 리뷰 본문 중복 제거", [item["review_id"] for item in deduplicated["other"]] == ["b2", "b3"])
    check("전역 dedup 후 상품별 최소 리뷰 유지", all(len(items) >= 2 for items in deduplicated.values()))
    print("\nAmazon Reviews 2023 가공 규칙 검증 통과")


if __name__ == "__main__":
    main()
