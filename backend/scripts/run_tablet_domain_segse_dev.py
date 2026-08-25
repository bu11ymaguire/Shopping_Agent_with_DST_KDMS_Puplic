"""Run the frozen 24-case B0 versus SEGSE-lite development comparison once."""

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
    ARM_ORDER,
    aggregate_arm,
    evaluate_development_gate,
    previous_state_summary,
    score_case,
    seed_dialogue_state,
)
from app.extended_experiment import (  # noqa: E402
    ExtendedUnderstandingProvider,
    update_extended_dialogue_state,
)
from app.llm import build_client, write_report  # noqa: E402
from app.segse_experiment import (  # noqa: E402
    SEGSEUnderstandingProvider,
    update_segse_dialogue_state,
)


DEFAULT_DATASET = BACKEND_ROOT / "data" / "tablet_domain_segse_dev_v1.json"
DEFAULT_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_segse_dev_protocol_v1.json"
)
DEFAULT_OUTPUT = BACKEND_ROOT / "reports" / "tablet_domain_segse_dev_v1.json"
DEFAULT_SUMMARY = BACKEND_ROOT / "data" / "results" / "tablet_domain_segse_dev_v1.json"
BRANCH = "codex/segse-lite-experiment"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_lf_normalized(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=REPO_ROOT, text=True, encoding="utf-8"
    ).strip()


def _verify_frozen_inputs(
    *, manifest: Mapping[str, Any], dataset_path: Path, output_path: Path, summary_path: Path
) -> None:
    if _git("branch", "--show-current") != BRANCH:
        raise RuntimeError(f"SEGSE development run requires {BRANCH}")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked worktree must be clean before the first live run")
    if manifest["status"] != "development_contract_frozen_before_first_live_run":
        raise RuntimeError("protocol manifest is not in its pre-run frozen state")
    if manifest["live_run_completed"] is not False:
        raise RuntimeError("protocol manifest already records a live run")
    if output_path.exists() or summary_path.exists():
        raise RuntimeError("refusing to overwrite an existing SEGSE development artifact")
    if _sha256_lf_normalized(dataset_path) != manifest[
        "frozen_sources_sha256_lf_normalized"
    ]["data/tablet_domain_segse_dev_v1.json"]:
        raise RuntimeError("development fixture differs from the frozen manifest")
    for relative, expected in manifest["frozen_sources_sha256_lf_normalized"].items():
        actual = _sha256_lf_normalized(BACKEND_ROOT / relative)
        if actual != expected:
            raise RuntimeError(f"frozen source differs: {relative}")
    if tuple(manifest["live_arm_order"]) != ARM_ORDER:
        raise RuntimeError("live arm order differs from the frozen protocol")


def _trace_summary(path: Path) -> dict[str, Any]:
    records = [
        json.loads(line)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip())
    ] if path.exists() else []
    reported_models: dict[str, int] = {}
    structured_modes: dict[str, int] = {}
    for record in records:
        model = str(record.get("reported_model"))
        reported_models[model] = reported_models.get(model, 0) + 1
        mode = str(record.get("structured_mode"))
        structured_modes[mode] = structured_modes.get(mode, 0) + 1
    return {
        "logical_call_count": len(records),
        "validation_success_count": sum(
            bool(item.get("validation_success")) for item in records
        ),
        "schema_repair_count": sum(int(item.get("retry_count") or 0) for item in records),
        "transport_retry_count": sum(
            int(item.get("transport_retry_count") or 0) for item in records
        ),
        "http_attempt_count": sum(len(item.get("attempts", [])) for item in records),
        "fallback_count": sum(bool(item.get("fallback_used")) for item in records),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in records),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in records),
        "reported_models": reported_models,
        "structured_modes": structured_modes,
        "sha256": _sha256(path) if path.exists() else None,
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }


def _base_report(
    *,
    run_id: str,
    dataset_path: Path,
    manifest: Mapping[str, Any],
    trace_filenames: Mapping[str, str],
) -> dict[str, Any]:
    settings = load_llm_settings()
    return {
        "schema_version": "tablet-domain-segse-dev-run-v1",
        "study_status": "development_not_confirmatory",
        "run_id": run_id,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "version_control": {
            "branch": _git("branch", "--show-current"),
            "execution_commit": _git("rev-parse", "HEAD"),
            "tracked_worktree_clean_before_run": True,
            "base_commit": manifest["base_commit"],
        },
        "dataset": {
            "path": str(dataset_path),
            "sha256_lf_normalized": manifest["frozen_sources_sha256_lf_normalized"][
                "data/tablet_domain_segse_dev_v1.json"
            ],
            "case_count": manifest["development_case_count"],
            "contrast_family_count": manifest["contrast_family_count"],
        },
        "runtime": {
            "provider": settings.provider,
            "requested_model": settings.requested_model,
            "temperature": 0.0,
            "structured_mode": "json_schema",
        },
        "protocol": {
            "arm_order": list(ARM_ORDER),
            "independent_one_turn_cases": True,
            "one_understanding_logical_call_per_arm_case": True,
            "missing_output_as_empty_prediction": True,
            "downstream_catalog_calls": 0,
            "confirmatory_evidence": False,
        },
        "expected_live_logical_call_count": 48,
        "arms": {
            arm: {
                "status": "pending",
                "started_at": None,
                "completed_at": None,
                "trace_filename": trace_filenames[arm],
                "trace_summary": None,
                "cases": [],
                "metrics": None,
            }
            for arm in ARM_ORDER
        },
        "development_gate": None,
        "limitations": [
            "The fixture was designed for method development and is not untouched confirmatory evidence.",
            "The run evaluates Understanding and state transition only; catalog, ranking, hard-filter completion, and response quality are out of scope.",
            "B0 and D4 use independent non-bitwise-deterministic model calls.",
        ],
    }


