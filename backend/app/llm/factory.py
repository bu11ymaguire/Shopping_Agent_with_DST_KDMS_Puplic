"""제공업체 선택을 한 곳으로 모은다.

LangGraph 노드는 ``build_client()``가 돌려준 ``LLMClient``만 받는다. 노드 안에서
제공업체 SDK를 직접 호출하지 않으므로, 제공업체가 바뀌어도 노드는 수정하지 않는다.

    LangGraph Node
      → LLMClient (StructuredLLMClient)
         ├─ LuxiaTransport   제3자 브리지 (GPT-4o-mini)
         └─ MockTransport    키 없이 테스트
"""

from __future__ import annotations

from app.config import LLMSettings, load_llm_settings
from app.llm.base import STRUCTURED_MODE_LADDER, LLMTransport, StructuredMode
from app.llm.luxia import LuxiaTransport
from app.llm.mock import MockTransport
from app.llm.structured import StructuredLLMClient
from app.llm.trace import NullTraceWriter, TraceWriter

# 2026-08-06 실제 capability probe에서 Luxia GPT-4o-mini 브리지가
# json_schema + strict를 대조군에서도 강제함을 확인했다. Luxia의 정상 경로는
# 검증된 한 모드만 사용하고, 공급자를 바꾼 경우에만 범용 3단 사다리를 쓴다.
LUXIA_VERIFIED_MODE_LADDER: tuple[StructuredMode, ...] = ("json_schema",)


def default_mode_ladder(settings: LLMSettings) -> tuple[StructuredMode, ...]:
    if settings.provider == "luxia":
        return LUXIA_VERIFIED_MODE_LADDER
    return STRUCTURED_MODE_LADDER


def build_transport(settings: LLMSettings) -> LLMTransport:
    if settings.provider == "luxia":
        return LuxiaTransport(settings)
    if settings.provider == "mock":
        return MockTransport()
    raise ValueError(f"알 수 없는 provider: {settings.provider!r}")


def build_client(
    settings: LLMSettings | None = None,
    *,
    transport: LLMTransport | None = None,
    trace: bool = True,
    trace_filename: str = "llm_trace.jsonl",
    unsupported_modes: frozenset[StructuredMode] | set[StructuredMode] | None = None,
    mode_ladder: tuple[StructuredMode, ...] | None = None,
) -> StructuredLLMClient:
    """설정에 맞는 LLM 클라이언트를 만든다.

    Parameters
    ----------
    settings:
        생략하면 환경 변수에서 읽는다.
    transport:
        직접 주입하면 provider 설정을 무시한다. 테스트에서 쓴다.
    trace:
        False면 로그 파일을 만들지 않는다.
    unsupported_modes:
        capability probe가 확인한 "이 제공업체가 거부하는 모드". 미리 주입하면
        첫 호출에서 실패할 모드를 시도하는 왕복을 없앨 수 있다.
    mode_ladder:
        직접 지정하지 않으면 실제 probe가 확인된 Luxia는 ``json_schema``만,
        그 밖의 provider는 범용 3단 강등 경로를 사용한다.
    """
    resolved = settings or load_llm_settings()
    writer: TraceWriter = (
        TraceWriter(resolved.log_dir, filename=trace_filename)
        if trace
        else NullTraceWriter()
    )

    client = StructuredLLMClient(
        transport or build_transport(resolved),
        resolved,
        trace_writer=writer,
        mode_ladder=mode_ladder or default_mode_ladder(resolved),
    )
    for mode in unsupported_modes or ():
        client.mark_unsupported(mode)
    return client
