"""Run the frozen SEGSE v1.2 multi-turn end-to-end development guardrail once."""

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

from app.config import load_llm_settings  # noqa: E402
from app.evaluation.tablet_domain_segse import active_final_tokens  # noqa: E402
from app.evaluation.tablet_domain_segse_e2e import (  # noqa: E402
    ARM_ORDER,
    aggregate_e2e_arm,
    attach_catalog_hard_filter_checks,
    evaluate_e2e_development_gate,
    load_segse_e2e_dataset,
    score_e2e_turn,
    verify_gold_trajectory,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.extended_experiment import build_extended_experiment_service  # noqa: E402
from app.llm import LLMHTTPError, LLMTimeoutError, write_report  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.segse_experiment_v12 import (  # noqa: E402
    SEGSE_V12_PROMPT_VERSION,
    build_segse_v12_experiment_service,
)


DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_segse_e2e_dev_v1.json"
DEFAULT_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v1.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "tablet_domain_segse_e2e_dev_v1.json"
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v1.json"
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).strip()


def _set_counts(gold: set[str], predicted: set[str]) -> dict[str, Any]:
    return {
        "gold": sorted(gold),
        "predicted": sorted(predicted),
        "true_positive": len(gold & predicted),
        "false_positive": len(predicted - gold),
        "false_negative": len(gold - predicted),
        "exact": gold == predicted,
    }


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


def _verify_frozen_inputs(manifest: dict[str, Any], dataset_path: Path) -> None:
    if _git("branch", "--show-current") != "codex/segse-lite-experiment":
        raise RuntimeError("SEGSE end-to-end development run requires its isolated branch")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked worktree must be clean before the live run")
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
        raise RuntimeError("execution commit does not descend from the selected method")
    if _sha256_lf(dataset_path) != manifest["dataset"]["sha256_lf_normalized"]:
        raise RuntimeError("development fixture differs from its frozen manifest")
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        if _sha256_lf(BACKEND_ROOT / relative) != expected:
            raise RuntimeError(f"frozen source differs: {relative}")
    if tuple(manifest["live_arm_order"]) != ARM_ORDER:
        raise RuntimeError("live arm order differs from the frozen protocol")


def _build_service(
    arm: str, *, trace_filename: str, catalog_dir: Path
) -> Any:
    catalog = ExperimentalAmazonCatalog(catalog_dir)
    if arm == "b0_c_semantic_noop":
        return build_extended_experiment_service(
            "c_semantic_noop",
            trace_filename=trace_filename,
            catalog=catalog,
        )
    return build_segse_v12_experiment_service(
        trace_filename=trace_filename,
        catalog=catalog,
    )


