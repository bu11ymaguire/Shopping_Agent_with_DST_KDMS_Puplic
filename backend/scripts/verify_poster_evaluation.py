"""Verify guarded aggregation with synthetic labels and real frozen provenance."""

from __future__ import annotations

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
)
from app.evaluation.poster_results import (  # noqa: E402
    IncompleteAnnotationError,
    PosterEvaluationError,
    evaluate_completed_poster_annotations,
    krippendorff_alpha_ordinal,
    paired_bootstrap_difference,
)
from app.llm import write_report  # noqa: E402
from scripts.prepare_poster_annotation_collection import prepare_collection  # noqa: E402

SOURCE_DIR = BACKEND_ROOT / "reports" / "poster_annotation_v1"
PRIVATE_PATH = SOURCE_DIR / "private_provenance.json"
SUMMARY_PATH = BACKEND_ROOT / "data" / "manifests" / "poster_annotation_packet_v1.json"
ANNOTATOR_IDS = ["annotator-01", "annotator-02", "annotator-03"]


def _check(condition: bool, label: str, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail!r}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def _synthetic_grade(item_id: str) -> int:
    digits = [int(character) for character in item_id if character.isdigit()]
    return sum(digits) % 4


def _fill_product_packet(path: Path) -> dict[str, Any]:
    packet = ProductAnnotationPacket.model_validate_json(path.read_text(encoding="utf-8"))
    payload = packet.model_dump(mode="json")
    for scenario in payload["scenarios"]:
        for candidate in scenario["candidates"]:
            candidate["annotation"] = {
                "relevance": _synthetic_grade(candidate["candidate_id"]),
                "rationale": "Synthetic verifier label; not a human judgment.",
            }
    return payload


def _fill_review_packet(path: Path) -> dict[str, Any]:
    packet = ReviewAnnotationPacket.model_validate_json(path.read_text(encoding="utf-8"))
    payload = packet.model_dump(mode="json")
    for scenario in payload["scenarios"]:
        for review in scenario["reviews"]:
            review["annotation"] = {
                "relevance": _synthetic_grade(review["review_id"]),
                "rationale": "Synthetic verifier label; not a human judgment.",
            }
    return payload


def _collection_manifest(method: str = "synthetic_verifier") -> dict[str, Any]:
    return {
        "schema_version": "poster-annotation-collection-v1",
        "packet_base_id": "poster-v1-2fc174978a6dbbec",
        "collection_method": method,
        "product_annotator_ids": ANNOTATOR_IDS,
        "review_annotator_ids": ANNOTATOR_IDS,
        "annotators_worked_independently": False,
        "private_provenance_withheld_until_completion": False,
        "completed_at": None,
        "annotation_authorship_disclosure": (
            "Synthetic labels generated only for deterministic verifier coverage."
        ),
        "notes": ["This is not human annotation and must not be reported."],
    }


