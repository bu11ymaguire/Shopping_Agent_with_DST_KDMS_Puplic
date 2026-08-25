"""Verify the frozen SEGSE v1.2 multi-turn development result."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse_e2e import (  # noqa: E402
    evaluate_e2e_development_gate,
)


SUMMARY = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v1.json"
MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v1_result.json"
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
    check("summary size", SUMMARY.stat().st_size == manifest["tracked_summary"]["size_bytes"])
    baseline = summary["arms"]["b0_c_semantic_noop"]["metrics"]
    treatment = summary["arms"]["d4_segse_v12"]["metrics"]
    decision = evaluate_e2e_development_gate(baseline, treatment)
    check("decision recomputes", decision == summary["decision"])
    check("execution invalid", decision["execution_valid"] is False)
    check(
        "completion gate rejects both arms",
        not decision["checks"]["baseline_turn_output_completion_at_least_95_percent"]
        and not decision["checks"][
            "treatment_turn_output_completion_at_least_95_percent"
        ],
    )
    headline = manifest["headline"]
    check("B0 Candidate FP", baseline["candidate_fp_count"] == headline["b0_candidate_fp"])
    check("v1.2 Candidate FP", treatment["candidate_fp_count"] == headline["v12_candidate_fp"])
    check("B0 C2U FP", baseline["c2u_fp_count"] == headline["b0_c2u_fp"])
    check("v1.2 C2U FP", treatment["c2u_fp_count"] == headline["v12_c2u_fp"])
    check(
        "v1.2 has zero turnwise Final FP",
        treatment["turnwise_accumulated_state"]["false_positive"] == 0,
    )
    check(
        "v1.2 has zero scenario-final FP",
        treatment["scenario_final_state"]["false_positive"] == 0,
    )
    check(
        "recall guard fails",
        not decision["checks"]["raw_candidate_recall_drop_at_most_2pp"],
    )
    for artifact in manifest["git_ignored_artifacts"].values():
        path = BACKEND_ROOT / artifact["path"]
        if path.exists():
            check(f"optional artifact hash: {artifact['path']}", _sha256(path) == artifact["sha256"])
            check(f"optional artifact size: {artifact['path']}", path.stat().st_size == artifact["size_bytes"])
    print("SEGSE v1.2 end-to-end development result verification completed.")


if __name__ == "__main__":
    main()
