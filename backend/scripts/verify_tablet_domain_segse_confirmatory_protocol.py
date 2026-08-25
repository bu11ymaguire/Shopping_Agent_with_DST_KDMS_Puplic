"""Verify the frozen SEGSE confirmatory protocol before the holdout is authored."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.segse_v14_freeze import method_fingerprint  # noqa: E402


PROTOCOL = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_v1_protocol.json"
)
FREEZE = BACKEND_ROOT / "data" / "manifests" / "segse_v14_method_freeze.json"
DOC = (
    BACKEND_ROOT / "docs" / "tablet_domain_segse_confirmatory_protocol_v1.md"
)


def check(label: str, condition: bool, detail: object = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"[ok] {label}")


def main() -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))

    check("protocol document exists", DOC.exists())
    check(
        "protocol is frozen before data and before any run",
        protocol["status"]
        == "protocol_frozen_before_holdout_authoring_and_before_any_confirmatory_run"
        and protocol["holdout_exists"] is False
        and protocol["any_confirmatory_run_completed"] is False
        and protocol["confirmatory_evidence"] is False,
    )
    check(
        "protocol pins the frozen method fingerprint",
        protocol["evaluated_method"]["combined_method_sha256"]
        == freeze["method"]["combined_method_sha256"]
        == method_fingerprint()["combined_method_sha256"],
    )
    check(
        "the method is re-verified around the run",
        protocol["evaluated_method"]["reverify_before_and_after_run"] is True,
    )
    check(
        "exactly one primary question",
        isinstance(protocol["primary_question"], str)
        and protocol["primary_question"].count("?") == 1,
    )
    analyses = protocol["analyses"]
    check(
        "Analysis A runs live exactly once",
        analyses["A_end_to_end"]["runs"] == 1
        and "temperature 0" in analyses["A_end_to_end"]["upstream"],
    )
    check(
        "Analysis B is offline and paired",
        analyses["B_fixed_upstream_paired"]["additional_llm_calls"] == 0
        and set(analyses["B_fixed_upstream_paired"]["downstream"])
        == {"v1.3 deterministic pipeline", "v1.4 deterministic pipeline"},
    )
    check(
        "Analysis B claims only the interpretation-layer effect",
        "interpretation-layer effect"
        in analyses["B_fixed_upstream_paired"]["supports"]
        and "the causal effect of the whole v1.4 method"
        in analyses["B_fixed_upstream_paired"]["does_not_support"],
    )
    boundaries = analyses["claim_boundaries"]
    check(
        "each analysis has an explicit claim boundary",
        "whole frozen v1.4 system" in boundaries["A_end_to_end"]
        and "identical raw proposal" in boundaries["B_fixed_upstream"]
        and "provider-level nondeterminism" in boundaries["C_test_retest"],
    )
    decomposition = analyses["provider_variation_decomposition"]
    check(
        "provider variance has a measurement and a stated reason it needs one",
        decomposition["in_scope"] is True
        and "repeating the identical frozen method"
        in decomposition["reason_it_needs_a_second_run"]
        and "5 of 24" in decomposition["known_magnitude_from_development"]
        and "without a measurement of it" in decomposition["forbidden_inference"],
    )
    check(
        "Analysis B validity rests on prompt and schema equality",
        "byte-for-byte" in analyses["B_fixed_upstream_paired"]["valid_because"]
        and freeze["method"]["prompt_sha256"]
        == method_fingerprint()["prompt_sha256"],
    )
    check(
        "claims must name their analysis",
        analyses["claims_must_state_which_analysis"] is True,
    )
    rule = protocol["denominator_rule"]
    check(
        "missing and malformed turns stay in the denominator",
        rule["count_every_authored_turn"] is True
        and rule["missing_output_is_failure"] is True
        and rule["schema_failure_is_failure"] is True
        and rule["successful_turn_only_denominator_forbidden"] is True,
    )
    outcome = protocol["primary_outcome"]
    check(
        "primary outcome is graded closeness to the Gold State",
        outcome["name"] == "final_dst_closeness_to_gold_state"
        and outcome["graded_not_binary"] is True
        and set(outcome["closeness_measures"]) == {"error_tokens", "jaccard", "micro_f1"},
    )
    check(
        "exact match is reported but demoted",
        outcome["exact_match_role"]["reported"] is True
        and outcome["exact_match_role"]["is_the_headline"] is False
        and "0/6 exact" in outcome["exact_match_role"]["reason_demoted"],
    )
    paired = protocol["paired_improvement_primary"]
    check(
        "paired improvement over v1.3 is a primary and comes from Analysis B",
        paired["analysis"] == "B_fixed_upstream_paired"
        and paired["paired_unit"] == "one episode, 20 pairs"
        and "episodes improved, unchanged, and worsened" in paired["reported"],
    )
    check(
        "the optional reference ladder stays off until explicitly enabled",
        paired["reference_ladder_optional"]["enabled"] is False,
    )
    rule = protocol["decision_rule"]
    check(
        "the confirmatory claim is comparative, not absolute",
        rule["the_confirmatory_claim_is_comparative_not_absolute"] is True
        and "untouched measurement of this method exists"
        in rule["why_no_absolute_threshold"],
    )
    check(
        "pass requires paired improvement, bounded regression, and guardrails",
        set(rule["pass_requires_all_of"])
        == {
            "paired_final_state_closeness_improves",
            "regression_is_bounded",
            "improvement_is_broad",
            "correction_recall_recovered",
            "guardrails_hold",
        }
        and "CI lower bound is above 0"
        in rule["pass_requires_all_of"]["paired_final_state_closeness_improves"],
    )
    check(
        "success from exact match alone or a single F1 is forbidden",
        "declaring success from exact match alone" in rule["forbidden"]
        and "declaring success from an aggregate F1 alone" in rule["forbidden"]
        and "moving a threshold after seeing the result" in rule["forbidden"],
    )
    calibration = protocol["development_calibration_reference"]
    check(
        "development calibration is labelled as neither threshold nor evidence",
        calibration["not_a_threshold"] is True
        and calibration["not_confirmatory_evidence"] is True
        and calibration["paired_v13_to_v14"]["worsened"] == 0,
    )
    roles = protocol["run_roles"]
    check(
        "Run A primary and Run B robustness roles are fixed verbatim",
        roles["governing_statement"].startswith(
            "Run A is the sole primary confirmatory evaluation."
        )
        and "shall not replace, average with, or retroactively redefine Run A"
        in roles["governing_statement"],
    )
    check(
        "Run B is enabled and cannot decide pass or fail",
        roles["run_b"]["enabled"] is True
        and roles["run_b"]["decides_pass_or_fail"] is False
        and roles["run_a"]["decides_pass_or_fail"] is True
        and roles["run_a"]["is_the_reported_headline"] is True,
    )
    order = roles["execution_order_rule"]
    check(
        "both runs execute before either result is opened",
        order["open_run_a_before_run_b_completes"] is False
        and order["ordered"].index("execute Run B")
        > order["ordered"].index("do not open Run A results")
        and order["ordered"][-1] == "open and analyse results"
        and protocol["execution_contract"][
            "both_runs_execute_before_any_result_is_opened"
        ]
        is True,
    )
    check(
        "averaging, cherry-picking, and pooling the two runs are forbidden",
        any("averaging" in item for item in roles["forbidden_operations"])
        and any("whichever run is better" in item for item in roles["forbidden_operations"])
        and any("independent samples" in item for item in roles["forbidden_operations"])
        and "never as independent samples" in roles["pooling_rule"],
    )
    check(
        "the two-run disagreement report is predeclared",
        len(roles["reported_disagreements_between_runs"]) >= 8
        and "raw_structured_proposal_exact_match_disagreement"
        in roles["reported_disagreements_between_runs"],
    )
    check(
        "Analysis C exists and cannot decide pass or fail",
        analyses["C_test_retest_provider_stability"]["role"]
        == "pre-registered robustness analysis only"
        and "any primary PASS or FAIL decision"
        in analyses["C_test_retest_provider_stability"]["does_not_support"],
    )
    check(
        "provider variation is now measured rather than assumed",
        analyses["provider_variation_decomposition"]["in_scope"] is True
        and analyses["provider_variation_decomposition"]["measured_by"]
        == "C_test_retest_provider_stability",
    )
    primary = protocol["primary_metrics"]
    check(
        "graded closeness and paired improvement are primary metric groups",
        "mean_per_episode_error_tokens" in primary["final_state_closeness"]
        and "mean_per_episode_jaccard" in primary["final_state_closeness"]
        and "episodes_worsened"
        in primary["final_state_closeness_improvement_over_v13"]
        and "paired_bootstrap_95_ci"
        in primary["final_state_closeness_improvement_over_v13"],
    )
    check(
        "the headline may not be exact match alone",
        protocol["reporting_requirements"][
            "headline_is_graded_closeness_and_paired_improvement"
        ]["exact_match_may_not_stand_alone"]
        is True,
    )
    check(
        "false-update and correction metrics are both primary",
        "candidate_fp_count" in primary["false_update"]
        and "c2u_fp_count" in primary["false_update"]
        and "value_correction_recall" in primary["correction"]
        and "scope_correction_recall" in primary["correction"],
    )
    check(
        "material behavior is primary alongside persistence",
        "material_delta_f1" in primary["material_behavior"]
        and "material_operation_f1" in primary["material_behavior"]
        and "scenario_final_state_f1" in primary["persistence"],
    )
    check(
        "guardrails cover recall, deletion, filters, and reliability",
        set(protocol["guardrail_metrics"])
        == {
            "raw_candidate_recall",
            "retract_recall",
            "hard_filter_completion_rate",
            "turn_output_completion_rate",
        },
    )
    check(
        "a guardrail regression fails the claim",
        "fails the confirmatory claim" in protocol["guardrail_rule"],
    )
    disclosure = protocol["persistence_amplification_disclosure"]
    check(
        "persistence amplification disclosure is mandatory",
        disclosure["required"] is True
        and "amplified by persistence" in disclosure["statement"]
        and "per-decision view" in disclosure["statement"],
    )
    check(
        "provenance and relevance are declared not evaluable",
        set(protocol["not_evaluable"])
        >= {"provenance_accuracy", "product_relevance", "review_relevance"},
    )
    rules = protocol["holdout_authoring_rules"]
    check(
        "holdout is authored after the freeze and frozen before execution",
        rules["author_after_method_freeze"] is True
        and "utterances" in rules["freeze_before_execution"]
        and "gold accumulated state" in rules["freeze_before_execution"],
    )
    check(
        "products, rankings, and human grades are never authored",
        set(rules["never_authored"])
        == {"recommended products", "system rankings", "human relevance grades"},
    )
    check(
        "every frozen utterance source is listed and exists",
        len(rules["forbidden_utterance_sources"]) == 5
        and all(
            (BACKEND_ROOT / relative).exists()
            for relative in rules["forbidden_utterance_sources"]
        ),
        [
            relative
            for relative in rules["forbidden_utterance_sources"]
            if not (BACKEND_ROOT / relative).exists()
        ],
    )
    check(
        "the development sentence is forbidden and its family is redirected",
        rules["explicitly_forbidden_sentence"]
        == "A microSD slot that accepts large cards would be convenient."
        and rules["development_failure_families_included_as_new_surface_forms_only"]
        is True
        and "no note_taking event" in rules["expansion_storage_family_gold_rule"],
    )
    check(
        "gold self-consistency is machine-checked before the freeze",
        len(rules["machine_checked_before_freeze"]) == 4
        and rules["deterministic_overlap_check_before_freeze"] is True,
    )
    size = protocol["holdout_size"]
    check(
        "holdout size is 20 scenarios by 4 turns",
        size["scenarios"] == 20
        and size["turns_per_scenario"] == 4
        and size["total_turns"] == size["scenarios"] * size["turns_per_scenario"],
    )
    check(
        "composition is a coverage constraint, not a turn quota",
        protocol["composition_semantics"] == "coverage_constraint_not_turn_quota"
        and "not 80 independent unit tests" in protocol["composition_note"],
    )
    episode = protocol["episode_design_rules"]
    check(
        "episodes must be coherent dialogues mixing transition families",
        episode["each_scenario_is_one_coherent_user_episode"] is True
        and episode["minimum_distinct_transition_families_per_scenario"] >= 2
        and episode["single_family_scenario_forbidden"] is True
        and episode["turn_order_must_read_as_natural_dialogue"] is True,
    )
    composition = protocol["composition_minimums_in_turns"]
    check(
        "constrained turns fit inside the holdout",
        sum(composition.values()) <= size["total_turns"],
        sum(composition.values()),
    )
    dimension = protocol["dimension_attribution_negative_design"]
    check(
        "dimension-attribution negatives raised to six turns",
        dimension["minimum_turns"] == 6
        and composition["dimension_attribution_negative"] == 6,
    )
    check(
        "the six negatives are distinct families, not paraphrases",
        dimension["must_be_distinct_semantic_families_not_paraphrases"] is True
        and len(dimension["families"]) == 6
        and len(set(dimension["families"])) == 6
        and "paraphrases of the development microSD sentence"
        in dimension["forbidden"],
    )
    check(
        "the negatives are framed as a generalization test, not an adversarial one",
        "not an adversarial attempt" in dimension["purpose"]
        and "lexically support" in dimension["gold_rule"],
    )
    reporting = protocol["reporting_requirements"]
    check(
        "per-operation numerator and denominator reporting is mandatory",
        reporting["per_operation_numerator_and_denominator"]["required"] is True
        and reporting["aggregate_correction_recall_alone_is_insufficient"] is True
        and {
            "value_correction_recall",
            "scope_correction_hard_to_soft_recall",
            "scope_correction_soft_to_hard_recall",
            "retract_recall",
            "reactivation_recall",
        }
        <= set(reporting["per_operation_numerator_and_denominator"]["applies_to"]),
    )
    sequence = protocol["authoring_sequence"]
    check(
        "authoring order is fixed and ends with the first execution",
        sequence["ordered_steps"][0] == "protocol freeze"
        and sequence["ordered_steps"].index("holdout freeze")
        < sequence["ordered_steps"].index("first v1.4 execution")
        and sequence["no_step_may_be_reordered"] is True,
    )
    check(
        "v1.4 is never run and never read while authoring",
        sequence["execute_v14_during_authoring"] is False
        and sequence["read_v14_output_while_annotating"] is False
        and sequence["annotate_from_frozen_semantic_contract_only"] is True,
    )
    policy = protocol["next_change_policy"]
    check(
        "the next intervention forks v1.5 instead of editing v1.4",
        policy["frozen_v14_is_not_edited_again"] is True
        and policy["next_intervention_forks_a_new_version"] == "v1.5"
        and "dimension" in policy["planned_v15_scope"]
        and len(policy["required_cycle_for_v15"]) == 3,
    )
    secondary = protocol["secondary_question"]
    check(
        "the secondary question is declared now and cannot be promoted later",
        secondary["status"] == "secondary_and_descriptive"
        and secondary["cannot_be_promoted_to_primary_after_the_run"] is True,
    )
    history = protocol["amendment_history"]
    check(
        "amendments are recorded and were made before any data or run existed",
        bool(history)
        and all(
            item["holdout_existed_at_amendment"] is False
            and item["any_run_completed_at_amendment"] is False
            for item in history
        ),
    )
    check(
        "composition covers every correction and negative family",
        set(composition)
        >= {
            "value_correction_hard_numeric",
            "scope_correction_hard_to_soft",
            "scope_correction_soft_to_hard",
            "retract_hard_fact",
            "retract_soft_fact",
            "reactivation_after_retract",
            "confirmation_no_material_change",
            "unchanged_requirement_zero_event",
            "dimension_attribution_negative",
            "negative_polarity",
            "tradeoff_compromised_side",
            "compound_two_or_more_facts",
            "relaxation_on_hard_only_id_gold_is_retract",
        }
        and all(value >= 3 for value in composition.values()),
    )
    execution = protocol["execution_contract"]
    check(
        "one run, no peeking, hashes recorded",
        execution["analysis_a_runs"] == 1
        and execution["no_intermediate_aggregation_before_completion"] is True
        and execution["record_raw_report_and_trace_hashes"] is True,
    )
    check(
        "exposure, weakness, and scope limits are predeclared",
        any("exposed development fixture" in item for item in protocol["interpretation_limits"])
        and any("dimension-attribution" in item for item in protocol["interpretation_limits"])
        and any("not recommendation quality" in item for item in protocol["interpretation_limits"]),
    )
    print("SEGSE confirmatory protocol verification completed.")


if __name__ == "__main__":
    main()
