"""Verify the frozen posthoc M0/A/B/C/D/E result and local audit artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_extended_experiment_result_v1.json"
)
PROTOCOL_PATH = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_extended_experiment_protocol_v1.json"
)


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_lf_normalized(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12)


def metric(metrics: dict[str, Any], strategy: str, *path: str) -> Any:
    value: Any = metrics[strategy]
    for part in path:
        value = value[part]
    return value


def verify_tracked_artifacts(
    manifest: dict[str, Any], summary: dict[str, Any]
) -> None:
    for label in ("compact_result", "analysis_report"):
        artifact = manifest["artifacts"][label]
        path = BACKEND_ROOT / artifact["path"]
        check(f"{label} exists", path.is_file(), path)
        check(
            f"{label} LF-normalized hash",
            sha256_lf_normalized(path) == artifact["sha256_lf_normalized"],
            artifact["sha256_lf_normalized"],
        )

    raw_artifact = manifest["artifacts"]["raw_report"]
    check(
        "summary points to frozen raw hash",
        summary["raw_report_sha256"] == raw_artifact["sha256"],
        summary["raw_report_sha256"],
    )
    raw_path = BACKEND_ROOT / raw_artifact["path"]
    if raw_path.exists():
        check("local raw hash", sha256_file(raw_path) == raw_artifact["sha256"])
        check(
            "local raw size",
            raw_path.stat().st_size == raw_artifact["size_bytes"],
            raw_path.stat().st_size,
        )
    else:
        print("[SKIP] local raw report is Git-ignored; manifest hash remains verifiable")


def verify_protocol(
    manifest: dict[str, Any], protocol: dict[str, Any], summary: dict[str, Any]
) -> list[str]:
    order = manifest["strategy_order"]
    check("strategy order matches protocol", order == protocol["strategy_order"])
    check("strategy order matches summary", order == summary["protocol"]["strategy_order"])
    check(
        "dataset hash matches protocol",
        manifest["dataset"]["sha256"]
        == protocol["dataset"]["sha256"]
        == summary["dataset"]["sha256"],
    )
    check(
        "dataset dimensions",
        manifest["dataset"]["scenario_count"] == 20
        and manifest["dataset"]["turn_count"] == 81,
    )
    check(
        "posthoc study boundary",
        summary["study_status"] == "posthoc_exploratory_not_confirmatory"
        and manifest["status"] == "posthoc_exploratory_result_frozen",
    )
    check(
        "execution identity",
        manifest["run_id"] == summary["run_id"]
        and manifest["execution_commit"]
        == summary["version_control"]["execution_commit"],
    )
    check(
        "single Understanding logical call contract",
        summary["protocol"]["understanding_llm_calls_per_turn"] == 1,
    )
    check(
        "deterministic response composer",
        summary["protocol"]["response_composer"] == "deterministic_template",
    )
    return order


def verify_metrics(
    manifest: dict[str, Any], summary: dict[str, Any], order: list[str]
) -> None:
    metrics = summary["strategy_metrics"]
    traces = summary["strategy_trace_summaries"]
    check("all strategy metrics present", list(metrics) == order)
    check("all strategy trace summaries present", list(traces) == order)

    for strategy in order:
        check(
            f"{strategy} common 81-turn denominator",
            metrics[strategy]["expected_turn_count"] == 81,
        )
        check(
            f"{strategy} missing-turn accounting",
            metrics[strategy]["completed_turn_count"]
            + metrics[strategy]["extended_diagnostics"][
                "missing_turns_counted_as_failures"
            ]
            == 81,
        )

    frozen = manifest["key_metrics"]
    mappings = {
        "turn_output_completion": ("turn_output_completion_rate",),
        "candidate_f1": ("canonical_id_micro", "f1"),
        "candidate_false_positives": (
            "extended_diagnostics",
            "candidate_false_positive_count",
        ),
        "carryover_to_update_false_positives": (
            "extended_diagnostics",
            "carryover_to_update_false_positive_count",
        ),
        "state_diff_f1": ("state_diff_micro", "f1"),
        "material_state_diff_false_positives": (
            "extended_diagnostics",
            "material_state_diff_false_positive_count",
        ),
        "final_state_f1": ("final_state_micro", "f1"),
        "correction_recall": (
            "extended_diagnostics",
            "correction_candidate_recall",
        ),
        "hard_filter_completion": ("hard_constraint_completion_rate",),
    }
    for strategy, expected_values in frozen.items():
        for label, expected in expected_values.items():
            actual = metric(metrics, strategy, *mappings[label])
            valid = close(actual, expected) if isinstance(expected, float) else actual == expected
            check(f"{strategy} frozen {label}", valid, actual)

    m0 = metrics["m0_baseline"]
    c = metrics["c_semantic_noop"]
    check(
        "C reduces material State Diff FP",
        c["extended_diagnostics"]["material_state_diff_false_positive_count"]
        < m0["extended_diagnostics"]["material_state_diff_false_positive_count"],
    )
    check(
        "C does not claim candidate FP reduction",
        c["extended_diagnostics"]["candidate_false_positive_count"]
        >= m0["extended_diagnostics"]["candidate_false_positive_count"],
    )


def verify_selection(manifest: dict[str, Any], summary: dict[str, Any]) -> None:
    selection = summary["selection"]
    metrics = summary["strategy_metrics"]
    baseline = metrics[selection["baseline"]]
    margins = selection["thresholds"]
    checks: dict[str, dict[str, bool]] = {}
    for strategy, values in metrics.items():
        checks[strategy] = {
            "final_state_f1": values["final_state_micro"]["f1"]
            >= baseline["final_state_micro"]["f1"]
            - margins["final_state_f1_non_inferiority_margin"],
            "correction_recall": values["extended_diagnostics"][
                "correction_candidate_recall"
            ]
            >= baseline["extended_diagnostics"]["correction_candidate_recall"]
            - margins["correction_recall_non_inferiority_margin"],
            "hard_filter_completion": values["hard_constraint_completion_rate"]
            >= baseline["hard_constraint_completion_rate"]
            - margins["hard_filter_completion_non_inferiority_margin"],
            "turn_output_completion": values["turn_output_completion_rate"]
            >= baseline["turn_output_completion_rate"]
            - margins["turn_output_completion_non_inferiority_margin"],
        }
    for strategy, expected_gate in selection["gates"].items():
        check(
            f"{strategy} recomputed retention checks",
            checks[strategy] == expected_gate["checks"],
            checks[strategy],
        )
        check(
            f"{strategy} recomputed gate",
            all(checks[strategy].values()) == expected_gate["passed"],
        )

    eligible = [strategy for strategy, gate in selection["gates"].items() if gate["passed"]]
    eligible.sort(
        key=lambda strategy: (
            -metrics[strategy]["state_diff_micro"]["f1"],
            metrics[strategy]["extended_diagnostics"][
                "carryover_to_update_false_positive_count"
            ],
            metrics[strategy]["extended_diagnostics"][
                "understanding_latency_ms_median"
            ],
            strategy,
        )
    )
    check(
        "eligible strategy rank reproduced",
        eligible == selection["eligible_strategies_in_rank_order"],
        eligible,
    )
    check(
        "exploratory selection reproduced",
        eligible[0] == selection["exploratory_selected_strategy"]
        == manifest["selection"]["exploratory_selected_strategy"]
        == "c_semantic_noop",
    )
    check(
        "E failed only correction retention",
        checks["e_compact_context"]
        == {
            "final_state_f1": True,
            "correction_recall": False,
            "hard_filter_completion": True,
            "turn_output_completion": True,
        },
    )


def verify_llm_audit(
    manifest: dict[str, Any], summary: dict[str, Any], order: list[str]
) -> None:
    trace_summaries = summary["strategy_trace_summaries"]
    audit = manifest["llm_audit"]
    totals = {
        "logical": 0,
        "repairs": 0,
        "transport": 0,
        "fallback": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    for strategy in order:
        trace = trace_summaries[strategy]
        totals["logical"] += trace["understanding_call_count"]
        totals["repairs"] += trace["schema_repair_count"]
        totals["transport"] += trace["transport_retry_count"]
        totals["fallback"] += trace["fallback_count"]
        totals["input_tokens"] += trace["input_tokens"]
        totals["output_tokens"] += trace["output_tokens"]
        check(
            f"{strategy} reported model",
            trace["reported_models"] == {audit["reported_model"]: trace["record_count"]},
        )
        check(
            f"{strategy} structured mode",
            trace["structured_modes"] == {audit["structured_mode"]: trace["record_count"]},
        )
        check(
            f"{strategy} logical calls match trace manifest",
            trace["understanding_call_count"]
            == manifest["traces"][strategy]["logical_calls"],
        )

    check("logical call total", totals["logical"] == audit["logical_understanding_calls"])
    check("schema repair total", totals["repairs"] == audit["schema_repair_attempts"])
    check(
        "HTTP attempt identity",
        totals["logical"] + totals["repairs"]
        == audit["structured_generation_http_attempts"],
    )
    check("no transport retry", totals["transport"] == audit["transport_retries"] == 0)
    check("no fallback", totals["fallback"] == audit["fallbacks"] == 0)
    check(
        "trace-reported input token total",
        totals["input_tokens"] == audit["trace_reported_input_tokens"],
    )
    check(
        "trace-reported output token total",
        totals["output_tokens"] == audit["trace_reported_output_tokens"],
    )


def verify_local_traces(manifest: dict[str, Any], order: list[str]) -> None:
    logs_dir = BACKEND_ROOT / "logs"
    for strategy in order:
        expected = manifest["traces"][strategy]
        path = logs_dir / expected["file"]
        if not path.exists():
            print(f"[SKIP] {strategy} local trace is Git-ignored")
            continue
        check(f"{strategy} local trace hash", sha256_file(path) == expected["sha256"])
        check(f"{strategy} local trace size", path.stat().st_size == expected["size_bytes"])
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        attempts = sum(len(record.get("attempts", [])) for record in records)
        check(f"{strategy} local trace records", len(records) == expected["logical_calls"])
        check(f"{strategy} local HTTP attempts", attempts == expected["http_attempts"])


def main() -> None:
    manifest = load_json(MANIFEST_PATH)
    protocol = load_json(PROTOCOL_PATH)
    compact_path = BACKEND_ROOT / manifest["artifacts"]["compact_result"]["path"]
    summary = load_json(compact_path)

    verify_tracked_artifacts(manifest, summary)
    order = verify_protocol(manifest, protocol, summary)
    verify_metrics(manifest, summary, order)
    verify_selection(manifest, summary)
    verify_llm_audit(manifest, summary, order)
    verify_local_traces(manifest, order)
    print("[PASS] frozen extended-experiment posthoc result verified")


if __name__ == "__main__":
    main()
