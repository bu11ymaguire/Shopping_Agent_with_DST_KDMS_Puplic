"""Offline contract verification for the M0/A/B/C/D/E comparison framework."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_extended import (  # noqa: E402
    select_exploratory_strategy,
)
from app.extended_experiment import (  # noqa: E402
    ExtendedStateOperation,
    ExtendedStateSnapshotValue,
    ExtendedTabletDomainUnderstandingOutput,
    ExtendedUnderstandingProvider,
    FullStateOutput,
    SOMOperationOutput,
    SparseDeltaOutput,
    compact_previous_state_summary,
    update_extended_dialogue_state,
)
from app.llm.json_utils import to_strict_json_schema  # noqa: E402
from app.models.actual_demo import (  # noqa: E402
    TabletDomainFacetCandidates,
    TabletDomainStateUpdateCandidate,
    TabletDomainUnderstandingOutput,
)
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402

MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_extended_experiment_protocol_v1.json"
)


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def candidate(
    canonical_id: str,
    value_text: str,
    *,
    scope: str | None,
    evidence: str | None = None,
) -> TabletDomainStateUpdateCandidate:
    return TabletDomainStateUpdateCandidate.model_validate(
        {
            "canonical_id": canonical_id,
            "scope": scope,
            "value_text": value_text,
            "evidence_text": evidence or value_text,
            "origin": "explicit",
            "confidence": 1.0,
        }
    )


def standard(
    *candidates: TabletDomainStateUpdateCandidate,
) -> TabletDomainUnderstandingOutput:
    return TabletDomainUnderstandingOutput(
        utterance="test utterance",
        domain_route="in_domain",
        intents=["refine"],
        facets=TabletDomainFacetCandidates(),
        candidates=list(candidates),
        supersedes=[],
        residual_color_choice=False,
    )


def extended(
    strategy: str,
    *candidates: TabletDomainStateUpdateCandidate,
) -> ExtendedTabletDomainUnderstandingOutput:
    return ExtendedTabletDomainUnderstandingOutput(
        **standard(*candidates).model_dump(mode="python"),
        experimental_strategy=strategy,
    )


def previous_summary() -> dict[str, Any]:
    return {
        "category": {
            "canonical_id": "category_tablet",
            "value_text": "tablet shopping environment",
            "origin": "environment",
            "confidence": 1.0,
            "status": "confirmed",
            "evidence_turn_ids": [],
            "updated_at_turn_id": "environment-init",
        },
        "hard_constraints": {
            "budget": {
                "canonical_id": "budget",
                "value_text": "$275 maximum",
                "origin": "explicit",
                "confidence": 1.0,
                "status": "confirmed",
                "evidence_turn_ids": ["t1"],
                "updated_at_turn_id": "t1",
            }
        },
        "soft_constraints": {
            "battery": {
                "canonical_id": "battery",
                "value_text": "battery life matters",
                "origin": "explicit",
                "confidence": 1.0,
                "status": "confirmed",
                "evidence_turn_ids": ["t1"],
                "updated_at_turn_id": "t1",
            }
        },
        "subjective_needs": {
            "subjective_property": None,
            "event": None,
            "activity": None,
            "goal_purpose": None,
            "goal_audience": None,
        },
        "current_item_rank_context": None,
        "visible_ranked_products": [],
    }


class FakeClient:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.last_messages = None
        self.last_model = None

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.last_messages = kwargs["messages"]
        self.last_model = kwargs["response_model"]
        return self.payload


def verify_noop_gate() -> None:
    budget = candidate("budget", "$275 maximum", scope="hard")
    state, _ = update_extended_dialogue_state(
        create_tablet_environment_state(),
        extended("m0_baseline", budget),
        [],
        turn_id="t1",
    )
    echo = candidate("budget", "$275 maximum", scope="hard")
    storage = candidate("storage_capacity", "at least 128 GB storage", scope="hard")
    _, baseline_diff = update_extended_dialogue_state(
        state,
        extended("m0_baseline", echo, storage),
        [],
        turn_id="t2-baseline",
    )
    noop_state, noop_diff = update_extended_dialogue_state(
        state,
        extended("c_semantic_noop", echo, storage),
        [],
        turn_id="t2-noop",
    )
    check(
        "baseline counts repeated candidate as changed",
        "hard_constraints.budget" in baseline_diff.changed_paths,
    )
    check(
        "semantic no-op suppresses repeated material path",
        "hard_constraints.budget" not in noop_diff.changed_paths,
    )
    check(
        "semantic no-op retains genuine new update",
        "hard_constraints.storage_capacity" in noop_diff.changed_paths,
    )
    check(
        "semantic no-op does not refresh repeated provenance",
        noop_state.hard_constraints["budget"].updated_at_turn_id == "t1",
    )
    correction = candidate("budget", "$300 maximum", scope="hard")
    corrected, correction_diff = update_extended_dialogue_state(
        state,
        extended("c_semantic_noop", correction),
        [],
        turn_id="t3",
    )
    check(
        "semantic no-op preserves a numeric correction",
        "hard_constraints.budget" in correction_diff.changed_paths
        and corrected.hard_constraints["budget"].value_text == "$300 maximum",
    )


async def verify_adapters() -> None:
    summary = previous_summary()
    som = SOMOperationOutput(
        utterance="128 GB is the floor",
        domain_route="in_domain",
        intents=["refine"],
        supersedes=[],
        slot_operations=[
            ExtendedStateOperation(canonical_id="budget", operation="carryover"),
            ExtendedStateOperation(canonical_id="battery", operation="carryover"),
            ExtendedStateOperation(
                canonical_id="storage_capacity",
                operation="upsert",
                scope="hard",
                value_text="at least 128 GB storage",
                evidence_text="128 GB",
                origin="explicit",
                confidence=1.0,
            ),
        ],
    )
    som_result = await ExtendedUnderstandingProvider(
        FakeClient(som), "a_som_operation"
    )(
        utterance=som.utterance,
        previous_state_summary=summary,
        conversation_id="c",
        turn=2,
    )
    check(
        "SOM arm separates carryover from current candidate",
        [item.canonical_id for item in som_result.candidates]
        == ["storage_capacity"]
        and set(som_result.experimental_carryover_ids) == {"budget", "battery"},
    )
    check("SOM active-slot coverage contract", not som_result.experimental_contract_violations)

    sparse = SparseDeltaOutput(
        utterance=som.utterance,
        domain_route="in_domain",
        intents=["refine"],
        supersedes=[],
        state_operations=[som.slot_operations[-1]],
    )
    sparse_result = await ExtendedUnderstandingProvider(
        FakeClient(sparse), "b_sparse_delta"
    )(
        utterance=sparse.utterance,
        previous_state_summary=summary,
        conversation_id="c",
        turn=2,
    )
    check(
        "sparse arm emits only delta candidate",
        [item.canonical_id for item in sparse_result.candidates]
        == ["storage_capacity"],
    )

    full = FullStateOutput(
        utterance=som.utterance,
        domain_route="in_domain",
        intents=["refine"],
        supersedes=[],
        full_state=[
            ExtendedStateSnapshotValue(
                canonical_id="budget",
                scope="hard",
                value_text="$275 maximum",
                origin="explicit",
                confidence=1.0,
            ),
            ExtendedStateSnapshotValue(
                canonical_id="battery",
                scope="soft",
                value_text="battery life matters",
                origin="explicit",
                confidence=1.0,
            ),
            ExtendedStateSnapshotValue(
                canonical_id="storage_capacity",
                scope="hard",
                value_text="at least 128 GB storage",
                origin="explicit",
                confidence=1.0,
            ),
        ],
    )
    full_result = await ExtendedUnderstandingProvider(
        FakeClient(full), "d_full_state_diff"
    )(
        utterance=full.utterance,
        previous_state_summary=summary,
        conversation_id="c",
        turn=2,
    )
    check(
        "full-state arm derives only material delta",
        [item.canonical_id for item in full_result.candidates]
        == ["storage_capacity"]
        and not full_result.experimental_delete_ids,
    )

    ordinary = standard(
        candidate(
            "storage_capacity",
            "at least 128 GB storage",
            scope="hard",
        )
    )
    compact_client = FakeClient(ordinary)
    await ExtendedUnderstandingProvider(compact_client, "e_compact_context")(
        utterance=ordinary.utterance,
        previous_state_summary=summary,
        conversation_id="c",
        turn=2,
    )
    sent = json.loads(compact_client.last_messages[-1].content)
    compact = sent["reference_only_previous_state"]
    check("compact arm removes evidence/provenance dumps", "evidence_turn_ids" not in json.dumps(compact))
    check("compact arm keeps hard value for correction", compact["hard_constraints_for_correction"][0]["value_text"] == "$275 maximum")


def verify_selection() -> None:
    def metrics(diff: float, final: float, correction: float, hard: float, echo: int) -> dict[str, Any]:
        return {
            "state_diff_micro": {"f1": diff},
            "final_state_micro": {"f1": final},
            "hard_constraint_completion_rate": hard,
            "scenario_completion_rate": 1.0,
            "turn_output_completion_rate": 0.98,
            "extended_diagnostics": {
                "correction_candidate_recall": correction,
                "carryover_to_update_false_positive_count": echo,
                "understanding_latency_ms_median": 10.0,
            },
        }

    selected = select_exploratory_strategy(
        {
            "m0_baseline": metrics(0.56, 0.75, 1.0, 0.63, 20),
            "c_semantic_noop": metrics(0.72, 0.75, 1.0, 0.63, 20),
            "unsafe": metrics(0.90, 0.60, 0.5, 0.30, 0),
        }
    )
    check("retention gate rejects destructive apparent winner", not selected["gates"]["unsafe"]["passed"])
    check("lexicographic rule selects retained State Diff gain", selected["exploratory_selected_strategy"] == "c_semantic_noop")
    check("selection label remains post-hoc", selected["status"] == "exploratory_posthoc_only")


async def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check(
        "protocol frozen before live run",
        manifest["status"] == "protocol_frozen_before_extended_live_run",
    )
    check(
        "protocol fixes 486 Understanding calls",
        manifest["expected_understanding_call_count"] == 486,
    )
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        text = (BACKEND_ROOT / relative).read_text(encoding="utf-8").replace(
            "\r\n", "\n"
        )
        actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
        check(f"frozen source hash: {relative}", actual == expected)
    for model in (SOMOperationOutput, SparseDeltaOutput, FullStateOutput):
        strict = to_strict_json_schema(model)
        check(f"strict schema builds: {model.__name__}", bool(strict))
    compact = compact_previous_state_summary(previous_summary())
    check("compact summary has suppression IDs", compact["already_known_ids"] == ["battery"])
    verify_noop_gate()
    await verify_adapters()
    verify_selection()
    print("Extended experiment offline verification completed.")


if __name__ == "__main__":
    asyncio.run(main())
