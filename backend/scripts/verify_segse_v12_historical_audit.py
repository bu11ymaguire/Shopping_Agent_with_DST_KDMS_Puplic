"""Verify the SEGSE v1.2 historical freeze mismatch is recorded, not repaired.

This script passes when the mismatch still has exactly the shape the audit note
records.  It deliberately does not make the historical v1.2 verifier pass, and it
fails loudly if anyone edits the historical freeze manifest to hide the mismatch.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

AUDIT = BACKEND_ROOT / "data" / "manifests" / "segse_v12_historical_freeze_audit.json"
DOC = BACKEND_ROOT / "docs" / "segse_v12_historical_freeze_audit.md"
HISTORICAL = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_dev_v12_protocol.json"
)
V14_FREEZE = BACKEND_ROOT / "data" / "manifests" / "segse_v14_method_freeze.json"
SUBJECT = "app/segse_experiment_v12.py"


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"[ok] {label}")


def _sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def main() -> None:
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))
    historical = json.loads(HISTORICAL.read_text(encoding="utf-8"))
    v14_freeze = json.loads(V14_FREEZE.read_text(encoding="utf-8"))

    check("audit document exists", DOC.exists())
    check(
        "the audit is classified as a historical mismatch, not a current pass",
        audit["status"] == "expected_historical_mismatch_recorded_not_repaired"
        and audit["classification"] == "FAIL_AS_HISTORICAL_MISMATCH"
        and audit["must_not_be_reported_as"] == "PASS_CURRENT",
    )

    recorded_expected = audit["hashes"]["original_frozen_expected_sha256_lf_normalized"]
    recorded_actual = audit["hashes"]["current_working_tree_sha256_lf_normalized"]
    check(
        "the historical manifest still pins the original hash, unedited",
        historical["frozen_sources_sha256_lf_normalized"][SUBJECT]
        == recorded_expected,
        historical["frozen_sources_sha256_lf_normalized"][SUBJECT],
    )
    check(
        "the working-tree hash still matches what the audit recorded",
        _sha256_lf(BACKEND_ROOT / SUBJECT) == recorded_actual,
        _sha256_lf(BACKEND_ROOT / SUBJECT),
    )
    check(
        "the mismatch is still real",
        recorded_expected != recorded_actual,
    )
    check(
        "the historical manifest status is untouched",
        historical["status"] == audit["subject"]["historical_manifest_status"],
    )

    changed_at = audit["source_change"]["changed_at_commit"]
    log = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", f"backend/{SUBJECT}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    check(
        "the recorded change commit is still the last one to touch the file",
        log == changed_at,
        f"recorded {changed_at[:8]}, actual {log[:8]}",
    )
    check(
        "the change happened after the v1.2 freeze",
        audit["source_change"]["occurred_after_the_v12_freeze"] is True,
    )

    check(
        "v1.4 is unaffected because it pins the current hash",
        audit["impact_on_v13_and_v14"]["impact"] == "none"
        and v14_freeze["method"]["source_sha256_lf_normalized"][SUBJECT]
        == recorded_actual,
    )

    not_taken = set(audit["actions_deliberately_not_taken"])
    check(
        "no repair action was taken on the historical record",
        {
            "changing the expected hash in the historical v1.2 manifest",
            "adjusting the historical verifier so that it passes",
            "moving or retagging any freeze tag",
            "reclassifying the failure as a current pass",
        }
        <= not_taken,
    )
    check(
        "the historical verifier is expected to keep failing",
        audit["expected_verifier_behaviour"][
            "scripts/verify_tablet_domain_segse_dev_v12.py"
        ].startswith("expected to fail"),
    )
    print("SEGSE v1.2 historical freeze audit verification completed.")


if __name__ == "__main__":
    main()
