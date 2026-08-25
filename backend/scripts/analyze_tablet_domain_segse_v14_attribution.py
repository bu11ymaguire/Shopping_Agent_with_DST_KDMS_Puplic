"""Attribute the v1.3 to v1.4 movement to code or to provider variance.

The two live runs used the same fixture, the same prompt text, and the same
temperature, so any metric difference has exactly two possible sources: the
deterministic interpretation change, or a different raw proposal from the
provider.  This script separates them offline with zero new LLM calls.

For every turn it replays the *recorded* raw proposal of each run through both
deterministic pipelines against that run's own recorded pre-turn state, giving a
two-by-two grid:

    {v1.3 raw, v1.4 raw} x {v1.3 code, v1.4 code}

A cell that differs across code columns is attributable to the code change.  A
cell that differs across raw rows only is attributable to provider variance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Mapping

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.llm import write_report  # noqa: E402
from app.models import DialogueState  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.segse_experiment import update_segse_dialogue_state  # noqa: E402
from app.segse_experiment_v12 import SEGSEV12ProposalOutput  # noqa: E402
from app.segse_experiment_v13 import SEGSEV13UnderstandingProvider  # noqa: E402
from app.segse_experiment_v14 import SEGSEV14UnderstandingProvider  # noqa: E402


DEFAULT_V13 = BACKEND_ROOT / "reports" / "tablet_domain_segse_e2e_dev_v13.json"
DEFAULT_V14 = BACKEND_ROOT / "reports" / "tablet_domain_segse_e2e_dev_v14.json"
DEFAULT_OUTPUT = (
    BACKEND_ROOT
    / "data"
    / "results"
    / "tablet_domain_segse_e2e_dev_v14_attribution.json"
)


class _RecordedClient:
    def __init__(self, output: SEGSEV12ProposalOutput) -> None:
        self.output = output

    async def generate_structured(self, **_: Any) -> SEGSEV12ProposalOutput:
        return self.output


def _state_summary(state: DialogueState) -> dict[str, Any]:
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


def _recorded_turns(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Index every recorded turn with its raw proposal and its pre-turn state."""

    turns: dict[str, dict[str, Any]] = {}
    for scenario in report["treatment"]["scenarios"]:
        previous = create_tablet_environment_state()
        for entry in sorted(scenario["turns"], key=lambda item: item["gold_turn"]):
            details = entry["understanding_details"]
            key = f'{scenario["scenario_id"]}t{entry["gold_turn"]}'
            turns[key] = {
                "utterance": details["utterance"],
                "domain_route": details["domain_route"],
                "unsupported_category_text": details["unsupported_category_text"],
                "unsupported_category_evidence": details[
                    "unsupported_category_evidence"
                ],
                "intents": details["intents"],
                "item_action": details["item_action"],
                "tradeoff": details["tradeoff"],
                "raw_proposal_events": details["segse_v12_raw_proposal_events"],
                "previous_state": previous,
            }
            previous = DialogueState.model_validate(entry["pipeline"]["dialogue_state"])
    return turns


def _proposal(record: Mapping[str, Any]) -> SEGSEV12ProposalOutput:
    if record["item_action"] is not None:
        raise RuntimeError(
            "recorded item_action replay is not supported by this attribution script"
        )
    return SEGSEV12ProposalOutput.model_validate(
        {
            "utterance": record["utterance"],
            "domain_route": record["domain_route"],
            "unsupported_category_text": record["unsupported_category_text"],
            "unsupported_category_evidence": record["unsupported_category_evidence"],
            "intents": record["intents"],
            "state_events": record["raw_proposal_events"],
            "item_action": None,
            "tradeoff": record["tradeoff"],
        }
    )


