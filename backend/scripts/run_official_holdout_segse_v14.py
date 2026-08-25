"""Measure frozen SEGSE v1.4 on the official 20-scenario / 81-turn holdout.

This exists because the defect that motivated the whole SEGSE line was observed
here, not on the SEGSE development fixture:

    official Full   state_diff_micro F1 = 0.562
                    final_state_micro F1 = 0.758
    official No-memory state_diff_micro F1 = 0.700   <- higher than Full

Adding persistent memory made the per-turn State Diff worse. v1.3 and v1.4 were
developed and validated on a separate six-episode fixture, so that original
number was never re-measured. This run closes that gap.

Reuse classification: the holdout was already consumed by the official batch, so
this is NOT untouched confirmatory evidence. It is a measurement of an already
hash-frozen method on previously exposed data. The method cannot be tuned to the
result because `segse-v1.4-freeze` pins the prompt, schema, vocabulary, repair
contract, and every decision-affecting source file, and the fingerprint is
re-verified before and after the run.

Scoring reuses the unmodified official scorer and the unmodified automatic
aggregator, which are the exact functions that produced 0.562 and 0.758, so the
comparison shares one denominator: all 81 gold turns, with missing outputs
counted as failures.

Confound to keep in mind: the official Full arm used the v2.3 Understanding
prompt while this run uses the SEGSE prompt plus the SEGSE state manager. It is a
method-level A-versus-B comparison, not a single-variable ablation.
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

from app.config import load_llm_settings  # noqa: E402
from app.evaluation.tablet_domain_automatic import (  # noqa: E402
    aggregate_condition,
    load_trace_index,
    score_condition_scenario,
)
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_official import (  # noqa: E402
    active_state_ids,
    score_holdout_turn,
    trace_summary,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.llm import LLMHTTPError, LLMTimeoutError, write_report  # noqa: E402
from app.segse_experiment_v14 import (  # noqa: E402
    SEGSE_V14_PROMPT_VERSION,
    build_segse_v14_experiment_service,
)
from app.segse_v14_freeze import method_fingerprint  # noqa: E402

#: The SEGSE arm is inherently full-memory; the scorer keys on this label.
CONDITION = "full"

DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEFAULT_HOLDOUT_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_multiturn_holdout_v1.json"
)
DEFAULT_METHOD_FREEZE = (
    BACKEND_ROOT / "data" / "manifests" / "segse_v14_method_freeze.json"
)
DEFAULT_BASELINE = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_automatic_benchmark_v1.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "official_holdout_segse_v14.json"
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "data" / "results" / "official_holdout_segse_v14.json"
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
    """Hash line-ending normalized bytes.

    The frozen holdout manifest recorded an LF hash, and this Windows checkout has
    CRLF in the working tree, so the raw byte hash legitimately differs while the
    content is identical.
    """

    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).strip()


def _is_infrastructure_error(error: Exception) -> bool:
    if isinstance(error, LLMTimeoutError):
        return True
    return isinstance(error, LLMHTTPError) and (
        error.status_code == 0 or error.status_code == 429 or error.status_code >= 500
    )


def _verify_method_freeze(freeze_path: Path, *, phase: str) -> dict[str, Any]:
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    current = method_fingerprint()
    if frozen["method"]["combined_method_sha256"] != current["combined_method_sha256"]:
        raise RuntimeError(f"frozen v1.4 method changed ({phase})")
    for relative, expected in frozen["method"][
        "source_sha256_lf_normalized"
    ].items():
        if current["source_sha256_lf_normalized"].get(relative) != expected:
            raise RuntimeError(f"frozen v1.4 source changed ({phase}): {relative}")
    return current


async def _run_turn_with_retries(
    service: Any, conversation_id: str, utterance: str, *, retry_limit: int = 2
) -> tuple[Any, float, int]:
    retries = 0
    while True:
        started = time.perf_counter()
        try:
            turn = await service.run_turn(conversation_id, utterance)
            return turn, round((time.perf_counter() - started) * 1000, 1), retries
        except Exception as error:
            if not _is_infrastructure_error(error) or retries >= retry_limit:
                raise
            retries += 1
            await asyncio.sleep(min(5 * retries, 15))


def build_comparison(
    baseline: dict[str, Any],
    treatment: dict[str, Any],
    treatment_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compare the frozen v1.4 arm against the recorded official Full metrics.

    ``micro_scores`` reports only precision, recall, and F1, so raw TP/FP/FN come
    from the per-scenario records when they are available.
    """

    def pair(path: tuple[str, ...]) -> dict[str, Any]:
        before: Any = baseline
        after: Any = treatment
        for key in path:
            before = before[key] if isinstance(before, dict) else None
            after = after[key] if isinstance(after, dict) else None
        delta = None
        if isinstance(before, (int, float)) and isinstance(after, (int, float)):
            delta = round(after - before, 6)
        return {"official_full": before, "segse_v14": after, "delta": delta}

    counts: dict[str, Any] = {}
    if treatment_records is not None:
        for prefix in ("state_diff", "final_state", "canonical"):
            counts[prefix] = {
                key: sum(int(item[f"{prefix}_{key}"]) for item in treatment_records)
                for key in ("tp", "fp", "fn")
            }

    return {
        "primary_defect_metrics": {
            "state_diff_micro_f1": pair(("state_diff_micro", "f1")),
            "final_state_micro_f1": pair(("final_state_micro", "f1")),
        },
        "state_diff_detail": {
            "precision": pair(("state_diff_micro", "precision")),
            "recall": pair(("state_diff_micro", "recall")),
            "segse_v14_counts": counts.get("state_diff"),
        },
        "final_state_detail": {
            "precision": pair(("final_state_micro", "precision")),
            "recall": pair(("final_state_micro", "recall")),
            "segse_v14_counts": counts.get("final_state"),
        },
        "supporting": {
            "canonical_id_micro_f1": pair(("canonical_id_micro", "f1")),
            "policy_lane_accuracy": pair(("policy_lane_accuracy",)),
            "hard_filter_completion_rate": pair(("hard_filter_completion_rate",)),
            "recommendation_reach_rate": pair(("recommendation_reach_rate",)),
            "turn_completion_rate": pair(("turn_completion_rate",)),
            "scenario_completion_rate": pair(("scenario_completion_rate",)),
        },
    }


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = args.dataset.resolve()
    manifest_path = args.holdout_manifest.resolve()
    freeze_path = args.method_freeze.resolve()
    baseline_path = args.baseline.resolve()
    catalog_dir = args.catalog_dir.resolve()
    output = args.output.resolve()
    summary_output = args.summary_output.resolve()
    if output.exists() or summary_output.exists():
        raise RuntimeError("output exists; refusing to overwrite")

    holdout_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if holdout_manifest["status"] != "frozen_before_first_system_run":
        raise RuntimeError("holdout manifest is not in its frozen state")
    if _sha256_lf(dataset_path) != holdout_manifest["dataset_sha256"]:
        raise RuntimeError("official holdout dataset differs from its frozen manifest")
    fingerprint_before = _verify_method_freeze(freeze_path, phase="before")

    baseline_summary = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_metrics = baseline_summary["condition_metrics"][CONDITION]

    settings = load_llm_settings()
    if settings.temperature != 0:
        raise RuntimeError("this run requires LLM_TEMPERATURE=0")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked worktree must be clean before the run")

    dataset = load_tablet_holdout_dataset(dataset_path)
    catalog_probe = ExperimentalAmazonCatalog(catalog_dir)
    if not catalog_probe.available:
        raise RuntimeError(
            catalog_probe.status().unavailable_reason or "catalog unavailable"
        )

    run_id = (
        "official-segse-v14-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    )
    trace_filename = f"{run_id}_segse_v14_full.jsonl"
    report: dict[str, Any] = {
        "schema_version": "official-holdout-segse-v14-run-v1",
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "reuse_classification": {
            "label": "previously_exposed_holdout_measured_with_a_hash_frozen_method",
            "untouched_confirmatory_evidence": False,
            "method_frozen_before_run": True,
            "method_can_be_tuned_to_this_result": False,
            "why_this_run_exists": (
                "The defect that motivated SEGSE was observed on this holdout, but "
                "v1.3 and v1.4 were developed and validated on a separate "
                "six-episode fixture, so the original number was never re-measured."
            ),
            "confound": (
                "The official Full arm used the v2.3 Understanding prompt; this run "
                "uses the SEGSE prompt plus the SEGSE state manager. Method-level A "
                "versus B, not a single-variable ablation."
            ),
        },
        "version_control": {
            "execution_commit": _git("rev-parse", "HEAD"),
            "method_freeze_tag": "segse-v1.4-freeze",
            "tracked_worktree_clean": True,
        },
        "method_freeze": {
            "manifest_sha256": _sha256(freeze_path),
            "combined_method_sha256": fingerprint_before["combined_method_sha256"],
            "verified_before_run": True,
            "verified_after_run": None,
        },
        "frozen_inputs": {
            "dataset_path": dataset_path.relative_to(BACKEND_ROOT).as_posix(),
            "dataset_sha256_lf_normalized": _sha256_lf(dataset_path),
            "dataset_sha256_raw_bytes": _sha256(dataset_path),
            "dataset_matches_frozen_manifest": True,
            "scenario_count": len(dataset.scenarios),
            "turn_count": sum(len(item.turns) for item in dataset.scenarios),
            "baseline_path": baseline_path.relative_to(BACKEND_ROOT).as_posix(),
            "baseline_sha256": _sha256(baseline_path),
            "baseline_condition": CONDITION,
            "baseline_additional_llm_calls": 0,
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": settings.temperature,
            "prompt_version": SEGSE_V14_PROMPT_VERSION,
            "understanding_calls_per_attempted_turn": 1,
        },
        "protocol": {
            "scoring": "unmodified app/evaluation/tablet_domain_official.score_holdout_turn",
            "aggregation": (
                "unmodified app/evaluation/tablet_domain_automatic."
                "score_condition_scenario and aggregate_condition"
            ),
            "denominator": "all 81 gold turns; missing outputs count as failures",
            "stop_scenario_on_error": True,
            "run_once": True,
        },
        "catalog": None,
        "condition": {
            "name": CONDITION,
            "trace_filename": trace_filename,
            "trace_sha256": None,
            "trace_summary": None,
            "scenarios": [],
            "metrics": None,
        },
        "comparison_versus_official_full": None,
        "limitations": [
            "The holdout was already consumed by the official batch, so this is not untouched confirmatory evidence.",
            "The official Full arm used a different Understanding prompt, so the delta mixes prompt and state-manager effects.",
            "One run only; provider nondeterminism is not estimated here.",
        ],
    }
    write_report(output, report)

    service = build_segse_v14_experiment_service(
        trace_filename=trace_filename,
        catalog=ExperimentalAmazonCatalog(catalog_dir),
    )
    try:
        status = service.catalog.status()
        report["catalog"] = {
            "schema_version": status.schema_version,
            "dataset_revision": status.dataset_revision,
            "product_count": status.product_count,
            "review_count": status.review_count,
        }
        for scenario in dataset.scenarios:
            snapshot = await service.create_conversation()
            turns: list[Any] = []
            turn_metrics: list[dict[str, Any]] = []
            gold_active_ids = {"category_tablet"}
            retries_total = 0
            error: Exception | None = None
            error_turn: int | None = None
            for gold_turn in scenario.turns:
                try:
                    turn, wall_latency_ms, retries = await _run_turn_with_retries(
                        service, snapshot.conversation_id, gold_turn.utterance
                    )
                except Exception as exc:
                    error = exc
                    error_turn = gold_turn.turn
                    break
                retries_total += retries
                gold_active_ids.update(gold_turn.gold_candidate_ids)
                turns.append(turn)
                turn_metrics.append(
                    score_holdout_turn(
                        gold_turn,
                        turn,
                        service.catalog,
                        wall_latency_ms=wall_latency_ms,
                        gold_active_ids=gold_active_ids,
                        condition=CONDITION,
                    )
                )
            final_actual_ids = (
                active_state_ids(turns[-1].dialogue_state)
                if turns
                else {"category_tablet"}
            )
            final_expected_ids = set(scenario.final_gold_state_ids)
            report["condition"]["scenarios"].append(
                {
                    "scenario_id": scenario.id,
                    "title": scenario.title,
                    "status": "error" if error else "completed",
                    "expected_turn_count": len(scenario.turns),
                    "completed_turn_count": len(turns),
                    "infrastructure_outer_retry_count": retries_total,
                    "error_turn": error_turn,
                    "error_type": type(error).__name__ if error else None,
                    "error_classification": (
                        "infrastructure"
                        if error and _is_infrastructure_error(error)
                        else "semantic_or_schema"
                        if error
                        else None
                    ),
                    "error_message": str(error)[:500] if error else None,
                    "final_state_ids_expected": sorted(final_expected_ids),
                    "final_state_ids_actual": sorted(final_actual_ids),
                    "final_state_counts": {
                        "exact": final_expected_ids == final_actual_ids,
                        "true_positive": len(final_expected_ids & final_actual_ids),
                        "false_positive": len(final_actual_ids - final_expected_ids),
                        "false_negative": len(final_expected_ids - final_actual_ids),
                    },
                    "turn_metrics": turn_metrics,
                    "turns": [item.model_dump(mode="json") for item in turns],
                }
            )
            write_report(output, report)
            print(
                f"segse_v14/{CONDITION}: {scenario.id} "
                f"({len(turns)}/{len(scenario.turns)} turns)",
                flush=True,
            )
    finally:
        await service.aclose()

    trace_path = settings.log_dir / trace_filename
    trace_records = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report["condition"]["trace_sha256"] = _sha256(trace_path)
    report["condition"]["trace_summary"] = trace_summary(trace_records)

    trace_index, trace_provenance = load_trace_index(trace_path, dataset)
    raw_scenarios = {
        item["scenario_id"]: item for item in report["condition"]["scenarios"]
    }
    records = [
        score_condition_scenario(
            scenario, CONDITION, raw_scenarios[scenario.id], trace_index[scenario.id]
        )
        for scenario in dataset.scenarios
    ]
    treatment_metrics = aggregate_condition(records)
    report["condition"]["metrics"] = treatment_metrics
    report["condition"]["scenario_records"] = records
    report["condition"]["trace_provenance"] = trace_provenance
    report["comparison_versus_official_full"] = build_comparison(
        baseline_metrics, treatment_metrics, records
    )
    report["method_freeze"]["verified_after_run"] = True
    _verify_method_freeze(freeze_path, phase="after")
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output, report)

    summary = {
        "schema_version": "official-holdout-segse-v14-summary-v1",
        "run_id": run_id,
        "reuse_classification": report["reuse_classification"],
        "version_control": report["version_control"],
        "method_freeze": report["method_freeze"],
        "frozen_inputs": report["frozen_inputs"],
        "runtime": report["runtime"],
        "protocol": report["protocol"],
        "catalog": report["catalog"],
        "official_full_baseline_metrics": baseline_metrics,
        "segse_v14_metrics": treatment_metrics,
        "comparison_versus_official_full": report["comparison_versus_official_full"],
        "scenario_records": records,
        "trace_provenance": trace_provenance,
        "limitations": report["limitations"],
    }
    write_report(summary_output, summary)
    print(
        json.dumps(
            report["comparison_versus_official_full"], ensure_ascii=False, indent=2
        ),
        flush=True,
    )
    print(f"raw_report={output}", flush=True)
    print(f"summary_report={summary_output}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--holdout-manifest", type=Path, default=DEFAULT_HOLDOUT_MANIFEST
    )
    parser.add_argument("--method-freeze", type=Path, default=DEFAULT_METHOD_FREEZE)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
