"""Render the confirmatory Final State tables in the reporting format."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_SUMMARY = BACKEND_ROOT / "data" / "results" / "segse_confirmatory_v1.json"
DEFAULT_OUTPUT = BACKEND_ROOT / "docs" / "segse_confirmatory_v1_tables.md"

HEADER = (
    "| Condition | TP | FP | FN | Precision | Recall | Final State F1 |\n"
    "| --------- | -: | -: | -: | --------: | -----: | -------------: |"
)


def row(name: str, block: dict) -> str:
    return (
        f'| {name} | {block["tp"]} | {block["fp"]} | {block["fn"]} '
        f'| {block["precision"]:.3f} | {block["recall"]:.3f} | {block["f1"]:.3f} |'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = json.loads(args.summary.resolve().read_text(encoding="utf-8"))
    a = summary["analysis_a_end_to_end"]
    b = summary["analysis_b_fixed_upstream"]
    c = summary["analysis_c_test_retest"]

    lines: list[str] = []
    lines.append("# SEGSE confirmatory holdout tables")
    lines.append("")
    lines.append(
        "Untouched 20-episode / 80-turn holdout over the same frozen tablet catalog "
        "(117 products, 7,552 reviews) and the same review corpus as the official "
        "holdout. Method and protocol were hash-frozen before the run."
    )
    lines.append("")
    lines.append("## Final State, Analysis A (frozen v1.4, end to end)")
    lines.append("")
    lines.append(HEADER)
    lines.append(row("SEGSE v1.4 (Run A, primary)", a["final_state_table"]))
    lines.append(row("SEGSE v1.4 (Run B, test-retest)", c["run_b_final_state_table"]))
    lines.append("")
    lines.append(
        "Run B is a provider-stability measurement. It never replaces, averages "
        "with, or redefines Run A."
    )
    lines.append("")
    lines.append("## Final State, Analysis B (identical raw proposals)")
    lines.append("")
    lines.append(
        "| Deterministic contract | Exact match | Mean error tokens | Mean Jaccard |"
    )
    lines.append("| --- | -: | -: | -: |")
    for label, key in (("v1.3", "v13_final_state_closeness"), ("v1.4", "v14_final_state_closeness")):
        block = b[key]
        lines.append(
            f'| {label} | {block["exact_match_count"]}/{block["episode_count"]} '
            f'| {block["mean_error_tokens"]:.3f} | {block["mean_jaccard"]:.3f} |'
        )
    lines.append("")
    paired = b["paired_improvement"]
    boot = b["paired_bootstrap_jaccard"]
    lines.append(
        f'Paired over 20 episodes: {paired["episodes_improved"]} improved, '
        f'{paired["episodes_unchanged"]} unchanged, {paired["episodes_worsened"]} worsened. '
        f'Mean Jaccard improvement {boot["point"]:+.3f}, '
        f'95% paired bootstrap CI [{boot["ci_lower_95"]:.3f}, {boot["ci_upper_95"]:.3f}].'
    )
    lines.append("")
    lines.append("## Correction recall by operation, Analysis A")
    lines.append("")
    lines.append("| Operation | Hit / Target | Recall |")
    lines.append("| --- | -: | -: |")
    for label, block in a["correction_recall_by_operation"].items():
        recall = "n/a" if block["recall"] is None else f'{block["recall"]:.3f}'
        lines.append(f'| {label} | {block["reported_as"]} | {recall} |')
    combined = a["combined_value_and_scope_correction"]
    lines.append(
        f'| **value + scope combined** | **{combined["reported_as"]}** | '
        f'**{combined["recall"]:.3f}** |'
    )
    lines.append("")
    v13 = b["value_and_scope_correction_recall"]["v13"]
    v14 = b["value_and_scope_correction_recall"]["v14"]
    lines.append(
        f'Under identical raw proposals the same metric is v1.3 {v13["reported_as"]} '
        f'and v1.4 {v14["reported_as"]}.'
    )
    lines.append("")
    lines.append("## Supporting metrics, Analysis A")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | -: |")
    for label, value in (
        ("Turn output completion", a["turn_output_completion_rate"]),
        ("Candidate precision", a["candidate"]["precision"]),
        ("Candidate recall", a["candidate"]["recall"]),
        ("Candidate FP", a["candidate_fp_count"]),
        ("C2U FP", a["c2u_fp_count"]),
        ("Novel candidate FP", a["novel_candidate_fp_count"]),
        ("Forbidden predictions", a["forbidden_prediction_count"]),
        ("Material State Diff F1", a["material_state_diff"]["f1"]),
        ("Material operation F1", a["material_operation"]["f1"]),
        ("Turnwise accumulated state F1", a["turnwise_accumulated_state"]["f1"]),
        ("Evidence-span validity", a["evidence_span_validity"]["rate"]),
        ("Policy lane accuracy", a["policy_lane_accuracy"]),
        ("Hard-filter completion", a["hard_filter_completion_rate"]),
        ("Confirmation metadata recall", a["confirmation_metadata_recall"]["recall"]),
        ("Top-3 hard violation rate", a["top3_hard_constraint_violation_rate"]),
    ):
        rendered = f"{value:.3f}" if isinstance(value, float) else str(value)
        lines.append(f"| {label} | {rendered} |")
    lines.append("")
    lines.append("## Dimension-attribution negatives, Analysis A")
    lines.append("")
    lines.append("| Turn | Confusion family | Forbidden IDs predicted | Candidate FP | Clean |")
    lines.append("| --- | --- | --- | --- | --- |")
    for item in a["dimension_attribution_turns"]:
        lines.append(
            f'| {item["turn_id"]} | {item["confusion_family"]} '
            f'| {item["forbidden_predicted_ids"] or "-"} '
            f'| {item["candidate_fp_ids"] or "-"} '
            f'| {"yes" if item["clean"] else "no"} |'
        )
    lines.append("")
    lines.append("## Analysis C, test-retest")
    lines.append("")
    lines.append("| Disagreement | Value |")
    lines.append("| --- | -: |")
    d = c["disagreement"]
    for key in (
        "compared_turns",
        "raw_structured_proposal_exact_match_disagreement",
        "candidate_set_disagreement",
        "material_operation_disagreement",
        "candidate_fp_difference",
        "c2u_fp_difference",
        "correction_recall_difference",
        "material_state_diff_f1_difference",
        "final_state_f1_difference",
        "turn_completion_difference",
    ):
        value = d[key]
        rendered = f"{value:+.3f}" if isinstance(value, float) else str(value)
        lines.append(f"| {key.replace('_', ' ')} | {rendered} |")
    lines.append("")
    lines.append("## Pre-registered decision")
    lines.append("")
    lines.append(f'Status: **{summary["decision"]["status"]}**')
    lines.append("")
    lines.append("| Check | Result |")
    lines.append("| --- | --- |")
    for key, value in summary["decision"]["checks"].items():
        lines.append(f'| {key.replace("_", " ")} | {"PASS" if value else "FAIL"} |')
    lines.append("")
    while lines and not lines[-1].strip():
        lines.pop()
    args.output.resolve().write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\ntables={args.output.resolve()}")


if __name__ == "__main__":
    main()
