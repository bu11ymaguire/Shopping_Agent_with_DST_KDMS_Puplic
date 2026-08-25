"""Run the SEGSE v1.3 treatment on the exposed multi-turn development fixture."""

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
from app.segse_experiment_v13 import (  # noqa: E402
    SEGSE_V13_PROMPT_VERSION,
    build_segse_v13_experiment_service,
)
from scripts.run_tablet_domain_segse_e2e_dev import (  # noqa: E402
    _git,
    _run_turn_with_retries,
    _set_counts,
    _sha256,
    _sha256_lf,
)


DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_segse_e2e_dev_v1.json"
DEFAULT_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v13.json"
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
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "tablet_domain_segse_e2e_dev_v13.json"
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v13.json"
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
) -> None:
    if _git("branch", "--show-current") != "codex/segse-lite-experiment":
        raise RuntimeError("v1.3 development run requires the isolated SEGSE branch")
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
        raise RuntimeError("execution commit does not descend from the v1.2 diagnosis")
    if _sha256_lf(dataset_path) != manifest["dataset"]["sha256_lf_normalized"]:
        raise RuntimeError("development fixture differs from the v1.3 manifest")
    if _sha256(baseline_path) != baseline_manifest["tracked_summary"]["sha256"]:
        raise RuntimeError("frozen B0 reference summary hash differs")
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        if _sha256_lf(BACKEND_ROOT / relative) != expected:
            raise RuntimeError(f"frozen v1.3 source differs: {relative}")


