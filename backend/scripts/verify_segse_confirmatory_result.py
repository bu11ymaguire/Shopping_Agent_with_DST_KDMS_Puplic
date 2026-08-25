"""Verify the SEGSE confirmatory result and its recorded failure."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.segse_v14_freeze import method_fingerprint  # noqa: E402

SUMMARY = BACKEND_ROOT / "data" / "results" / "segse_confirmatory_v1.json"
MANIFEST = BACKEND_ROOT / "data" / "manifests" / "segse_confirmatory_v1_result.json"
HOLDOUT_FREEZE = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_holdout_v1_freeze.json"
)


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"[ok] {label}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_lf(path: Path) -> str:
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def main() -> None:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    holdout = json.loads(HOLDOUT_FREEZE.read_text(encoding="utf-8"))

    check(
        "the result is recorded as a confirmatory failure",
        manifest["status"] == "confirmatory_fail"
        and summary["decision"]["status"] == "confirmatory_fail",
    )
    check(
        "the threshold was not moved after the result",
        manifest["decision"]["threshold_moved_after_seeing_the_result"] is False
        and set(manifest["decision"]["failed_checks"])
        == {"regression_is_bounded", "correction_recall_recovered"},
    )
    check(
        "summary hash",
        _sha256(SUMMARY) == manifest["tracked_summary"]["sha256"],
    )
    for key, relative in (
        ("result_sha256_lf_normalized", "result"),
        ("tables_sha256_lf_normalized", "tables"),
    ):
        path = BACKEND_ROOT / manifest["tracked_documents"][relative]
        check(
            f"document hash: {manifest['tracked_documents'][relative]}",
            _sha256_lf(path) == manifest["tracked_documents"][key],
        )
    check(
        "the frozen method still matches what was measured",
        manifest["freezes"]["method"]["combined_method_sha256"]
        == method_fingerprint()["combined_method_sha256"],
    )
    check(
        "the frozen holdout still matches what was measured",
        manifest["freezes"]["holdout"]["combined_holdout_sha256"]
        == holdout["combined_holdout_sha256"],
    )
    for relative, expected in holdout["pinned_sha256_lf_normalized"].items():
        check(
            f"holdout artifact unchanged: {relative}",
            _sha256_lf(BACKEND_ROOT / relative) == expected,
        )
    check(
        "freezes were verified on both sides of the run",
        manifest["freezes"]["verified_before_run"] is True
        and manifest["freezes"]["verified_after_run"] is True
        and summary["freezes_verified_after_run"] is not None,
    )
    check(
        "both runs executed before any result was opened",
        manifest["run_roles"]["both_runs_executed_before_any_result_opened"] is True
        and manifest["run_roles"]["runs_averaged_or_pooled"] is False,
    )
    check(
        "the correction non-replication is recorded, not hidden",
        manifest["analysis_b_fixed_upstream"]["value_and_scope_correction_recall"][
            "identical"
        ]
        is True
        and manifest["analysis_a_run_a_primary"]["correction_recall_by_operation"][
            "value_and_scope_combined"
        ]
        == "8/14",
    )
    check(
        "the accumulated-state replication is recorded with its CI",
        manifest["analysis_b_fixed_upstream"]["paired_bootstrap_95_ci"][0] > 0
        and manifest["analysis_b_fixed_upstream"]["episodes_improved"] == 13
        and manifest["analysis_b_fixed_upstream"]["episodes_worsened"] == 3,
    )
    check(
        "the precision regression off the development fixture is recorded",
        manifest["analysis_a_run_a_primary"]["c2u_fp_count"] == 7
        and manifest["analysis_a_run_a_primary"]["candidate_fp_count"] == 17
        and any(
            "C2U FP 0 to 7" in item for item in manifest["what_does_not_replicate"]
        ),
    )
    check(
        "the dominant residual failure is named",
        manifest["newly_established"]["dominant_residual_failure"]
        == "semantic dimension attribution"
        and manifest["analysis_a_run_a_primary"][
            "dimension_attribution_negatives_clean"
        ]
        == "0/6",
    )
    check(
        "patching this holdout is explicitly forbidden",
        "patching v1.4 against this holdout" in manifest["forbidden_next_actions"]
        and "re-running this holdout for a better number"
        in manifest["forbidden_next_actions"],
    )
    for artifact in manifest["git_ignored_artifacts"].values():
        path = BACKEND_ROOT / artifact["path"]
        if path.exists():
            check(
                f"optional artifact hash: {artifact['path']}",
                _sha256(path) == artifact["sha256"],
            )
    print("SEGSE confirmatory result verification completed.")


if __name__ == "__main__":
    main()
