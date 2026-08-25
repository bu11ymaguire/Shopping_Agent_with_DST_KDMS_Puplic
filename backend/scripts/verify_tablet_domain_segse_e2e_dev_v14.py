"""Verify the frozen SEGSE v1.4 development protocol before and after the live run."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.segse_experiment_v13 import SEGSE_V13_SYSTEM_PROMPT  # noqa: E402
from app.segse_experiment_v14 import (  # noqa: E402
    SEGSE_V14_PROMPT_VERSION,
    SEGSE_V14_SYSTEM_PROMPT,
)


MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_e2e_dev_v14.json"
)
V13_RESULT_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v13_result.json"
)
V1_RESULT_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v1_result.json"
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[ok] {label}")


def _sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check("v1.4 remains development-only", manifest["confirmatory_evidence"] is False)
    check(
        "prompt version frozen",
        manifest["prompt_version"] == SEGSE_V14_PROMPT_VERSION,
    )
    check(
        "only deterministic interpretation changed",
        manifest["changed_layer"] == "deterministic_event_interpretation_only"
        and manifest["prompt_text_identical_to_v13"] is True
        and SEGSE_V14_SYSTEM_PROMPT == SEGSE_V13_SYSTEM_PROMPT,
    )
    check(
        "gate is the predeclared v1.3 gate",
        manifest["development_gates"]["gate_identical_to_v13"] is True,
    )
    for relative, expected in manifest[
        "frozen_sources_sha256_lf_normalized"
    ].items():
        check(
            f"frozen v1.4 source hash: {relative}",
            _sha256_lf(BACKEND_ROOT / relative) == expected,
        )
    check(
        "development fixture unchanged",
        _sha256_lf(BACKEND_ROOT / manifest["dataset"]["path"])
        == manifest["dataset"]["sha256_lf_normalized"],
    )
    baseline_manifest = json.loads(V1_RESULT_MANIFEST.read_text(encoding="utf-8"))
    check(
        "B0 reference is reused, not recomputed",
        manifest["baseline_reference"]["reuse"] == "tracked_frozen_summary"
        and manifest["baseline_reference"]["additional_llm_calls"] == 0
        and manifest["baseline_reference"]["sha256"]
        == baseline_manifest["tracked_summary"]["sha256"]
        and _sha256(BACKEND_ROOT / manifest["baseline_reference"]["path"])
        == manifest["baseline_reference"]["sha256"],
    )
    v13_manifest = json.loads(V13_RESULT_MANIFEST.read_text(encoding="utf-8"))
    check(
        "v1.3 reference is reused, not recomputed",
        manifest["v13_reference"]["additional_llm_calls"] == 0
        and manifest["v13_reference"]["sha256"]
        == v13_manifest["tracked_summary"]["sha256"]
        and _sha256(BACKEND_ROOT / manifest["v13_reference"]["path"])
        == manifest["v13_reference"]["sha256"],
    )
    check(
        "one Understanding call per attempted turn",
        manifest["run_contract"]["understanding_calls_per_attempted_turn"] == 1
        and manifest["run_contract"]["maximum_logical_calls"] == 24
        and manifest["run_contract"]["temperature"] == 0,
    )
    check(
        "missing outputs stay failures",
        manifest["run_contract"]["missing_outputs_counted_as_failures"] is True
        and manifest["run_contract"]["continue_after_failed_turn"] is True,
    )
    print("SEGSE v1.4 development protocol verification completed.")


if __name__ == "__main__":
    main()
