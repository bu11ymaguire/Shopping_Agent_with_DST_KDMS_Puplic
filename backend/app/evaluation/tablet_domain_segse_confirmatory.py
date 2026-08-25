"""Schema, loader, and gold self-consistency checks for the SEGSE confirmatory holdout.

Twenty four-turn episodes over the same frozen tablet catalog and the same review
corpus the official holdout used. Gold carries typed current-turn state events,
which the older 20-episode holdouts do not: their `gold_state_diff` collapses
every mutation into `upsert:<id>`, so a value correction and a no-op are the same
string there and correction recall cannot be defined.

Every turn declares the transition families it exercises, so coverage is checked
by code rather than asserted in prose. Declared downstream effects are re-derived
from the deterministic Query Generator and Policy, so a mis-authored gold fails
before the first system run.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.tablet_domain_segse import (
    active_final_tokens,
    expected_state,
    gold_material_operations,
)
from app.models.actual_demo import (
    ACTUAL_PREFERENCE_IDS,
    ActualHardFilters,
    TabletDomainCanonicalId,
)
from app.models.understanding import IntentName
from app.nodes.actual_policy import select_actual_policy
from app.nodes.actual_recommendation import generate_actual_query
from app.nodes.actual_state_manager import create_tablet_environment_state

TransitionFamily = Literal[
    "value_correction_hard_numeric",
    "scope_correction_hard_to_soft",
    "scope_correction_soft_to_hard",
    "retract_hard_fact",
    "retract_soft_fact",
    "reactivation_after_retract",
    "confirmation_no_material_change",
    "unchanged_requirement_zero_event",
    "dimension_attribution_negative",
    "negative_polarity",
    "tradeoff_compromised_side",
    "compound_two_or_more_facts",
    "relaxation_on_hard_only_id_gold_is_retract",
    "initial_acquisition",
]

DimensionConfusionFamily = Literal[
    "expandable storage",
    "external accessories",
    "display feature",
    "connectivity feature",
    "port or charging feature",
    "stylus accessory",
]

#: Minimum turns per family, frozen in the confirmatory protocol manifest.
COVERAGE_MINIMUMS: dict[str, int] = {
    "value_correction_hard_numeric": 6,
    "scope_correction_hard_to_soft": 4,
    "scope_correction_soft_to_hard": 4,
    "retract_hard_fact": 4,
    "retract_soft_fact": 3,
    "reactivation_after_retract": 3,
    "confirmation_no_material_change": 4,
    "unchanged_requirement_zero_event": 6,
    "dimension_attribution_negative": 6,
    "negative_polarity": 3,
    "tradeoff_compromised_side": 3,
    "compound_two_or_more_facts": 6,
    "relaxation_on_hard_only_id_gold_is_retract": 3,
}


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SEGSEConfirmatoryGoldEvent(_Contract):
    canonical_id: TabletDomainCanonicalId
    act: Literal["assert", "confirm", "retract", "refine"]
    scope_after: Literal["hard", "soft"] | None = None
    value_after: str | None = None
    evidence_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def event_contract(self) -> "SEGSEConfirmatoryGoldEvent":
        if self.act in {"confirm", "retract"}:
            if self.scope_after is not None or self.value_after is not None:
                raise ValueError("confirm/retract cannot carry a replacement state")
        elif self.value_after is None:
            raise ValueError("assert/refine require value_after")
        is_preference = self.canonical_id in ACTUAL_PREFERENCE_IDS
        if self.act in {"assert", "refine"} and is_preference != (
            self.scope_after is not None
        ):
            raise ValueError(
                "preference IDs require scope; facet IDs require null scope"
            )
        return self


class SEGSEConfirmatoryTurn(_Contract):
    turn: int = Field(ge=1, le=4)
    utterance: str = Field(min_length=1)
    gold_route: Literal["in_domain", "unsupported_category"]
    gold_intents: list[IntentName] = Field(min_length=1)
    gold_events: list[SEGSEConfirmatoryGoldEvent]
    forbidden_event_ids: list[TabletDomainCanonicalId] = Field(default_factory=list)
    families: list[TransitionFamily] = Field(min_length=1)
    dimension_confusion_family: DimensionConfusionFamily | None = None
    gold_policy_lane: Literal["clarify-lane", "recommend-lane"]
    gold_question_target: str | None = None
    expected_hard_filters_after_turn: ActualHardFilters

    @model_validator(mode="after")
    def turn_contract(self) -> "SEGSEConfirmatoryTurn":
        pairs = [(item.canonical_id, item.act) for item in self.gold_events]
        if len(pairs) != len(set(pairs)):
            raise ValueError("gold event pairs must be unique within a turn")
        gold_ids = {item.canonical_id for item in self.gold_events}
        if gold_ids & set(self.forbidden_event_ids):
            raise ValueError("gold and forbidden IDs must not overlap")
        if len(self.families) != len(set(self.families)):
            raise ValueError("families must not repeat within a turn")
        if self.gold_policy_lane == "clarify-lane" and not self.gold_question_target:
            raise ValueError("clarify lane requires a question target")
        if self.gold_policy_lane == "recommend-lane" and self.gold_question_target:
            raise ValueError("recommend lane cannot have a question target")
        for item in self.gold_events:
            if item.evidence_text not in self.utterance:
                raise ValueError(
                    f"gold evidence must be an exact current-turn substring: "
                    f"{item.evidence_text!r}"
                )
        if ("dimension_attribution_negative" in self.families) != (
            self.dimension_confusion_family is not None
        ):
            raise ValueError(
                "dimension-attribution turns declare exactly one confusion family"
            )
        if "dimension_attribution_negative" in self.families and not (
            self.forbidden_event_ids
        ):
            raise ValueError(
                "a dimension-attribution negative must list the plausible wrong IDs"
            )
        if "unchanged_requirement_zero_event" in self.families and self.gold_events:
            raise ValueError("an unchanged-requirement turn has no gold events")
        if "compound_two_or_more_facts" in self.families and len(self.gold_events) < 2:
            raise ValueError("a compound turn needs at least two gold events")
        return self

    def as_case(self, scenario_id: str) -> dict[str, Any]:
        return {
            "id": f"{scenario_id}t{self.turn}",
            "family": scenario_id,
            "utterance": self.utterance,
            "gold_route": self.gold_route,
            "gold_events": [item.model_dump(mode="json") for item in self.gold_events],
            "forbidden_event_ids": list(self.forbidden_event_ids),
        }


class SEGSEConfirmatoryScenario(_Contract):
    id: str = Field(pattern=r"^sc\d{2}$")
    title: str = Field(min_length=1)
    tags: list[str] = Field(min_length=2)
    turns: list[SEGSEConfirmatoryTurn] = Field(min_length=4, max_length=4)
    final_gold_active_tokens: list[str]

    @model_validator(mode="after")
    def scenario_contract(self) -> "SEGSEConfirmatoryScenario":
        if [item.turn for item in self.turns] != [1, 2, 3, 4]:
            raise ValueError("scenario turns must be contiguous from one")
        if len(self.final_gold_active_tokens) != len(
            set(self.final_gold_active_tokens)
        ):
            raise ValueError("final gold tokens must be unique")
        declared = {
            family
            for turn in self.turns
            for family in turn.families
            if family != "initial_acquisition"
        }
        if len(declared) < 2:
            raise ValueError(
                "every episode must exercise at least two distinct transition families"
            )
        return self


class SEGSEConfirmatoryDataset(_Contract):
    dataset_version: Literal["tablet-domain-segse-confirmatory-holdout-v1.0"]
    split: Literal["untouched_holdout"]
    status: Literal["frozen_before_first_system_run"]
    schema_version: Literal["tablet-domain-segse-confirmatory-holdout-v1"]
    created_after_freeze_tag: Literal["segse-confirmatory-v1-protocol-freeze"]
    catalog_scope: Literal["same frozen tablet catalog and review corpus as the official holdout"]
    design_note: str = Field(min_length=1)
    scenarios: list[SEGSEConfirmatoryScenario] = Field(min_length=20, max_length=20)

    @model_validator(mode="after")
    def dataset_contract(self) -> "SEGSEConfirmatoryDataset":
        ids = [item.id for item in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("scenario IDs must be unique")
        if ids != sorted(ids):
            raise ValueError("scenario IDs must be ordered")
        utterances = [
            turn.utterance.casefold().strip()
            for scenario in self.scenarios
            for turn in scenario.turns
        ]
        if len(utterances) != len(set(utterances)):
            raise ValueError("utterances must be unique across the holdout")
        return self


def load_segse_confirmatory_dataset(path: Path | str) -> SEGSEConfirmatoryDataset:
    return SEGSEConfirmatoryDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def coverage_counts(dataset: SEGSEConfirmatoryDataset) -> dict[str, int]:
    counts: dict[str, int] = {family: 0 for family in COVERAGE_MINIMUMS}
    for scenario in dataset.scenarios:
        for turn in scenario.turns:
            for family in turn.families:
                if family in counts:
                    counts[family] += 1
    return counts


def coverage_gaps(dataset: SEGSEConfirmatoryDataset) -> dict[str, dict[str, int]]:
    counts = coverage_counts(dataset)
    return {
        family: {"required": minimum, "actual": counts[family]}
        for family, minimum in COVERAGE_MINIMUMS.items()
        if counts[family] < minimum
    }


def dimension_confusion_families(dataset: SEGSEConfirmatoryDataset) -> dict[str, int]:
    counts: dict[str, int] = {}
    for scenario in dataset.scenarios:
        for turn in scenario.turns:
            if turn.dimension_confusion_family is None:
                continue
            counts[turn.dimension_confusion_family] = (
                counts.get(turn.dimension_confusion_family, 0) + 1
            )
    return counts


def gold_trajectory_problems(dataset: SEGSEConfirmatoryDataset) -> list[str]:
    """Re-derive every declared downstream effect from deterministic code."""

    problems: list[str] = []
    for scenario in dataset.scenarios:
        gold_state = create_tablet_environment_state()
        for gold_turn in scenario.turns:
            label = f"{scenario.id}t{gold_turn.turn}"
            case = gold_turn.as_case(scenario.id)
            operations = gold_material_operations(case, gold_state)
            gold_state = expected_state(case, gold_state)

            actual_filters = generate_actual_query(gold_state).hard_filters
            if actual_filters != gold_turn.expected_hard_filters_after_turn:
                problems.append(
                    f"{label} hard filters: declared "
                    f"{gold_turn.expected_hard_filters_after_turn.model_dump(exclude_none=True)} "
                    f"but deterministic query gives "
                    f"{actual_filters.model_dump(exclude_none=True)}"
                )

            policy = select_actual_policy(gold_state)
            question = policy.question_target.field if policy.question_target else None
            if policy.lane != gold_turn.gold_policy_lane:
                problems.append(
                    f"{label} policy lane: declared {gold_turn.gold_policy_lane} "
                    f"but deterministic policy gives {policy.lane}"
                )
            if question != gold_turn.gold_question_target:
                problems.append(
                    f"{label} question target: declared "
                    f"{gold_turn.gold_question_target!r} but deterministic policy "
                    f"gives {question!r}"
                )

            problems.extend(
                _family_problems(label, gold_turn, operations)
            )
        final_tokens = active_final_tokens(gold_state)
        if final_tokens != set(scenario.final_gold_active_tokens):
            problems.append(
                f"{scenario.id} final tokens: declared "
                f"{sorted(scenario.final_gold_active_tokens)} but gold trajectory "
                f"gives {sorted(final_tokens)}"
            )
    return problems


def _family_problems(
    label: str,
    turn: SEGSEConfirmatoryTurn,
    operations: dict[str, str],
) -> Iterable[str]:
    """Check each declared family against the operation the gold actually implies."""

    expectations: dict[str, set[str]] = {
        "value_correction_hard_numeric": {"update_value"},
        "scope_correction_hard_to_soft": {"update_scope"},
        "scope_correction_soft_to_hard": {"update_scope"},
        "retract_hard_fact": {"retract"},
        "retract_soft_fact": {"retract"},
        "reactivation_after_retract": {"reactivate"},
        "initial_acquisition": {"add"},
    }
    produced = set(operations.values())
    for family, required in expectations.items():
        if family in turn.families and not (produced & required):
            yield (
                f"{label} declares {family} but the gold transition produces "
                f"{sorted(produced) or 'no material operation'}"
            )
    if "confirmation_no_material_change" in turn.families:
        if not any(item.act == "confirm" for item in turn.gold_events):
            yield f"{label} declares a confirmation but has no confirm event"
        if produced:
            yield (
                f"{label} declares a confirmation with no material change but "
                f"produces {sorted(produced)}"
            )
    if "unchanged_requirement_zero_event" in turn.families and produced:
        yield f"{label} declares an unchanged turn but produces {sorted(produced)}"
    if "relaxation_on_hard_only_id_gold_is_retract" in turn.families:
        if "retract" not in produced:
            yield (
                f"{label} declares a hard-only relaxation whose gold must be a "
                f"retract, but produces {sorted(produced) or 'nothing'}"
            )


def verify_gold_trajectory(dataset: SEGSEConfirmatoryDataset) -> None:
    problems = gold_trajectory_problems(dataset)
    if problems:
        raise ValueError(
            "gold is inconsistent with deterministic code:\n  "
            + "\n  ".join(problems)
        )


__all__ = [
    "COVERAGE_MINIMUMS",
    "SEGSEConfirmatoryDataset",
    "SEGSEConfirmatoryGoldEvent",
    "SEGSEConfirmatoryScenario",
    "SEGSEConfirmatoryTurn",
    "coverage_counts",
    "coverage_gaps",
    "dimension_confusion_families",
    "gold_trajectory_problems",
    "load_segse_confirmatory_dataset",
    "verify_gold_trajectory",
]
