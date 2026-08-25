"""교체 가능한 LLM 계층.

노드는 ``LLMClient``만 알면 된다. 제공업체 선택은 ``factory.build_client()``에서 일어난다.
"""

from app.llm.base import (
    STRUCTURED_MODE_LADDER,
    ChatMessage,
    LLMClient,
    LLMError,
    LLMHTTPError,
    LLMResponseShapeError,
    LLMTimeoutError,
    LLMTransport,
    RawCompletion,
    StructuredMode,
    StructuredOutputError,
    assistant,
    system,
    user,
)
from app.llm.factory import build_client, build_transport
from app.llm.luxia import LuxiaTransport
from app.llm.mock import MockTransport, ScriptedStep
from app.llm.structured import StructuredLLMClient
from app.llm.trace import (
    AttemptRecord,
    LLMTraceRecord,
    NullTraceWriter,
    TraceWriter,
    write_report,
)

__all__ = [
    "STRUCTURED_MODE_LADDER",
    "AttemptRecord",
    "ChatMessage",
    "LLMClient",
    "LLMError",
    "LLMHTTPError",
    "LLMResponseShapeError",
    "LLMTimeoutError",
    "LLMTraceRecord",
    "LLMTransport",
    "LuxiaTransport",
    "MockTransport",
    "NullTraceWriter",
    "RawCompletion",
    "ScriptedStep",
    "StructuredLLMClient",
    "StructuredMode",
    "StructuredOutputError",
    "TraceWriter",
    "assistant",
    "build_client",
    "build_transport",
    "system",
    "user",
    "write_report",
]
