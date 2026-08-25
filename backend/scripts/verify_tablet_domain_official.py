"""Offline verification for the official tablet holdout scorer."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.actual_service import ActualDemoService  # noqa: E402
from app.actual_workflow import ActualCatalogWorkflow  # noqa: E402
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_official import (  # noqa: E402
    active_state_ids,
    aggregate_condition_metrics,
    score_holdout_turn,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.models.actual_demo import (  # noqa: E402
    TabletDomainFacetCandidates,
    TabletDomainStateUpdateCandidate,
    TabletDomainUnderstandingOutput,
)
from app.nodes.actual_response import ActualTemplateResponseComposer  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402

DATASET = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
OFFICIAL_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_holdout_official_v1.json"
)
OFFICIAL_RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
OFFICIAL_ANALYSIS = (
    BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1_analysis.json"
)


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate(
    canonical_id: str,
    evidence: str,
    *,
    scope: str | None = None,
) -> TabletDomainStateUpdateCandidate:
    return TabletDomainStateUpdateCandidate.model_validate(
        {
            "canonical_id": canonical_id,
            "scope": scope,
            "value_text": evidence,
            "evidence_text": evidence,
            "origin": "explicit",
            "confidence": 1.0,
        }
    )


def sequence() -> list[TabletDomainUnderstandingOutput]:
    reading = candidate("activity_reading", "reading PDFs")
    budget = candidate("budget", "$275", scope="hard")
    storage = candidate("storage_capacity", "128 GB", scope="hard")
    battery = candidate("battery", "Battery life", scope="soft")
    return [
        TabletDomainUnderstandingOutput(
            utterance="placeholder",
            domain_route="in_domain",
            intents=["search"],
            facets=TabletDomainFacetCandidates(activity=reading),
            candidates=[reading],
            supersedes=[],
            residual_color_choice=False,
        ),
        TabletDomainUnderstandingOutput(
            utterance="placeholder",
            domain_route="in_domain",
            intents=["refine"],
            facets=TabletDomainFacetCandidates(),
            candidates=[budget],
            supersedes=[],
            residual_color_choice=False,
        ),
        TabletDomainUnderstandingOutput(
            utterance="placeholder",
            domain_route="in_domain",
            intents=["refine"],
            facets=TabletDomainFacetCandidates(),
            candidates=[storage],
            supersedes=[],
            residual_color_choice=False,
        ),
        TabletDomainUnderstandingOutput.model_validate(
            {
                "utterance": "placeholder",
                "domain_route": "in_domain",
                "intents": ["refine"],
                "facets": {},
                "candidates": [battery.model_dump(mode="json")],
                "tradeoff": {
                    "prioritized_ids": ["battery"],
                    "compromised_ids": ["portability"],
                    "value_text": "battery over weight",
                    "evidence_text": "Battery life is worth a bit more weight",
                    "origin": "explicit",
                    "confidence": 1.0,
                },
                "supersedes": [],
                "residual_color_choice": False,
            }
        ),
    ]


class SequenceProvider:
    def __init__(self) -> None:
        self.outputs = sequence()
        self.index = 0

    async def __call__(self, **kwargs) -> TabletDomainUnderstandingOutput:
        output = self.outputs[self.index].model_copy(deep=True)
        self.index += 1
        return output.model_copy(update={"utterance": kwargs["utterance"]})


async def verify() -> None:
    scenario = load_tablet_holdout_dataset(DATASET).scenarios[0]
    catalog = ExperimentalAmazonCatalog()
    workflow = ActualCatalogWorkflow(
        catalog=catalog,
        understand=SequenceProvider(),
        response_composer=ActualTemplateResponseComposer(),
    )
    service = ActualDemoService(
        workflow,
        catalog,
        llm_provider="offline-sequence",
        initial_state_factory=create_tablet_environment_state,
    )
    snapshot = await service.create_conversation()
    turns = []
    metrics = []
    gold_active_ids = {"category_tablet"}
    for gold in scenario.turns:
        started = time.perf_counter()
        turn = await service.run_turn(snapshot.conversation_id, gold.utterance)
        wall = round((time.perf_counter() - started) * 1000, 1)
        gold_active_ids.update(gold.gold_candidate_ids)
        turns.append(turn)
        metrics.append(
            score_holdout_turn(
                gold,
                turn,
                catalog,
                wall_latency_ms=wall,
                gold_active_ids=gold_active_ids,
                condition="full",
            )
        )

    expected_final = set(scenario.final_gold_state_ids)
    actual_final = active_state_ids(turns[-1].dialogue_state)
    record = {
        "scenario_id": scenario.id,
        "status": "completed",
        "expected_turn_count": len(scenario.turns),
        "turn_metrics": metrics,
        "final_state_counts": {
            "exact": expected_final == actual_final,
            "true_positive": len(expected_final & actual_final),
            "false_positive": len(actual_final - expected_final),
            "false_negative": len(expected_final - actual_final),
        },
    }
    aggregate = aggregate_condition_metrics([record])
    check("all synthetic turns complete", aggregate["turn_output_completion_rate"] == 1)
    check("canonical micro-F1", aggregate["canonical_id_micro"]["f1"] == 1)
    check("State Diff micro-F1", aggregate["state_diff_micro"]["f1"] == 1)
    check("policy accuracy", aggregate["policy_lane_accuracy"] == 1)
    check("hard-filter completion", aggregate["hard_constraint_completion_rate"] == 1)
    check("final state exact", aggregate["final_state_micro"]["f1"] == 1)
    check("evidence consistency", aggregate["evidence_consistency_rate"] == 1)

    frozen = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
    check("official batch is frozen", frozen["status"].startswith("first_official"))
    check("official human relevance pending", frozen["human_relevance_status"] == "not_collected")
    if OFFICIAL_RAW.exists() and OFFICIAL_ANALYSIS.exists():
        check(
            "official raw report hash",
            sha256(OFFICIAL_RAW)
            == frozen["git_excluded_artifacts"]["raw_report"]["sha256"],
        )
        check(
            "official analysis report hash",
            sha256(OFFICIAL_ANALYSIS)
            == frozen["git_excluded_artifacts"]["deterministic_analysis"]["sha256"],
        )
        analysis = json.loads(OFFICIAL_ANALYSIS.read_text(encoding="utf-8"))
        paired = analysis["paired_comparisons"]["full_minus_no_memory"]
        check("paired final-state benefit preserved", paired["final_state_micro_f1_delta"] > 0)
        check("paired State Diff trade-off preserved", paired["state_diff_micro_f1_delta"] < 0)
        check(
            "fixed-upstream No-review candidate identity",
            analysis["fixed_upstream_no_review"]["candidate_count_identity_rate"] == 1,
        )
    print("Tablet-domain official scorer verification completed.")


if __name__ == "__main__":
    asyncio.run(verify())
