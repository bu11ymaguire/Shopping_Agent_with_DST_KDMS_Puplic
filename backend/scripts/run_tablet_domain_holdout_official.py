"""Run the frozen 20-scenario holdout once under all three conditions.

This command is intentionally batch-only: it executes Full, No-memory, and
No-review in that order, checkpoints raw output, and computes metrics only after
all three conditions have ended. Existing official output is never overwritten.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.actual_service import build_tablet_domain_v2_experiment_service  # noqa: E402
from app.config import load_llm_settings, load_review_retrieval_settings  # noqa: E402
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_official import (  # noqa: E402
    active_state_ids,
    aggregate_condition_metrics,
    compare_condition_outputs,
    score_holdout_turn,
    trace_summary,
)
from app.experiment_conditions import CONDITION_CONTRACT  # noqa: E402
from app.llm import LLMHTTPError, LLMTimeoutError, write_report  # noqa: E402
from app.models.actual_demo import TabletDomainUnderstandingOutput  # noqa: E402
from app.nodes.actual_response import (  # noqa: E402
    ACTUAL_CLARIFY_PROMPT_VERSION,
    ACTUAL_RECOMMEND_PROMPT_VERSION,
)
from app.nodes.tablet_domain_understanding import (  # noqa: E402
    TABLET_DOMAIN_SYSTEM_PROMPT,
    TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
)

CONDITION_ORDER = ("full", "no_memory", "no_review")
DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEFAULT_V2_MANIFEST = BACKEND_ROOT / "data" / "manifests" / "tablet_domain_v2_freeze.json"
DEFAULT_HOLDOUT_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_multiturn_holdout_v1.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1_summary.json"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).strip()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify_frozen_inputs(
    dataset_path: Path,
    v2_manifest_path: Path,
    holdout_manifest_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    v2 = _load_json(v2_manifest_path)
    holdout = _load_json(holdout_manifest_path)
    if holdout["status"] != "frozen_before_first_system_run":
        raise RuntimeError("holdout manifest is not in the pre-run frozen state")
    if _sha256(dataset_path) != holdout["dataset_sha256"]:
        raise RuntimeError("holdout dataset hash differs from the frozen manifest")
    if _sha256(v2_manifest_path) != holdout["v2_freeze_manifest_sha256"]:
        raise RuntimeError("v2 freeze manifest hash differs from the holdout freeze")
    for relative, expected in v2["frozen_runtime"]["source_sha256"].items():
        if _sha256(BACKEND_ROOT / relative) != expected:
            raise RuntimeError(f"frozen runtime source changed: {relative}")
    return v2, holdout


def _verify_runtime_settings(v2: dict[str, Any]) -> dict[str, Any]:
    llm = load_llm_settings()
    retrieval = load_review_retrieval_settings()
    frozen = v2["frozen_runtime"]
    if llm.provider != frozen["provider"]:
        raise RuntimeError("LLM provider differs from the frozen runtime")
    if llm.requested_model != frozen["requested_model"]:
        raise RuntimeError("requested model differs from the frozen runtime")
    if llm.temperature != 0.0:
        raise RuntimeError("official holdout requires LLM_TEMPERATURE=0")
    expected_retrieval = frozen["review_retrieval"]
    actual_retrieval = {
        "mode": retrieval.mode,
        "embedding_model": retrieval.embedding_model,
        "embedding_revision": retrieval.embedding_revision,
        "reranker_model": retrieval.reranker_model,
        "reranker_revision": retrieval.reranker_revision,
        "bi_encoder_per_product": retrieval.bi_encoder_per_product,
        "reranked_per_product": retrieval.reranked_per_product,
    }
    for key, value in actual_retrieval.items():
        if value != expected_retrieval[key]:
            raise RuntimeError(f"review retrieval setting differs: {key}")
    if _text_sha256(TABLET_DOMAIN_SYSTEM_PROMPT) != frozen["system_prompt_sha256"]:
        raise RuntimeError("tablet-domain system prompt hash differs from freeze")
    llm.require_api_key()
    return {
        "provider": llm.provider,
        "requested_model": llm.requested_model,
        "temperature": llm.temperature,
        "top_p": llm.top_p,
        "max_tokens": llm.max_tokens,
        "timeout_seconds": llm.timeout_seconds,
        "max_transport_retries": llm.max_retries,
        "review_retrieval": actual_retrieval,
    }


def _is_infrastructure_error(error: Exception) -> bool:
    if isinstance(error, LLMTimeoutError):
        return True
    return isinstance(error, LLMHTTPError) and (
        error.status_code == 0 or error.status_code == 429 or error.status_code >= 500
    )


def _safe_error(error: Exception) -> str:
    return str(error)[:500]


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip())
    ]


def _base_report(
    *,
    run_id: str,
    dataset_path: Path,
    v2_manifest_path: Path,
    holdout_manifest_path: Path,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "tablet-domain-official-holdout-run-v1",
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "batch_protocol": {
            "condition_order": list(CONDITION_ORDER),
            "run_each_condition_once": True,
            "aggregate_only_after_all_conditions": True,
            "intermediate_results_inspected": False,
            "allowed_outer_rerun_reasons": [
                "HTTP 429 or 5xx",
                "timeout",
                "network connection failure",
            ],
            "disallowed_rerun_reasons": [
                "schema-valid semantic error",
                "structured schema validation failure",
                "undesired recommendation or metric",
            ],
            "outer_infrastructure_retry_limit_per_turn": 2,
        },
        "version_control": {
            "execution_commit": _git("rev-parse", "HEAD"),
            "tracked_worktree_clean": not bool(
                _git("status", "--porcelain", "--untracked-files=no")
            ),
            "system_freeze_tag": "tablet-domain-v2.3-freeze",
            "system_freeze_commit": _git(
                "rev-list", "-n", "1", "tablet-domain-v2.3-freeze"
            ),
            "holdout_freeze_tag": "tablet-domain-holdout-v1-freeze",
            "holdout_freeze_commit": _git(
                "rev-list", "-n", "1", "tablet-domain-holdout-v1-freeze"
            ),
        },
        "frozen_inputs": {
            "dataset_path": str(dataset_path.relative_to(BACKEND_ROOT)),
            "dataset_sha256": _sha256(dataset_path),
            "v2_manifest_path": str(v2_manifest_path.relative_to(BACKEND_ROOT)),
            "v2_manifest_sha256": _sha256(v2_manifest_path),
            "holdout_manifest_path": str(
                holdout_manifest_path.relative_to(BACKEND_ROOT)
            ),
            "holdout_manifest_sha256": _sha256(holdout_manifest_path),
        },
        "runtime": {
            **runtime,
            "understanding_prompt_version": TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
            "understanding_schema_version": TabletDomainUnderstandingOutput.schema_version,
            "clarify_prompt_version": ACTUAL_CLARIFY_PROMPT_VERSION,
            "recommend_prompt_version": ACTUAL_RECOMMEND_PROMPT_VERSION,
        },
        "condition_contract": CONDITION_CONTRACT,
        "catalog": None,
        "conditions": {
            condition: {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "trace_filename": f"tablet_domain_holdout_{run_id}_{condition}_llm_trace.jsonl",
                "trace_sha256": None,
                "trace_summary": None,
                "scenarios": [],
                "metrics": None,
            }
            for condition in CONDITION_ORDER
        },
        "comparisons": None,
    }


async def _run_turn_with_infrastructure_retries(
    service: Any,
    conversation_id: str,
    utterance: str,
    *,
    retry_limit: int,
) -> tuple[Any, float, int]:
    infrastructure_retries = 0
    while True:
        started = time.perf_counter()
        try:
            turn = await service.run_turn(conversation_id, utterance)
            return (
                turn,
                round((time.perf_counter() - started) * 1000, 1),
                infrastructure_retries,
            )
        except Exception as error:
            if not _is_infrastructure_error(error) or infrastructure_retries >= retry_limit:
                raise
            infrastructure_retries += 1
            await asyncio.sleep(min(5 * infrastructure_retries, 15))


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset.resolve()
    output = args.output.resolve()
    summary_output = args.summary_output.resolve()
    if output.exists() or summary_output.exists():
        raise RuntimeError(
            "official output already exists; refusing to overwrite or rerun the frozen batch"
        )
    v2_manifest_path = args.v2_manifest.resolve()
    holdout_manifest_path = args.holdout_manifest.resolve()
    v2, _ = _verify_frozen_inputs(
        dataset_path, v2_manifest_path, holdout_manifest_path
    )
    runtime = _verify_runtime_settings(v2)
    dataset = load_tablet_holdout_dataset(dataset_path)
    run_id = f"tablet-holdout-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    report = _base_report(
        run_id=run_id,
        dataset_path=dataset_path,
        v2_manifest_path=v2_manifest_path,
        holdout_manifest_path=holdout_manifest_path,
        runtime=runtime,
    )
    if not report["version_control"]["tracked_worktree_clean"]:
        raise RuntimeError("tracked worktree must be clean for the official run")
    write_report(output, report)

    for condition in CONDITION_ORDER:
        payload = report["conditions"][condition]
        payload["status"] = "running"
        payload["started_at"] = _utc_now()
        trace_path = load_llm_settings().log_dir / payload["trace_filename"]
        if trace_path.exists():
            raise RuntimeError(f"official trace path already exists: {trace_path}")
        service = build_tablet_domain_v2_experiment_service(
            condition, trace_filename=payload["trace_filename"]
        )
        try:
            catalog_status = service.catalog.status()
            catalog_record = {
                "schema_version": catalog_status.schema_version,
                "dataset_revision": catalog_status.dataset_revision,
                "product_count": catalog_status.product_count,
                "review_count": catalog_status.review_count,
            }
            if report["catalog"] is None:
                report["catalog"] = catalog_record
            elif report["catalog"] != catalog_record:
                raise RuntimeError("catalog changed between official conditions")

            for scenario in dataset.scenarios:
                snapshot = await service.create_conversation()
                turns = []
                turn_metrics = []
                gold_active_ids = {"category_tablet"}
                infrastructure_retry_count = 0
                error: Exception | None = None
                error_turn: int | None = None
                for gold_turn in scenario.turns:
                    try:
                        turn, wall_latency_ms, retries = (
                            await _run_turn_with_infrastructure_retries(
                                service,
                                snapshot.conversation_id,
                                gold_turn.utterance,
                                retry_limit=2,
                            )
                        )
                    except Exception as exc:
                        error = exc
                        error_turn = gold_turn.turn
                        break
                    infrastructure_retry_count += retries
                    gold_active_ids.update(gold_turn.gold_candidate_ids)
                    turns.append(turn)
                    turn_metrics.append(
                        score_holdout_turn(
                            gold_turn,
                            turn,
                            service.catalog,
                            wall_latency_ms=wall_latency_ms,
                            gold_active_ids=gold_active_ids,
                            condition=condition,
                        )
                    )

                final_actual_ids = (
                    active_state_ids(turns[-1].dialogue_state)
                    if turns
                    else {"category_tablet"}
                )
                final_expected_ids = set(scenario.final_gold_state_ids)
                scenario_record = {
                    "scenario_id": scenario.id,
                    "title": scenario.title,
                    "status": "error" if error else "completed",
                    "expected_turn_count": len(scenario.turns),
                    "completed_turn_count": len(turns),
                    "infrastructure_outer_retry_count": infrastructure_retry_count,
                    "error_turn": error_turn,
                    "error_type": type(error).__name__ if error else None,
                    "error_classification": (
                        "infrastructure" if error and _is_infrastructure_error(error)
                        else "semantic_or_schema" if error
                        else None
                    ),
                    "error_message": _safe_error(error) if error else None,
                    "final_state_ids_expected": sorted(final_expected_ids),
                    "final_state_ids_actual": sorted(final_actual_ids),
                    "final_state_counts": {
                        "exact": final_expected_ids == final_actual_ids,
                        "true_positive": len(final_expected_ids & final_actual_ids),
                        "false_positive": len(final_actual_ids - final_expected_ids),
                        "false_negative": len(final_expected_ids - final_actual_ids),
                    },
                    "turn_metrics": turn_metrics,
                    "turns": [turn.model_dump(mode="json") for turn in turns],
                }
                payload["scenarios"].append(scenario_record)
                write_report(output, report)
                print(
                    f"{condition}: {scenario.id} ({len(turns)}/{len(scenario.turns)} turns)",
                    flush=True,
                )
        finally:
            await service.aclose()
        payload["completed_at"] = _utc_now()
        payload["status"] = "completed"
        write_report(output, report)

    # No condition metrics are computed or printed before reaching this point.
    for condition in CONDITION_ORDER:
        payload = report["conditions"][condition]
        trace_path = load_llm_settings().log_dir / payload["trace_filename"]
        trace_records = _read_trace(trace_path)
        payload["trace_sha256"] = _sha256(trace_path)
        payload["trace_summary"] = trace_summary(trace_records)
        payload["metrics"] = aggregate_condition_metrics(payload["scenarios"])
    report["comparisons"] = compare_condition_outputs(report)
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output, report)

    summary = {
        "schema_version": "tablet-domain-official-holdout-summary-v1",
        "generated_at": _utc_now(),
        "run_id": run_id,
        "raw_report_sha256": _sha256(output),
        "dataset_sha256": report["frozen_inputs"]["dataset_sha256"],
        "version_control": report["version_control"],
        "runtime": report["runtime"],
        "catalog": report["catalog"],
        "condition_metrics": {
            condition: report["conditions"][condition]["metrics"]
            for condition in CONDITION_ORDER
        },
        "condition_trace_summaries": {
            condition: report["conditions"][condition]["trace_summary"]
            for condition in CONDITION_ORDER
        },
        "comparisons": report["comparisons"],
        "limitations": [
            "No product or review NDCG is available before blind human relevance annotation.",
            "Retrieval and Cross-Encoder latency share one current workflow trace boundary.",
            "The primary result is one frozen batch; repeated runs would be a separate robustness analysis.",
        ],
    }
    write_report(summary_output, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"raw_report={output}", flush=True)
    print(f"summary_report={summary_output}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--v2-manifest", type=Path, default=DEFAULT_V2_MANIFEST)
    parser.add_argument(
        "--holdout-manifest", type=Path, default=DEFAULT_HOLDOUT_MANIFEST
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
