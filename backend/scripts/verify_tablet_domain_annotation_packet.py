"""Verify blindness, integrity, and coverage of the official holdout packet."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_annotation import (  # noqa: E402
    load_official_product_packet,
    load_official_review_packet,
)

PACKET_ROOT = BACKEND_ROOT / "reports" / "tablet_domain_holdout_annotation_packet_v1"
TRACKED_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_annotation_packet_v1.json"
)


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_text_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> None:
    manifest_path = PACKET_ROOT / "packet_manifest.json"
    provenance_path = PACKET_ROOT / "private_provenance.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    tracked = json.loads(TRACKED_MANIFEST.read_text(encoding="utf-8"))
    check("tracked packet is blank", tracked["human_grade_count"] == 0)
    check(
        "tracked packet manifest hash",
        tracked["git_excluded_packet"]["packet_manifest"]["sha256"]
        == sha256(manifest_path),
    )
    check("three annotators", manifest["annotator_count"] == 3)
    check("17 scenarios with output", manifest["pooled_scenario_count"] == 17)
    check(
        "no-output scenarios excluded",
        manifest["skipped_no_output_scenarios"] == ["th08", "th11", "th20"],
    )
    for relative, frozen in manifest["files"].items():
        path = PACKET_ROOT / relative
        check(f"packet file exists: {relative}", path.exists())
        check(f"packet byte size: {relative}", path.stat().st_size == frozen["bytes"])
        check(f"packet hash: {relative}", sha256(path) == frozen["sha256"])

    original_products = {
        item["parent_asin"]
        for scenario in provenance["scenarios"].values()
        for item in scenario["products"].values()
    }
    original_reviews = {
        item["review_id"]
        for scenario in provenance["scenarios"].values()
        for item in scenario["reviews"].values()
    }
    product_sets = []
    review_sets = []
    product_orders = []
    review_orders = []
    for number in range(1, 4):
        annotator = f"annotator-{number:02d}"
        product_path = PACKET_ROOT / annotator / "product_annotations.json"
        review_path = PACKET_ROOT / annotator / "review_annotations.json"
        product = load_official_product_packet(product_path)
        review = load_official_review_packet(review_path)
        public_text = product_path.read_text(encoding="utf-8") + review_path.read_text(
            encoding="utf-8"
        )
        check(
            f"{annotator} hides system labels",
            all(
                token not in public_text
                for token in ("no_memory", "fixed_upstream_no_review", "system_ranks")
            ),
        )
        check(
            f"{annotator} hides original product IDs",
            not any(product_id in public_text for product_id in original_products),
        )
        check(
            f"{annotator} hides original review IDs",
            not any(review_id in public_text for review_id in original_reviews),
        )
        product_items = [
            item for scenario in product.scenarios for item in scenario.candidates
        ]
        review_items = [item for scenario in review.scenarios for item in scenario.reviews]
        check(f"{annotator} product workload", len(product_items) == 88)
        check(f"{annotator} review workload", len(review_items) == 239)
        check(
            f"{annotator} all grades blank",
            all(item.annotation.grade is None for item in [*product_items, *review_items]),
        )
        candidates = {item.candidate_id for item in product_items}
        check(
            f"{annotator} review-product links",
            all(item.candidate_id in candidates for item in review_items),
        )
        product_sets.append(candidates)
        review_sets.append({item.review_id for item in review_items})
        product_orders.append(
            tuple(
                item.candidate_id
                for scenario in product.scenarios
                for item in scenario.candidates
            )
        )
        review_orders.append(
            tuple(
                item.review_id
                for scenario in review.scenarios
                for item in scenario.reviews
            )
        )
    check("annotators share product pool", len({frozenset(x) for x in product_sets}) == 1)
    check("annotators share review pool", len({frozenset(x) for x in review_sets}) == 1)
    check("product order independently shuffled", len(set(product_orders)) == 3)
    check("review order independently shuffled", len(set(review_orders)) == 3)
    check(
        "provenance has only evaluated systems",
        provenance["systems"]
        == ["full", "no_memory", "fixed_upstream_no_review"],
    )
    session_manifest_path = PACKET_ROOT / "session_manifest.json"
    check("session manifest exists", session_manifest_path.exists())
    sessions = json.loads(session_manifest_path.read_text(encoding="utf-8"))
    check(
        "tracked session manifest hash",
        tracked["git_excluded_packet"]["session_manifest"]["sha256"]
        == sha256(session_manifest_path),
    )
    check("session limit is 120", sessions["max_items_per_session"] == 120)
    for annotator, records in sessions["annotators"].items():
        check(f"{annotator} has three sessions", len(records) == 3)
        check(
            f"{annotator} product session coverage",
            sum(item["item_count"] for item in records if item["kind"] == "product")
            == 88,
        )
        check(
            f"{annotator} review session coverage",
            sum(item["item_count"] for item in records if item["kind"] == "review")
            == 239,
        )
        for record in records:
            path = PACKET_ROOT / annotator / "sessions" / record["filename"]
            check(
                f"{annotator} bounded session {record['filename']}",
                record["item_count"] <= 120,
            )
            check(
                f"{annotator} session hash {record['filename']}",
                sha256(path) == record["sha256"],
            )
            if record["kind"] == "product":
                load_official_product_packet(path)
            else:
                load_official_review_packet(path)
        check(
            f"{annotator} sessions contain no private provenance",
            not (PACKET_ROOT / annotator / "sessions" / "private_provenance.json").exists(),
        )
    for relative, frozen_hash in tracked["source_sha256_lf_normalized"].items():
        check(
            f"tracked packet source hash: {relative}",
            normalized_text_sha256(BACKEND_ROOT / relative) == frozen_hash,
        )
    print("Tablet-domain official annotation packet verification completed.")


if __name__ == "__main__":
    main()
