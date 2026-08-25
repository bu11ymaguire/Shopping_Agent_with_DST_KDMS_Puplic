"""로컬 Amazon 실데이터 experimental catalog와 FastAPI 경로를 검증한다."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.api import create_app  # noqa: E402
from app.experimental_catalog import (  # noqa: E402
    ExperimentalAmazonCatalog,
    ExperimentalCatalogUnavailableError,
)
from app.service import build_demo_test_service  # noqa: E402


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def verify_store(store: ExperimentalAmazonCatalog) -> str:
    status = store.status()
    check("experimental catalog 사용 가능", status.available)
    check("목표 상품 수 100~300", 100 <= status.product_count <= 300, status.product_count)
    check("실제 리뷰 포함", status.review_count >= 2000, status.review_count)
    check("고정 dataset revision", len(status.dataset_revision or "") == 40)

    all_products = store.search_products(limit=10)
    check("manifest 상품 수와 검색 total 일치", all_products.total == status.product_count)
    check(
        "가격 누락값을 보간하지 않음",
        all_products.coverage["price_usd"] < all_products.total,
        all_products.coverage,
    )
    check(
        "무게 누락값을 보간하지 않음",
        all_products.coverage["weight_grams"] < all_products.total,
        all_products.coverage,
    )

    filtered = store.search_products(
        max_price_usd=300,
        min_storage_gb=32,
        sort_by="price_value",
        limit=20,
    )
    check("구조화 필터 결과 존재", bool(filtered.products), filtered.total)
    check(
        "가격·용량 필터 준수",
        all(
            product.price_usd is not None
            and product.price_usd <= 300
            and product.storage_gb is not None
            and product.storage_gb >= 32
            for product in filtered.products
        ),
    )
    price_scores = [
        product.attribute_scores.price_value for product in filtered.products
    ]
    check(
        "표본 분위수 점수로 정렬",
        price_scores == sorted(price_scores, reverse=True),
        price_scores,
    )

    product = filtered.products[0]
    detail = store.get_product(product.parent_asin)
    check("상세 조회 ID 일치", detail.parent_asin == product.parent_asin)
    check("분류 provenance 제공", bool(detail.classification))
    check("속성 추출 근거 제공", bool(detail.attribute_evidence))

    reviews = store.search_reviews(
        product.parent_asin,
        verified_only=True,
        limit=10,
    )
    check("실제 리뷰 top-k 제공", bool(reviews.reviews))
    check("리뷰 limit 준수", len(reviews.reviews) <= 10)
    check(
        "상품·verified·source 필터 준수",
        all(
            review.parent_asin == product.parent_asin
            and review.verified_purchase
            and review.source == "amazon_reviews_2023"
            for review in reviews.reviews
        ),
    )
    check(
        "응답 리뷰 ID 중복 없음",
        len({review.review_id for review in reviews.reviews}) == len(reviews.reviews),
    )
    return product.parent_asin


def verify_api(store: ExperimentalAmazonCatalog, parent_asin: str) -> None:
    app = create_app(build_demo_test_service(), store)
    with TestClient(app) as client:
        status = client.get("/api/experimental/catalog/status")
        check("experimental status API", status.status_code == 200, status.text[:200])
        products = client.get(
            "/api/experimental/catalog/products",
            params={
                "max_price_usd": 300,
                "min_storage_gb": 32,
                "sort_by": "price_value",
                "limit": 5,
            },
        )
        check("experimental products API", products.status_code == 200, products.text[:200])
        check("products API top-k", len(products.json()["products"]) == 5)
        detail = client.get(f"/api/experimental/catalog/products/{parent_asin}")
        check("experimental detail API", detail.status_code == 200, detail.text[:200])
        reviews = client.get(
            f"/api/experimental/catalog/products/{parent_asin}/reviews",
            params={"verified_only": True, "limit": 5},
        )
        check("experimental reviews API", reviews.status_code == 200, reviews.text[:200])
        missing = client.get("/api/experimental/catalog/products/NOT-A-PRODUCT")
        check("없는 실데이터 상품 404", missing.status_code == 404)
        invalid_limit = client.get(
            "/api/experimental/catalog/products", params={"limit": 51}
        )
        check("과도한 top-k 요청 거부", invalid_limit.status_code == 422)


def verify_absent_data_isolated() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = ExperimentalAmazonCatalog(directory)
        check("실데이터 파일 부재를 status로 격리", not store.status().available)
        check(
            "status에서 로컬 절대 경로를 노출하지 않음",
            directory not in (store.status().unavailable_reason or ""),
        )
        try:
            store.search_products()
        except ExperimentalCatalogUnavailableError:
            pass
        else:
            raise AssertionError("실데이터 파일이 없는데 검색이 성공했습니다.")


def main() -> None:
    store = ExperimentalAmazonCatalog()
    parent_asin = verify_store(store)
    verify_api(store, parent_asin)
    verify_absent_data_isolated()
    print("\nAmazon Reviews 2023 experimental catalog 검증 통과")


if __name__ == "__main__":
    main()
