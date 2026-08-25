"""Export the frozen official Full run as 20 human-readable Markdown logs.

The official raw report and LLM JSONL trace are intentionally Git-excluded.
This exporter combines their audit-relevant fields with the tracked frozen
holdout and writes ``log/ep1.md`` through ``log/ep20.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
DEFAULT_REPORT = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
DEFAULT_HOLDOUT = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "log"
CONDITION = "full"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if (line := raw.strip())
    ]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_text(value: Any, *, indent: int | None = 2) -> str:
    return json.dumps(value, ensure_ascii=False, indent=indent, sort_keys=False)


def json_block(value: Any) -> str:
    return f"````json\n{json_text(value)}\n````"


def text_block(value: Any) -> str:
    text = "" if value is None else str(value)
    return f"````text\n{text}\n````"


def quote(value: Any) -> str:
    text = "" if value is None else str(value)
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def inline(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")
    return text or "—"


def id_list(values: list[str] | None) -> str:
    if not values:
        return "—"
    return ", ".join(f"`{value}`" for value in values)


def pretty_raw_response(value: Any) -> str:
    if value is None:
        return text_block("null")
    if isinstance(value, str):
        try:
            return json_block(json.loads(value))
        except json.JSONDecodeError:
            return text_block(value)
    return json_block(value)


def first_seen_conversation_ids(records: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for record in records:
        conversation_id = record.get("conversation_id")
        if conversation_id and conversation_id not in seen:
            seen.add(conversation_id)
            ordered.append(conversation_id)
    return ordered


def rankings_section(turn: dict[str, Any]) -> str:
    rankings = sorted(turn.get("rankings") or [], key=lambda item: item["rank"])
    cards = (turn.get("final_response") or {}).get("product_cards") or []
    products = {
        card["product"]["parent_asin"]: card["product"]
        for card in cards
        if card.get("product", {}).get("parent_asin")
    }
    if not rankings:
        return "해당 턴에는 랭킹 출력이 없습니다."

    lines = [
        "| 순위 | Product ID | 상품명 | Total | Hard | Metadata | Subjective | Review | Reliability | Evidence review IDs |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for ranking in rankings[:3]:
        product_id = ranking["product_id"]
        product = products.get(product_id, {})
        score = ranking.get("score") or {}
        lines.append(
            "| {rank} | `{product_id}` | {title} | {total} | {hard} | {metadata} | "
            "{subjective} | {review} | {reliability} | {evidence} |".format(
                rank=ranking.get("rank", "—"),
                product_id=product_id,
                title=inline(product.get("title", "(카드 미출력)")),
                total=inline(score.get("total")),
                hard=inline(score.get("hard_constraint_match")),
                metadata=inline(score.get("metadata_match")),
                subjective=inline(score.get("subjective_need_match")),
                review=inline(score.get("review_evidence_score")),
                reliability=inline(score.get("evidence_reliability")),
                evidence=id_list(ranking.get("evidence_review_ids")),
            )
        )
    return "\n".join(lines)


def pipeline_section(turn: dict[str, Any]) -> str:
    traces = turn.get("trace") or []
    if not traces:
        return "해당 턴에는 pipeline node trace가 없습니다."
    lines = [
        "| 순서 | Node | Role / Owner | Lane | Latency (ms) | Output summary |",
        "| ---: | --- | --- | --- | ---: | --- |",
    ]
    for index, trace in enumerate(traces, start=1):
        summary = json_text(trace.get("output_summary"), indent=None)
        lines.append(
            f"| {index} | `{trace.get('node_id')}` | "
            f"{inline(trace.get('role'))} / {inline(trace.get('owner'))} | "
            f"{inline(trace.get('lane'))} | {inline(trace.get('latency_ms'))} | "
            f"<code>{inline(summary)}</code> |"
        )
    return "\n".join(lines)


def llm_call_section(record: dict[str, Any], index: int) -> str:
    lines = [
        f"#### GPT-4o-mini 호출 {index}: `{record.get('node', 'unknown')}`",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| timestamp | `{inline(record.get('timestamp'))}` |",
        f"| provider | `{inline(record.get('provider'))}` |",
        f"| requested / reported model | `{inline(record.get('requested_model'))}` / `{inline(record.get('reported_model'))}` |",
        f"| prompt version | `{inline(record.get('prompt_version'))}` |",
        f"| schema | `{inline(record.get('schema_name'))}` / `{inline(record.get('schema_version'))}` |",
        f"| structured mode | `{inline(record.get('structured_mode'))}` |",
        f"| validation success | `{inline(record.get('validation_success'))}` |",
        f"| fallback / schema retry / transport retry | `{inline(record.get('fallback_used'))}` / `{inline(record.get('retry_count'))}` / `{inline(record.get('transport_retry_count'))}` |",
        f"| latency | `{inline(record.get('latency_ms'))} ms` |",
        f"| input / output tokens | `{inline(record.get('input_tokens'))}` / `{inline(record.get('output_tokens'))}` |",
        "",
        "**Request messages**",
        "",
        json_block(record.get("messages") or []),
        "",
        "**Raw response**",
        "",
        pretty_raw_response(record.get("raw_response")),
        "",
        "**Validated output**",
        "",
        json_block(record.get("validated_output")),
        "",
        "**Validation errors**",
        "",
        json_block(record.get("validation_errors") or []),
        "",
        "**Attempts**",
        "",
        json_block(record.get("attempts") or []),
    ]
    return "\n".join(lines)


def completed_turn_section(
    gold_turn: dict[str, Any],
    turn: dict[str, Any],
    turn_metric: dict[str, Any] | None,
    llm_records: list[dict[str, Any]],
) -> str:
    final_response = turn.get("final_response") or {}
    recommendation = turn.get("recommendation") or {}
    lines = [
        f"## Turn {gold_turn['turn']}",
        "",
        f"- Turn ID: `{inline(turn.get('turn_id'))}`",
        f"- Gold policy lane: `{inline(gold_turn.get('gold_policy_lane'))}`",
        f"- Actual policy lane: `{inline((turn.get('policy') or {}).get('lane'))}`",
        f"- Gold State Diff: {id_list(gold_turn.get('gold_state_diff'))}",
        f"- Expected hard filters after turn: `{inline(json_text(gold_turn.get('expected_hard_filters_after_turn') or {}, indent=None))}`",
        "",
        "### 대화",
        "",
        "**User**",
        "",
        quote(gold_turn["utterance"]),
        "",
        "**Agent**",
        "",
        quote(final_response.get("message", "(Agent message 없음)")),
        "",
        "### Understanding (PLAN)",
        "",
        json_block(turn.get("understanding")),
        "",
        "### State Diff (MEMORY)",
        "",
        json_block(turn.get("state_diff")),
        "",
        "### Dialogue State after turn",
        "",
        json_block(turn.get("dialogue_state")),
        "",
        "### Policy (DECIDE)",
        "",
        json_block(turn.get("policy")),
        "",
        "### Query / Browse",
        "",
        "**Query**",
        "",
        json_block(turn.get("query")),
        "",
        "**Browse result summary**",
        "",
        json_block(turn.get("browse_result")),
        "",
        "### Recommendation / Response",
        "",
        f"- RA-Rec explanation: {inline(recommendation.get('explanation'))}",
        f"- Unresolved preferences: `{inline(json_text(recommendation.get('unresolved_preferences') or [], indent=None))}`",
        f"- Final next actions: `{inline(json_text(final_response.get('next_actions') or [], indent=None))}`",
        "",
        rankings_section(turn),
        "",
        "### Pipeline node trace",
        "",
        pipeline_section(turn),
        "",
        "### Turn evaluation",
        "",
        json_block(turn_metric),
        "",
        "### GPT-4o-mini call trace",
        "",
    ]
    if llm_records:
        for index, record in enumerate(llm_records, start=1):
            lines.extend([llm_call_section(record, index), ""])
    else:
        lines.append("이 턴에 대응하는 LLM trace record가 없습니다.")
    return "\n".join(lines).rstrip()


def failed_turn_section(
    gold_turn: dict[str, Any],
    scenario: dict[str, Any],
    llm_records: list[dict[str, Any]],
) -> str:
    lines = [
        f"## Turn {gold_turn['turn']} — 실행 실패",
        "",
        f"- Gold policy lane: `{inline(gold_turn.get('gold_policy_lane'))}`",
        f"- Gold State Diff: {id_list(gold_turn.get('gold_state_diff'))}",
        f"- Error type: `{inline(scenario.get('error_type'))}`",
        f"- Error classification: `{inline(scenario.get('error_classification'))}`",
        f"- Error message: {inline(scenario.get('error_message'))}",
        "",
        "### 대화",
        "",
        "**User**",
        "",
        quote(gold_turn["utterance"]),
        "",
        "**Agent**",
        "",
        "> 응답이 생성되지 않았습니다.",
        "",
        "### GPT-4o-mini call trace",
        "",
    ]
    if llm_records:
        for index, record in enumerate(llm_records, start=1):
            lines.extend([llm_call_section(record, index), ""])
    else:
        lines.append("실패 턴에 대응하는 LLM trace record가 없습니다.")
    return "\n".join(lines).rstrip()


def episode_markdown(
    *,
    episode_number: int,
    report: dict[str, Any],
    scenario: dict[str, Any],
    gold_scenario: dict[str, Any],
    conversation_id: str,
    records_by_turn: dict[int, list[dict[str, Any]]],
    raw_report_sha256: str,
    trace_sha256: str,
) -> str:
    condition = report["conditions"][CONDITION]
    completed_turns = {
        int(str(turn["turn_id"]).split("-")[-1]): turn
        for turn in scenario.get("turns") or []
    }
    turn_metrics = {
        int(metric["turn"]): metric for metric in scenario.get("turn_metrics") or []
    }
    lines = [
        f"# Episode {episode_number}: {scenario['title']}",
        "",
        "> 이 문서는 Git에서 제외된 official raw report와 GPT-4o-mini JSONL trace를 사람이 읽을 수 있는 형태로 재구성한 감사 로그다. 원본 API 인증 헤더는 로그 스키마에 저장되지 않는다.",
        "",
        "## 실행 정보",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| Episode / scenario | `ep{episode_number}` / `{scenario['scenario_id']}` |",
        f"| Condition | `{CONDITION}` |",
        f"| Run ID | `{report['run_id']}` |",
        f"| Conversation ID | `{conversation_id}` |",
        f"| Model | `{report['runtime'].get('reported_model') or 'gpt-4o-mini-2024-07-18'}` |",
        f"| Status | `{scenario['status']}` |",
        f"| Completed turns | `{scenario['completed_turn_count']}/{scenario['expected_turn_count']}` |",
        f"| Official run completed at | `{report.get('completed_at')}` |",
        f"| Raw report SHA-256 | `{raw_report_sha256}` |",
        f"| Full trace SHA-256 | `{trace_sha256}` |",
        "",
        "## 최종 상태 비교",
        "",
        f"- Expected active IDs: {id_list(scenario.get('final_state_ids_expected'))}",
        f"- Actual active IDs: {id_list(scenario.get('final_state_ids_actual'))}",
        f"- Counts: `{inline(json_text(scenario.get('final_state_counts'), indent=None))}`",
    ]
    if scenario.get("error_type"):
        lines.extend(
            [
                f"- Error turn: `{scenario.get('error_turn')}`",
                f"- Error: `{scenario.get('error_type')}` — {inline(scenario.get('error_message'))}",
            ]
        )
    lines.append("")

    for gold_turn in gold_scenario["turns"]:
        turn_number = int(gold_turn["turn"])
        if turn_number in completed_turns:
            section = completed_turn_section(
                gold_turn,
                completed_turns[turn_number],
                turn_metrics.get(turn_number),
                records_by_turn.get(turn_number, []),
            )
        elif scenario.get("error_turn") == turn_number:
            section = failed_turn_section(
                gold_turn,
                scenario,
                records_by_turn.get(turn_number, []),
            )
        else:
            section = "\n".join(
                [
                    f"## Turn {turn_number} — 미실행",
                    "",
                    "앞선 턴의 오류로 실행되지 않았습니다.",
                    "",
                    "**예정된 User utterance**",
                    "",
                    quote(gold_turn["utterance"]),
                ]
            )
        lines.extend([section, ""])

    expected_trace_name = condition["trace_filename"]
    lines.extend(
        [
            "## 원본 provenance",
            "",
            f"- Raw report: `backend/reports/{DEFAULT_REPORT.name}` (Git excluded)",
            f"- LLM trace: `backend/logs/{expected_trace_name}` (Git excluded)",
            f"- Frozen holdout: `backend/data/{DEFAULT_HOLDOUT.name}` (Git tracked)",
            "- Catalog 전체 후보와 전체 리뷰 payload는 Markdown에 복제하지 않고, 실제 응답·top-3 랭킹·evidence review ID·LLM request/response를 보존했다.",
            "",
        ]
    )
    return "\n".join(lines)


def export(report_path: Path, holdout_path: Path, output_dir: Path) -> list[Path]:
    report = load_json(report_path)
    holdout = load_json(holdout_path)
    if report.get("status") != "completed":
        raise RuntimeError("official report is not complete")
    condition = report["conditions"][CONDITION]
    scenarios = condition["scenarios"]
    gold_scenarios = holdout["scenarios"]
    if len(scenarios) != 20 or len(gold_scenarios) != 20:
        raise RuntimeError("expected exactly 20 official and 20 gold scenarios")

    trace_path = BACKEND_ROOT / "logs" / condition["trace_filename"]
    trace_records = load_jsonl(trace_path)
    if sha256_file(trace_path) != condition["trace_sha256"]:
        raise RuntimeError("Full trace SHA-256 differs from official report")

    conversation_ids = first_seen_conversation_ids(trace_records)
    if len(conversation_ids) != len(scenarios):
        raise RuntimeError("scenario/conversation count mismatch")
    gold_by_id = {scenario["id"]: scenario for scenario in gold_scenarios}
    records_by_conversation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in trace_records:
        records_by_conversation[record["conversation_id"]].append(record)

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    used_trace_records = 0
    raw_report_sha256 = sha256_file(report_path)
    for index, (scenario, conversation_id) in enumerate(
        zip(scenarios, conversation_ids, strict=True), start=1
    ):
        if scenario["scenario_id"] not in gold_by_id:
            raise RuntimeError(f"missing gold scenario: {scenario['scenario_id']}")
        completed = scenario.get("turns") or []
        if completed and completed[0]["conversation_id"] != conversation_id:
            raise RuntimeError(f"conversation order mismatch: {scenario['scenario_id']}")
        scenario_records = records_by_conversation[conversation_id]
        used_trace_records += len(scenario_records)
        records_by_turn: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for record in scenario_records:
            records_by_turn[int(record["turn"])].append(record)
        content = episode_markdown(
            episode_number=index,
            report=report,
            scenario=scenario,
            gold_scenario=gold_by_id[scenario["scenario_id"]],
            conversation_id=conversation_id,
            records_by_turn=records_by_turn,
            raw_report_sha256=raw_report_sha256,
            trace_sha256=condition["trace_sha256"],
        )
        target = output_dir / f"ep{index}.md"
        target.write_text(content, encoding="utf-8", newline="\n")
        outputs.append(target)

    if used_trace_records != len(trace_records):
        raise RuntimeError("not all Full trace records were exported")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    outputs = export(args.report.resolve(), args.holdout.resolve(), args.output_dir.resolve())
    total_bytes = sum(path.stat().st_size for path in outputs)
    print(f"exported={len(outputs)} total_bytes={total_bytes}")
    for path in outputs:
        print(path.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
