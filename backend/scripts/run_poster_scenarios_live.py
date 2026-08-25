"""Replay frozen poster scenarios through the live Luxia-backed pipeline."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.actual_service import build_actual_runtime_service  # noqa: E402
from app.config import load_llm_settings, load_review_retrieval_settings  # noqa: E402
from app.evaluation.poster_annotation import load_poster_scenario_dataset  # noqa: E402
from app.evaluation.poster_live_replay import (  # noqa: E402
    PosterLiveScenarioMetrics,
    aggregate_live_replay_metrics,
    build_live_scenario_metrics,
)
from app.llm import write_report  # noqa: E402
from app.nodes.actual_response import (  # noqa: E402
    ACTUAL_CLARIFY_PROMPT_VERSION,
    ACTUAL_RECOMMEND_PROMPT_VERSION,
)
from app.nodes.actual_understanding import (  # noqa: E402
    ACTUAL_UNDERSTANDING_PROMPT_VERSION,
)

DEFAULT_DATASET = BACKEND_ROOT / "data" / "poster_recommendation_scenarios_v1.json"
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "poster_scenarios_live_v1.json"
DEFAULT_SUMMARY = BACKEND_ROOT / "reports" / "poster_scenarios_live_v1_summary.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _base_report(dataset_path: Path) -> dict[str, object]:
    settings = load_llm_settings()
    retrieval = load_review_retrieval_settings()
    return {
        "schema_version": "poster-live-replay-v1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "dataset_path": str(dataset_path.relative_to(BACKEND_ROOT)),
        "dataset_sha256": _sha256(dataset_path),
        "provider": settings.provider,
        "requested_model": settings.requested_model,
        "understanding_prompt_version": ACTUAL_UNDERSTANDING_PROMPT_VERSION,
        "clarify_prompt_version": ACTUAL_CLARIFY_PROMPT_VERSION,
        "recommend_prompt_version": ACTUAL_RECOMMEND_PROMPT_VERSION,
        "review_retrieval_mode": retrieval.mode,
        "scenarios": [],
    }


def _load_or_initialize_report(
    output: Path, dataset_path: Path, *, resume: bool
) -> dict[str, object]:
    if not output.exists():
        return _base_report(dataset_path)
    if not resume:
        raise RuntimeError(f"refusing to overwrite existing report: {output}")
    report = json.loads(output.read_text(encoding="utf-8"))
    if report.get("schema_version") != "poster-live-replay-v1":
        raise RuntimeError("existing report has a different schema version")
    if report.get("dataset_sha256") != _sha256(dataset_path):
        raise RuntimeError("existing report belongs to a different dataset")
    return report


async def execute(args: argparse.Namespace) -> dict[str, object]:
    dataset_path = args.dataset.resolve()
    output = args.output.resolve()
    summary_output = args.summary_output.resolve()
    dataset = load_poster_scenario_dataset(dataset_path)
    scenarios = dataset.scenarios
    if args.scenario:
        selected = set(args.scenario)
        unknown = selected - {item.id for item in scenarios}
        if unknown:
            raise ValueError(f"unknown scenario IDs: {sorted(unknown)}")
        scenarios = [item for item in scenarios if item.id in selected]
    if args.limit:
        scenarios = scenarios[: args.limit]

    report = _load_or_initialize_report(output, dataset_path, resume=args.resume)
    existing = {
        item["scenario_id"]: item
        for item in report["scenarios"]  # type: ignore[index]
        if item["metrics"]["status"] == "completed"
    }
    service = build_actual_runtime_service()
    try:
        catalog_status = service.catalog.status()
        report["catalog"] = {
            "schema_version": catalog_status.schema_version,
            "dataset_revision": catalog_status.dataset_revision,
            "product_count": catalog_status.product_count,
            "review_count": catalog_status.review_count,
        }
        for scenario in scenarios:
            if scenario.id in existing:
                print(f"SKIP {scenario.id}: already completed", flush=True)
                continue
            snapshot = await service.create_conversation()
            turns = []
            latencies = []
            error: Exception | None = None
            for index, utterance in enumerate(scenario.conversation_turns, start=1):
                started = time.perf_counter()
                try:
                    turn = await service.run_turn(snapshot.conversation_id, utterance)
                except Exception as exc:  # live run must checkpoint the failure
                    error = exc
                    print(
                        f"ERROR {scenario.id} turn-{index}: {type(exc).__name__}",
                        flush=True,
                    )
                    break
                latency = round((time.perf_counter() - started) * 1000, 1)
                turns.append(turn)
                latencies.append(latency)
                top = turn.rankings[0].product_id if turn.rankings else "clarify"
                print(
                    f"{scenario.id} turn-{index}: {turn.policy.lane} / "
                    f"top={top} / {latency:.1f} ms",
                    flush=True,
                )
            metrics = build_live_scenario_metrics(
                scenario,
                turns,
                latencies,
                service.catalog,
                error=error,
            )
            record = {
                "scenario_id": scenario.id,
                "title": scenario.title,
                "conversation_turns": scenario.conversation_turns,
                "final_request_summary": scenario.final_request_summary,
                "turns": [turn.model_dump(mode="json") for turn in turns],
                "metrics": metrics.model_dump(mode="json"),
            }
            records = [
                item
                for item in report["scenarios"]  # type: ignore[index]
                if item["scenario_id"] != scenario.id
            ]
            records.append(record)
            report["scenarios"] = records
            write_report(output, report)
    finally:
        await service.aclose()

    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_report(output, report)
    metrics = [
        PosterLiveScenarioMetrics.model_validate(item["metrics"])
        for item in report["scenarios"]  # type: ignore[index]
        if item["scenario_id"] in {scenario.id for scenario in scenarios}
    ]
    summary = {
        "schema_version": "poster-live-replay-summary-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_report_sha256": _sha256(output),
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
    write_report(summary_output, summary)
    print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2), flush=True)
    print(f"raw_report={output}", flush=True)
    print(f"summary_report={summary_output}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--scenario", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
