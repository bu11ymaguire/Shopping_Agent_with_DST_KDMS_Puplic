"""Development-only scorer for tablet-domain v2 Understanding and routing.

This dataset is intentionally a dev set, not an untouched holdout. Its cases may be
used to improve the v2 prompt until a version is frozen. The final holdout must be
authored separately after that freeze.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from app.evaluation.actual_understanding import (
    ActualUnderstandingEvalCase,
    ActualUnderstandingGold,
    ActualEvalContract,
    aggregate_actual_understanding_scores,
    score_actual_understanding_prediction,
)
from app.models.actual_demo import TabletDomainUnderstandingOutput


class TabletDomainGold(ActualUnderstandingGold):
    domain_route: Literal["in_domain", "unsupported_category"]
    unsupported_category_text: str | None = None

    @model_validator(mode="after")
    def route_matches_gold_state(self) -> TabletDomainGold:
        unsupported = self.domain_route == "unsupported_category"
        if unsupported != (self.unsupported_category_text is not None):
            raise ValueError(
                "unsupported_category_text is required only for unsupported_category"
            )
        if unsupported:
            if not self.unsupported_category_text.strip():
                raise ValueError("unsupported_category_text must not be blank")
            if self.intents != ["unknown"]:
                raise ValueError("unsupported gold must use only unknown intent")
            if (
                self.candidates
                or self.item_action is not None
                or self.tradeoff is not None
                or self.supersedes
            ):
                raise ValueError("unsupported gold must not mutate tablet state")
        if any(
            candidate.canonical_id == "category_tablet"
            for candidate in self.candidates
        ):
            raise ValueError("environment category must not appear in v2 gold")
        return self


class TabletDomainDevCase(ActualUnderstandingEvalCase):
    id: str = Field(pattern=r"^td\d{2}$")
    gold: TabletDomainGold


class TabletDomainDevDataset(ActualEvalContract):
    dataset_version: str
    split: Literal["dev"]
    schema_version: Literal["understanding-v3.1-tablet-domain-en"]
    prompt_version_at_creation: str
    description: str
    cases: list[TabletDomainDevCase] = Field(min_length=20, max_length=30)

    @model_validator(mode="after")
    def case_ids_are_unique(self) -> TabletDomainDevDataset:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("tablet-domain dev case IDs must be unique")
        return self


def load_tablet_domain_dev_dataset(
    path: Path | str,
) -> TabletDomainDevDataset:
    return TabletDomainDevDataset.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _normalized_category(value: str | None) -> str | None:
    if value is None:
        return None
    clean = value.strip().casefold()
    aliases = {
        "notebook": "laptop",
        "smartphone": "phone",
        "mobile phone": "phone",
        "smart watch": "smartwatch",
    }
    return aliases.get(clean, clean)


def score_tablet_domain_prediction(
    case: TabletDomainDevCase,
    prediction: TabletDomainUnderstandingOutput,
    *,
    latency_ms: float | None = None,
) -> dict[str, Any]:
    score = score_actual_understanding_prediction(
        case,
        prediction,
        latency_ms=latency_ms,
    )
    unsupported = case.gold.domain_route == "unsupported_category"
    predicted_text = _normalized_category(prediction.unsupported_category_text)
    gold_text = _normalized_category(case.gold.unsupported_category_text)
    evidence_correct = (
        prediction.unsupported_category_evidence is not None
        and prediction.unsupported_category_evidence.strip().casefold()
        in case.utterance.casefold()
        if unsupported
        else prediction.unsupported_category_evidence is None
    )
    score.update(
        {
            "domain_route_exact": prediction.domain_route
            == case.gold.domain_route,
            "unsupported_category_text_exact": predicted_text == gold_text,
            "unsupported_category_evidence_span_correct": evidence_correct,
            "unsupported_no_tablet_mutation": (
                not prediction.candidates
                and prediction.item_action is None
                and prediction.tradeoff is None
                and not prediction.supersedes
                if unsupported
                else True
            ),
        }
    )
    return score


def aggregate_tablet_domain_scores(
    case_scores: list[dict[str, Any]],
    *,
    total_cases: int,
) -> dict[str, Any]:
    metrics = aggregate_actual_understanding_scores(
        case_scores,
        total_cases=total_cases,
    )
    successes = [score for score in case_scores if score.get("validation_success")]

    def accuracy(key: str) -> float:
        return (
            sum(bool(score.get(key)) for score in successes) / total_cases
            if total_cases
            else 1.0
        )

    metrics.update(
        {
            "domain_route_exact_accuracy": accuracy("domain_route_exact"),
            "unsupported_category_text_exact_accuracy": accuracy(
                "unsupported_category_text_exact"
            ),
            "unsupported_category_evidence_span_accuracy": accuracy(
                "unsupported_category_evidence_span_correct"
            ),
            "unsupported_no_tablet_mutation_accuracy": accuracy(
                "unsupported_no_tablet_mutation"
            ),
        }
    )
    return metrics
