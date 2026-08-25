"""Exploratory turn-level hidden-intent probe over frozen tablet-holdout states.

무엇을 하는가
-------------
동결된 20개 holdout 에피소드의 **매 턴마다** 누적 대화 상태 두 개를 만든다.

    gold_dst  사전 동결된 turn 주석을 1턴부터 해당 턴까지 누적한 Gold DST
    full_dst  official raw run의 Full 조건이 해당 턴을 처리한 뒤의 DialogueState

두 스냅숏을 **같은 렌더러**로 직렬화해 LLM에게 주고, 발화 원문 없이 상태만 보고
"이 사람은 어떤 상황인가"와 숨은 의도 가설을 구조화해 답하게 한다. 그 결과를 해당
턴의 설계 발화와 나란히 놓은 정성 비교 artifact를 만든다.

비교 단위가 턴이므로 에피소드 단위로는 보이지 않던 것이 드러난다.

    - 어느 턴에서 Full DST가 Gold DST와 처음 갈라지는가
    - 갈라진 뒤 회복되는가, 끝까지 유지되는가
    - 상태가 갈라진 턴에서 추론된 상황이 실제로 달라지는가

마지막 턴 비교는 이전 에피소드 단위 비교와 같은 스냅숏을 본다. 즉 턴 단위는
에피소드 단위를 포함한다.

무엇을 하지 않는가
------------------
- 동결된 system/prompt/schema/holdout/official raw를 수정하거나 재실행하지 않는다.
- 새 gold label을 만들지 않는다. 설계 쪽 대조 정보는 이미 동결된 title/tags/발화뿐이다.
- 점수·정확도·NDCG를 계산하지 않는다. 이것은 measured claim이 아니라 탐색 관찰이다.
- `hidden_intent_analysis`의 네 metric(`not_evaluable_with_current_holdout`)을
  대체하거나 해소하지 않는다.
- 생성된 가설은 분석 artifact이며 pipeline 상태나 랭킹에 들어가지 않는다.

flow.md 계약에 맞춰 LLM은 `hypothesis_text`, `hypothesis_type`, `related_attributes`,
`evidence_ids`, `suggested_scope`, `alternative_explanations`, `abstain_from_ranking`만
생성한다. confidence·uncertainty·ranking impact는 코드의 책임이므로 LLM에게 요구하지
않으며, 이 탐색 단계에서는 계산하지도 않는다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.evaluation.tablet_domain_automatic import _apply_gold_turn_to_oracle_state
from app.evaluation.tablet_domain_gold_oracle_rankings import (
    reconstruct_final_gold_state,
)
from app.evaluation.tablet_domain_holdout import (
    TabletHoldoutDataset,
    TabletHoldoutScenario,
    TabletHoldoutTurn,
)
from app.evaluation.tablet_domain_official import active_state_ids
from app.llm import LLMClient, StructuredOutputError, system, user
from app.models import DialogueState
from app.nodes.actual_state_manager import create_tablet_environment_state

SCHEMA_VERSION = "tablet-domain-intent-probe-turn-v1"
PROBE_LABEL = "exploratory_qualitative_turn_level_hidden_intent_probe"
INTENT_PROBE_PROMPT_VERSION = "latent-hypothesis-probe-en-v1"

#: 렌더링과 리포트에서 항상 이 순서로 facet을 노출한다.
FACET_ORDER: tuple[str, ...] = (
    "subjective_property",
    "event",
    "activity",
    "goal_purpose",
    "goal_audience",
)

CONDITION_ORDER: tuple[str, ...] = ("gold_dst", "full_dst")

CONDITION_HEADINGS = {
    "gold_dst": "Gold DST 기반 추론",
    "full_dst": "Full-Memory DST 기반 추론",
}

#: 상태 ID를 가리킬 때 쓰는 짧은 이름. "기반 추론"을 붙이지 않는다.
CONDITION_LABELS = {
    "gold_dst": "Gold DST",
    "full_dst": "Full-Memory DST",
    "no_memory_dst": "No-memory DST",
}

#: 조건 이름 → official raw의 condition 키.
RAW_CONDITION_OF = {"full_dst": "full", "no_memory_dst": "no_memory"}


# --------------------------------------------------------------------------- #
# LLM 출력 계약
# --------------------------------------------------------------------------- #


class ProbeContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


HypothesisType = Literal["situational_context", "tradeoff", "motive_hypothesis"]
HypothesisScope = Literal["current_purchase", "category", "persistent"]


class ProbeHypothesis(ProbeContract):
    """flow.md의 Latent Hypothesis Generator 출력 필드만 담는다."""

    hypothesis_text: str = Field(min_length=1)
    hypothesis_type: HypothesisType
    related_attributes: list[str]
    evidence_ids: list[str]
    suggested_scope: HypothesisScope
    alternative_explanations: list[str]
    abstain_from_ranking: bool


class IntentProbeOutput(ProbeContract):
    """상태 스냅숏 하나에 대한 상황 추론 결과."""

    schema_version: ClassVar[str] = "intent-probe-v1"

    situation_summary: str = Field(min_length=1)
    hypotheses: list[ProbeHypothesis] = Field(min_length=1, max_length=4)
    missing_information: list[str]


INTENT_PROBE_SYSTEM_PROMPT = """You are the Latent Hypothesis Generator of a tablet-shopping assistant.

You receive ONE accumulated dialogue state snapshot taken at a single point in an ongoing
shopping conversation. You do not receive the original utterances, the product catalog, or any
recommendation. The snapshot is the only evidence available. The conversation may continue, so
the snapshot can be partial.

Your job has two parts.

1. situation_summary: two or three sentences describing, in plain English, what shopping
   situation this state most plausibly represents. Who appears to be shopping, for what use,
   and under what practical circumstances. Stay inside what the snapshot supports.

2. hypotheses: one to four structured hidden-intent hypotheses. A hidden intent is a motive,
   situational circumstance, or priority ordering that plausibly explains the recorded state
   but is not itself literally recorded as a constraint.

Rules for hypotheses:
- hypothesis_type is situational_context for a circumstance of this purchase occasion,
  tradeoff for a priority ordering between two competing criteria, and motive_hypothesis for
  an underlying goal or motivation.
- related_attributes lists the tablet attributes the hypothesis would affect, such as price,
  storage, RAM, weight, battery, display, audio, durability, rating, or operating system.
- evidence_ids lists ONLY canonical_id values that literally appear in the given snapshot.
  Never invent an identifier. If a hypothesis has no snapshot support, do not emit it.
- suggested_scope is current_purchase when the hypothesis applies to this purchase occasion
  only, category when it plausibly applies to tablet shopping generally, and persistent when
  it looks like a durable trait of this shopper. Prefer the narrowest scope the evidence
  supports.
- alternative_explanations lists competing readings of the same evidence. Leave it empty only
  when no plausible alternative exists.
- abstain_from_ranking is true when the hypothesis is not confirmed by the snapshot and should
  therefore be displayed as context without changing product ranking.

Do not infer a lasting preference from a purchase or an inspection alone; a purchase outcome
can reflect situational availability rather than taste. Do not restate a recorded constraint
as if it were a hidden intent. Do not output confidence numbers; the application computes those.
missing_information lists the pieces of information a clarifying question should target next.

