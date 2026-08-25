"""Write the SEGSE v1.4 method freeze manifest.

The freeze records the method, not a result.  It pins the instruction text, the
strict schema, the canonical dimension vocabulary, the deterministic repair
contract, the decision-affecting source files, and the runtime that produced the
development evidence.  Nothing here claims generalization; the freeze exists so a
later untouched confirmatory run can prove it executed this exact method.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_review_retrieval_settings  # noqa: E402
from app.llm import write_report  # noqa: E402
from app.segse_v14_freeze import (  # noqa: E402
    canonical_vocabulary,
    deterministic_repair_contract,
    method_fingerprint,
)


DEFAULT_OUTPUT = BACKEND_ROOT / "data" / "manifests" / "segse_v14_method_freeze.json"
DEFAULT_RESULT_MANIFEST = (
    BACKEND_ROOT
    / "data"
    / "manifests"
    / "tablet_domain_segse_e2e_dev_v14_result.json"
)
DEFAULT_TAG = "segse-v1.4-freeze"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(*, freeze_tag: str, result_manifest_path: Path) -> dict[str, object]:
    result_manifest = json.loads(result_manifest_path.read_text(encoding="utf-8"))
    retrieval = load_review_retrieval_settings()
    return {
        "schema_version": "segse-v14-method-freeze-manifest-v1",
        "status": "frozen_before_untouched_confirmatory_holdout_authoring",
        "freeze_git_tag": freeze_tag,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "base_commit_before_freeze_changes": _git("rev-parse", "HEAD"),
        "development_decision": (
            "PASS - freeze v1.4 for confirmatory evaluation. This is not a system "
            "adoption decision and carries no generalization claim."
        ),
        "method": method_fingerprint(),
        "canonical_vocabulary": canonical_vocabulary(),
        "deterministic_repair_contract": deterministic_repair_contract(),
        "frozen_runtime": {
            "provider": "luxia",
            "requested_model": "gpt-4o-mini",
            "temperature": 0,
            "understanding_calls_per_attempted_turn": 1,
            "response_composer": "deterministic_template",
            "review_retrieval": {
                "mode": retrieval.mode,
                "embedding_model": retrieval.embedding_model,
                "embedding_revision": retrieval.embedding_revision,
                "reranker_model": retrieval.reranker_model,
                "reranker_revision": retrieval.reranker_revision,
                "bi_encoder_per_product": retrieval.bi_encoder_per_product,
                "reranked_per_product": retrieval.reranked_per_product,
            },
            # The authoritative weights are inline in rank_actual_products; this
            # copy is descriptive and the source file hash pins the real values.
            "product_ranking_weights": {
                "hard_constraint_match": 0.30,
                "metadata_match": 0.30,
                "subjective_need_match": 0.20,
                "review_evidence_score": 0.15,
                "evidence_reliability": 0.05,
            },
            "catalog": {
                "products": 117,
                "reviews": 7552,
                "environment_category": "category_tablet",
            },
        },
        "selected_development_evidence": {
            "run_id": result_manifest["run_id"],
            "execution_commit": result_manifest["execution_commit"],
            "summary_path": result_manifest["tracked_summary"]["path"],
            "summary_sha256": result_manifest["tracked_summary"]["sha256"],
            "attribution_path": result_manifest["tracked_attribution"]["path"],
            "attribution_sha256": result_manifest["tracked_attribution"]["sha256"],
            "gate_status": result_manifest["status"],
            "headline": result_manifest["headline"],
            "not_a_holdout_result": True,
            "fixture": "exposed six-scenario / 24-turn development fixture",
        },
        "known_open_weakness": {
            "layer": "semantic_dimension_attribution",
            "observation": (
                "An expansion-storage utterance was canonicalized to the "
                "note_taking soft preference, which has no lexical support in that "
                "utterance. The existing microSD guard is keyed to storage_capacity "
                "only, so it does not cover other soft preference IDs."
            ),
            "attribution": "provider variance in the v1.4 run, not the v1.4 code change",
            "deliberately_unfixed_reason": (
                "Adding a soft-preference dimension gate after observing this run "
                "would fit the method to an already exposed fixture."
            ),
            "resolution_path": (
                "Decide the gate on contract grounds before the confirmatory run, or "
                "measure the weakness on the untouched holdout as authored."
            ),
        },
        "confirmatory_requirements": {
            "author_holdout_after_this_freeze": True,
            "reuse_any_frozen_utterance": False,
            "freeze_before_execution": [
                "utterances",
                "gold typed state events",
                "gold material operations",
                "gold accumulated state",
                "gold policy lane and question target",
                "expected hard constraints",
            ],
            "do_not_freeze": [
                "recommended products",
                "system rankings",
                "human relevance grades",
            ],
            "run_each_analysis_once": True,
            "analyses": [
                "A: end-to-end live understanding through the frozen v1.4 pipeline",
                "B: fixed-upstream paired replay of the same recorded raw proposals "
                "through the v1.3 and v1.4 deterministic pipelines",
            ],
        },
        "limitations": [
            "The v1.4 evidence comes from an exposed development fixture of 24 turns.",
            "Raw proposals differed on 5 of 24 turns between the v1.3 and v1.4 runs, "
            "so end-to-end comparisons of the two versions mix method effect with "
            "provider variation. Analysis B exists to remove that confound.",
            "Final-state gains are amplified by persistence across later turns and "
            "are not independent per-turn discoveries.",
            "Provenance accuracy is not computed by the current evaluator.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--result-manifest", type=Path, default=DEFAULT_RESULT_MANIFEST
    )
    parser.add_argument("--freeze-tag", type=str, default=DEFAULT_TAG)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"freeze manifest already exists: {output}")
    manifest = build_manifest(
        freeze_tag=args.freeze_tag,
        result_manifest_path=args.result_manifest.resolve(),
    )
    write_report(output, manifest)
    print(json.dumps(manifest["method"], ensure_ascii=False, indent=2), flush=True)
    print(f"freeze_manifest={output}", flush=True)
    print(f"freeze_manifest_sha256={_sha256(output)}", flush=True)


if __name__ == "__main__":
    main()
