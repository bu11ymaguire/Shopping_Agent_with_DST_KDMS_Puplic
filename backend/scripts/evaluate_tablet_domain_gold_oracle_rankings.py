"""Run the post-hoc final Gold-State Oracle ranking diagnostic once."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.evaluation.tablet_domain_gold_oracle_rankings import (  # noqa: E402
    ANALYSIS_LABEL,
    SCHEMA_VERSION,
    compact_oracle_result,
    render_oracle_markdown,
    run_gold_state_oracle_rankings,
    sha256_file,
)
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.experimental_catalog import ExperimentalAmazonCatalog  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.review_retrieval import (  # noqa: E402
    SEMANTIC_INDEX_MANIFEST,
    build_review_retriever,
)

GOLD = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
OFFICIAL_RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
OFFICIAL_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_holdout_official_v1.json"
)
RAW_OUTPUT = (
    BACKEND_ROOT
    / "reports"
    / "tablet_domain_gold_state_oracle_rankings_posthoc_v1.json"
)
RESULT_OUTPUT = (
    BACKEND_ROOT
    / "data"
    / "results"
    / "tablet_domain_gold_state_oracle_rankings_posthoc_v1.json"
)
MARKDOWN_OUTPUT = (
    BACKEND_ROOT
    / "docs"
    / "tablet_domain_gold_state_oracle_rankings_posthoc_v1.md"
)
MANIFEST_OUTPUT = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_gold_state_oracle_rankings_posthoc_v1.json"
)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(BACKEND_ROOT).as_posix()


def _git(command: list[str]) -> str:
    completed = subprocess.run(
        ["git", *command],
        cwd=BACKEND_ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _artifact(path: Path, *, git_excluded: bool = False) -> dict[str, object]:
    return {
        "path": _relative(path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "git_excluded": git_excluded,
    }


def _refuse_existing(paths: list[Path], overwrite: bool) -> None:
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        rendered = ", ".join(_relative(path) for path in existing)
        raise RuntimeError(
            f"post-hoc outputs already exist ({rendered}); use --overwrite explicitly"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--official-raw", type=Path, default=OFFICIAL_RAW)
    parser.add_argument("--raw-output", type=Path, default=RAW_OUTPUT)
    parser.add_argument("--result-output", type=Path, default=RESULT_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=MARKDOWN_OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=MANIFEST_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    output_paths = [
        args.raw_output,
        args.result_output,
        args.markdown_output,
        args.manifest_output,
    ]
    _refuse_existing(output_paths, args.overwrite)

    official_manifest = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
    expected_raw_hash = official_manifest["git_excluded_artifacts"]["raw_report"][
        "sha256"
    ]
    if sha256_file(args.official_raw) != expected_raw_hash:
        raise RuntimeError("official raw report hash differs from the frozen manifest")

    gold = load_tablet_holdout_dataset(args.gold)
    official_raw = json.loads(args.official_raw.read_text(encoding="utf-8"))
    catalog = ExperimentalAmazonCatalog()
    if not catalog.available:
        raise RuntimeError("the frozen local tablet catalog is unavailable")
    retrieval_settings = load_review_retrieval_settings()
    if retrieval_settings.mode != "semantic":
        raise RuntimeError("Gold-State Oracle ranking requires semantic review retrieval")
    semantic_manifest_path = (
        retrieval_settings.index_dir / SEMANTIC_INDEX_MANIFEST
    )
    if not semantic_manifest_path.is_file():
        raise RuntimeError("the frozen semantic review index manifest is unavailable")
    review_retriever = build_review_retriever(retrieval_settings)

    print(
        "Starting post-hoc Gold-State Oracle ranking: "
        "20 final snapshots, zero LLM calls.",
        flush=True,
    )
    report = run_gold_state_oracle_rankings(
        gold=gold,
        official_raw=official_raw,
        catalog=catalog,
        review_retriever=review_retriever,
        checkpoint_path=args.raw_output,
    )
    report["source_artifacts"] = {
        "gold_holdout": _artifact(args.gold),
        "official_raw": _artifact(args.official_raw, git_excluded=True),
        "official_manifest": _artifact(OFFICIAL_MANIFEST),
        "catalog_manifest": _artifact(catalog.manifest_path, git_excluded=True),
        "semantic_index_manifest": _artifact(
            semantic_manifest_path, git_excluded=True
        ),
    }
    report["runtime"] = {
        "catalog_status": catalog.status().model_dump(mode="json"),
        "review_retrieval": {
            "mode": retrieval_settings.mode,
            "embedding_model": retrieval_settings.embedding_model,
            "embedding_revision": retrieval_settings.embedding_revision,
            "reranker_model": retrieval_settings.reranker_model,
            "reranker_revision": retrieval_settings.reranker_revision,
            "bi_encoder_per_product": retrieval_settings.bi_encoder_per_product,
            "reranked_per_product": retrieval_settings.reranked_per_product,
        },
    }
    write_report(args.raw_output, report)

    compact = compact_oracle_result(report)
    compact["source_artifacts"] = report["source_artifacts"]
    compact["runtime"] = report["runtime"]
    write_report(args.result_output, compact)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(
        render_oracle_markdown(compact), encoding="utf-8"
    )

    source_paths = [
        BACKEND_ROOT
        / "app"
        / "evaluation"
        / "tablet_domain_gold_oracle_rankings.py",
        BACKEND_ROOT / "scripts" / "evaluate_tablet_domain_gold_oracle_rankings.py",
        BACKEND_ROOT / "scripts" / "verify_tablet_domain_gold_oracle_rankings.py",
        BACKEND_ROOT / "app" / "evaluation" / "tablet_domain_automatic.py",
        BACKEND_ROOT / "app" / "nodes" / "actual_recommendation.py",
        BACKEND_ROOT / "app" / "review_retrieval.py",
    ]
    missing_sources = [path for path in source_paths if not path.is_file()]
    if missing_sources:
        raise RuntimeError(
            "required source file missing: "
            + ", ".join(str(path) for path in missing_sources)
        )
    manifest = {
        "schema_version": "tablet-domain-gold-state-oracle-rankings-posthoc-manifest-v1",
        "status": "post_hoc_secondary_analysis_complete",
        "analysis_schema_version": SCHEMA_VERSION,
        "analysis_label": ANALYSIS_LABEL,
        "poster_submission_preceded_analysis": True,
        "official_run_reexecuted": False,
        "new_gold_product_labels_added": False,
        "additional_llm_calls": 0,
        "recommendation_quality_claim_allowed": False,
        "execution_commit": _git(["rev-parse", "HEAD"]),
        "worktree_dirty_at_execution": bool(_git(["status", "--porcelain"])),
        "immutable_inputs": report["source_artifacts"],
        "outputs": {
            "raw_report": _artifact(args.raw_output, git_excluded=True),
            "compact_result": _artifact(args.result_output),
            "markdown_report": _artifact(args.markdown_output),
        },
        "source_files": {
            _relative(path): {"sha256": sha256_file(path)} for path in source_paths
        },
        "runtime": report["runtime"],
        "summary": report["summary"],
        "guardrail": report["guardrail"],
    }
    write_report(args.manifest_output, manifest)
    print(
        json.dumps(
            {
                "status": report["status"],
                "raw_report": _artifact(args.raw_output, git_excluded=True),
                "compact_result": _artifact(args.result_output),
                "markdown_report": _artifact(args.markdown_output),
                "manifest": _artifact(args.manifest_output),
                "summary": report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
