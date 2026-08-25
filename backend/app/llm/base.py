"""LLM 계층의 공통 계약.

계층을 둘로 나눈다.

    Layer 1  LLMTransport   제공업체별. HTTP 요청 한 번을 보내고 원본 응답을 돌려준다.
    Layer 2  LLMClient      제공업체 무관. 스키마 검증과 fallback 강등을 담당한다.

LangGraph 노드는 Layer 2만 본다. 그래서 제공업체가 바뀌어도 노드 코드는 그대로다.
판단 기준은 "ChatOpenAI가 동작하는가"가 아니라 "어떤 제공업체를 쓰더라도 같은
Pydantic 객체가 나오는가"다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

TModel = TypeVar("TModel", bound=BaseModel)

Role = Literal["system", "user", "assistant"]

#: Structured output을 얻어내는 방식. 위에서 아래로 강등한다.
#: - json_schema : strict JSON Schema. 스키마 준수를 제공업체가 보장한다.
#: - json_object : JSON 형식만 보장. 필드와 enum은 틀릴 수 있다.
#: - prompt_only : 일반 텍스트. 프롬프트로 JSON을 요구하고 직접 파싱한다.
StructuredMode = Literal["json_schema", "json_object", "prompt_only"]

STRUCTURED_MODE_LADDER: tuple[StructuredMode, ...] = (
    "json_schema",
    "json_object",
    "prompt_only",
)


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str

    def to_payload(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


def system(content: str) -> ChatMessage:
    return ChatMessage(role="system", content=content)


def user(content: str) -> ChatMessage:
    return ChatMessage(role="user", content=content)


def assistant(content: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=content)


@dataclass
class RawCompletion:
    """제공업체 응답 원본과 그 주변 관측값.

    Luxia 브리지는 응답을 정규화해 ``choices[0].message.content``만 돌려줄 수
    있다. 그래서 ``reported_model``과 토큰 수는 ``None``일 수 있다. 이 값이
    비어 있다는 사실 자체가 연구 로그에 남아야 할 관측 결과다.
    """

    text: str
    raw: dict[str, Any]
    status_code: int
    latency_ms: int
    #: 응답이 에코한 모델 이름. 브리지가 제거하면 None.
    reported_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: 실제로 보낸 request body. API 키는 헤더에 있으므로 여기 포함되지 않는다.
    request_body: dict[str, Any] = field(default_factory=dict)
    #: 429/5xx로 재시도한 횟수.
    transport_retries: int = 0

    @property
    def has_usage(self) -> bool:
        return self.input_tokens is not None or self.output_tokens is not None


# --------------------------------------------------------------------------- #
# 예외
# --------------------------------------------------------------------------- #


class LLMError(Exception):
    """LLM 계층의 모든 예외의 기반."""


class LLMHTTPError(LLMError):
    """2xx가 아닌 응답. probe가 원본 body를 읽을 수 있도록 함께 담는다."""

    def __init__(self, status_code: int, body: str, *, url: str = "") -> None:
        self.status_code = status_code
        self.body = body
        self.url = url
        preview = body if len(body) <= 500 else f"{body[:500]}…"
        super().__init__(f"HTTP {status_code} from {url or 'LLM endpoint'}: {preview}")


class LLMTimeoutError(LLMError):
    """요청이 timeout_seconds를 넘겼다."""


class LLMResponseShapeError(LLMError):
    """2xx이지만 choices[0].message.content를 찾을 수 없다."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        super().__init__(
            "응답에서 choices[0].message.content를 찾을 수 없습니다. "
            f"최상위 키: {sorted(raw.keys())}"
        )


class StructuredOutputError(LLMError):
    """fallback 강등과 repair 재시도를 모두 소진해도 스키마 검증에 실패했다."""

    def __init__(self, message: str, *, attempts: list[Any] | None = None) -> None:
        self.attempts = attempts or []
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class LLMTransport(Protocol):
    """제공업체별 HTTP 호출 한 겹.

    ``response_format``과 ``extra_body``를 그대로 통과시키는 이유는
    capability probe가 제공업체가 무엇을 받아들이는지 실측해야 하기 때문이다.
    """

    provider_name: str

    async def complete(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
        retries: int | None = None,
    ) -> RawCompletion: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class LLMClient(Protocol):
    """LangGraph 노드가 보는 인터페이스.

    노드는 이 두 메서드만 쓴다. 제공업체 교체는 factory에서 일어난다.
    """

    async def generate_structured(
        self,
        *,
        messages: list[ChatMessage],
        response_model: type[TModel],
        temperature: float = 0.0,
        node: str = "unknown",
        prompt_version: str = "unversioned",
        conversation_id: str | None = None,
        turn: int | None = None,
    ) -> TModel: ...

    async def generate_text(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        node: str = "unknown",
        prompt_version: str = "unversioned",
        conversation_id: str | None = None,
        turn: int | None = None,
    ) -> str: ...

    async def aclose(self) -> None: ...


# --------------------------------------------------------------------------- #
# 응답 파싱 도우미
# --------------------------------------------------------------------------- #


def extract_text(raw: dict[str, Any]) -> str:
    """OpenAI 호환 응답에서 본문 텍스트를 꺼낸다.

    브리지가 필드를 조금씩 다르게 정규화할 수 있으므로 방어적으로 읽는다.
    구조가 예상과 다르면 조용히 빈 문자열을 돌려주지 않고 예외를 던진다.
    조용한 실패는 "모델이 이해했는데 결과가 반영되지 않는" 상황을 만든다.
    """
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content
            # 일부 브리지는 message 없이 text를 준다.
            text = first.get("text")
            if isinstance(text, str):
                return text

    # 최상위에 바로 content를 놓는 변형.
    for key in ("content", "output_text", "response"):
        value = raw.get(key)
        if isinstance(value, str):
            return value

    raise LLMResponseShapeError(raw)


def extract_usage(raw: dict[str, Any]) -> tuple[int | None, int | None]:
    """(input_tokens, output_tokens). 응답에 usage가 없으면 (None, None)."""
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return None, None

    def pick(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int):
                return value
        return None

    return (
        pick("prompt_tokens", "input_tokens"),
        pick("completion_tokens", "output_tokens"),
    )


def extract_reported_model(raw: dict[str, Any]) -> str | None:
    """응답이 에코한 모델 이름. 브리지가 제거하면 None.

    model alias가 실제로 어떤 snapshot을 가리키는지 불분명할 수 있으므로,
    이 값이 있는지 없는지를 연구 로그에 남긴다.
    """
    value = raw.get("model")
    return value if isinstance(value, str) and value else None
