"""Split each blinded product/review packet into bounded annotation sessions."""

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
    create_annotation_sessions,
)

DEFAULT_COLLECTION_DIR = BACKEND_ROOT / "reports" / "poster_annotation_v1_completed"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection-dir", type=Path, default=DEFAULT_COLLECTION_DIR)
    parser.add_argument("--max-items", type=int, default=120)
    args = parser.parse_args()
    try:
        result = create_annotation_sessions(
            collection_dir=args.collection_dir.resolve(),
            max_items_per_session=args.max_items,
        )
    except (FileNotFoundError, PosterSessionError, ValueError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
