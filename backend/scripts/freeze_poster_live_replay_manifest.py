"""Freeze the first live replay summary without committing raw LLM traces."""

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

from app.llm import write_report  # noqa: E402

DEFAULT_SUMMARY = BACKEND_ROOT / "reports" / "poster_scenarios_live_v1_summary.json"
DEFAULT_RETRY = (
    BACKEND_ROOT / "reports" / "poster_scenarios_live_v1_ph14_retry_summary.json"
)
DEFAULT_OUTPUT = (
    BACKEND_ROOT / "data" / "manifests" / "poster_live_replay_luxia_v1.json"
)
RUNNER_PATH = BACKEND_ROOT / "scripts" / "run_poster_scenarios_live.py"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=BACKEND_ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def freeze(summary_path: Path, retry_path: Path, output_path: Path) -> dict[str, object]:
    if output_path.exists():
        raise RuntimeError(f"refusing to overwrite frozen manifest: {output_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    retry = json.loads(retry_path.read_text(encoding="utf-8"))
    scenario_metrics = summary["scenario_metrics"]
    retry_metrics = retry["scenario_metrics"][0]
    manifest = {
        "schema_version": "poster-live-replay-run-manifest-v1",
        "run_status": "first_run_with_recorded_failure",
        "frozen_after_first_complete_attempt": True,
        "production_system_commit": _git_head(),
        "runner_sha256": _sha256(RUNNER_PATH),
        "source_summary_sha256": _sha256(summary_path),
        "raw_report_sha256": summary["raw_report_sha256"],
        "dataset_sha256": summary["dataset_sha256"],
        "provider": summary["provider"],
        "requested_model": summary["requested_model"],
        "prompt_versions": summary["prompt_versions"],
        "review_retrieval_mode": summary["review_retrieval_mode"],
        "metrics": summary["metrics"],
        "scenario_outcomes": [
            {
                "scenario_id": item["scenario_id"],
                "status": item["status"],
                "completed_turn_count": item["completed_turn_count"],
                "canonical_id_exact": item["canonical_id_exact"],
                "hard_filter_exact": item["hard_filter_exact"],
                "final_lane": item["final_lane"],
                "final_recommendation_count": item["final_recommendation_count"],
                "error_type": item["error_type"],
            }
            for item in scenario_metrics
        ],
        "separate_retry_observation": {
            "scenario_id": retry_metrics["scenario_id"],
            "source_summary_sha256": _sha256(retry_path),
            "status": retry_metrics["status"],
            "completed_turn_count": retry_metrics["completed_turn_count"],
            "canonical_id_exact": retry_metrics["canonical_id_exact"],
            "hard_filter_exact": retry_metrics["hard_filter_exact"],
            "final_lane": retry_metrics["final_lane"],
            "interpretation": (
                "The schema failure did not recur, but the scenario still ended in "
                "clarify-lane. The retry is not substituted into first-run metrics."
            ),
        },
        "interpretation": [
            "The frozen gold-state packet is not an end-to-end live-pipeline result.",
            "Only 8 of 20 scenarios reached the expected final recommend lane on the first run.",
            "No production prompt, retriever, or ranker was changed after viewing this run.",
        ],
        "limitations": summary["limitations"],
    }
    write_report(output_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--retry-summary", type=Path, default=DEFAULT_RETRY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = freeze(
        args.summary.resolve(),
        args.retry_summary.resolve(),
        args.output.resolve(),
    )
    print(json.dumps(manifest["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
