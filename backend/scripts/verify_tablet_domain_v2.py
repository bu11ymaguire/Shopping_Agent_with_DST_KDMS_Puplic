"""Verify the deterministic tablet-domain v2 environment and routing contract."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from pydantic import ValidationError

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.models.actual_demo import (  # noqa: E402
    TabletDomainFacetCandidates,
    TabletDomainStateUpdateCandidate,
    TabletDomainUnderstandingOutput,
)
from app.nodes.actual_policy import select_actual_policy  # noqa: E402
from app.nodes.actual_state_manager import (  # noqa: E402
    create_tablet_environment_state,
    update_actual_dialogue_state,
)
from app.nodes.state_manager import create_initial_dialogue_state  # noqa: E402

FREEZE_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_v2_freeze.json"
)
DEV_DATASET = BACKEND_ROOT / "data" / "tablet_domain_understanding_dev_v1.json"
SEMANTIC_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "amazon_tablet_semantic_retrieval_v1.json"
)


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def constraint(
    canonical_id: str,
    value_text: str,
    evidence_text: str,
    *,
    scope: str,
) -> TabletDomainStateUpdateCandidate:
    return TabletDomainStateUpdateCandidate.model_validate(
        {
            "canonical_id": canonical_id,
            "scope": scope,
            "value_text": value_text,
            "evidence_text": evidence_text,
            "origin": "explicit",
            "confidence": 1.0,
        }
    )


def in_domain_output() -> TabletDomainUnderstandingOutput:
    return TabletDomainUnderstandingOutput(
        utterance="Something for note taking under $300.",
        intents=["search"],
        domain_route="in_domain",
        facets=TabletDomainFacetCandidates(),
        candidates=[
            constraint(
                "budget",
                "$300 maximum",
                "under $300",
                scope="hard",
            ),
            constraint(
                "note_taking",
                "note taking is important",
                "note taking",
                scope="soft",
            ),
        ],
        supersedes=[],
        residual_color_choice=False,
    )


def unsupported_output() -> TabletDomainUnderstandingOutput:
    return TabletDomainUnderstandingOutput(
        utterance="I need a laptop for coding.",
        intents=["unknown"],
        domain_route="unsupported_category",
        unsupported_category_text="laptop",
        unsupported_category_evidence="laptop",
        facets=TabletDomainFacetCandidates(),
        candidates=[],
        supersedes=[],
        residual_color_choice=False,
    )


def expect_invalid(label: str, payload: dict[str, object]) -> None:
    try:
        TabletDomainUnderstandingOutput.model_validate(payload)
    except ValidationError:
        check(label, True)
    else:
        raise AssertionError(f"{label}: validation unexpectedly succeeded")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    legacy = create_initial_dialogue_state()
    check("legacy v1 state remains category-free", legacy.category is None)

    initial = create_tablet_environment_state()
    check("environment route is in-domain", initial.domain_route == "in_domain")
    check(
        "tablet category is confirmed environment state",
        initial.category is not None
        and initial.category.canonical_id == "category_tablet"
        and initial.category.origin == "environment"
        and initial.category.status == "confirmed",
    )
    check(
        "environment state has no fabricated utterance provenance",
        initial.category is not None
        and initial.category.evidence_turn_ids == []
        and initial.category.updated_at_turn_id == "environment-init",
    )

    first_state, first_diff = update_actual_dialogue_state(
        initial,
        in_domain_output(),
        [],
        turn_id="turn-1",
    )
    check(
        "in-domain utterance cannot replace environment category",
        first_state.category == initial.category,
    )
    check(
        "in-domain constraints merge normally",
        set(first_state.hard_constraints) == {"budget"}
        and set(first_state.soft_constraints) == {"note_taking"},
        first_diff.changed_paths,
    )
    first_policy = select_actual_policy(first_state)
    check(
        "specific in-domain state reaches recommendation lane",
        first_policy.lane == "recommend-lane",
        first_policy.model_dump(mode="json"),
    )

    blocked_state, blocked_diff = update_actual_dialogue_state(
        first_state,
        unsupported_output(),
        [],
        turn_id="turn-2",
    )
    check(
        "unsupported request does not erase tablet preferences",
        blocked_state.hard_constraints == first_state.hard_constraints
        and blocked_state.soft_constraints == first_state.soft_constraints,
        blocked_diff.changed_paths,
    )
    blocked_policy = select_actual_policy(blocked_state)
    check(
        "explicit laptop request is blocked before retrieval",
        blocked_policy.lane == "clarify-lane"
        and blocked_policy.question_target is not None
        and blocked_policy.question_target.field == "supported category",
        blocked_policy.model_dump(mode="json"),
    )

    resumed_state, resumed_diff = update_actual_dialogue_state(
        blocked_state,
        in_domain_output().model_copy(
            update={
                "utterance": "Actually, a tablet for note taking under $300.",
                "candidates": [],
            }
        ),
        [],
        turn_id="turn-3",
    )
    check(
        "later in-domain turn clears only the unsupported route",
        resumed_state.domain_route == "in_domain"
        and resumed_state.unsupported_category_text is None
        and resumed_state.category == initial.category,
        resumed_diff.changed_paths,
    )

    common = {
        "utterance": "I need a laptop.",
        "intents": ["unknown"],
        "domain_route": "unsupported_category",
        "unsupported_category_text": "laptop",
        "unsupported_category_evidence": "laptop",
        "facets": TabletDomainFacetCandidates().model_dump(mode="json"),
        "candidates": [],
        "supersedes": [],
        "residual_color_choice": False,
    }
    category_candidate = {
        "canonical_id": "category_tablet",
        "scope": None,
        "value_text": "tablet",
        "evidence_text": "tablet",
        "origin": "explicit",
        "confidence": 1.0,
    }
    expect_invalid(
        "v2 schema rejects category candidates",
        {
            **common,
            "intents": ["search"],
            "domain_route": "in_domain",
            "unsupported_category_text": None,
            "unsupported_category_evidence": None,
            "candidates": [category_candidate],
        },
    )
    expect_invalid(
        "unsupported route rejects tablet preference candidates",
        {
            **common,
            "candidates": [
                constraint(
                    "budget",
                    "$500 maximum",
                    "$500",
                    scope="hard",
                ).model_dump(mode="json")
            ],
        },
    )
    expect_invalid(
        "unsupported route rejects supersedes mutations",
        {**common, "supersedes": ["battery"]},
    )

    manifest = json.loads(FREEZE_MANIFEST.read_text(encoding="utf-8"))
    check(
        "v2 freeze precedes untouched holdout authoring",
        manifest["status"] == "frozen_before_untouched_holdout_authoring",
    )
    check(
        "frozen dataset hash still matches",
        sha256(DEV_DATASET)
        == manifest["selected_development_run"]["dataset_sha256"],
    )
    check(
        "frozen semantic manifest still matches",
        sha256(SEMANTIC_MANIFEST)
        == manifest["frozen_runtime"]["review_retrieval"][
            "tracked_semantic_manifest_sha256"
        ],
    )
    for relative, expected_hash in manifest["frozen_runtime"][
        "source_sha256"
    ].items():
        check(
            f"frozen source hash {relative}",
            sha256(BACKEND_ROOT / relative) == expected_hash,
        )

    print("Tablet-domain v2 deterministic verification completed.")


if __name__ == "__main__":
    main()
