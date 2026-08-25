"""Verify the isolated SEGSE-lite v1.1 development contract and optional result."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse import (  # noqa: E402
    aggregate_arm,
    evaluate_development_gate,
)
from app.segse_experiment_v11 import (  # noqa: E402
    SEGSEV11AssertEvent,
    SEGSEV11ConfirmEvent,
    SEGSEV11ItemActionProposal,
    SEGSEV11ProposalOutput,
    SEGSEV11RefineEvent,
    _normalize_events,
    _raw_to_v1,
    _sanitize_action,
    _v11_post_authorize,
)

MANIFEST_PATH = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_dev_v11_protocol.json"
)
RESULT_PATH = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v11.json"


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
    schema = SEGSEV11ProposalOutput.model_json_schema(mode="validation")
    check("v1.1 strict schema builds", bool(schema))
    check("act discriminator is present", "discriminator" in json.dumps(schema))
    check("v1.1 is post-v1 development", manifest["confirmatory_evidence"] is False)
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        check(
            f"frozen v1.1 hash: {relative}",
            _sha256_lf(BACKEND_ROOT / relative) == expected,
        )
    baseline_path = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v1.json"
    check(
        "v1 B0 result is frozen",
        _sha256(baseline_path) == manifest["frozen_baseline_result_sha256"],
    )

    raw_refine = SEGSEV11RefineEvent(
        canonical_id="battery",
        act="refine",
        scope_after="soft",
        value_after="long battery life matters",
        trigger_evidence_text="long battery life",
        origin="explicit",
        confidence=0.95,
    )
    v1_refine = _raw_to_v1(raw_refine)
    normalized, audits = _normalize_events([v1_refine], set())
    check("new refine normalizes to assert", normalized[0].act == "assert")
    check("normalization is audited", len(audits) == 1)

    raw_confirm = SEGSEV11ConfirmEvent(
        canonical_id="battery",
        act="confirm",
        trigger_evidence_text="battery is still important",
        origin="explicit",
        confidence=0.97,
    )
    v1_confirm = _raw_to_v1(raw_confirm)
    check(
        "confirm source is derived",
        v1_confirm.value_source == "prior_state_reference"
        and v1_confirm.source_ref == "battery"
        and v1_confirm.value_after is None,
    )

    invalid_action = SEGSEV11ItemActionProposal(
        name="compare_first_second",
        target_rank=1,
        compare_rank=None,
    )
    action, violations = _sanitize_action(invalid_action)
    check("invalid action is isolated", action is None and len(violations) == 1)

    expansion = _raw_to_v1(
        SEGSEV11AssertEvent(
            canonical_id="storage_capacity",
            act="assert",
            scope_after="hard",
            value_after="1 TB microSD expansion",
            trigger_evidence_text="microSD expansion",
            origin="explicit",
            confidence=0.95,
        )
    )
    accepted, rejected = _v11_post_authorize(
        [expansion], current_utterance="microSD expansion", prior_rejections=[]
    )
    check(
        "expansion storage is not internal storage",
        not accepted and rejected[0].reason == "expansion_storage_is_not_internal_storage",
    )


def verify_optional_result() -> None:
    if not RESULT_PATH.exists():
        print("[SKIP] v1.1 live result is absent")
        return
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    check("v1.1 result completed", result["status"] == "completed")
    check("v1.1 remains development-only", not result["protocol"]["confirmatory_evidence"])
    treatment = result["treatment"]
    check("v1.1 has 24 case scores", len(treatment["case_scores"]) == 24)
    check(
        "v1.1 metrics recompute",
        aggregate_arm(treatment["case_scores"]) == treatment["metrics"],
    )
    gate = evaluate_development_gate(
        result["baseline"]["metrics"], treatment["metrics"]
    )
    check("v1.1 gate recomputes", gate == result["development_gate"])
    check(
        "v1.1 trace covers all cases",
        treatment["trace_summary"]["logical_call_count"] == 24,
    )


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    verify_contract(manifest)
    verify_optional_result()
    print("SEGSE-lite v1.1 development verification completed.")


if __name__ == "__main__":
    main()
