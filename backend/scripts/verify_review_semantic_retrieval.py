"""Smoke-check the pinned semantic review index and explicit token fallback."""

from __future__ import annotations

import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.review_retrieval import build_review_retriever  # noqa: E402


def _check(condition: bool, label: str, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail!r}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def main() -> None:
    catalog = ExperimentalAmazonCatalog()
    _check(catalog.available, "실제 catalog 사용 가능")
    products = catalog.search_products(sort_by="review_count", limit=12).products
    product_ids = [product.parent_asin for product in products]
    settings = load_review_retrieval_settings()

    started = time.perf_counter()
    result = build_review_retriever(settings).retrieve(
        catalog,
        product_ids,
        "long battery life fast charging and reliable performance",
    )
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    _check(
        result.method == "semantic_cross_encoder",
        "pinned semantic + Cross-Encoder 경로",
        result.method,
    )
    _check(result.fallback_reason is None, "semantic 경로에 fallback 없음")
    _check(bool(result.reviews), "실제 리뷰 검색 결과 존재", len(result.reviews))
    _check(
        len(result.reviews) <= len(product_ids) * settings.reranked_per_product,
        "상품별 최종 top-k 제한",
        len(result.reviews),
    )
    _check(
        all(review.retrieval_method == "semantic_cross_encoder" for review in result.reviews),
        "리뷰별 retrieval provenance",
    )
    _check(
        all(0 <= review.retrieval_score <= 100 for review in result.reviews),
        "semantic 점수 범위",
    )
    _check(
        all(review.source == "amazon_reviews_2023" for review in result.reviews),
        "원본 실제 리뷰만 반환",
    )

    with tempfile.TemporaryDirectory() as temporary:
        missing_settings = replace(settings, index_dir=Path(temporary))
        fallback = build_review_retriever(missing_settings).retrieve(
            catalog,
            product_ids[:2],
            "battery life",
        )
    _check(fallback.method == "token", "인덱스 부재 시 token fallback")
    _check(
        fallback.fallback_reason == "semantic_index_missing",
        "fallback 사유 계측",
        fallback.fallback_reason,
    )
    _check(
        all(review.retrieval_method == "token" for review in fallback.reviews),
        "fallback 리뷰 provenance",
    )

    print(
        json.dumps(
            {
                "status": "ok",
                "semantic_review_count": len(result.reviews),
                "cold_start_latency_ms": latency_ms,
                "top_reviews": [
                    {
                        "review_id": review.review_id,
                        "parent_asin": review.parent_asin,
                        "retrieval_score": review.retrieval_score,
                    }
                    for review in result.reviews[:5]
                ],
                "fallback_reason": fallback.fallback_reason,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
