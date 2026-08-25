"""Verify the frozen real recommendation judgments and deterministic metrics."""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.evaluation.actual_recommendation import (  # noqa: E402
    build_recommendation_eval_state,
    load_actual_recommendation_eval_dataset,
    product_satisfies_hard_filters,
    score_graded_ranking,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.nodes.actual_policy import select_actual_policy  # noqa: E402
from app.nodes.actual_recommendation import (  # noqa: E402
    browse_actual_catalog,
    generate_actual_query,
    rank_actual_products,
)
from app.review_retrieval import build_review_retriever  # noqa: E402

DATASET_PATH = BACKEND_ROOT / "data" / "actual_recommendation_eval_v1.json"
SUMMARY_PATH = BACKEND_ROOT / "data" / "manifests" / "actual_recommendation_eval_v1.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(condition: bool, label: str, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail!r}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def main() -> None:
    dataset = load_actual_recommendation_eval_dataset(DATASET_PATH)
    catalog = ExperimentalAmazonCatalog()
    _check(catalog.available, "실제 catalog 사용 가능")
    _check(len(dataset.product_cases) >= 4, "상품 추천 case 최소 수")
    _check(len(dataset.review_cases) >= 4, "리뷰 검색 case 최소 수")
    _check(dataset.frozen_before_baseline_run, "최초 기준선 전 fixture 동결")
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    _check(
        summary["dataset_sha256"] == _sha256(DATASET_PATH),
        "추적 요약과 fixture hash 일치",
    )
    _check(
        summary["protocol"]["product_judgment_count"]
        == sum(len(case.product_judgments) for case in dataset.product_cases)
        and summary["protocol"]["review_judgment_count"]
        == sum(len(case.review_judgments) for case in dataset.review_cases),
        "추적 요약 판정 수 일치",
    )

    oracle = score_graded_ranking(
        ["a", "b", "c"], {"a": 3, "b": 2, "c": 1}, k=3
    )
    _check(oracle["ndcg_at_3"] == 1.0, "NDCG oracle self-test")
    incomplete = score_graded_ranking(["x", "a"], {"a": 3}, k=2)
    _check(
        incomplete["judgment_coverage_at_2"] == 0.5,
        "미판정 결과 coverage self-test",
    )

    settings = load_review_retrieval_settings()
    retrievers = {
        mode: build_review_retriever(replace(settings, mode=mode))
        for mode in ("token", "semantic")
    }
    for case in dataset.product_cases:
        _check(bool(case.product_judgments), f"{case.id} 상품 판정 존재")
        _check(
            any(item.relevance >= 2 for item in case.product_judgments),
            f"{case.id} relevant 상품 존재",
        )
        for judgment in case.product_judgments:
            _check(
                catalog.get_product(judgment.parent_asin).parent_asin
                == judgment.parent_asin,
                f"{case.id} 판정 상품 catalog 연결",
                judgment.parent_asin,
            )

        state = build_recommendation_eval_state(case)
        _check(
            select_actual_policy(state).lane == "recommend-lane",
            f"{case.id} recommend lane",
        )
        query = generate_actual_query(state)
        _check(
            query.hard_filters == case.expected_hard_filters,
            f"{case.id} hard filter 계약",
        )
        judged_ids = {item.parent_asin for item in case.product_judgments}
        for mode, retriever in retrievers.items():
            products, reviews, browse = browse_actual_catalog(
                query,
                catalog,
                rejected_product_ids=set(),
                review_retriever=retriever,
            )
            if mode == "semantic":
                _check(
                    browse.review_retrieval_method == "semantic_cross_encoder"
                    and browse.review_retrieval_fallback_reason is None,
                    f"{case.id} semantic fallback 없음",
                )
            _check(
                all(
                    product_satisfies_hard_filters(
                        product,
                        query.hard_filters,
                        allow_budget_overrun=query.allow_budget_overrun,
                    )
                    for product in products
                ),
                f"{case.id} {mode} 후보 hard filter 준수",
            )
            rankings, visible_reviews = rank_actual_products(
                state, query, products, reviews
            )
            ranked_ids = {item.product_id for item in rankings}
            _check(
                ranked_ids <= judged_ids,
                f"{case.id} {mode} top-10 pooled 판정 coverage",
                sorted(ranked_ids - judged_ids),
            )
            visible_by_id = {item.review_id: item for item in visible_reviews}
            referenced = {
                review_id
                for item in rankings
                for review_id in item.evidence_review_ids
            }
            _check(
                referenced == set(visible_by_id),
                f"{case.id} {mode} 표시 리뷰와 evidence ID 일치",
            )
            _check(
                all(
                    visible_by_id[review_id].parent_asin == item.product_id
                    for item in rankings
                    for review_id in item.evidence_review_ids
                ),
                f"{case.id} {mode} evidence 상품 연결",
            )

    for case in dataset.review_cases:
        _check(bool(case.review_judgments), f"{case.id} 리뷰 판정 존재")
        _check(
            any(item.relevance >= 2 for item in case.review_judgments),
            f"{case.id} relevant 리뷰 존재",
        )
        judged_ids = {item.review_id for item in case.review_judgments}
        catalog_reviews = catalog.get_reviews_by_ids(list(judged_ids))
        _check(
            len(catalog_reviews) == len(judged_ids)
            and all(item.parent_asin == case.parent_asin for item in catalog_reviews),
            f"{case.id} 판정 리뷰 catalog 연결",
        )
        for mode, retriever in retrievers.items():
            result = retriever.retrieve(catalog, [case.parent_asin], case.query)
            if mode == "semantic":
                _check(
                    result.method == "semantic_cross_encoder"
                    and result.fallback_reason is None,
                    f"{case.id} semantic fallback 없음",
                )
            returned_ids = {item.review_id for item in result.reviews}
            _check(
                returned_ids <= judged_ids,
                f"{case.id} {mode} pooled 리뷰 판정 coverage",
                sorted(returned_ids - judged_ids),
            )

    print(
        "[PASS] actual recommendation evaluation contract - "
        f"products={len(dataset.product_cases)}, reviews={len(dataset.review_cases)}"
    )


if __name__ == "__main__":
    main()
