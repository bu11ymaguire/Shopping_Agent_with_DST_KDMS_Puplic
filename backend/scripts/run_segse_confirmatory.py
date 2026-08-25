"""Execute the SEGSE confirmatory holdout: Run A then Run B, back to back.

Run A is the sole primary confirmatory evaluation. Run B is the pre-registered
test-retest analysis of provider nondeterminism and never replaces, averages with,
or retroactively redefines Run A. Both execute before either result is opened, so
the decision to run B cannot depend on seeing A.

The scorer and aggregator are the unmodified SEGSE end-to-end functions, and the
frozen v1.4 method fingerprint is verified before and after.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
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
from app.evaluation.tablet_domain_segse_confirmatory import (  # noqa: E402
    gold_trajectory_problems,
    load_segse_confirmatory_dataset,
)
from app.evaluation.tablet_domain_segse_e2e import (  # noqa: E402
    aggregate_e2e_arm,
    attach_catalog_hard_filter_checks,
    score_e2e_turn,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.segse_experiment_v14 import (  # noqa: E402
    SEGSE_V14_PROMPT_VERSION,
    build_segse_v14_experiment_service,
)
from app.segse_v14_freeze import method_fingerprint  # noqa: E402
from scripts.run_tablet_domain_segse_e2e_dev import (  # noqa: E402
    _run_turn_with_retries,
    _set_counts,
    _sha256,
)

#: The frozen scorer dispatches SEGSE-shaped understanding on this arm key.
SCORER_ARM_KEY = "d4_segse_v12"
RUNS = ("run_a_primary", "run_b_test_retest")

DEFAULT_HOLDOUT = (
    BACKEND_ROOT / "data" / "tablet_domain_segse_confirmatory_holdout_v1.json"
)
DEFAULT_HOLDOUT_FREEZE = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_holdout_v1_freeze.json"
)
DEFAULT_METHOD_FREEZE = (
    BACKEND_ROOT / "data" / "manifests" / "segse_v14_method_freeze.json"
)
DEFAULT_PROTOCOL = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_v1_protocol.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "segse_confirmatory_v1.json"
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


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).strip()


def _sha256_lf(path: Path) -> str:
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _verify_freezes(
    *, holdout_freeze: Path, method_freeze: Path, protocol: Path, phase: str
) -> dict[str, Any]:
    holdout = json.loads(holdout_freeze.read_text(encoding="utf-8"))
    method = json.loads(method_freeze.read_text(encoding="utf-8"))
    proto = json.loads(protocol.read_text(encoding="utf-8"))
    current = method_fingerprint()
    if method["method"]["combined_method_sha256"] != current["combined_method_sha256"]:
        raise RuntimeError(f"frozen v1.4 method changed ({phase})")
    for relative, expected in holdout["pinned_sha256_lf_normalized"].items():
        if _sha256_lf(BACKEND_ROOT / relative) != expected:
            raise RuntimeError(f"frozen holdout artifact changed ({phase}): {relative}")
    if not proto["run_roles"]["run_b"]["enabled"]:
        raise RuntimeError("the protocol has test-retest disabled")
    if proto["run_roles"]["run_b"]["decides_pass_or_fail"]:
        raise RuntimeError("Run B must not decide pass or fail")
    return {
        "holdout_combined_sha256": holdout["combined_holdout_sha256"],
        "method_combined_sha256": current["combined_method_sha256"],
        "protocol_revision": max(
            int(item["revision"]) for item in proto["amendment_history"]
        ),
    }


async def _execute_run(
    *,
    label: str,
    dataset: Any,
    catalog_dir: Path,
    trace_filename: str,
) -> list[dict[str, Any]]:
    service = build_segse_v14_experiment_service(
        trace_filename=trace_filename,
        catalog=ExperimentalAmazonCatalog(catalog_dir),
    )
    records: list[dict[str, Any]] = []
    try:
        for scenario in dataset.scenarios:
            snapshot = await service.create_conversation()
            conversation_id = snapshot.conversation_id
            gold_state = create_tablet_environment_state()
            prior_fp_tokens: set[str] = set()
            metrics: list[dict[str, Any]] = []
            turns: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            retries = 0
            for gold_turn in scenario.turns:
                before = await service.get_conversation(conversation_id)
                actual_before = before.dialogue_state
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
                    actual_after = actual_before.model_copy(deep=True)
                    errors.append(
                        {
                            "gold_turn": gold_turn.turn,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:500],
                        }
                    )
                scored, gold_state, prior_fp_tokens = score_e2e_turn(
                    arm=SCORER_ARM_KEY,
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
                scored["families"] = list(gold_turn.families)
                scored["dimension_confusion_family"] = (
                    gold_turn.dimension_confusion_family
                )
                attach_catalog_hard_filter_checks(scored, turn, service.catalog)
                metrics.append(scored)
                if turn is not None:
                    turns.append(
                        {
                            "gold_turn": gold_turn.turn,
                            "pipeline": turn.model_dump(mode="json"),
                            "understanding_details": turn.understanding.model_dump(
                                mode="json"
                            ),
                        }
                    )
            final = await service.get_conversation(conversation_id)
            actual_final = active_final_tokens(final.dialogue_state)
            expected_final = set(scenario.final_gold_active_tokens)
            records.append(
                {
                    "scenario_id": scenario.id,
                    "title": scenario.title,
                    "tags": list(scenario.tags),
                    "status": "completed" if not errors else "completed_with_errors",
                    "expected_turn_count": len(scenario.turns),
                    "completed_turn_count": sum(item["completed"] for item in metrics),
                    "infrastructure_outer_retry_count": retries,
                    "errors": errors,
                    "final_state_counts": _set_counts(expected_final, actual_final),
                    "turn_metrics": metrics,
                    "turns": turns,
                }
            )
            print(
                f"{label}: {scenario.id} "
                f"({sum(item['completed'] for item in metrics)}/{len(scenario.turns)} turns)",
                flush=True,
            )
    finally:
        await service.aclose()
    return records


async def execute(args: argparse.Namespace) -> None:
    holdout_path = args.holdout.resolve()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("confirmatory output exists; refusing to overwrite")
    dataset = load_segse_confirmatory_dataset(holdout_path)
    problems = gold_trajectory_problems(dataset)
    if problems:
        raise RuntimeError(f"gold is inconsistent; refusing to run: {problems[:3]}")
    before = _verify_freezes(
        holdout_freeze=args.holdout_freeze.resolve(),
        method_freeze=args.method_freeze.resolve(),
        protocol=args.protocol.resolve(),
        phase="before",
    )
    settings = load_llm_settings()
    if settings.temperature != 0:
        raise RuntimeError("the confirmatory run requires LLM_TEMPERATURE=0")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked worktree must be clean before the confirmatory run")
    catalog_dir = args.catalog_dir.resolve()
    probe = ExperimentalAmazonCatalog(catalog_dir)
    if not probe.available:
        raise RuntimeError(probe.status().unavailable_reason or "catalog unavailable")

    run_id = (
        "segse-confirmatory-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    )
    status = probe.status()
    report: dict[str, Any] = {
        "schema_version": "segse-confirmatory-v1-run-v1",
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "study_status": "untouched_confirmatory_holdout_first_system_run",
        "run_roles": {
            "run_a_primary": "sole primary confirmatory evaluation; decides pass or fail",
            "run_b_test_retest": "pre-registered provider-nondeterminism analysis only",
            "governing_statement": (
                "Run A is the sole primary confirmatory evaluation. Run B is a "
                "pre-registered test-retest analysis of provider-level "
                "nondeterminism and shall not replace, average with, or "
                "retroactively redefine Run A."
            ),
            "both_runs_execute_before_any_result_is_opened": True,
        },
        "version_control": {
            "execution_commit": _git("rev-parse", "HEAD"),
            "method_freeze_tag": "segse-v1.4-freeze",
            "protocol_freeze_tag": "segse-confirmatory-v1-protocol-freeze",
            "holdout_freeze_tag": "segse-confirmatory-holdout-v1-freeze",
            "tracked_worktree_clean": True,
        },
        "freezes_verified_before_run": before,
        "freezes_verified_after_run": None,
        "holdout": {
            "path": holdout_path.relative_to(BACKEND_ROOT).as_posix(),
            "sha256_lf_normalized": _sha256_lf(holdout_path),
            "scenario_count": len(dataset.scenarios),
            "turn_count": sum(len(item.turns) for item in dataset.scenarios),
            "split": dataset.split,
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": settings.temperature,
            "prompt_version": SEGSE_V14_PROMPT_VERSION,
            "understanding_calls_per_attempted_turn": 1,
        },
        "catalog": {
            "path": str(catalog_dir),
            "schema_version": status.schema_version,
            "dataset_revision": status.dataset_revision,
            "product_count": status.product_count,
            "review_count": status.review_count,
        },
        "runs": {
            name: {
                "status": "pending",
                "trace_filename": f"{run_id}_{name}.jsonl",
                "trace_sha256": None,
                "scenarios": [],
                "metrics": None,
            }
            for name in RUNS
        },
        "results_opened": False,
        "limitations": [
            "One primary run; Run B measures provider nondeterminism and is not a second evaluation.",
            "Run A and Run B are repeated measurements of the same 80 turns and are never pooled as independent samples.",
            "Product and review relevance are out of scope for this state experiment.",
        ],
    }
    write_report(output, report)

    # Both runs execute before any metric is computed or printed.
    for name in RUNS:
        payload = report["runs"][name]
        payload["status"] = "running"
        write_report(output, report)
        payload["scenarios"] = await _execute_run(
            label=name,
            dataset=dataset,
            catalog_dir=catalog_dir,
            trace_filename=payload["trace_filename"],
        )
        trace_path = settings.log_dir / payload["trace_filename"]
        payload["trace_sha256"] = (
            _sha256(trace_path) if trace_path.exists() else None
        )
        payload["status"] = "completed"
        write_report(output, report)

    for name in RUNS:
        payload = report["runs"][name]
        payload["metrics"] = aggregate_e2e_arm(payload["scenarios"])
    report["freezes_verified_after_run"] = _verify_freezes(
        holdout_freeze=args.holdout_freeze.resolve(),
        method_freeze=args.method_freeze.resolve(),
        protocol=args.protocol.resolve(),
        phase="after",
    )
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output, report)
    print(f"raw_report={output}", flush=True)
    print(
        "both runs complete; use scripts/evaluate_segse_confirmatory.py to open results",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--holdout-freeze", type=Path, default=DEFAULT_HOLDOUT_FREEZE)
    parser.add_argument("--method-freeze", type=Path, default=DEFAULT_METHOD_FREEZE)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
