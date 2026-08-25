"""Freeze the selected tablet-domain v2.3 development candidate before holdout authoring."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.nodes.actual_response import (  # noqa: E402
    ACTUAL_CLARIFY_PROMPT_VERSION,
    ACTUAL_RECOMMEND_PROMPT_VERSION,
)
from app.nodes.tablet_domain_understanding import (  # noqa: E402
    TABLET_DOMAIN_SYSTEM_PROMPT,
    TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
)

DEFAULT_REPORT = (
    BACKEND_ROOT / "reports" / "tablet_domain_understanding_dev_v2_3.json"
)
DEFAULT_OUTPUT = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_v2_freeze.json"
)
SEMANTIC_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "amazon_tablet_semantic_retrieval_v1.json"
)
SOURCE_PATHS = (
    BACKEND_ROOT / "app" / "models" / "actual_demo.py",
    BACKEND_ROOT / "app" / "models" / "pipeline.py",
    BACKEND_ROOT / "app" / "nodes" / "tablet_domain_understanding.py",
    BACKEND_ROOT / "app" / "nodes" / "actual_state_manager.py",
    BACKEND_ROOT / "app" / "nodes" / "actual_policy.py",
    BACKEND_ROOT / "app" / "nodes" / "actual_recommendation.py",
    BACKEND_ROOT / "app" / "nodes" / "actual_response.py",
    BACKEND_ROOT / "app" / "review_retrieval.py",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=BACKEND_ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def freeze(report_path: Path, output_path: Path) -> dict[str, object]:
    if output_path.exists():
        raise RuntimeError(f"refusing to overwrite frozen manifest: {output_path}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("split") != "dev":
        raise RuntimeError("the selected input must remain explicitly marked as dev")
    if report.get("prompt_version") != "spn-understanding-tablet-domain-en-v2.3-dev":
        raise RuntimeError("expected the selected v2.3 development run")
    current_prompt_hash = _sha256_bytes(TABLET_DOMAIN_SYSTEM_PROMPT.encode("utf-8"))
    if report.get("system_prompt_sha256") != current_prompt_hash:
        raise RuntimeError("current system prompt differs from selected v2.3 run")
    if report["metrics"].get("validation_success_rate") != 1.0:
        raise RuntimeError("selected dev run did not validate all cases")
    if report["metrics"].get("domain_route_exact_accuracy") != 1.0:
        raise RuntimeError("selected dev run did not route all cases correctly")

    retrieval = load_review_retrieval_settings()
    source_hashes = {
        path.relative_to(BACKEND_ROOT).as_posix(): _sha256(path)
        for path in SOURCE_PATHS
    }
    manifest = {
        "schema_version": "tablet-domain-v2-freeze-manifest-v1",
        "status": "frozen_before_untouched_holdout_authoring",
        "freeze_git_tag": "tablet-domain-v2.3-freeze",
        "base_commit_before_freeze_changes": _git_head(),
        "research_scope": {
            "domain": "tablet_shopping",
            "environment_category": "category_tablet",
            "catalog_products": 117,
            "catalog_reviews": 7552,
            "explicit_other_category_route": "unsupported_category",
            "latent_subjective_generation": "disabled_for_v2_evaluation",
        },
        "selected_development_run": {
            "report_sha256": _sha256(report_path),
            "run_id": report["run_id"],
            "dataset_version": report["dataset_version"],
            "dataset_sha256": report["dataset_sha256"],
            "selected_prompt_version": report["prompt_version"],
            "selection_reason": (
                "v2.3 achieved 26/26 schema validation, 26/26 domain routing, "
                "and the best canonical micro-F1 among recorded v2 dev iterations."
            ),
            "metrics": report["metrics"],
            "trace_summary": report["trace_summary"],
            "not_a_holdout_result": True,
        },
        "frozen_runtime": {
            "provider": "luxia",
            "requested_model": "gpt-4o-mini",
            "reported_model_in_selected_run": "gpt-4o-mini-2024-07-18",
            "understanding_prompt_version": TABLET_DOMAIN_UNDERSTANDING_PROMPT_VERSION,
            "understanding_schema_version": "understanding-v3.1-tablet-domain-en",
            "system_prompt_sha256": current_prompt_hash,
            "clarify_prompt_version": ACTUAL_CLARIFY_PROMPT_VERSION,
            "recommend_prompt_version": ACTUAL_RECOMMEND_PROMPT_VERSION,
            "review_retrieval": {
                "mode": retrieval.mode,
                "embedding_model": retrieval.embedding_model,
                "embedding_revision": retrieval.embedding_revision,
                "reranker_model": retrieval.reranker_model,
                "reranker_revision": retrieval.reranker_revision,
                "bi_encoder_per_product": retrieval.bi_encoder_per_product,
                "reranked_per_product": retrieval.reranked_per_product,
                "combined_score_weights": {
                    "normalized_cosine": 0.35,
                    "cross_encoder_sigmoid": 0.65,
                },
                "tracked_semantic_manifest_sha256": _sha256(SEMANTIC_MANIFEST),
            },
            "product_ranking_weights": {
                "hard_constraint_match": 0.30,
                "metadata_match": 0.30,
                "subjective_need_match": 0.20,
                "review_evidence_score": 0.15,
                "evidence_reliability": 0.05,
            },
            "source_sha256": source_hashes,
        },
        "ablation_contract": {
            "invariant_all_conditions": [
                "environment.category=tablet",
                "same frozen Understanding model/prompt/schema",
                "same deterministic policy and hard filters",
                "same catalog and candidate limits",
            ],
            "full": "persistent dialogue state plus review evidence",
            "no_memory": "current-turn state only plus review evidence",
            "no_review": "persistent dialogue state with review contribution removed",
            "one_variable_rule": True,
        },
        "holdout_protocol": {
            "author_after_this_freeze": True,
            "freeze_before_execution": [
                "utterances",
                "gold State Diff",
                "gold policy/action",
                "expected hard constraints",
            ],
            "do_not_freeze": [
                "recommended products",
                "system rankings",
                "human relevance grades",
            ],
            "run_each_condition_once": True,
        },
        "limitations": [
            "The selected 26-case metrics are development results and cannot support a generalization claim.",
            "Remaining dev errors include negated operating-system handling, retained-state copying, and trade-off boundary cases.",
            "The untouched multi-turn holdout and Full/No-memory/No-review outputs do not exist yet.",
        ],
    }
    write_report(output_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = freeze(args.report.resolve(), args.output.resolve())
    print(json.dumps(manifest["selected_development_run"]["metrics"], indent=2))
    print(f"manifest={args.output.resolve()}")


if __name__ == "__main__":
    main()
