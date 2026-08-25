"""Aggregate completed blinded annotations into guarded poster metrics."""

from __future__ import annotations

import hashlib
import json
import random
import statistics
from collections import Counter
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.evaluation.actual_recommendation import (
    product_satisfies_hard_filters,
    score_graded_ranking,
)
from app.evaluation.poster_annotation import (
    ProductAnnotationPacket,
    ReviewAnnotationPacket,
)
from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models.actual_demo import ActualRecommendationQuery


class PosterEvaluationError(RuntimeError):
    """Raised when completed annotations cannot be evaluated safely."""


class IncompleteAnnotationError(PosterEvaluationError):
    """Raised when any selected packet still has a blank grade."""


class PosterResultContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PosterAnnotationCollectionManifest(PosterResultContract):
    schema_version: Literal["poster-annotation-collection-v1"]
    packet_base_id: str = Field(min_length=1)
    collection_method: Literal[
        "pending", "independent_human_annotation", "synthetic_verifier"
    ]
    product_annotator_ids: list[str] = Field(min_length=2)
    review_annotator_ids: list[str] = Field(min_length=2)
    annotators_worked_independently: bool
    private_provenance_withheld_until_completion: bool
    completed_at: str | None = None
    annotation_authorship_disclosure: str = Field(min_length=1)
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def collection_contract_is_consistent(
        self,
    ) -> PosterAnnotationCollectionManifest:
        pattern = "annotator-"
        for kind, annotators in (
            ("product", self.product_annotator_ids),
            ("review", self.review_annotator_ids),
        ):
            if len(annotators) != len(set(annotators)):
                raise ValueError(f"{kind} annotator IDs must be unique")
            if any(
                not item.startswith(pattern)
                or len(item) != len("annotator-00")
                or not item[-2:].isdigit()
                for item in annotators
            ):
                raise ValueError(f"{kind} annotator IDs must use annotator-NN")
        if self.collection_method == "independent_human_annotation":
            if not self.annotators_worked_independently:
                raise ValueError("human collection must attest independent annotation")
            if not self.private_provenance_withheld_until_completion:
                raise ValueError("human collection must attest provenance blinding")
            if not self.completed_at:
                raise ValueError("human collection must record completed_at")
        return self


class PosterSystemProvenance(PosterResultContract):
    rank: int = Field(ge=1)
    total_score: float
    evidence_review_ids: list[str]


class PosterProductProvenance(PosterResultContract):
    parent_asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    pool_source: Literal["hard_negative", "system_union"]
    systems: dict[str, PosterSystemProvenance]


class PosterReviewProvenance(PosterResultContract):
    source_review_id: str = Field(pattern=r"^ar23-[0-9a-f]{20}$")
    parent_asin: str = Field(pattern=r"^[A-Z0-9]{10}$")
    cited_by: list[str]


class PosterScenarioProvenance(PosterResultContract):
    scenario_id: str = Field(pattern=r"^ph\d{2}$")
    query: ActualRecommendationQuery
    product_map: dict[str, PosterProductProvenance]
    review_map: dict[str, PosterReviewProvenance]

    @model_validator(mode="after")
    def blind_identifiers_are_consistent(self) -> PosterScenarioProvenance:
        prefix = f"{self.scenario_id}-"
        if any(not item.startswith(prefix + "p") for item in self.product_map):
            raise ValueError("product blind IDs must match scenario_id")
        if any(not item.startswith(prefix + "p") for item in self.review_map):
            raise ValueError("review blind IDs must match scenario_id")
        ranks: dict[str, list[int]] = {}
        for product in self.product_map.values():
            for system, value in product.systems.items():
                ranks.setdefault(system, []).append(value.rank)
        for system, values in ranks.items():
            if sorted(values) != list(range(1, len(values) + 1)):
                raise ValueError(f"{system} ranks must be contiguous within a scenario")
        return self


