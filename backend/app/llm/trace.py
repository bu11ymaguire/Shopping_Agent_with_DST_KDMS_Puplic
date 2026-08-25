"""LLM 호출 연구 로그.

제3자 API를 쓰면 model alias가 실제로 어떤 snapshot을 가리키는지 알 수 없다.
그래서 요청과 응답을 원본째로 남긴다.

특히 다음을 구분해 저장한다. 이 구분이 없으면 나중에 "Understanding 성능이
낮은 이유가 모델 때문인지 스키마 매핑 때문인지"를 분리할 수 없다.

    raw_response       LLM 원본 출력
    validated_output   Pydantic 검증 후 출력
    validation_errors  검증 실패 이유
    attempts           재시도별 결과
    structured_mode    실제로 성공한 모드 (fallback이 실행됐는지)
    prompt_version     프롬프트 버전
    schema_version     스키마 버전

LangSmith 같은 외부 도구에 로그를 의존하지 않는다. 비용이나 계정 문제 없이
실험 데이터를 재분석할 수 있어야 한다.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.llm.base import StructuredMode


class AttemptRecord(BaseModel):
    """구조화 출력 시도 한 번의 결과."""

    attempt_index: int
    structured_mode: StructuredMode
    #: repair 재요청이었는지. 첫 시도는 False.
    is_repair: bool = False
    http_status: int | None = None
    latency_ms: int | None = None
    #: 이 시도에서 받은 원본 텍스트. 길어도 자르지 않는다. 연구 로그가 원본이어야 한다.
    raw_text: str | None = None
    outcome: Literal[
        "validated",
        "http_error",
        "shape_error",
        "json_parse_error",
        "schema_validation_error",
        "timeout",
    ]
    error_detail: str | None = None


class LLMTraceRecord(BaseModel):
    """LLM 호출 한 번(= 강등과 재시도를 모두 포함)의 로그 한 줄."""

    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── 어디서 호출됐는지 ──────────────────────────────────────
    conversation_id: str | None = None
    turn: int | None = None
    node: str = "unknown"

    # ── 무엇으로 호출했는지 ────────────────────────────────────
    provider: str
    #: 우리가 의도한 모델.
    requested_model: str
    #: 응답이 에코한 모델. None이면 브리지가 제거했다는 뜻이다.
    reported_model: str | None = None
    prompt_version: str = "unversioned"
    schema_name: str | None = None
    schema_version: str | None = None
    temperature: float
    max_tokens: int | None = None

    # ── 결과 ───────────────────────────────────────────────────
    #: 최종적으로 성공한 모드. 실패하면 마지막으로 시도한 모드.
    structured_mode: StructuredMode | None = None
    #: json_schema보다 아래로 내려갔는지. 제공업체 호환성 판단의 근거다.
    fallback_used: bool = False
    validation_success: bool
    #: transport 재시도(429/5xx)를 제외한, 스키마 실패로 인한 재요청 횟수.
    retry_count: int = 0
    #: timeout, 429, 5xx 때문에 transport 내부에서 다시 보낸 횟수.
    transport_retry_count: int = 0

    raw_response: str | None = None
    validated_output: dict[str, Any] | None = None
    validation_errors: list[str] = Field(default_factory=list)

    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None

    attempts: list[AttemptRecord] = Field(default_factory=list)

    #: 보낸 메시지. 프롬프트 회귀를 추적하려면 원문이 필요하다.
    messages: list[dict[str, str]] = Field(default_factory=list)


class TraceWriter:
    """JSONL append 전용 writer.

    한 프로세스 안에서 여러 코루틴이 같은 파일에 쓸 수 있으므로 lock을 둔다.
    """

    def __init__(self, log_dir: Path | str, *, filename: str = "llm_trace.jsonl") -> None:
        self.log_dir = Path(log_dir)
        self.path = self.log_dir / filename
        self._lock = threading.Lock()
        self._ready = False

    def _ensure_dir(self) -> None:
        if self._ready:
            return
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._ready = True

    def write(self, record: LLMTraceRecord) -> None:
        self._ensure_dir()
        line = record.model_dump_json()
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def read_all(self) -> list[LLMTraceRecord]:
        """저장된 로그를 다시 읽는다. 사후 분석과 테스트에 쓴다."""
        if not self.path.exists():
            return []
        records: list[LLMTraceRecord] = []
        for raw_line in self.path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            records.append(LLMTraceRecord.model_validate_json(line))
        return records


class NullTraceWriter(TraceWriter):
    """로그를 남기지 않는 writer. 단위 테스트에서 파일을 만들지 않기 위해 쓴다."""

    def __init__(self) -> None:  # noqa: D107 - 부모 시그니처를 의도적으로 무시한다.
        self.log_dir = Path(os.devnull)
        self.path = Path(os.devnull)
        self._lock = threading.Lock()
        self._ready = True

    def write(self, record: LLMTraceRecord) -> None:
        return None

    def read_all(self) -> list[LLMTraceRecord]:
        return []


def write_report(path: Path | str, payload: Any) -> Path:
    """probe·평가 결과를 사람이 읽을 수 있는 JSON으로 저장한다."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(indent=2)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    target.write_text(text + "\n", encoding="utf-8")
    return target
