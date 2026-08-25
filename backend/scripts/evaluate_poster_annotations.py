"""Aggregate completed blinded packets into guarded poster evaluation metrics."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.poster_results import (  # noqa: E402
    PosterEvaluationError,
    evaluate_completed_poster_annotations,
)
from app.llm import write_report  # noqa: E402

DEFAULT_COLLECTION_DIR = BACKEND_ROOT / "reports" / "poster_annotation_v1_completed"
DEFAULT_COLLECTION_MANIFEST = DEFAULT_COLLECTION_DIR / "collection_manifest.json"
DEFAULT_PRIVATE_PROVENANCE = (
    BACKEND_ROOT / "reports" / "poster_annotation_v1" / "private_provenance.json"
)
DEFAULT_FROZEN_PACKET_DIR = BACKEND_ROOT / "reports" / "poster_annotation_v1"
DEFAULT_PACKET_SUMMARY = (
    BACKEND_ROOT / "data" / "manifests" / "poster_annotation_packet_v1.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "poster_evaluation_v1.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-dir", type=Path, default=DEFAULT_COLLECTION_DIR)
    parser.add_argument(
        "--collection-manifest", type=Path, default=DEFAULT_COLLECTION_MANIFEST
    )
    parser.add_argument(
        "--private-provenance", type=Path, default=DEFAULT_PRIVATE_PROVENANCE
    )
    parser.add_argument(
        "--frozen-packet-dir", type=Path, default=DEFAULT_FROZEN_PACKET_DIR
    )
    parser.add_argument("--packet-summary", type=Path, default=DEFAULT_PACKET_SUMMARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    args = parser.parse_args()
    if args.bootstrap_samples < 1_000:
        parser.error("--bootstrap-samples must be at least 1000")
    try:
        result = evaluate_completed_poster_annotations(
            collection_dir=args.collection_dir.resolve(),
            collection_manifest_path=args.collection_manifest.resolve(),
            private_provenance_path=args.private_provenance.resolve(),
            packet_summary_path=args.packet_summary.resolve(),
            frozen_packet_dir=args.frozen_packet_dir.resolve(),
            bootstrap_samples=args.bootstrap_samples,
        )
    except (PosterEvaluationError, FileNotFoundError, ValueError) as exc:
        print(json.dumps({"status": "not_ready", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
    write_report(args.output, result)
    print(
        json.dumps(
            {
                "status": "ok",
                "poster_readiness": result["poster_readiness"],
                "systems": result["systems"],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
