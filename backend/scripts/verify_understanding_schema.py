"""UnderstandingOutput의 closed vocabulary와 교차 필드 계약을 검증한다."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from app.llm.json_utils import to_strict_json_schema  # noqa: E402
from app.config import LLMSettings  # noqa: E402
from app.llm import MockTransport, StructuredLLMClient  # noqa: E402
from app.llm.trace import NullTraceWriter  # noqa: E402
from app.models import UnderstandingOutput  # noqa: E402
from app.nodes import understand_utterance  # noqa: E402


TURN_ONE = {
    "utterance": "쓰던 아이폰이 고장 나서 새로 바꿔야 해요.",
    "intents": ["search"],
    "facets": {
        "subjective_property": None,
        "event": {
            "target": {"kind": "facet", "facet": "event"},
            "canonical_id": "event_device_failure",
            "value_text": "기존 기기 고장",
            "evidence_text": "쓰던 아이폰이 고장 나서",
            "origin": "explicit",
            "confidence": 1.0,
        },
        "activity": None,
        "goal_purpose": {
            "target": {"kind": "facet", "facet": "goal_purpose"},
            "canonical_id": "goal_replace_device",
            "value_text": "고장 난 기기 교체",
            "evidence_text": "새로 바꿔야 해요",
            "origin": "implicit",
            "confidence": 0.9,
        },
        "goal_audience": None,
    },
    "candidates": [
        {
            "target": {"kind": "category"},
            "canonical_id": "category_smartphone",
            "value_text": "스마트폰",
            "evidence_text": "아이폰",
            "origin": "explicit",
            "confidence": 1.0,
        },
        {
            "target": {"kind": "facet", "facet": "event"},
            "canonical_id": "event_device_failure",
            "value_text": "기존 기기 고장",
            "evidence_text": "쓰던 아이폰이 고장 나서",
            "origin": "explicit",
            "confidence": 1.0,
        },
        {
            "target": {"kind": "facet", "facet": "goal_purpose"},
            "canonical_id": "goal_replace_device",
            "value_text": "고장 난 기기 교체",
            "evidence_text": "새로 바꿔야 해요",
            "origin": "implicit",
            "confidence": 0.9,
        },
        {
            "target": {
                "kind": "constraint",
                "scope": "soft",
                "key": "urgency_pressure",
            },
            "canonical_id": "urgency_pressure",
            "value_text": "수령 대기 민감 가능성",
            "evidence_text": "고장 나서 새로 바꿔야",
            "origin": "inferred",
            "confidence": 0.64,
        },
    ],
    "item_action": None,
    "supersedes": [],
    "residual_color_choice": False,
}


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def rejected(payload: dict) -> bool:
    try:
        UnderstandingOutput.model_validate(payload)
        return False
    except ValidationError:
        return True


async def verify_node_call() -> None:
    transport = MockTransport(default_text=json.dumps(TURN_ONE, ensure_ascii=False))
    client = StructuredLLMClient(
        transport,
        LLMSettings(provider="mock", api_key="unused"),
        trace_writer=NullTraceWriter(),
    )
    result = await understand_utterance(
        client,
        utterance=TURN_ONE["utterance"],
        previous_state_summary={"recommended_items": []},
        conversation_id="verify",
        turn=1,
    )
    check("Understanding 노드가 검증 객체 반환", result.utterance == TURN_ONE["utterance"])
    check(
        "Understanding 노드는 strict schema 요청",
        transport.calls[0]["response_format_type"] == "json_schema",
    )


def main() -> None:
    result = UnderstandingOutput.model_validate(TURN_ONE)
    check("부록 C 1턴 시드 검증", result.intents == ["search"])
    check(
        "TypeScript id 호환 접근자",
        result.candidates[0].id == "category_smartphone",
    )

    free_id = copy.deepcopy(TURN_ONE)
    free_id["candidates"][3]["canonical_id"] = "fast_delivery_for_broken_phone"
    check("자유 생성 canonical_id 거부", rejected(free_id))

    mismatched_target = copy.deepcopy(TURN_ONE)
    mismatched_target["candidates"][0]["canonical_id"] = "battery"
    check("target과 맞지 않는 canonical_id 거부", rejected(mismatched_target))

    mismatched_key = copy.deepcopy(TURN_ONE)
    mismatched_key["candidates"][3]["target"]["key"] = "delivery_deadline"
    check("constraint key와 canonical_id 불일치 거부", rejected(mismatched_key))

    missing_duplicate = copy.deepcopy(TURN_ONE)
    missing_duplicate["candidates"] = [
        candidate
        for candidate in missing_duplicate["candidates"]
        if candidate["canonical_id"] != "event_device_failure"
    ]
    normalized = UnderstandingOutput.model_validate(missing_duplicate)
    check(
        "누락된 facet 후보를 candidates에 결정론적으로 보충",
        any(
            candidate.canonical_id == "event_device_failure"
            for candidate in normalized.candidates
        ),
    )

    duplicate_candidate = copy.deepcopy(TURN_ONE)
    duplicate_candidate["candidates"].append(
        copy.deepcopy(duplicate_candidate["candidates"][0])
    )
    check("중복 canonical_id 후보 거부", rejected(duplicate_candidate))

    premature_color = copy.deepcopy(TURN_ONE)
    premature_color["residual_color_choice"] = True
    check("구매 전 잔여 색상 수용 확정 거부", rejected(premature_color))

    actionless_purchase = copy.deepcopy(TURN_ONE)
    actionless_purchase["intents"] = ["purchase"]
    check("item_action 없는 purchase intent 거부", rejected(actionless_purchase))

    schema = to_strict_json_schema(UnderstandingOutput)
    check("schema_version은 출력 필드가 아님", "schema_version" not in schema["properties"])
    check("strict schema 최상위 추가 필드 금지", schema["additionalProperties"] is False)
    check(
        "모든 최상위 필드 required",
        set(schema["required"]) == set(schema["properties"]),
    )
    schema_text = str(schema)
    check("closed vocabulary가 JSON Schema에 포함", "delivery_deadline" in schema_text)

    asyncio.run(verify_node_call())

    print("\nUnderstandingOutput 스키마 검증 통과")


if __name__ == "__main__":
    main()
