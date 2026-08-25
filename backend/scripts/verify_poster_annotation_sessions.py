"""Verify bounded session coverage, tamper rejection, merge, and result handoff."""

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
from app.evaluation.poster_results import evaluate_completed_poster_annotations  # noqa: E402
from app.evaluation.poster_sessions import (  # noqa: E402
    PosterSessionError,
    PosterSessionSetManifest,
    create_annotation_sessions,
    merge_completed_sessions,
)
from app.llm import write_report  # noqa: E402
from scripts.prepare_poster_annotation_collection import prepare_collection  # noqa: E402

SOURCE_DIR = BACKEND_ROOT / "reports" / "poster_annotation_v1"
PRIVATE_PATH = SOURCE_DIR / "private_provenance.json"
SUMMARY_PATH = BACKEND_ROOT / "data" / "manifests" / "poster_annotation_packet_v1.json"
TEMPLATE_PATH = (
    BACKEND_ROOT / "data" / "poster_annotation_collection_manifest.template.json"
)


def _check(condition: bool, label: str, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail!r}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def _fill_session(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    kind = payload["annotation_kind"]
    key = "candidates" if kind == "product" else "reviews"
    identifier = "candidate_id" if kind == "product" else "review_id"
    for scenario in payload["scenarios"]:
        for item in scenario[key]:
            grade = sum(int(value) for value in item[identifier] if value.isdigit()) % 4
            item["annotation"] = {
                "relevance": grade,
                "rationale": "Synthetic session verifier label; not human annotation.",
            }
    write_report(path, payload)


def _synthetic_collection_manifest() -> dict[str, Any]:
    return {
        "schema_version": "poster-annotation-collection-v1",
        "packet_base_id": "poster-v1-2fc174978a6dbbec",
        "collection_method": "synthetic_verifier",
        "product_annotator_ids": ["annotator-01", "annotator-02", "annotator-03"],
        "review_annotator_ids": ["annotator-01", "annotator-02", "annotator-03"],
        "annotators_worked_independently": False,
        "private_provenance_withheld_until_completion": False,
        "completed_at": None,
        "annotation_authorship_disclosure": (
            "Synthetic labels generated only by the session verifier."
        ),
        "notes": ["Never report this as human annotation."],
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        collection_dir = Path(temporary) / "collection"
        prepare_collection(
            source_dir=SOURCE_DIR,
            output_dir=collection_dir,
            summary_path=SUMMARY_PATH,
            template_path=TEMPLATE_PATH,
        )
        result = create_annotation_sessions(
            collection_dir=collection_dir,
            max_items_per_session=120,
        )
        manifest = PosterSessionSetManifest.model_validate_json(
            (collection_dir / "session_manifest.json").read_text(encoding="utf-8")
        )
        _check(result["session_file_count"] == 15, "3인 session 파일 15개")
        _check(
            all(item.item_count <= 120 for item in manifest.files),
            "모든 session 120건 이하",
        )
        for annotator_id in ("annotator-01", "annotator-02", "annotator-03"):
            product = result["groups"][f"{annotator_id}:product"]
            review = result["groups"][f"{annotator_id}:review"]
            _check(product["session_count"] == 2, f"{annotator_id} product 2 sessions")
            _check(product["total_items"] == 170, f"{annotator_id} product 전체 coverage")
            _check(review["session_count"] == 3, f"{annotator_id} review 3 sessions")
            _check(review["total_items"] == 302, f"{annotator_id} review 전체 coverage")

        try:
            merge_completed_sessions(collection_dir=collection_dir)
        except PosterSessionError as exc:
            _check("incomplete product annotation" in str(exc), "빈 session 병합 거부")
        else:
            raise AssertionError("blank sessions must not merge")

        first_path = collection_dir / manifest.files[0].relative_path
        original = json.loads(first_path.read_text(encoding="utf-8"))
        tampered = json.loads(first_path.read_text(encoding="utf-8"))
        key = "candidates" if tampered["annotation_kind"] == "product" else "reviews"
        tampered["scenarios"][0][key][0]["title"] = "tampered"
        write_report(first_path, tampered)
        try:
            merge_completed_sessions(collection_dir=collection_dir)
        except PosterSessionError as exc:
            _check("non-annotation content changed" in str(exc), "session 비라벨 변조 거부")
        else:
            raise AssertionError("tampered session must not merge")
        write_report(first_path, original)

        for record in manifest.files:
            _fill_session(collection_dir / record.relative_path)
        merged = merge_completed_sessions(collection_dir=collection_dir)
        _check(merged["merged_packet_count"] == 6, "완료 session을 packet 6개로 병합")
        for annotator_id in ("annotator-01", "annotator-02", "annotator-03"):
            product = ProductAnnotationPacket.model_validate_json(
                (collection_dir / annotator_id / "product_annotations.json").read_text(
                    encoding="utf-8"
                )
            )
            review = ReviewAnnotationPacket.model_validate_json(
                (collection_dir / annotator_id / "review_annotations.json").read_text(
                    encoding="utf-8"
                )
            )
            _check(
                all(
                    item.annotation.relevance is not None
                    for scenario in product.scenarios
                    for item in scenario.candidates
                ),
                f"{annotator_id} product 완결",
            )
            _check(
                all(
                    item.annotation.relevance is not None
                    for scenario in review.scenarios
                    for item in scenario.reviews
                ),
                f"{annotator_id} review 완결",
            )
        rerun = merge_completed_sessions(collection_dir=collection_dir)
        _check(rerun["merged_packet_count"] == 6, "동일 label 병합 재실행 안전")

        collection_manifest = collection_dir / "collection_manifest.json"
        write_report(collection_manifest, _synthetic_collection_manifest())
        evaluation = evaluate_completed_poster_annotations(
            collection_dir=collection_dir,
            collection_manifest_path=collection_manifest,
            private_provenance_path=PRIVATE_PATH,
            packet_summary_path=SUMMARY_PATH,
            frozen_packet_dir=SOURCE_DIR,
            bootstrap_samples=1_000,
        )
        _check(
            not evaluation["poster_readiness"][
                "recommendation_and_review_result_ready"
            ],
            "session synthetic label은 human result가 아님",
        )
    print("[PASS] bounded poster annotation session workflow")


if __name__ == "__main__":
    main()
