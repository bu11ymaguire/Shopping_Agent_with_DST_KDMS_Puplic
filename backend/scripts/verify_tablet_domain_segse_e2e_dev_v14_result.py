"""Verify the SEGSE v1.4 exposed-fixture development result."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.run_tablet_domain_segse_e2e_dev_v14 import (  # noqa: E402
    evaluate_v14_gate,
)


SUMMARY = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v14.json"
ATTRIBUTION = (
    BACKEND_ROOT
    / "data"
    / "results"
    / "tablet_domain_segse_e2e_dev_v14_attribution.json"
)
MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v14_result.json"
)
V13_SUMMARY = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_e2e_dev_v13.json"


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[ok] {label}")


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


def main() -> None:
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    attribution = json.loads(ATTRIBUTION.read_text(encoding="utf-8"))
    v13 = json.loads(V13_SUMMARY.read_text(encoding="utf-8"))

    check("result is development-only", manifest["confirmatory_evidence"] is False)
    check(
        "decision is worded as a freeze, not a system adoption",
        manifest["development_decision"].startswith(
            "PASS - freeze v1.4 for confirmatory evaluation"
        )
        and "not a system adoption decision" in manifest["development_decision"],
    )
    check(
        "state gains are labelled persistence-amplified",
        "amplified by persistence" in manifest["state_metric_interpretation"],
    )
    check(
        "the open failure layer is separated from the solved ones",
        manifest["open_failure_layer"]["name"]
        == "novel_candidate_grounding_and_dimension_attribution"
        and set(manifest["open_failure_layer"]["distinct_from"])
        == {"carryover_to_update", "material_no_op", "correction_semantics"},
    )
    check("summary hash", _sha256(SUMMARY) == manifest["tracked_summary"]["sha256"])
    check(
        "attribution hash",
        _sha256(ATTRIBUTION) == manifest["tracked_attribution"]["sha256"],
    )
    check(
        "attribution generator hash",
        _sha256_lf(BACKEND_ROOT / manifest["tracked_attribution"]["generator"])
        == manifest["tracked_attribution"]["generator_sha256_lf_normalized"],
    )
    check(
        "attribution used no new LLM calls",
        manifest["tracked_attribution"]["additional_llm_calls"] == 0
        and attribution["additional_llm_calls"] == 0,
    )

    baseline = summary["baseline_reference"]["metrics"]
    treatment = summary["treatment"]["metrics"]
    check(
        "decision recomputes from the frozen gate",
        evaluate_v14_gate(baseline, treatment) == summary["decision"],
    )
    check(
        "recorded status matches the manifest",
        summary["decision"]["status"] == manifest["status"],
    )
    check("all gate checks pass", all(summary["decision"]["checks"].values()))
    check("24 treatment turns completed", treatment["completed_turn_count"] == 24)

    check(
        "prompt text unchanged from v1.3",
        summary["runtime"]["prompt_text_identical_to_v13"] is True
        and manifest["prompt_text_identical_to_v13"] is True,
    )
    check(
        "only deterministic interpretation changed",
        manifest["changed_layer"] == "deterministic_event_interpretation_only",
    )

    v13_metrics = v13["treatment"]["metrics"]
    check(
        "v1.3 reference reused without new calls",
        summary["v13_reference"]["run_id"] == v13["run_id"]
        and summary["v13_reference"]["metrics"] == v13_metrics,
    )
    check(
        "correction recall fully recovered",
        treatment["correction_recall"]["target_count"] == 2
        and treatment["correction_recall"]["recall"] == 1.0
        and v13_metrics["correction_recall"]["recall"] == 0.0,
    )
    check(
        "retract and reactivation recall preserved",
        treatment["retract_recall"]["recall"] >= v13_metrics["retract_recall"]["recall"]
        and treatment["reactivation_recall"]["recall"]
        >= v13_metrics["reactivation_recall"]["recall"],
    )
    check(
        "sparse-grounding precision not given back",
        treatment["candidate_fp_count"] <= v13_metrics["candidate_fp_count"]
        and treatment["c2u_fp_count"] <= v13_metrics["c2u_fp_count"]
        and treatment["raw_candidate"]["precision"]
        >= v13_metrics["raw_candidate"]["precision"]
        and treatment["raw_candidate"]["recall"]
        >= v13_metrics["raw_candidate"]["recall"]
        and treatment["lexical_evidence_validity"]["rate"] == 1.0,
    )
    check(
        "turn completion not worsened",
        treatment["turn_output_completion_rate"]
        >= v13_metrics["turn_output_completion_rate"],
    )
    check(
        "scenario-final FP not increased against v1.3",
        treatment["scenario_final_state"]["false_positive"]
        <= v13_metrics["scenario_final_state"]["false_positive"],
    )

    regression = summary["regression_versus_v13"]
    check(
        "the turnwise FP increase is recorded rather than hidden",
        regression["turnwise_final_fp"]["v13"] == 1
        and regression["turnwise_final_fp"]["v14"] == 3
        and regression["turnwise_final_fp"]["delta"] == 2,
    )
    check(
        "the only new final FP token is recorded",
        manifest["attribution"]["new_final_fp_tokens"] == ["note_taking|soft"]
        and [
            item["token"] for item in treatment["first_final_fp_origins"]
        ]
        == ["note_taking|soft"],
    )

    code_turns = manifest["attribution"]["code_attributed_turns"]
    provider_turns = manifest["attribution"]["provider_attributed_turns"]
    check(
        "attribution manifest matches the computed replay",
        attribution["turns_where_code_changed_outcome"] == code_turns
        and attribution["turns_where_provider_changed_outcome"] == provider_turns,
    )
    check(
        "no code-attributed turn introduced the new FP",
        "se05t2" in provider_turns and "se05t2" not in code_turns,
    )
    by_id = {item["turn_id"]: item for item in attribution["turns"]}
    new_fp_turn = by_id["se05t2"]["cells"]
    check(
        "both pipelines agree on the provider-variance turn",
        new_fp_turn["v13_raw|v13_code"]["material_operations"]
        == new_fp_turn["v13_raw|v14_code"]["material_operations"]
        and new_fp_turn["v14_raw|v13_code"]["material_operations"]
        == new_fp_turn["v14_raw|v14_code"]["material_operations"]
        and new_fp_turn["v14_raw|v14_code"]["material_operations"]
        == ["note_taking|add"],
    )
    for turn_id in ("se02t4", "se04t3"):
        cells = by_id[turn_id]["cells"]
        check(
            f"code change recovers the correction at {turn_id}",
            cells["v13_raw|v13_code"]["material_operations"]
            != cells["v13_raw|v14_code"]["material_operations"],
        )

    for artifact in manifest["git_ignored_artifacts"].values():
        path = BACKEND_ROOT / artifact["path"]
        if path.exists():
            check(
                f"optional artifact hash: {artifact['path']}",
                _sha256(path) == artifact["sha256"],
            )
    print("SEGSE v1.4 result verification completed.")


if __name__ == "__main__":
    main()