def _safe_div(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def evaluate_v13_gate(
    baseline: Mapping[str, Any], treatment: Mapping[str, Any]
) -> dict[str, Any]:
    candidate_fp_reduction = _safe_div(
        baseline["candidate_fp_count"] - treatment["candidate_fp_count"],
        baseline["candidate_fp_count"],
    )
    c2u_reduction = _safe_div(
        baseline["c2u_fp_count"] - treatment["c2u_fp_count"],
        baseline["c2u_fp_count"],
    )
    deltas = {
        "raw_candidate_recall": round(
            treatment["raw_candidate"]["recall"]
            - baseline["raw_candidate"]["recall"],
            6,
        ),
        "correction_recall": round(
            treatment["correction_recall"]["recall"]
            - baseline["correction_recall"]["recall"],
            6,
        ),
        "retract_recall": round(
            treatment["retract_recall"]["recall"]
            - baseline["retract_recall"]["recall"],
            6,
        ),
        "reactivation_recall": round(
            treatment["reactivation_recall"]["recall"]
            - baseline["reactivation_recall"]["recall"],
            6,
        ),
        "turnwise_state_f1": round(
            treatment["turnwise_accumulated_state"]["f1"]
            - baseline["turnwise_accumulated_state"]["f1"],
            6,
        ),
        "scenario_final_state_f1": round(
            treatment["scenario_final_state"]["f1"]
            - baseline["scenario_final_state"]["f1"],
            6,
        ),
        "hard_filter_completion": round(
            treatment["hard_filter_completion_rate"]
            - baseline["hard_filter_completion_rate"],
            6,
        ),
        "policy_lane_accuracy": round(
            treatment["policy_lane_accuracy"] - baseline["policy_lane_accuracy"],
            6,
        ),
        "recommendation_completion": round(
            treatment["recommendation_pipeline_completion_rate"]
            - baseline["recommendation_pipeline_completion_rate"],
            6,
        ),
    }
    checks = {
        "treatment_turn_output_completion_at_least_95_percent": treatment[
            "turn_output_completion_rate"
        ]
        >= 0.95,
        "candidate_fp_reduction_at_least_30_percent": candidate_fp_reduction
        >= 0.30,
        "c2u_fp_reduction_at_least_50_percent": c2u_reduction >= 0.50,
        "no_event_false_assertions_not_increased": treatment[
            "false_assertion_on_no_event_turn_count"
        ]
        <= baseline["false_assertion_on_no_event_turn_count"],
        "turnwise_final_fp_not_increased": treatment[
            "turnwise_accumulated_state"
        ]["false_positive"]
        <= baseline["turnwise_accumulated_state"]["false_positive"],
        "scenario_final_fp_not_increased": treatment["scenario_final_state"][
            "false_positive"
        ]
        <= baseline["scenario_final_state"]["false_positive"],
        "raw_candidate_recall_drop_at_most_2pp": deltas[
            "raw_candidate_recall"
        ]
        >= -0.02,
        "correction_recall_drop_at_most_2pp": deltas["correction_recall"] >= -0.02,
        "retract_recall_drop_at_most_2pp": deltas["retract_recall"] >= -0.02,
        "reactivation_recall_drop_at_most_2pp": deltas["reactivation_recall"] >= -0.02,
        "turnwise_state_f1_drop_at_most_001": deltas["turnwise_state_f1"] >= -0.01,
        "scenario_final_state_f1_drop_at_most_001": deltas[
            "scenario_final_state_f1"
        ]
        >= -0.01,
        "hard_filter_completion_drop_at_most_1pp": deltas[
            "hard_filter_completion"
        ]
        >= -0.01,
        "policy_lane_accuracy_drop_at_most_2pp": deltas["policy_lane_accuracy"]
        >= -0.02,
        "recommendation_completion_drop_at_most_2pp": deltas[
            "recommendation_completion"
        ]
        >= -0.02,
    }
    return {
        "status": (
            "development_gate_passed_confirmatory_method_freeze_allowed"
            if all(checks.values())
            else "development_gate_failed"
        ),
        "baseline_reuse": "frozen v1 B0+C live output; no new baseline calls",
        "candidate_fp_reduction_fraction": round(candidate_fp_reduction, 6),
        "c2u_fp_reduction_fraction": round(c2u_reduction, 6),
        "metric_deltas": deltas,
        "checks": checks,
        "claim_boundary": (
            "v1.3 was developed after exposure to this fixture. Passing only permits "
            "a later method freeze and a new untouched confirmatory holdout."
        ),
    }


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset.resolve()
    manifest_path = args.manifest.resolve()
    baseline_path = args.baseline.resolve()
    baseline_manifest_path = args.baseline_manifest.resolve()
    catalog_dir = args.catalog_dir.resolve()
    output_path = args.output.resolve()
    summary_path = args.summary_output.resolve()
    if output_path.exists() or summary_path.exists():
        raise RuntimeError("v1.3 output exists; refusing to overwrite")
    dataset = load_segse_e2e_dataset(dataset_path)
    verify_gold_trajectory(dataset)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    baseline_summary = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_manifest = json.loads(
        baseline_manifest_path.read_text(encoding="utf-8")
    )
    _verify_inputs(
        manifest=manifest,
        dataset_path=dataset_path,
        baseline_path=baseline_path,
        baseline_manifest=baseline_manifest,
    )
    settings = load_llm_settings()
    if settings.temperature != 0:
        raise RuntimeError("SEGSE v1.3 development requires LLM_TEMPERATURE=0")
    catalog = ExperimentalAmazonCatalog(catalog_dir)
    status = catalog.status()
    if not catalog.available:
        raise RuntimeError(status.unavailable_reason or "catalog unavailable")

    run_id = f"segse-e2e-dev-v13-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:6]}"
    trace_filename = f"{run_id}_d4_segse_v13.jsonl"
    report: dict[str, Any] = {
        "schema_version": "tablet-domain-segse-e2e-dev-v13-run-v1",
        "study_status": "post_v12_exposed_fixture_development_not_confirmatory",
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
            "reuse_classification": "exposed_development_fixture_reused_for_v13_iteration",
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": settings.temperature,
            "prompt_version": SEGSE_V13_PROMPT_VERSION,
        },
        "catalog": {
            "path": str(catalog.catalog_dir),
            "schema_version": status.schema_version,
            "dataset_revision": status.dataset_revision,
            "product_count": status.product_count,
            "review_count": status.review_count,
        },
        "protocol": {
            "live_arm": "d4_segse_v13",
            "baseline_reuse": str(baseline_path),
            "understanding_calls_per_attempted_turn": 1,
            "continue_after_failed_turn": True,
            "missing_outputs_counted_as_failures": True,
            "response_composer": "deterministic_template",
        },
        "treatment": {
            "status": "running",
            "trace_filename": trace_filename,
            "trace_sha256": None,
            "scenarios": [],
            "metrics": None,
        },
        "decision": None,
        "limitations": [
            "The fixture was exposed during v1.3 design and is development-only.",
            "The B0 reference is reused from the v1 run and has 22/24 completion.",
            "Human relevance and response wording are outside this state experiment.",
        ],
    }
    write_report(output_path, report)
    service = build_segse_v13_experiment_service(
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
                    arm="d4_segse_v12",
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
                f"d4_segse_v13: {scenario.id} "
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
    report["decision"] = evaluate_v13_gate(baseline_metrics, treatment_metrics)
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output_path, report)
    summary = {
        "schema_version": "tablet-domain-segse-e2e-dev-v13-summary-v1",
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
        "treatment": {
            "status": report["treatment"]["status"],
            "trace_sha256": report["treatment"]["trace_sha256"],
            "metrics": treatment_metrics,
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
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--baseline-manifest", type=Path, default=DEFAULT_BASELINE_MANIFEST
    )
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
