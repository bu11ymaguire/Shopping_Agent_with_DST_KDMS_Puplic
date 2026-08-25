"""Build an ignored annotation pool from token and semantic recommendation outputs."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.evaluation.actual_recommendation import (  # noqa: E402
    build_recommendation_eval_state,
    load_actual_recommendation_eval_dataset,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.nodes.actual_policy import select_actual_policy  # noqa: E402
from app.nodes.actual_recommendation import (  # noqa: E402
    browse_actual_catalog,
    generate_actual_query,
    rank_actual_products,
)
from app.review_retrieval import build_review_retriever  # noqa: E402

DATASET_PATH = BACKEND_ROOT / "data" / "actual_recommendation_eval_v1.json"
OUTPUT_PATH = BACKEND_ROOT / "reports" / "actual_recommendation_eval_pool_v1.json"


def _product_payload(catalog: ExperimentalAmazonCatalog, parent_asin: str) -> dict:
    product = catalog.get_product(parent_asin)
    return {
        "parent_asin": product.parent_asin,
        "title": product.title,
        "price_usd": product.price_usd,
        "average_rating": product.average_rating,
        "selected_review_count": product.selected_review_count,
        "storage_gb": product.storage_gb,
        "memory_gb": product.memory_gb,
        "screen_inches": product.screen_inches,
        "weight_grams": product.weight_grams,
        "operating_system": product.operating_system,
        "stylus_mentioned": product.stylus_mentioned,
        "attribute_scores": product.attribute_scores.model_dump(mode="json"),
        "features": product.features,
        "description": product.description,
    }


def main() -> None:
    dataset = load_actual_recommendation_eval_dataset(DATASET_PATH)
    catalog = ExperimentalAmazonCatalog()
    if not catalog.available:
        raise RuntimeError(catalog.status().unavailable_reason)
    settings = load_review_retrieval_settings()
    retrievers = {
        mode: build_review_retriever(replace(settings, mode=mode))
        for mode in ("token", "semantic")
    }

    product_cases: list[dict] = []
    for case in dataset.product_cases:
        state = build_recommendation_eval_state(case)
        policy = select_actual_policy(state)
        query = generate_actual_query(state)
        modes: dict[str, object] = {}
        pooled_product_ids: list[str] = []
        for mode, retriever in retrievers.items():
            products, reviews, summary = browse_actual_catalog(
                query,
                catalog,
                rejected_product_ids=set(),
                review_retriever=retriever,
            )
            rankings, visible_reviews = rank_actual_products(
                state,
                query,
                products,
                reviews,
            )
            pooled_product_ids.extend(item.product_id for item in rankings[:10])
            modes[mode] = {
                "browse_summary": summary.model_dump(mode="json"),
                "rankings": [item.model_dump(mode="json") for item in rankings],
                "visible_review_ids": [item.review_id for item in visible_reviews],
            }
        unique_pool = list(dict.fromkeys(pooled_product_ids))
        product_cases.append(
            {
                "case_id": case.id,
                "description": case.description,
                "policy_lane": policy.lane,
                "query": query.model_dump(mode="json"),
                "modes": modes,
                "product_pool": [
                    _product_payload(catalog, parent_asin)
                    for parent_asin in unique_pool
                ],
            }
        )

    review_cases: list[dict] = []
    for case in dataset.review_cases:
        modes: dict[str, object] = {}
        pooled_review_ids: list[str] = []
        reviews_by_id: dict[str, Any] = {}
        for mode, retriever in retrievers.items():
            result = retriever.retrieve(catalog, [case.parent_asin], case.query)
            modes[mode] = {
                "method": result.method,
                "fallback_reason": result.fallback_reason,
                "ranked_review_ids": [review.review_id for review in result.reviews],
                "retrieval_scores": {
                    review.review_id: review.retrieval_score
                    for review in result.reviews
                },
            }
            for review in result.reviews:
                pooled_review_ids.append(review.review_id)
                reviews_by_id[review.review_id] = review
        review_cases.append(
            {
                "case_id": case.id,
                "description": case.description,
                "query": case.query,
                "parent_asin": case.parent_asin,
                "product": _product_payload(catalog, case.parent_asin),
                "modes": modes,
                "review_pool": [
                    reviews_by_id[review_id].model_dump(mode="json")
                    for review_id in dict.fromkeys(pooled_review_ids)
                ],
            }
        )

    write_report(
        OUTPUT_PATH,
        {
            "dataset_version": dataset.dataset_version,
            "dataset_revision": dataset.dataset_revision,
            "annotation_warning": (
                "This pool contains raw review text and is Git ignored. Assign labels "
                "from text and metadata, not from production total scores."
            ),
            "product_cases": product_cases,
            "review_cases": review_cases,
        },
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "product_cases": len(product_cases),
                "review_cases": len(review_cases),
                "output": str(OUTPUT_PATH.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
