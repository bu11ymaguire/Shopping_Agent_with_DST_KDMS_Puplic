"""Verify the frozen SEGSE v1.2 multi-turn development protocol."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_segse import (  # noqa: E402
    active_final_tokens,
    expected_state,
    gold_material_operations,
)
from app.evaluation.tablet_domain_segse_e2e import (  # noqa: E402
    ARM_ORDER,
    load_segse_e2e_dataset,
    verify_gold_trajectory,
)
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402


DATASET = BACKEND_ROOT / "data" / "tablet_domain_segse_e2e_dev_v1.json"
MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v1.json"
)
MODULE_FIXTURE = BACKEND_ROOT / "data" / "tablet_domain_segse_dev_v1.json"
OFFICIAL_FIXTURE = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
CONFIRMATORY_FIXTURE = (
    BACKEND_ROOT / "data" / "tablet_domain_m0_c_confirmatory_holdout_v1.json"
)


def check(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[ok] {label}")


def _utterances(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "cases" in payload:
        return {str(item["utterance"]).casefold() for item in payload["cases"]}
    return {
        str(turn["utterance"]).casefold()
        for scenario in payload["scenarios"]
        for turn in scenario["turns"]
    }


def _sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def main() -> None:
    dataset = load_segse_e2e_dataset(DATASET)
    verify_gold_trajectory(dataset)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    check("manifest is development-only", manifest["confirmatory_evidence"] is False)
    check(
        "fixture hash frozen",
        _sha256_lf(DATASET) == manifest["dataset"]["sha256_lf_normalized"],
    )
    check(
        "frozen source hashes match",
        all(
            _sha256_lf(BACKEND_ROOT / relative) == expected
            for relative, expected in manifest[
                "frozen_sources_sha256_lf_normalized"
            ].items()
        ),
    )
    check("six frozen development scenarios", len(dataset.scenarios) == 6)
    check(
        "twenty-four sequential turns",
        sum(len(item.turns) for item in dataset.scenarios) == 24,
    )
    check("fixed live arm order", ARM_ORDER == ("b0_c_semantic_noop", "d4_segse_v12"))

    current_utterances = {
        turn.utterance.casefold()
        for scenario in dataset.scenarios
        for turn in scenario.turns
    }
    previous_utterances = set().union(
        *(
            _utterances(path)
            for path in (MODULE_FIXTURE, OFFICIAL_FIXTURE, CONFIRMATORY_FIXTURE)
        )
    )
    check(
        "no literal reuse from prior state fixtures",
        not (current_utterances & previous_utterances),
    )

    no_event = confirmation = retract = correction = reactivation = 0
    all_operations: set[str] = set()
    for scenario in dataset.scenarios:
        gold_state = create_tablet_environment_state()
        for turn in scenario.turns:
            case = turn.as_case(scenario.id)
            operations = gold_material_operations(case, gold_state)
            all_operations.update(operations.values())
            no_event += not turn.gold_events
            confirmation += sum(item.act == "confirm" for item in turn.gold_events)
            retract += sum(item.act == "retract" for item in turn.gold_events)
            correction += sum(
                operation in {"update_value", "update_scope", "refine"}
                for operation in operations.values()
            )
            reactivation += sum(
                operation == "reactivate" for operation in operations.values()
            )
            gold_state = expected_state(case, gold_state)
        check(
            f"{scenario.id} declared final tokens",
            active_final_tokens(gold_state) == set(scenario.final_gold_active_tokens),
        )

    check("five event-free negative controls", no_event == 5)
    check("one confirmation target", confirmation == 1)
    check("two retract targets", retract == 2)
    check("two correction targets", correction == 2)
    check("one reactivation target", reactivation == 1)
    check(
        "material operation families covered",
        {"add", "update_value", "update_scope", "retract", "reactivate"}
        <= all_operations,
    )
    check(
        "every scenario includes a persistence or no-op challenge",
        all(
            set(item.tags)
            & {
                "persistence",
                "confirmation",
                "retract",
                "scope_correction",
                "no_update_control",
                "soft_retract",
            }
            for item in dataset.scenarios
        ),
    )
    print("SEGSE v1.2 end-to-end development protocol verification completed.")


if __name__ == "__main__":
    main()