An empty or nearly empty snapshot is a valid input, and is expected early in a conversation.
In that case say plainly that the state carries almost no information and keep the hypotheses
minimal and explicitly low-support.
"""


# --------------------------------------------------------------------------- #
# 결정론적 렌더러 — 두 조건에 동일하게 적용한다
# --------------------------------------------------------------------------- #


def _preference_entry(canonical_id: str, value: Any) -> dict[str, Any]:
    return {
        "canonical_id": canonical_id,
        "value_text": value.value_text,
        "origin": value.origin,
        "status": value.status,
    }


def render_state_for_probe(state: DialogueState) -> dict[str, Any]:
    """상태를 LLM 입력용 JSON으로 직렬화한다.

    두 조건이 정확히 같은 필드 집합·같은 정렬을 받도록 한 함수만 사용한다.
    상품 ID와 발화 원문은 넣지 않는다.
    """
    facets = []
    for facet in FACET_ORDER:
        value = state.subjective_needs.get_facet(facet)  # type: ignore[arg-type]
        if value is None:
            continue
        facets.append({"facet": facet, **_preference_entry(value.canonical_id, value)})

    return {
        "domain_route": state.domain_route,
        "category": state.category.canonical_id if state.category else None,
        "hard_constraints": [
            _preference_entry(key, value)
            for key, value in sorted(state.hard_constraints.items())
        ],
        "soft_preferences": [
            _preference_entry(key, value)
            for key, value in sorted(state.soft_constraints.items())
        ],
        "facets": facets,
        "tradeoffs": [
            {"canonical_id": item.canonical_id, "value_text": item.value_text}
            for item in state.tradeoffs
        ],
        "rejected_items": [
            {
                "reason_text": item.reason.value_text,
                "reason_type": item.reason_type,
                "reason_canonical_id": item.reason.canonical_id,
            }
            for item in state.rejected_items
        ],
        "inspected_item_count": len(state.inspected_items),
        "purchased_item_count": len(state.purchased_items),
        "has_current_item": state.current_item is not None,
    }


def state_shape(state: DialogueState) -> dict[str, Any]:
    """리포트에 남길 상태 규모 요약. 점수가 아니라 서술 통계다."""
    return {
        "active_canonical_ids": sorted(active_state_ids(state)),
        "hard_constraint_count": len(state.hard_constraints),
        "soft_preference_count": len(state.soft_constraints),
        "facet_count": sum(
            1
            for facet in FACET_ORDER
            if state.subjective_needs.get_facet(facet) is not None  # type: ignore[arg-type]
        ),
        "tradeoff_count": len(state.tradeoffs),
        "rejected_item_count": len(state.rejected_items),
        "purchased_item_count": len(state.purchased_items),
    }


# --------------------------------------------------------------------------- #
# 턴 단위 상태 확보
# --------------------------------------------------------------------------- #


def designed_scenario(scenario: TabletHoldoutScenario) -> dict[str, Any]:
    """설계 쪽 대조 정보. 전부 동결 데이터셋에 이미 있던 필드다."""
    return {
        "title": scenario.title,
        "tags": list(scenario.tags),
        "turn_count": len(scenario.turns),
        "final_gold_state_ids": sorted(scenario.final_gold_state_ids),
        "final_expected_hard_filters": scenario.final_expected_hard_filters.model_dump(
            mode="json", exclude_none=True
        ),
    }


def designed_turn(gold_turn: TabletHoldoutTurn) -> dict[str, Any]:
    return {
        "utterance": gold_turn.utterance,
        "gold_domain_route": gold_turn.gold_domain_route,
        "gold_intents": list(gold_turn.gold_intents),
        "gold_candidate_ids": sorted(gold_turn.gold_candidate_ids),
        "gold_state_diff": list(gold_turn.gold_state_diff),
        "gold_policy_lane": gold_turn.gold_policy_lane,
        "expected_hard_filters_after_turn": (
            gold_turn.expected_hard_filters_after_turn.model_dump(
                mode="json", exclude_none=True
            )
        ),
    }


def gold_dst_by_turn(scenario: TabletHoldoutScenario) -> dict[int, DialogueState]:
    """1턴부터 각 턴까지 누적한 Gold DST 스냅숏을 턴 번호로 돌려준다.

    마지막 스냅숏은 동결된 `final_gold_state_ids`와 `final_expected_hard_filters`를
    검증하는 기존 재구성 함수의 결과와 일치해야 한다. 어긋나면 즉시 실패한다.
    """
    state = create_tablet_environment_state()
    snapshots: dict[int, DialogueState] = {}
    for gold_turn in scenario.turns:
        state = _apply_gold_turn_to_oracle_state(state, gold_turn)
        snapshots[gold_turn.turn] = state.model_copy(deep=True)

    frozen_final = reconstruct_final_gold_state(scenario)
    last_turn = scenario.turns[-1].turn
    if active_state_ids(snapshots[last_turn]) != active_state_ids(frozen_final):
        raise RuntimeError(
            f"{scenario.id}: accumulated Gold DST differs from the frozen final gold state"
        )
    return snapshots


def full_dst_by_turn(
    official_raw: dict[str, Any],
    scenario: TabletHoldoutScenario,
    *,
    condition: str = "full_dst",
) -> tuple[dict[int, DialogueState], dict[str, Any]]:
    """official raw run에서 턴별 DialogueState를 꺼낸다.

    완주하지 않은 턴은 상태가 없다. 그 사실을 숨기지 않고 사유로 남기고
    해당 턴의 LLM 호출을 건너뛴다.
    """
    raw_condition = RAW_CONDITION_OF[condition]
    scenario_payload = next(
        item
        for item in official_raw["conditions"][raw_condition]["scenarios"]
        if item["scenario_id"] == scenario.id
    )
    context = {
        "run_status": scenario_payload["status"],
        "completed_turn_count": scenario_payload["completed_turn_count"],
        "expected_turn_count": scenario_payload["expected_turn_count"],
        "error_turn": scenario_payload.get("error_turn"),
        "error_classification": scenario_payload.get("error_classification"),
    }
    snapshots: dict[int, DialogueState] = {}
    parse_errors: dict[int, str] = {}
    for index, turn_payload in enumerate(scenario_payload.get("turns") or [], start=1):
        turn_id = turn_payload.get("turn_id") or f"turn-{index}"
        try:
            number = int(str(turn_id).removeprefix("turn-"))
        except ValueError:
            number = index
        try:
            snapshots[number] = DialogueState.model_validate(
                turn_payload["dialogue_state"]
            )
        except ValidationError as error:
            parse_errors[number] = str(error)[:500]
    context["parse_errors"] = parse_errors
    return snapshots, context


# --------------------------------------------------------------------------- #
# LLM 호출
# --------------------------------------------------------------------------- #


async def probe_state(
    client: LLMClient,
    *,
    rendered_state: dict[str, Any],
    scenario_id: str,
    turn: int,
    condition: str,
) -> IntentProbeOutput:
    payload = {
        "environment": {
            "domain": "tablet_shopping",
            "snapshot_is_the_only_evidence": True,
            "original_utterances_withheld": True,
            "conversation_may_continue": True,
        },
        "dialogue_state_snapshot": rendered_state,
    }
    return await client.generate_structured(
        messages=[
            system(INTENT_PROBE_SYSTEM_PROMPT),
            user(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        ],
        response_model=IntentProbeOutput,
        temperature=0.0,
        node="latent-hypothesis-probe",
        prompt_version=INTENT_PROBE_PROMPT_VERSION,
        conversation_id=f"{scenario_id}:{condition}",
        turn=turn,
    )


# --------------------------------------------------------------------------- #
# 실행
# --------------------------------------------------------------------------- #


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


GUARDRAIL = {
    "analysis_label": PROBE_LABEL,
    "comparison_unit": "turn",
    "position": "exploratory_qualitative_observation",
    "official_run_reexecuted": False,
    "frozen_prompt_or_schema_modified": False,
    "new_gold_labels_added": False,
    "designed_situation_reference": "frozen scenario title, tags, and per-turn utterances only",
    "accuracy_or_ndcg_computed": False,
    "resolves_hidden_intent_analysis_not_evaluable_metrics": False,
    "hypotheses_enter_pipeline_state_or_ranking": False,
    "llm_confidence_requested": False,
    "sampling": "one sample per turn per condition at temperature 0.0",
    "known_asymmetry": (
        "Gold snapshot value_text is derived mechanically from canonical IDs and is uniformly "
        "explicit/confirmed, while Full snapshot value_text is LLM-written from real utterances. "
        "Both are rendered by one function, but the surface text origin differs."
    ),
    "claim_allowed": (
        "turn-by-turn qualitative comparison of what each dialogue state lets a model infer, "
        "including where the two states first diverge"
    ),
    "claim_not_allowed": (
        "any measured hidden-intent accuracy, ranking quality, or human relevance claim"
    ),
}


async def run_intent_probe(
    *,
    client: LLMClient,
    gold: TabletHoldoutDataset,
    official_raw: dict[str, Any],
    conditions: tuple[str, ...] = CONDITION_ORDER,
    scenario_ids: list[str] | None = None,
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    """턴마다 조건별로 한 번씩 상태 추론을 돌린다."""

    selected = [
        scenario
        for scenario in gold.scenarios
        if scenario_ids is None or scenario.id in scenario_ids
    ]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "probe_label": PROBE_LABEL,
        "status": "running",
        "started_at": _utc_now(),
        "completed_at": None,
        "guardrail": GUARDRAIL,
        "probe_contract": {
            "comparison_unit": "turn",
            "prompt_version": INTENT_PROBE_PROMPT_VERSION,
            "response_schema": IntentProbeOutput.__name__,
            "response_schema_version": IntentProbeOutput.schema_version,
            "conditions": list(conditions),
            "renderer": "app.evaluation.tablet_domain_intent_probe.render_state_for_probe",
            "input_withholds": [
                "original utterances",
                "scenario title and tags",
                "turn index",
                "product catalog",
                "recommendation output",
            ],
        },
        "episodes": [],
        "summary": None,
    }

    for scenario in selected:
        gold_states = gold_dst_by_turn(scenario)
        condition_states: dict[str, dict[int, DialogueState]] = {}
        condition_context: dict[str, dict[str, Any]] = {}
        for condition in conditions:
            if condition == "gold_dst":
                continue
            states, context = full_dst_by_turn(
                official_raw, scenario, condition=condition
            )
            condition_states[condition] = states
            condition_context[condition] = context

        episode: dict[str, Any] = {
            "scenario_id": scenario.id,
            "designed": designed_scenario(scenario),
            "condition_run_context": condition_context,
            "turns": [],
        }
        # 체크포인트가 진행 중인 에피소드까지 포함하도록 먼저 붙인다.
        report["episodes"].append(episode)

        for gold_turn in scenario.turns:
            turn_record: dict[str, Any] = {
                "turn": gold_turn.turn,
                "designed": designed_turn(gold_turn),
                "conditions": {},
            }
            for condition in conditions:
                if condition == "gold_dst":
                    state = gold_states[gold_turn.turn]
                    entry: dict[str, Any] = {"status": "state_available"}
                else:
                    state = condition_states[condition].get(gold_turn.turn)
                    context = condition_context[condition]
                    if state is None:
                        reason = (
                            "state_parse_error"
                            if gold_turn.turn in context["parse_errors"]
                            else "turn_not_completed_in_official_run"
                        )
                        turn_record["conditions"][condition] = {
                            "status": reason,
                            "state_shape": None,
                            "rendered_state": None,
                            "probe": None,
                        }
                        continue
                    entry = {"status": "state_available"}

                rendered = render_state_for_probe(state)
                entry["state_shape"] = state_shape(state)
                entry["rendered_state"] = rendered
                try:
                    probe = await probe_state(
                        client,
                        rendered_state=rendered,
                        scenario_id=scenario.id,
                        turn=gold_turn.turn,
                        condition=condition,
                    )
                except StructuredOutputError as error:
                    entry["probe"] = None
                    entry["probe_error"] = str(error)[:500]
                else:
                    entry["probe"] = probe.model_dump(mode="json")
                turn_record["conditions"][condition] = entry

            episode["turns"].append(turn_record)
            if checkpoint_path is not None:
                from app.llm import write_report

                write_report(checkpoint_path, report)
            print(
                f"probe: {scenario.id} turn-{gold_turn.turn} "
                + " ".join(
                    f"{condition}={'ok' if turn_record['conditions'].get(condition, {}).get('probe') else 'skip'}"
                    for condition in conditions
                ),
                flush=True,
            )

    report["summary"] = summarize_probe(report)
    report["status"] = "completed"
    report["completed_at"] = _utc_now()
    return report


# --------------------------------------------------------------------------- #
# 턴 단위 상태 차이
# --------------------------------------------------------------------------- #


def turn_state_divergence(
    turn_record: dict[str, Any], condition: str = "full_dst"
) -> dict[str, Any]:
    """해당 턴의 Gold DST와 비교 조건 DST의 canonical ID 차이."""
    gold_entry = turn_record["conditions"].get("gold_dst", {})
    other_entry = turn_record["conditions"].get(condition, {})
    gold_shape = gold_entry.get("state_shape")
    other_shape = other_entry.get("state_shape")
    gold_ids = set(gold_shape["active_canonical_ids"]) if gold_shape else set()
    if other_shape is None:
        return {
            "turn": turn_record["turn"],
            "gold_ids": sorted(gold_ids),
            "other_ids": None,
            "missing": sorted(gold_ids),
            "extra": [],
            "divergence_size": len(gold_ids),
            "comparable": False,
            "status": other_entry.get("status", "unknown"),
        }
    other_ids = set(other_shape["active_canonical_ids"])
    missing = sorted(gold_ids - other_ids)
    extra = sorted(other_ids - gold_ids)
    return {
        "turn": turn_record["turn"],
        "gold_ids": sorted(gold_ids),
        "other_ids": sorted(other_ids),
        "missing": missing,
        "extra": extra,
        "divergence_size": len(missing) + len(extra),
        "comparable": True,
        "status": other_entry.get("status", "state_available"),
    }


def episode_divergence_trajectory(
    episode: dict[str, Any], condition: str = "full_dst"
) -> dict[str, Any]:
    """에피소드 안에서 상태 차이가 어느 턴에 처음 생기고 어떻게 이어지는가."""
    rows = [turn_state_divergence(turn, condition) for turn in episode["turns"]]
    comparable = [row for row in rows if row["comparable"]]
    diverged = [row for row in comparable if row["divergence_size"] > 0]
    first = diverged[0]["turn"] if diverged else None
    last_comparable = comparable[-1] if comparable else None
    recovered = bool(
        first is not None
        and last_comparable is not None
        and last_comparable["divergence_size"] == 0
    )
    sizes = [row["divergence_size"] for row in comparable]
    return {
        "scenario_id": episode["scenario_id"],
        "title": episode["designed"]["title"],
        "turn_count": len(rows),
        "comparable_turns": len(comparable),
        "first_divergence_turn": first,
        "diverged_turn_count": len(diverged),
        "divergence_size_by_turn": sizes,
        "max_divergence_size": max(sizes, default=0),
        "final_divergence_size": (
            last_comparable["divergence_size"] if last_comparable else None
        ),
        "recovered_by_last_comparable_turn": recovered,
        # 차이가 한 번도 줄지 않았는가. 상태 오류가 누적되는지 회복되는지를 본다.
        "divergence_never_shrinks": all(
            later >= earlier for earlier, later in zip(sizes, sizes[1:])
        ),
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# 서술 통계
# --------------------------------------------------------------------------- #

#: 이 길이 이상 겹치면 발화 문면이 상태에 그대로 실려 온 것으로 계측한다.
VERBATIM_MIN_CHARS = 12


def value_text_verbatim_overlap(
    rendered_state: dict[str, Any] | None, utterances: list[str]
) -> list[str]:
    """상태의 value_text 중 설계 발화의 부분 문자열인 것을 모은다.

    Gold 스냅숏의 value_text는 canonical ID에서 기계적으로 생성되므로 원문과
    겹치지 않는다. Full 스냅숏은 LLM이 발화를 읽고 쓴 문면이라 겹칠 수 있다.
    두 조건의 입력 surface가 실제로 얼마나 다른지를 추측 대신 수치로 남긴다.
    """
    if not rendered_state:
        return []
    haystack = " ".join(utterance.casefold() for utterance in utterances)
    texts: list[str] = []
    for group in ("hard_constraints", "soft_preferences", "facets", "tradeoffs"):
        texts.extend(item["value_text"] for item in rendered_state.get(group, []))
    texts.extend(
        item["reason_text"] for item in rendered_state.get("rejected_items", [])
    )
    return [
        text
        for text in texts
        if len(text) >= VERBATIM_MIN_CHARS and text.casefold() in haystack
    ]


def _iter_turns(report: dict[str, Any]):
    for episode in report["episodes"]:
        utterances = [turn["designed"]["utterance"] for turn in episode["turns"]]
        for turn_record in episode["turns"]:
            yield episode, utterances, turn_record


def summarize_probe(report: dict[str, Any]) -> dict[str, Any]:
    """서술 통계만 만든다. 정확도나 순위 품질은 계산하지 않는다."""
    conditions = report["probe_contract"]["conditions"]
    turn_rows = list(_iter_turns(report))
    per_condition: dict[str, Any] = {}

    for condition in conditions:
        entries = []
        overlap_counts = 0
        overlap_turns = 0
        for _episode, utterances, turn_record in turn_rows:
            entry = turn_record["conditions"].get(condition, {})
            entries.append(entry)
            overlap = value_text_verbatim_overlap(
                entry.get("rendered_state"), utterances
            )
            overlap_counts += len(overlap)
            overlap_turns += int(bool(overlap))
        probed = [entry for entry in entries if entry.get("probe")]
        shapes = [entry["state_shape"] for entry in entries if entry.get("state_shape")]
        hypothesis_counts = [len(entry["probe"]["hypotheses"]) for entry in probed]
        per_condition[condition] = {
            "turns": len(entries),
            "state_available": len(shapes),
            "probe_succeeded": len(probed),
            "turn_not_completed": sum(
                1
                for entry in entries
                if entry.get("status") == "turn_not_completed_in_official_run"
            ),
            "probe_failed": sum(
                1 for entry in entries if entry.get("probe_error") is not None
            ),
            "mean_active_canonical_ids": (
                round(
                    sum(len(shape["active_canonical_ids"]) for shape in shapes)
                    / len(shapes),
                    2,
                )
                if shapes
                else None
            ),
            "mean_hypotheses_per_turn": (
                round(sum(hypothesis_counts) / len(hypothesis_counts), 2)
                if hypothesis_counts
                else None
            ),
            "hypothesis_type_counts": _count_field(probed, "hypothesis_type"),
            "suggested_scope_counts": _count_field(probed, "suggested_scope"),
            "abstain_from_ranking_true": sum(
                1
                for entry in probed
                for hypothesis in entry["probe"]["hypotheses"]
                if hypothesis["abstain_from_ranking"]
            ),
            "unsupported_evidence_id_count": sum(
                _unsupported_evidence_ids(entry) for entry in probed
            ),
            "empty_evidence_hypothesis_count": sum(
                1
                for entry in probed
                for hypothesis in entry["probe"]["hypotheses"]
                if not hypothesis["evidence_ids"]
            ),
            "verbatim_utterance_value_text_count": overlap_counts,
            "turns_with_verbatim_utterance_value_text": overlap_turns,
        }

    comparison_condition = next(
        (condition for condition in conditions if condition != "gold_dst"), None
    )
    trajectories = (
        [
            episode_divergence_trajectory(episode, comparison_condition)
            for episode in report["episodes"]
        ]
        if comparison_condition
        else []
    )
    onset = [
        trajectory["first_divergence_turn"]
        for trajectory in trajectories
        if trajectory["first_divergence_turn"] is not None
    ]
    comparable_turn_rows = [
        row
        for trajectory in trajectories
        for row in trajectory["rows"]
        if row["comparable"]
    ]
    monotonic_candidates = [
        trajectory
        for trajectory in trajectories
        if trajectory["first_divergence_turn"] is not None
        and trajectory["comparable_turns"] > 1
    ]
    divergence = {
        "comparison_condition": comparison_condition,
        "episodes": len(trajectories),
        "comparable_turns": len(comparable_turn_rows),
        "identical_state_turns": sum(
            1 for row in comparable_turn_rows if row["divergence_size"] == 0
        ),
        "diverged_state_turns": sum(
            1 for row in comparable_turn_rows if row["divergence_size"] > 0
        ),
        "episodes_with_any_divergence": len(onset),
        "first_divergence_turn_counts": {
            str(turn): onset.count(turn) for turn in sorted(set(onset))
        },
        "episodes_recovered_after_divergence": sum(
            1
            for trajectory in trajectories
            if trajectory["recovered_by_last_comparable_turn"]
        ),
        # 단조성은 실제로 갈린 에피소드에서만 의미가 있다. 한 번도 갈리지 않은
        # 에피소드를 분모에 넣으면 "줄지 않았다"가 과장된다.
        "episodes_with_divergence_and_multiple_comparable_turns": len(
            monotonic_candidates
        ),
        "episodes_where_divergence_never_shrinks": sum(
            1
            for trajectory in monotonic_candidates
            if trajectory["divergence_never_shrinks"]
        ),
        "episodes_where_divergence_shrinks_at_least_once": [
            trajectory["scenario_id"]
            for trajectory in monotonic_candidates
            if not trajectory["divergence_never_shrinks"]
        ],
        "mean_divergence_size_over_comparable_turns": (
            round(
                sum(row["divergence_size"] for row in comparable_turn_rows)
                / len(comparable_turn_rows),
                2,
            )
            if comparable_turn_rows
            else None
        ),
    }

    return {
        "episode_count": len(report["episodes"]),
        "turn_count": len(turn_rows),
        "per_condition": per_condition,
        "state_divergence": divergence,
        "metric_claim_allowed": False,
        "note": (
            "Counts describe what each condition produced per turn. They are not accuracy "
            "scores against any designed-situation ground truth, which the frozen holdout "
            "does not contain."
        ),
    }


def _count_field(entries: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in entries:
        for hypothesis in entry["probe"]["hypotheses"]:
            counts[hypothesis[field]] = counts.get(hypothesis[field], 0) + 1
    return dict(sorted(counts.items()))


def _unsupported_evidence_ids(entry: dict[str, Any]) -> int:
    """스냅숏에 없는 ID를 근거로 든 횟수. 계측이며 gold 채점이 아니다."""
    rendered = entry.get("rendered_state") or {}
    available = {
        *(item["canonical_id"] for item in rendered.get("hard_constraints", [])),
        *(item["canonical_id"] for item in rendered.get("soft_preferences", [])),
        *(item["canonical_id"] for item in rendered.get("facets", [])),
        *(item["canonical_id"] for item in rendered.get("tradeoffs", [])),
        *(
            item["reason_canonical_id"]
            for item in rendered.get("rejected_items", [])
        ),
    }
    if rendered.get("category"):
        available.add(rendered["category"])
    return sum(
        1
        for hypothesis in entry["probe"]["hypotheses"]
        for evidence_id in hypothesis["evidence_ids"]
        if evidence_id not in available
    )


# --------------------------------------------------------------------------- #
# 읽기용 Markdown
# --------------------------------------------------------------------------- #


def episode_heading(scenario_id: str, title: str) -> str:
    return f"{scenario_id} — {title}"


def heading_anchor(heading: str) -> str:
    """Markdown 뷰어가 heading에서 만드는 슬러그를 그대로 재현한다.

    소문자화 후 영숫자·하이픈·공백·밑줄만 남기고 공백을 하이픈으로 바꾼다.
    em dash가 제거되면서 앞뒤 공백이 두 개의 하이픈이 되는 것까지 맞춘다.
    """
    kept = "".join(
        char
        for char in heading.casefold()
        if char.isalnum() or char in {"-", " ", "_"}
    )
    return kept.replace(" ", "-")


def _anchor(scenario_id: str, title: str) -> str:
    return heading_anchor(episode_heading(scenario_id, title))


def _ids(values: list[str] | None) -> str:
    if values is None:
        return "없음"
    return ", ".join(f"`{item}`" for item in values) or "-"


def _render_stats(report: dict[str, Any]) -> list[str]:
    summary = report["summary"]
    conditions = report["probe_contract"]["conditions"]
    lines = [
        "## 조건별 턴 단위 서술 통계",
        "",
        f"비교 단위는 턴이다. 전체 {summary['turn_count']}턴, "
        f"{summary['episode_count']}에피소드.",
        "",
        "| 조건 | 상태 확보 | 추론 성공 | 평균 상태 ID | 턴당 평균 가설 "
        "| 스냅숏에 없는 근거 ID | 근거 ID 없는 가설 | 발화 문면 그대로인 value_text |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for condition in conditions:
        stat = summary["per_condition"][condition]
        lines.append(
            f"| {CONDITION_HEADINGS.get(condition, condition)} "
            f"| {stat['state_available']}/{stat['turns']} "
            f"| {stat['probe_succeeded']}/{stat['turns']} "
            f"| {stat['mean_active_canonical_ids'] if stat['mean_active_canonical_ids'] is not None else 'N/A'} "
            f"| {stat['mean_hypotheses_per_turn'] if stat['mean_hypotheses_per_turn'] is not None else 'N/A'} "
            f"| {stat['unsupported_evidence_id_count']} "
            f"| {stat['empty_evidence_hypothesis_count']} "
            f"| {stat['verbatim_utterance_value_text_count']} "
            f"({stat['turns_with_verbatim_utterance_value_text']} 턴) |"
        )

    divergence = summary["state_divergence"]
    lines.extend(
        [
            "",
            "## 턴 단위 상태 차이 요약",
            "",
            f"- 비교 조건: `{divergence['comparison_condition']}`",
            f"- 양쪽 상태가 모두 있는 턴: {divergence['comparable_turns']}",
            f"- 상태가 동일한 턴: {divergence['identical_state_turns']}",
            f"- 상태가 갈린 턴: {divergence['diverged_state_turns']}",
            f"- 갈린 턴의 평균 차이 크기: "
            f"{divergence['mean_divergence_size_over_comparable_turns']}",
            f"- 한 번이라도 갈린 에피소드: {divergence['episodes_with_any_divergence']}"
            f"/{divergence['episodes']}",
            f"- 갈린 뒤 마지막 비교 가능 턴에서 다시 일치한 에피소드: "
            f"{divergence['episodes_recovered_after_divergence']}",
            f"- 갈린 에피소드 중 차이가 한 번도 줄지 않은 것: "
            f"{divergence['episodes_where_divergence_never_shrinks']}"
            f"/{divergence['episodes_with_divergence_and_multiple_comparable_turns']}"
            + (
                " (줄어든 에피소드: "
                + ", ".join(
                    f"`{item}`"
                    for item in divergence["episodes_where_divergence_shrinks_at_least_once"]
                )
                + ")"
                if divergence["episodes_where_divergence_shrinks_at_least_once"]
                else ""
            ),
            "",
            "처음 갈라진 턴의 분포:",
            "",
            "| 처음 갈라진 턴 | 에피소드 수 |",
            "| ---: | ---: |",
        ]
    )
    for turn, count in divergence["first_divergence_turn_counts"].items():
        lines.append(f"| {turn} | {count} |")
    lines.append("")
    return lines


def _render_trajectory_overview(report: dict[str, Any]) -> list[str]:
    condition = report["summary"]["state_divergence"]["comparison_condition"]
    lines = [
        "## 에피소드별 갈라짐 경과",
        "",
        "`처음 갈라진 턴`은 Gold DST와 비교 조건 DST의 canonical ID 집합이 처음 달라진 턴이다.",
        "양쪽 상태가 모두 존재하는 턴만 비교 대상이다.",
        "",
        "`턴별 차이 크기`는 1턴부터 비교 가능한 턴까지의 차이 크기를 순서대로 나열한 것이다.",
        "",
        "| 에피소드 | 설계 상황 | 턴 | 비교 가능 턴 | 처음 갈라진 턴 | 갈린 턴 수 | 턴별 차이 크기 | 마지막 차이 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for episode in report["episodes"]:
        trajectory = episode_divergence_trajectory(episode, condition)
        link = f"[{trajectory['scenario_id']}](#{_anchor(trajectory['scenario_id'], trajectory['title'])})"
        sizes = trajectory["divergence_size_by_turn"]
        lines.append(
            f"| {link} | {trajectory['title']} | {trajectory['turn_count']} "
            f"| {trajectory['comparable_turns']} "
            f"| {trajectory['first_divergence_turn'] if trajectory['first_divergence_turn'] is not None else '-'} "
            f"| {trajectory['diverged_turn_count']} "
            f"| {' → '.join(str(size) for size in sizes) or '-'} "
            f"| {trajectory['final_divergence_size'] if trajectory['final_divergence_size'] is not None else '-'} |"
        )
    lines.append("")
    return lines


def _render_probe_block(entry: dict[str, Any], label: str) -> list[str]:
    if not entry.get("probe"):
        reason = entry.get("probe_error") or entry.get("status") or "unavailable"
        return [f"- **{label}**: 추론 없음 (`{reason}`)"]
    probe = entry["probe"]
    lines = [f"- **{label}**: {probe['situation_summary']}"]
    for index, hypothesis in enumerate(probe["hypotheses"], start=1):
        lines.append(
            f"  {index}. [{hypothesis['hypothesis_type']} · "
            f"{hypothesis['suggested_scope']} · "
            f"abstain={str(hypothesis['abstain_from_ranking']).lower()}] "
            f"{hypothesis['hypothesis_text']} "
            f"— 근거 {_ids(hypothesis['evidence_ids'])}"
        )
    if probe["missing_information"]:
        lines.append(
            "  - 다음에 물을 것: " + "; ".join(probe["missing_information"])
        )
    return lines


def render_probe_markdown(report: dict[str, Any]) -> str:
    conditions = report["probe_contract"]["conditions"]
    comparison = report["summary"]["state_divergence"]["comparison_condition"]
    lines = [
        "# Tablet-domain hidden-intent probe, turn level (exploratory)",
        "",
        "동결된 20개 holdout 에피소드의 **매 턴** 누적 대화 상태만 보고 GPT-4o-mini가",
        "상황과 숨은 의도 가설을 추론한 결과다. 발화 원문, 시나리오 제목, 태그, 턴 번호는",
        "모델에게 주지 않았다.",
        "",
        "## 이 문서를 읽는 순서",
        "",
        "1. 조건별 턴 단위 서술 통계 — 두 조건이 턴마다 무엇을 만들어냈는지",
        "2. 턴 단위 상태 차이 요약 — 몇 턴에서 갈렸고 처음 갈라진 턴은 언제인지",
        "3. 에피소드별 갈라짐 경과 — 에피소드마다 갈라짐이 어떻게 진행되는지",
        "4. 에피소드·턴별 상세 비교 — 발화, 양쪽 상태 ID, 양쪽 추론",
        "",
        "## 성격과 한계",
        "",
        "- 정성 탐색 관찰. 정확도·NDCG·human relevance 주장을 하지 않는다.",
        "- official run 재실행 없음, 동결 prompt/schema 수정 없음, 새 gold label 없음.",
        "- 설계 대조 정보는 이미 동결돼 있던 title/tags/턴별 발화뿐이다.",
        "- `hidden_intent_analysis`의 `not_evaluable_with_current_holdout` 네 metric을 대체하지 않는다.",
        "- 생성된 가설은 분석 artifact이며 pipeline 상태나 랭킹에 들어가지 않는다.",
        "- 조건당 턴당 temperature 0.0 단일 표본이다. 반복 분산은 측정하지 않았다.",
        "- 마지막 턴 비교는 에피소드 단위 비교와 같은 스냅숏을 본다. 턴 단위가 그것을 포함한다.",
        "- 알려진 비대칭: Gold 스냅숏의 `value_text`는 canonical ID에서 기계적으로 만들어지고",
        "  provenance가 균일하게 explicit/confirmed다. Full 스냅숏은 LLM이 실제 발화에서 쓴 문면이라",
        "  발화 원문 조각이 그대로 실려 올 수 있다. 아래 표의 마지막 열이 그 실측치다.",
        "  따라서 두 조건의 차이는 상태 내용 차이와 문면 출처 차이가 섞인 결과다.",
        "",
    ]
    lines.extend(_render_stats(report))
    lines.extend(_render_trajectory_overview(report))
    lines.extend(
        [
            "## 에피소드·턴별 상세 비교",
            "",
            "턴마다 설계 발화, Gold DST 상태 ID, 비교 조건 상태 ID, 그리고 두 조건의 추론을 놓았다.",
            "모델은 발화와 제목을 보지 못한 상태로 답했다.",
            "",
        ]
    )

    for episode in report["episodes"]:
        designed = episode["designed"]
        trajectory = episode_divergence_trajectory(episode, comparison)
        lines.extend(
            [
                f"### {episode_heading(episode['scenario_id'], designed['title'])}",
                "",
                f"설계 태그: `{', '.join(designed['tags'])}` · 턴 {designed['turn_count']}개 · "
                f"처음 갈라진 턴 "
                f"{trajectory['first_divergence_turn'] if trajectory['first_divergence_turn'] is not None else '없음'}",
                "",
            ]
        )
        for context_condition, context in (episode.get("condition_run_context") or {}).items():
            lines.append(
                f"official run(`{context_condition}`): {context['run_status']} · "
                f"{context['completed_turn_count']}/{context['expected_turn_count']} 턴 완주"
                + (
                    f" · 실패 턴 {context['error_turn']} ({context['error_classification']})"
                    if context.get("error_turn")
                    else ""
                )
            )
        lines.append("")

        rows = {row["turn"]: row for row in trajectory["rows"]}
        for turn_record in episode["turns"]:
            number = turn_record["turn"]
            turn_designed = turn_record["designed"]
            row = rows[number]
            lines.extend(
                [
                    f"#### {episode['scenario_id']} · turn {number}",
                    "",
                    f"> {turn_designed['utterance']}",
                    "",
                    f"- Gold DST 상태 ID: {_ids(row['gold_ids'])}",
                    f"- {CONDITION_LABELS.get(comparison, comparison)} 상태 ID: "
                    f"{_ids(row['other_ids'])}",
                    f"- 상태 차이: 놓침 {_ids(row['missing'])} / 추가 {_ids(row['extra'])}"
                    + ("" if row["comparable"] else f" (`{row['status']}`)"),
                    f"- Gold 기대 hard filter: "
                    + (
                        "`"
                        + ", ".join(
                            f"{key}={value}"
                            for key, value in sorted(
                                turn_designed["expected_hard_filters_after_turn"].items()
                            )
                        )
                        + "`"
                        if turn_designed["expected_hard_filters_after_turn"]
                        else "없음"
                    ),
                    f"- Gold State Diff: {_ids(turn_designed['gold_state_diff'])} · "
                    f"lane `{turn_designed['gold_policy_lane']}`",
                    "",
                ]
            )
            for condition in conditions:
                entry = turn_record["conditions"].get(condition, {})
                lines.extend(
                    _render_probe_block(
                        entry, CONDITION_HEADINGS.get(condition, condition)
                    )
                )
            lines.append("")
        lines.append("---")
        lines.append("")

    lines.extend(_render_footer(report))
    return "\n".join(lines)


def notable_divergence_turns(
    report: dict[str, Any], *, limit: int = 5
) -> list[dict[str, Any]]:
    """차이가 직전 비교 턴보다 가장 많이 커진 턴을 결정론적으로 고른다.

    사람이 고른 사례가 아니라 궤적에서 계산한 것이므로 재생성해도 같은 목록이 나온다.
    """
    condition = report["summary"]["state_divergence"]["comparison_condition"]
    candidates: list[dict[str, Any]] = []
    for episode in report["episodes"]:
        trajectory = episode_divergence_trajectory(episode, condition)
        comparable = [row for row in trajectory["rows"] if row["comparable"]]
        turn_by_number = {turn["turn"]: turn for turn in episode["turns"]}
        previous = 0
        for row in comparable:
            delta = row["divergence_size"] - previous
            previous = row["divergence_size"]
            if delta <= 0:
                continue
            turn_record = turn_by_number[row["turn"]]
            candidates.append(
                {
                    "scenario_id": episode["scenario_id"],
                    "title": episode["designed"]["title"],
                    "turn": row["turn"],
                    "utterance": turn_record["designed"]["utterance"],
                    "divergence_increase": delta,
                    "divergence_size": row["divergence_size"],
                    "missing": row["missing"],
                    "extra": row["extra"],
                    "gold_state_diff": turn_record["designed"]["gold_state_diff"],
                    "summaries": {
                        name: (
                            (entry.get("probe") or {}).get("situation_summary")
                            if entry.get("probe")
                            else None
                        )
                        for name, entry in turn_record["conditions"].items()
                    },
                }
            )
    candidates.sort(
        key=lambda item: (
            -item["divergence_increase"],
            -item["divergence_size"],
            item["scenario_id"],
            item["turn"],
        )
    )
    return candidates[:limit]


def compact_probe_result(report: dict[str, Any]) -> dict[str, Any]:
    """추적 가능한 압축 결과. 렌더된 상태 원문을 빼고 판단에 필요한 것만 남긴다."""
    condition = report["summary"]["state_divergence"]["comparison_condition"]
    episodes = []
    for episode in report["episodes"]:
        trajectory = episode_divergence_trajectory(episode, condition)
        rows = {row["turn"]: row for row in trajectory["rows"]}
        turns = []
        for turn_record in episode["turns"]:
            row = rows[turn_record["turn"]]
            turns.append(
                {
                    "turn": turn_record["turn"],
                    "utterance": turn_record["designed"]["utterance"],
                    "gold_state_diff": turn_record["designed"]["gold_state_diff"],
                    "gold_policy_lane": turn_record["designed"]["gold_policy_lane"],
                    "expected_hard_filters_after_turn": turn_record["designed"][
                        "expected_hard_filters_after_turn"
                    ],
                    "divergence": {
                        key: row[key]
                        for key in (
                            "gold_ids",
                            "other_ids",
                            "missing",
                            "extra",
                            "divergence_size",
                            "comparable",
                            "status",
                        )
                    },
                    "conditions": {
                        name: {
                            "status": entry.get("status"),
                            "active_canonical_ids": (
                                (entry.get("state_shape") or {}).get(
                                    "active_canonical_ids"
                                )
                            ),
                            "probe": entry.get("probe"),
                            "probe_error": entry.get("probe_error"),
                        }
                        for name, entry in turn_record["conditions"].items()
                    },
                }
            )
        episodes.append(
            {
                "scenario_id": episode["scenario_id"],
                "designed": episode["designed"],
                "condition_run_context": episode.get("condition_run_context"),
                "divergence_trajectory": {
                    key: trajectory[key]
                    for key in (
                        "turn_count",
                        "comparable_turns",
                        "first_divergence_turn",
                        "diverged_turn_count",
                        "divergence_size_by_turn",
                        "max_divergence_size",
                        "final_divergence_size",
                        "recovered_by_last_comparable_turn",
                        "divergence_never_shrinks",
                    )
                },
                "turns": turns,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "probe_label": PROBE_LABEL,
        "status": report["status"],
        "started_at": report.get("started_at"),
        "completed_at": report.get("completed_at"),
        "guardrail": report["guardrail"],
        "probe_contract": report["probe_contract"],
        "planned_calls": report.get("planned_calls"),
        "summary": report["summary"],
        "notable_divergence_turns": notable_divergence_turns(report),
        "episodes": episodes,
    }


def render_probe_report_markdown(compact: dict[str, Any]) -> str:
    """추적 가능한 압축 레포트. 전체 턴 상세는 git 제외 reports/ 문서에 있다."""
    summary = compact["summary"]
    divergence = summary["state_divergence"]
    conditions = compact["probe_contract"]["conditions"]
    lines = [
        "# Turn-level hidden-intent probe (exploratory)",
        "",
        "상태: **정성 탐색 관찰 완료**",
        "",
        "동결된 20-scenario / 81-turn holdout의 **매 턴** 누적 대화 상태를 두 갈래로 만들어,",
        "발화 원문 없이 상태만 보고 GPT-4o-mini가 상황과 숨은 의도 가설을 추론하게 했다.",
        "",
        "- `gold_dst`: 사전 동결된 turn 주석을 1턴부터 누적한 Gold DST",
        "- `full_dst`: official raw run의 Full 조건이 해당 턴을 처리한 뒤의 DialogueState",
        "",
        "> 이 문서는 숨은 의도 정확도가 아니다. 설계 상황을 서술한 gold label이 동결 holdout에",
        "> 없으므로 점수를 만들지 않았다. official run 재실행, 동결 prompt/schema 수정,",
        "> 새 gold label 추가는 없다. 생성된 가설은 분석 artifact이며 랭킹에 들어가지 않는다.",
        "> `hidden_intent_analysis`의 `not_evaluable_with_current_holdout` 네 metric을 해소하지 않는다.",
        "",
        "## 실행 규모",
        "",
        f"- 에피소드 {summary['episode_count']}개 / 턴 {summary['turn_count']}개",
        f"- LLM 호출 {(compact.get('planned_calls') or {}).get('total')}회, "
        f"조건당 턴당 temperature 0.0 단일 표본",
        "",
        "| 조건 | 상태 확보 | 추론 성공 | 평균 상태 ID | 턴당 평균 가설 "
        "| 스냅숏에 없는 근거 ID | 근거 ID 없는 가설 | 발화 문면 그대로인 value_text |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for condition in conditions:
        stat = summary["per_condition"][condition]
        lines.append(
            f"| `{condition}` "
            f"| {stat['state_available']}/{stat['turns']} "
            f"| {stat['probe_succeeded']}/{stat['turns']} "
            f"| {stat['mean_active_canonical_ids'] if stat['mean_active_canonical_ids'] is not None else 'N/A'} "
            f"| {stat['mean_hypotheses_per_turn'] if stat['mean_hypotheses_per_turn'] is not None else 'N/A'} "
            f"| {stat['unsupported_evidence_id_count']} "
            f"| {stat['empty_evidence_hypothesis_count']} "
            f"| {stat['verbatim_utterance_value_text_count']} "
            f"({stat['turns_with_verbatim_utterance_value_text']} 턴) |"
        )

    lines.extend(
        [
            "",
            "`full_dst`의 미확보 턴은 official Full run이 그 턴에 도달하지 못한 경우다.",
            "Gold 스냅숏의 `value_text`는 canonical ID에서 기계적으로 만들어지고 provenance가",
            "균일하게 explicit/confirmed다. Full 스냅숏은 LLM이 실제 발화에서 쓴 문면이라 발화",
            "조각이 그대로 실려 올 수 있다. 위 표의 마지막 열이 그 실측치이며, 두 조건의 차이는",
            "상태 내용 차이와 문면 출처 차이가 섞인 결과다.",
            "",
            "## 턴 단위 상태 차이",
            "",
            f"| 항목 | 값 |",
            "| --- | ---: |",
            f"| 양쪽 상태가 모두 있는 턴 | {divergence['comparable_turns']} |",
            f"| 상태가 동일한 턴 | {divergence['identical_state_turns']} |",
            f"| 상태가 갈린 턴 | {divergence['diverged_state_turns']} |",
            f"| 갈린 턴의 평균 차이 크기 | "
            f"{divergence['mean_divergence_size_over_comparable_turns']} |",
            f"| 한 번이라도 갈린 에피소드 | "
            f"{divergence['episodes_with_any_divergence']}/{divergence['episodes']} |",
            f"| 갈린 뒤 마지막 비교 턴에서 다시 일치한 에피소드 | "
            f"{divergence['episodes_recovered_after_divergence']} |",
            f"| 갈린 에피소드 중 차이가 한 번도 줄지 않은 것 | "
            f"{divergence['episodes_where_divergence_never_shrinks']}"
            f"/{divergence['episodes_with_divergence_and_multiple_comparable_turns']} |",
            "",
            "처음 갈라진 턴의 분포:",
            "",
            "| 처음 갈라진 턴 | 에피소드 수 |",
            "| ---: | ---: |",
        ]
    )
    for turn, count in divergence["first_divergence_turn_counts"].items():
        lines.append(f"| {turn} | {count} |")

    lines.extend(
        [
            "",
            "## 에피소드별 갈라짐 궤적",
            "",
            "`턴별 차이 크기`는 비교 가능한 턴의 canonical ID 차이 크기를 순서대로 나열한 것이다.",
            "",
            "| 에피소드 | 설계 상황 | 비교 가능 턴 | 처음 갈라진 턴 | 턴별 차이 크기 | 차이가 줄지 않음 |",
            "| --- | --- | ---: | ---: | --- | :---: |",
        ]
    )
    for episode in compact["episodes"]:
        trajectory = episode["divergence_trajectory"]
        sizes = trajectory["divergence_size_by_turn"]
        # 한 번도 갈리지 않은 에피소드에는 단조성을 표시하지 않는다.
        shrink = (
            "-"
            if trajectory["comparable_turns"] <= 1
            or trajectory["first_divergence_turn"] is None
            else ("yes" if trajectory["divergence_never_shrinks"] else "no")
        )
        lines.append(
            f"| `{episode['scenario_id']}` | {episode['designed']['title']} "
            f"| {trajectory['comparable_turns']} "
            f"| {trajectory['first_divergence_turn'] if trajectory['first_divergence_turn'] is not None else '-'} "
            f"| {' → '.join(str(size) for size in sizes) or '-'} "
            f"| {shrink} |"
        )

    lines.extend(
        [
            "",
            "## 차이가 가장 크게 벌어진 턴",
            "",
            "직전 비교 턴보다 차이가 가장 많이 커진 턴을 궤적에서 계산해 고른 것이다.",
            "사람이 선별한 사례가 아니므로 재생성하면 같은 목록이 나온다.",
            "",
        ]
    )
    for item in compact["notable_divergence_turns"]:
        lines.extend(
            [
                f"### `{item['scenario_id']}` turn {item['turn']} — {item['title']}",
                "",
                f"> {item['utterance']}",
                "",
                f"- Gold State Diff: {_ids(item['gold_state_diff'])}",
                f"- 상태 차이: 놓침 {_ids(item['missing'])} / 추가 {_ids(item['extra'])} "
                f"(직전 대비 +{item['divergence_increase']}, 누적 {item['divergence_size']})",
            ]
        )
        for name in conditions:
            summary_text = item["summaries"].get(name)
            lines.append(
                f"- `{name}` 추론: {summary_text if summary_text else '추론 없음'}"
            )
        lines.append("")

    lines.extend(
        [
            "## Artifact",
            "",
            "| 종류 | 경로 | Git |",
            "| --- | --- | --- |",
            "| 원본 (렌더된 상태 포함) | `reports/tablet_domain_intent_probe_turn_v1.json` | 제외 |",
            "| 전체 턴 상세 문서 | `reports/tablet_domain_intent_probe_turn_v1.md` | 제외 |",
            "| LLM 호출 trace | `logs/tablet_domain_intent_probe_turn_v1_llm_trace.jsonl` | 제외 |",
            "| 압축 결과 | `data/results/tablet_domain_intent_probe_turn_v1.json` | 추적 |",
            "| 이 문서 | `docs/tablet_domain_intent_probe_turn_v1.md` | 추적 |",
            "| manifest | `data/manifests/tablet_domain_intent_probe_turn_v1.json` | 추적 |",
            "",
            "재생성:",
            "",
            "```powershell",
            "cd backend",
            "python scripts\\run_tablet_domain_intent_probe.py --overwrite   # LLM 호출 발생",
            "python scripts\\run_tablet_domain_intent_probe.py --reaggregate-only  # LLM 0회",
            "python scripts\\verify_tablet_domain_intent_probe.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _render_footer(report: dict[str, Any]) -> list[str]:
    runtime = report.get("runtime") or {}
    sources = report.get("source_artifacts") or {}
    lines = [
        "## 재현 정보",
        "",
        f"- schema: `{report['schema_version']}`",
        f"- 비교 단위: `{report['probe_contract']['comparison_unit']}`",
        f"- prompt: `{report['probe_contract']['prompt_version']}`, "
        f"response schema: `{report['probe_contract']['response_schema_version']}`",
        f"- provider: `{runtime.get('provider')}`, "
        f"requested model: `{runtime.get('requested_model')}`, temperature 0.0",
        f"- structured mode: `{runtime.get('structured_mode_preferred')}`",
        f"- 실행 시각: {report.get('started_at')} ~ {report.get('completed_at')}",
        f"- 실행 커밋: `{runtime.get('execution_commit')}`",
        "",
        "읽기 전용으로 사용한 동결 입력:",
        "",
    ]
    for name, artifact in sources.items():
        lines.append(
            f"- `{name}`: `{artifact['path']}` (sha256 `{artifact['sha256'][:16]}...`)"
        )
    lines.extend(
        [
            "",
            "재생성 명령:",
            "",
            "```powershell",
            "cd backend",
            "# 원본 실행 (LLM 호출 발생)",
            "python scripts\\run_tablet_domain_intent_probe.py --overwrite",
            "# 이 문서만 재생성 (LLM 호출 0회)",
            "python scripts\\run_tablet_domain_intent_probe.py --reaggregate-only",
            "# 오프라인 검증",
            "python scripts\\verify_tablet_domain_intent_probe.py",
            "```",
            "",
            "구조화 원본은 같은 폴더의 `tablet_domain_intent_probe_turn_v1.json`,",
            "LLM 호출 trace는 `logs/tablet_domain_intent_probe_turn_v1_llm_trace.jsonl`에 있다.",
            "세 파일 모두 Git 제외 경로다.",
            "",
        ]
    )
    return lines
