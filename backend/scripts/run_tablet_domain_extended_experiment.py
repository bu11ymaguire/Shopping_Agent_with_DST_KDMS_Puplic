"""Run the post-hoc M0/A/B/C/D/E Full-Memory false-update comparison.

This is an Extended_Experiment artifact, not a rerun or replacement of the official
frozen batch.  It uses one Understanding LLM call per turn and deterministic response
composition so downstream state, policy, query, retrieval, and ranking stay shared.
Metrics are computed only after every requested strategy finishes.
"""

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
from typing import Any
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_llm_settings  # noqa: E402
from app.evaluation.tablet_domain_extended import (  # noqa: E402
    CORRECTION_TURNS,
    aggregate_extended_metrics,
    score_extended_turn,
    select_exploratory_strategy,
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
    EXTENDED_PROMPT_VERSIONS,
    EXTENDED_STRATEGY_ORDER,
    ExtendedStrategy,
    build_extended_experiment_service,
)
from app.llm import LLMHTTPError, LLMTimeoutError, write_report  # noqa: E402

DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEFAULT_HOLDOUT_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_multiturn_holdout_v1.json"
)
DEFAULT_OUTPUT = (
    BACKEND_ROOT / "reports" / "tablet_domain_extended_experiment_posthoc_v1.json"
)
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_extended_experiment_posthoc_v1.json"
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


def _parse_strategies(raw: str) -> list[ExtendedStrategy]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(values) - set(EXTENDED_STRATEGY_ORDER))
    if unknown:
        raise ValueError(f"unknown strategies: {unknown}")
    if len(values) != len(set(values)):
        raise ValueError("strategies must not repeat")
    return values  # type: ignore[return-value]


def _base_report(
    *,
    run_id: str,
    strategies: list[ExtendedStrategy],
    dataset_path: Path,
    dataset_hash: str,
    catalog: ExperimentalAmazonCatalog,
) -> dict[str, Any]:
    settings = load_llm_settings()
    status = catalog.status()
    return {
        "schema_version": "tablet-domain-extended-posthoc-run-v1",
        "study_status": "posthoc_exploratory_not_confirmatory",
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "version_control": {
            "branch": _git("branch", "--show-current"),
            "execution_commit": _git("rev-parse", "HEAD"),
            "tracked_worktree_clean": not bool(
                _git("status", "--porcelain", "--untracked-files=no")
            ),
            "official_base_commit": "92d70c21993a6780d1078e77af64750c3629410d",
        },
        "protocol": {
            "strategy_order": strategies,
            "run_each_strategy_once": True,
            "aggregate_only_after_all_strategies": True,
            "understanding_llm_calls_per_turn": 1,
            "response_composer": "deterministic_template",
            "shared_downstream": [
                "DialogueState contract",
                "Policy",
                "Query",
                "catalog filtering",
                "semantic review retrieval",
                "Cross-Encoder",
                "Rank",
            ],
            "correction_turns": [f"{scenario}:{turn}" for scenario, turn in sorted(CORRECTION_TURNS)],
        },
        "dataset": {
            "path": str(dataset_path),
            "sha256": dataset_hash,
            "reuse_classification": "previously_observed_official_holdout_posthoc",
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": settings.temperature,
            "prompt_versions": {
                strategy: EXTENDED_PROMPT_VERSIONS[strategy]
                for strategy in strategies
            },
        },
        "catalog": {
            "path": str(catalog.catalog_dir),
            "schema_version": status.schema_version,
            "dataset_revision": status.dataset_revision,
            "product_count": status.product_count,
            "review_count": status.review_count,
        },
        "expected_understanding_call_count": 81 * len(strategies),
        "strategies": {
            strategy: {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "trace_filename": f"tablet_extended_{run_id}_{strategy}.jsonl",
                "trace_sha256": None,
                "trace_summary": None,
                "scenarios": [],
                "metrics": None,
            }
            for strategy in strategies
        },
        "selection": None,
        "gold_state_oracle_comparison": None,
        "limitations": [
            "The 20-scenario dataset was observed before this strategy study.",
            "Any selected strategy is exploratory until a new untouched holdout is frozen and run once.",
            "Gold-State Oracle agreement is recommender fidelity, not human product relevance.",
            "Template responses isolate state behavior; natural-language response quality is not compared.",
        ],
    }


