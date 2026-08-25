"""Rebuild a compact summary from an immutable raw live-replay report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.poster_live_replay import (  # noqa: E402
    PosterLiveScenarioMetrics,
    aggregate_live_replay_metrics,
)
from app.llm import write_report  # noqa: E402

DEFAULT_INPUT = BACKEND_ROOT / "reports" / "poster_scenarios_live_v1.json"
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "poster_scenarios_live_v1_summary.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize(input_path: Path, output_path: Path) -> dict[str, object]:
    report = json.loads(input_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != "poster-live-replay-v1":
        raise ValueError("unsupported raw replay schema")
    metrics = [
        PosterLiveScenarioMetrics.model_validate(item["metrics"])
        for item in report["scenarios"]
    ]
    summary = {
        "schema_version": "poster-live-replay-summary-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_report_sha256": _sha256(input_path),
        "dataset_sha256": report["dataset_sha256"],
        "provider": report["provider"],
        "requested_model": report["requested_model"],
        "prompt_versions": {
            "understanding": report["understanding_prompt_version"],
            "clarify": report["clarify_prompt_version"],
            "recommend": report["recommend_prompt_version"],
        },
        "review_retrieval_mode": report["review_retrieval_mode"],
        "metrics": aggregate_live_replay_metrics(metrics),
        "scenario_metrics": [item.model_dump(mode="json") for item in metrics],
        "limitations": [
            "These are researcher-authored controlled scenarios, not human conversation logs.",
            "This replay measures the live pipeline on frozen inputs; it is not a user study.",
            "Candidate relevance still requires independent human annotation.",
        ],
    }
    write_report(output_path, summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = summarize(args.input.resolve(), args.output.resolve())
    print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
