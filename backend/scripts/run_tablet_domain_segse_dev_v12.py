"""Run provider-compatible SEGSE-lite v1.2 with frozen v1 B0 scores."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_llm_settings  # noqa: E402
from app.evaluation.tablet_domain_segse import (  # noqa: E402
    aggregate_arm,
    evaluate_development_gate,
    previous_state_summary,
    score_case,
    seed_dialogue_state,
)
from app.llm import build_client, write_report  # noqa: E402
from app.segse_experiment import update_segse_dialogue_state  # noqa: E402
from app.segse_experiment_v12 import SEGSEV12UnderstandingProvider  # noqa: E402

DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_segse_dev_v1.json"
DEFAULT_BASELINE = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v1.json"
DEFAULT_MANIFEST = BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_dev_v12_protocol.json"
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "tablet_domain_segse_dev_v12.json"
DEFAULT_SUMMARY = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v12.json"
BRANCH = "codex/segse-lite-experiment"
BASELINE_ARM = "b0_c_semantic_noop"
TREATMENT_ARM = "d4_segse_lite_v12"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_lf(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verify(
    manifest: Mapping[str, Any], baseline_path: Path, output: Path, summary: Path
) -> None:
    if _git("branch", "--show-current") != BRANCH:
        raise RuntimeError(f"v1.2 run requires {BRANCH}")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked worktree must be clean before v1.2 live execution")
    if output.exists() or summary.exists():
        raise RuntimeError("refusing to overwrite v1.2 artifacts")
    if _sha256(baseline_path) != manifest["frozen_baseline_result_sha256"]:
        raise RuntimeError("frozen B0 result differs")
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        if _sha256_lf(BACKEND_ROOT / relative) != expected:
            raise RuntimeError(f"frozen v1.2 source differs: {relative}")


def _trace_summary(path: Path) -> dict[str, Any]:
    records = [
        json.loads(line)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip())
    ] if path.exists() else []
    models: dict[str, int] = {}
    modes: dict[str, int] = {}
    for record in records:
        model = str(record.get("reported_model"))
        models[model] = models.get(model, 0) + 1
        mode = str(record.get("structured_mode"))
        modes[mode] = modes.get(mode, 0) + 1
    return {
        "logical_call_count": len(records),
        "validation_success_count": sum(bool(item.get("validation_success")) for item in records),
        "schema_repair_count": sum(int(item.get("retry_count") or 0) for item in records),
        "transport_retry_count": sum(int(item.get("transport_retry_count") or 0) for item in records),
        "http_attempt_count": sum(len(item.get("attempts", [])) for item in records),
        "fallback_count": sum(bool(item.get("fallback_used")) for item in records),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in records),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in records),
        "reported_models": models,
        "structured_modes": modes,
        "sha256": _sha256(path) if path.exists() else None,
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset).resolve()
    baseline_path = Path(args.baseline_result).resolve()
    manifest_path = Path(args.manifest).resolve()
    output_path = Path(args.output).resolve()
    summary_path = Path(args.summary).resolve()
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    baseline_result = json.loads(baseline_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _verify(manifest, baseline_path, output_path, summary_path)
    cases = list(dataset["cases"])
    if len(cases) != 24:
        raise RuntimeError("v1.2 requires the frozen 24-case fixture")
    baseline = baseline_result["arms"][BASELINE_ARM]
    run_id = f"segse-dev-v12-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    trace_filename = f"tablet_segse_dev_{run_id}_{TREATMENT_ARM}.jsonl"
    settings = load_llm_settings()
    if settings.provider == "luxia":
        settings.require_api_key()
    report: dict[str, Any] = {
        "schema_version": "tablet-domain-segse-dev-v12-run-v1",
        "study_status": "post_v1_development_not_confirmatory",
        "run_id": run_id,
        "status": "running",
        "started_at": _now(),
        "completed_at": None,
        "version_control": {
            "branch": _git("branch", "--show-current"),
            "execution_commit": _git("rev-parse", "HEAD"),
            "tracked_worktree_clean_before_run": True,
        },
        "dataset": {
            "path": str(dataset_path),
            "case_count": 24,
            "reuse_classification": "same_exposed_development_fixture",
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": 0.0,
        },
        "protocol": {
            "baseline_reused_from_run_id": baseline_result["run_id"],
            "baseline_additional_llm_calls": 0,
            "treatment_live_logical_calls_expected": 24,
            "provider_schema_preflight": "flat_schema_without_oneOf",
            "missing_output_as_empty_prediction": True,
            "catalog_calls": 0,
            "confirmatory_evidence": False,
        },
        "baseline": {
            "arm": BASELINE_ARM,
            "source_result_sha256": manifest["frozen_baseline_result_sha256"],
            "metrics": baseline["metrics"],
            "case_scores": baseline["case_scores"],
        },
        "treatment": {
            "arm": TREATMENT_ARM,
            "status": "running",
            "trace_filename": trace_filename,
            "trace_summary": None,
            "cases": [],
            "metrics": None,
        },
        "development_gate": None,
        "limitations": [
            "V1.2 was designed after inspecting v1 and v1.1 preflight outcomes on this development fixture.",
            "Only v1.2 receives new calls; B0 is frozen and reused.",
            "No downstream hard-filter evaluation is included.",
        ],
    }
    write_report(output_path, report)
    client = build_client(settings, trace=True, trace_filename=trace_filename)
    provider = SEGSEV12UnderstandingProvider(client)
    try:
        for index, case in enumerate(cases, start=1):
            before = seed_dialogue_state(case)
            after = before.model_copy(deep=True)
            understanding = None
            state_diff = None
            error: Exception | None = None
            started = time.perf_counter()
            try:
                understanding = await provider(
                    utterance=case["utterance"],
                    previous_state_summary=previous_state_summary(before),
                    conversation_id=f"segse-dev-v12-{case['id']}",
                    turn=1,
                )
                after, state_diff = update_segse_dialogue_state(
                    before, understanding, [], turn_id=f"{case['id']}-turn-1"
                )
            except Exception as exc:
                error = exc
            score = score_case(
                arm="d4_segse_lite",
                case=case,
                before=before,
                after=after,
                understanding=understanding,
                error=error,
            )
            report["treatment"]["cases"].append(
                {
                    "case_id": case["id"],
                    "family": case["family"],
                    "utterance": case["utterance"],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "understanding": understanding.model_dump(mode="json") if understanding else None,
                    "state_diff": state_diff.model_dump(mode="json") if state_diff else None,
                    "final_state": after.model_dump(mode="json"),
                    "score": score,
                }
            )
            write_report(output_path, report)
            print(
                f"{TREATMENT_ARM}: {case['id']} ({index}/{len(cases)}) "
                f"{'completed' if error is None else type(error).__name__}",
                flush=True,
            )
    finally:
        await client.aclose()
    scores = [item["score"] for item in report["treatment"]["cases"]]
    report["treatment"]["metrics"] = aggregate_arm(scores)
    report["treatment"]["status"] = "completed"
    report["treatment"]["trace_summary"] = _trace_summary(
        settings.log_dir / trace_filename
    )
    report["development_gate"] = evaluate_development_gate(
        baseline["metrics"], report["treatment"]["metrics"]
    )
    report["status"] = "completed"
    report["completed_at"] = _now()
    write_report(output_path, report)
    compact = {
        key: report[key]
        for key in (
            "schema_version",
            "study_status",
            "run_id",
            "status",
            "started_at",
            "completed_at",
            "version_control",
            "dataset",
            "runtime",
            "protocol",
            "baseline",
            "development_gate",
            "limitations",
        )
    }
    compact["treatment"] = {
        "arm": TREATMENT_ARM,
        "status": report["treatment"]["status"],
        "metrics": report["treatment"]["metrics"],
        "trace_summary": report["treatment"]["trace_summary"],
        "case_scores": scores,
    }
    write_report(summary_path, compact)
    return compact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--baseline-result", default=str(DEFAULT_BASELINE))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    return parser


def main() -> None:
    result = asyncio.run(execute(_parser().parse_args()))
    print(json.dumps(result["development_gate"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
