"""Verify the frozen-input automatic benchmark and its tracked artifacts."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.evaluation.tablet_domain_automatic import (  # noqa: E402
    BOOTSTRAP_RESAMPLES,
    build_automatic_benchmark,
    render_poster_markdown,
    sha256_file,
    write_csv,
)
from app.evaluation.tablet_domain_holdout import load_tablet_holdout_dataset  # noqa: E402
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402

GOLD = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
PRIOR = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1_analysis.json"
RESULT = BACKEND_ROOT / "data" / "results" / "tablet_domain_automatic_benchmark_v1.json"
SCENARIOS = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_automatic_benchmark_v1_scenarios.csv"
)
POSTER = BACKEND_ROOT / "docs" / "tablet_domain_automatic_benchmark_v1.md"
DEFINITIONS = BACKEND_ROOT / "docs" / "tablet_domain_automatic_metric_definitions.md"
OFFICIAL_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_holdout_official_v1.json"
)
HUMAN_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_annotation_packet_v1.json"
)
AUTOMATIC_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_automatic_benchmark_v1.json"
)


def normalized_text_sha256(path: Path) -> str:
    import hashlib

    normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"[PASS] {label}")


def main() -> None:
    for path in (
        GOLD,
        RAW,
        PRIOR,
        RESULT,
        SCENARIOS,
        POSTER,
        DEFINITIONS,
        HUMAN_MANIFEST,
        AUTOMATIC_MANIFEST,
    ):
        check(path.exists(), f"artifact exists: {path.relative_to(BACKEND_ROOT)}")

    official_manifest = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
    check(
        sha256_file(RAW)
        == official_manifest["git_excluded_artifacts"]["raw_report"]["sha256"],
        "official raw report remains immutable",
    )
    check(
        sha256_file(PRIOR)
        == official_manifest["git_excluded_artifacts"]["deterministic_analysis"]["sha256"],
        "prior deterministic analysis remains immutable",
    )

    gold = load_tablet_holdout_dataset(GOLD)
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    trace_paths = {
        condition: BACKEND_ROOT / "logs" / raw["conditions"][condition]["trace_filename"]
        for condition in ("full", "no_memory", "no_review")
    }
    catalog = ExperimentalAmazonCatalog()
    check(catalog.available, "frozen local catalog is available")
    recomputed, rows = build_automatic_benchmark(
        gold=gold,
        raw=raw,
        prior_analysis=prior,
        trace_paths=trace_paths,
        catalog=catalog,
    )
    recomputed["source_artifacts"] = {
        "gold": {"path": GOLD.relative_to(BACKEND_ROOT).as_posix(), "sha256": sha256_file(GOLD)},
        "official_raw": {"path": RAW.relative_to(BACKEND_ROOT).as_posix(), "sha256": sha256_file(RAW)},
        "prior_analysis": {"path": PRIOR.relative_to(BACKEND_ROOT).as_posix(), "sha256": sha256_file(PRIOR)},
    }
    tracked = json.loads(RESULT.read_text(encoding="utf-8"))
    check(recomputed == tracked, "machine-readable summary reproduces exactly")

    with tempfile.TemporaryDirectory(prefix="tablet-automatic-") as directory:
        regenerated_csv = Path(directory) / "scenarios.csv"
        write_csv(regenerated_csv, rows)
        check(
            regenerated_csv.read_bytes() == SCENARIOS.read_bytes(),
            "scenario-level CSV reproduces exactly",
        )
    check(
        render_poster_markdown(tracked) == POSTER.read_text(encoding="utf-8"),
        "poster Markdown reproduces exactly",
    )

    paired = tracked["paired_full_vs_no_memory"]
    check(
        all(
            metric["bootstrap_resamples"] == BOOTSTRAP_RESAMPLES
            for name, metric in paired.items()
            if name != "rejection_retention"
        ),
        "paired metrics use the frozen scenario bootstrap count",
    )
    check(
        paired["rejection_retention"]["status"] == "insufficient_support"
        and paired["rejection_retention"]["ci95"] is None,
        "unsupported rejection CI is not fabricated",
    )
    fixed = tracked["fixed_upstream_no_review"]
    check(fixed["contract"]["additional_llm_calls"] == 0, "No-review makes no LLM call")
    check(fixed["candidate_count_identity_rate"] == 1.0, "No-review holds candidates fixed")
    check(not fixed["quality_claim_allowed"], "No-review quality claim remains disabled")
    check(
        tracked["not_officially_computed"]["product_ndcg_at_3"].startswith("excluded")
        and tracked["not_officially_computed"]["review_ndcg_at_3"].startswith("excluded"),
        "human NDCG is excluded from official automatic results",
    )
    check(
        all(
            item["status"] == "not_evaluable_with_current_holdout"
            for item in tracked["hidden_intent_analysis"].values()
        ),
        "unsupported hidden-intent metrics are explicitly not evaluable",
    )
    check(
        tracked["oracle_analysis"]["additional_llm_calls"] == 0,
        "oracle is evaluator-only and makes no LLM calls",
    )
    automatic_manifest = json.loads(AUTOMATIC_MANIFEST.read_text(encoding="utf-8"))
    for relative, payload in automatic_manifest["tracked_outputs"].items():
        check(
            normalized_text_sha256(BACKEND_ROOT / relative)
            == payload["sha256_lf_normalized"],
            f"tracked automatic artifact hash: {relative}",
        )
    for relative, expected in automatic_manifest["source_sha256_lf_normalized"].items():
        check(
            normalized_text_sha256(BACKEND_ROOT / relative) == expected,
            f"automatic evaluator source hash: {relative}",
        )
    human = json.loads(HUMAN_MANIFEST.read_text(encoding="utf-8"))
    check(
        human["status"] == "frozen_blank_packet_before_human_annotation",
        "optional human packet stays frozen",
    )
    print("Tablet-domain automatic benchmark verification completed.")


if __name__ == "__main__":
    main()
