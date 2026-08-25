"""Post-hoc final-state ranking diagnostic for the frozen tablet holdout.

This module does not create recommendation ground truth.  It injects the
pre-run Gold State annotation into the already implemented deterministic
Query/Browse/Review/Rank path and records the resulting oracle-conditioned
rankings.  Understanding and both response composers are intentionally absent,
so the analysis performs zero LLM calls.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.evaluation.actual_recommendation import product_satisfies_hard_filters
from app.evaluation.tablet_domain_automatic import (
    _apply_gold_turn_to_oracle_state,
)
from app.evaluation.tablet_domain_holdout import (
    TabletHoldoutDataset,
    TabletHoldoutScenario,
)
from app.evaluation.tablet_domain_official import active_state_ids
from app.experimental_catalog import ExperimentalAmazonCatalog
from app.models import DialogueState
from app.nodes.actual_policy import select_actual_policy
from app.nodes.actual_recommendation import (
    browse_actual_catalog,
    generate_actual_query,
    rank_actual_products,
)
from app.nodes.actual_state_manager import create_tablet_environment_state
from app.review_retrieval import ReviewEvidenceRetriever

SCHEMA_VERSION = "tablet-domain-gold-state-oracle-rankings-posthoc-v1"
ANALYSIS_LABEL = "post_hoc_secondary_gold_state_oracle_ranking_diagnostic"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reconstruct_final_gold_state(
    scenario: TabletHoldoutScenario,
) -> DialogueState:
    """Accumulate the frozen turn annotations into one final snapshot state."""

    state = create_tablet_environment_state()
    for gold_turn in scenario.turns:
        state = _apply_gold_turn_to_oracle_state(state, gold_turn)

    actual_ids = active_state_ids(state)
    expected_ids = set(scenario.final_gold_state_ids)
    if actual_ids != expected_ids:
        raise RuntimeError(
            f"{scenario.id}: reconstructed Gold State IDs differ from frozen final IDs"
        )
    query = generate_actual_query(state)
    if query.hard_filters != scenario.final_expected_hard_filters:
        raise RuntimeError(
            f"{scenario.id}: reconstructed hard filters differ from frozen final filters"
        )
    return state


def _final_condition_output(
    condition_payload: dict[str, Any], scenario: TabletHoldoutScenario
) -> dict[str, Any]:
    scenario_payload = next(
        item
        for item in condition_payload["scenarios"]
        if item["scenario_id"] == scenario.id
    )
    expected_turn_id = f"turn-{len(scenario.turns)}"
    turn = next(
        (
            item
            for item in scenario_payload.get("turns", [])
            if item.get("turn_id") == expected_turn_id
        ),
        None,
    )
    if turn is None:
        return {
            "status": "missing_final_turn",
            "completed_turn_count": scenario_payload["completed_turn_count"],
            "expected_turn_count": scenario_payload["expected_turn_count"],
            "lane": None,
            "top3_product_ids": [],
        }
    rankings = turn.get("rankings") or []
    lane = turn["policy"]["lane"]
    return {
        "status": "ranked" if rankings else lane,
        "completed_turn_count": scenario_payload["completed_turn_count"],
        "expected_turn_count": scenario_payload["expected_turn_count"],
        "lane": lane,
        "top3_product_ids": [item["product_id"] for item in rankings[:3]],
    }


def compare_lists(oracle_ids: list[str], actual_ids: list[str]) -> dict[str, Any]:
    if not oracle_ids or not actual_ids:
        return {
            "comparable": False,
            "top1_same": None,
            "ordered_top3_exact": None,
            "top3_set_exact": None,
            "overlap_count": None,
            "jaccard": None,
        }
    oracle_set = set(oracle_ids)
    actual_set = set(actual_ids)
    union = oracle_set | actual_set
    return {
        "comparable": True,
        "top1_same": oracle_ids[0] == actual_ids[0],
        "ordered_top3_exact": oracle_ids == actual_ids,
        "top3_set_exact": oracle_set == actual_set,
        "overlap_count": len(oracle_set & actual_set),
        "jaccard": len(oracle_set & actual_set) / len(union),
    }


def _product_record(product: Any) -> dict[str, Any]:
    return {
        "product_id": product.parent_asin,
        "title": product.title,
        "brand": product.brand,
        "price_usd": product.price_usd,
        "average_rating": product.average_rating,
        "rating_number": product.rating_number,
        "storage_gb": product.storage_gb,
        "memory_gb": product.memory_gb,
        "screen_inches": product.screen_inches,
        "weight_grams": product.weight_grams,
        "operating_system": product.operating_system,
    }


def run_gold_state_oracle_rankings(
    *,
    gold: TabletHoldoutDataset,
    official_raw: dict[str, Any],
    catalog: ExperimentalAmazonCatalog,
    review_retriever: ReviewEvidenceRetriever,
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    """Run the frozen deterministic recommender once per final Gold snapshot."""

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "analysis_label": ANALYSIS_LABEL,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "unit": "episode_final_state_snapshot",
            "gold_inputs": [
                "turn-level gold_candidate_ids and gold_candidate_scopes",
                "final_gold_state_ids",
                "final_expected_hard_filters",
            ],
            "product_trajectory_actions_applied": False,
            "tradeoff_relations_applied": False,
            "rejected_product_ids": "empty by final-snapshot contract",
        },
        "execution_contract": {
            "additional_llm_calls": 0,
            "understanding_bypassed": True,
            "response_composer_bypassed": True,
            "query_generator": "generate_actual_query",
            "catalog_browse": "browse_actual_catalog",
            "review_retrieval": "pinned local semantic bi-encoder plus Cross-Encoder",
            "product_ranker": "rank_actual_products",
            "result_limit": 10,
            "semantic_fallback_allowed": False,
        },
        "guardrail": (
            "Oracle-conditioned rankings are deterministic system outputs under "
            "annotated state, not Gold products, relevance labels, or recommendation "
            "quality ground truth."
        ),
        "scenarios": [],
    }

    for scenario in gold.scenarios:
        started = time.perf_counter()
        state = reconstruct_final_gold_state(scenario)
        policy = select_actual_policy(state)
        expected_lane = scenario.turns[-1].gold_policy_lane
        if policy.lane != expected_lane:
            raise RuntimeError(
                f"{scenario.id}: Gold-State policy {policy.lane} != {expected_lane}"
            )
        query = generate_actual_query(state)
        products, reviews, browse_summary = browse_actual_catalog(
            query,
            catalog,
            rejected_product_ids=set(),
            review_retriever=review_retriever,
        )
        if (
            browse_summary.review_retrieval_method != "semantic_cross_encoder"
            or browse_summary.review_retrieval_fallback_reason is not None
        ):
            raise RuntimeError(
                f"{scenario.id}: semantic review retrieval was not used without fallback"
            )
        rankings, visible_reviews = rank_actual_products(
            state,
            query,
            products,
            reviews,
            result_limit=10,
        )
        product_by_id = {product.parent_asin: product for product in products}
        top3 = rankings[:3]
        hard_violations = sum(
            not product_satisfies_hard_filters(
                product_by_id[item.product_id],
                scenario.final_expected_hard_filters,
                allow_budget_overrun=query.allow_budget_overrun,
            )
            for item in top3
        )
        if hard_violations:
            raise RuntimeError(
                f"{scenario.id}: Oracle Top-3 violates frozen Gold hard filters"
            )

        full = _final_condition_output(official_raw["conditions"]["full"], scenario)
        no_memory = _final_condition_output(
            official_raw["conditions"]["no_memory"], scenario
        )
        oracle_top3_ids = [item.product_id for item in top3]
        record = {
            "scenario_id": scenario.id,
            "title": scenario.title,
            "expected_final_turn": len(scenario.turns),
            "gold_state_ids": sorted(active_state_ids(state)),
            "gold_hard_filters": scenario.final_expected_hard_filters.model_dump(
                mode="json", exclude_none=True
            ),
            "oracle_policy_lane": policy.lane,
            "oracle_query": query.model_dump(mode="json"),
            "browse_summary": browse_summary.model_dump(mode="json"),
            "oracle_top10": [item.model_dump(mode="json") for item in rankings],
            "oracle_top3_products": [
                _product_record(product_by_id[item.product_id]) for item in top3
            ],
            "oracle_visible_reviews": [
                item.model_dump(mode="json") for item in visible_reviews
            ],
            "oracle_top3_hard_filter_violations": hard_violations,
            "full_final_output": full,
            "no_memory_final_output": no_memory,
            "oracle_vs_full": compare_lists(
                oracle_top3_ids, full["top3_product_ids"]
            ),
            "oracle_vs_no_memory": compare_lists(
                oracle_top3_ids, no_memory["top3_product_ids"]
            ),
            "elapsed_seconds": time.perf_counter() - started,
        }
        report["scenarios"].append(record)
        if checkpoint_path is not None:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(
            f"oracle: {scenario.id} top3={oracle_top3_ids} "
            f"reviews={browse_summary.review_retrieval_method} "
            f"elapsed={record['elapsed_seconds']:.1f}s",
            flush=True,
        )

    report["status"] = "complete"
    report["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = summarize_oracle_report(report)
    return report


def _comparison_summary(
    scenarios: list[dict[str, Any]], key: str
) -> dict[str, Any]:
    comparable = [item[key] for item in scenarios if item[key]["comparable"]]
    count = len(comparable)
    return {
        "comparable_scenario_count": count,
        "top1_agreement_rate": (
            sum(bool(item["top1_same"]) for item in comparable) / count
            if count
            else None
        ),
        "ordered_top3_exact_rate": (
            sum(bool(item["ordered_top3_exact"]) for item in comparable) / count
            if count
            else None
        ),
        "top3_set_exact_rate": (
            sum(bool(item["top3_set_exact"]) for item in comparable) / count
            if count
            else None
        ),
        "mean_top3_overlap_count": (
            sum(int(item["overlap_count"]) for item in comparable) / count
            if count
            else None
        ),
        "mean_top3_jaccard": (
            sum(float(item["jaccard"]) for item in comparable) / count
            if count
            else None
        ),
    }


def summarize_oracle_report(report: dict[str, Any]) -> dict[str, Any]:
    scenarios = report["scenarios"]
    return {
        "scenario_count": len(scenarios),
        "oracle_recommendation_count": sum(
            bool(item["oracle_top10"]) for item in scenarios
        ),
        "oracle_top3_product_count": sum(
            min(3, len(item["oracle_top10"])) for item in scenarios
        ),
        "oracle_top3_hard_filter_violations": sum(
            item["oracle_top3_hard_filter_violations"] for item in scenarios
        ),
        "semantic_retrieval_count": sum(
            item["browse_summary"]["review_retrieval_method"]
            == "semantic_cross_encoder"
            for item in scenarios
        ),
        "review_retrieval_fallback_count": sum(
            item["browse_summary"]["review_retrieval_fallback_reason"] is not None
            for item in scenarios
        ),
        "oracle_vs_full": _comparison_summary(scenarios, "oracle_vs_full"),
        "oracle_vs_no_memory": _comparison_summary(
            scenarios, "oracle_vs_no_memory"
        ),
        "quality_claim_allowed": False,
    }


def compact_oracle_result(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": report["status"],
        "analysis_label": report["analysis_label"],
        "created_at_utc": report["created_at_utc"],
        "completed_at_utc": report["completed_at_utc"],
        "scope": report["scope"],
        "execution_contract": report["execution_contract"],
        "guardrail": report["guardrail"],
        "summary": report["summary"],
        "scenarios": [
            {
                "scenario_id": item["scenario_id"],
                "title": item["title"],
                "gold_state_ids": item["gold_state_ids"],
                "gold_hard_filters": item["gold_hard_filters"],
                "oracle_query": item["oracle_query"],
                "oracle_top3": [
                    {
                        **product,
                        "rank": item["oracle_top10"][index]["rank"],
                        "score": item["oracle_top10"][index]["score"],
                        "evidence_review_ids": item["oracle_top10"][index][
                            "evidence_review_ids"
                        ],
                    }
                    for index, product in enumerate(item["oracle_top3_products"])
                ],
                "full_final_output": item["full_final_output"],
                "no_memory_final_output": item["no_memory_final_output"],
                "oracle_vs_full": item["oracle_vs_full"],
                "oracle_vs_no_memory": item["oracle_vs_no_memory"],
            }
            for item in report["scenarios"]
        ],
    }


def _metric(value: float | None) -> str:
    if value is None or not math.isfinite(value):
        return "N/A"
    return f"{value:.3f}"


def render_oracle_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    full = summary["oracle_vs_full"]
    no_memory = summary["oracle_vs_no_memory"]
    rows = []
    for item in result["scenarios"]:
        oracle_ids = [entry["product_id"] for entry in item["oracle_top3"]]
        rows.append(
            "| {scenario} | {oracle} | {full_ids} | {full_j} | {memory_ids} | {memory_j} |".format(
                scenario=item["scenario_id"],
                oracle=" → ".join(f"`{value}`" for value in oracle_ids) or "—",
                full_ids=" → ".join(
                    f"`{value}`"
                    for value in item["full_final_output"]["top3_product_ids"]
                )
                or item["full_final_output"]["status"],
                full_j=_metric(item["oracle_vs_full"]["jaccard"]),
                memory_ids=" → ".join(
                    f"`{value}`"
                    for value in item["no_memory_final_output"]["top3_product_ids"]
                )
                or item["no_memory_final_output"]["status"],
                memory_j=_metric(item["oracle_vs_no_memory"]["jaccard"]),
            )
        )
    return "\n".join(
        [
            "# Post-hoc Gold-State Oracle recommendation analysis",
            "",
            "상태: **사후 보조 진단 완료**",
            "",
            (
                "에피소드 종료 Gold-State를 동결된 Query→Browse→semantic review "
                "retrieval→Rank 경로에 주입했다. Understanding과 Response Composer를 "
                "호출하지 않았으므로 추가 LLM 호출은 0회다."
            ),
            "",
            "> 이 결과는 Gold 추천 상품이나 relevance 정답이 아니다. 현재 결정론적 recommender가 annotated state를 받았을 때의 oracle-conditioned 출력이다.",
            "",
            "## Summary",
            "",
            "| Comparison | Comparable episodes | Top-1 agreement | Exact Top-3 order | Exact Top-3 set | Mean overlap | Mean Jaccard |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            (
                f"| Oracle vs Full | {full['comparable_scenario_count']} | "
                f"{_metric(full['top1_agreement_rate'])} | "
                f"{_metric(full['ordered_top3_exact_rate'])} | "
                f"{_metric(full['top3_set_exact_rate'])} | "
                f"{_metric(full['mean_top3_overlap_count'])} | "
                f"{_metric(full['mean_top3_jaccard'])} |"
            ),
            (
                f"| Oracle vs No-memory | {no_memory['comparable_scenario_count']} | "
                f"{_metric(no_memory['top1_agreement_rate'])} | "
                f"{_metric(no_memory['ordered_top3_exact_rate'])} | "
                f"{_metric(no_memory['top3_set_exact_rate'])} | "
                f"{_metric(no_memory['mean_top3_overlap_count'])} | "
                f"{_metric(no_memory['mean_top3_jaccard'])} |"
            ),
            "",
            f"- Oracle recommendation episodes: {summary['oracle_recommendation_count']}/{summary['scenario_count']}",
            f"- Oracle Top-3 Gold hard-filter violations: {summary['oracle_top3_hard_filter_violations']}/{summary['oracle_top3_product_count']}",
            f"- Semantic retrieval without fallback: {summary['semantic_retrieval_count']}/{summary['scenario_count']}",
            "",
            "## Episode-level final lists",
            "",
            "| Episode | Gold-State Oracle Top-3 | Full final Top-3 | Jaccard | No-memory final Top-3 | Jaccard |",
            "| --- | --- | --- | ---: | --- | ---: |",
            *rows,
            "",
            "## Interpretation boundary",
            "",
            "Oracle와의 일치는 state-to-ranking 전달 충실도 진단이다. 인간 relevance, 만족도, Product NDCG 또는 추천 품질 우위를 의미하지 않는다. 이 분석은 포스터 제출 뒤 수행된 post-hoc secondary analysis이며 기존 official primary benchmark를 변경하지 않는다.",
            "",
        ]
    )
