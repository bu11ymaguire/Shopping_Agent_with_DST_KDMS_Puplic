"""Verify flat-schema SEGSE-lite v1.2 and its optional development result."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse import aggregate_arm, evaluate_development_gate  # noqa: E402
from app.segse_experiment_v12 import (  # noqa: E402
    SEGSEV12Event,
    SEGSEV12ProposalOutput,
    _normalize_events,
    _raw_to_v1,
)

MANIFEST_PATH = BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_dev_v12_protocol.json"
RESULT_PATH = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v12.json"


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


def verify_contract(manifest: dict) -> None:
    schema_text = json.dumps(SEGSEV12ProposalOutput.model_json_schema(mode="validation"))
    check("v1.2 strict schema builds", bool(schema_text))
    check("provider-incompatible oneOf is absent", '"oneOf"' not in schema_text)
    check("flat event object is present", "SEGSEV12Event" in schema_text)
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        check(
            f"frozen v1.2 hash: {relative}",
            _sha256_lf(BACKEND_ROOT / relative) == expected,
        )
    baseline = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v1.json"
    check("frozen B0 result", _sha256(baseline) == manifest["frozen_baseline_result_sha256"])

    confirm = _raw_to_v1(
        SEGSEV12Event(
            canonical_id="budget",
            act="confirm",
            scope_after="hard",
            value_after="$280 maximum",
            trigger_evidence_text="keep the budget",
            origin="explicit",
            confidence=0.97,
        )
    )
    normalized, audits = _normalize_events([confirm], {"budget"})
    check(
        "confirm payload is deterministically cleared",
        normalized[0].scope_after is None and normalized[0].value_after is None,
    )
    check("confirm payload clearing is audited", len(audits) == 1)

    refine = _raw_to_v1(
        SEGSEV12Event(
            canonical_id="battery",
            act="refine",
            scope_after="soft",
            value_after="long gaming endurance",
            trigger_evidence_text="long gaming endurance",
            origin="explicit",
            confidence=0.97,
        )
    )
    normalized, audits = _normalize_events([refine], set())
    check("new refine normalizes to assert", normalized[0].act == "assert")
    check("refine normalization is audited", len(audits) == 1)


def verify_optional_result() -> None:
    if not RESULT_PATH.exists():
        print("[SKIP] v1.2 live result is absent")
        return
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    treatment = result["treatment"]
    check("v1.2 result completed", result["status"] == "completed")
    check("v1.2 is not confirmatory", not result["protocol"]["confirmatory_evidence"])
    check("v1.2 has 24 cases", len(treatment["case_scores"]) == 24)
    check("v1.2 metrics recompute", aggregate_arm(treatment["case_scores"]) == treatment["metrics"])
    check(
        "v1.2 gate recomputes",
        evaluate_development_gate(result["baseline"]["metrics"], treatment["metrics"])
        == result["development_gate"],
    )
    check("v1.2 trace covers 24 calls", treatment["trace_summary"]["logical_call_count"] == 24)


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    verify_contract(manifest)
    verify_optional_result()
    print("SEGSE-lite v1.2 development verification completed.")


if __name__ == "__main__":
    main()
