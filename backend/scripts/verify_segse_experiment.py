"""Offline verification for the isolated SEGSE-lite development contract."""

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

from app.llm.json_utils import to_strict_json_schema  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.segse_experiment import (  # noqa: E402
    SEGSEProposalOutput,
    SEGSEStateEvent,
    SEGSEUnderstandingProvider,
    authorize_segse_events,
    compact_segse_previous_state,
    update_segse_dialogue_state,
)

DEV_FIXTURE = BACKEND_ROOT / "data" / "tablet_domain_segse_dev_v1.json"
DEV_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_dev_protocol_v1.json"
)
FROZEN_UTTERANCE_FILES = (
    BACKEND_ROOT / "data" / "tablet_domain_understanding_dev_v1.json",
    BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json",
    BACKEND_ROOT / "data" / "tablet_domain_m0_c_confirmatory_holdout_v1.json",
)


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def summary_from_state(state: Any) -> dict[str, Any]:
    return {
        "category": state.category.model_dump(mode="json") if state.category else None,
        "hard_constraints": {
            key: value.model_dump(mode="json")
            for key, value in state.hard_constraints.items()
        },
        "soft_constraints": {
            key: value.model_dump(mode="json")
            for key, value in state.soft_constraints.items()
        },
        "subjective_needs": state.subjective_needs.model_dump(mode="json"),
        "current_item_rank_context": state.current_item,
        "visible_ranked_products": [],
    }


def event(
    canonical_id: str,
    act: str,
    evidence: str,
    *,
    scope: str | None = None,
    value: str | None = None,
    relation: str | None = None,
    source: str = "current_utterance",
    source_ref: str | None = None,
    origin: str = "explicit",
) -> SEGSEStateEvent:
    return SEGSEStateEvent.model_validate(
        {
            "canonical_id": canonical_id,
            "act": act,
            "scope_after": scope,
            "value_after": value,
            "relation": relation,
            "trigger_evidence_text": evidence,
            "value_source": source,
            "source_ref": source_ref,
            "origin": origin,
            "confidence": 1.0,
        }
    )


def proposal(
    utterance: str,
    *events: SEGSEStateEvent,
    tradeoff: dict[str, Any] | None = None,
) -> SEGSEProposalOutput:
    return SEGSEProposalOutput.model_validate(
        {
            "utterance": utterance,
            "domain_route": "in_domain",
            "unsupported_category_text": None,
            "unsupported_category_evidence": None,
            "intents": ["refine"],
            "state_events": [item.model_dump(mode="python") for item in events],
            "item_action": None,
            "tradeoff": tradeoff,
        }
    )


class FakeClient:
    def __init__(self, output: SEGSEProposalOutput) -> None:
        self.output = output
        self.last_messages = None
        self.last_model = None

    async def generate_structured(self, **kwargs: Any) -> SEGSEProposalOutput:
        self.last_messages = kwargs["messages"]
        self.last_model = kwargs["response_model"]
        return self.output


async def adapt(
    output: SEGSEProposalOutput,
    state: Any,
) -> Any:
    return await SEGSEUnderstandingProvider(FakeClient(output))(
        utterance=output.utterance,
        previous_state_summary=summary_from_state(state),
        conversation_id="segse-offline",
        turn=len(state.preference_history) + 1,
    )


def verify_authorization() -> None:
    empty = summary_from_state(create_tablet_environment_state())
    valid = proposal(
        "I need at least 128 GB storage.",
        event(
            "storage_capacity",
            "assert",
            "128 GB storage",
            scope="hard",
            value="at least 128 GB storage",
            relation="require",
        ),
    )
    accepted, rejected = authorize_segse_events(
        valid,
        current_utterance=valid.utterance,
        previous_state_summary=empty,
    )
    check("valid current-grounded assertion authorized", len(accepted) == 1 and not rejected)

    missing_evidence = proposal(
        "My budget is $300.",
        event(
            "battery",
            "assert",
            "battery life",
            scope="soft",
            value="battery life matters",
            relation="prefer",
        ),
    )
    _, rejected = authorize_segse_events(
        missing_evidence,
        current_utterance=missing_evidence.utterance,
        previous_state_summary=empty,
    )
    check(
        "previous-state copy without current evidence rejected",
        rejected[0].reason == "trigger_evidence_not_exact_current_substring",
    )

    negated = proposal(
        "I don't want Windows.",
        event(
            "operating_system",
            "assert",
            "don't want Windows",
            scope="hard",
            value="Windows required",
            relation="require",
        ),
    )
    _, rejected = authorize_segse_events(
        negated,
        current_utterance=negated.utterance,
        previous_state_summary=empty,
    )
    check(
        "negated OS cannot become positive requirement",
        rejected[0].reason == "negated_phrase_cannot_authorize_positive_event",
    )

    cherry_picked_negation = proposal(
        "Windows is the one system I do not want.",
        event(
            "operating_system",
            "assert",
            "Windows",
            scope="hard",
            value="Windows required",
            relation="require",
        ),
    )
    _, rejected = authorize_segse_events(
        cherry_picked_negation,
        current_utterance=cherry_picked_negation.utterance,
        previous_state_summary=empty,
    )
    check(
        "OS negation cannot be bypassed with a cherry-picked noun span",
        rejected[0].reason == "negated_phrase_cannot_authorize_positive_event",
    )

    wrong_scope = proposal(
        "Battery life matters.",
        event(
            "battery",
            "assert",
            "Battery life",
            scope="hard",
            value="battery hard minimum",
            relation="require",
        ),
    )
    _, rejected = authorize_segse_events(
        wrong_scope,
        current_utterance=wrong_scope.utterance,
        previous_state_summary=empty,
    )
    check(
        "canonical scope compatibility enforced",
        rejected[0].reason == "assert_scope_is_illegal_for_canonical_id",
    )