def main() -> None:
    _check(SOURCE_DIR.is_dir(), "동결 public packet 사용 가능")
    _check(PRIVATE_PATH.is_file(), "private provenance 사용 가능")
    _check(
        krippendorff_alpha_ordinal([[0, 0, 0], [1, 1, 1], [3, 3, 3]]) == 1.0,
        "ordinal alpha 완전 일치 self-test",
    )
    disagreement_alpha = krippendorff_alpha_ordinal(
        [[0, 3, 0], [1, 2, 1], [3, 0, 3], [2, 1, 2]]
    )
    _check(
        disagreement_alpha is not None and disagreement_alpha < 1.0,
        "ordinal alpha 불일치 감지",
        round(disagreement_alpha or 0.0, 6),
    )
    bootstrap_one = paired_bootstrap_difference(
        [0.8, 0.7, 0.9],
        [0.6, 0.7, 0.8],
        samples=1_000,
        seed="poster-bootstrap-self-test",
    )
    bootstrap_two = paired_bootstrap_difference(
        [0.8, 0.7, 0.9],
        [0.6, 0.7, 0.8],
        samples=1_000,
        seed="poster-bootstrap-self-test",
    )
    _check(bootstrap_one == bootstrap_two, "paired bootstrap 결정론적 seed")

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        completed_dir = root / "completed"
        prepared = prepare_collection(
            source_dir=SOURCE_DIR,
            output_dir=completed_dir,
            summary_path=SUMMARY_PATH,
            template_path=(
                BACKEND_ROOT
                / "data"
                / "poster_annotation_collection_manifest.template.json"
            ),
        )
        _check(prepared["copied_public_packet_count"] == 6, "작업용 public packet 6개 복사")
        _check(not prepared["private_provenance_copied"], "private provenance 복사 차단")
        for annotator_id in ANNOTATOR_IDS:
            target = completed_dir / annotator_id
            write_report(
                target / "product_annotations.json",
                _fill_product_packet(target / "product_annotations.json"),
            )
            write_report(
                target / "review_annotations.json",
                _fill_review_packet(target / "review_annotations.json"),
            )
        collection_path = completed_dir / "collection_manifest.json"
        write_report(collection_path, _collection_manifest())
        result = evaluate_completed_poster_annotations(
            collection_dir=completed_dir,
            collection_manifest_path=collection_path,
            private_provenance_path=PRIVATE_PATH,
            packet_summary_path=SUMMARY_PATH,
            frozen_packet_dir=SOURCE_DIR,
            bootstrap_samples=1_000,
        )
        _check(
            set(result["systems"]) == {"token", "semantic"},
            "동결 provenance의 두 retrieval system",
        )
        _check(
            result["annotation_collection"]["product"][
                "krippendorff_alpha_ordinal"
            ]
            == 1.0,
            "synthetic product 완전 일치",
        )
        _check(
            result["annotation_collection"]["review"][
                "krippendorff_alpha_ordinal"
            ]
            == 1.0,
            "synthetic review 완전 일치",
        )
        _check(
            all(
                system["product_ranking"]["hard_filter_violation_rate_at_10"]
                == 0.0
                for system in result["systems"].values()
            ),
            "동결 system hard-filter 위반 없음",
        )
        _check(
            all(
                system["product_ranking"]["evidence_consistency_rate_at_3"]
                == 1.0
                for system in result["systems"].values()
            ),
            "동결 system evidence 연결 일치",
        )
        _check(
            len(result["paired_bootstrap_comparisons"]) == 1,
            "semantic-minus-token paired CI 생성",
        )
        readiness = result["poster_readiness"]
        _check(
            not readiness["recommendation_and_review_result_ready"],
            "synthetic label은 human 결과로 승격하지 않음",
        )
        _check(
            not readiness["required_ablation_systems_present"],
            "packet v1의 ablation 공백 노출",
        )
        _check(
            not readiness["state_diff_primary_metric_present"],
            "packet v1의 State Diff 공백 노출",
        )
        _check(
            not readiness["full_poster_primary_claim_ready"],
            "부분 지표를 전체 포스터 주장으로 승격하지 않음",
        )

        tampered_path = completed_dir / "annotator-01" / "product_annotations.json"
        tampered = json.loads(tampered_path.read_text(encoding="utf-8"))
        tampered["scenarios"][0]["candidates"][0]["title"] += " tampered"
        write_report(tampered_path, tampered)
        try:
            evaluate_completed_poster_annotations(
                collection_dir=completed_dir,
                collection_manifest_path=collection_path,
                private_provenance_path=PRIVATE_PATH,
                packet_summary_path=SUMMARY_PATH,
                frozen_packet_dir=SOURCE_DIR,
                bootstrap_samples=1_000,
            )
        except PosterEvaluationError as exc:
            _check(
                "changed non-annotation content" in str(exc),
                "완료 packet의 비라벨 변조 거부",
            )
        else:
            raise AssertionError("tampered completed packet must fail")

        pending_path = root / "pending_collection.json"
        write_report(pending_path, _collection_manifest(method="pending"))
        try:
            evaluate_completed_poster_annotations(
                collection_dir=SOURCE_DIR,
                collection_manifest_path=pending_path,
                private_provenance_path=PRIVATE_PATH,
                packet_summary_path=SUMMARY_PATH,
                frozen_packet_dir=SOURCE_DIR,
                bootstrap_samples=1_000,
            )
        except IncompleteAnnotationError as exc:
            _check("blank product grades" in str(exc), "빈 packet 평가 거부")
        else:
            raise AssertionError("blank packet evaluation must fail")

    print("[PASS] guarded poster evaluation aggregation")


if __name__ == "__main__":
    main()
