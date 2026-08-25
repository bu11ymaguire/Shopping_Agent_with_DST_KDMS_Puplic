"""Open and analyse the SEGSE confirmatory result after both runs completed.

Three analyses, each with its own claim boundary:

    A  end-to-end        the frozen v1.4 system on untouched data
    B  fixed-upstream    the v1.3 to v1.4 deterministic contract change, same proposals
    C  test-retest       provider nondeterminism of the identical method on identical data

The pre-registered decision rule is comparative and rests on Analysis B plus the
guardrails. Absolute numbers are reported without a threshold.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.segse_final_state_closeness import (  # noqa: E402
    aggregate_closeness,
    closeness_from_counts,
    paired_closeness_improvement,
)
from app.evaluation.tablet_domain_segse_confirmatory import (  # noqa: E402
    load_segse_confirmatory_dataset,
)
from app.llm import write_report  # noqa: E402
from app.models import DialogueState  # noqa: E402
from app.nodes.actual_state_manager import create_tablet_environment_state  # noqa: E402
from app.segse_experiment import update_segse_dialogue_state  # noqa: E402
from app.segse_experiment_v12 import SEGSEV12ProposalOutput  # noqa: E402
from app.segse_experiment_v13 import SEGSEV13UnderstandingProvider  # noqa: E402
from app.segse_experiment_v14 import SEGSEV14UnderstandingProvider  # noqa: E402
from app.evaluation.tablet_domain_segse import active_final_tokens  # noqa: E402

DEFAULT_RAW = BACKEND_ROOT / "reports" / "segse_confirmatory_v1.json"
DEFAULT_HOLDOUT = (
    BACKEND_ROOT / "data" / "tablet_domain_segse_confirmatory_holdout_v1.json"
)
DEFAULT_PROTOCOL = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_v1_protocol.json"
)
DEFAULT_SUMMARY = BACKEND_ROOT / "data" / "results" / "segse_confirmatory_v1.json"
BOOTSTRAP_SAMPLES = 10000
BOOTSTRAP_SEED = 20260819

CORRECTION_LABELS = {
    "value_correction": {"update_value"},
    "scope_correction": {"update_scope"},
    "refinement": {"refine"},
    "retract": {"retract"},
    "reactivation": {"reactivate"},
}


class _RecordedClient:
    def __init__(self, output: SEGSEV12ProposalOutput) -> None:
        self.output = output

    async def generate_structured(self, **_: Any) -> SEGSEV12ProposalOutput:
        return self.output


def _state_summary(state: DialogueState) -> dict[str, Any]:
    return {
        "category": state.category.model_dump(mode="json") if state.category else None,
        "hard_constraints": {
            k: v.model_dump(mode="json") for k, v in state.hard_constraints.items()
        },
        "soft_constraints": {
            k: v.model_dump(mode="json") for k, v in state.soft_constraints.items()
        },
        "subjective_needs": state.subjective_needs.model_dump(mode="json"),
        "current_item_rank_context": state.current_item,
        "visible_ranked_products": [],
    }


def _operation_recall(turns: list[dict[str, Any]], labels: set[str]) -> dict[str, Any]:
    gold_total = 0
    matched = 0
    for item in turns:
        predicted = set(item["actual_material_operations"])
        targets = [
            pair
            for pair in item["gold_material_operations"]
            if pair.rsplit("|", 1)[1] in labels
        ]
        gold_total += len(targets)
        matched += sum(pair in predicted for pair in targets)
    return {
        "numerator": matched,
        "denominator": gold_total,
        "recall": round(matched / gold_total, 6) if gold_total else None,
        "reported_as": f"{matched}/{gold_total}",
    }


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(p, 6),
        "recall": round(r, 6),
        "f1": round(2 * p * r / (p + r), 6) if p + r else 0.0,
    }


def _final_state_table(records: list[dict[str, Any]]) -> dict[str, Any]:
    tp = sum(int(r["final_state_counts"]["true_positive"]) for r in records)
    fp = sum(int(r["final_state_counts"]["false_positive"]) for r in records)
    fn = sum(int(r["final_state_counts"]["false_negative"]) for r in records)
    return _prf(tp, fp, fn)


def _closeness_by_episode(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        r["scenario_id"]: closeness_from_counts(r["final_state_counts"])
        for r in records
    }


def _paired_bootstrap(deltas: list[float]) -> dict[str, Any]:
    rng = random.Random(BOOTSTRAP_SEED)
    n = len(deltas)
    point = sum(deltas) / n if n else 0.0
    samples = []
    for _ in range(BOOTSTRAP_SAMPLES):
        draw = [deltas[rng.randrange(n)] for _ in range(n)]
        samples.append(sum(draw) / n)
    samples.sort()
    lo = samples[int(0.025 * len(samples))]
    hi = samples[min(int(0.975 * len(samples)), len(samples) - 1)]
    return {
        "point": round(point, 6),
        "ci_lower_95": round(lo, 6),
        "ci_upper_95": round(hi, 6),
        "samples": BOOTSTRAP_SAMPLES,
        "seed": BOOTSTRAP_SEED,
        "episodes": n,
    }


def _recorded_turns(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    turns: dict[str, dict[str, Any]] = {}
    for scenario in run["scenarios"]:
        previous = create_tablet_environment_state()
        for entry in sorted(scenario["turns"], key=lambda item: item["gold_turn"]):
            details = entry["understanding_details"]
            turns[f'{scenario["scenario_id"]}t{entry["gold_turn"]}'] = {
                "scenario_id": scenario["scenario_id"],
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
            previous = DialogueState.model_validate(
                entry["pipeline"]["dialogue_state"]
            )
    return turns


async def _replay_arm(factory: Any, dataset: Any, recorded: dict[str, Any]) -> dict[str, Any]:
    """Replay recorded proposals through one deterministic pipeline, per episode."""

    finals: dict[str, set[str]] = {}
    operations: dict[str, dict[str, str]] = {}
    for scenario in dataset.scenarios:
        state = create_tablet_environment_state()
        for gold_turn in scenario.turns:
            key = f"{scenario.id}t{gold_turn.turn}"
            record = recorded.get(key)
            if record is None:
                continue
            # The sanitized item action shares its field set with the raw proposal
            # shape, so it replays as recorded rather than being dropped.
            proposal = SEGSEV12ProposalOutput.model_validate(
                {
                    "utterance": record["utterance"],
                    "domain_route": record["domain_route"],
                    "unsupported_category_text": record["unsupported_category_text"],
                    "unsupported_category_evidence": record[
                        "unsupported_category_evidence"
                    ],
                    "intents": record["intents"],
                    "state_events": record["raw_proposal_events"],
                    "item_action": record["item_action"],
                    "tradeoff": record["tradeoff"],
                }
            )
            provider = factory(_RecordedClient(proposal))
            understanding = await provider(
                utterance=record["utterance"],
                previous_state_summary=_state_summary(state),
                conversation_id="segse-confirmatory-replay",
                turn=gold_turn.turn,
            )
            state, _ = update_segse_dialogue_state(
                state, understanding, [], turn_id=key  # type: ignore[arg-type]
            )
            operations[key] = {
                item.canonical_id: item.operation
                for item in understanding.segse_material_operations
            }
        finals[scenario.id] = active_final_tokens(state)
    return {"finals": finals, "operations": operations}


def _closeness_from_finals(
    dataset: Any, finals: dict[str, set[str]]
) -> dict[str, dict[str, Any]]:
    gold = {s.id: set(s.final_gold_active_tokens) for s in dataset.scenarios}
    return {
        sid: closeness_from_counts(
            {"gold": sorted(gold[sid]), "predicted": sorted(finals.get(sid, set()))}
        )
        for sid in gold
    }


def _run_disagreement(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def index(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            f'{s["scenario_id"]}t{m["turn"]}': m
            for s in run["scenarios"]
            for m in s["turn_metrics"]
        }

    left, right = index(a), index(b)
    shared = sorted(set(left) & set(right))
    raw_diff = candidate_diff = op_diff = 0
    for key in shared:
        if (left[key]["raw_event_pairs"] or []) != (right[key]["raw_event_pairs"] or []):
            raw_diff += 1
        if left[key]["raw_candidate"]["predicted"] != right[key]["raw_candidate"]["predicted"]:
            candidate_diff += 1
        if left[key]["actual_material_operations"] != right[key]["actual_material_operations"]:
            op_diff += 1
    am, bm = a["metrics"], b["metrics"]
    return {
        "compared_turns": len(shared),
        "raw_structured_proposal_exact_match_disagreement": raw_diff,
        "candidate_set_disagreement": candidate_diff,
        "material_operation_disagreement": op_diff,
        "candidate_fp_difference": bm["candidate_fp_count"] - am["candidate_fp_count"],
        "c2u_fp_difference": bm["c2u_fp_count"] - am["c2u_fp_count"],
        "correction_recall_difference": round(
            bm["correction_recall"]["recall"] - am["correction_recall"]["recall"], 6
        ),
        "material_state_diff_f1_difference": round(
            bm["material_delta"]["f1"] - am["material_delta"]["f1"], 6
        ),
        "final_state_f1_difference": round(
            bm["scenario_final_state"]["f1"] - am["scenario_final_state"]["f1"], 6
        ),
        "turn_completion_difference": round(
            bm["turn_output_completion_rate"] - am["turn_output_completion_rate"], 6
        ),
        "pooling_rule": (
            "Repeated measurements of the same 80 turns. Stability analysis only; "
            "never averaged, never pooled as independent samples, never used to "
            "redefine Run A."
        ),
    }


async def execute(args: argparse.Namespace) -> None:
    raw = json.loads(args.raw.resolve().read_text(encoding="utf-8"))
    if raw["status"] != "completed":
        raise RuntimeError("both runs must complete before results are opened")
    protocol = json.loads(args.protocol.resolve().read_text(encoding="utf-8"))
    dataset = load_segse_confirmatory_dataset(args.holdout.resolve())
    summary_output = args.summary_output.resolve()
    if summary_output.exists():
        raise RuntimeError("summary exists; refusing to overwrite")

    run_a = raw["runs"]["run_a_primary"]
    run_b = raw["runs"]["run_b_test_retest"]
    turns_a = [t for s in run_a["scenarios"] for t in s["turn_metrics"]]

    # --- Analysis A -------------------------------------------------------
    closeness_a = _closeness_by_episode(run_a["scenarios"])
    analysis_a = {
        "claim": "the actual confirmatory performance of the whole frozen v1.4 system",
        "final_state_table": _final_state_table(run_a["scenarios"]),
        "final_state_closeness": aggregate_closeness(list(closeness_a.values())),
        "per_episode_closeness": closeness_a,
        "material_state_diff": run_a["metrics"]["material_delta"],
        "material_operation": run_a["metrics"]["material_operation"],
        "turnwise_accumulated_state": run_a["metrics"]["turnwise_accumulated_state"],
        "candidate": run_a["metrics"]["raw_candidate"],
        "candidate_fp_count": run_a["metrics"]["candidate_fp_count"],
        "c2u_fp_count": run_a["metrics"]["c2u_fp_count"],
        "novel_candidate_fp_count": run_a["metrics"]["novel_candidate_fp_count"],
        "forbidden_prediction_count": run_a["metrics"]["forbidden_prediction_count"],
        "correction_recall_by_operation": {
            label: _operation_recall(turns_a, labels)
            for label, labels in CORRECTION_LABELS.items()
        },
        "combined_value_and_scope_correction": _operation_recall(
            turns_a, {"update_value", "update_scope"}
        ),
        "confirmation_metadata_recall": run_a["metrics"][
            "confirmation_metadata_recall"
        ],
        "evidence_span_validity": run_a["metrics"]["lexical_evidence_validity"],
        "policy_lane_accuracy": run_a["metrics"]["policy_lane_accuracy"],
        "hard_filter_completion_rate": run_a["metrics"]["hard_filter_completion_rate"],
        "turn_output_completion_rate": run_a["metrics"]["turn_output_completion_rate"],
        "recommendation_pipeline_completion_rate": run_a["metrics"][
            "recommendation_pipeline_completion_rate"
        ],
        "top3_hard_constraint_violation_rate": run_a["metrics"][
            "top3_hard_constraint_violation_rate"
        ],
        "final_fp_origin_counts": run_a["metrics"]["first_final_fp_origin_counts"],
        "dimension_attribution_turns": [
            {
                "turn_id": f'{item["scenario_id"]}t{item["turn"]}',
                "confusion_family": item.get("dimension_confusion_family"),
                "forbidden_predicted_ids": item["forbidden_predicted_ids"],
                "candidate_fp_ids": item["candidate_fp_ids"],
                "clean": not item["forbidden_predicted_ids"]
                and not item["candidate_fp_ids"],
            }
            for item in turns_a
            if item.get("dimension_confusion_family")
        ],
    }

    # --- Analysis B -------------------------------------------------------
    recorded = _recorded_turns(run_a)
    v13 = await _replay_arm(SEGSEV13UnderstandingProvider, dataset, recorded)
    v14 = await _replay_arm(SEGSEV14UnderstandingProvider, dataset, recorded)
    closeness_v13 = _closeness_from_finals(dataset, v13["finals"])
    closeness_v14 = _closeness_from_finals(dataset, v14["finals"])
    paired = paired_closeness_improvement(closeness_v13, closeness_v14)
    jaccard_deltas = [
        item["jaccard_improvement"] for item in paired["per_episode"]
    ]
    error_deltas = [
        float(item["error_token_reduction"]) for item in paired["per_episode"]
    ]

    def replay_correction(operations: dict[str, dict[str, str]]) -> dict[str, Any]:
        gold_total = 0
        matched = 0
        for scenario in dataset.scenarios:
            state = create_tablet_environment_state()
            for gold_turn in scenario.turns:
                key = f"{scenario.id}t{gold_turn.turn}"
                case = gold_turn.as_case(scenario.id)
                from app.evaluation.tablet_domain_segse import (
                    expected_state,
                    gold_material_operations,
                )

                gold_ops = gold_material_operations(case, state)
                state = expected_state(case, state)
                targets = {
                    cid: op
                    for cid, op in gold_ops.items()
                    if op in {"update_value", "update_scope"}
                }
                gold_total += len(targets)
                produced = operations.get(key, {})
                matched += sum(
                    produced.get(cid) == op for cid, op in targets.items()
                )
        return {
            "numerator": matched,
            "denominator": gold_total,
            "recall": round(matched / gold_total, 6) if gold_total else None,
            "reported_as": f"{matched}/{gold_total}",
        }

    analysis_b = {
        "claim": (
            "the deterministic interpretation-layer effect of the v1.3 to v1.4 "
            "contract change under identical raw proposals"
        ),
        "does_not_claim": [
            "the causal effect of the whole v1.4 method",
            "how often the upstream emits the proposal that makes the contract matter",
        ],
        "additional_llm_calls": 0,
        "v13_final_state_closeness": aggregate_closeness(list(closeness_v13.values())),
        "v14_final_state_closeness": aggregate_closeness(list(closeness_v14.values())),
        "paired_improvement": {
            k: v for k, v in paired.items() if k != "per_episode"
        },
        "per_episode": paired["per_episode"],
        "paired_bootstrap_jaccard": _paired_bootstrap(jaccard_deltas),
        "paired_bootstrap_error_token_reduction": _paired_bootstrap(error_deltas),
        "value_and_scope_correction_recall": {
            "v13": replay_correction(v13["operations"]),
            "v14": replay_correction(v14["operations"]),
        },
    }

    # --- Analysis C -------------------------------------------------------
    closeness_b = _closeness_by_episode(run_b["scenarios"])
    analysis_c = {
        "claim": "provider-level nondeterminism of the frozen method on identical data",
        "does_not_claim": [
            "any primary pass or fail decision",
            "a better or averaged performance number",
        ],
        "run_b_final_state_table": _final_state_table(run_b["scenarios"]),
        "run_b_final_state_closeness": aggregate_closeness(list(closeness_b.values())),
        "disagreement": _run_disagreement(run_a, run_b),
    }

    # --- Decision rule ----------------------------------------------------
    rule = protocol["decision_rule"]["pass_requires_all_of"]
    guardrails = {
        "raw_candidate_recall": run_a["metrics"]["raw_candidate"]["recall"],
        "retract_recall": run_a["metrics"]["retract_recall"]["recall"],
        "hard_filter_completion_rate": run_a["metrics"]["hard_filter_completion_rate"],
        "turn_output_completion_rate": run_a["metrics"]["turn_output_completion_rate"],
    }
    v13_corr = analysis_b["value_and_scope_correction_recall"]["v13"]["recall"] or 0.0
    v14_corr = analysis_b["value_and_scope_correction_recall"]["v14"]["recall"] or 0.0
    checks = {
        "paired_final_state_closeness_improves": (
            analysis_b["paired_bootstrap_jaccard"]["point"] > 0
            and analysis_b["paired_bootstrap_jaccard"]["ci_lower_95"] > 0
        ),
        "regression_is_bounded": paired["episodes_worsened"] <= 2,
        "improvement_is_broad": paired["episodes_improved"] >= 8,
        "correction_recall_recovered": v14_corr > v13_corr,
        "guardrails_hold": all(
            value is not None and value > 0 for value in guardrails.values()
        ),
    }
    decision = {
        "rule": rule,
        "checks": checks,
        "guardrail_values": guardrails,
        "status": (
            "confirmatory_pass" if all(checks.values()) else "confirmatory_fail"
        ),
        "claim_boundary": (
            "Untouched holdout, method and protocol frozen by hash before the run. "
            "Supports a state-update correctness claim only."
        ),
    }

    summary = {
        "schema_version": "segse-confirmatory-v1-summary-v1",
        "run_id": raw["run_id"],
        "study_status": raw["study_status"],
        "version_control": raw["version_control"],
        "freezes_verified_before_run": raw["freezes_verified_before_run"],
        "freezes_verified_after_run": raw["freezes_verified_after_run"],
        "holdout": raw["holdout"],
        "runtime": raw["runtime"],
        "catalog": raw["catalog"],
        "run_roles": raw["run_roles"],
        "analysis_a_end_to_end": analysis_a,
        "analysis_b_fixed_upstream": analysis_b,
        "analysis_c_test_retest": analysis_c,
        "decision": decision,
        "run_a_metrics": run_a["metrics"],
        "run_b_metrics": run_b["metrics"],
        "limitations": raw["limitations"],
    }
    write_report(summary_output, summary)
    print(
        json.dumps(
            {
                "final_state_table_run_a": analysis_a["final_state_table"],
                "final_state_closeness_run_a": analysis_a["final_state_closeness"],
                "correction_by_operation": analysis_a["correction_recall_by_operation"],
                "paired_improvement": analysis_b["paired_improvement"],
                "paired_bootstrap_jaccard": analysis_b["paired_bootstrap_jaccard"],
                "test_retest": analysis_c["disagreement"],
                "decision": {"status": decision["status"], "checks": decision["checks"]},
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print(f"summary_report={summary_output}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--summary-output", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
