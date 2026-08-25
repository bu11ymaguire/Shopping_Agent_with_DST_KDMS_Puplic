"""Run the frozen untouched M0-versus-C confirmatory experiment exactly once."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.actual_service import ActualDemoService  # noqa: E402
from app.actual_workflow import ActualCatalogWorkflow  # noqa: E402
from app.config import load_llm_settings, load_review_retrieval_settings  # noqa: E402
from app.evaluation.tablet_domain_confirmatory import (  # noqa: E402
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CONFIRMATORY_CORRECTION_TURNS,
    aggregate_confirmatory_metrics,
    evaluate_confirmatory_decision,
    paired_bootstrap_state_diff,
    score_confirmatory_turn,
)
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_official import (  # noqa: E402
    active_state_ids,
    trace_summary,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.extended_experiment import (  # noqa: E402
    ExtendedTabletDomainUnderstandingOutput,
    build_extended_experiment_service,
    update_extended_dialogue_state,
)
from app.llm import LLMHTTPError, LLMTimeoutError, write_report  # noqa: E402
from app.nodes.actual_response import ActualTemplateResponseComposer  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.review_retrieval import build_review_retriever  # noqa: E402

DEFAULT_DATASET = (
    BACKEND_ROOT / "data" / "tablet_domain_m0_c_confirmatory_holdout_v1.json"
)
DEFAULT_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_m0_c_confirmatory_protocol_v1.json"
)
DEFAULT_OUTPUT = (
    BACKEND_ROOT / "reports" / "tablet_domain_m0_c_confirmatory_v1.json"
)
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_m0_c_confirmatory_v1.json"
)
DEFAULT_CATALOG = (
    Path.home()
    / "OneDrive"
    / "Desktop"
    / "KDMS_Prepare"
    / "backend"
    / "data"
    / "amazon_reviews_2023"
    / "tablet_catalog_v2"
)
LIVE_STRATEGIES = ("m0_baseline", "c_semantic_noop")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_lf_normalized(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).strip()


def _safe_error(error: Exception) -> str:
    return str(error)[:500]


def _is_infrastructure_error(error: Exception) -> bool:
    if isinstance(error, LLMTimeoutError):
        return True
    return isinstance(error, LLMHTTPError) and (
        error.status_code == 0 or error.status_code == 429 or error.status_code >= 500
    )


async def _run_turn_with_retries(
    service: Any, conversation_id: str, utterance: str
) -> tuple[Any, float, int]:
    retries = 0
    while True:
        started = time.perf_counter()
        try:
            turn = await service.run_turn(conversation_id, utterance)
            return turn, round((time.perf_counter() - started) * 1000, 1), retries
        except Exception as error:
            if not _is_infrastructure_error(error) or retries >= 2:
                raise
            retries += 1
            await asyncio.sleep(min(5 * retries, 15))


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip())
    ]


def _set_counts(expected: set[str], actual: set[str]) -> dict[str, int | bool]:
    return {
        "exact": expected == actual,
        "true_positive": len(expected & actual),
        "false_positive": len(actual - expected),
        "false_negative": len(expected - actual),
    }


def _missing_turns(scenario: Any, completed: int) -> list[dict[str, Any]]:
    return [
        {
            "turn": item.turn,
            "gold_candidate_ids": list(item.gold_candidate_ids),
            "gold_state_diff": list(item.gold_state_diff),
            "is_correction_turn": (scenario.id, item.turn)
            in CONFIRMATORY_CORRECTION_TURNS,
        }
        for item in scenario.turns[completed:]
    ]


def _verify_frozen_inputs(manifest: dict[str, Any], dataset_path: Path) -> None:
    if _git("branch", "--show-current") != "Extended_Experiment":
        raise RuntimeError("confirmatory run requires Extended_Experiment")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked worktree must be clean before confirmatory execution")
    ancestor = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            manifest["selected_method_commit"],
            "HEAD",
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    if ancestor.returncode != 0:
        raise RuntimeError("execution commit does not descend from selected method commit")
    if _sha256_lf_normalized(dataset_path) != manifest["dataset"][
        "sha256_lf_normalized"
    ]:
        raise RuntimeError("confirmatory dataset differs from the frozen manifest")
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        actual = _sha256_lf_normalized(BACKEND_ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"frozen source differs: {relative}")
    if tuple(manifest["live_strategy_order"]) != LIVE_STRATEGIES:
        raise RuntimeError("live strategy order is not the frozen M0 then C order")


class ReplayUnderstandingProvider:
    """Return the exact M0 Understanding objects without another model call."""

    def __init__(
        self,
        outputs: Mapping[str, ExtendedTabletDomainUnderstandingOutput],
    ) -> None:
        self.outputs = dict(outputs)

    async def __call__(
        self,
        *,
        utterance: str,
        previous_state_summary: Mapping[str, Any],
        conversation_id: str,
        turn: int,
    ) -> ExtendedTabletDomainUnderstandingOutput:
        del previous_state_summary, conversation_id, turn
        output = self.outputs.get(utterance)
        if output is None:
            raise KeyError(f"no frozen M0 Understanding output for {utterance!r}")
        return output.model_copy(deep=True)


def _build_replay_service(
    *,
    outputs: Mapping[str, ExtendedTabletDomainUnderstandingOutput],
    catalog: ExperimentalAmazonCatalog,
) -> ActualDemoService:
    workflow = ActualCatalogWorkflow(
        catalog=catalog,
        understand=ReplayUnderstandingProvider(outputs),
        response_composer=ActualTemplateResponseComposer(),
        review_retriever=build_review_retriever(load_review_retrieval_settings()),
        state_updater=update_extended_dialogue_state,
    )
    return ActualDemoService(
        workflow,
        catalog,
        llm_provider="fixed-m0-upstream:c_semantic_noop",
        initial_state_factory=create_tablet_environment_state,
        experiment_condition="full",
    )


def _base_report(
    *,
    run_id: str,
    manifest: dict[str, Any],
    dataset_path: Path,
    catalog: ExperimentalAmazonCatalog,
) -> dict[str, Any]:
    settings = load_llm_settings()
    status = catalog.status()
    return {
        "schema_version": "tablet-domain-m0-c-confirmatory-run-v1",
        "study_status": "confirmatory_frozen_untouched_holdout",
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "version_control": {
            "branch": _git("branch", "--show-current"),
            "execution_commit": _git("rev-parse", "HEAD"),
            "tracked_worktree_clean": True,
            "selected_method_commit": manifest["selected_method_commit"],
        },
        "dataset": {
            "path": str(dataset_path),
            "sha256_lf_normalized": manifest["dataset"][
                "sha256_lf_normalized"
            ],
            "scenario_count": 20,
            "turn_count": 80,
            "reuse_classification": "new_untouched_confirmatory_holdout",
        },
        "protocol": {
            "live_strategy_order": list(LIVE_STRATEGIES),
            "run_each_live_strategy_once": True,
            "aggregate_only_after_both_live_strategies": True,
            "fixed_upstream_replay": "M0 Understanding outputs replayed through C State Manager",
            "understanding_llm_calls_per_attempted_live_turn": 1,
            "response_composer": "deterministic_template",
            "correction_turns": [
                f"{scenario}:{turn}"
                for scenario, turn in sorted(CONFIRMATORY_CORRECTION_TURNS)
            ],
            "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
            "bootstrap_seed": BOOTSTRAP_SEED,
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": settings.temperature,
            "prompt_version": "spn-understanding-tablet-domain-en-v2.3-frozen",
        },
        "catalog": {
            "path": str(catalog.catalog_dir),
            "schema_version": status.schema_version,
            "dataset_revision": status.dataset_revision,
            "product_count": status.product_count,
            "review_count": status.review_count,
        },
        "expected_maximum_live_logical_calls": 160,
        "conditions": {
            strategy: {
                "kind": "independent_live",
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "trace_filename": f"tablet_confirmatory_{run_id}_{strategy}.jsonl",
                "trace_sha256": None,
                "trace_summary": None,
                "scenarios": [],
                "metrics": None,
            }
            for strategy in LIVE_STRATEGIES
        }
        | {
            "c_fixed_upstream_replay": {
                "kind": "deterministic_replay",
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "source_condition": "m0_baseline",
                "additional_llm_calls": 0,
                "scenarios": [],
                "metrics": None,
            }
        },
        "paired_bootstrap": None,
        "decision": None,
        "limitations": [
            "Product relevance and natural-language response quality are outside this state experiment.",
            "Independent live arms can differ because GPT-4o-mini calls are not bitwise deterministic.",
            "The fixed-upstream replay is the causal State Manager comparison; independent live is operational replication.",
        ],
    }


async def _run_live_condition(
    *,
    strategy: str,
    scenarios: list[Any],
    catalog_dir: Path,
    report: dict[str, Any],
    output_path: Path,
    replay_outputs: dict[str, ExtendedTabletDomainUnderstandingOutput],
) -> None:
    payload = report["conditions"][strategy]
    payload["status"] = "running"
    payload["started_at"] = _utc_now()
    trace_path = load_llm_settings().log_dir / payload["trace_filename"]
    if trace_path.exists():
        raise RuntimeError(f"trace already exists: {trace_path}")
    service = build_extended_experiment_service(
        strategy,  # type: ignore[arg-type]
        trace_filename=payload["trace_filename"],
        catalog=ExperimentalAmazonCatalog(catalog_dir),
    )
    try:
        for scenario in scenarios:
            snapshot = await service.create_conversation()
            turns = []
            turn_metrics = []
            prior_active_ids = {"category_tablet"}
            gold_active_ids = {"category_tablet"}
            outer_retries = 0
            error: Exception | None = None
            error_turn: int | None = None
            for gold_turn in scenario.turns:
                try:
                    turn, wall_latency, turn_retries = await _run_turn_with_retries(
                        service, snapshot.conversation_id, gold_turn.utterance
                    )
                except Exception as exc:
                    error = exc
                    error_turn = gold_turn.turn
                    break
                outer_retries += turn_retries
                gold_active_ids.update(gold_turn.gold_candidate_ids)
                turn_metrics.append(
                    score_confirmatory_turn(
                        scenario_id=scenario.id,
                        gold=gold_turn,
                        turn=turn,
                        catalog=service.catalog,
                        wall_latency_ms=wall_latency,
                        gold_active_ids=gold_active_ids,
                        prior_active_ids=prior_active_ids,
                    )
                )
                prior_active_ids = active_state_ids(turn.dialogue_state)
                turns.append(turn)
                if strategy == "m0_baseline":
                    replay_outputs[gold_turn.utterance] = turn.understanding.model_copy(
                        deep=True,
                        update={"experimental_strategy": "c_semantic_noop"},
                    )

            actual_final = (
                active_state_ids(turns[-1].dialogue_state)
                if turns
                else {"category_tablet"}
            )
            expected_final = set(scenario.final_gold_state_ids)
            payload["scenarios"].append(
                {
                    "scenario_id": scenario.id,
                    "title": scenario.title,
                    "status": "error" if error else "completed",
                    "expected_turn_count": len(scenario.turns),
                    "completed_turn_count": len(turns),
                    "infrastructure_outer_retry_count": outer_retries,
                    "error_turn": error_turn,
                    "error_type": type(error).__name__ if error else None,
                    "error_classification": (
                        "infrastructure"
                        if error and _is_infrastructure_error(error)
                        else "semantic_or_schema"
                        if error
                        else None
                    ),
                    "error_message": _safe_error(error) if error else None,
                    "final_state_ids_expected": sorted(expected_final),
                    "final_state_ids_actual": sorted(actual_final),
                    "final_state_counts": _set_counts(expected_final, actual_final),
                    "missing_gold_turns": _missing_turns(scenario, len(turns)),
                    "turn_metrics": turn_metrics,
                    "turns": [turn.model_dump(mode="json") for turn in turns],
                }
            )
            write_report(output_path, report)
            print(
                f"{strategy}: {scenario.id} ({len(turns)}/{len(scenario.turns)} turns)",
                flush=True,
            )
    finally:
        await service.aclose()
    payload["status"] = "completed"
    payload["completed_at"] = _utc_now()
    write_report(output_path, report)


async def _run_fixed_upstream_replay(
    *,
    scenarios: list[Any],
    catalog_dir: Path,
    report: dict[str, Any],
    output_path: Path,
    replay_outputs: Mapping[str, ExtendedTabletDomainUnderstandingOutput],
) -> None:
    payload = report["conditions"]["c_fixed_upstream_replay"]
    payload["status"] = "running"
    payload["started_at"] = _utc_now()
    m0_records = {
        item["scenario_id"]: item
        for item in report["conditions"]["m0_baseline"]["scenarios"]
    }
    service = _build_replay_service(
        outputs=replay_outputs,
        catalog=ExperimentalAmazonCatalog(catalog_dir),
    )
    try:
        for scenario in scenarios:
            snapshot = await service.create_conversation()
            baseline_completed = m0_records[scenario.id]["completed_turn_count"]
            turns = []
            turn_metrics = []
            prior_active_ids = {"category_tablet"}
            gold_active_ids = {"category_tablet"}
            error: Exception | None = None
            error_turn: int | None = None
            for gold_turn in scenario.turns[:baseline_completed]:
                started = time.perf_counter()
                try:
                    turn = await service.run_turn(
                        snapshot.conversation_id, gold_turn.utterance
                    )
                except Exception as exc:
                    error = exc
                    error_turn = gold_turn.turn
                    break
                wall_latency = round((time.perf_counter() - started) * 1000, 1)
                gold_active_ids.update(gold_turn.gold_candidate_ids)
                turn_metrics.append(
                    score_confirmatory_turn(
                        scenario_id=scenario.id,
                        gold=gold_turn,
                        turn=turn,
                        catalog=service.catalog,
                        wall_latency_ms=wall_latency,
                        gold_active_ids=gold_active_ids,
                        prior_active_ids=prior_active_ids,
                    )
                )
                prior_active_ids = active_state_ids(turn.dialogue_state)
                turns.append(turn)

            actual_final = (
                active_state_ids(turns[-1].dialogue_state)
                if turns
                else {"category_tablet"}
            )
            expected_final = set(scenario.final_gold_state_ids)
            payload["scenarios"].append(
                {
                    "scenario_id": scenario.id,
                    "title": scenario.title,
                    "status": "error" if error else "completed_to_m0_boundary",
                    "expected_turn_count": len(scenario.turns),
                    "completed_turn_count": len(turns),
                    "source_m0_completed_turn_count": baseline_completed,
                    "error_turn": error_turn,
                    "error_type": type(error).__name__ if error else None,
                    "error_message": _safe_error(error) if error else None,
                    "final_state_ids_expected": sorted(expected_final),
                    "final_state_ids_actual": sorted(actual_final),
                    "final_state_counts": _set_counts(expected_final, actual_final),
                    "missing_gold_turns": _missing_turns(scenario, len(turns)),
                    "turn_metrics": turn_metrics,
                    "turns": [turn.model_dump(mode="json") for turn in turns],
                }
            )
            write_report(output_path, report)
            print(
                f"c_fixed_upstream_replay: {scenario.id} "
                f"({len(turns)}/{len(scenario.turns)} turns)",
                flush=True,
            )
    finally:
        await service.aclose()
    payload["status"] = "completed"
    payload["completed_at"] = _utc_now()
    write_report(output_path, report)


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset.resolve()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    _verify_frozen_inputs(manifest, dataset_path)
    settings = load_llm_settings()
    if settings.temperature != 0:
        raise RuntimeError("confirmatory comparison requires LLM_TEMPERATURE=0")
    output_path = args.output.resolve()
    summary_path = args.summary_output.resolve()
    if output_path.exists() or summary_path.exists():
        raise RuntimeError("confirmatory output exists; refusing to overwrite")

    catalog_dir = args.catalog_dir.resolve()
    os.environ.setdefault("REVIEW_RETRIEVAL_INDEX_DIR", str(catalog_dir))
    catalog = ExperimentalAmazonCatalog(catalog_dir)
    if not catalog.available:
        raise RuntimeError(catalog.status().unavailable_reason)
    dataset = load_tablet_holdout_dataset(dataset_path)
    scenarios = list(dataset.scenarios)
    if len(scenarios) != 20 or sum(len(item.turns) for item in scenarios) != 80:
        raise RuntimeError("confirmatory dataset must remain 20 scenarios and 80 turns")

    run_id = (
        f"m0c-confirmatory-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:8]}"
    )
    report = _base_report(
        run_id=run_id,
        manifest=manifest,
        dataset_path=dataset_path,
        catalog=catalog,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    write_report(output_path, report)

    replay_outputs: dict[str, ExtendedTabletDomainUnderstandingOutput] = {}
    for strategy in LIVE_STRATEGIES:
        await _run_live_condition(
            strategy=strategy,
            scenarios=scenarios,
            catalog_dir=catalog_dir,
            report=report,
            output_path=output_path,
            replay_outputs=replay_outputs,
        )

    # No metric is calculated before both independent live arms have completed.
    await _run_fixed_upstream_replay(
        scenarios=scenarios,
        catalog_dir=catalog_dir,
        report=report,
        output_path=output_path,
        replay_outputs=replay_outputs,
    )

    metrics: dict[str, dict[str, Any]] = {}
    for condition, payload in report["conditions"].items():
        payload["metrics"] = aggregate_confirmatory_metrics(payload["scenarios"])
        metrics[condition] = payload["metrics"]
        if payload["kind"] == "independent_live":
            trace_path = settings.log_dir / payload["trace_filename"]
            records = _read_trace(trace_path)
            payload["trace_sha256"] = _sha256(trace_path)
            payload["trace_summary"] = {
                **trace_summary(records),
                "input_tokens": sum(item.get("input_tokens") or 0 for item in records),
                "output_tokens": sum(item.get("output_tokens") or 0 for item in records),
                "http_attempt_count": sum(
                    len(item.get("attempts", [])) for item in records
                ),
            }

    fixed_bootstrap = paired_bootstrap_state_diff(
        report["conditions"]["m0_baseline"]["scenarios"],
        report["conditions"]["c_fixed_upstream_replay"]["scenarios"],
    )
    live_bootstrap = paired_bootstrap_state_diff(
        report["conditions"]["m0_baseline"]["scenarios"],
        report["conditions"]["c_semantic_noop"]["scenarios"],
    )
    report["paired_bootstrap"] = {
        "fixed_upstream_replay": fixed_bootstrap,
        "independent_live_replication": live_bootstrap,
    }
    report["decision"] = evaluate_confirmatory_decision(
        m0_metrics=metrics["m0_baseline"],
        replay_c_metrics=metrics["c_fixed_upstream_replay"],
        live_c_metrics=metrics["c_semantic_noop"],
        fixed_upstream_bootstrap=fixed_bootstrap,
        independent_live_bootstrap=live_bootstrap,
    )
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output_path, report)

    summary = {
        "schema_version": "tablet-domain-m0-c-confirmatory-summary-v1",
        "study_status": report["study_status"],
        "generated_at": _utc_now(),
        "run_id": run_id,
        "raw_report_sha256": _sha256(output_path),
        "dataset": report["dataset"],
        "version_control": report["version_control"],
        "protocol": report["protocol"],
        "catalog": report["catalog"],
        "condition_metrics": metrics,
        "live_trace_summaries": {
            strategy: report["conditions"][strategy]["trace_summary"]
            for strategy in LIVE_STRATEGIES
        },
        "live_trace_sha256": {
            strategy: report["conditions"][strategy]["trace_sha256"]
            for strategy in LIVE_STRATEGIES
        },
        "paired_bootstrap": report["paired_bootstrap"],
        "decision": report["decision"],
        "limitations": report["limitations"],
    }
    write_report(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"raw_report={output_path}", flush=True)
    print(f"summary_report={summary_path}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
