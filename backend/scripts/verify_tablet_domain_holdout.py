"""Verify the untouched tablet-domain multi-turn holdout before its first run."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.poster_annotation import (  # noqa: E402
    load_poster_scenario_dataset,
)
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_understanding import (  # noqa: E402
    load_tablet_domain_dev_dataset,
)
from app.nodes.tablet_domain_understanding import (  # noqa: E402
    TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
)

HOLDOUT_PATH = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEV_PATH = BACKEND_ROOT / "data" / "tablet_domain_understanding_dev_v1.json"
V1_PATH = BACKEND_ROOT / "data" / "poster_recommendation_scenarios_v1.json"


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def main() -> None:
    holdout = load_tablet_holdout_dataset(HOLDOUT_PATH)
    dev = load_tablet_domain_dev_dataset(DEV_PATH)
    v1 = load_poster_scenario_dataset(V1_PATH)
    check("split is untouched holdout", holdout.split == "untouched_holdout")
    check("holdout is frozen before first run", holdout.frozen_before_first_run)
    check(
        "holdout follows the frozen prompt",
        holdout.frozen_prompt_version == TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
    )
    check("exactly 20 scenarios", len(holdout.scenarios) == 20)
    turn_count = sum(len(scenario.turns) for scenario in holdout.scenarios)
    check("poster-scale turn count", 80 <= turn_count <= 120, turn_count)

    required_tags = {
        "unsupported-category",
        "recovery",
        "negation",
        "unsupported-field",
        "correction",
        "contradiction",
        "tradeoff",
        "reject",
        "inspect",
        "compare",
        "purchase",
        "rank-reference",
        "ram",
        "storage",
    }
    tags = {tag for scenario in holdout.scenarios for tag in scenario.tags}
    check("planned multi-turn phenomena are covered", required_tags <= tags)

    holdout_utterances = {
        turn.utterance.casefold().strip()
        for scenario in holdout.scenarios
        for turn in scenario.turns
    }
    dev_utterances = {case.utterance.casefold().strip() for case in dev.cases}
    v1_utterances = {
        utterance.casefold().strip()
        for scenario in v1.scenarios
        for utterance in scenario.conversation_turns
    }
    check(
        "no exact dev utterance reuse",
        not (holdout_utterances & dev_utterances),
        sorted(holdout_utterances & dev_utterances),
    )
    check(
        "no exact v1 utterance reuse",
        not (holdout_utterances & v1_utterances),
        sorted(holdout_utterances & v1_utterances),
    )

    for scenario in holdout.scenarios:
        previous_route = "in_domain"
        for turn in scenario.turns:
            expected_upserts = {
                f"upsert:{canonical_id}"
                for canonical_id in turn.gold_candidate_ids
            }
            check(
                f"{scenario.id} turn {turn.turn} State Diff covers updates",
                expected_upserts <= set(turn.gold_state_diff),
            )
            if turn.gold_item_action is not None:
                check(
                    f"{scenario.id} turn {turn.turn} State Diff covers action",
                    f"action:{turn.gold_item_action.name}"
                    in turn.gold_state_diff,
                )
            if turn.gold_tradeoff is not None:
                check(
                    f"{scenario.id} turn {turn.turn} State Diff covers trade-off",
                    any(item.startswith("tradeoff:") for item in turn.gold_state_diff),
                )
            if turn.gold_domain_route != previous_route:
                check(
                    f"{scenario.id} turn {turn.turn} State Diff covers route",
                    f"route:{turn.gold_domain_route}" in turn.gold_state_diff,
                )
            previous_route = turn.gold_domain_route

    raw = json.loads(HOLDOUT_PATH.read_text(encoding="utf-8"))
    serialized = json.dumps(raw, ensure_ascii=False).casefold()
    for forbidden_field in (
        '"product_id"',
        '"parent_asin"',
        '"recommended_products"',
        '"gold_ranking"',
    ):
        check(
            f"holdout excludes {forbidden_field}",
            forbidden_field not in serialized,
        )

    print("Untouched tablet-domain holdout verification completed.")


if __name__ == "__main__":
    main()
