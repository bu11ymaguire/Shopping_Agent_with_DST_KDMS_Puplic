"""Hash-freeze the SEGSE confirmatory holdout before the first system run."""

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

from app.evaluation.tablet_domain_segse_confirmatory import (  # noqa: E402
    coverage_counts,
    dimension_confusion_families,
    gold_trajectory_problems,
    load_segse_confirmatory_dataset,
)
from app.llm import write_report  # noqa: E402
from app.segse_v14_freeze import method_fingerprint  # noqa: E402

HOLDOUT = BACKEND_ROOT / "data" / "tablet_domain_segse_confirmatory_holdout_v1.json"
PINNED = (
    "data/tablet_domain_segse_confirmatory_holdout_v1.json",
    "app/evaluation/tablet_domain_segse_confirmatory.py",
    "scripts/verify_segse_confirmatory_holdout.py",
)
DEFAULT_OUTPUT = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_holdout_v1_freeze.json"
)
DEFAULT_TAG = "segse-confirmatory-holdout-v1-freeze"


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).strip()


def _sha256_lf(path: Path) -> str:
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--freeze-tag", type=str, default=DEFAULT_TAG)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"holdout freeze already exists: {output}")

    dataset = load_segse_confirmatory_dataset(HOLDOUT)
    problems = gold_trajectory_problems(dataset)
    if problems:
        raise RuntimeError(f"gold is inconsistent; refusing to freeze: {problems[:3]}")

    pinned = {relative: _sha256_lf(BACKEND_ROOT / relative) for relative in PINNED}
    manifest = {
        "schema_version": "tablet-domain-segse-confirmatory-holdout-v1-freeze-v1",
        "status": "frozen_before_first_system_run",
        "freeze_git_tag": args.freeze_tag,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "base_commit_before_freeze_changes": _git("rev-parse", "HEAD"),
        "dataset": {
            "path": PINNED[0],
            "sha256_lf_normalized": pinned[PINNED[0]],
            "dataset_version": dataset.dataset_version,
            "split": dataset.split,
            "scenario_count": len(dataset.scenarios),
            "turn_count": sum(len(item.turns) for item in dataset.scenarios),
            "catalog_scope": dataset.catalog_scope,
        },
        "pinned_sha256_lf_normalized": pinned,
        "combined_holdout_sha256": hashlib.sha256(
            json.dumps(pinned, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "evaluated_method": {
            "freeze_git_tag": "segse-v1.4-freeze",
            "combined_method_sha256": method_fingerprint()["combined_method_sha256"],
        },
        "protocol": {
            "freeze_git_tag": "segse-confirmatory-v1-protocol-freeze",
            "manifest": "data/manifests/tablet_domain_segse_confirmatory_v1_protocol.json",
        },
        "coverage_counts": coverage_counts(dataset),
        "dimension_confusion_families": dimension_confusion_families(dataset),
        "gold_self_consistency": {
            "hard_filters_rederived_from_query_generator": True,
            "policy_rederived_from_deterministic_policy": True,
            "final_tokens_rederived_from_gold_trajectory": True,
            "problems": 0,
        },
        "authoring_discipline": {
            "v14_executed_during_authoring": False,
            "v14_output_read_while_annotating": False,
            "gold_authored_from_frozen_contract_only": True,
            "products_rankings_human_grades_authored": False,
        },
        "any_system_run_completed": False,
        "confirmatory_evidence": False,
        "next_step": "execute Run A then Run B back to back without opening either result",
    }
    write_report(output, manifest)
    print(json.dumps({k: manifest[k] for k in ("status", "dataset", "combined_holdout_sha256")}, ensure_ascii=False, indent=2))
    print(f"freeze_manifest={output}", flush=True)


if __name__ == "__main__":
    main()