def _compare_with_gold_state_oracle(
    report: dict[str, Any], oracle_path: Path
) -> dict[str, Any]:
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    if oracle.get("schema_version") != "tablet-domain-gold-state-oracle-rankings-posthoc-v1":
        raise RuntimeError("unsupported Gold-State Oracle artifact")
    oracle_index = {
        item["scenario_id"]: [
            product["product_id"] for product in item.get("oracle_top3", [])
        ]
        for item in oracle["scenarios"]
    }
    comparisons: dict[str, Any] = {}
    for strategy, payload in report["strategies"].items():
        rows = []
        for scenario in payload["scenarios"]:
            expected = oracle_index.get(scenario["scenario_id"], [])
            turns = scenario.get("turns", [])
            actual = (
                [item["product_id"] for item in turns[-1].get("rankings", [])[:3]]
                if turns and scenario["completed_turn_count"] == scenario["expected_turn_count"]
                else []
            )
            comparable = bool(expected and actual)
            union = set(expected) | set(actual)
            rows.append(
                {
                    "scenario_id": scenario["scenario_id"],
                    "comparable": comparable,
                    "oracle_top3": expected,
                    "strategy_top3": actual,
                    "top1_same": comparable and expected[0] == actual[0],
                    "ordered_top3_exact": comparable and expected == actual,
                    "top3_set_exact": comparable and set(expected) == set(actual),
                    "overlap_count": (
                        len(set(expected) & set(actual)) if comparable else None
                    ),
                    "jaccard": (
                        len(set(expected) & set(actual)) / len(union)
                        if comparable and union
                        else None
                    ),
                }
            )
        comparable_rows = [item for item in rows if item["comparable"]]
        count = len(comparable_rows)
        comparisons[strategy] = {
            "comparable_scenario_count": count,
            "top1_agreement_rate": (
                sum(item["top1_same"] for item in comparable_rows) / count
                if count
                else None
            ),
            "ordered_top3_exact_rate": (
                sum(item["ordered_top3_exact"] for item in comparable_rows) / count
                if count
                else None
            ),
            "top3_set_exact_rate": (
                sum(item["top3_set_exact"] for item in comparable_rows) / count
                if count
                else None
            ),
            "mean_top3_overlap_count": (
                sum(item["overlap_count"] for item in comparable_rows) / count
                if count
                else None
            ),
            "mean_top3_jaccard": (
                sum(item["jaccard"] for item in comparable_rows) / count
                if count
                else None
            ),
            "scenarios": rows,
        }
    return {
        "analysis_label": "secondary_posthoc_gold_state_oracle_fidelity",
        "source": {"path": str(oracle_path), "sha256": _sha256(oracle_path)},
        "guardrail": (
            "Oracle-conditioned rankings are deterministic recommender outputs, "
            "not Gold products or human relevance labels."
        ),
        "strategies": comparisons,
    }


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    strategies = _parse_strategies(args.strategies)
    dataset_path = args.dataset.resolve()
    output = args.output.resolve()
    summary_output = args.summary_output.resolve()
    if output.exists() or summary_output.exists():
        raise RuntimeError("extended output exists; refusing to overwrite an experiment run")
    manifest = json.loads(args.holdout_manifest.read_text(encoding="utf-8"))
    dataset_hash = _sha256(dataset_path)
    if dataset_hash != manifest["dataset_sha256"]:
        raise RuntimeError("dataset differs from the frozen historical holdout")
    if _git("branch", "--show-current") != "Extended_Experiment":
        raise RuntimeError("extended comparison must run on Extended_Experiment")
    if load_llm_settings().temperature != 0:
        raise RuntimeError("extended comparison requires LLM_TEMPERATURE=0")

    catalog_dir = args.catalog_dir.resolve()
    os.environ.setdefault("REVIEW_RETRIEVAL_INDEX_DIR", str(catalog_dir))
    catalog = ExperimentalAmazonCatalog(catalog_dir)
    if not catalog.available:
        raise RuntimeError(catalog.status().unavailable_reason)
    dataset = load_tablet_holdout_dataset(dataset_path)
    scenarios = dataset.scenarios[: args.max_scenarios or None]
    expected_turns = sum(len(scenario.turns) for scenario in scenarios)
    run_id = f"extended-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    report = _base_report(
        run_id=run_id,
        strategies=strategies,
        dataset_path=dataset_path,
        dataset_hash=dataset_hash,
        catalog=catalog,
    )
    report["expected_understanding_call_count"] = expected_turns * len(strategies)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    write_report(output, report)

    for strategy in strategies:
        payload = report["strategies"][strategy]
        payload["status"] = "running"
        payload["started_at"] = _utc_now()
        trace_path = load_llm_settings().log_dir / payload["trace_filename"]
        if trace_path.exists():
            raise RuntimeError(f"trace already exists: {trace_path}")
        service = build_extended_experiment_service(
            strategy,
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
                retries = 0
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
                    retries += turn_retries
                    gold_active_ids.update(gold_turn.gold_candidate_ids)
                    turn_metrics.append(
                        score_extended_turn(
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
                        "status": "error" if error else "completed",
                        "expected_turn_count": len(scenario.turns),
                        "completed_turn_count": len(turns),
                        "infrastructure_outer_retry_count": retries,
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
                        "missing_gold_turns": [
                            {
                                "turn": item.turn,
                                "gold_candidate_ids": list(item.gold_candidate_ids),
                                "gold_state_diff": list(item.gold_state_diff),
                                "is_correction_turn": (scenario.id, item.turn)
                                in CORRECTION_TURNS,
                            }
                            for item in scenario.turns[len(turns) :]
                        ],
                        "turn_metrics": turn_metrics,
                        "turns": [turn.model_dump(mode="json") for turn in turns],
                    }
                )
                write_report(output, report)
                print(
                    f"{strategy}: {scenario.id} ({len(turns)}/{len(scenario.turns)} turns)",
                    flush=True,
                )
        finally:
            await service.aclose()
        payload["status"] = "completed"
        payload["completed_at"] = _utc_now()
        write_report(output, report)

    # Prespecified rule: no metric is calculated or printed until all arms finish.
    metrics: dict[str, dict[str, Any]] = {}
    for strategy in strategies:
        payload = report["strategies"][strategy]
        trace_path = load_llm_settings().log_dir / payload["trace_filename"]
        records = _read_trace(trace_path)
        payload["trace_sha256"] = _sha256(trace_path)
        payload["trace_summary"] = {
            **trace_summary(records),
            "input_tokens": sum(item.get("input_tokens") or 0 for item in records),
            "output_tokens": sum(item.get("output_tokens") or 0 for item in records),
        }
        payload["metrics"] = aggregate_extended_metrics(payload["scenarios"])
        metrics[strategy] = payload["metrics"]
    if "m0_baseline" in metrics:
        report["selection"] = select_exploratory_strategy(metrics)
    if args.oracle is not None:
        oracle_path = args.oracle.resolve()
        if not oracle_path.is_file():
            raise RuntimeError(f"Gold-State Oracle artifact not found: {oracle_path}")
        report["gold_state_oracle_comparison"] = _compare_with_gold_state_oracle(
            report, oracle_path
        )
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output, report)

    summary = {
        "schema_version": "tablet-domain-extended-posthoc-summary-v1",
        "study_status": report["study_status"],
        "generated_at": _utc_now(),
        "run_id": run_id,
        "raw_report_sha256": _sha256(output),
        "dataset": report["dataset"],
        "version_control": report["version_control"],
        "protocol": report["protocol"],
        "catalog": report["catalog"],
        "strategy_metrics": metrics,
        "strategy_trace_summaries": {
            strategy: report["strategies"][strategy]["trace_summary"]
            for strategy in strategies
        },
        "selection": report["selection"],
        "gold_state_oracle_comparison": report["gold_state_oracle_comparison"],
        "limitations": report["limitations"],
    }
    write_report(summary_output, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"raw_report={output}", flush=True)
    print(f"summary_report={summary_output}", flush=True)
    return summary


def _set_counts(expected: set[str], actual: set[str]) -> dict[str, int | bool]:
    return {
        "exact": expected == actual,
        "true_positive": len(expected & actual),
        "false_positive": len(actual - expected),
        "false_negative": len(expected - actual),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--holdout-manifest", type=Path, default=DEFAULT_HOLDOUT_MANIFEST
    )
    parser.add_argument("--catalog-dir", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument(
        "--oracle",
        type=Path,
        default=None,
        help="optional prior Gold-State Oracle compact JSON for secondary Top-3 fidelity",
    )
    parser.add_argument(
        "--strategies",
        default=",".join(EXTENDED_STRATEGY_ORDER),
        help="comma-separated strategy IDs in execution order",
    )
    parser.add_argument("--max-scenarios", type=int, default=0)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
