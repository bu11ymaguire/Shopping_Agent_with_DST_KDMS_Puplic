"""Verify the tablet-domain v2 development fixture and scorer offline."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_understanding import (  # noqa: E402
    TabletDomainDevCase,
    aggregate_tablet_domain_scores,
    load_tablet_domain_dev_dataset,
    score_tablet_domain_prediction,
)
from app.models.actual_demo import (  # noqa: E402
    TabletDomainFacetCandidates,
    ActualItemActionCandidate,
    ActualRejectionReason,
    ActualTradeoffCandidate,
    TabletDomainStateUpdateCandidate,
    TabletDomainUnderstandingOutput,
)
from app.nodes.tablet_domain_understanding import (  # noqa: E402
    TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
)

DATASET_PATH = BACKEND_ROOT / "data" / "tablet_domain_understanding_dev_v1.json"


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def prediction_from_gold(
    case: TabletDomainDevCase,
) -> TabletDomainUnderstandingOutput:
    candidates = []
    for gold in case.gold.candidates:
        value_text = " ".join(group[0] for group in gold.value_must_contain)
        candidates.append(
            TabletDomainStateUpdateCandidate.model_validate(
                {
                    "canonical_id": gold.canonical_id,
                    "scope": gold.scope,
                    "value_text": value_text or str(gold.canonical_id),
                    "evidence_text": case.utterance,
                    "origin": gold.origin,
                    "confidence": 1.0,
                }
            )
        )

    action = None
    if gold_action := case.gold.item_action:
        rejection = None
        if gold_action.rejection_reason_id:
            rejection = ActualRejectionReason(
                canonical_id=gold_action.rejection_reason_id,
                value_text="gold rejection reason",
                evidence_text=case.utterance,
                reason_type=gold_action.reason_type,
            )
        action = ActualItemActionCandidate(
            name=gold_action.name,
            target_rank=gold_action.target_rank,
            compare_rank=gold_action.compare_rank,
            rejection_reason=rejection,
        )

    tradeoff = None
    if gold_tradeoff := case.gold.tradeoff:
        tradeoff = ActualTradeoffCandidate(
            prioritized_ids=gold_tradeoff.prioritized_ids,
            compromised_ids=gold_tradeoff.compromised_ids,
            value_text="gold trade-off",
            evidence_text=case.utterance,
            origin=gold_tradeoff.origin,
            confidence=1.0,
        )

    unsupported = case.gold.domain_route == "unsupported_category"
    return TabletDomainUnderstandingOutput(
        utterance=case.utterance,
        intents=case.gold.intents,
        domain_route=case.gold.domain_route,
        unsupported_category_text=case.gold.unsupported_category_text,
        unsupported_category_evidence=(case.gold.unsupported_category_text if unsupported else None),
        facets=TabletDomainFacetCandidates(),
        candidates=candidates,
        item_action=action,
        tradeoff=tradeoff,
        supersedes=case.gold.supersedes,
        residual_color_choice=False,
    )


def main() -> None:
    dataset = load_tablet_domain_dev_dataset(DATASET_PATH)
    check("dataset is explicitly development-only", dataset.split == "dev")
    check("development set has 26 cases", len(dataset.cases) == 26)
    check(
        "dataset preserves the prompt version at fixture creation",
        dataset.prompt_version_at_creation
        == "spn-understanding-tablet-domain-en-v2-dev",
    )
    check(
        "selected prompt is explicitly frozen after dev",
        TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION
        == "spn-understanding-tablet-domain-en-v2.3-frozen",
        TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
    )
    required_tags = {
        "implicit-domain",
        "unsupported-category",
        "ram",
        "storage",
        "negation",
        "over-inference-negative-control",
        "rank-reference",
        "reject",
        "constraint-change",
        "tradeoff",
        "contradiction",
    }
    tags = {tag for case in dataset.cases for tag in case.tags}
    check("all planned dev phenomena are represented", required_tags <= tags)
    check(
        "environment category is absent from every gold candidate",
        all(
            all(
                candidate.canonical_id != "category_tablet"
                for candidate in case.gold.candidates
            )
            for case in dataset.cases
        ),
    )

    predictions = [prediction_from_gold(case) for case in dataset.cases]
    scores = [
        score_tablet_domain_prediction(case, prediction)
        for case, prediction in zip(dataset.cases, predictions, strict=True)
    ]
    metrics = aggregate_tablet_domain_scores(
        scores,
        total_cases=len(dataset.cases),
    )
    for key in (
        "validation_success_rate",
        "domain_route_exact_accuracy",
        "unsupported_category_text_exact_accuracy",
        "unsupported_category_evidence_span_accuracy",
        "unsupported_no_tablet_mutation_accuracy",
        "canonical_id_exact_accuracy",
        "canonical_id_micro_f1",
    ):
        check(f"gold self-score {key}", metrics[key] == 1.0, metrics[key])

    route_case = next(case for case in dataset.cases if case.id == "td07")
    wrong_route_payload = prediction_from_gold(route_case).model_dump(mode="json")
    wrong_route_payload.update(
        {
            "intents": ["search"],
            "domain_route": "in_domain",
            "unsupported_category_text": None,
            "unsupported_category_evidence": None,
        }
    )
    wrong_route = TabletDomainUnderstandingOutput.model_validate(wrong_route_payload)
    route_score = score_tablet_domain_prediction(route_case, wrong_route)
    check(
        "scorer detects an unsupported request routed in-domain",
        not route_score["domain_route_exact"],
    )

    value_case = next(case for case in dataset.cases if case.id == "td25")
    wrong_value = prediction_from_gold(value_case)
    wrong_value.candidates[0].value_text = "at least 12 GB RAM"
    value_score = score_tablet_domain_prediction(value_case, wrong_value)
    check(
        "scorer separates corrected numeric value from canonical ID",
        value_score["canonical_id_exact"]
        and value_score["value_correct"] < value_score["value_checked_count"],
    )

    print("Tablet-domain v2 dev dataset/scorer verification completed.")


if __name__ == "__main__":
    main()