async def _replay(
    provider_factory: Any, record: Mapping[str, Any]
) -> tuple[list[str], list[str], list[str]]:
    provider = provider_factory(_RecordedClient(_proposal(record)))
    previous: DialogueState = record["previous_state"]
    understanding = await provider(
        utterance=record["utterance"],
        previous_state_summary=_state_summary(previous),
        conversation_id="segse-v14-attribution",
        turn=1,
    )
    _, _ = update_segse_dialogue_state(
        previous.model_copy(deep=True),
        understanding,  # type: ignore[arg-type]
        [],
        turn_id="attribution",
    )
    authorized = sorted(
        f"{item.canonical_id}|{item.act}"
        for item in understanding.segse_authorized_events
    )
    operations = sorted(
        f"{item.canonical_id}|{item.operation}"
        for item in understanding.segse_material_operations
    )
    rejections = sorted(item.reason for item in understanding.segse_event_rejections)
    return authorized, operations, rejections


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    v13_report = json.loads(args.v13_report.resolve().read_text(encoding="utf-8"))
    v14_report = json.loads(args.v14_report.resolve().read_text(encoding="utf-8"))
    v13_turns = _recorded_turns(v13_report)
    v14_turns = _recorded_turns(v14_report)
    if set(v13_turns) != set(v14_turns):
        raise RuntimeError("the two runs do not cover the same turns")

    factories = {
        "v13_code": SEGSEV13UnderstandingProvider,
        "v14_code": SEGSEV14UnderstandingProvider,
    }
    rows: list[dict[str, Any]] = []
    for key in sorted(v13_turns):
        cells: dict[str, dict[str, Any]] = {}
        for raw_label, record in (("v13_raw", v13_turns[key]), ("v14_raw", v14_turns[key])):
            for code_label, factory in factories.items():
                authorized, operations, rejections = await _replay(factory, record)
                cells[f"{raw_label}|{code_label}"] = {
                    "authorized_event_pairs": authorized,
                    "material_operations": operations,
                    "rejection_reasons": rejections,
                }
        raw_identical = (
            v13_turns[key]["raw_proposal_events"]
            == v14_turns[key]["raw_proposal_events"]
        )
        code_effect_on_v13_raw = (
            cells["v13_raw|v13_code"]["material_operations"]
            != cells["v13_raw|v14_code"]["material_operations"]
        )
        code_effect_on_v14_raw = (
            cells["v14_raw|v13_code"]["material_operations"]
            != cells["v14_raw|v14_code"]["material_operations"]
        )
        provider_effect_under_v14_code = (
            cells["v13_raw|v14_code"]["material_operations"]
            != cells["v14_raw|v14_code"]["material_operations"]
        )
        rows.append(
            {
                "turn_id": key,
                "utterance": v14_turns[key]["utterance"],
                "raw_proposal_identical_across_runs": raw_identical,
                "code_changed_outcome_on_v13_raw": code_effect_on_v13_raw,
                "code_changed_outcome_on_v14_raw": code_effect_on_v14_raw,
                "provider_changed_outcome_under_v14_code": (
                    provider_effect_under_v14_code
                ),
                "cells": cells,
            }
        )

    summary = {
        "schema_version": "tablet-domain-segse-e2e-dev-v14-attribution-v1",
        "status": "offline_attribution_diagnostic_not_confirmatory",
        "additional_llm_calls": 0,
        "method": (
            "Each recorded raw proposal is replayed through both deterministic "
            "pipelines against that run's own recorded pre-turn state. Item actions "
            "are absent in this fixture, and sanitized controls are reused, so only "
            "state events are attributed."
        ),
        "v13_run_id": v13_report["run_id"],
        "v14_run_id": v14_report["run_id"],
        "turn_count": len(rows),
        "counts": {
            "raw_proposal_identical": sum(
                bool(item["raw_proposal_identical_across_runs"]) for item in rows
            ),
            "raw_proposal_differed": sum(
                not item["raw_proposal_identical_across_runs"] for item in rows
            ),
            "code_changed_outcome_on_v13_raw": sum(
                bool(item["code_changed_outcome_on_v13_raw"]) for item in rows
            ),
            "code_changed_outcome_on_v14_raw": sum(
                bool(item["code_changed_outcome_on_v14_raw"]) for item in rows
            ),
            "provider_changed_outcome_under_v14_code": sum(
                bool(item["provider_changed_outcome_under_v14_code"]) for item in rows
            ),
        },
        "turns_where_code_changed_outcome": [
            item["turn_id"]
            for item in rows
            if item["code_changed_outcome_on_v13_raw"]
            or item["code_changed_outcome_on_v14_raw"]
        ],
        "turns_where_provider_changed_outcome": [
            item["turn_id"]
            for item in rows
            if item["provider_changed_outcome_under_v14_code"]
        ],
        "turns": rows,
    }
    write_report(args.output.resolve(), summary)
    print(json.dumps(summary["counts"], indent=2), flush=True)
    print(
        "code-attributed turns: "
        f'{summary["turns_where_code_changed_outcome"]}',
        flush=True,
    )
    print(
        "provider-attributed turns: "
        f'{summary["turns_where_provider_changed_outcome"]}',
        flush=True,
    )
    for item in rows:
        if not (
            item["code_changed_outcome_on_v13_raw"]
            or item["code_changed_outcome_on_v14_raw"]
            or item["provider_changed_outcome_under_v14_code"]
        ):
            continue
        print("-" * 88, flush=True)
        print(f'{item["turn_id"]}: {item["utterance"]}', flush=True)
        for cell, value in item["cells"].items():
            print(
                f'  {cell:<22} ops={value["material_operations"]} '
                f'rejections={value["rejection_reasons"]}',
                flush=True,
            )
    print(f"attribution_report={args.output.resolve()}", flush=True)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v13-report", type=Path, default=DEFAULT_V13)
    parser.add_argument("--v14-report", type=Path, default=DEFAULT_V14)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
