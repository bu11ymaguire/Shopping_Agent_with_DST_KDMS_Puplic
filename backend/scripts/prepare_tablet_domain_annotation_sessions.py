"""Split official blind packets into bounded annotator sessions."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_annotation import (  # noqa: E402
    OfficialProductPacket,
    OfficialReviewPacket,
    load_official_product_packet,
    load_official_review_packet,
)
from app.llm import write_report  # noqa: E402

PACKET_ROOT = BACKEND_ROOT / "reports" / "tablet_domain_holdout_annotation_packet_v1"
MAX_ITEMS = 120


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def chunks_by_scenario(scenarios: list[Any], item_field: str) -> list[list[Any]]:
    chunks: list[list[Any]] = []
    current: list[Any] = []
    count = 0
    for scenario in scenarios:
        size = len(getattr(scenario, item_field))
        if size > MAX_ITEMS:
            raise RuntimeError(f"one scenario exceeds the session limit: {scenario.scenario_id}")
        if current and count + size > MAX_ITEMS:
            chunks.append(current)
            current = []
            count = 0
        current.append(scenario)
        count += size
    if current:
        chunks.append(current)
    return chunks


def main() -> None:
    manifest: dict[str, Any] = {
        "schema_version": "tablet-domain-annotation-sessions-v1",
        "max_items_per_session": MAX_ITEMS,
        "annotators": {},
        "files": {},
    }
    for number in range(1, 4):
        annotator = f"annotator-{number:02d}"
        root = PACKET_ROOT / annotator
        session_dir = root / "sessions"
        if session_dir.exists():
            raise RuntimeError(f"refusing to overwrite sessions: {session_dir}")
        product = load_official_product_packet(root / "product_annotations.json")
        review = load_official_review_packet(root / "review_annotations.json")
        product_chunks = chunks_by_scenario(product.scenarios, "candidates")
        review_chunks = chunks_by_scenario(review.scenarios, "reviews")
        records = []
        for kind, chunks in (("product", product_chunks), ("review", review_chunks)):
            for index, scenarios in enumerate(chunks, start=1):
                filename = f"{kind}-session-{index:02d}.json"
                path = session_dir / filename
                if kind == "product":
                    packet = OfficialProductPacket(
                        **product.model_dump(exclude={"packet_id", "scenarios"}),
                        packet_id=f"{product.packet_id}-session-{index:02d}",
                        scenarios=scenarios,
                    )
                    count = sum(len(item.candidates) for item in scenarios)
                else:
                    packet = OfficialReviewPacket(
                        **review.model_dump(exclude={"packet_id", "scenarios"}),
                        packet_id=f"{review.packet_id}-session-{index:02d}",
                        scenarios=scenarios,
                    )
                    count = sum(len(item.reviews) for item in scenarios)
                write_report(path, packet)
                relative = str(path.relative_to(PACKET_ROOT)).replace("\\", "/")
                frozen = {"bytes": path.stat().st_size, "sha256": sha256(path)}
                manifest["files"][relative] = frozen
                records.append(
                    {
                        "kind": kind,
                        "filename": filename,
                        "scenario_ids": [item.scenario_id for item in scenarios],
                        "item_count": count,
                        **frozen,
                    }
                )
        manifest["annotators"][annotator] = records
    write_report(PACKET_ROOT / "session_manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
