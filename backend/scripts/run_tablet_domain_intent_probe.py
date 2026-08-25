"""Run the exploratory turn-level hidden-intent probe over the frozen holdout.

동결된 holdout 데이터셋과 official raw run은 읽기만 한다. 결과는 새 파일로만 쓴다.
비교 단위는 턴이다. Gold DST는 81턴 전부, Full DST는 official run이 완주한 턴만 있다.

    python scripts\\run_tablet_domain_intent_probe.py --scenario th01
    python scripts\\run_tablet_domain_intent_probe.py
    python scripts\\run_tablet_domain_intent_probe.py --reaggregate-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import load_llm_settings  # noqa: E402
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_intent_probe import (  # noqa: E402
    CONDITION_ORDER,
    GUARDRAIL,
    PROBE_LABEL,
    SCHEMA_VERSION,
    compact_probe_result,
    full_dst_by_turn,
    render_probe_markdown,
    render_probe_report_markdown,
    run_intent_probe,
    sha256_file,
    summarize_probe,
)
from app.llm import build_client, write_report  # noqa: E402

GOLD = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
OFFICIAL_RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
OFFICIAL_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_holdout_official_v1.json"
)
RAW_OUTPUT = BACKEND_ROOT / "reports" / "tablet_domain_intent_probe_turn_v1.json"
MARKDOWN_OUTPUT = BACKEND_ROOT / "reports" / "tablet_domain_intent_probe_turn_v1.md"
RESULT_OUTPUT = (
    BACKEND_ROOT / "data" / "results" / "tablet_domain_intent_probe_turn_v1.json"
)
REPORT_OUTPUT = BACKEND_ROOT / "docs" / "tablet_domain_intent_probe_turn_v1.md"
MANIFEST_OUTPUT = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_intent_probe_turn_v1.json"
)
TRACE_FILENAME = "tablet_domain_intent_probe_turn_v1_llm_trace.jsonl"

#: manifest에 해시로 남길 소스. 이 목록에 없는 파일은 해싱하지 않는다.
SOURCE_FILES = (
    BACKEND_ROOT / "app" / "evaluation" / "tablet_domain_intent_probe.py",
    BACKEND_ROOT / "scripts" / "run_tablet_domain_intent_probe.py",
    BACKEND_ROOT / "scripts" / "verify_tablet_domain_intent_probe.py",
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


def _write_outputs(args: argparse.Namespace, report: dict) -> dict[str, object]:
    """git 제외 원본과 추적 대상 산출물을 함께 쓴다."""
    write_report(args.raw_output, report)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(render_probe_markdown(report), encoding="utf-8")

    compact = compact_probe_result(report)
    compact["source_artifacts"] = report.get("source_artifacts")
    compact["runtime"] = report.get("runtime")
    write_report(args.result_output, compact)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.write_text(
        render_probe_report_markdown(compact), encoding="utf-8"
    )

    missing = [path for path in SOURCE_FILES if not path.is_file()]
    if missing:
        raise RuntimeError(
            "required source file missing: " + ", ".join(str(path) for path in missing)
        )
    manifest = {
        "schema_version": "tablet-domain-intent-probe-turn-manifest-v1",
        "status": "exploratory_turn_level_probe_complete",
        "analysis_schema_version": SCHEMA_VERSION,
        "analysis_label": PROBE_LABEL,
        "comparison_unit": "turn",
        "official_run_reexecuted": False,
        "frozen_prompt_or_schema_modified": False,
        "new_gold_labels_added": False,
        "accuracy_or_ndcg_computed": False,
        "additional_llm_calls": (report.get("planned_calls") or {}).get("total"),
        "execution_commit": _git(["rev-parse", "HEAD"]),
        "worktree_dirty_at_execution": bool(_git(["status", "--porcelain"])),
        "immutable_inputs": report.get("source_artifacts"),
        "outputs": {
            "raw_report": _artifact(args.raw_output, git_excluded=True),
            "full_detail_markdown": _artifact(args.markdown_output, git_excluded=True),
            "compact_result": _artifact(args.result_output),
            "tracked_report": _artifact(args.report_output),
        },
        "source_files": {
            _relative(path): {"sha256": sha256_file(path)} for path in SOURCE_FILES
        },
        "runtime": report.get("runtime"),
        "summary": report["summary"],
        "guardrail": report["guardrail"],
    }
    write_report(args.manifest_output, manifest)
    return {
        "raw_report": manifest["outputs"]["raw_report"],
        "full_detail_markdown": manifest["outputs"]["full_detail_markdown"],
        "compact_result": manifest["outputs"]["compact_result"],
        "tracked_report": manifest["outputs"]["tracked_report"],
        "manifest": _artifact(args.manifest_output),
    }


def _reaggregate(args: argparse.Namespace) -> None:
    """LLM 호출 없이 기존 raw report에서 요약과 모든 문서를 다시 만든다."""
    report = json.loads(args.raw_output.read_text(encoding="utf-8"))
    if report.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(
            f"raw report schema is {report.get('schema_version')!r}, expected {SCHEMA_VERSION!r}"
        )
    report["summary"] = summarize_probe(report)
    report["guardrail"] = GUARDRAIL
    outputs = _write_outputs(args, report)
    print(
        json.dumps(
            {
                "mode": "reaggregate_only",
                "additional_llm_calls": 0,
                **outputs,
                "summary": report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def _planned_calls(
    gold, official_raw: dict, scenario_ids: list[str] | None, conditions: tuple[str, ...]
) -> dict[str, int]:
    """조건별로 상태가 실제로 존재하는 턴 수를 세어 호출량을 미리 알린다."""
    scenarios = [
        scenario
        for scenario in gold.scenarios
        if scenario_ids is None or scenario.id in scenario_ids
    ]
    counts = {condition: 0 for condition in conditions}
    for scenario in scenarios:
        for condition in conditions:
            if condition == "gold_dst":
                counts[condition] += len(scenario.turns)
                continue
            states, _context = full_dst_by_turn(
                official_raw, scenario, condition=condition
            )
            counts[condition] += sum(
                1 for gold_turn in scenario.turns if gold_turn.turn in states
            )
    counts["total"] = sum(counts[condition] for condition in conditions)
    counts["scenarios"] = len(scenarios)
    counts["gold_turns"] = sum(len(scenario.turns) for scenario in scenarios)
    return counts


async def _main(args: argparse.Namespace) -> None:
    if args.reaggregate_only:
        _reaggregate(args)
        return

    outputs = [
        args.raw_output,
        args.markdown_output,
        args.result_output,
        args.report_output,
        args.manifest_output,
    ]
    existing = [path for path in outputs if path.exists()]
    if existing and not args.overwrite:
        raise RuntimeError(
            "probe outputs already exist ("
            + ", ".join(_relative(path) for path in existing)
            + "); pass --overwrite to replace them"
        )

    manifest = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
    expected = manifest["git_excluded_artifacts"]["raw_report"]["sha256"]
    if sha256_file(args.official_raw) != expected:
        raise RuntimeError(
            "official raw report hash differs from the frozen manifest; refusing to probe"
        )

    gold = load_tablet_holdout_dataset(args.gold)
    official_raw = json.loads(args.official_raw.read_text(encoding="utf-8"))

    settings = load_llm_settings()
    if settings.provider == "luxia":
        settings.require_api_key()
    client = build_client(settings, trace_filename=TRACE_FILENAME)

    conditions = tuple(args.conditions or CONDITION_ORDER)
    scenario_ids = list(args.scenario) or None
    planned = _planned_calls(gold, official_raw, scenario_ids, conditions)
    print(
        f"turn-level intent probe: {planned['total']} LLM calls over "
        f"{planned['scenarios']} episodes / {planned['gold_turns']} gold turns "
        + ", ".join(
            f"{condition}={planned[condition]}" for condition in conditions
        ),
        flush=True,
    )

    try:
        report = await run_intent_probe(
            client=client,
            gold=gold,
            official_raw=official_raw,
            conditions=conditions,
            scenario_ids=scenario_ids,
            checkpoint_path=args.raw_output,
        )
    finally:
        await client.aclose()

    report["planned_calls"] = planned
    report["source_artifacts"] = {
        "gold_holdout": _artifact(args.gold),
        "official_raw": _artifact(args.official_raw, git_excluded=True),
        "official_manifest": _artifact(OFFICIAL_MANIFEST),
    }
    report["runtime"] = {
        "provider": settings.provider,
        "requested_model": settings.requested_model,
        "temperature": 0.0,
        "structured_mode_preferred": client.preferred_mode,
        "trace_filename": TRACE_FILENAME,
        "execution_commit": _git(["rev-parse", "HEAD"]),
        "worktree_dirty_at_execution": bool(_git(["status", "--porcelain"])),
    }
    outputs_summary = _write_outputs(args, report)
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "probe_label": PROBE_LABEL,
                "status": report["status"],
                **outputs_summary,
                "summary": report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--official-raw", type=Path, default=OFFICIAL_RAW)
    parser.add_argument("--raw-output", type=Path, default=RAW_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=MARKDOWN_OUTPUT)
    parser.add_argument("--result-output", type=Path, default=RESULT_OUTPUT)
    parser.add_argument("--report-output", type=Path, default=REPORT_OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=MANIFEST_OUTPUT)
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="한 시나리오만 돌린다. 여러 번 지정할 수 있다.",
    )
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=list(CONDITION_ORDER),
        default=None,
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--reaggregate-only",
        action="store_true",
        help="LLM 호출 없이 기존 raw report에서 요약과 Markdown만 다시 만든다.",
    )
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
