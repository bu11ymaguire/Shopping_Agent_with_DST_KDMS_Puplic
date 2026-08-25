"""Deterministic checks for the graded final-state closeness metric.

The last section reproduces the recorded development numbers from the tracked
result summaries, which is the calibration the protocol cites when it explains why
exact match is not the headline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.segse_final_state_closeness import (  # noqa: E402
    aggregate_closeness,
    closeness_from_counts,
    episode_closeness,
    paired_closeness_improvement,
)

PROTOCOL = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_confirmatory_v1_protocol.json"
)


def check(label: str, condition: bool, detail: Any = None) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    suffix = f" - {detail}" if detail is not None else ""
    print(f"[PASS] {label}{suffix}")


def verify_episode_scoring() -> None:
    identical = episode_closeness({"a|hard|300", "b|soft"}, {"a|hard|300", "b|soft"})
    check(
        "identical state scores zero error tokens and Jaccard 1.0",
        identical["error_tokens"] == 0
        and identical["jaccard"] == 1.0
        and identical["exact"] is True,
    )

    one_missing = episode_closeness({"a|hard|300", "b|soft"}, {"a|hard|300"})
    check(
        "a single missing token costs one error token, not the whole episode",
        one_missing["error_tokens"] == 1
        and one_missing["false_negative"] == 1
        and one_missing["exact"] is False
        and one_missing["jaccard"] == 0.5,
    )

    one_extra = episode_closeness({"a|hard|300"}, {"a|hard|300", "c|soft"})
    check(
        "a single spurious token is scored the same magnitude as a missing one",
        one_extra["error_tokens"] == 1
        and one_extra["false_positive"] == 1
        and one_extra["exact"] is False,
    )

    wrong_value = episode_closeness({"a|hard|256"}, {"a|hard|128"})
    check(
        "a stale hard value counts as one FP plus one FN",
        wrong_value["error_tokens"] == 2
        and wrong_value["jaccard"] == 0.0
        and wrong_value["true_positive"] == 0,
    )

    disjoint = episode_closeness({"a|hard|300"}, {"z|soft"})
    check(
        "a fully wrong state is distinguishable from a nearly right one",
        disjoint["jaccard"] == 0.0 and one_missing["jaccard"] > disjoint["jaccard"],
    )

    from_counts = closeness_from_counts(
        {"gold": ["a|hard|300", "b|soft"], "predicted": ["a|hard|300"]}
    )
    check(
        "closeness derives from an already recorded counts block",
        from_counts["error_tokens"] == 1 and from_counts["jaccard"] == 0.5,
    )


def verify_exact_match_is_coarser() -> None:
    """Two arms with the same exact-match score can differ a lot in closeness."""

    coarse = [
        episode_closeness({"a", "b", "c"}, {"a", "b"}),
        episode_closeness({"a", "b", "c"}, {"a", "b"}),
    ]
    worse = [
        episode_closeness({"a", "b", "c"}, set()),
        episode_closeness({"a", "b", "c"}, {"x", "y", "z"}),
    ]
    coarse_agg = aggregate_closeness(coarse)
    worse_agg = aggregate_closeness(worse)
    check(
        "equal exact-match rates can hide very different closeness",
        coarse_agg["exact_match_count"] == worse_agg["exact_match_count"] == 0
        and coarse_agg["mean_error_tokens"] < worse_agg["mean_error_tokens"]
        and coarse_agg["mean_jaccard"] > worse_agg["mean_jaccard"],
        f'{coarse_agg["mean_jaccard"]} vs {worse_agg["mean_jaccard"]}',
    )


def verify_paired_improvement() -> None:
    before = {
        "s1": episode_closeness({"a", "b"}, {"a"}),
        "s2": episode_closeness({"a", "b"}, set()),
        "s3": episode_closeness({"a"}, {"a"}),
    }
    after = {
        "s1": episode_closeness({"a", "b"}, {"a", "b"}),
        "s2": episode_closeness({"a", "b"}, {"a"}),
        "s3": episode_closeness({"a"}, {"a", "z"}),
    }
    paired = paired_closeness_improvement(before, after)
    check(
        "paired comparison counts direction per episode",
        paired["episodes_improved"] == 2
        and paired["episodes_worsened"] == 1
        and paired["episodes_unchanged"] == 0,
    )
    check(
        "paired means are computed over episodes, not pooled tokens",
        paired["episode_count"] == 3
        and paired["mean_error_token_reduction"] == round((1 + 1 - 1) / 3, 6),
    )
    check(
        "pairing is labelled as repeated measurement",
        "never pooled as independent samples" in paired["pairing_note"],
    )
    try:
        paired_closeness_improvement(before, {"s1": after["s1"]})
    except ValueError:
        print("[PASS] mismatched episode sets are rejected")
    else:
        raise AssertionError("mismatched episode sets must be rejected")


def _arm_final_counts(path: Path, arm_key: tuple[str, ...]) -> dict[str, Any]:
    node: Any = json.loads(path.read_text(encoding="utf-8"))
    for key in arm_key:
        node = node[key]
    return {
        item["scenario_id"]: closeness_from_counts(item["final_state_counts"])
        for item in node["scenarios"]
    }


def verify_development_calibration() -> None:
    """Recompute the calibration numbers the protocol quotes, if raw reports exist."""

    reports = BACKEND_ROOT / "reports"
    sources = {
        "b0_c": (reports / "tablet_domain_segse_e2e_dev_v1.json", ("arms", "b0_c_semantic_noop")),
        "v13": (reports / "tablet_domain_segse_e2e_dev_v13.json", ("treatment",)),
        "v14": (reports / "tablet_domain_segse_e2e_dev_v14.json", ("treatment",)),
    }
    if not all(path.exists() for path, _ in sources.values()):
        print("[SKIP] git-ignored raw development reports are not present")
        return

    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    quoted = protocol["development_calibration_reference"]
    computed = {}
    for name, (path, arm_key) in sources.items():
        episodes = _arm_final_counts(path, arm_key)
        aggregate = aggregate_closeness(list(episodes.values()))
        computed[name] = (episodes, aggregate)
        expected = quoted["per_episode_final_state"][name]
        check(
            f"{name} calibration matches the protocol note",
            expected["exact"]
            == f'{aggregate["exact_match_count"]}/{aggregate["episode_count"]}'
            and abs(expected["mean_error_tokens"] - aggregate["mean_error_tokens"])
            < 1e-3
            and abs(expected["mean_jaccard"] - aggregate["mean_jaccard"]) < 1e-3,
            f'exact {aggregate["exact_match_count"]}/{aggregate["episode_count"]}, '
            f'err {aggregate["mean_error_tokens"]}, jac {aggregate["mean_jaccard"]}',
        )

    check(
        "v1.3 scored zero exact match while still being over half right",
        computed["v13"][1]["exact_match_count"] == 0
        and computed["v13"][1]["mean_jaccard"] > 0.5,
        computed["v13"][1]["mean_jaccard"],
    )
    paired = paired_closeness_improvement(computed["v13"][0], computed["v14"][0])
    expected_paired = quoted["paired_v13_to_v14"]
    check(
        "paired development movement matches the protocol note",
        paired["episodes_improved"] == expected_paired["improved"]
        and paired["episodes_unchanged"] == expected_paired["unchanged"]
        and paired["episodes_worsened"] == expected_paired["worsened"],
        f'{paired["episodes_improved"]}/{paired["episodes_unchanged"]}/'
        f'{paired["episodes_worsened"]}',
    )
    check(
        "v1.4 misses are single-token rather than broad failures",
        all(
            item["error_tokens"] <= 1
            for item in computed["v14"][0].values()
            if not item["exact"]
        ),
        {k: v["error_tokens"] for k, v in computed["v14"][0].items() if not v["exact"]},
    )


def main() -> None:
    verify_episode_scoring()
    verify_exact_match_is_coarser()
    verify_paired_improvement()
    verify_development_calibration()
    print("SEGSE final-state closeness verification completed.")


if __name__ == "__main__":
    main()
