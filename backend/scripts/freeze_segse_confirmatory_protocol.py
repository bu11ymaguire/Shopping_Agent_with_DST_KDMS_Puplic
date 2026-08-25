"""Pin the confirmatory protocol by hash so the holdout cannot be authored against a moving target.

After this freeze the protocol is closed: primary outcome, decision rule, run
roles, composition, and authoring order can no longer change without the freeze
verifier failing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.llm import write_report  # noqa: E402

PINNED_FILES = (
    "docs/tablet_domain_segse_confirmatory_protocol_v1.md",
    "data/manifests/tablet_domain_segse_confirmatory_v1_protocol.json",
    "scripts/verify_tablet_domain_segse_confirmatory_protocol.py",
    "app/evaluation/segse_final_state_closeness.py",
    "scripts/verify_segse_final_state_closeness.py",
)
DEFAULT_OUTPUT = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_v1_freeze.json"
)
DEFAULT_TAG = "segse-confirmatory-v1-protocol-freeze"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_manifest(*, freeze_tag: str) -> dict[str, object]:
    protocol = json.loads(
        (
            BACKEND_ROOT
            / "data"
            / "manifests"
            / "tablet_domain_segse_confirmatory_v1_protocol.json"
        ).read_text(encoding="utf-8")
    )
    method_freeze = json.loads(
        (BACKEND_ROOT / "data" / "manifests" / "segse_v14_method_freeze.json").read_text(
            encoding="utf-8"
        )
    )
    if protocol["holdout_exists"] or protocol["any_confirmatory_run_completed"]:
        raise RuntimeError("the protocol may only be frozen before data and before any run")
    pinned = {relative: _sha256_lf(BACKEND_ROOT / relative) for relative in PINNED_FILES}
    return {
        "schema_version": "tablet-domain-segse-confirmatory-v1-freeze-manifest-v1",
        "status": "protocol_closed_before_holdout_authoring",
        "freeze_git_tag": freeze_tag,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "base_commit_before_freeze_changes": _git("rev-parse", "HEAD"),
        "evaluated_method_freeze": {
            "manifest": "data/manifests/segse_v14_method_freeze.json",
            "freeze_git_tag": method_freeze["freeze_git_tag"],
            "combined_method_sha256": method_freeze["method"]["combined_method_sha256"],
        },
        "protocol_revision": max(
            int(item["revision"]) for item in protocol["amendment_history"]
        ),
        "pinned_sha256_lf_normalized": pinned,
        "combined_protocol_sha256": hashlib.sha256(
            json.dumps(pinned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "closed_decisions": {
            "primary_outcome": protocol["primary_outcome"]["name"],
            "primary_is_graded_not_binary": protocol["primary_outcome"][
                "graded_not_binary"
            ],
            "exact_match_is_the_headline": protocol["primary_outcome"][
                "exact_match_role"
            ]["is_the_headline"],
            "second_primary": protocol["paired_improvement_primary"]["name"],
            "decision_rule_is_comparative": protocol["decision_rule"][
                "the_confirmatory_claim_is_comparative_not_absolute"
            ],
            "holdout_scenarios": protocol["holdout_size"]["scenarios"],
            "holdout_turns": protocol["holdout_size"]["total_turns"],
            "dimension_attribution_negative_turns": protocol[
                "dimension_attribution_negative_design"
            ]["minimum_turns"],
            "test_retest_enabled": protocol["run_roles"]["run_b"]["enabled"],
            "run_b_decides_pass_or_fail": protocol["run_roles"]["run_b"][
                "decides_pass_or_fail"
            ],
        },
        "next_step": "author the 20 scenario by 4 turn untouched holdout, annotate gold from the contract only, validate, hash-freeze the holdout, then execute Run A and Run B back to back",
        "holdout_exists": False,
        "any_confirmatory_run_completed": False,
        "confirmatory_evidence": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--freeze-tag", type=str, default=DEFAULT_TAG)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"protocol freeze manifest already exists: {output}")
    manifest = build_manifest(freeze_tag=args.freeze_tag)
    write_report(output, manifest)
    print(json.dumps(manifest["closed_decisions"], ensure_ascii=False, indent=2), flush=True)
    print(f'combined_protocol_sha256={manifest["combined_protocol_sha256"]}', flush=True)
    print(f"freeze_manifest={output}", flush=True)


if __name__ == "__main__":
    main()
