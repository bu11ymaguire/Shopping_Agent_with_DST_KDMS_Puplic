"""Deterministic fingerprint of the frozen SEGSE v1.4 method.

The freeze must pin every input that can change a state decision: the instruction
text, the strict output schema, the canonical dimension vocabulary, the legal
scope table, the deterministic repair contract, and the source files that hold
the validator, the State Manager, the Policy, the Query Generator, and the
retrieval/ranking path.

Both the freeze writer and the freeze verifier import this module, so a drift in
any pinned input surfaces as a hash mismatch rather than as a silent change.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, get_args

from app.llm.json_utils import to_strict_json_schema
from app.models.actual_demo import TabletDomainCanonicalId, TabletDomainFacetId
from app.segse_experiment import _ALLOWED_SCOPES
from app.segse_experiment_v12 import SEGSEV12ProposalOutput
from app.segse_experiment_v13 import _SOFT_ONLY_IDS
from app.segse_experiment_v14 import (
    _CLAUSE_SPLIT,
    _FACET_DIMENSION_TERMS,
    _HARD_VALUE_CONTRACTS,
    _OS_LABELS,
    _POSITIVE_PREFERENCE,
    _REQUIREMENT_RELAXATION,
    SEGSE_V14_PROMPT_VERSION,
    SEGSE_V14_SYSTEM_PROMPT,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]

#: Everything a state decision may read, beyond the prompt and the schema.
FROZEN_SOURCE_FILES = (
    "app/segse_experiment.py",
    "app/segse_experiment_v11.py",
    "app/segse_experiment_v12.py",
    "app/segse_experiment_v13.py",
    "app/segse_experiment_v14.py",
    "app/models/actual_demo.py",
    "app/models/pipeline.py",
    "app/nodes/actual_state_manager.py",
    "app/nodes/actual_policy.py",
    "app/nodes/actual_recommendation.py",
    "app/nodes/actual_response.py",
    "app/review_retrieval.py",
    "app/experimental_catalog.py",
)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return _digest(text.replace("\r\n", "\n").replace("\r", "\n"))


def prompt_sha256() -> str:
    return _digest(SEGSE_V14_SYSTEM_PROMPT)


def schema_sha256() -> str:
    return _digest(_canonical_json(to_strict_json_schema(SEGSEV12ProposalOutput)))


def _literal_values(alias: Any) -> list[str]:
    """Flatten a Literal, or a union of Literals, into its string members."""

    values: list[str] = []
    for member in get_args(alias):
        if isinstance(member, str):
            values.append(member)
        else:
            values.extend(_literal_values(member))
    return values


def canonical_vocabulary() -> dict[str, Any]:
    """Serialize the closed vocabulary and the dimension metadata that gates it."""

    return {
        "canonical_ids": sorted(_literal_values(TabletDomainCanonicalId)),
        "facet_ids": sorted(_literal_values(TabletDomainFacetId)),
        "allowed_scopes": {
            key: sorted(value) for key, value in sorted(_ALLOWED_SCOPES.items())
        },
        "soft_only_ids": sorted(_SOFT_ONLY_IDS),
        "operating_system_labels": sorted(_OS_LABELS),
        "facet_dimension_terms": dict(sorted(_FACET_DIMENSION_TERMS.items())),
        "hard_value_contracts": {
            key: {
                "reason": value.reason,
                "unit_pattern": value.unit_pattern,
                "anchor_dimension": value.anchor_dimension,
                "utterance_dimension": value.utterance_dimension,
                "competing_ids": list(value.competing_ids),
                "numeric_range": list(value.numeric_range)
                if value.numeric_range
                else None,
            }
            for key, value in sorted(_HARD_VALUE_CONTRACTS.items())
        },
        "relaxation_patterns": {
            "requirement_relaxation": _REQUIREMENT_RELAXATION.pattern,
            "positive_preference": _POSITIVE_PREFERENCE.pattern,
            "clause_split": _CLAUSE_SPLIT.pattern,
        },
    }


def canonical_vocabulary_sha256() -> str:
    return _digest(_canonical_json(canonical_vocabulary()))


def deterministic_repair_contract() -> dict[str, Any]:
    """The three deterministic repairs, stated so a later drift is detectable."""

    return {
        "facet_repair": {
            "applies_to": "facet canonical IDs with act assert or refine",
            "clears_scope_and_relation": True,
            "value_source": "current evidence anchor",
            "requires_dimension_evidence_in": "anchor",
            "on_missing_dimension_evidence": (
                "leave value null so the frozen authorization contract rejects it"
            ),
        },
        "hard_dimension_evidence": {
            "tier_1": "v1.3 dimension pattern inside value_after plus anchor",
            "tier_2": (
                "narrower unambiguous pattern anywhere in the current utterance"
            ),
            "tier_2_refused_when": "a competing canonical dimension shares the utterance",
            "anchored_regardless_of_tier": ["number", "unit", "numeric_range"],
        },
        "scope_relaxation": {
            "input_act": "retract",
            "output_act": "assert",
            "output_scope": "soft",
            "all_conditions_required": [
                "active prior fact for the same canonical ID",
                "prior scope is hard",
                "closed vocabulary admits soft scope for that ID",
                "obligation negation present in the current utterance",
                "a separate clause states a positive preference",
                "that clause names the same canonical dimension",
            ],
            "value_and_anchor_source": "the positive preference clause",
        },
        "unchanged_lower_layer": [
            "C semantic no-op suppression in update_segse_dialogue_state",
            "previous state is read-only reference memory",
            "exact current-utterance anchor requirement",
            "negative polarity rejection",
            "microSD expansion rejection under storage_capacity",
            "negative operating system remains unrepresentable",
            "trade-off compromised side is never a positive preference",
        ],
    }


def deterministic_repair_contract_sha256() -> str:
    return _digest(_canonical_json(deterministic_repair_contract()))


def source_sha256() -> dict[str, str]:
    return {
        relative: sha256_lf(BACKEND_ROOT / relative)
        for relative in FROZEN_SOURCE_FILES
    }


def method_fingerprint() -> dict[str, Any]:
    """The single object a freeze manifest pins and a verifier recomputes."""

    sources = source_sha256()
    return {
        "prompt_version": SEGSE_V14_PROMPT_VERSION,
        "prompt_sha256": prompt_sha256(),
        "schema_version": SEGSEV12ProposalOutput.schema_version,
        "strict_schema_sha256": schema_sha256(),
        "canonical_vocabulary_sha256": canonical_vocabulary_sha256(),
        "deterministic_repair_contract_sha256": (
            deterministic_repair_contract_sha256()
        ),
        "source_sha256_lf_normalized": sources,
        "combined_method_sha256": _digest(
            _canonical_json(
                {
                    "prompt": prompt_sha256(),
                    "schema": schema_sha256(),
                    "vocabulary": canonical_vocabulary_sha256(),
                    "repair_contract": deterministic_repair_contract_sha256(),
                    "sources": sources,
                }
            )
        ),
    }


__all__ = [
    "FROZEN_SOURCE_FILES",
    "canonical_vocabulary",
    "canonical_vocabulary_sha256",
    "deterministic_repair_contract",
    "deterministic_repair_contract_sha256",
    "method_fingerprint",
    "prompt_sha256",
    "schema_sha256",
    "sha256_lf",
    "source_sha256",
]
