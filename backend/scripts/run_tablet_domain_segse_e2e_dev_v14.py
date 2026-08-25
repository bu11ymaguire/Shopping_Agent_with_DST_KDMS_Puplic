"""Run the SEGSE v1.4 treatment on the exposed multi-turn development fixture.

v1.4 changes deterministic interpretation only.  The gate, the frozen B0+C
reference summary, and the scoring code are the same objects v1.3 used, so the
two treatments differ by exactly the deterministic repair contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_llm_settings  # noqa: E402
from app.evaluation.tablet_domain_segse import active_final_tokens  # noqa: E402
from app.evaluation.tablet_domain_segse_e2e import (  # noqa: E402
    aggregate_e2e_arm,
    attach_catalog_hard_filter_checks,
    load_segse_e2e_dataset,
    score_e2e_turn,
    verify_gold_trajectory,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.segse_experiment_v14 import (  # noqa: E402
    SEGSE_V14_PROMPT_VERSION,
    build_segse_v14_experiment_service,
)
from scripts.run_tablet_domain_segse_e2e_dev import (  # noqa: E402
    _git,
    _run_turn_with_retries,
    _set_counts,
    _sha256,
    _sha256_lf,
)
from scripts.run_tablet_domain_segse_e2e_dev_v13 import (  # noqa: E402
    evaluate_v13_gate,
)

#: v1.4 is judged by the identical predeclared development gate as v1.3.
evaluate_v14_gate = evaluate_v13_gate

#: The frozen scorer dispatches SEGSE-shaped understanding on this arm key.
SCORER_ARM_KEY = "d4_segse_v12"

DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_segse_e2e_dev_v1.json"
DEFAULT_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_e2e_dev_v14.json"
)
DEFAULT_BASELINE = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v1.json"
)
DEFAULT_BASELINE_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v1_result.json"
)
DEFAULT_V13_SUMMARY = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v13.json"
)
DEFAULT_V13_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v13_result.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "tablet_domain_segse_e2e_dev_v14.json"
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v14.json"
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


def _verify_inputs(
    *,
    manifest: Mapping[str, Any],
    dataset_path: Path,
    baseline_path: Path,
    baseline_manifest: Mapping[str, Any],
    v13_path: Path,
    v13_manifest: Mapping[str, Any],
) -> None:
    if _git("branch", "--show-current") != "codex/segse-lite-experiment":
        raise RuntimeError("v1.4 development run requires the isolated SEGSE branch")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked worktree must be clean before the live run")
    ancestor = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            manifest["selected_method_parent_commit"],
            "HEAD",
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    if ancestor.returncode != 0:
        raise RuntimeError("execution commit does not descend from the v1.3 result")
    if _sha256_lf(dataset_path) != manifest["dataset"]["sha256_lf_normalized"]:
        raise RuntimeError("development fixture differs from the v1.4 manifest")
    if _sha256(baseline_path) != baseline_manifest["tracked_summary"]["sha256"]:
        raise RuntimeError("frozen B0 reference summary hash differs")
    if _sha256(v13_path) != v13_manifest["tracked_summary"]["sha256"]:
        raise RuntimeError("recorded v1.3 summary hash differs")
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        if _sha256_lf(BACKEND_ROOT / relative) != expected:
            raise RuntimeError(f"frozen v1.4 source differs: {relative}")


def _regression_view(
    v13: Mapping[str, Any], treatment: Mapping[str, Any]
) -> dict[str, Any]:
    """Report the v1.3 to v1.4 movement that the B0 gate does not express."""

    def delta(getter: Any) -> Any:
        before = getter(v13)
        after = getter(treatment)
        return {
            "v13": before,
            "v14": after,
            "delta": round(after - before, 6),
        }

    return {
        "candidate_fp_count": delta(lambda item: item["candidate_fp_count"]),
        "c2u_fp_count": delta(lambda item: item["c2u_fp_count"]),
        "novel_candidate_fp_count": delta(
            lambda item: item["novel_candidate_fp_count"]
        ),
        "raw_candidate_recall": delta(lambda item: item["raw_candidate"]["recall"]),
        "raw_candidate_precision": delta(
            lambda item: item["raw_candidate"]["precision"]
        ),
        "turnwise_final_fp": delta(
            lambda item: item["turnwise_accumulated_state"]["false_positive"]
        ),
        "scenario_final_fp": delta(
            lambda item: item["scenario_final_state"]["false_positive"]
        ),
        "turnwise_state_f1": delta(
            lambda item: item["turnwise_accumulated_state"]["f1"]
        ),
        "scenario_final_state_f1": delta(
            lambda item: item["scenario_final_state"]["f1"]
        ),
        "correction_recall": delta(lambda item: item["correction_recall"]["recall"]),
        "retract_recall": delta(lambda item: item["retract_recall"]["recall"]),
        "reactivation_recall": delta(
            lambda item: item["reactivation_recall"]["recall"]
        ),
        "material_operation_f1": delta(
            lambda item: item["material_operation"]["f1"]
        ),
        "turn_output_completion_rate": delta(
            lambda item: item["turn_output_completion_rate"]
        ),
        "hard_filter_completion_rate": delta(
            lambda item: item["hard_filter_completion_rate"]
        ),
        "policy_lane_accuracy": delta(lambda item: item["policy_lane_accuracy"]),
        "false_assertion_on_no_event_turn_count": delta(
            lambda item: item["false_assertion_on_no_event_turn_count"]
        ),
    }


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset.resolve()
    manifest_path = args.manifest.resolve()
    baseline_path = args.baseline.resolve()
    baseline_manifest_path = args.baseline_manifest.resolve()
    v13_path = args.v13_summary.resolve()
    v13_manifest_path = args.v13_manifest.resolve()
    catalog_dir = args.catalog_dir.resolve()
    output_path = args.output.resolve()
    summary_path = args.summary_output.resolve()
    if output_path.exists() or summary_path.exists():
        raise RuntimeError("v1.4 output exists; refusing to overwrite")
    dataset = load_segse_e2e_dataset(dataset_path)
    verify_gold_trajectory(dataset)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline_summary = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_manifest = json.loads(
        baseline_manifest_path.read_text(encoding="utf-8")
    )
    v13_summary = json.loads(v13_path.read_text(encoding="utf-8"))
    v13_manifest = json.loads(v13_manifest_path.read_text(encoding="utf-8"))
    _verify_inputs(
        manifest=manifest,
        dataset_path=dataset_path,
        baseline_path=baseline_path,
        baseline_manifest=baseline_manifest,
        v13_path=v13_path,
        v13_manifest=v13_manifest,
    )
    settings = load_llm_settings()
    if settings.temperature != 0:
        raise RuntimeError("SEGSE v1.4 development requires LLM_TEMPERATURE=0")
    catalog = ExperimentalAmazonCatalog(catalog_dir)
    status = catalog.status()
    if not catalog.available:
        raise RuntimeError(status.unavailable_reason or "catalog unavailable")

    run_id = (
        "segse-e2e-dev-v14-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
    )
    trace_filename = f"{run_id}_d4_segse_v14.jsonl"
    report: dict[str, Any] = {
        "schema_version": "tablet-domain-segse-e2e-dev-v14-run-v1",
        "study_status": "post_v13_exposed_fixture_development_not_confirmatory",
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "version_control": {
            "branch": _git("branch", "--show-current"),
            "execution_commit": _git("rev-parse", "HEAD"),
            "tracked_worktree_clean": True,
        },
        "dataset": {
            "path": str(dataset_path),
            "sha256_lf_normalized": _sha256_lf(dataset_path),
            "scenario_count": len(dataset.scenarios),
            "turn_count": sum(len(item.turns) for item in dataset.scenarios),
            "reuse_classification": (
                "exposed_development_fixture_reused_for_v14_iteration"
            ),
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": settings.temperature,
            "prompt_version": SEGSE_V14_PROMPT_VERSION,
            "prompt_text_identical_to_v13": True,
        },
        "catalog": {
            "path": str(catalog.catalog_dir),
            "schema_version": status.schema_version,
            "dataset_revision": status.dataset_revision,
            "product_count": status.product_count,
            "review_count": status.review_count,
        },
        "protocol": {
            "live_arm": "d4_segse_v14",
            "baseline_reuse": str(baseline_path),
            "v13_reference_reuse": str(v13_path),
            "understanding_calls_per_attempted_turn": 1,
            "continue_after_failed_turn": True,
            "missing_outputs_counted_as_failures": True,
            "response_composer": "deterministic_template",
            "changed_layer": "deterministic_event_interpretation_only",
        },
        "treatment": {
            "status": "running",
            "trace_filename": trace_filename,
            "trace_sha256": None,
            "scenarios": [],
            "metrics": None,
        },
        "decision": None,
        "regression_versus_v13": None,
        "limitations": [
            "The fixture was exposed during v1.3 and v1.4 design and is development-only.",
            "The B0 reference is reused from the v1 run and has 22/24 completion.",
            "The v1.3 reference is a single recorded live run, so provider variance "
            "between v1.3 and v1.4 is not separated from the deterministic change.",
            "Human relevance and response wording are outside this state experiment.",
        ],
    }
    write_report(output_path, report)
    service = build_segse_v14_experiment_service(
        trace_filename=trace_filename,
        catalog=ExperimentalAmazonCatalog(catalog_dir),
    )
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
            final_snapshot = await service.get_conversation(conversation_id)
            actual_final = active_final_tokens(final_snapshot.dialogue_state)
            expected_final = set(scenario.final_gold_active_tokens)
            report["treatment"]["scenarios"].append(
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
            write_report(output_path, report)
            print(
                f"d4_segse_v14: {scenario.id} "
                f"({sum(item['completed'] for item in metrics)}/{len(scenario.turns)} turns)",
                flush=True,
            )
    finally:
        await service.aclose()

    trace_path = settings.log_dir / trace_filename
    treatment_metrics = aggregate_e2e_arm(report["treatment"]["scenarios"])
    report["treatment"].update(
        {
            "status": "completed",
            "trace_sha256": _sha256(trace_path) if trace_path.exists() else None,
            "metrics": treatment_metrics,
        }
    )
    baseline_metrics = baseline_summary["arms"]["b0_c_semantic_noop"]["metrics"]
    v13_metrics = v13_summary["treatment"]["metrics"]
    report["decision"] = evaluate_v14_gate(baseline_metrics, treatment_metrics)
    report["regression_versus_v13"] = _regression_view(v13_metrics, treatment_metrics)
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output_path, report)
    summary = {
        "schema_version": "tablet-domain-segse-e2e-dev-v14-summary-v1",
        "study_status": report["study_status"],
        "run_id": report["run_id"],
        "version_control": report["version_control"],
        "dataset": report["dataset"],
        "runtime": report["runtime"],
        "catalog": report["catalog"],
        "baseline_reference": {
            "run_id": baseline_summary["run_id"],
            "summary_sha256": _sha256(baseline_path),
            "metrics": baseline_metrics,
        },
        "v13_reference": {
            "run_id": v13_summary["run_id"],
            "summary_sha256": _sha256(v13_path),
            "metrics": v13_metrics,
        },
        "treatment": {
            "status": report["treatment"]["status"],
            "trace_sha256": report["treatment"]["trace_sha256"],
            "metrics": treatment_metrics,
        },
        "decision": report["decision"],
        "regression_versus_v13": report["regression_versus_v13"],
        "limitations": report["limitations"],
    }
    write_report(summary_path, summary)
    print(json.dumps(summary["decision"], ensure_ascii=False, indent=2), flush=True)
    print(
        json.dumps(summary["regression_versus_v13"], ensure_ascii=False, indent=2),
        flush=True,
    )
    print(f"raw_report={output_path}", flush=True)
    print(f"summary_report={summary_path}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--baseline-manifest", type=Path, default=DEFAULT_BASELINE_MANIFEST
    )
    parser.add_argument("--v13-summary", type=Path, default=DEFAULT_V13_SUMMARY)
    parser.add_argument("--v13-manifest", type=Path, default=DEFAULT_V13_MANIFEST)
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
