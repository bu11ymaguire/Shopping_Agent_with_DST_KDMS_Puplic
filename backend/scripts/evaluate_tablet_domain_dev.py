"""Evaluate the development-only tablet-domain v2 Understanding prompt with Luxia."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_llm_settings  # noqa: E402
from app.evaluation.tablet_domain_understanding import (  # noqa: E402
    TabletDomainDevCase,
    aggregate_tablet_domain_scores,
    load_tablet_domain_dev_dataset,
    score_tablet_domain_prediction,
)
from app.llm import build_client, write_report  # noqa: E402
from app.llm.trace import LLMTraceRecord, TraceWriter  # noqa: E402
from app.nodes.tablet_domain_understanding import (  # noqa: E402
    TABLET_DOMAIN_SYSTEM_PROMPT,
    TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
    understand_tablet_domain_utterance,
)

DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_understanding_dev_v1.json"
TRACE_FILENAME = "tablet_domain_understanding_dev_trace.jsonl"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _failure_score(
    case: TabletDomainDevCase,
    error: Exception,
    latency_ms: float,
) -> dict[str, Any]:
    return {
        "validation_success": False,
        "intent_tp": 0,
        "intent_fp": 0,
        "intent_fn": len(set(case.gold.intents)),
        "candidate_tp": 0,
        "candidate_fp": 0,
        "candidate_fn": len(case.gold.candidates),
        "target_correct": 0,
        "origin_correct": 0,
        "shared_candidate_count": 0,
        "value_checked_count": 0,
        "value_correct": 0,
        "evidence_checked_count": 0,
        "evidence_correct": 0,
        "predicted_candidate_count": 0,
        "duplicate_candidate_count": 0,
        "forbidden_hits": [],
        "unmapped_ids": [],
        "error_type": type(error).__name__,
        "error": str(error),
        "latency_ms": latency_ms,
    }


async def run_luxia(
    cases: list[TabletDomainDevCase],
    *,
    concurrency: int,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[LLMTraceRecord]]:
    settings = load_llm_settings()
    if settings.provider != "luxia":
        raise RuntimeError("Tablet-domain dev evaluation requires LLM_PROVIDER=luxia.")
    settings.require_api_key()
    client = build_client(settings, trace=True, trace_filename=TRACE_FILENAME)
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(
        case: TabletDomainDevCase,
        turn: int,
    ) -> dict[str, Any]:
        async with semaphore:
            started = time.perf_counter()
            try:
                prediction = await understand_tablet_domain_utterance(
                    client,
                    utterance=case.utterance,
                    previous_state_summary=case.previous_state_summary,
                    conversation_id=f"{run_id}:{case.id}",
                    turn=turn,
                )
                latency_ms = (time.perf_counter() - started) * 1000
                score = score_tablet_domain_prediction(
                    case,
                    prediction,
                    latency_ms=latency_ms,
                )
            except Exception as exc:
                prediction = None
                latency_ms = (time.perf_counter() - started) * 1000
                score = _failure_score(case, exc, latency_ms)
            return {
                "case_id": case.id,
                "tags": case.tags,
                "utterance": case.utterance,
                "gold": case.gold.model_dump(mode="json"),
                "prediction": (
                    prediction.model_dump(mode="json") if prediction else None
                ),
                "score": score,
            }

    try:
        results = await asyncio.gather(
            *(evaluate_one(case, index) for index, case in enumerate(cases, start=1))
        )
    finally:
        await client.aclose()

    records = [
        record
        for record in TraceWriter(settings.log_dir, filename=TRACE_FILENAME).read_all()
        if record.conversation_id
        and record.conversation_id.startswith(f"{run_id}:")
    ]
    return results, records


def summarize_traces(records: list[LLMTraceRecord]) -> dict[str, Any]:
    return {
        "trace_count": len(records),
        "validation_success_rate": (
            sum(record.validation_success for record in records) / len(records)
            if records
            else None
        ),
        "structured_mode_distribution": dict(
            Counter(record.structured_mode or "none" for record in records)
        ),
        "fallback_rate": (
            sum(record.fallback_used for record in records) / len(records)
            if records
            else None
        ),
        "schema_repair_count": sum(record.retry_count for record in records),
        "reported_models": dict(
            Counter(record.reported_model or "missing" for record in records)
        ),
    }


def select_cases(
    all_cases: list[TabletDomainDevCase],
    requested_ids: list[str] | None,
    limit: int | None,
) -> list[TabletDomainDevCase]:
    selected = all_cases
    if requested_ids:
        requested = set(requested_ids)
        selected = [case for case in selected if case.id in requested]
        missing = requested - {case.id for case in selected}
        if missing:
            raise ValueError(f"Unknown case IDs: {sorted(missing)}")
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be at least 1")
        selected = selected[:limit]
    return selected


async def execute(args: argparse.Namespace) -> Path:
    dataset_bytes = args.dataset.read_bytes()
    dataset = load_tablet_domain_dev_dataset(args.dataset)
    cases = select_cases(dataset.cases, args.cases, args.limit)
    run_id = (
        "tablet-domain-dev-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"-{uuid4().hex[:8]}"
    )
    results, traces = await run_luxia(
        cases,
        concurrency=args.concurrency,
        run_id=run_id,
    )
    metrics = aggregate_tablet_domain_scores(
        [result["score"] for result in results],
        total_cases=len(cases),
    )
    output = args.output or (
        BACKEND_ROOT / "reports" / "tablet_domain_understanding_dev_v1.json"
    )
    write_report(
        output,
        {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": "luxia",
            "prompt_version": TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
            "dataset_version": dataset.dataset_version,
            "split": dataset.split,
            "schema_version": dataset.schema_version,
            "dataset_sha256": _sha256_bytes(dataset_bytes),
            "system_prompt_sha256": _sha256_bytes(
                TABLET_DOMAIN_SYSTEM_PROMPT.encode("utf-8")
            ),
            "protocol": (
                "Development-only prompt iteration. These metrics must not be "
                "reported as untouched holdout performance."
            ),
            "selected_case_ids": [case.id for case in cases],
            "metrics": metrics,
            "trace_summary": summarize_traces(traces),
            "cases": results,
        },
    )
    print(f"provider=luxia split=dev cases={len(cases)}")
    for key in (
        "validation_success_rate",
        "domain_route_exact_accuracy",
        "unsupported_no_tablet_mutation_accuracy",
        "canonical_id_exact_accuracy",
        "canonical_id_micro_f1",
        "intent_exact_accuracy",
        "item_action_exact_accuracy",
        "tradeoff_exact_accuracy",
        "candidate_value_semantics_accuracy",
    ):
        print(f"{key}={metrics[key]:.3f}")
    print(f"report={output.resolve()}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
