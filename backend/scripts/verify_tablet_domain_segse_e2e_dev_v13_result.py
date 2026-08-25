"""Verify the SEGSE v1.3 exposed-fixture development result."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.run_tablet_domain_segse_e2e_dev_v13 import (  # noqa: E402
    evaluate_v13_gate,
)


SUMMARY = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v13.json"
MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v13_result.json"
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[ok] {label}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check("result is development-only", manifest["confirmatory_evidence"] is False)
    check("summary hash", _sha256(SUMMARY) == manifest["tracked_summary"]["sha256"])
    baseline = summary["baseline_reference"]["metrics"]
    treatment = summary["treatment"]["metrics"]
    check("decision recomputes", evaluate_v13_gate(baseline, treatment) == summary["decision"])
    check("24 treatment turns completed", treatment["completed_turn_count"] == 24)
    check("C2U remains zero", treatment["c2u_fp_count"] == 0)
    check("one final FP remains", treatment["scenario_final_state"]["false_positive"] == 1)
    check("correction recall gate fails", not summary["decision"]["checks"]["correction_recall_drop_at_most_2pp"])
    check("turnwise state gate fails", not summary["decision"]["checks"]["turnwise_state_f1_drop_at_most_001"])
    for artifact in manifest["git_ignored_artifacts"].values():
        path = BACKEND_ROOT / artifact["path"]
        if path.exists():
            check(f"optional artifact hash: {artifact['path']}", _sha256(path) == artifact["sha256"])
    print("SEGSE v1.3 result verification completed.")


if __name__ == "__main__":
    main()
