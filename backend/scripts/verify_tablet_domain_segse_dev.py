"""Verify the frozen SEGSE development runner, evaluator, and optional live result."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse import (  # noqa: E402
    ARM_ORDER,
    aggregate_arm,
    evaluate_development_gate,
    expected_state,
    gold_material_operations,
    score_case,
    seed_dialogue_state,
)
from app.segse_experiment import (  # noqa: E402
    SEGSEAppliedOperation,
    SEGSEMetadataDelta,
    SEGSEStateEvent,
)

DATASET_PATH = BACKEND_ROOT / "data" / "tablet_domain_segse_dev_v1.json"
MANIFEST_PATH = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_dev_protocol_v1.json"
)
RESULT_PATH = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v1.json"


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[OK] {label}")


def _sha256_lf_normalized(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _event(payload: dict[str, Any]) -> SEGSEStateEvent:
    return SEGSEStateEvent(
        canonical_id=payload["canonical_id"],
        act=payload["act"],
        scope_after=payload.get("scope_after"),
        value_after=payload.get("value_after"),
        relation=payload.get("relation"),
        trigger_evidence_text=payload["evidence_text"],
        value_source=payload["value_source"],
        source_ref=payload.get("source_ref"),
        origin="explicit",
        confidence=0.98,
    )


def verify_frozen_contract(dataset: dict[str, Any], manifest: dict[str, Any]) -> None:
    check("development status is explicit", dataset["split"] == "development")
    check("24 cases are frozen", len(dataset["cases"]) == 24)
    check(
        "12 contrast families are frozen",
        len({item["family"] for item in dataset["cases"]}) == 12,
    )
    check("live arm order is B0 then D4", tuple(manifest["live_arm_order"]) == ARM_ORDER)
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        check(
            f"frozen hash: {relative}",
            _sha256_lf_normalized(BACKEND_ROOT / relative) == expected,
        )


def verify_perfect_oracle(dataset: dict[str, Any]) -> None:
    records = []
    operation_counts: dict[str, int] = {}
    for case in dataset["cases"]:
        before = seed_dialogue_state(case)
        operations = gold_material_operations(case, before)
        for operation in operations.values():
            operation_counts[operation] = operation_counts.get(operation, 0) + 1
        events = [_event(item) for item in case["gold_events"]]
        understanding = SimpleNamespace(
            domain_route=case["gold_route"],
            segse_raw_events=events,
            segse_authorized_events=events,
            segse_event_rejections=[],
            segse_material_operations=[
                SEGSEAppliedOperation(
                    canonical_id=canonical_id,
                    operation=operation,
                    changed_paths=[],
                )
                for canonical_id, operation in operations.items()
            ],
            segse_metadata_deltas=[
                SEGSEMetadataDelta(
                    canonical_id=item["canonical_id"],
                    changes=["support_added"],
                    decision_eligibility_changed=False,
                )
                for item in case["gold_events"]
                if item["act"] == "confirm"
            ],
        )
        records.append(
            score_case(
                arm="d4_segse_lite",
                case=case,
                before=before,
                after=expected_state(case, before),
                understanding=understanding,
            )
        )
    metrics = aggregate_arm(records)
    for field in (
        "raw_candidate",
        "authorized_candidate",
        "raw_typed_event",
        "authorized_typed_event",
        "material_delta",
        "material_operation_exact",
        "final_state",
    ):
        check(f"perfect oracle {field} F1", metrics[field]["f1"] == 1.0)
    check("perfect oracle route accuracy", metrics["route_accuracy"] == 1.0)
    check("fixture includes corrections", metrics["correction_recall"]["target_count"] >= 3)
    check("fixture includes retractions", metrics["retract_recall"]["target_count"] == 2)
    check("fixture includes reactivation", metrics["reactivation_recall"]["target_count"] == 1)
    check("fixture includes typed refinement", operation_counts.get("refine") == 1)

    first = dataset["cases"][0]
    prior = seed_dialogue_state(first)
    missing = score_case(
        arm="d4_segse_lite",
        case=first,
        before=prior,
        after=prior.model_copy(deep=True),
        understanding=None,
        error=RuntimeError("synthetic missing output"),
    )
    check("missing output is incomplete", not missing["completed"])
    check("missing output contributes false negatives", missing["raw_candidate"]["false_negative"] == 1)


def verify_optional_result() -> None:
    if not RESULT_PATH.exists():
        print("[SKIP] live development result is absent")
        return
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    check("live result completed", result["status"] == "completed")
    check("live result remains development-only", result["study_status"] == "development_not_confirmatory")
    check("result arm order", tuple(result["protocol"]["arm_order"]) == ARM_ORDER)
    for arm in ARM_ORDER:
        arm_result = result["arms"][arm]
        check(f"{arm} has 24 case scores", len(arm_result["case_scores"]) == 24)
        check(
            f"{arm} metrics recompute exactly",
            aggregate_arm(arm_result["case_scores"]) == arm_result["metrics"],
        )
        check(
            f"{arm} trace logical calls cover all cases",
            arm_result["trace_summary"]["logical_call_count"] == 24,
        )
    recomputed = evaluate_development_gate(
        result["arms"]["b0_c_semantic_noop"]["metrics"],
        result["arms"]["d4_segse_lite"]["metrics"],
    )
    check("development gate recomputes exactly", recomputed == result["development_gate"])


def main() -> None:
    dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    verify_frozen_contract(dataset, manifest)
    verify_perfect_oracle(dataset)
    verify_optional_result()
    print("SEGSE development evaluation verification completed.")


if __name__ == "__main__":
    main()
