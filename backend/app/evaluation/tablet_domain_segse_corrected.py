"""Case-local operation guardrails for SEGSE development result errata.

The frozen v1 evaluator aggregated operation pairs globally, allowing an operation
from one case to satisfy the same canonical operation in another case.  All other
metrics were already accumulated case-locally.  This module corrects only the three
operation-recall guardrails and then reuses the frozen gate calculation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.evaluation.tablet_domain_segse import evaluate_development_gate


def _case_local_operation_recall(
    records: list[Mapping[str, Any]], labels: set[str]
) -> dict[str, Any]:
    target_count = 0
    true_positive = 0
    for record in records:
        actual = set(record["actual_material_operations"])
        for gold_pair in record["gold_material_operations"]:
            operation = str(gold_pair).rsplit("|", 1)[1]
            if operation not in labels:
                continue
            target_count += 1
            true_positive += gold_pair in actual
    return {
        "target_count": target_count,
        "true_positive": true_positive,
        "recall": round(true_positive / target_count, 6) if target_count else 0.0,
    }


def corrected_arm_metrics(
    frozen_metrics: Mapping[str, Any], records: list[Mapping[str, Any]]
) -> dict[str, Any]:
    metrics = deepcopy(dict(frozen_metrics))
    metrics["correction_recall"] = _case_local_operation_recall(
        records, {"update_value", "update_scope", "refine"}
    )
    metrics["retract_recall"] = _case_local_operation_recall(records, {"retract"})
    metrics["reactivation_recall"] = _case_local_operation_recall(
        records, {"reactivate"}
    )
    return metrics


def build_corrected_v12_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    baseline = result["baseline"]
    treatment = result["treatment"]
    baseline_metrics = corrected_arm_metrics(
        baseline["metrics"], baseline["case_scores"]
    )
    treatment_metrics = corrected_arm_metrics(
        treatment["metrics"], treatment["case_scores"]
    )
    return {
        "schema_version": "tablet-domain-segse-dev-v12-corrected-result-v1",
        "study_status": "development_erratum_not_confirmatory",
        "source_run_id": result["run_id"],
        "source_execution_commit": result["version_control"]["execution_commit"],
        "evaluator_erratum": {
            "bug": "operation guardrails were matched globally instead of within each case",
            "affected_fields": [
                "correction_recall",
                "retract_recall",
                "reactivation_recall",
            ],
            "unaffected_fields": "all candidate, typed-event, material ID, material-operation PRF, final-state, route, evidence, completion, FP/FN counts",
        },
        "baseline": {
            "arm": baseline["arm"],
            "metrics": baseline_metrics,
        },
        "treatment": {
            "arm": treatment["arm"],
            "metrics": treatment_metrics,
            "trace_summary": treatment["trace_summary"],
        },
        "development_gate": evaluate_development_gate(
            baseline_metrics, treatment_metrics
        ),
        "limitations": [
            "This is a deterministic erratum over the same exposed development fixture.",
            "The correction does not create new model outputs or confirmatory evidence.",
            "Downstream hard-filter completion remains unevaluated.",
        ],
    }
