"""Build the deterministic case-local guardrail erratum for SEGSE v1.2."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse_corrected import (  # noqa: E402
    build_corrected_v12_summary,
)
from app.llm import write_report  # noqa: E402

SOURCE = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v12.json"
OUTPUT = (
    BACKEND_ROOT
    / "data"
    / "results"
    / "tablet_domain_segse_dev_v12_corrected.json"
)


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"refusing to overwrite corrected result: {OUTPUT}")
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    result = build_corrected_v12_summary(source)
    write_report(OUTPUT, result)
    print(OUTPUT)


if __name__ == "__main__":
    main()
