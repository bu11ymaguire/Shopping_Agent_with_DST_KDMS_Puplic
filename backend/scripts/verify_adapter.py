"""Adapter 계층 자체 검증. API 키 없이 실행한다.

    python scripts/verify_adapter.py

fallback 강등, repair 재요청, 모드 거부 캐싱, 예외 전파를 MockTransport로
결정론적으로 재현한다. LLM 계층을 수정한 뒤 회귀를 확인하는 용도다.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import ClassVar, Literal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, Field  # noqa: E402

from app.config import LLMSettings  # noqa: E402
from app.llm import (  # noqa: E402
    ChatMessage,
    LLMHTTPError,
    MockTransport,
    ScriptedStep,
    StructuredLLMClient,
    StructuredOutputError,
    system,
    user,
)
from app.llm.json_utils import extract_json_object, to_strict_json_schema  # noqa: E402
from app.llm.trace import NullTraceWriter  # noqa: E402
from app.llm.factory import build_client  # noqa: E402


class Demo(BaseModel):
    # 필드가 아니라 메타데이터다. ClassVar로 선언하지 않으면 Pydantic이 필드로 보고 거부한다.
    schema_version: ClassVar[str] = "demo-v1"

    intent: Literal["search", "reject"]
    score: float = Field(ge=0.0, le=1.0)
    note: str


SETTINGS = LLMSettings(provider="mock", api_key="unused")


def make_client(transport: MockTransport) -> StructuredLLMClient:
    return StructuredLLMClient(transport, SETTINGS, trace_writer=NullTraceWriter())


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


async def main() -> None:
    valid = json.dumps({"intent": "search", "score": 0.5, "note": "ok"})
    messages = [system("You extract intents."), user("아이폰 찾아줘")]

    # 1) json_schema 경로가 첫 시도에 성공한다.
    transport = MockTransport(script=[ScriptedStep(text=valid)])
    client = make_client(transport)
    result = await client.generate_structured(messages=messages, response_model=Demo)
    check("json_schema 첫 시도 성공", result.intent == "search")
    check("성공 모드 기록", client.preferred_mode == "json_schema", str(client.preferred_mode))
    check(
        "response_format이 json_schema로 전달됨",
        transport.calls[0]["response_format_type"] == "json_schema",
        str(transport.calls[0]),
    )
    check(
        "스키마 이름이 전달됨",
        transport.calls[0]["schema_name"] == "Demo",
        str(transport.calls[0]),
    )

    # 2) 코드 펜스로 감싼 응답도 파싱한다.
    fenced = f"여기 결과입니다.\n```json\n{valid}\n```\n확인해 주세요."
    transport = MockTransport(script=[ScriptedStep(text=fenced)])
    result = await make_client(transport).generate_structured(
        messages=messages, response_model=Demo
    )
    check("코드 펜스 응답 파싱", result.note == "ok")

    # 3) 스키마 위반 → 같은 모드에서 repair 1회 → 성공.
    broken = json.dumps({"intent": "browse", "score": 9.9})
    transport = MockTransport(script=[ScriptedStep(text=broken), ScriptedStep(text=valid)])
    client = make_client(transport)
    result = await client.generate_structured(messages=messages, response_model=Demo)
    check("스키마 위반 후 repair 성공", result.intent == "search")
    check("repair 재요청 1회", len(transport.calls) == 2, f"{len(transport.calls)}회 호출")
    check(
        "repair도 같은 모드 유지",
        transport.calls[1]["response_format_type"] == "json_schema",
        str(transport.calls[1]),
    )

    # 4) 제공업체가 json_schema를 400으로 거부 → json_object로 강등.
    transport = MockTransport(
        script=[
            ScriptedStep(raise_status=400),  # json_schema 거부
            ScriptedStep(text=valid),  # json_object 성공
        ]
    )
    client = make_client(transport)
    result = await client.generate_structured(messages=messages, response_model=Demo)
    check("400 거부 후 json_object로 강등", result.intent == "search")
    check(
        "json_schema가 사용 불가로 기록됨",
        "json_schema" in client.unsupported_modes,
        str(sorted(client.unsupported_modes)),
    )
    check(
        "두 번째 시도는 json_object",
        transport.calls[1]["response_format_type"] == "json_object",
        str(transport.calls[1]),
    )

    # 거부 기록이 남아 다음 호출은 json_schema를 건너뛴다.
    transport.reset()
    transport.script = [ScriptedStep(text=valid)]
    await client.generate_structured(messages=messages, response_model=Demo)
    check(
        "거부 모드를 다시 시도하지 않음",
        transport.calls[0]["response_format_type"] == "json_object",
        str(transport.calls[0]),
    )

    # 5) json_schema·json_object 모두 거부 → prompt_only로 강등.
    transport = MockTransport(
        script=[
            ScriptedStep(raise_status=400),
            ScriptedStep(raise_status=422),
            ScriptedStep(text=valid),
        ]
    )
    client = make_client(transport)
    result = await client.generate_structured(messages=messages, response_model=Demo)
    check("두 모드 거부 후 prompt_only 성공", result.intent == "search")
    check(
        "prompt_only는 response_format 없음",
        transport.calls[2]["response_format_type"] is None,
        str(transport.calls[2]),
    )

    # 6) 5xx는 모드 문제가 아니므로 강등하지 않고 올린다.
    transport = MockTransport(script=[ScriptedStep(raise_status=503)])
    client = make_client(transport)
    try:
        await client.generate_structured(messages=messages, response_model=Demo)
        check("5xx는 예외로 올라온다", False, "예외가 발생하지 않았다")
    except LLMHTTPError as exc:
        check("5xx는 예외로 올라온다", exc.status_code == 503)
        check(
            "5xx는 모드를 사용 불가로 만들지 않음",
            "json_schema" not in client.unsupported_modes,
        )

    # 7) 모든 경로 소진 → StructuredOutputError.
    transport = MockTransport(script=[ScriptedStep(text="JSON이 아닙니다")] * 8)
    client = make_client(transport)
    try:
        await client.generate_structured(messages=messages, response_model=Demo)
        check("전부 실패 시 StructuredOutputError", False, "예외가 발생하지 않았다")
    except StructuredOutputError as exc:
        check("전부 실패 시 StructuredOutputError", len(exc.attempts) > 0, f"{len(exc.attempts)}회 시도")

    # 8) strict JSON Schema 변환이 additionalProperties와 required를 채운다.
    schema = to_strict_json_schema(Demo)
    check("additionalProperties=false", schema.get("additionalProperties") is False)
    check(
        "모든 property가 required",
        set(schema["required"]) == set(schema["properties"].keys()),
        str(schema["required"]),
    )

    # 9) 중괄호 균형 파싱이 문자열 안의 중괄호에 속지 않는다.
    tricky = 'prefix {"a": "}{ not a brace", "b": {"c": 1}} suffix'
    parsed = extract_json_object(tricky)
    check("문자열 내 중괄호 무시", parsed == {"a": "}{ not a brace", "b": {"c": 1}}, str(parsed))

    # 10) generate_text 경로.
    transport = MockTransport(default_text="안녕하세요")
    text = await make_client(transport).generate_text(messages=messages)
    check("generate_text 동작", text == "안녕하세요", text)

    # 11) 실제 probe가 case A였으므로 factory의 Luxia 기본값은 strict 한 경로다.
    luxia_transport = MockTransport(script=[ScriptedStep(text=valid)])
    luxia_settings = LLMSettings(provider="luxia", api_key="unused")
    luxia_client = build_client(
        luxia_settings,
        transport=luxia_transport,
        trace=False,
    )
    await luxia_client.generate_structured(messages=messages, response_model=Demo)
    check(
        "Luxia 기본 경로는 검증된 json_schema만 사용",
        [call["response_format_type"] for call in luxia_transport.calls]
        == ["json_schema"],
        str(luxia_transport.calls),
    )

    print("\n모든 스모크 테스트 통과")


if __name__ == "__main__":
    asyncio.run(main())
