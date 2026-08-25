"""Verify frozen poster scenarios and a blinded two-scenario packet smoke build."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.poster_annotation import (  # noqa: E402
    ProductAnnotationPacket,
    ReviewAnnotationPacket,
    build_poster_scenario_state,
    load_poster_scenario_dataset,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.nodes.actual_policy import select_actual_policy  # noqa: E402
from app.nodes.actual_recommendation import generate_actual_query  # noqa: E402
from scripts.build_poster_annotation_packets import (  # noqa: E402
    DEFAULT_SEED,
    build_packets,
)

DATASET_PATH = BACKEND_ROOT / "data" / "poster_recommendation_scenarios_v1.json"
SUMMARY_PATH = BACKEND_ROOT / "data" / "manifests" / "poster_annotation_packet_v1.json"
LOCAL_PACKET_DIR = BACKEND_ROOT / "reports" / "poster_annotation_v1"


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


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _keys(child)}
    return set()


def main() -> None:
    dataset = load_poster_scenario_dataset(DATASET_PATH)
    _check(len(dataset.scenarios) == 20, "poster 독립 시나리오 20개")
    _check(dataset.recommended_annotator_count == 3, "권장 annotator 3명")
    _check(dataset.frozen_before_first_packet_build, "첫 packet build 전 시나리오 동결")
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    _check(
        summary["scenario_dataset_sha256"] == _sha256(DATASET_PATH),
        "추적 요약과 scenario dataset hash 일치",
    )
    _check(summary["scenario_count"] == 20, "추적 요약 시나리오 수")
    _check(summary["annotator_count"] == 3, "추적 요약 annotator 수")
    _check(
        all(len(scenario.conversation_turns) == 4 for scenario in dataset.scenarios),
        "각 시나리오 4턴",
    )
    required_tags = {
        "budget",
        "storage",
        "memory",
        "weight",
        "rating",
        "android",
        "ios",
        "reading",
        "gaming",
        "video",
        "child",
        "work",
        "durability",
        "battery",
        "audio",
    }
    observed_tags = {tag for scenario in dataset.scenarios for tag in scenario.tags}
    _check(required_tags <= observed_tags, "요구 시나리오 유형 coverage")

    catalog = ExperimentalAmazonCatalog()
    _check(catalog.available, "실제 catalog 사용 가능")
    candidate_counts: dict[str, int] = {}
    for scenario in dataset.scenarios:
        state = build_poster_scenario_state(scenario)
        _check(
            select_actual_policy(state).lane == "recommend-lane",
            f"{scenario.id} recommend lane",
        )
        query = generate_actual_query(state)
        _check(
            query.hard_filters == scenario.expected_hard_filters,
            f"{scenario.id} hard-filter 계약",
        )
        filters = query.hard_filters
        result = catalog.search_products(
            max_price_usd=filters.max_price_usd,
            min_storage_gb=filters.min_storage_gb,
            min_memory_gb=filters.min_memory_gb,
            max_weight_grams=filters.max_weight_grams,
            min_rating=filters.min_rating,
            min_screen_inches=filters.min_screen_inches,
            operating_system=filters.operating_system,
            limit=300,
        )
        candidate_counts[scenario.id] = result.total
    _check(
        all(count >= 2 for count in candidate_counts.values()),
        "모든 시나리오에 비교 가능한 hard-filter 후보",
        min(candidate_counts.values()),
    )

    with tempfile.TemporaryDirectory() as temporary:
        output_dir = Path(temporary) / "packets"
        result = build_packets(
            argparse.Namespace(
                dataset=DATASET_PATH,
                output_dir=output_dir,
                annotators=2,
                hard_negatives=1,
                seed=DEFAULT_SEED,
                limit=2,
            )
        )
        _check(result["scenario_count"] == 2, "packet smoke 시나리오 제한")
        _check(result["annotator_count"] == 2, "packet smoke annotator 수")
        _check(len(result["packet_files"]) == 4, "annotator별 product/review packet")

        products: list[ProductAnnotationPacket] = []
        reviews: list[ReviewAnnotationPacket] = []
        for annotator_number in (1, 2):
            annotator_dir = output_dir / f"annotator-{annotator_number:02d}"
            product_raw = json.loads(
                (annotator_dir / "product_annotations.json").read_text(
                    encoding="utf-8"
                )
            )
            review_raw = json.loads(
                (annotator_dir / "review_annotations.json").read_text(
                    encoding="utf-8"
                )
            )
            products.append(ProductAnnotationPacket.model_validate(product_raw))
            reviews.append(ReviewAnnotationPacket.model_validate(review_raw))
            public_keys = _keys(product_raw) | _keys(review_raw)
            forbidden = {
                "parent_asin",
                "source_review_id",
                "systems",
                "rank",
                "total_score",
                "retrieval_score",
                "retrieval_method",
                "pool_source",
            }
            _check(
                not (public_keys & forbidden),
                f"annotator-{annotator_number:02d} system provenance blind",
                sorted(public_keys & forbidden),
            )
            _check(
                all(
                    candidate.annotation.relevance is None
                    and candidate.annotation.rationale is None
                    for scenario in products[-1].scenarios
                    for candidate in scenario.candidates
                ),
                f"annotator-{annotator_number:02d} product label blank",
            )
            _check(
                all(
                    review.annotation.relevance is None
                    and review.annotation.rationale is None
                    for scenario in reviews[-1].scenarios
                    for review in scenario.reviews
                ),
                f"annotator-{annotator_number:02d} review label blank",
            )

        product_ids = [
            {
                candidate.candidate_id
                for candidate in scenario.candidates
            }
            for scenario in products[0].scenarios
        ]
        _check(
            product_ids
            == [
                {candidate.candidate_id for candidate in scenario.candidates}
                for scenario in products[1].scenarios
            ],
            "annotator 간 동일 candidate 집합",
        )
        orders_one = [
            [candidate.candidate_id for candidate in scenario.candidates]
            for scenario in products[0].scenarios
        ]
        orders_two = [
            [candidate.candidate_id for candidate in scenario.candidates]
            for scenario in products[1].scenarios
        ]
        _check(orders_one != orders_two, "annotator별 candidate 순서 독립 randomization")
        for product_packet, review_packet in zip(products, reviews, strict=True):
            valid_product_ids = {
                candidate.candidate_id
                for scenario in product_packet.scenarios
                for candidate in scenario.candidates
            }
            _check(
                all(
                    review.candidate_id in valid_product_ids
                    for scenario in review_packet.scenarios
                    for review in scenario.reviews
                ),
                f"{product_packet.annotator_id} review-product blind ID 연결",
            )

        private = json.loads(
            (output_dir / "private_provenance.json").read_text(encoding="utf-8")
        )
        _check(private["scenario_count"] == 2, "private provenance 시나리오 수")
        _check(
            private["product_judgment_count_per_annotator"]
            == result["product_judgment_count_per_annotator"],
            "private/public product 판정 수 일치",
        )
        _check(
            all(
                entry["parent_asin"]
                for scenario in private["scenarios"]
                for entry in scenario["product_map"].values()
            ),
            "원본 상품 ID는 private manifest에만 보존",
        )
        _check(
            all(
                entry["source_review_id"]
                for scenario in private["scenarios"]
                for entry in scenario["review_map"].values()
            ),
            "원본 리뷰 ID는 private manifest에만 보존",
        )

    if LOCAL_PACKET_DIR.is_dir():
        for relative_path, expected in summary["local_artifacts"].items():
            path = LOCAL_PACKET_DIR / relative_path
            _check(path.is_file(), "추적한 local packet 파일 존재", relative_path)
            _check(path.stat().st_size == expected["bytes"], "local packet byte 크기", relative_path)
            _check(_sha256(path) == expected["sha256"], "local packet SHA-256", relative_path)

    print(
        "[PASS] poster annotation packet contract - "
        f"scenarios={len(dataset.scenarios)}, min_candidates={min(candidate_counts.values())}"
    )


if __name__ == "__main__":
    main()