class PosterPrivateProvenance(PosterResultContract):
    schema_version: Literal["poster-annotation-private-provenance-v1"]
    packet_base_id: str
    dataset_version: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    catalog_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    randomization_seed: str
    annotator_count: int = Field(ge=2)
    hard_negatives_per_scenario: int = Field(ge=0)
    scenario_count: int = Field(ge=1)
    product_judgment_count_per_annotator: int = Field(ge=1)
    review_judgment_count_per_annotator: int = Field(ge=1)
    scenarios: list[PosterScenarioProvenance]

    @model_validator(mode="after")
    def counts_and_ids_are_consistent(self) -> PosterPrivateProvenance:
        if len(self.scenarios) != self.scenario_count:
            raise ValueError("private scenario_count does not match scenarios")
        ids = [scenario.scenario_id for scenario in self.scenarios]
        if len(ids) != len(set(ids)):
            raise ValueError("private scenario IDs must be unique")
        product_count = sum(len(item.product_map) for item in self.scenarios)
        review_count = sum(len(item.review_map) for item in self.scenarios)
        if product_count != self.product_judgment_count_per_annotator:
            raise ValueError("private product judgment count does not match")
        if review_count != self.review_judgment_count_per_annotator:
            raise ValueError("private review judgment count does not match")
        return self


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_collection_manifest(path: Path | str) -> PosterAnnotationCollectionManifest:
    return PosterAnnotationCollectionManifest.model_validate(_load_json(Path(path)))


def load_private_provenance(path: Path | str) -> PosterPrivateProvenance:
    return PosterPrivateProvenance.model_validate(_load_json(Path(path)))


def krippendorff_alpha_ordinal(units: list[list[int]]) -> float | None:
    """Return ordinal alpha for units with at least two 0-3 ratings."""

    categories = (0, 1, 2, 3)
    index = {category: position for position, category in enumerate(categories)}
    observed = [[0.0 for _ in categories] for _ in categories]
    usable_units = 0
    for ratings in units:
        values = [value for value in ratings if value in index]
        if len(values) < 2:
            continue
        usable_units += 1
        counts = Counter(values)
        denominator = len(values) - 1
        for left in categories:
            for right in categories:
                pairs = counts[left] * (counts[right] - (left == right))
                observed[index[left]][index[right]] += pairs / denominator
    if not usable_units:
        return None

    marginals = [sum(row) for row in observed]
    total = sum(marginals)
    if total <= 1:
        return None
    expected = [[0.0 for _ in categories] for _ in categories]
    for left in range(len(categories)):
        for right in range(len(categories)):
            pairs = marginals[left] * (
                marginals[right] - (1 if left == right else 0)
            )
            expected[left][right] = pairs / (total - 1)

    distances = [[0.0 for _ in categories] for _ in categories]
    for left in range(len(categories)):
        for right in range(left + 1, len(categories)):
            cumulative = sum(marginals[left : right + 1])
            cumulative -= (marginals[left] + marginals[right]) / 2
            distances[left][right] = distances[right][left] = cumulative**2

    observed_disagreement = sum(
        observed[left][right] * distances[left][right]
        for left in range(len(categories))
        for right in range(len(categories))
    )
    expected_disagreement = sum(
        expected[left][right] * distances[left][right]
        for left in range(len(categories))
        for right in range(len(categories))
    )
    if expected_disagreement == 0:
        return 1.0 if observed_disagreement == 0 else None
    return 1 - observed_disagreement / expected_disagreement


