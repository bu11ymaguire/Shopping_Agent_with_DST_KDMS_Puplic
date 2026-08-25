"""Graded closeness between a final DialogueState and the frozen Gold State.

The confirmatory primary outcome is how close the final accumulated state gets to
gold, not whether it matches token for token.  Exact match is retained as the
strictest cut but never stands alone: on the development fixture the v1.3 arm
scored 0/6 exact while its mean Jaccard was .569, and both v1.4 misses were
single-token.

This module is evaluator-side only.  It reads recorded token sets and never
participates in a state decision, so it is deliberately outside the frozen v1.4
method fingerprint.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def _safe_div(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def episode_closeness(gold: Iterable[str], predicted: Iterable[str]) -> dict[str, Any]:
    """Score one episode's final state against gold.

    ``error_tokens`` is the symmetric difference size, so lower is better and 0
    means identical.  ``jaccard`` is 1.0 only for an exact match.
    """

    gold_set = set(gold)
    predicted_set = set(predicted)
    true_positive = len(gold_set & predicted_set)
    false_positive = len(predicted_set - gold_set)
    false_negative = len(gold_set - predicted_set)
    union = len(gold_set | predicted_set)
    return {
        "gold": sorted(gold_set),
        "predicted": sorted(predicted_set),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "error_tokens": false_positive + false_negative,
        "jaccard": round(_safe_div(true_positive, union), 6) if union else 1.0,
        "exact": gold_set == predicted_set,
    }


def closeness_from_counts(counts: Mapping[str, Any]) -> dict[str, Any]:
    """Derive closeness from an already recorded final_state_counts block."""

    return episode_closeness(counts["gold"], counts["predicted"])


def aggregate_closeness(episodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Pool per-episode closeness without hiding the per-episode spread."""

    if not episodes:
        return {
            "episode_count": 0,
            "exact_match_count": 0,
            "exact_match_rate": 0.0,
            "mean_error_tokens": 0.0,
            "mean_jaccard": 0.0,
            "micro": {"precision": 0.0, "recall": 0.0, "f1": 0.0},
        }
    true_positive = sum(int(item["true_positive"]) for item in episodes)
    false_positive = sum(int(item["false_positive"]) for item in episodes)
    false_negative = sum(int(item["false_negative"]) for item in episodes)
    precision = _safe_div(true_positive, true_positive + false_positive)
    recall = _safe_div(true_positive, true_positive + false_negative)
    exact = sum(bool(item["exact"]) for item in episodes)
    return {
        "episode_count": len(episodes),
        "exact_match_count": exact,
        "exact_match_rate": round(_safe_div(exact, len(episodes)), 6),
        "mean_error_tokens": round(
            _safe_div(sum(int(item["error_tokens"]) for item in episodes), len(episodes)),
            6,
        ),
        "mean_jaccard": round(
            _safe_div(sum(float(item["jaccard"]) for item in episodes), len(episodes)),
            6,
        ),
        "micro": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(_safe_div(2 * precision * recall, precision + recall), 6),
        },
    }


def paired_closeness_improvement(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare two contracts episode by episode on the same gold.

    ``before`` and ``after`` map an episode ID to that episode's closeness record.
    Both must cover exactly the same episodes, because the comparison is paired
    and pooling them as independent samples is forbidden by the protocol.
    """

    if set(before) != set(after):
        raise ValueError("paired comparison requires identical episode IDs")
    per_episode: list[dict[str, Any]] = []
    improved = unchanged = worsened = 0
    for episode_id in sorted(before):
        old = before[episode_id]
        new = after[episode_id]
        error_delta = int(new["error_tokens"]) - int(old["error_tokens"])
        if error_delta < 0:
            improved += 1
            direction = "improved"
        elif error_delta > 0:
            worsened += 1
            direction = "worsened"
        else:
            unchanged += 1
            direction = "unchanged"
        per_episode.append(
            {
                "episode_id": episode_id,
                "error_tokens_before": int(old["error_tokens"]),
                "error_tokens_after": int(new["error_tokens"]),
                "error_token_reduction": -error_delta,
                "jaccard_before": float(old["jaccard"]),
                "jaccard_after": float(new["jaccard"]),
                "jaccard_improvement": round(
                    float(new["jaccard"]) - float(old["jaccard"]), 6
                ),
                "direction": direction,
            }
        )
    count = len(per_episode)
    return {
        "episode_count": count,
        "episodes_improved": improved,
        "episodes_unchanged": unchanged,
        "episodes_worsened": worsened,
        "mean_error_token_reduction": round(
            _safe_div(
                sum(item["error_token_reduction"] for item in per_episode), count
            ),
            6,
        ),
        "mean_jaccard_improvement": round(
            _safe_div(
                sum(item["jaccard_improvement"] for item in per_episode), count
            ),
            6,
        ),
        "per_episode": per_episode,
        "pairing_note": (
            "Repeated measurements on the same episodes. Paired analysis only; "
            "never pooled as independent samples."
        ),
    }


__all__ = [
    "aggregate_closeness",
    "closeness_from_counts",
    "episode_closeness",
    "paired_closeness_improvement",
]
