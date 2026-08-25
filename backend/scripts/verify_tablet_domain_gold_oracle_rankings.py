"""Verify the post-hoc Gold-State Oracle ranking artifacts without reranking."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.actual_recommendation import (  # noqa: E402
    product_satisfies_hard_filters,
)
from app.evaluation.tablet_domain_gold_oracle_rankings import (  # noqa: E402
    SCHEMA_VERSION,
    compact_oracle_result,
    render_oracle_markdown,
    sha256_file,
)
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402

GOLD = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
OFFICIAL_RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
OFFICIAL_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_holdout_official_v1.json"
)
RAW_REPORT = (
    BACKEND_ROOT
    / "reports"
    / "tablet_domain_gold_state_oracle_rankings_posthoc_v1.json"
)
RESULT = (
    BACKEND_ROOT
    / "data"
    / "results"
    / "tablet_domain_gold_state_oracle_rankings_posthoc_v1.json"
)
MARKDOWN = (
    BACKEND_ROOT
    / "docs"
    / "tablet_domain_gold_state_oracle_rankings_posthoc_v1.md"
)
MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_gold_state_oracle_rankings_posthoc_v1.json"
)


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[PASS] {label}")


def main() -> None:
    for path in (GOLD, OFFICIAL_RAW, OFFICIAL_MANIFEST, RAW_REPORT, RESULT, MARKDOWN, MANIFEST):
        check(path.is_file(), f"artifact exists: {path.relative_to(BACKEND_ROOT)}")

    official_manifest = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
    check(
        sha256_file(OFFICIAL_RAW)
        == official_manifest["git_excluded_artifacts"]["raw_report"]["sha256"],
        "official raw report remains immutable",
    )
    gold = load_tablet_holdout_dataset(GOLD)
    gold_by_id = {scenario.id: scenario for scenario in gold.scenarios}
    report = json.loads(RAW_REPORT.read_text(encoding="utf-8"))
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    check(report["schema_version"] == SCHEMA_VERSION, "raw report schema")
    check(report["status"] == "complete", "raw report completed")
    check(
        report["execution_contract"]["additional_llm_calls"] == 0
        and report["execution_contract"]["understanding_bypassed"]
        and report["execution_contract"]["response_composer_bypassed"],
        "zero-LLM oracle execution contract",
    )
    check(len(report["scenarios"]) == 20, "all 20 scenarios are present")
    check(
        {item["scenario_id"] for item in report["scenarios"]} == set(gold_by_id),
        "scenario IDs equal the frozen holdout",
    )

    catalog = ExperimentalAmazonCatalog()
    check(catalog.available, "frozen local catalog is available")
    for item in report["scenarios"]:
        scenario = gold_by_id[item["scenario_id"]]
        check(
            set(item["gold_state_ids"]) == set(scenario.final_gold_state_ids),
            f"{scenario.id}: final Gold State IDs",
        )
        check(
            item["gold_hard_filters"]
            == scenario.final_expected_hard_filters.model_dump(
                mode="json", exclude_none=True
            ),
            f"{scenario.id}: final Gold hard filters",
        )
        check(
            item["browse_summary"]["review_retrieval_method"]
            == "semantic_cross_encoder"
            and item["browse_summary"]["review_retrieval_fallback_reason"] is None,
            f"{scenario.id}: semantic retrieval without fallback",
        )
        rankings = item["oracle_top10"]
        check(
            [entry["rank"] for entry in rankings] == list(range(1, len(rankings) + 1)),
            f"{scenario.id}: contiguous ranking positions",
        )
        check(
            [entry["score"]["total"] for entry in rankings]
            == sorted(
                [entry["score"]["total"] for entry in rankings], reverse=True
            ),
            f"{scenario.id}: non-increasing total scores",
        )
        visible_review_ids = {
            review["review_id"] for review in item["oracle_visible_reviews"]
        }
        evidence_review_ids = {
            review_id
            for ranking in rankings
            for review_id in ranking["evidence_review_ids"]
        }
        check(
            evidence_review_ids == visible_review_ids,
            f"{scenario.id}: ranking evidence matches stored reviews",
        )
        violations = 0
        for product in item["oracle_top3_products"]:
            catalog_product = catalog.get_product(product["product_id"])
            violations += int(
                not product_satisfies_hard_filters(
                    catalog_product,
                    scenario.final_expected_hard_filters,
                    allow_budget_overrun=item["oracle_query"][
                        "allow_budget_overrun"
                    ],
                )
            )
        check(
            violations == item["oracle_top3_hard_filter_violations"] == 0,
            f"{scenario.id}: Oracle Top-3 satisfies Gold hard filters",
        )

    recomputed = compact_oracle_result(report)
    recomputed["source_artifacts"] = report["source_artifacts"]
    recomputed["runtime"] = report["runtime"]
    check(recomputed == result, "compact result reproduces from raw report")
    check(
        render_oracle_markdown(result) == MARKDOWN.read_text(encoding="utf-8"),
        "Markdown report reproduces from compact result",
    )
    check(
        manifest["poster_submission_preceded_analysis"]
        and not manifest["official_run_reexecuted"]
        and not manifest["new_gold_product_labels_added"],
        "post-hoc scope is explicit",
    )
    check(
        manifest["additional_llm_calls"] == 0
        and not manifest["recommendation_quality_claim_allowed"],
        "manifest forbids LLM and quality claims",
    )
    check(manifest["summary"] == report["summary"], "manifest summary matches raw")
    for payload in manifest["immutable_inputs"].values():
        path = BACKEND_ROOT / payload["path"]
        check(
            path.is_file() and sha256_file(path) == payload["sha256"],
            f"immutable input hash: {payload['path']}",
        )
    for payload in manifest["outputs"].values():
        path = BACKEND_ROOT / payload["path"]
        check(
            path.is_file() and sha256_file(path) == payload["sha256"],
            f"output hash: {payload['path']}",
        )
    for relative, payload in manifest["source_files"].items():
        path = BACKEND_ROOT / relative
        check(
            path.is_file() and sha256_file(path) == payload["sha256"],
            f"source hash: {relative}",
        )
    serialized = json.dumps(report, ensure_ascii=False).casefold()
    check(
        "llm_api_key" not in serialized
        and "luxia_api_key" not in serialized
        and "apikey" not in serialized,
        "no secret field names are present in the report",
    )
    print("Post-hoc Gold-State Oracle ranking verification completed.")


if __name__ == "__main__":
    main()
