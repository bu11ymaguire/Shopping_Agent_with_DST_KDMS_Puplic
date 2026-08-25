"""Verify tracked and optional raw artifacts from the first SEGSE dev run."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse import (  # noqa: E402
    ARM_ORDER,
    aggregate_arm,
    evaluate_development_gate,
)

RESULT_PATH = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v1.json"
MANIFEST_PATH = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_dev_result_v1.json"
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[OK] {label}")


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


def _resolve(relative: str) -> Path:
    return BACKEND_ROOT / relative


def verify_tracked(result: dict[str, Any], manifest: dict[str, Any]) -> None:
    compact = manifest["artifacts"]["compact_result"]
    analysis = manifest["artifacts"]["analysis"]
    check("compact result size", RESULT_PATH.stat().st_size == compact["size_bytes"])
    check("compact result hash", _sha256(RESULT_PATH) == compact["sha256"])
    check(
        "analysis hash",
        _sha256_lf(_resolve(analysis["path"])) == analysis["sha256_lf_normalized"],
    )
    check("result remains development-only", not manifest["confirmatory_evidence"])
    check("module gate failure is recorded", manifest["status"] == "development_module_gate_failed")
    check("execution identity", result["run_id"] == manifest["run_id"])
    check("arm order", tuple(manifest["arm_order"]) == ARM_ORDER)
    for arm in ARM_ORDER:
        payload = result["arms"][arm]
        check(f"{arm} has 24 cases", len(payload["case_scores"]) == 24)
        check(
            f"{arm} metrics recompute exactly",
            aggregate_arm(payload["case_scores"]) == payload["metrics"],
        )
    gate = evaluate_development_gate(
        result["arms"]["b0_c_semantic_noop"]["metrics"],
        result["arms"]["d4_segse_lite"]["metrics"],
    )
    check("development gate recomputes", gate == result["development_gate"])
    check("gate failed", gate["status"] == "module_gate_failed")
    check(
        "only schema completion prespecified check failed",
        [key for key, value in gate["checks"].items() if value is False]
        == ["schema_completion_drop_at_most_05pp"],
    )
    b0 = result["arms"]["b0_c_semantic_noop"]["metrics"]
    d4 = result["arms"]["d4_segse_lite"]["metrics"]
    headline = manifest["headline"]
    expected = {
        "b0_schema_completion": b0["schema_completion_rate"],
        "d4_schema_completion": d4["schema_completion_rate"],
        "b0_raw_candidate_fp": b0["raw_candidate"]["false_positive"],
        "d4_raw_candidate_fp": d4["raw_candidate"]["false_positive"],
        "b0_c2u_fp": b0["c2u_fp_count"],
        "d4_c2u_fp": d4["c2u_fp_count"],
        "b0_material_fp": b0["material_delta"]["false_positive"],
        "d4_material_fp": d4["material_delta"]["false_positive"],
        "b0_material_fn": b0["material_delta"]["false_negative"],
        "d4_material_fn": d4["material_delta"]["false_negative"],
        "b0_final_state_f1": b0["final_state"]["f1"],
        "d4_final_state_f1": d4["final_state"]["f1"],
        "b0_final_state_fp": b0["final_state"]["false_positive"],
        "d4_final_state_fp": d4["final_state"]["false_positive"],
        "d4_event_rejection_count": d4["event_rejection_count"],
    }
    check("headline metrics recompute", headline == expected)


def verify_optional_raw(manifest: dict[str, Any]) -> None:
    raw = manifest["artifacts"]["raw_report"]
    raw_path = _resolve(raw["path"])
    if raw_path.exists():
        check("raw report size", raw_path.stat().st_size == raw["size_bytes"])
        check("raw report hash", _sha256(raw_path) == raw["sha256"])
    else:
        print("[SKIP] optional raw report is absent")
    for arm, trace in manifest["traces"].items():
        path = BACKEND_ROOT / "logs" / trace["file"]
        if not path.exists():
            print(f"[SKIP] optional {arm} trace is absent")
            continue
        check(f"{arm} trace size", path.stat().st_size == trace["size_bytes"])
        check(f"{arm} trace hash", _sha256(path) == trace["sha256"])
        lines = sum(1 for line in path.open(encoding="utf-8") if line.strip())
        check(f"{arm} trace logical calls", lines == trace["logical_calls"])


def main() -> None:
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    verify_tracked(result, manifest)
    verify_optional_raw(manifest)
    print("SEGSE first development result verification completed.")


if __name__ == "__main__":
    main()