async def _run_arm(
    *,
    arm: str,
    cases: list[dict[str, Any]],
    trace_filename: str,
    report: dict[str, Any],
    output_path: Path,
) -> None:
    payload = report["arms"][arm]
    payload["status"] = "running"
    payload["started_at"] = _utc_now()
    settings = load_llm_settings()
    if settings.provider == "luxia":
        settings.require_api_key()
    client = build_client(settings, trace=True, trace_filename=trace_filename)
    provider: Any = (
        ExtendedUnderstandingProvider(client, "c_semantic_noop")
        if arm == "b0_c_semantic_noop"
        else SEGSEUnderstandingProvider(client)
    )
    updater: Any = (
        update_extended_dialogue_state
        if arm == "b0_c_semantic_noop"
        else update_segse_dialogue_state
    )
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
                    conversation_id=f"segse-dev-{arm}-{case['id']}",
                    turn=1,
                )
                after, state_diff = updater(
                    before,
                    understanding,
                    [],
                    turn_id=f"{case['id']}-turn-1",
                )
            except Exception as exc:  # Missing output remains in every denominator.
                error = exc
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            score = score_case(
                arm=arm,
                case=case,
                before=before,
                after=after,
                understanding=understanding,
                error=error,
            )
            payload["cases"].append(
                {
                    "case_id": case["id"],
                    "family": case["family"],
                    "utterance": case["utterance"],
                    "latency_ms": latency_ms,
                    "prior_state": before.model_dump(mode="json"),
                    "understanding": (
                        understanding.model_dump(mode="json")
                        if understanding is not None
                        else None
                    ),
                    "state_diff": (
                        state_diff.model_dump(mode="json")
                        if state_diff is not None
                        else None
                    ),
                    "final_state": after.model_dump(mode="json"),
                    "score": score,
                }
            )
            write_report(output_path, report)
            print(
                f"{arm}: {case['id']} ({index}/{len(cases)}) "
                f"{'completed' if error is None else type(error).__name__}",
                flush=True,
            )
    finally:
        await client.aclose()
    payload["metrics"] = aggregate_arm(
        [item["score"] for item in payload["cases"]]
    )
    payload["status"] = "completed"
    payload["completed_at"] = _utc_now()
    trace_path = settings.log_dir / trace_filename
    payload["trace_summary"] = _trace_summary(trace_path)
    write_report(output_path, report)


def _compact_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "tablet-domain-segse-dev-result-v1",
        "study_status": report["study_status"],
        "run_id": report["run_id"],
        "status": report["status"],
        "started_at": report["started_at"],
        "completed_at": report["completed_at"],
        "version_control": report["version_control"],
        "dataset": report["dataset"],
        "runtime": report["runtime"],
        "protocol": report["protocol"],
        "arms": {
            arm: {
                "status": report["arms"][arm]["status"],
                "metrics": report["arms"][arm]["metrics"],
                "trace_summary": report["arms"][arm]["trace_summary"],
                "case_scores": [
                    item["score"] for item in report["arms"][arm]["cases"]
                ],
            }
            for arm in ARM_ORDER
        },
        "development_gate": report["development_gate"],
        "limitations": report["limitations"],
    }


async def execute(args: argparse.Namespace) -> dict[str, Any]:
    dataset_path = Path(args.dataset).resolve()
    manifest_path = Path(args.manifest).resolve()
    output_path = Path(args.output).resolve()
    summary_path = Path(args.summary).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    _verify_frozen_inputs(
        manifest=manifest,
        dataset_path=dataset_path,
        output_path=output_path,
        summary_path=summary_path,
    )
    cases = list(dataset["cases"])
    if len(cases) != manifest["development_case_count"]:
        raise RuntimeError("development case count differs from the frozen protocol")

    run_id = f"segse-dev-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    trace_filenames = {
        arm: f"tablet_segse_dev_{run_id}_{arm}.jsonl" for arm in ARM_ORDER
    }
    report = _base_report(
        run_id=run_id,
        dataset_path=dataset_path,
        manifest=manifest,
        trace_filenames=trace_filenames,
    )
    write_report(output_path, report)
    for arm in ARM_ORDER:
        await _run_arm(
            arm=arm,
            cases=cases,
            trace_filename=trace_filenames[arm],
            report=report,
            output_path=output_path,
        )
    report["development_gate"] = evaluate_development_gate(
        report["arms"]["b0_c_semantic_noop"]["metrics"],
        report["arms"]["d4_segse_lite"]["metrics"],
    )
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    write_report(output_path, report)
    compact = _compact_summary(report)
    write_report(summary_path, compact)
    return compact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    return parser


def main() -> None:
    result = asyncio.run(execute(_parser().parse_args()))
    print(json.dumps(result["development_gate"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
