"""Finish the official-holdout SEGSE v1.4 measurement from its recorded raw run.

The live 81-turn run completed and checkpointed every scenario; only the
aggregation step failed on a metric key name. Aggregation is fully deterministic
and makes zero LLM calls, so this script completes the measurement from the
recorded artifact instead of repeating the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_automatic import (  # noqa: E402
    aggregate_condition,
    load_trace_index,
    score_condition_scenario,
)
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_official import trace_summary  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.segse_v14_freeze import method_fingerprint  # noqa: E402
from scripts.run_official_holdout_segse_v14 import (  # noqa: E402
    CONDITION,
    build_comparison,
)

DEFAULT_RAW = BACKEND_ROOT / "reports" / "official_holdout_segse_v14.json"
DEFAULT_GOLD = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEFAULT_BASELINE = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_automatic_benchmark_v1.json"
)
DEFAULT_METHOD_FREEZE = (
    BACKEND_ROOT / "data" / "manifests" / "segse_v14_method_freeze.json"
)
DEFAULT_SUMMARY = (
    BACKEND_ROOT / "data" / "results" / "official_holdout_segse_v14.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_method_freeze(freeze_path: Path) -> dict[str, Any]:
    frozen = json.loads(freeze_path.read_text(encoding="utf-8"))
    current = method_fingerprint()
    if frozen["method"]["combined_method_sha256"] != current["combined_method_sha256"]:
        raise RuntimeError("frozen v1.4 method changed since the run")
    return current


def _per_scenario_final_state(
    gold: Any, raw_scenarios: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = []
    for scenario in gold.scenarios:
        record = raw_scenarios[scenario.id]
        expected = set(record["final_state_ids_expected"])
        actual = set(record["final_state_ids_actual"])
        rows.append(
            {
                "scenario_id": scenario.id,
                "title": scenario.title,
                "completed_turns": record["completed_turn_count"],
                "expected_turns": record["expected_turn_count"],
                "exact": expected == actual,
                "true_positive": len(expected & actual),
                "false_positive": len(actual - expected),
                "false_negative": len(expected - actual),
                "error_tokens": len(actual - expected) + len(expected - actual),
                "missing": sorted(expected - actual),
                "spurious": sorted(actual - expected),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--method-freeze", type=Path, default=DEFAULT_METHOD_FREEZE)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    raw_path = args.raw.resolve()
    summary_output = args.summary_output.resolve()
    if summary_output.exists():
        raise RuntimeError("summary already exists; refusing to overwrite")

    report = json.loads(raw_path.read_text(encoding="utf-8"))
    fingerprint = _verify_method_freeze(args.method_freeze.resolve())
    gold = load_tablet_holdout_dataset(args.gold.resolve())
    baseline = json.loads(args.baseline.resolve().read_text(encoding="utf-8"))
    baseline_metrics = baseline["condition_metrics"][CONDITION]

    scenarios = report["condition"]["scenarios"]
    if len(scenarios) != len(gold.scenarios):
        raise RuntimeError("recorded run does not cover every gold scenario")
    raw_scenarios = {item["scenario_id"]: item for item in scenarios}

    trace_path = BACKEND_ROOT / "logs" / report["condition"]["trace_filename"]
    trace_records = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    trace_index, trace_provenance = load_trace_index(trace_path, gold)

    records = [
        score_condition_scenario(
            scenario, CONDITION, raw_scenarios[scenario.id], trace_index[scenario.id]
        )
        for scenario in gold.scenarios
    ]
    treatment_metrics = aggregate_condition(records)
    comparison = build_comparison(baseline_metrics, treatment_metrics, records)

    report["condition"]["trace_sha256"] = _sha256(trace_path)
    report["condition"]["trace_summary"] = trace_summary(trace_records)
    report["condition"]["metrics"] = treatment_metrics
    report["condition"]["scenario_records"] = records
    report["condition"]["trace_provenance"] = trace_provenance
    report["comparison_versus_official_full"] = comparison
    report["method_freeze"]["verified_after_run"] = True
    report["method_freeze"]["combined_method_sha256_after_run"] = fingerprint[
        "combined_method_sha256"
    ]
    report["aggregation_note"] = (
        "The live run completed all 81 turns and checkpointed every scenario. The "
        "first aggregation attempt failed on a metric key name, so aggregation was "
        "completed offline from the recorded artifact with zero additional LLM calls. "
        "No turn was re-executed."
    )
    report["status"] = "completed"
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_report(raw_path, report)

    summary = {
        "schema_version": "official-holdout-segse-v14-summary-v1",
        "run_id": report["run_id"],
        "reuse_classification": report["reuse_classification"],
        "version_control": report["version_control"],
        "method_freeze": report["method_freeze"],
        "frozen_inputs": report["frozen_inputs"],
        "runtime": report["runtime"],
        "protocol": report["protocol"],
        "aggregation_note": report["aggregation_note"],
        "catalog": report["catalog"],
        "official_full_baseline_metrics": baseline_metrics,
        "segse_v14_metrics": treatment_metrics,
        "comparison_versus_official_full": comparison,
        "per_scenario_final_state": _per_scenario_final_state(gold, raw_scenarios),
        "scenario_records": records,
        "trace_provenance": trace_provenance,
        "limitations": report["limitations"],
    }
    write_report(summary_output, summary)
    print(json.dumps(comparison, ensure_ascii=False, indent=2), flush=True)
    print(f"summary_report={summary_output}", flush=True)


if __name__ == "__main__":
    main()
