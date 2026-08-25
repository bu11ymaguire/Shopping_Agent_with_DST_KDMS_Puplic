"""Verify SEGSE v1.3 value safety and frozen development inputs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.segse_experiment import SEGSEStateEvent  # noqa: E402
from app.segse_experiment_v13 import (  # noqa: E402
    SEGSE_V13_PROMPT_VERSION,
    _filter_hard_value_contracts,
    _normalize_soft_only_scopes,
)


MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v13.json"
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[ok] {label}")


def _sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _event(
    canonical_id: str,
    *,
    scope: str,
    value: str,
    evidence: str,
) -> SEGSEStateEvent:
    return SEGSEStateEvent(
        canonical_id=canonical_id,
        act="assert",
        scope_after=scope,
        value_after=value,
        relation="require" if scope == "hard" else "prefer",
        trigger_evidence_text=evidence,
        value_source="current_utterance",
        source_ref=None,
        origin="explicit",
        confidence=1,
    )


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check("v1.3 remains development-only", manifest["confirmatory_evidence"] is False)
    check(
        "prompt version frozen",
        manifest["prompt_version"] == SEGSE_V13_PROMPT_VERSION,
    )
    check(
        "frozen source hashes",
        all(
            _sha256_lf(BACKEND_ROOT / relative) == expected
            for relative, expected in manifest[
                "frozen_sources_sha256_lf_normalized"
            ].items()
        ),
    )

    battery = _event(
        "battery",
        scope="hard",
        value="long",
        evidence="Long battery life is a priority.",
    )
    normalized, audits = _normalize_soft_only_scopes([battery])
    check("soft-only hard scope normalized", normalized[0].scope_after == "soft")
    check("soft-only relation normalized", normalized[0].relation == "prefer")
    check("scope normalization audited", len(audits) == 1)

    invalid_rating = _event(
        "min_rating",
        scope="hard",
        value="11 inches",
        evidence="The screen must be at least 11 inches.",
    )
    accepted, rejections, value_rejections = _filter_hard_value_contracts(
        [invalid_rating], []
    )
    check("11-inch rating rejected", not accepted)
    check(
        "rating dimension rejection is explicit",
        rejections[0].reason == "min_rating_requires_zero_to_five_star_dimension",
    )
    check("value rejection audit retained", value_rejections == rejections)

    valid_display = _event(
        "display",
        scope="hard",
        value="at least 11 inches",
        evidence="The screen must be at least 11 inches.",
    )
    accepted, rejections, _ = _filter_hard_value_contracts([valid_display], [])
    check("valid numeric display accepted", accepted == [valid_display] and not rejections)

    invalid_storage = _event(
        "storage_capacity",
        scope="hard",
        value="large cards",
        evidence="A microSD slot that accepts large cards would be convenient.",
    )
    accepted, rejections, _ = _filter_hard_value_contracts([invalid_storage], [])
    check("non-numeric storage rejected", not accepted and bool(rejections))
    check(
        "baseline result reused without new call",
        manifest["baseline_reference"]["reuse"] == "tracked_frozen_summary",
    )
    print("SEGSE v1.3 development protocol verification completed.")


if __name__ == "__main__":
    main()
