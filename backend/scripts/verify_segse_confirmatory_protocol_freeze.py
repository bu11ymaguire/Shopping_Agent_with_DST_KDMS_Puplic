"""Verify the confirmatory protocol has not moved since it was closed by hash."""

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

FREEZE = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_v1_freeze.json"
)


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"[ok] {label}")


def _sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def main() -> None:
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    check(
        "protocol is closed before any holdout data or run",
        freeze["status"] == "protocol_closed_before_holdout_authoring"
        and freeze["holdout_exists"] is False
        and freeze["any_confirmatory_run_completed"] is False
        and freeze["confirmatory_evidence"] is False,
    )
    for relative, expected in freeze["pinned_sha256_lf_normalized"].items():
        check(
            f"pinned protocol artifact unchanged: {relative}",
            _sha256_lf(BACKEND_ROOT / relative) == expected,
        )
    recomputed = hashlib.sha256(
        json.dumps(
            freeze["pinned_sha256_lf_normalized"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    check(
        "combined protocol fingerprint recomputes",
        recomputed == freeze["combined_protocol_sha256"],
        recomputed,
    )
    check(
        "the frozen method it evaluates has not moved either",
        freeze["evaluated_method_freeze"]["combined_method_sha256"]
        == method_fingerprint()["combined_method_sha256"],
    )
    closed = freeze["closed_decisions"]
    check(
        "primary outcome is closed as graded closeness with exact match demoted",
        closed["primary_outcome"] == "final_dst_closeness_to_gold_state"
        and closed["primary_is_graded_not_binary"] is True
        and closed["exact_match_is_the_headline"] is False,
    )
    check(
        "the paired improvement second primary is closed",
        closed["second_primary"] == "final_state_closeness_improvement_over_v13"
        and closed["decision_rule_is_comparative"] is True,
    )
    check(
        "holdout shape and negative coverage are closed",
        closed["holdout_scenarios"] == 20
        and closed["holdout_turns"] == 80
        and closed["dimension_attribution_negative_turns"] == 6,
    )
    check(
        "test-retest is enabled but cannot decide pass or fail",
        closed["test_retest_enabled"] is True
        and closed["run_b_decides_pass_or_fail"] is False,
    )
    check(
        "the next step is holdout authoring, not another method change",
        freeze["next_step"].startswith("author the 20 scenario by 4 turn untouched holdout"),
    )
    print("SEGSE confirmatory protocol freeze verification completed.")


if __name__ == "__main__":
    main()
