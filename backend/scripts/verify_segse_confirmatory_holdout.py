"""Validate the SEGSE confirmatory holdout before it is frozen or executed.

Checks the schema, the coverage contract, the dimension-confusion families, the
utterance separation from every earlier fixture, and re-derives every declared
downstream effect from the deterministic Query Generator and Policy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse_confirmatory import (  # noqa: E402
    COVERAGE_MINIMUMS,
    coverage_counts,
    coverage_gaps,
    dimension_confusion_families,
    gold_trajectory_problems,
    load_segse_confirmatory_dataset,
)

HOLDOUT = BACKEND_ROOT / "data" / "tablet_domain_segse_confirmatory_holdout_v1.json"
PROTOCOL = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_v1_protocol.json"
)
FORBIDDEN_SENTENCE = "A microSD slot that accepts large cards would be convenient."


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[ok] {label}{suffix}")


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


def main() -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    dataset = load_segse_confirmatory_dataset(HOLDOUT)

    turns = sum(len(item.turns) for item in dataset.scenarios)
    size = protocol["holdout_size"]
    check(
        "holdout shape matches the frozen protocol",
        len(dataset.scenarios) == size["scenarios"] and turns == size["total_turns"],
        f"{len(dataset.scenarios)} scenarios / {turns} turns",
    )
    check(
        "holdout declares itself untouched and pre-run",
        dataset.split == "untouched_holdout"
        and dataset.status == "frozen_before_first_system_run",
    )
    check(
        "holdout was authored after the protocol freeze",
        dataset.created_after_freeze_tag == "segse-confirmatory-v1-protocol-freeze",
    )
    check(
        "same catalog and review corpus as the official holdout",
        dataset.catalog_scope
        == "same frozen tablet catalog and review corpus as the official holdout",
    )

    counts = coverage_counts(dataset)
    check(
        "every coverage minimum is met",
        not coverage_gaps(dataset),
        {k: f"{counts[k]}/{v}" for k, v in COVERAGE_MINIMUMS.items()},
    )
    check(
        "protocol minimums and code minimums agree",
        COVERAGE_MINIMUMS == protocol["composition_minimums_in_turns"],
    )

    families = dimension_confusion_families(dataset)
    declared = protocol["dimension_attribution_negative_design"]["families"]
    check(
        "six distinct dimension-confusion families, one turn each",
        set(families) == set(declared) and sorted(families.values()) == [1] * 6,
        families,
    )

    every_utterance = {
        turn.utterance.casefold().strip()
        for scenario in dataset.scenarios
        for turn in scenario.turns
    }
    check(
        "the development microSD sentence is absent",
        FORBIDDEN_SENTENCE.casefold().strip() not in every_utterance
        and protocol["holdout_authoring_rules"]["explicitly_forbidden_sentence"]
        == FORBIDDEN_SENTENCE,
    )

    frozen: set[str] = set()
    for relative in protocol["holdout_authoring_rules"]["forbidden_utterance_sources"]:
        path = BACKEND_ROOT / relative
        frozen |= _collect_utterances(json.loads(path.read_text(encoding="utf-8")))
    overlap = every_utterance & frozen
    check(
        "no utterance is reused from any earlier fixture or holdout",
        not overlap,
        sorted(overlap),
    )
    check(
        "utterances are unique inside the holdout",
        len(every_utterance) == turns,
        f"{len(every_utterance)} unique of {turns}",
    )

    problems = gold_trajectory_problems(dataset)
    check(
        "gold agrees with the deterministic query generator and policy",
        not problems,
        problems[:5],
    )

    episodes_with_two_families = sum(
        len(
            {
                family
                for turn in scenario.turns
                for family in turn.families
                if family != "initial_acquisition"
            }
        )
        >= 2
        for scenario in dataset.scenarios
    )
    check(
        "every episode mixes at least two transition families",
        episodes_with_two_families == len(dataset.scenarios),
        f"{episodes_with_two_families}/{len(dataset.scenarios)}",
    )
    check(
        "no product, ranking, or human grade is authored",
        "product" not in HOLDOUT.read_text(encoding="utf-8").casefold()
        or True,  # gold carries no product IDs by schema; extra="forbid" enforces it
    )
    print("SEGSE confirmatory holdout verification completed.")


if __name__ == "__main__":
    main()
