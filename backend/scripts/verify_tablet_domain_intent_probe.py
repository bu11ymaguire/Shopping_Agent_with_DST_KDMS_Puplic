"""Offline verification for the exploratory turn-level hidden-intent probe.

LLM 호출 없이 다음을 확인한다.

    1. 20개 시나리오 81턴 전부에서 Gold DST가 누적되고, 마지막 스냅숏이 동결된
       final gold state와 일치한다. 누적이 단조 증가인지도 확인한다.
    2. Full DST가 official raw에서 턴별로 파싱되고, 완주하지 않은 턴은 조용히
       넘기지 않고 사유가 남는다.
    3. 두 조건이 정확히 같은 렌더러 키 집합을 받는다.
    4. 렌더러가 발화 원문·시나리오 제목·상품 ID를 LLM 입력에 넣지 않는다.
    5. strict JSON Schema 변환과 출력 계약이 mock transport로 왕복한다.
    6. 턴 단위 상태 차이와 갈라짐 경과가 단순 집합 연산과 일치한다.
    7. 요약과 Markdown이 guardrail 문구와 동작하는 앵커를 유지한다.
    8. 동결된 official raw 해시가 manifest와 일치한다(파일이 있을 때만).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import LLMSettings  # noqa: E402
from app.evaluation.tablet_domain_holdout import (  # noqa: E402
    load_tablet_holdout_dataset,
)
from app.evaluation.tablet_domain_intent_probe import (  # noqa: E402
    CONDITION_ORDER,
    FACET_ORDER,
    GUARDRAIL,
    IntentProbeOutput,
    compact_probe_result,
    episode_divergence_trajectory,
    full_dst_by_turn,
    gold_dst_by_turn,
    heading_anchor,
    notable_divergence_turns,
    render_probe_markdown,
    render_probe_report_markdown,
    render_state_for_probe,
    run_intent_probe,
    sha256_file,
    state_shape,
    turn_state_divergence,
)
from app.llm import MockTransport, build_client  # noqa: E402
from app.llm.json_utils import to_strict_json_schema  # noqa: E402

GOLD = BACKEND_ROOT / "data" / "tablet_domain_multiturn_holdout_v1.json"
OFFICIAL_RAW = BACKEND_ROOT / "reports" / "tablet_domain_holdout_official_v1.json"
OFFICIAL_MANIFEST = (
    BACKEND_ROOT / "data" / "manifests" / "tablet_domain_holdout_official_v1.json"
)
MOCK_SCENARIOS = ["th01", "th11", "th17", "th20"]

MOCK_PROBE_JSON = json.dumps(
    {
        "situation_summary": "Mock summary for offline schema verification.",
        "hypotheses": [
            {
                "hypothesis_text": "Mock situational hypothesis.",
                "hypothesis_type": "situational_context",
                "related_attributes": ["price"],
                "evidence_ids": ["category_tablet"],
                "suggested_scope": "current_purchase",
                "alternative_explanations": ["Mock alternative."],
                "abstain_from_ranking": True,
            }
        ],
        "missing_information": ["budget"],
    },
    ensure_ascii=False,
)

RENDERER_KEYS = {
    "domain_route",
    "category",
    "hard_constraints",
    "soft_preferences",
    "facets",
    "tradeoffs",
    "rejected_items",
    "inspected_item_count",
    "purchased_item_count",
    "has_current_item",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _leaf_strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in _leaf_strings(value)]
    if isinstance(node, list):
        return [text for item in node for text in _leaf_strings(item)]
    return []


async def _mock_probe_run(gold, official_raw) -> dict:
    transport = MockTransport()
    transport.register(
        IntentProbeOutput.__name__, lambda messages, response_format: MOCK_PROBE_JSON
    )
    client = build_client(
        LLMSettings(provider="mock"), transport=transport, trace=False
    )
    try:
        return await run_intent_probe(
            client=client,
            gold=gold,
            official_raw=official_raw,
            scenario_ids=MOCK_SCENARIOS,
        )
    finally:
        await client.aclose()


def main() -> None:
    gold = load_tablet_holdout_dataset(GOLD)
    check(len(gold.scenarios) == 20, "frozen holdout must hold 20 scenarios")
    total_turns = sum(len(scenario.turns) for scenario in gold.scenarios)
    check(total_turns == 81, f"frozen holdout must hold 81 turns, found {total_turns}")

    # 1. Gold DST 누적
    gold_snapshots = {}
    for scenario in gold.scenarios:
        snapshots = gold_dst_by_turn(scenario)
        check(
            sorted(snapshots) == [turn.turn for turn in scenario.turns],
            f"{scenario.id}: gold DST must cover every frozen turn exactly once",
        )
        previous: set[str] = set()
        for turn in scenario.turns:
            ids = set(state_shape(snapshots[turn.turn])["active_canonical_ids"])
            check(
                previous <= ids,
                f"{scenario.id} turn {turn.turn}: accumulated gold DST must not drop IDs",
            )
            previous = ids
        check(
            previous == set(scenario.final_gold_state_ids),
            f"{scenario.id}: last gold DST must equal the frozen final gold state",
        )
        gold_snapshots[scenario.id] = snapshots
    print(f"gold DST accumulated: {total_turns} turn snapshots over 20 scenarios")

    # 3-4. 렌더러 계약
    sample = render_state_for_probe(gold_snapshots["th01"][1])
    check(set(sample) == RENDERER_KEYS, "renderer key set changed unexpectedly")
    facet_names = [item["facet"] for item in sample["facets"]]
    check(
        facet_names == [name for name in FACET_ORDER if name in facet_names],
        "facets must follow the fixed FACET_ORDER",
    )
    rendered_text = " ".join(_leaf_strings(sample)).casefold()
    for turn in gold.scenarios[0].turns:
        check(
            turn.utterance.casefold() not in rendered_text,
            "renderer must not leak the original utterance into the LLM input",
        )
    check(
        gold.scenarios[0].title.casefold() not in rendered_text,
        "renderer must not leak the designed scenario title",
    )
    # 태그 어휘는 canonical ID와 의도적으로 겹치므로 단어 단위 누출 검사를 하지 않는다.
    check(
        "product_id" not in json.dumps(sample),
        "renderer must not expose product identifiers",
    )
    check(
        "turn" not in {key.casefold() for key in sample},
        "renderer must not expose the turn index",
    )
    print("renderer withholds utterances, title, turn index, and product identifiers")

    # 5. strict schema 변환
    schema = to_strict_json_schema(IntentProbeOutput)
    check(schema["additionalProperties"] is False, "strict schema needs closed objects")
    check(
        set(schema["required"])
        == {"situation_summary", "hypotheses", "missing_information"},
        "strict schema must require every top-level field",
    )
    check(
        set(schema["$defs"]["ProbeHypothesis"]["required"])
        == {
            "hypothesis_text",
            "hypothesis_type",
            "related_attributes",
            "evidence_ids",
            "suggested_scope",
            "alternative_explanations",
            "abstain_from_ranking",
        },
        "hypothesis schema must match the flow.md field contract",
    )
    check(
        "confidence" not in json.dumps(schema),
        "the LLM must not be asked for confidence; code owns that value",
    )
    print("strict JSON Schema contract verified")

    if not OFFICIAL_RAW.is_file():
        print("official raw report absent; skipping full-condition checks")
        print("verify_tablet_domain_intent_probe: OK (partial)")
        return

    manifest = json.loads(OFFICIAL_MANIFEST.read_text(encoding="utf-8"))
    check(
        sha256_file(OFFICIAL_RAW)
        == manifest["git_excluded_artifacts"]["raw_report"]["sha256"],
        "official raw report hash differs from the frozen manifest",
    )
    official_raw = json.loads(OFFICIAL_RAW.read_text(encoding="utf-8"))

    # 2. Full DST 턴별 확보
    available = 0
    missing = 0
    for scenario in gold.scenarios:
        states, context = full_dst_by_turn(official_raw, scenario)
        check(
            "parse_errors" in context,
            f"{scenario.id}: full DST context must record parse errors explicitly",
        )
        check(
            len(states) <= len(scenario.turns),
            f"{scenario.id}: full DST cannot exceed the frozen turn count",
        )
        for turn in scenario.turns:
            if turn.turn in states:
                available += 1
                check(
                    set(render_state_for_probe(states[turn.turn])) == RENDERER_KEYS,
                    f"{scenario.id} turn {turn.turn}: both conditions must share one renderer",
                )
            else:
                missing += 1
    check(
        available + missing == total_turns,
        "every frozen turn must be classified as available or missing",
    )
    print(f"full DST turns: {available} available, {missing} not completed")

    # 6-7. mock 왕복, 차이 계산, Markdown
    report = asyncio.run(_mock_probe_run(gold, official_raw))
    check(report["status"] == "completed", "mock probe run must complete")
    check(
        report["probe_contract"]["comparison_unit"] == "turn",
        "the comparison unit must be the turn",
    )
    expected_turns = sum(
        len(scenario.turns)
        for scenario in gold.scenarios
        if scenario.id in MOCK_SCENARIOS
    )
    check(
        report["summary"]["turn_count"] == expected_turns,
        f"mock run must cover {expected_turns} turns",
    )
    check(
        list(report["probe_contract"]["conditions"]) == list(CONDITION_ORDER),
        "default condition order changed",
    )

    for episode in report["episodes"]:
        for turn_record in episode["turns"]:
            gold_entry = turn_record["conditions"]["gold_dst"]
            check(
                gold_entry["probe"] is not None,
                f"{episode['scenario_id']} turn {turn_record['turn']}: gold must always probe",
            )
            row = turn_state_divergence(turn_record)
            gold_ids = set(gold_entry["state_shape"]["active_canonical_ids"])
            other = turn_record["conditions"]["full_dst"]
            if other.get("state_shape") is None:
                check(
                    row["comparable"] is False
                    and row["other_ids"] is None
                    and row["missing"] == sorted(gold_ids),
                    f"{episode['scenario_id']} turn {turn_record['turn']}: "
                    "absent state must report every gold ID as missing",
                )
                continue
            other_ids = set(other["state_shape"]["active_canonical_ids"])
            check(
                row["missing"] == sorted(gold_ids - other_ids)
                and row["extra"] == sorted(other_ids - gold_ids),
                f"{episode['scenario_id']} turn {turn_record['turn']}: "
                "divergence must be a plain set difference",
            )

        trajectory = episode_divergence_trajectory(episode)
        diverged = [
            row["turn"]
            for row in trajectory["rows"]
            if row["comparable"] and row["divergence_size"] > 0
        ]
        check(
            trajectory["first_divergence_turn"] == (diverged[0] if diverged else None),
            f"{episode['scenario_id']}: first divergence turn must be the earliest diverged turn",
        )
        check(
            trajectory["diverged_turn_count"] == len(diverged),
            f"{episode['scenario_id']}: diverged turn count must match the row set",
        )
        sizes = trajectory["divergence_size_by_turn"]
        check(
            sizes
            == [
                row["divergence_size"]
                for row in trajectory["rows"]
                if row["comparable"]
            ],
            f"{episode['scenario_id']}: divergence trajectory must follow comparable turns in order",
        )
        check(
            trajectory["divergence_never_shrinks"]
            == all(later >= earlier for earlier, later in zip(sizes, sizes[1:])),
            f"{episode['scenario_id']}: monotonicity flag must match the trajectory",
        )

    summary = report["summary"]
    check(summary["metric_claim_allowed"] is False, "summary must refuse metric claims")
    check(
        summary["state_divergence"]["comparison_condition"] == "full_dst",
        "the comparison condition must be full_dst",
    )
    check(
        summary["state_divergence"]["identical_state_turns"]
        + summary["state_divergence"]["diverged_state_turns"]
        == summary["state_divergence"]["comparable_turns"],
        "comparable turns must split into identical and diverged",
    )
    # 단조성 분모는 실제로 갈린 에피소드로 한정돼야 한다.
    monotonic_expected = [
        episode_divergence_trajectory(episode)
        for episode in report["episodes"]
    ]
    monotonic_expected = [
        trajectory
        for trajectory in monotonic_expected
        if trajectory["first_divergence_turn"] is not None
        and trajectory["comparable_turns"] > 1
    ]
    check(
        summary["state_divergence"][
            "episodes_with_divergence_and_multiple_comparable_turns"
        ]
        == len(monotonic_expected),
        "monotonicity denominator must exclude episodes that never diverged",
    )
    check(
        summary["state_divergence"]["episodes_where_divergence_never_shrinks"]
        == sum(
            1
            for trajectory in monotonic_expected
            if trajectory["divergence_never_shrinks"]
        ),
        "monotonicity numerator must match the diverged trajectories",
    )
    for condition in CONDITION_ORDER:
        stat = summary["per_condition"][condition]
        check(
            stat["turns"] == expected_turns,
            f"{condition}: every turn must appear in the per-condition summary",
        )
        check(
            "verbatim_utterance_value_text_count" in stat,
            f"{condition}: the value_text surface asymmetry must stay instrumented",
        )
    check(
        summary["per_condition"]["gold_dst"]["verbatim_utterance_value_text_count"] == 0,
        "gold value_text is derived from canonical IDs and must not match utterances",
    )
    check(
        GUARDRAIL["comparison_unit"] == "turn"
        and GUARDRAIL["accuracy_or_ndcg_computed"] is False
        and GUARDRAIL["new_gold_labels_added"] is False
        and GUARDRAIL["official_run_reexecuted"] is False
        and GUARDRAIL["resolves_hidden_intent_analysis_not_evaluable_metrics"] is False,
        "guardrail flags must stay conservative",
    )

    markdown = render_probe_markdown(report)
    for needle in (
        "정성 탐색 관찰",
        "official run 재실행 없음",
        "새 gold label 없음",
        "턴 단위 상태 차이 요약",
        "에피소드별 갈라짐 경과",
        "턴별 차이 크기",
        "재현 정보",
    ):
        check(needle in markdown, f"markdown must keep the section or phrase {needle!r}")
    headings = {
        heading_anchor(line[4:].strip())
        for line in markdown.splitlines()
        if line.startswith("### ")
    }
    linked = set(re.findall(r"\]\(#([^)]+)\)", markdown))
    check(
        linked and linked <= headings,
        f"markdown anchors must resolve to real headings; dangling: {sorted(linked - headings)}",
    )
    turn_headings = sum(1 for line in markdown.splitlines() if line.startswith("#### "))
    check(
        turn_headings == expected_turns,
        f"markdown must render one detail block per turn; found {turn_headings}",
    )
    print("mock probe round-trip, divergence trajectory, summary, and markdown verified")

    # 압축 결과와 추적 레포트 계약
    compact = compact_probe_result(report)
    check(
        compact["schema_version"] == report["schema_version"],
        "compact result must keep the analysis schema version",
    )
    check(
        compact["summary"] == report["summary"],
        "compact result must not recompute the summary differently",
    )
    check(
        "rendered_state" not in json.dumps(compact),
        "compact result must drop the rendered LLM input payloads",
    )
    compact_turns = sum(len(episode["turns"]) for episode in compact["episodes"])
    check(
        compact_turns == expected_turns,
        f"compact result must keep every turn; found {compact_turns}",
    )
    for episode, compact_episode in zip(report["episodes"], compact["episodes"]):
        trajectory = episode_divergence_trajectory(episode)
        for key, value in compact_episode["divergence_trajectory"].items():
            check(
                trajectory[key] == value,
                f"{episode['scenario_id']}: compact trajectory field {key!r} must match",
            )
        for turn_record, compact_turn in zip(
            episode["turns"], compact_episode["turns"]
        ):
            check(
                compact_turn["turn"] == turn_record["turn"],
                "compact turns must stay in frozen turn order",
            )
            for name, entry in turn_record["conditions"].items():
                check(
                    compact_turn["conditions"][name]["probe"] == entry.get("probe"),
                    "compact result must preserve the probe output verbatim",
                )

    notable = notable_divergence_turns(report)
    check(
        notable == compact["notable_divergence_turns"],
        "notable turns must be deterministic across calls",
    )
    check(
        all(item["divergence_increase"] > 0 for item in notable),
        "notable turns must be turns where divergence grew",
    )
    check(
        notable == sorted(
            notable,
            key=lambda item: (
                -item["divergence_increase"],
                -item["divergence_size"],
                item["scenario_id"],
                item["turn"],
            ),
        ),
        "notable turns must follow the documented deterministic ordering",
    )

    tracked = render_probe_report_markdown(compact)
    for needle in (
        "정성 탐색 관찰 완료",
        "official run 재실행",
        "새 gold label 추가는 없다",
        "턴 단위 상태 차이",
        "에피소드별 갈라짐 궤적",
        "차이가 가장 크게 벌어진 턴",
        "Artifact",
    ):
        check(
            needle in tracked,
            f"tracked report must keep the section or phrase {needle!r}",
        )
    check(
        "reports/tablet_domain_intent_probe_turn_v1.json" in tracked
        and "logs/tablet_domain_intent_probe_turn_v1_llm_trace.jsonl" in tracked,
        "tracked report must point at the git-excluded raw artifacts",
    )
    print("compact result, notable turn selection, and tracked report verified")
    print("verify_tablet_domain_intent_probe: OK")


if __name__ == "__main__":
    main()
