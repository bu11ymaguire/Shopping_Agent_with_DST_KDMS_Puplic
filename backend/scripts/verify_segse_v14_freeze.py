"""Verify that the working tree still matches the frozen SEGSE v1.4 method."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.segse_experiment_v13 import SEGSE_V13_SYSTEM_PROMPT  # noqa: E402
from app.segse_experiment_v14 import SEGSE_V14_SYSTEM_PROMPT  # noqa: E402
from app.segse_v14_freeze import (  # noqa: E402
    canonical_vocabulary,
    deterministic_repair_contract,
    method_fingerprint,
)


FREEZE = BACKEND_ROOT / "data" / "manifests" / "segse_v14_method_freeze.json"
RESULT_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v14_result.json"
)


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"[ok] {label}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
    current = method_fingerprint()

    check(
        "freeze records a method, not a confirmatory result",
        frozen["status"] == "frozen_before_untouched_confirmatory_holdout_authoring"
        and frozen["selected_development_evidence"]["not_a_holdout_result"] is True,
    )
    check(
        "development decision is stated as a freeze, not a system adoption",
        frozen["development_decision"].startswith("PASS - freeze v1.4")
        and "no generalization claim" in frozen["development_decision"],
    )
    check(
        "prompt version pinned",
        frozen["method"]["prompt_version"] == current["prompt_version"],
        current["prompt_version"],
    )
    check(
        "prompt text pinned and still identical to v1.3",
        frozen["method"]["prompt_sha256"] == current["prompt_sha256"]
        and SEGSE_V14_SYSTEM_PROMPT == SEGSE_V13_SYSTEM_PROMPT,
    )
    check(
        "strict output schema pinned",
        frozen["method"]["schema_version"] == current["schema_version"]
        and frozen["method"]["strict_schema_sha256"]
        == current["strict_schema_sha256"],
    )
    check(
        "canonical dimension vocabulary pinned",
        frozen["method"]["canonical_vocabulary_sha256"]
        == current["canonical_vocabulary_sha256"]
        and frozen["canonical_vocabulary"] == canonical_vocabulary(),
    )
    check(
        "deterministic repair contract pinned",
        frozen["method"]["deterministic_repair_contract_sha256"]
        == current["deterministic_repair_contract_sha256"]
        and frozen["deterministic_repair_contract"]
        == deterministic_repair_contract(),
    )
    for relative, expected in frozen["method"][
        "source_sha256_lf_normalized"
    ].items():
        check(
            f"frozen source hash: {relative}",
            current["source_sha256_lf_normalized"].get(relative) == expected,
        )
    check(
        "combined method fingerprint pinned",
        frozen["method"]["combined_method_sha256"]
        == current["combined_method_sha256"],
        current["combined_method_sha256"],
    )
    check(
        "every closed-vocabulary facet ID has dimension terms",
        set(frozen["canonical_vocabulary"]["facet_ids"])
        == set(frozen["canonical_vocabulary"]["facet_dimension_terms"]),
    )
    check(
        "C and the lower-layer defenses are declared unchanged",
        any(
            "C semantic no-op suppression" in item
            for item in frozen["deterministic_repair_contract"][
                "unchanged_lower_layer"
            ]
        ),
    )
    check(
        "runtime pinned at temperature zero and one call per turn",
        frozen["frozen_runtime"]["temperature"] == 0
        and frozen["frozen_runtime"]["understanding_calls_per_attempted_turn"] == 1,
    )

    result_manifest = json.loads(RESULT_MANIFEST.read_text(encoding="utf-8"))
    evidence = frozen["selected_development_evidence"]
    check(
        "freeze points at the recorded v1.4 development evidence",
        evidence["run_id"] == result_manifest["run_id"]
        and evidence["gate_status"] == result_manifest["status"]
        and evidence["summary_sha256"]
        == result_manifest["tracked_summary"]["sha256"]
        and _sha256(BACKEND_ROOT / evidence["summary_path"])
        == evidence["summary_sha256"]
        and _sha256(BACKEND_ROOT / evidence["attribution_path"])
        == evidence["attribution_sha256"],
    )
    check(
        "the open dimension-attribution weakness is recorded, not hidden",
        frozen["known_open_weakness"]["layer"] == "semantic_dimension_attribution"
        and frozen["known_open_weakness"]["attribution"].startswith(
            "provider variance"
        ),
    )
    check(
        "confirmatory requirements forbid frozen-utterance reuse",
        frozen["confirmatory_requirements"]["reuse_any_frozen_utterance"] is False
        and frozen["confirmatory_requirements"]["author_holdout_after_this_freeze"]
        is True,
    )
    check(
        "both confirmatory analyses are predeclared",
        len(frozen["confirmatory_requirements"]["analyses"]) == 2
        and any(
            item.startswith("B: fixed-upstream")
            for item in frozen["confirmatory_requirements"]["analyses"]
        ),
    )
    check(
        "persistence amplification and provider variance are declared limitations",
        any("amplified by persistence" in item for item in frozen["limitations"])
        and any("provider variation" in item for item in frozen["limitations"]),
    )
    print("SEGSE v1.4 method freeze verification completed.")


if __name__ == "__main__":
    main()
