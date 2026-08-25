"""Freeze the untouched tablet-domain multi-turn holdout before any system run."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.llm import write_report  # noqa: E402

DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEFAULT_OUTPUT = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_multiturn_holdout_v1.json"
)
V2_FREEZE = BACKEND_ROOT / "data" / "manifests" / "tablet_domain_v2_freeze.json"
CONTRACT_PATH = BACKEND_ROOT / "app" / "evaluation" / "tablet_domain_holdout.py"
VERIFIER_PATH = BACKEND_ROOT / "scripts" / "verify_tablet_domain_holdout.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_tag_commit(tag: str) -> str:
    return subprocess.run(
        ["git", "rev-list", "-n", "1", tag],
        cwd=BACKEND_ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def freeze(dataset_path: Path, output_path: Path) -> dict[str, object]:
    if output_path.exists():
        raise RuntimeError(f"refusing to overwrite frozen manifest: {output_path}")
    dataset = load_tablet_holdout_dataset(dataset_path)
    v2 = json.loads(V2_FREEZE.read_text(encoding="utf-8"))
    freeze_tag = dataset.created_after_freeze_tag
    manifest = {
        "schema_version": "tablet-domain-holdout-freeze-manifest-v1",
        "status": "frozen_before_first_system_run",
        "holdout_freeze_git_tag": "tablet-domain-holdout-v1-freeze",
        "created_after_v2_freeze_tag": freeze_tag,
        "v2_freeze_commit": git_tag_commit(freeze_tag),
        "dataset_version": dataset.dataset_version,
        "dataset_sha256": sha256(dataset_path),
        "v2_freeze_manifest_sha256": sha256(V2_FREEZE),
        "contract_sha256": sha256(CONTRACT_PATH),
        "verifier_sha256": sha256(VERIFIER_PATH),
        "frozen_runtime": {
            "understanding_prompt_version": dataset.frozen_prompt_version,
            "understanding_schema_version": v2["frozen_runtime"][
                "understanding_schema_version"
            ],
            "system_prompt_sha256": v2["frozen_runtime"][
                "system_prompt_sha256"
            ],
            "requested_model": v2["frozen_runtime"]["requested_model"],
            "review_retrieval": v2["frozen_runtime"]["review_retrieval"],
            "product_ranking_weights": v2["frozen_runtime"][
                "product_ranking_weights"
            ],
        },
        "evaluation_input": {
            "scenario_count": len(dataset.scenarios),
            "turn_count": sum(len(item.turns) for item in dataset.scenarios),
            "turns_per_scenario": {
                item.id: len(item.turns) for item in dataset.scenarios
            },
            "gold_fields": [
                "domain route",
                "intent",
                "canonical State Diff",
                "policy lane and question target",
                "item action",
                "trade-off",
                "hard constraints",
            ],
            "predefined_recommendation_products": False,
            "predefined_rankings": False,
            "predefined_human_relevance": False,
        },
        "execution_protocol": {
            "conditions": ["full", "no_memory", "no_review"],
            "same_input_all_conditions": True,
            "run_each_condition_once": True,
            "environment_category_all_conditions": "category_tablet",
            "pool_after_execution": "union of condition top-k outputs",
            "blind_before_human_annotation": True,
        },
        "separation": {
            "v1_gold_state_packet": "module-level retrieval/ranking evaluation",
            "this_holdout": "end-to-end Understanding through ranking evaluation",
            "must_not_merge_packets": True,
        },
    }
    write_report(output_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = freeze(args.dataset.resolve(), args.output.resolve())
    print(json.dumps(manifest["evaluation_input"], indent=2))
    print(f"manifest={args.output.resolve()}")


if __name__ == "__main__":
    main()
