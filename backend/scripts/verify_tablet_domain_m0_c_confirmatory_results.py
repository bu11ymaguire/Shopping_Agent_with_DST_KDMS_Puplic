"""Verify the frozen M0-versus-C confirmatory result and optional raw audit files."""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_confirmatory import (  # noqa: E402
    evaluate_confirmatory_decision,
    paired_bootstrap_state_diff,
)

COMPACT_PATH = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_m0_c_confirmatory_v1.json"
)
MANIFEST_PATH = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_m0_c_confirmatory_result_v1.json"
)
PROTOCOL_MANIFEST_PATH = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_m0_c_confirmatory_protocol_v1.json"
)


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_lf_normalized(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-12)


def resolve_artifact(relative: str, *, location: str = "backend") -> Path:
    if location == "repo":
        return BACKEND_ROOT.parent / relative
    return BACKEND_ROOT / relative


def verify_tracked_artifacts(manifest: dict[str, Any]) -> None:
    for label in ("compact_result", "analysis_report", "result_verifier"):
        artifact = manifest["artifacts"][label]
        path = resolve_artifact(artifact["path"])
        check(f"{label} exists", path.is_file(), path)
        check(
            f"{label} LF-normalized hash",
            sha256_lf_normalized(path) == artifact["sha256_lf_normalized"],
        )


def verify_identity(
    compact: dict[str, Any],
    manifest: dict[str, Any],
    protocol: dict[str, Any],
) -> None:
    check(
        "confirmatory summary schema",
        compact["schema_version"]
        == "tablet-domain-m0-c-confirmatory-summary-v1",
    )
    check("confirmatory result frozen", manifest["status"] == "confirmatory_result_frozen")
    check("run ID agrees", compact["run_id"] == manifest["run_id"])
    check(
        "execution commit agrees",
        compact["version_control"]["execution_commit"]
        == manifest["execution_commit"],
    )
    check(
        "selected method commit agrees",
        compact["version_control"]["selected_method_commit"]
        == manifest["selected_method_commit"]
        == protocol["selected_method_commit"],
    )
    check(
        "untouched dataset hash agrees",
        compact["dataset"]["sha256_lf_normalized"]
        == manifest["dataset"]["sha256_lf_normalized"]
        == protocol["dataset"]["sha256_lf_normalized"],
    )
    check(
        "protocol manifest hash frozen",
        sha256_lf_normalized(PROTOCOL_MANIFEST_PATH)
        == manifest["protocol"]["sha256_lf_normalized"],
    )
    check("20 confirmatory scenarios", compact["dataset"]["scenario_count"] == 20)
    check("80 confirmatory turns", compact["dataset"]["turn_count"] == 80)
    check(
        "M0 then C live order",
        compact["protocol"]["live_strategy_order"]
        == manifest["live_strategy_order"]
        == ["m0_baseline", "c_semantic_noop"],
    )


def verify_metrics_and_decision(compact: dict[str, Any]) -> None:
    metrics = compact["condition_metrics"]
    m0 = metrics["m0_baseline"]
    live_c = metrics["c_semantic_noop"]
    replay_c = metrics["c_fixed_upstream_replay"]

    check("M0 produced 56/80 turns", m0["completed_turn_count"] == 56)
    check("C live produced 56/80 turns", live_c["completed_turn_count"] == 56)
    check("C replay follows 56/80 M0 boundary", replay_c["completed_turn_count"] == 56)
    check(
        "fixed replay keeps candidate micro counts",
        replay_c["canonical_id_micro"] == m0["canonical_id_micro"],
    )
    for field in (
        "candidate_false_positive_count",
        "carryover_to_update_false_positive_count",
    ):
        check(
            f"fixed replay keeps {field}",
            replay_c["extended_diagnostics"][field]
            == m0["extended_diagnostics"][field],
        )

    check(
        "fixed replay improves State Diff F1",
        replay_c["state_diff_micro"]["f1"] > m0["state_diff_micro"]["f1"],
    )
    check(
        "fixed replay reduces material false positives 47 to 13",
        m0["extended_diagnostics"]["material_state_diff_false_positive_count"]
        == 47
        and replay_c["extended_diagnostics"][
            "material_state_diff_false_positive_count"
        ]
        == 13,
    )
    check(
        "live C reduces material false positives 47 to 11",
        live_c["extended_diagnostics"]["material_state_diff_false_positive_count"]
        == 11,
    )
    for label, getter in (
        ("Final State F1", lambda item: item["final_state_micro"]["f1"]),
        (
            "correction recall",
            lambda item: item["extended_diagnostics"]["correction_candidate_recall"],
        ),
        (
            "hard-filter completion",
            lambda item: item["hard_constraint_completion_rate"],
        ),
        ("turn completion", lambda item: item["turn_output_completion_rate"]),
    ):
        check(f"fixed replay preserves {label}", close(getter(replay_c), getter(m0)))

    fixed_bootstrap = compact["paired_bootstrap"]["fixed_upstream_replay"]
    live_bootstrap = compact["paired_bootstrap"]["independent_live_replication"]
    check(
        "fixed paired bootstrap lower bound positive",
        fixed_bootstrap["confidence_interval_95"][0] > 0,
        fixed_bootstrap["confidence_interval_95"],
    )
    check(
        "live paired bootstrap lower bound positive",
        live_bootstrap["confidence_interval_95"][0] > 0,
        live_bootstrap["confidence_interval_95"],
    )

    recomputed = evaluate_confirmatory_decision(
        m0_metrics=m0,
        replay_c_metrics=replay_c,
        live_c_metrics=live_c,
        fixed_upstream_bootstrap=fixed_bootstrap,
        independent_live_bootstrap=live_bootstrap,
    )
    check("decision recomputes exactly", recomputed == compact["decision"])
    check("C is confirmed", compact["decision"]["confirmed"])
    for comparison in ("fixed_upstream_replay", "independent_live_replication"):
        section = compact["decision"][comparison]
        check(f"{comparison} passed", section["passed"])
        check(f"{comparison} all checks true", all(section["checks"].values()))


