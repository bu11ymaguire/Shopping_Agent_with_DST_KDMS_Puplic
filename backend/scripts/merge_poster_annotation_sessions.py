"""Merge completed bounded sessions back into the guarded collection packets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.poster_sessions import (  # noqa: E402
    PosterSessionError,
    merge_completed_sessions,
)

DEFAULT_COLLECTION_DIR = BACKEND_ROOT / "reports" / "poster_annotation_v1_completed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-dir", type=Path, default=DEFAULT_COLLECTION_DIR)
    args = parser.parse_args()
    try:
        result = merge_completed_sessions(collection_dir=args.collection_dir.resolve())
    except (FileNotFoundError, PosterSessionError, ValueError) as exc:
        print(json.dumps({"status": "not_ready", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