def _round_metrics(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: round(value, 6) if isinstance(value, float) else value
        for key, value in values.items()
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("cannot take a percentile of an empty sequence")
    position = (len(sorted_values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def paired_bootstrap_difference(
    values_a: list[float],
    values_b: list[float],
    *,
    samples: int,
    seed: str,
) -> dict[str, float | int | str]:
    if len(values_a) != len(values_b) or not values_a:
        raise ValueError("paired bootstrap requires equally sized non-empty inputs")
    differences = [left - right for left, right in zip(values_a, values_b, strict=True)]
    generator = random.Random(seed)
    bootstrapped = []
    for _ in range(samples):
        bootstrapped.append(
            _mean([differences[generator.randrange(len(differences))] for _ in differences])
        )
    bootstrapped.sort()
    return _round_metrics(
        {
            "paired_unit_count": len(differences),
            "mean_difference_a_minus_b": _mean(differences),
            "ci_95_low": _percentile(bootstrapped, 0.025),
            "ci_95_high": _percentile(bootstrapped, 0.975),
            "bootstrap_samples": samples,
            "bootstrap_seed": seed,
        }
    )


def _annotation_key(scenario_id: str, item_id: str) -> str:
    return f"{scenario_id}:{item_id}"


def _load_product_packets(
    collection_dir: Path,
    annotator_ids: list[str],
) -> list[ProductAnnotationPacket]:
    packets = []
    for annotator_id in annotator_ids:
        path = collection_dir / annotator_id / "product_annotations.json"
        if not path.is_file():
            raise PosterEvaluationError(f"missing product packet: {path}")
        packet = ProductAnnotationPacket.model_validate(_load_json(path))
        if packet.annotator_id != annotator_id:
            raise PosterEvaluationError(f"product packet annotator mismatch: {path}")
        packets.append(packet)
    return packets


def _load_review_packets(
    collection_dir: Path,
    annotator_ids: list[str],
) -> list[ReviewAnnotationPacket]:
    packets = []
    for annotator_id in annotator_ids:
        path = collection_dir / annotator_id / "review_annotations.json"
        if not path.is_file():
            raise PosterEvaluationError(f"missing review packet: {path}")
        packet = ReviewAnnotationPacket.model_validate(_load_json(path))
        if packet.annotator_id != annotator_id:
            raise PosterEvaluationError(f"review packet annotator mismatch: {path}")
        packets.append(packet)
    return packets


def _immutable_public_payload(
    packet: ProductAnnotationPacket | ReviewAnnotationPacket,
) -> dict[str, Any]:
    payload = packet.model_dump(mode="json")
    for scenario in payload["scenarios"]:
        items = scenario.get("candidates", scenario.get("reviews", []))
        for item in items:
            item.pop("annotation", None)
    return payload


def _validate_frozen_public_packets(
    *,
    completed_products: list[ProductAnnotationPacket],
    completed_reviews: list[ReviewAnnotationPacket],
    frozen_packet_dir: Path,
    summary: dict[str, Any],
) -> None:
    for filename, packets, model in (
        ("product_annotations.json", completed_products, ProductAnnotationPacket),
        ("review_annotations.json", completed_reviews, ReviewAnnotationPacket),
    ):
        for completed in packets:
            relative = f"{completed.annotator_id}/{filename}"
            frozen_path = frozen_packet_dir / relative
            expected = summary["local_artifacts"].get(relative)
            if expected is None or not frozen_path.is_file():
                raise PosterEvaluationError(f"missing tracked frozen packet: {relative}")
            if frozen_path.stat().st_size != expected["bytes"]:
                raise PosterEvaluationError(f"frozen packet byte size changed: {relative}")
            if _sha256(frozen_path) != expected["sha256"]:
                raise PosterEvaluationError(f"frozen packet hash changed: {relative}")
            frozen = model.model_validate(_load_json(frozen_path))
            if _immutable_public_payload(completed) != _immutable_public_payload(frozen):
                raise PosterEvaluationError(
                    f"completed packet changed non-annotation content: {relative}"
                )


def _product_payloads(
    packet: ProductAnnotationPacket,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    payloads: dict[str, dict[str, Any]] = {}
    grades: dict[str, int] = {}
    incomplete = []
    for scenario in packet.scenarios:
        for candidate in scenario.candidates:
            key = _annotation_key(scenario.scenario_id, candidate.candidate_id)
            payloads[key] = candidate.model_dump(mode="json", exclude={"annotation"})
            if candidate.annotation.relevance is None:
                incomplete.append(key)
            else:
                grades[key] = candidate.annotation.relevance
    if incomplete:
        raise IncompleteAnnotationError(
            f"{packet.annotator_id} has {len(incomplete)} blank product grades"
        )
    return payloads, grades


def _review_payloads(
    packet: ReviewAnnotationPacket,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    payloads: dict[str, dict[str, Any]] = {}
    grades: dict[str, int] = {}
    incomplete = []
    for scenario in packet.scenarios:
        for review in scenario.reviews:
            key = _annotation_key(scenario.scenario_id, review.review_id)
            payloads[key] = review.model_dump(mode="json", exclude={"annotation"})
            if review.annotation.relevance is None:
                incomplete.append(key)
            else:
                grades[key] = review.annotation.relevance
    if incomplete:
        raise IncompleteAnnotationError(
            f"{packet.annotator_id} has {len(incomplete)} blank review grades"
        )
    return payloads, grades


def _validate_and_aggregate_packets(
    packets: list[ProductAnnotationPacket] | list[ReviewAnnotationPacket],
    *,
    kind: Literal["product", "review"],
) -> tuple[dict[str, float], dict[str, Any], list[dict[str, Any]]]:
    extractor = _product_payloads if kind == "product" else _review_payloads
    reference_payloads: dict[str, dict[str, Any]] | None = None
    grades_by_annotator: list[dict[str, int]] = []
    for packet in packets:
        payloads, grades = extractor(packet)  # type: ignore[arg-type]
        if reference_payloads is None:
            reference_payloads = payloads
        elif payloads != reference_payloads:
            raise PosterEvaluationError(
                f"{kind} candidate content or ID set differs across annotators"
            )
        grades_by_annotator.append(grades)
    if reference_payloads is None:
        raise PosterEvaluationError(f"no {kind} packets selected")

    units: dict[str, list[int]] = {
        key: [grades[key] for grades in grades_by_annotator]
        for key in sorted(reference_payloads)
    }
    aggregated = {key: float(statistics.median(values)) for key, values in units.items()}
    exact = sum(len(set(values)) == 1 for values in units.values())
    major = sum(max(values) - min(values) >= 2 for values in units.values())
    agreement = _round_metrics(
        {
            "annotator_count": len(packets),
            "unit_count": len(units),
            "krippendorff_alpha_ordinal": krippendorff_alpha_ordinal(
                list(units.values())
            ),
            "unanimous_exact_rate": exact / len(units),
            "major_disagreement_rate": major / len(units),
            "aggregation": "median",
        }
    )
    disagreements = [
        {"item_key": key, "grades": values, "median_grade": aggregated[key]}
        for key, values in units.items()
        if len(set(values)) > 1
    ]
    return aggregated, agreement, disagreements


def _system_names(private: PosterPrivateProvenance) -> list[str]:
    names = {
        system
        for scenario in private.scenarios
        for product in scenario.product_map.values()
        for system in product.systems
    }
    preferred = {"full": 0, "no_memory": 1, "no_review": 2, "token": 3, "semantic": 4}
    return sorted(names, key=lambda item: (preferred.get(item, 99), item))


def _product_case_results(
    private: PosterPrivateProvenance,
    product_grades: dict[str, float],
    catalog: ExperimentalAmazonCatalog,
    systems: list[str],
) -> dict[str, list[dict[str, Any]]]:
    results = {system: [] for system in systems}
    for scenario in private.scenarios:
        judgments = {
            blind_id: product_grades[_annotation_key(scenario.scenario_id, blind_id)]
            for blind_id in scenario.product_map
        }
        for system in systems:
            ranked = sorted(
                (
                    product.systems[system].rank,
                    blind_id,
                    product,
                )
                for blind_id, product in scenario.product_map.items()
                if system in product.systems
            )
            ranked_ids = [item[1] for item in ranked]
            top3 = score_graded_ranking(ranked_ids, judgments, k=3)
            top10 = score_graded_ranking(ranked_ids, judgments, k=10)
            violations = []
            inconsistent_evidence = []
            source_review_map = {
                review.source_review_id: (blind_id, review)
                for blind_id, review in scenario.review_map.items()
            }
            for _, blind_id, product in ranked[:10]:
                detail = catalog.get_product(product.parent_asin)
                if not product_satisfies_hard_filters(
                    detail,
                    scenario.query.hard_filters,
                    allow_budget_overrun=scenario.query.allow_budget_overrun,
                ):
                    violations.append(blind_id)
            for _, blind_id, product in ranked[:3]:
                evidence_ids = product.systems[system].evidence_review_ids
                if any(
                    source_id not in source_review_map
                    or source_review_map[source_id][1].parent_asin != product.parent_asin
                    for source_id in evidence_ids
                ):
                    inconsistent_evidence.append(blind_id)
            results[system].append(
                {
                    "scenario_id": scenario.scenario_id,
                    **top3,
                    **top10,
                    "hard_filter_violation_count_at_10": len(violations),
                    "ranked_count_at_10": min(10, len(ranked_ids)),
                    "evidence_inconsistency_count_at_3": len(inconsistent_evidence),
                    "ranked_count_at_3": min(3, len(ranked_ids)),
                }
            )
    return results


def _review_case_results(
    private: PosterPrivateProvenance,
    review_grades: dict[str, float],
    systems: list[str],
) -> dict[str, list[dict[str, Any]]]:
    results = {system: [] for system in systems}
    for scenario in private.scenarios:
        blind_by_source = {
            review.source_review_id: blind_id
            for blind_id, review in scenario.review_map.items()
        }
        candidate_by_review = {
            blind_id: blind_id.rsplit("-r", 1)[0]
            for blind_id in scenario.review_map
        }
        for system in systems:
            ranked_products = sorted(
                (
                    product.systems[system].rank,
                    blind_id,
                    product,
                )
                for blind_id, product in scenario.product_map.items()
                if system in product.systems
            )[:3]
            unit_metrics = []
            evidence_count = 0
            for _, blind_product_id, product in ranked_products:
                pooled_reviews = {
                    blind_id: review_grades[
                        _annotation_key(scenario.scenario_id, blind_id)
                    ]
                    for blind_id, candidate_id in candidate_by_review.items()
                    if candidate_id == blind_product_id
                }
                if not pooled_reviews:
                    continue
                ranked_review_ids = [
                    blind_by_source[source_id]
                    for source_id in product.systems[system].evidence_review_ids
                    if source_id in blind_by_source
                ]
                evidence_count += len(ranked_review_ids)
                unit_metrics.append(
                    score_graded_ranking(ranked_review_ids, pooled_reviews, k=3)
                )
            results[system].append(
                {
                    "scenario_id": scenario.scenario_id,
                    "assessable_product_count": len(unit_metrics),
                    "evidence_review_count": evidence_count,
                    "ndcg_at_3": _mean(
                        [float(item["ndcg_at_3"]) for item in unit_metrics]
                    ),
                    "precision_at_3": _mean(
                        [float(item["precision_at_3"]) for item in unit_metrics]
                    ),
                    "judgment_coverage_at_3": _mean(
                        [float(item["judgment_coverage_at_3"]) for item in unit_metrics]
                    ),
                }
            )
    return results


def _aggregate_product_system(cases: list[dict[str, Any]]) -> dict[str, Any]:
    violations = sum(item["hard_filter_violation_count_at_10"] for item in cases)
    ranked = sum(item["ranked_count_at_10"] for item in cases)
    inconsistent = sum(item["evidence_inconsistency_count_at_3"] for item in cases)
    top3 = sum(item["ranked_count_at_3"] for item in cases)
    return _round_metrics(
        {
            "scenario_count": len(cases),
            "ndcg_at_3": _mean([float(item["ndcg_at_3"]) for item in cases]),
            "ndcg_at_10": _mean([float(item["ndcg_at_10"]) for item in cases]),
            "recall_at_3": _mean([float(item["recall_at_3"]) for item in cases]),
            "precision_at_3": _mean(
                [float(item["precision_at_3"]) for item in cases]
            ),
            "hard_filter_violation_rate_at_10": violations / ranked if ranked else 0.0,
            "evidence_consistency_rate_at_3": 1 - inconsistent / top3 if top3 else 1.0,
        }
    )


def _aggregate_review_system(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return _round_metrics(
        {
            "scenario_count": len(cases),
            "assessable_product_count": sum(
                item["assessable_product_count"] for item in cases
            ),
            "evidence_review_count": sum(item["evidence_review_count"] for item in cases),
            "ndcg_at_3": _mean([float(item["ndcg_at_3"]) for item in cases]),
            "precision_at_3": _mean(
                [float(item["precision_at_3"]) for item in cases]
            ),
            "judgment_coverage_at_3": _mean(
                [float(item["judgment_coverage_at_3"]) for item in cases]
            ),
        }
    )


def _comparison_pairs(systems: list[str]) -> list[tuple[str, str]]:
    pairs = list(combinations(systems, 2))
    if "semantic" in systems and "token" in systems:
        pairs = [pair for pair in pairs if set(pair) != {"semantic", "token"}]
        pairs.insert(0, ("semantic", "token"))
    return pairs


def evaluate_completed_poster_annotations(
    *,
    collection_dir: Path,
    collection_manifest_path: Path,
    private_provenance_path: Path,
    packet_summary_path: Path,
    frozen_packet_dir: Path,
    bootstrap_samples: int = 10_000,
) -> dict[str, Any]:
    collection = load_collection_manifest(collection_manifest_path)
    private = load_private_provenance(private_provenance_path)
    summary = _load_json(packet_summary_path)
    if collection.packet_base_id != private.packet_base_id:
        raise PosterEvaluationError("collection/private packet_base_id mismatch")
    if summary["packet_base_id"] != private.packet_base_id:
        raise PosterEvaluationError("tracked summary/private packet_base_id mismatch")
    if summary["scenario_dataset_sha256"] != private.dataset_sha256:
        raise PosterEvaluationError("tracked summary/private dataset hash mismatch")
    expected_private = summary["local_artifacts"]["private_provenance.json"]
    if private_provenance_path.stat().st_size != expected_private["bytes"]:
        raise PosterEvaluationError("private provenance byte size changed")
    if _sha256(private_provenance_path) != expected_private["sha256"]:
        raise PosterEvaluationError("private provenance hash changed")
    if summary["catalog_manifest_sha256"] != private.catalog_manifest_sha256:
        raise PosterEvaluationError("catalog manifest hash differs from packet summary")
    if summary["semantic_manifest_sha256"] != private.semantic_manifest_sha256:
        raise PosterEvaluationError("semantic manifest hash differs from packet summary")

    product_packets = _load_product_packets(
        collection_dir, collection.product_annotator_ids
    )
    review_packets = _load_review_packets(collection_dir, collection.review_annotator_ids)
    _validate_frozen_public_packets(
        completed_products=product_packets,
        completed_reviews=review_packets,
        frozen_packet_dir=frozen_packet_dir,
        summary=summary,
    )
    for packet in [*product_packets, *review_packets]:
        if packet.dataset_sha256 != private.dataset_sha256:
            raise PosterEvaluationError("public packet/private dataset hash mismatch")
        if not packet.packet_id.startswith(private.packet_base_id):
            raise PosterEvaluationError("public packet/private packet ID mismatch")

    product_grades, product_agreement, product_disagreements = (
        _validate_and_aggregate_packets(product_packets, kind="product")
    )
    review_grades, review_agreement, review_disagreements = (
        _validate_and_aggregate_packets(review_packets, kind="review")
    )
    public_product_ids = {
        key.split(":", 1)[1] for key in product_grades
    }
    private_product_ids = {
        blind_id for scenario in private.scenarios for blind_id in scenario.product_map
    }
    public_review_ids = {key.split(":", 1)[1] for key in review_grades}
    private_review_ids = {
        blind_id for scenario in private.scenarios for blind_id in scenario.review_map
    }
    if public_product_ids != private_product_ids:
        raise PosterEvaluationError("public/private product blind ID mismatch")
    if public_review_ids != private_review_ids:
        raise PosterEvaluationError("public/private review blind ID mismatch")

    catalog = ExperimentalAmazonCatalog()
    if not catalog.available:
        raise PosterEvaluationError(catalog.status().unavailable_reason or "catalog unavailable")
    systems = _system_names(private)
    product_cases = _product_case_results(private, product_grades, catalog, systems)
    review_cases = _review_case_results(private, review_grades, systems)
    system_metrics = {
        system: {
            "product_ranking": _aggregate_product_system(product_cases[system]),
            "review_evidence": _aggregate_review_system(review_cases[system]),
        }
        for system in systems
    }

    comparisons_output = []
    for system_a, system_b in _comparison_pairs(systems):
        product_a = {item["scenario_id"]: item for item in product_cases[system_a]}
        product_b = {item["scenario_id"]: item for item in product_cases[system_b]}
        review_a = {item["scenario_id"]: item for item in review_cases[system_a]}
        review_b = {item["scenario_id"]: item for item in review_cases[system_b]}
        shared_product = sorted(set(product_a) & set(product_b))
        shared_review = sorted(set(review_a) & set(review_b))
        comparisons_output.append(
            {
                "comparison_id": f"{system_a}-minus-{system_b}",
                "system_a": system_a,
                "system_b": system_b,
                "product_ndcg_at_3": paired_bootstrap_difference(
                    [float(product_a[item]["ndcg_at_3"]) for item in shared_product],
                    [float(product_b[item]["ndcg_at_3"]) for item in shared_product],
                    samples=bootstrap_samples,
                    seed=f"poster-v1:{system_a}:{system_b}:product-ndcg3",
                ),
                "review_ndcg_at_3": paired_bootstrap_difference(
                    [float(review_a[item]["ndcg_at_3"]) for item in shared_review],
                    [float(review_b[item]["ndcg_at_3"]) for item in shared_review],
                    samples=bootstrap_samples,
                    seed=f"poster-v1:{system_a}:{system_b}:review-ndcg3",
                ),
            }
        )

    human_attested = (
        collection.collection_method == "independent_human_annotation"
        and collection.annotators_worked_independently
        and collection.private_provenance_withheld_until_completion
        and collection.completed_at is not None
    )
    ablation_ready = {"full", "no_memory", "no_review"} <= set(systems)
    blocking_gaps = []
    if not human_attested:
        blocking_gaps.append("independent human annotation is not attested")
    if not ablation_ready:
        blocking_gaps.append("Full/No-memory/No-review outputs are not all present")
    blocking_gaps.append("turn-level State Diff gold/evaluation is not part of packet v1")

    annotation_hashes = {
        str(path.relative_to(collection_dir)).replace("\\", "/"): _sha256(path)
        for annotator_id in sorted(
            set(collection.product_annotator_ids + collection.review_annotator_ids)
        )
        for path in (
            collection_dir / annotator_id / "product_annotations.json",
            collection_dir / annotator_id / "review_annotations.json",
        )
        if path.is_file()
    }
    return {
        "schema_version": "poster-human-evaluation-result-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "packet_base_id": private.packet_base_id,
        "dataset_sha256": private.dataset_sha256,
        "input_integrity": {
            "private_provenance_sha256": _sha256(private_provenance_path),
            "packet_summary_sha256": _sha256(packet_summary_path),
            "collection_manifest_sha256": _sha256(collection_manifest_path),
            "annotation_packet_sha256": annotation_hashes,
        },
        "annotation_collection": {
            "collection_method": collection.collection_method,
            "human_authorship_and_independence_attested": human_attested,
            "authorship_disclosure": collection.annotation_authorship_disclosure,
            "product": product_agreement,
            "review": review_agreement,
            "runner_cannot_verify_annotator_identity_or_human_authorship": True,
        },
        "systems": system_metrics,
        "paired_bootstrap_comparisons": comparisons_output,
        "disagreement_summary": {
            "product_disagreement_count": len(product_disagreements),
            "review_disagreement_count": len(review_disagreements),
            "product_items": product_disagreements,
            "review_items": review_disagreements,
        },
        "poster_readiness": {
            "recommendation_and_review_result_ready": human_attested,
            "required_ablation_systems_present": ablation_ready,
            "state_diff_primary_metric_present": False,
            "full_poster_primary_claim_ready": False,
            "blocking_gaps": blocking_gaps,
        },
        "limitations": [
            "Packet v1 compares token and semantic retrieval; it does not isolate memory contribution.",
            "The runner validates packet consistency but cannot verify annotator identity or authorship.",
            "Human-judged recommendation results must not be reported unless the collection attestation is true.",
            "Latent-hypothesis and controlled-inventory effects are outside packet v1.",
        ],
    }