def _collect_utterances(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "utterance" and isinstance(child, str):
                found.add(child.casefold().strip())
            else:
                found.update(_collect_utterances(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_utterances(child))
    return found


def verify_dev_fixture() -> None:
    dataset = json.loads(DEV_FIXTURE.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    check("fixture is explicitly development-only", dataset["split"] == "development")
    check("24 paired development cases", len(cases) == 24)
    check(
        "stable development case IDs",
        [item["id"] for item in cases] == [f"sg{number:02d}" for number in range(1, 25)],
    )
    family_counts: dict[str, int] = {}
    for item in cases:
        family_counts[item["family"]] = family_counts.get(item["family"], 0) + 1
        gold_ids = [event["canonical_id"] for event in item["gold_events"]]
        check(
            f"{item['id']} gold and forbidden IDs do not overlap",
            not (set(gold_ids) & set(item["forbidden_event_ids"])),
        )
        if item["gold_route"] != "in_domain":
            check(f"{item['id']} unsupported route has no events", not item["gold_events"])
            continue
        previous = {
            "hard_constraints": {},
            "soft_constraints": {},
            "subjective_needs": {facet: None for facet in (
                "subjective_property", "event", "activity", "goal_purpose", "goal_audience"
            )},
        }
        for fact in item["prior_facts"]:
            payload = {
                **fact,
                "confidence": 1.0,
                "evidence_turn_ids": ["prior"],
                "updated_at_turn_id": "prior",
            }
            scope = payload.pop("scope")
            if scope in {"hard", "soft"}:
                previous[f"{scope}_constraints"][fact["canonical_id"]] = payload
            else:
                facet = next(
                    name
                    for name, prefix in (
                        ("event", "event_"),
                        ("activity", "activity_"),
                        ("goal_purpose", "goal_"),
                        ("goal_audience", "audience_"),
                    )
                    if fact["canonical_id"].startswith(prefix)
                )
                previous["subjective_needs"][facet] = payload
        gold_events = [
            SEGSEStateEvent.model_validate(
                {
                    "canonical_id": gold["canonical_id"],
                    "act": gold["act"],
                    "scope_after": gold["scope_after"],
                    "value_after": gold["value_after"],
                    "relation": gold["relation"],
                    "trigger_evidence_text": gold["evidence_text"],
                    "value_source": gold["value_source"],
                    "source_ref": gold["source_ref"],
                    "origin": "explicit",
                    "confidence": 1.0,
                }
            )
            for gold in item["gold_events"]
        ]
        current = proposal(item["utterance"], *gold_events)
        accepted, rejected = authorize_segse_events(
            current,
            current_utterance=item["utterance"],
            previous_state_summary=previous,
        )
        check(
            f"{item['id']} gold events satisfy frozen authorization contract",
            len(accepted) == len(gold_events) and not rejected,
            [item.reason for item in rejected],
        )
    check(
        "12 contrast families with two cases each",
        len(family_counts) == 12 and set(family_counts.values()) == {2},
        family_counts,
    )
    new_utterances = {item["utterance"].casefold().strip() for item in cases}
    frozen_utterances: set[str] = set()
    for path in FROZEN_UTTERANCE_FILES:
        frozen_utterances.update(
            _collect_utterances(json.loads(path.read_text(encoding="utf-8")))
        )
    check("no exact frozen-fixture utterance reuse", not (new_utterances & frozen_utterances))


def verify_frozen_dev_contract() -> None:
    manifest = json.loads(DEV_MANIFEST.read_text(encoding="utf-8"))
    check(
        "development contract frozen before first live run",
        manifest["status"] == "development_contract_frozen_before_first_live_run"
        and not manifest["live_run_completed"]
        and not manifest["confirmatory_evidence"],
    )
    check(
        "manifest fixes development scope",
        manifest["development_case_count"] == 24
        and manifest["contrast_family_count"] == 12,
    )
    for relative, expected in manifest[
        "frozen_sources_sha256_lf_normalized"
    ].items():
        content = (BACKEND_ROOT / relative).read_text(encoding="utf-8")
        normalized = content.replace("\r\n", "\n").replace("\r", "\n")
        actual = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        check(f"frozen SEGSE source hash: {relative}", actual == expected)


async def verify_provider_and_merge() -> None:
    state = create_tablet_environment_state()
    add_budget = proposal(
        "My ceiling is $300.",
        event(
            "budget",
            "assert",
            "$300",
            scope="hard",
            value="$300 maximum",
            relation="require",
        ),
    )
    understanding = await adapt(add_budget, state)
    check(
        "state_events are sole candidate authority",
        [item.canonical_id for item in understanding.candidates] == ["budget"]
        and understanding.facets.model_dump(exclude_none=True) == {},
    )
    state, diff = update_segse_dialogue_state(
        state, understanding, [], turn_id="turn-1"
    )
    check(
        "manager derives add from assertion",
        understanding.segse_material_operations[0].operation == "add"
        and "hard_constraints.budget" in diff.changed_paths,
    )

    add_battery = proposal(
        "Battery life matters.",
        event(
            "battery",
            "assert",
            "Battery life",
            scope="soft",
            value="battery life matters",
            relation="prefer",
        ),
    )
    battery_understanding = await adapt(add_battery, state)
    state, _ = update_segse_dialogue_state(
        state, battery_understanding, [], turn_id="turn-2"
    )

    keep_budget = proposal(
        "Keep the same budget.",
        event(
            "budget",
            "confirm",
            "Keep the same budget",
            source="prior_state_reference",
            source_ref="budget",
        ),
    )
    keep_understanding = await adapt(keep_budget, state)
    check(
        "confirmation is authorized but not promoted to candidate",
        [item.act for item in keep_understanding.segse_authorized_events]
        == ["confirm"]
        and not keep_understanding.candidates,
    )
    state, keep_diff = update_segse_dialogue_state(
        state, keep_understanding, [], turn_id="turn-3"
    )
    check(
        "confirmation records support outside semantic diff",
        "hard_constraints.budget" not in keep_diff.changed_paths
        and keep_understanding.segse_metadata_deltas[0].changes
        == ["support_added"]
        and "turn-3" in state.hard_constraints["budget"].evidence_turn_ids,
    )

    correction = proposal(
        "Make the ceiling $350 instead.",
        event(
            "budget",
            "assert",
            "$350",
            scope="hard",
            value="$350 maximum",
            relation="require",
        ),
    )
    correction_understanding = await adapt(correction, state)
    state, correction_diff = update_segse_dialogue_state(
        state, correction_understanding, [], turn_id="turn-4"
    )
    check(
        "hard correction survives no-op defense",
        correction_understanding.segse_material_operations[0].operation
        == "update_value"
        and "hard_constraints.budget" in correction_diff.changed_paths
        and state.hard_constraints["budget"].value_text == "$350 maximum",
    )

    repeated_battery = proposal(
        "Battery still matters.",
        event(
            "battery",
            "assert",
            "Battery still matters",
            scope="soft",
            value="battery remains important",
            relation="prefer",
        ),
    )
    repeated_understanding = await adapt(repeated_battery, state)
    state, repeated_diff = update_segse_dialogue_state(
        state, repeated_understanding, [], turn_id="turn-5"
    )
    check(
        "C suppresses same-ID qualitative assertion",
        "battery" in repeated_understanding.segse_semantic_noop_ids
        and "soft_constraints.battery" not in repeated_diff.changed_paths,
    )

    refine_battery = proposal(
        "Battery during gaming sessions matters most.",
        event(
            "battery",
            "refine",
            "Battery during gaming sessions matters most",
            scope="soft",
            value="battery endurance during gaming sessions matters most",
            relation="prefer",
            source_ref="battery",
        ),
    )
    refine_understanding = await adapt(refine_battery, state)
    state, refine_diff = update_segse_dialogue_state(
        state, refine_understanding, [], turn_id="turn-6"
    )
    check(
        "fresh evidence can authorize qualitative refinement",
        refine_understanding.segse_material_operations[0].operation == "refine"
        and "soft_constraints.battery" in refine_diff.changed_paths,
    )

    retract = proposal(
        "I don't care about battery anymore.",
        event(
            "battery",
            "retract",
            "don't care about battery anymore",
            source="prior_state_reference",
            source_ref="battery",
        ),
    )
    retract_understanding = await adapt(retract, state)
    state, retract_diff = update_segse_dialogue_state(
        state, retract_understanding, [], turn_id="turn-7"
    )
    check(
        "retraction creates inactive tombstone",
        retract_understanding.segse_material_operations[0].operation == "retract"
        and state.soft_constraints["battery"].status == "superseded"
        and "soft_constraints.battery" in retract_diff.changed_paths,
    )

    reactivate = proposal(
        "Actually, battery life matters again.",
        event(
            "battery",
            "assert",
            "battery life matters again",
            scope="soft",
            value="battery life matters again",
            relation="prefer",
        ),
    )
    reactivate_understanding = await adapt(reactivate, state)
    state, _ = update_segse_dialogue_state(
        state, reactivate_understanding, [], turn_id="turn-8"
    )
    check(
        "reactivation requires fresh current evidence",
        reactivate_understanding.segse_material_operations[0].operation
        == "reactivate"
        and state.soft_constraints["battery"].status == "confirmed",
    )


async def verify_compact_context() -> None:
    state = create_tablet_environment_state()
    add_battery = proposal(
        "Battery life matters.",
        event(
            "battery",
            "assert",
            "Battery life",
            scope="soft",
            value="battery life matters",
            relation="prefer",
        ),
    )
    understanding = await adapt(add_battery, state)
    state, _ = update_segse_dialogue_state(
        state, understanding, [], turn_id="context-1"
    )
    compact = compact_segse_previous_state(summary_from_state(state))
    serialized = json.dumps(compact)
    check("compact context omits evidence history", "evidence_turn_ids" not in serialized)
    check(
        "compact context uses qualitative IDs without value dump",
        compact["active_qualitative_ids_for_reference"]
        == [{"canonical_id": "battery", "scope": "soft", "status": "confirmed"}]
        and "battery life matters" not in serialized,
    )


async def verify_scope_and_decision_metadata() -> None:
    state = create_tablet_environment_state()
    inferred = proposal(
        "Battery performance may matter for this choice.",
        event(
            "battery",
            "assert",
            "Battery performance may matter",
            scope="soft",
            value="battery may matter",
            relation="prefer",
            origin="inferred",
        ),
    )
    inferred_understanding = await adapt(inferred, state)
    state, _ = update_segse_dialogue_state(
        state, inferred_understanding, [], turn_id="metadata-1"
    )
    check(
        "inferred assertion remains decision-ineligible",
        state.soft_constraints["battery"].status == "unconfirmed",
    )
    confirm = proposal(
        "Yes, battery performance is definitely important.",
        event(
            "battery",
            "confirm",
            "battery performance is definitely important",
            source="prior_state_reference",
            source_ref="battery",
        ),
    )
    confirm_understanding = await adapt(confirm, state)
    state, confirm_diff = update_segse_dialogue_state(
        state, confirm_understanding, [], turn_id="metadata-2"
    )
    metadata = confirm_understanding.segse_metadata_deltas[0]
    check(
        "confirmation exposes decision-eligibility metadata delta",
        metadata.decision_eligibility_changed
        and "status_confirmed" in metadata.changes
        and "provenance_promoted" in metadata.changes
        and not confirm_diff.changed_paths
        and state.soft_constraints["battery"].status == "confirmed",
    )

    soft_display = proposal(
        "A roomy display would be pleasant.",
        event(
            "display",
            "assert",
            "roomy display",
            scope="soft",
            value="large display preferred",
            relation="prefer",
        ),
    )
    soft_understanding = await adapt(soft_display, state)
    state, _ = update_segse_dialogue_state(
        state, soft_understanding, [], turn_id="scope-1"
    )
    hard_display = proposal(
        "Make 11 inches a strict screen minimum.",
        event(
            "display",
            "assert",
            "11 inches a strict screen minimum",
            scope="hard",
            value="at least 11 inch screen",
            relation="require",
        ),
    )
    hard_understanding = await adapt(hard_display, state)
    state, hard_diff = update_segse_dialogue_state(
        state, hard_understanding, [], turn_id="scope-2"
    )
    check(
        "scope update removes old record before adding new record",
        hard_understanding.segse_material_operations[0].operation == "update_scope"
        and "display" not in state.soft_constraints
        and "display" in state.hard_constraints
        and {
            "soft_constraints.display",
            "hard_constraints.display",
        }
        <= set(hard_diff.changed_paths),
    )


async def main() -> None:
    strict = to_strict_json_schema(SEGSEProposalOutput)
    check("SEGSE strict schema builds", bool(strict))
    verify_frozen_dev_contract()
    verify_dev_fixture()
    verify_authorization()
    await verify_provider_and_merge()
    await verify_compact_context()
    await verify_scope_and_decision_metadata()
    print("SEGSE-lite offline verification completed.")


if __name__ == "__main__":
    asyncio.run(main())
