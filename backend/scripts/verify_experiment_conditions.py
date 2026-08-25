"""Verify one-variable Full/No-memory/No-review experiment contracts offline."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.experiment_conditions import (  # noqa: E402
    CONDITION_CONTRACT,
    condition_turn_inputs,
    rank_products_without_review_contribution,
)
from app.models import PreferenceValue  # noqa: E402
from app.nodes.actual_recommendation import (  # noqa: E402
    browse_actual_catalog,
    generate_actual_query,
    rank_actual_products,
)
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.review_retrieval import TokenReviewRetriever  # noqa: E402


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def preference(canonical_id: str, value_text: str, turn: str) -> PreferenceValue:
    return PreferenceValue(
        canonical_id=canonical_id,
        value_text=value_text,
        origin="explicit",
        confidence=1.0,
        status="confirmed",
        evidence_turn_ids=[turn],
        updated_at_turn_id=turn,
    )


def main() -> None:
    check(
        "all three experiment conditions are explicit",
        set(CONDITION_CONTRACT) == {"full", "no_memory", "no_review"},
    )
    check(
        "environment category is invariant",
        all(
            contract["environment_category"] == "category_tablet"
            for contract in CONDITION_CONTRACT.values()
        ),
    )
    check(
        "only No-memory removes persistent state",
        {
            name
            for name, contract in CONDITION_CONTRACT.items()
            if not contract["persistent_dialogue_state"]
        }
        == {"no_memory"},
    )
    check(
        "only No-review removes review contribution",
        {
            name
            for name, contract in CONDITION_CONTRACT.items()
            if not contract["review_evidence_contribution"]
        }
        == {"no_review"},
    )

    persisted = create_tablet_environment_state()
    persisted.hard_constraints["budget"] = preference(
        "budget", "$500 maximum", "turn-1"
    )
    persisted.soft_constraints["note_taking"] = preference(
        "note_taking", "note taking matters", "turn-1"
    )
    full_state, full_rankings = condition_turn_inputs("full", persisted, [])
    no_review_state, no_review_rankings = condition_turn_inputs(
        "no_review", persisted, []
    )
    no_memory_state, no_memory_rankings = condition_turn_inputs(
        "no_memory", persisted, []
    )
    check(
        "Full and No-review receive identical persistent state",
        full_state == no_review_state == persisted
        and full_rankings == no_review_rankings == [],
    )
    check(
        "No-memory retains only environment state",
        no_memory_state.category is not None
        and no_memory_state.category.origin == "environment"
        and not no_memory_state.hard_constraints
        and not no_memory_state.soft_constraints
        and no_memory_rankings == [],
    )

    catalog = ExperimentalAmazonCatalog()
    check("real tablet catalog is available", catalog.available)
    query = generate_actual_query(persisted)
    products, reviews, _ = browse_actual_catalog(
        query,
        catalog,
        rejected_product_ids=set(),
        review_retriever=TokenReviewRetriever(per_product=5),
    )
    check("shared candidate set is non-empty", bool(products), len(products))
    full, full_reviews = rank_actual_products(
        persisted,
        query,
        products,
        reviews,
        result_limit=10,
    )
    no_review, visible_no_review = rank_products_without_review_contribution(
        persisted,
        query,
        products,
        reviews,
        result_limit=10,
    )
    check("Full produces review evidence", bool(full_reviews))
    check(
        "No-review exposes no review evidence",
        visible_no_review == []
        and all(not item.evidence_review_ids for item in no_review),
    )
    check(
        "No-review zeroes only review score fields before total",
        all(
            item.score.review_evidence_score == 0
            and item.score.evidence_reliability == 0
            and item.score.total
            == round(
                0.30 * item.score.hard_constraint_match
                + 0.30 * item.score.metadata_match
                + 0.20 * item.score.subjective_need_match
            )
            for item in no_review
        ),
    )
    check(
        "both rankers use only products from the identical candidate set",
        {item.product_id for item in [*full, *no_review]}
        <= {product.parent_asin for product in products},
    )

    print("Experiment-condition verification completed.")


if __name__ == "__main__":
    main()
