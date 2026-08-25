"""Verify the untouched M0-versus-C confirmatory dataset and frozen protocol."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.poster_annotation import (  # noqa: E402
    load_poster_scenario_dataset,
)
from app.evaluation.tablet_domain_confirmatory import (  # noqa: E402
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    CONFIRMATORY_CORRECTION_TURNS,
    evaluate_confirmatory_decision,
    paired_bootstrap_state_diff,
)
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_understanding import (  # noqa: E402
    load_tablet_domain_dev_dataset,
)
from app.extended_experiment import (  # noqa: E402
    ExtendedTabletDomainUnderstandingOutput,
    update_extended_dialogue_state,
)
from app.models.actual_demo import (  # noqa: E402
    TabletDomainFacetCandidates,
    TabletDomainStateUpdateCandidate,
)
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402

DATASET_PATH = (
    BACKEND_ROOT / "data" / "tablet_domain_m0_c_confirmatory_holdout_v1.json"
)
MANIFEST_PATH = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_m0_c_confirmatory_protocol_v1.json"
)
OLD_HOLDOUT_PATH = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEV_PATH = BACKEND_ROOT / "data" / "tablet_domain_understanding_dev_v1.json"
V1_PATH = BACKEND_ROOT / "data" / "poster_recommendation_scenarios_v1.json"


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def sha256_lf_normalized(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def candidate(
    canonical_id: str,
    value_text: str,
    *,
    scope: str | None,
) -> TabletDomainStateUpdateCandidate:
    return TabletDomainStateUpdateCandidate.model_validate(
        {
            "canonical_id": canonical_id,
            "scope": scope,
            "value_text": value_text,
            "evidence_text": value_text,
            "origin": "explicit",
            "confidence": 1.0,
        }
    )


def understanding(
    strategy: str,
    *candidates: TabletDomainStateUpdateCandidate,
) -> ExtendedTabletDomainUnderstandingOutput:
    return ExtendedTabletDomainUnderstandingOutput(
        utterance="contract test",
        domain_route="in_domain",
        intents=["refine"],
        facets=TabletDomainFacetCandidates(),
        candidates=list(candidates),
        supersedes=[],
        residual_color_choice=False,
        experimental_strategy=strategy,
    )


def verify_dataset() -> None:
    dataset = load_tablet_holdout_dataset(DATASET_PATH)
    old = load_tablet_holdout_dataset(OLD_HOLDOUT_PATH)
    dev = load_tablet_domain_dev_dataset(DEV_PATH)
    v1 = load_poster_scenario_dataset(V1_PATH)
    check("new untouched split", dataset.split == "untouched_holdout")
    check("frozen before first confirmatory run", dataset.frozen_before_first_run)
    check("exactly 20 scenarios", len(dataset.scenarios) == 20)
    check(
        "exactly 80 turns",
        sum(len(item.turns) for item in dataset.scenarios) == 80,
    )
    check(
        "new scenario ID range",
        [item.id for item in dataset.scenarios]
        == [f"th{number:02d}" for number in range(21, 41)],
    )
    check(
        "eight frozen correction turns",
        CONFIRMATORY_CORRECTION_TURNS
        == frozenset((f"th{number:02d}", 4) for number in range(21, 29)),
    )

    utterances = {
        turn.utterance.casefold().strip()
        for scenario in dataset.scenarios
        for turn in scenario.turns
    }
    old_utterances = {
        turn.utterance.casefold().strip()
        for scenario in old.scenarios
        for turn in scenario.turns
    }
    dev_utterances = {item.utterance.casefold().strip() for item in dev.cases}
    v1_utterances = {
        turn.casefold().strip()
        for scenario in v1.scenarios
        for turn in scenario.conversation_turns
    }
    check("no exact old-holdout reuse", not (utterances & old_utterances))
    check("no exact dev reuse", not (utterances & dev_utterances))
    check("no exact recommendation-v1 reuse", not (utterances & v1_utterances))

    required_tags = {
        "correction",
        "no-op",
        "unsupported-category",
        "recovery",
        "negation",
        "unsupported-field",
        "tradeoff",
        "compare",
        "inspect",
        "purchase",
        "reject",
        "rank-reference",
        "ram",
        "storage",
    }
    tags = {tag for scenario in dataset.scenarios for tag in scenario.tags}
    check("confirmatory phenomena covered", required_tags <= tags)

    scenario_index = {item.id: item for item in dataset.scenarios}
    for scenario_id, turn_number in sorted(CONFIRMATORY_CORRECTION_TURNS):
        scenario = scenario_index[scenario_id]
        current = scenario.turns[turn_number - 1]
        canonical_id = current.gold_candidate_ids[0]
        earlier = [
            turn
            for turn in scenario.turns[: turn_number - 1]
            if canonical_id in turn.gold_candidate_ids
        ]
        check(
            f"{scenario_id}:{turn_number} repeats a hard ID as a real correction",
            len(current.gold_candidate_ids) == 1
            and current.gold_candidate_scopes[canonical_id] == "hard"
            and bool(earlier),
        )
        check(
            f"{scenario_id}:{turn_number} changes hard filters",
            current.expected_hard_filters_after_turn
            != scenario.turns[turn_number - 2].expected_hard_filters_after_turn,
        )

    for scenario in dataset.scenarios:
        previous_route = "in_domain"
        for turn in scenario.turns:
            expected_upserts = {
                f"upsert:{canonical_id}" for canonical_id in turn.gold_candidate_ids
            }
            check(
                f"{scenario.id}:{turn.turn} material diff covers current candidates",
                expected_upserts <= set(turn.gold_state_diff),
            )
            if turn.gold_item_action is not None:
                check(
                    f"{scenario.id}:{turn.turn} material diff covers action",
                    f"action:{turn.gold_item_action.name}" in turn.gold_state_diff,
                )
            if turn.gold_tradeoff is not None:
                check(
                    f"{scenario.id}:{turn.turn} material diff covers trade-off",
                    any(item.startswith("tradeoff:") for item in turn.gold_state_diff),
                )
            if turn.gold_domain_route != previous_route:
                check(
                    f"{scenario.id}:{turn.turn} material diff covers route",
                    f"route:{turn.gold_domain_route}" in turn.gold_state_diff,
                )
            previous_route = turn.gold_domain_route

    serialized = DATASET_PATH.read_text(encoding="utf-8").casefold()
    for forbidden in (
        '"product_id"',
        '"parent_asin"',
        '"recommended_products"',
        '"gold_ranking"',
        '"system_output"',
    ):
        check(f"dataset excludes {forbidden}", forbidden not in serialized)


def verify_noop_contract() -> None:
    state = create_tablet_environment_state()
    budget_310 = candidate("budget", "$310 maximum", scope="hard")
    state, _ = update_extended_dialogue_state(
        state,
        understanding("c_semantic_noop", budget_310),
        [],
        turn_id="t1",
    )
    repeated = candidate("budget", "no more than 310 dollars", scope="hard")
    same_state, same_diff = update_extended_dialogue_state(
        state,
        understanding("c_semantic_noop", repeated),
        [],
        turn_id="t2",
    )
    check("C suppresses unit-equivalent budget echo", not same_diff.changed_paths)
    check(
        "C leaves provenance unchanged for no-op",
        same_state.hard_constraints["budget"].updated_at_turn_id == "t1",
    )
    corrected = candidate("budget", "$345 maximum", scope="hard")
    corrected_state, corrected_diff = update_extended_dialogue_state(
        state,
        understanding("c_semantic_noop", corrected),
        [],
        turn_id="t3",
    )
    check(
        "C preserves numeric correction",
        "hard_constraints.budget" in corrected_diff.changed_paths
        and corrected_state.hard_constraints["budget"].value_text == "$345 maximum",
    )


def synthetic_record(scenario_id: str, *, false_positive: int) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "turn_metrics": [
            {
                "state_diff_counts": {
                    "true_positive": 1,
                    "false_positive": false_positive,
                    "false_negative": 0,
                }
            }
        ],
        "missing_gold_turns": [],
    }


def synthetic_metrics(*, false_positive: int, correction: float = 1.0) -> dict[str, Any]:
    return {
        "final_state_micro": {"f1": 0.9},
        "hard_constraint_completion_rate": 0.9,
        "turn_output_completion_rate": 1.0,
        "extended_diagnostics": {
            "correction_candidate_recall": correction,
            "material_state_diff_false_positive_count": false_positive,
        },
    }


def verify_statistics_and_decision() -> None:
    m0_records = [synthetic_record(f"s{index}", false_positive=1) for index in range(4)]
    c_records = [synthetic_record(f"s{index}", false_positive=0) for index in range(4)]
    bootstrap = paired_bootstrap_state_diff(
        m0_records,
        c_records,
        resamples=1000,
        seed=BOOTSTRAP_SEED,
    )
    check("paired bootstrap detects uniform improvement", bootstrap["confidence_interval_95"][0] > 0)
    check("production bootstrap count frozen", BOOTSTRAP_RESAMPLES == 10_000)
    check("production bootstrap seed frozen", BOOTSTRAP_SEED == 20_260_817)

    m0 = synthetic_metrics(false_positive=4)
    c = synthetic_metrics(false_positive=0)
    decision = evaluate_confirmatory_decision(
        m0_metrics=m0,
        replay_c_metrics=c,
        live_c_metrics=c,
        fixed_upstream_bootstrap=bootstrap,
        independent_live_bootstrap=bootstrap,
    )
    check("all prespecified checks can confirm", decision["confirmed"])
    failed = evaluate_confirmatory_decision(
        m0_metrics=m0,
        replay_c_metrics=c,
        live_c_metrics=synthetic_metrics(false_positive=0, correction=0.8),
        fixed_upstream_bootstrap=bootstrap,
        independent_live_bootstrap=bootstrap,
    )
    check("correction regression blocks confirmation", not failed["confirmed"])


def verify_manifest() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    check(
        "protocol frozen before live run",
        manifest["status"] == "protocol_frozen_before_first_confirmatory_run",
    )
    check(
        "dataset hash frozen",
        sha256_lf_normalized(DATASET_PATH)
        == manifest["dataset"]["sha256_lf_normalized"],
    )
    check(
        "live order frozen M0 then C",
        manifest["live_strategy_order"] == ["m0_baseline", "c_semantic_noop"],
    )
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        check(
            f"frozen source hash: {relative}",
            sha256_lf_normalized(BACKEND_ROOT / relative) == expected,
        )
    ancestor = subprocess.run(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            manifest["selected_method_commit"],
            "HEAD",
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    check("selected method commit is an ancestor", ancestor.returncode == 0)


def main() -> None:
    verify_dataset()
    verify_noop_contract()
    verify_statistics_and_decision()
    verify_manifest()
    print("M0-versus-C confirmatory protocol verification completed.")


if __name__ == "__main__":
    main()
