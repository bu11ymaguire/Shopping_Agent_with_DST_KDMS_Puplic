"""Create a Git-ignored working copy of frozen public annotation packets."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.llm import write_report  # noqa: E402

DEFAULT_SOURCE = BACKEND_ROOT / "reports" / "poster_annotation_v1"
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "poster_annotation_v1_completed"
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "data" / "manifests" / "poster_annotation_packet_v1.json"
)
DEFAULT_TEMPLATE = (
    BACKEND_ROOT / "data" / "poster_annotation_collection_manifest.template.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_collection(
    *,
    source_dir: Path,
    output_dir: Path,
    summary_path: Path,
    template_path: Path,
) -> dict[str, Any]:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError(
            f"refusing to overwrite a non-empty annotation collection: {output_dir}"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    template = json.loads(template_path.read_text(encoding="utf-8"))
    if template["packet_base_id"] != summary["packet_base_id"]:
        raise RuntimeError("collection template and packet summary IDs differ")

    copied = []
    public_artifacts = {
        relative: expected
        for relative, expected in summary["local_artifacts"].items()
        if relative != "private_provenance.json"
    }
    for relative, expected in public_artifacts.items():
        source = source_dir / relative
        if not source.is_file():
            raise RuntimeError(f"missing frozen public packet: {source}")
        if source.stat().st_size != expected["bytes"]:
            raise RuntimeError(f"frozen public packet byte size changed: {source}")
        if _sha256(source) != expected["sha256"]:
            raise RuntimeError(f"frozen public packet hash changed: {source}")
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str(target.resolve()))

    collection_manifest = output_dir / "collection_manifest.json"
    write_report(collection_manifest, template)
    return {
        "status": "ok",
        "packet_base_id": summary["packet_base_id"],
        "working_directory": str(output_dir.resolve()),
        "copied_public_packet_count": len(copied),
        "copied_public_packets": copied,
        "collection_manifest": str(collection_manifest.resolve()),
        "private_provenance_copied": False,
        "next_step": (
            "Distribute only each annotator-NN directory. After independent annotation, "
            "truthfully complete collection_manifest.json and run "
            "scripts/evaluate_poster_annotations.py."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    args = parser.parse_args()
    try:
        result = prepare_collection(
            source_dir=args.source_dir.resolve(),
            output_dir=args.output_dir.resolve(),
            summary_path=args.summary.resolve(),
            template_path=args.template.resolve(),
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
