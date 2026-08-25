"""정규식 베이스라인과 Luxia Understanding을 동일 gold set으로 평가한다."""

from __future__ import annotations

import argparse
import asyncio
import time
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.baselines import regex_understand_utterance  # noqa: E402
from app.config import load_llm_settings  # noqa: E402
from app.evaluation import (  # noqa: E402
    UnderstandingEvalCase,
    aggregate_understanding_scores,
    load_understanding_eval_dataset,
    score_understanding_prediction,
)
from app.llm import build_client, write_report  # noqa: E402
from app.llm.trace import LLMTraceRecord, TraceWriter  # noqa: E402
from app.nodes import UNDERSTANDING_PROMPT_VERSION, understand_utterance  # noqa: E402

DEFAULT_DATASET = BACKEND_ROOT / "data" / "understanding_eval_v1.json"
TRACE_FILENAME = "understanding_eval_trace.jsonl"


def _failure_score(
    case: UnderstandingEvalCase, error: Exception, latency_ms: float
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
        "predicted_candidate_count": 0,
        "duplicate_candidate_count": 0,
        "forbidden_hits": [],
        "unmapped_ids": [],
        "error_type": type(error).__name__,
        "error": str(error),
        "latency_ms": latency_ms,
    }


def _result_payload(
    case: UnderstandingEvalCase,
    *,
    prediction: Any | None,
    score: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "tags": case.tags,
        "utterance": case.utterance,
        "gold": case.gold.model_dump(mode="json"),
        "prediction": prediction.model_dump(mode="json") if prediction else None,
        "score": score,
    }


def run_regex(cases: list[UnderstandingEvalCase]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        try:
            prediction = regex_understand_utterance(
                case.utterance, case.previous_state_summary
            )
            latency_ms = (time.perf_counter() - started) * 1000
            score = score_understanding_prediction(
                case, prediction, latency_ms=latency_ms
            )
        except Exception as exc:  # 한 case 실패가 전체 평가를 중단하지 않게 한다.
            prediction = None
            latency_ms = (time.perf_counter() - started) * 1000
            score = _failure_score(case, exc, latency_ms)
        results.append(_result_payload(case, prediction=prediction, score=score))
    return results


async def run_luxia(
    cases: list[UnderstandingEvalCase],
    *,
    concurrency: int,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[LLMTraceRecord]]:
    settings = load_llm_settings()
    if settings.provider != "luxia":
        raise RuntimeError(
            "--provider luxia 평가에는 LLM_PROVIDER=luxia 설정이 필요합니다."
        )
    settings.require_api_key()
    client = build_client(
        settings,
        trace=True,
        trace_filename=TRACE_FILENAME,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def evaluate_one(
        case: UnderstandingEvalCase, turn: int
    ) -> dict[str, Any]:
        async with semaphore:
            started = time.perf_counter()
            try:
                prediction = await understand_utterance(
                    client,
                    utterance=case.utterance,
                    previous_state_summary=case.previous_state_summary,
                    conversation_id=f"{run_id}:{case.id}",
                    turn=turn,
                )
                latency_ms = (time.perf_counter() - started) * 1000
                score = score_understanding_prediction(
                    case, prediction, latency_ms=latency_ms
                )
            except Exception as exc:  # trace에 원인이 남고 나머지 case는 계속한다.
                prediction = None
                latency_ms = (time.perf_counter() - started) * 1000
                score = _failure_score(case, exc, latency_ms)
            return _result_payload(case, prediction=prediction, score=score)

    try:
        results = await asyncio.gather(
            *(evaluate_one(case, index) for index, case in enumerate(cases, start=1))
        )
    finally:
        await client.aclose()

    records = [
        record
        for record in TraceWriter(
            settings.log_dir, filename=TRACE_FILENAME
        ).read_all()
        if record.conversation_id and record.conversation_id.startswith(f"{run_id}:")
    ]
    return results, records


def summarize_traces(records: list[LLMTraceRecord]) -> dict[str, Any]:
    attempts = sum(len(record.attempts) for record in records)
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
        "attempt_count": attempts,
        "reported_models": dict(
            Counter(record.reported_model or "missing" for record in records)
        ),
        "input_tokens": sum(record.input_tokens or 0 for record in records),
        "output_tokens": sum(record.output_tokens or 0 for record in records),
    }


def select_cases(
    all_cases: list[UnderstandingEvalCase],
    requested_ids: list[str] | None,
    limit: int | None,
) -> list[UnderstandingEvalCase]:
    selected = all_cases
    if requested_ids:
        requested = set(requested_ids)
        selected = [case for case in all_cases if case.id in requested]
        missing = requested - {case.id for case in selected}
        if missing:
            raise ValueError(f"없는 case id: {sorted(missing)}")
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit은 1 이상이어야 합니다.")
        selected = selected[:limit]
    return selected


async def execute(args: argparse.Namespace) -> Path:
    dataset = load_understanding_eval_dataset(args.dataset)
    if dataset.schema_version != "understanding-v1-generic-electronics":
        raise RuntimeError(f"지원하지 않는 schema version: {dataset.schema_version}")
    cases = select_cases(dataset.cases, args.cases, args.limit)
    run_id = f"understanding-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"

    if args.provider == "regex":
        results = run_regex(cases)
        trace_summary: dict[str, Any] | None = None
        provider_version = "regex-understanding-v1"
    else:
        results, trace_records = await run_luxia(
            cases, concurrency=args.concurrency, run_id=run_id
        )
        trace_summary = summarize_traces(trace_records)
        provider_version = UNDERSTANDING_PROMPT_VERSION

    scores = [result["score"] for result in results]
    metrics = aggregate_understanding_scores(scores, total_cases=len(cases))
    output = args.output or (
        BACKEND_ROOT / "reports" / f"understanding_eval_{args.provider}.json"
    )
    write_report(
        output,
        {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": args.provider,
            "provider_version": provider_version,
            "dataset_version": dataset.dataset_version,
            "schema_version": dataset.schema_version,
            "selected_case_ids": [case.id for case in cases],
            "metrics": metrics,
            "trace_summary": trace_summary,
            "cases": results,
        },
    )

    print(f"provider={args.provider} cases={len(cases)}")
    print(f"validation_success_rate={metrics['validation_success_rate']:.3f}")
    print(f"canonical_id_exact_accuracy={metrics['canonical_id_exact_accuracy']:.3f}")
    print(f"canonical_id_micro_f1={metrics['canonical_id_micro_f1']:.3f}")
    print(f"intent_exact_accuracy={metrics['intent_exact_accuracy']:.3f}")
    print(f"item_action_exact_accuracy={metrics['item_action_exact_accuracy']:.3f}")
    print(f"origin_accuracy={metrics['origin_accuracy_on_matched_ids']:.3f}")
    print(f"report={output.resolve()}")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("regex", "luxia"), default="regex")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