def verify_llm_audit(compact: dict[str, Any], manifest: dict[str, Any]) -> None:
    summaries = compact["live_trace_summaries"]
    m0 = summaries["m0_baseline"]
    c = summaries["c_semantic_noop"]
    audit = manifest["llm_audit"]
    check("126 live logical calls", m0["understanding_call_count"] + c["understanding_call_count"] == 126)
    check("57 schema repairs", m0["schema_repair_count"] + c["schema_repair_count"] == 57)
    check("183 HTTP attempts", m0["http_attempt_count"] + c["http_attempt_count"] == 183)
    check("no live transport retries", m0["transport_retry_count"] + c["transport_retry_count"] == 0)
    check("no live fallbacks", m0["fallback_count"] + c["fallback_count"] == 0)
    check("audit logical-call total", audit["logical_understanding_calls"] == 126)
    check("audit replay adds no LLM calls", audit["fixed_replay_additional_llm_calls"] == 0)
    for summary in (m0, c):
        check(
            "trace model is frozen GPT-4o-mini revision",
            summary["reported_models"] == {"gpt-4o-mini-2024-07-18": 63},
        )
        check("trace structured mode is json_schema", summary["structured_modes"] == {"json_schema": 63})


def verify_optional_raw(compact: dict[str, Any], manifest: dict[str, Any]) -> None:
    artifact = manifest["artifacts"]["raw_report"]
    raw_path = resolve_artifact(artifact["path"])
    if not raw_path.exists():
        print("[SKIP] optional raw report is absent")
        return
    check("raw report size", raw_path.stat().st_size == artifact["size_bytes"])
    check("raw report hash", sha256(raw_path) == artifact["sha256"])
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    check("raw run ID agrees", raw["run_id"] == compact["run_id"])
    check("raw execution completed", raw["status"] == "completed")
    conditions = raw["conditions"]
    check(
        "fixed replay uses M0 source and zero LLM calls",
        conditions["c_fixed_upstream_replay"]["source_condition"] == "m0_baseline"
        and conditions["c_fixed_upstream_replay"]["additional_llm_calls"] == 0,
    )

    source_records = {
        item["scenario_id"]: item for item in conditions["m0_baseline"]["scenarios"]
    }
    replay_records = conditions["c_fixed_upstream_replay"]["scenarios"]
    for replay in replay_records:
        source = source_records[replay["scenario_id"]]
        check(
            f"{replay['scenario_id']} replay stops at exact M0 boundary",
            replay["completed_turn_count"]
            == replay["source_m0_completed_turn_count"]
            == source["completed_turn_count"]
            and replay["missing_gold_turns"] == source["missing_gold_turns"],
        )
    check(
        "replay scenario-completion zero is a status-label artifact",
        conditions["c_fixed_upstream_replay"]["metrics"]["scenario_completion_rate"]
        == 0.0
        and all(item["status"] == "completed_to_m0_boundary" for item in replay_records),
    )

    expected_fixed = compact["paired_bootstrap"]["fixed_upstream_replay"]
    expected_live = compact["paired_bootstrap"]["independent_live_replication"]
    recomputed_fixed = paired_bootstrap_state_diff(
        conditions["m0_baseline"]["scenarios"],
        replay_records,
        resamples=expected_fixed["resamples"],
        seed=expected_fixed["seed"],
    )
    recomputed_live = paired_bootstrap_state_diff(
        conditions["m0_baseline"]["scenarios"],
        conditions["c_semantic_noop"]["scenarios"],
        resamples=expected_live["resamples"],
        seed=expected_live["seed"],
    )
    check("fixed bootstrap recomputes exactly", recomputed_fixed == expected_fixed)
    check("live bootstrap recomputes exactly", recomputed_live == expected_live)

    for label, trace in manifest["traces"].items():
        trace_path = BACKEND_ROOT / "logs" / trace["file"]
        if not trace_path.exists():
            print(f"[SKIP] optional {label} trace is absent")
            continue
        check(f"{label} trace size", trace_path.stat().st_size == trace["size_bytes"])
        check(f"{label} trace hash", sha256(trace_path) == trace["sha256"])
        line_count = sum(1 for line in trace_path.open(encoding="utf-8") if line.strip())
        check(f"{label} trace logical records", line_count == trace["logical_calls"])


def main() -> None:
    compact = json.loads(COMPACT_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    protocol = json.loads(PROTOCOL_MANIFEST_PATH.read_text(encoding="utf-8"))
    verify_tracked_artifacts(manifest)
    verify_identity(compact, manifest, protocol)
    verify_metrics_and_decision(compact)
    verify_llm_audit(compact, manifest)
    verify_optional_raw(compact, manifest)
    print("M0-versus-C confirmatory result verification completed.")


if __name__ == "__main__":
    main()
