"""API 호출 없이 동작하는 transport.

두 가지 목적이 있다.

    1. 키나 비용 없이 상태 전이·정책·랭킹·평가 코드를 테스트한다.
    2. fallback 강등과 repair 재요청 경로를 결정론적으로 재현한다.
       실패를 실제 API에서 우연히 만나기를 기다릴 수는 없다.

스키마 이름은 ``response_format["json_schema"]["name"]``에서 읽는다. 실제
제공업체가 받는 것과 같은 데이터를 보고 판단하므로 mock 전용 통신 채널을
따로 만들지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.llm.base import (
    ChatMessage,
    LLMHTTPError,
    LLMTimeoutError,
    RawCompletion,
    extract_text,
    extract_usage,
)

#: (messages, response_format) → 응답 본문 텍스트. 보통 JSON 문자열을 돌려준다.
MockHandler = Callable[[list[ChatMessage], dict[str, Any] | None], str]


@dataclass
class ScriptedStep:
    """호출 순서에 따라 미리 정해 둔 응답 한 번.

    ``text``를 주면 그 문자열을 그대로 돌려준다. ``raise_status``를 주면
    해당 상태 코드로 ``LLMHTTPError``를 던진다. ``raise_timeout``은
    타임아웃을 재현한다.
    """

    text: str | None = None
    raise_status: int | None = None
    raise_timeout: bool = False
    latency_ms: int = 12


@dataclass
class MockTransport:
    """스키마 이름별 핸들러와 스크립트된 응답을 지원하는 transport."""

    provider_name: str = "mock"

    #: 스키마 이름 → 핸들러.
    handlers: dict[str, MockHandler] = field(default_factory=dict)
    #: 등록된 핸들러가 없을 때 돌려줄 텍스트.
    default_text: str = "mock response"
    #: 앞에서부터 순서대로 소비된다. 비면 handlers로 넘어간다.
    script: list[ScriptedStep] = field(default_factory=list)
    #: usage 필드를 응답에 포함할지. Luxia가 usage를 주지 않는 경우를 재현하려면 False.
    include_usage: bool = True
    fixed_latency_ms: int = 12

    #: 호출 이력. 테스트에서 "몇 번 호출됐고 어떤 모드였나"를 확인한다.
    calls: list[dict[str, Any]] = field(default_factory=list)

    _script_index: int = 0

    def register(self, schema_name: str, handler: MockHandler) -> None:
        self.handlers[schema_name] = handler

    # ------------------------------------------------------------------ #
    # 내부
    # ------------------------------------------------------------------ #

    @staticmethod
    def _schema_name(response_format: dict[str, Any] | None) -> str | None:
        if not response_format:
            return None
        block = response_format.get("json_schema")
        if isinstance(block, dict):
            name = block.get("name")
            if isinstance(name, str) and name:
                return name
        return None

    def _resolve_text(
        self,
        messages: list[ChatMessage],
        response_format: dict[str, Any] | None,
    ) -> str:
        name = self._schema_name(response_format)
        if name and name in self.handlers:
            return self.handlers[name](messages, response_format)

        # 스키마 이름이 없는 모드(prompt_only)에서는 등록된 이름이 프롬프트에
        # 등장하는지 확인한다. 강등된 경로에서도 같은 핸들러를 재사용하기 위한 것이다.
        haystack = "\n".join(message.content for message in messages)
        for schema_name, handler in self.handlers.items():
            if schema_name in haystack:
                return handler(messages, response_format)

        return self.default_text

    # ------------------------------------------------------------------ #
    # LLMTransport
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> RawCompletion:
        payload: dict[str, Any] = {
            "model": "mock",
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [message.to_payload() for message in messages],
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if extra_body:
            payload.update(extra_body)

        self.calls.append(
            {
                "schema_name": self._schema_name(response_format),
                "response_format_type": (response_format or {}).get("type"),
                "message_count": len(messages),
            }
        )

        latency_ms = self.fixed_latency_ms

        if self._script_index < len(self.script):
            step = self.script[self._script_index]
            self._script_index += 1
            latency_ms = step.latency_ms
            if step.raise_timeout:
                raise LLMTimeoutError("mock timeout")
            if step.raise_status is not None:
                raise LLMHTTPError(
                    step.raise_status,
                    json.dumps({"error": "mock error"}),
                    url="mock://transport",
                )
            text = step.text if step.text is not None else self.default_text
        else:
            text = self._resolve_text(messages, response_format)

        raw: dict[str, Any] = {
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "model": "mock",
        }
        if self.include_usage:
            raw["usage"] = {"prompt_tokens": 0, "completion_tokens": 0}

        input_tokens, output_tokens = extract_usage(raw)
        return RawCompletion(
            text=extract_text(raw),
            raw=raw,
            status_code=200,
            latency_ms=latency_ms,
            reported_model="mock",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            request_body=payload,
            transport_retries=0,
        )

    async def aclose(self) -> None:
        return None

    def reset(self) -> None:
        """호출 이력과 스크립트 위치를 되돌린다."""
        self.calls.clear()
        self._script_index = 0
