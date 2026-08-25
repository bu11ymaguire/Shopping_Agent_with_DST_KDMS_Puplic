"""Untouched multi-turn holdout contracts for frozen tablet-domain v2.3."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.actual_understanding import (
    ActualGoldItemAction,
    ActualGoldTradeoff,
)
from app.models.actual_demo import (
    ACTUAL_FACET_IDS,
    ACTUAL_PREFERENCE_IDS,
    ActualCanonicalId,
    ActualHardFilters,
)
from app.models.understanding import IntentName


class TabletHoldoutContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


GoldScope = Literal["facet", "hard", "soft"]


class TabletHoldoutTurn(TabletHoldoutContract):
    turn: int = Field(ge=1, le=6)
    utterance: str = Field(min_length=1)
    gold_domain_route: Literal["in_domain", "unsupported_category"]
    gold_intents: list[IntentName] = Field(min_length=1)
    gold_candidate_ids: list[ActualCanonicalId]
    gold_candidate_scopes: dict[str, GoldScope]
    forbidden_candidate_ids: list[ActualCanonicalId] = Field(default_factory=list)
    gold_item_action: ActualGoldItemAction | None = None
    gold_tradeoff: ActualGoldTradeoff | None = None
    gold_state_diff: list[str]
    gold_policy_lane: Literal["clarify-lane", "recommend-lane"]
    gold_question_target: str | None = None
    expected_hard_filters_after_turn: ActualHardFilters

    @model_validator(mode="after")
    def turn_contracts(self) -> TabletHoldoutTurn:
        ids = list(self.gold_candidate_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("gold candidate IDs must be unique per turn")
        if set(self.gold_candidate_scopes) != set(ids):
            raise ValueError("gold_candidate_scopes must cover exactly the gold IDs")
        if set(ids) & set(self.forbidden_candidate_ids):
            raise ValueError("gold and forbidden candidate IDs must not overlap")
        if "category_tablet" in ids:
            raise ValueError("tablet category is environment state, not a turn update")
        subjective = ACTUAL_FACET_IDS["subjective_property"]
        if set(ids) & subjective:
            raise ValueError("latent subjective IDs are outside the frozen v2 holdout")
        facet_ids = set().union(*ACTUAL_FACET_IDS.values())
        for canonical_id, scope in self.gold_candidate_scopes.items():
            if canonical_id in facet_ids and scope != "facet":
                raise ValueError("facet IDs require facet scope")
            if canonical_id in ACTUAL_PREFERENCE_IDS and scope == "facet":
                raise ValueError("preference IDs require hard or soft scope")
        if len(self.gold_intents) != len(set(self.gold_intents)):
            raise ValueError("gold intents must be unique")
        if len(self.gold_state_diff) != len(set(self.gold_state_diff)):
            raise ValueError("gold State Diff operations must be unique")
        clarify = self.gold_policy_lane == "clarify-lane"
        if clarify != (self.gold_question_target is not None):
            raise ValueError("clarify alone requires a question target")
        if self.gold_domain_route == "unsupported_category":
            if self.gold_intents != ["unknown"]:
                raise ValueError("unsupported turn must use unknown intent")
            if ids or self.gold_item_action or self.gold_tradeoff:
                raise ValueError("unsupported turn must not mutate tablet preferences")
            if self.gold_policy_lane != "clarify-lane":
                raise ValueError("unsupported turn must clarify")
        return self


class TabletHoldoutScenario(TabletHoldoutContract):
    id: str = Field(pattern=r"^th\d{2}$")
    title: str = Field(min_length=1)
    tags: list[str] = Field(min_length=2)
    turns: list[TabletHoldoutTurn] = Field(min_length=4, max_length=6)
    final_gold_state_ids: list[ActualCanonicalId]
    final_expected_hard_filters: ActualHardFilters

    @model_validator(mode="after")
    def scenario_contracts(self) -> TabletHoldoutScenario:
        if [turn.turn for turn in self.turns] != list(
            range(1, len(self.turns) + 1)
        ):
            raise ValueError("turn numbers must be contiguous from one")
        if len(self.final_gold_state_ids) != len(set(self.final_gold_state_ids)):
            raise ValueError("final gold state IDs must be unique")
        if "category_tablet" not in self.final_gold_state_ids:
            raise ValueError("final state must retain environment category")
        expected_final_ids = {
            "category_tablet",
            *(
                canonical_id
                for turn in self.turns
                for canonical_id in turn.gold_candidate_ids
            ),
        }
        if set(self.final_gold_state_ids) != expected_final_ids:
            raise ValueError(
                "final gold state IDs must equal environment plus turn updates"
            )
        if (
            self.turns[-1].expected_hard_filters_after_turn
            != self.final_expected_hard_filters
        ):
            raise ValueError("last-turn and final hard filters must match")
        return self


class TabletHoldoutDataset(TabletHoldoutContract):
    dataset_version: str
    split: Literal["untouched_holdout"]
    schema_version: Literal["tablet-domain-multiturn-holdout-v1"]
    frozen_prompt_version: Literal[
        "spn-understanding-tablet-domain-en-v2.3-frozen"
    ]
    created_after_freeze_tag: Literal["tablet-domain-v2.3-freeze"]
    frozen_before_first_run: Literal[True]
    design_note: str
    scenarios: list[TabletHoldoutScenario] = Field(min_length=20, max_length=20)

    @model_validator(mode="after")
    def dataset_contracts(self) -> TabletHoldoutDataset:
        ids = [scenario.id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("holdout scenario IDs must be unique")
        utterances = [
            turn.utterance.casefold().strip()
            for scenario in self.scenarios
            for turn in scenario.turns
        ]
        if len(utterances) != len(set(utterances)):
            raise ValueError("holdout utterances must be unique")
        return self


def load_tablet_holdout_dataset(path: Path | str) -> TabletHoldoutDataset:
    return TabletHoldoutDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
