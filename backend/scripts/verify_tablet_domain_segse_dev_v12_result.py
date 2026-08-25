"""Verify v1.2 raw, corrected, and optional live SEGSE development artifacts."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse_corrected import (  # noqa: E402
    build_corrected_v12_summary,
)

SOURCE_PATH = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v12.json"
CORRECTED_PATH = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v12_corrected.json"
)
MANIFEST_PATH = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_dev_v12_result.json"
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


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    corrected = json.loads(CORRECTED_PATH.read_text(encoding="utf-8"))
    artifacts = manifest["artifacts"]
    check("source compact hash", _sha256(SOURCE_PATH) == artifacts["source_compact"]["sha256"])
    check("source compact size", SOURCE_PATH.stat().st_size == artifacts["source_compact"]["size_bytes"])
    check("corrected compact hash", _sha256(CORRECTED_PATH) == artifacts["corrected_compact"]["sha256"])
    check("corrected compact size", CORRECTED_PATH.stat().st_size == artifacts["corrected_compact"]["size_bytes"])
    analysis = BACKEND_ROOT / artifacts["analysis"]["path"]
    check("analysis hash", _sha256_lf(analysis) == artifacts["analysis"]["sha256_lf_normalized"])
    for relative, expected in manifest[
        "erratum_sources_sha256_lf_normalized"
    ].items():
        check(
            f"erratum source hash: {relative}",
            _sha256_lf(BACKEND_ROOT / relative) == expected,
        )
    check("corrected result recomputes", build_corrected_v12_summary(source) == corrected)
    check("result remains development-only", not manifest["confirmatory_evidence"])
    check("module gate passed with downstream pending", corrected["development_gate"]["status"] == "module_gate_passed_downstream_pending")
    check("all evaluable gate checks pass", all(value for value in corrected["development_gate"]["checks"].values() if value is not None))
    check("B0 correction recall corrected", corrected["baseline"]["metrics"]["correction_recall"]["recall"] == 0.5)
    check("v1.2 correction recall corrected", corrected["treatment"]["metrics"]["correction_recall"]["recall"] == 0.5)
    check("v1.2 schema completion", corrected["treatment"]["metrics"]["schema_completion_rate"] == 1.0)
    check("v1.2 C2U zero", corrected["treatment"]["metrics"]["c2u_fp_count"] == 0)
    check("v1.2 final-state FP zero", corrected["treatment"]["metrics"]["final_state"]["false_positive"] == 0)
    treatment = corrected["treatment"]["metrics"]
    expected_headline = {
        "schema_completion": treatment["schema_completion_rate"],
        "raw_candidate_false_positive": treatment["raw_candidate"]["false_positive"],
        "raw_candidate_recall": treatment["raw_candidate"]["recall"],
        "c2u_false_positive": treatment["c2u_fp_count"],
        "material_delta_f1": treatment["material_delta"]["f1"],
        "material_operation_f1": treatment["material_operation_exact"]["f1"],
        "final_state_precision": treatment["final_state"]["precision"],
        "final_state_recall": treatment["final_state"]["recall"],
        "final_state_f1": treatment["final_state"]["f1"],
        "final_state_false_positive": treatment["final_state"]["false_positive"],
        "final_state_false_negative": treatment["final_state"]["false_negative"],
        "correction_recall_case_local": treatment["correction_recall"]["recall"],
        "retract_recall_case_local": treatment["retract_recall"]["recall"],
        "reactivation_recall_case_local": treatment["reactivation_recall"]["recall"],
    }
    check("headline metrics recompute", manifest["corrected_headline"] == expected_headline)

    raw = artifacts["raw_report"]
    raw_path = BACKEND_ROOT / raw["path"]
    if raw_path.exists():
        check("raw report hash", _sha256(raw_path) == raw["sha256"])
        check("raw report size", raw_path.stat().st_size == raw["size_bytes"])
    else:
        print("[SKIP] optional raw report is absent")
    trace = manifest["trace"]
    trace_path = BACKEND_ROOT / "logs" / trace["file"]
    if trace_path.exists():
        check("trace hash", _sha256(trace_path) == trace["sha256"])
        check("trace size", trace_path.stat().st_size == trace["size_bytes"])
        check("trace logical calls", sum(1 for line in trace_path.open(encoding="utf-8") if line.strip()) == 24)
    else:
        print("[SKIP] optional v1.2 trace is absent")
    print("SEGSE-lite v1.2 result verification completed.")


if __name__ == "__main__":
    main()