async def _run_arm(
    *,
    arm: str,
    dataset: Any,
    catalog_dir: Path,
    report: dict[str, Any],
    output_path: Path,
) -> None:
    arm_report = report["arms"][arm]
    arm_report["status"] = "running"
    arm_report["started_at"] = _utc_now()
    trace_path = load_llm_settings().log_dir / arm_report["trace_filename"]
    if trace_path.exists():
        raise RuntimeError(f"trace already exists: {trace_path}")
    service = _build_service(
        arm,
        trace_filename=arm_report["trace_filename"],
        catalog_dir=catalog_dir,
    )
    try:
        for scenario in dataset.scenarios:
            snapshot = await service.create_conversation()
            conversation_id = snapshot.conversation_id
            gold_state = create_tablet_environment_state()
            prior_fp_tokens: set[str] = set()
            metrics: list[dict[str, Any]] = []
            turns: list[dict[str, Any]] = []
            retries = 0
            failed = False
            error_type = None
            error_message = None

            for index, gold_turn in enumerate(scenario.turns):
                before_snapshot = await service.get_conversation(conversation_id)
                actual_before = before_snapshot.dialogue_state
                turn = None
                latency = None
                error: Exception | None = None
                try:
                    turn, latency, turn_retries = await _run_turn_with_retries(
                        service, conversation_id, gold_turn.utterance
                    )
                    retries += turn_retries
                    actual_after = turn.dialogue_state
                except Exception as exc:
                    error = exc
                    failed = True
                    error_type = type(exc).__name__
                    error_message = str(exc)[:500]
                    actual_after = actual_before.model_copy(deep=True)

                scored, gold_state, prior_fp_tokens = score_e2e_turn(
                    arm=arm,
                    scenario_id=scenario.id,
                    gold=gold_turn,
                    actual_before=actual_before,
                    actual_after=actual_after,
                    gold_before=gold_state,
                    turn=turn,
                    prior_final_fp_tokens=prior_fp_tokens,
                    wall_latency_ms=latency,
                    error=error,
                )
                attach_catalog_hard_filter_checks(scored, turn, service.catalog)
                metrics.append(scored)
                if turn is not None:
                    turns.append(
                        {
                            "pipeline": turn.model_dump(mode="json"),
                            "understanding_details": turn.understanding.model_dump(
                                mode="json"
                            ),
                        }
                    )
                if not failed:
                    continue

                # Missing later turns remain in every denominator and gold continues.
                frozen_actual = actual_after.model_copy(deep=True)
                for missing in scenario.turns[index + 1 :]:
                    scored, gold_state, prior_fp_tokens = score_e2e_turn(
                        arm=arm,
                        scenario_id=scenario.id,
                        gold=missing,
                        actual_before=frozen_actual,
                        actual_after=frozen_actual,
                        gold_before=gold_state,
                        turn=None,
                        prior_final_fp_tokens=prior_fp_tokens,
                        wall_latency_ms=None,
                    )
                    metrics.append(scored)
                break

            final_snapshot = await service.get_conversation(conversation_id)
            actual_final = active_final_tokens(final_snapshot.dialogue_state)
            expected_final = set(scenario.final_gold_active_tokens)
            arm_report["scenarios"].append(
                {
                    "scenario_id": scenario.id,
                    "title": scenario.title,
                    "tags": list(scenario.tags),
                    "status": "error" if failed else "completed",
                    "expected_turn_count": len(scenario.turns),
                    "completed_turn_count": sum(item["completed"] for item in metrics),
                    "infrastructure_outer_retry_count": retries,
                    "error_type": error_type,
                    "error_message": error_message,
                    "final_state_counts": _set_counts(expected_final, actual_final),
                    "turn_metrics": metrics,
                    "turns": turns,
                }
            )
            write_report(output_path, report)
            print(
                f"{arm}: {scenario.id} "
                f"({sum(item['completed'] for item in metrics)}/{len(scenario.turns)} turns)",
                flush=True,
            )
    finally:
        await service.aclose()
    arm_report["status"] = "completed"
    arm_report["completed_at"] = _utc_now()
    arm_report["metrics"] = aggregate_e2e_arm(arm_report["scenarios"])
    arm_report["trace_sha256"] = _sha256(trace_path) if trace_path.exists() else None
    write_report(output_path, report)


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset.resolve()
    manifest_path = args.manifest.resolve()
    catalog_dir = args.catalog_dir.resolve()
    output_path = args.output.resolve()
    summary_path = args.summary_output.resolve()
    if output_path.exists() or summary_path.exists():
        raise RuntimeError("development output exists; refusing to overwrite")
    dataset = load_segse_e2e_dataset(dataset_path)
    verify_gold_trajectory(dataset)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _verify_frozen_inputs(manifest, dataset_path)
    settings = load_llm_settings()
    if settings.temperature != 0:
        raise RuntimeError("SEGSE development comparison requires LLM_TEMPERATURE=0")
    catalog = ExperimentalAmazonCatalog(catalog_dir)
    status = catalog.status()
    if not catalog.available:
        raise RuntimeError(status.unavailable_reason or "catalog unavailable")

    run_id = f"segse-e2e-dev-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
    report: dict[str, Any] = {
        "schema_version": "tablet-domain-segse-e2e-dev-run-v1",
        "study_status": "frozen_development_guardrail_not_confirmatory",
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
            "sha256_lf_normalized": _sha256_lf(dataset_path),
            "scenario_count": len(dataset.scenarios),
            "turn_count": sum(len(item.turns) for item in dataset.scenarios),
            "reuse_classification": "new_frozen_development_fixture",
        },
        "protocol": {
            "live_arm_order": list(ARM_ORDER),
            "one_understanding_call_per_attempted_turn": True,
            "response_composer": "deterministic_template",
            "missing_outputs_counted_as_failures": True,
            "case_local_operation_matching": True,
            "fp_validation_layers": [
                "raw_assertion_candidate",
                "authorized_candidate",
                "material_operation",
                "turnwise_accumulated_state",
                "scenario_final_state",
            ],
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": settings.temperature,
            "baseline_prompt_version": "spn-understanding-tablet-domain-en-v2.3-frozen",
            "treatment_prompt_version": SEGSE_V12_PROMPT_VERSION,
        },
        "catalog": {
            "path": str(catalog.catalog_dir),
            "schema_version": status.schema_version,
            "dataset_revision": status.dataset_revision,
            "product_count": status.product_count,
            "review_count": status.review_count,
        },
        "expected_maximum_live_logical_calls": 48,
        "arms": {
            arm: {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "trace_filename": f"{run_id}_{arm}.jsonl",
                "trace_sha256": None,
                "scenarios": [],
                "metrics": None,
            }
            for arm in ARM_ORDER
        },
        "decision": None,
        "limitations": [
            "This is a small development guardrail, not untouched confirmatory evidence.",
            "Independent live arms can differ because provider outputs are not bitwise deterministic.",
            "Human relevance and natural-language response quality are outside this state experiment.",
        ],
    }
    write_report(output_path, report)
    for arm in ARM_ORDER:
        await _run_arm(
            arm=arm,
            dataset=dataset,
            catalog_dir=catalog_dir,
            report=report,
            output_path=output_path,
        )
    report["decision"] = evaluate_e2e_development_gate(
        report["arms"][ARM_ORDER[0]]["metrics"],
        report["arms"][ARM_ORDER[1]]["metrics"],
    )
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output_path, report)

    summary = {
        "schema_version": "tablet-domain-segse-e2e-dev-summary-v1",
        "study_status": report["study_status"],
        "run_id": report["run_id"],
        "version_control": report["version_control"],
        "dataset": report["dataset"],
        "runtime": report["runtime"],
        "catalog": report["catalog"],
        "arms": {
            arm: {
                "status": report["arms"][arm]["status"],
                "trace_sha256": report["arms"][arm]["trace_sha256"],
                "metrics": report["arms"][arm]["metrics"],
            }
            for arm in ARM_ORDER
        },
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
