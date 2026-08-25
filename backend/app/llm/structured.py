"""제공업체 무관 structured output 계층.

제3자 API가 OpenAI의 어느 기능까지 호환하는지 단정할 수 없으므로, 세 경우를
모두 처리하고 실제로 어느 경로가 쓰였는지를 로그에 남긴다.

    경우 A  json_schema  strict JSON Schema 지원. 스키마 준수를 제공업체가 보장.
    경우 B  json_object  JSON 형식만 보장. 필드와 enum이 틀릴 수 있다.
    경우 C  prompt_only  일반 텍스트. 프롬프트로 JSON을 요구하고 직접 파싱.

강등 규칙
    제공업체가 4xx(429 제외)로 파라미터를 거부하면 그 모드는 이 프로세스 동안
    사용 불가로 기록하고 다음 모드로 내려간다. 매 호출마다 실패할 모드를
    다시 시도하면 왕복 비용을 계속 낸다.

    반대로 스키마 검증 실패는 요청별 사건이므로 모드 사용 불가로 기록하지 않는다.
    같은 모드 안에서 오류 내용을 포함해 1회 repair 재요청한다.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app.config import LLMSettings
from app.llm.base import (
    STRUCTURED_MODE_LADDER,
    ChatMessage,
    LLMHTTPError,
    LLMResponseShapeError,
    LLMTimeoutError,
    LLMTransport,
    RawCompletion,
    StructuredMode,
    StructuredOutputError,
    TModel,
)
from app.llm.json_utils import (
    JsonExtractionError,
    extract_json_object,
    schema_version_of,
    to_strict_json_schema,
)
from app.llm.trace import AttemptRecord, LLMTraceRecord, NullTraceWriter, TraceWriter

#: repair 재요청 횟수. 같은 모드에서 이 횟수만큼 더 시도한 뒤 다음 모드로 내려간다.
DEFAULT_REPAIR_ATTEMPTS = 1


def _json_instruction(model: type[BaseModel], *, include_schema: bool) -> str:
    """json_object·prompt_only 모드에서 시스템 메시지에 덧붙일 지시문."""
    lines = [
        "You must reply with a single JSON object and nothing else.",
        "Do not wrap the JSON in markdown code fences.",
        "Do not add explanations before or after the JSON.",
        "Every field listed in the schema must be present.",
        "Use only the enum values given in the schema. Never invent new identifiers.",
    ]
    if include_schema:
        schema = to_strict_json_schema(model)
        lines.append("")
        lines.append(f"JSON Schema (name: {model.__name__}):")
        lines.append(_compact_json(schema))
    return "\n".join(lines)


def _compact_json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class StructuredLLMClient:
    """LangGraph 노드가 보는 LLM 클라이언트.

    ``generate_structured``는 제공업체가 무엇이든 항상 같은 Pydantic 객체를
    돌려주거나 ``StructuredOutputError``를 던진다. 중간 형태를 노출하지 않는다.
    """

    def __init__(
        self,
        transport: LLMTransport,
        settings: LLMSettings,
        *,
        trace_writer: TraceWriter | None = None,
        mode_ladder: tuple[StructuredMode, ...] = STRUCTURED_MODE_LADDER,
        repair_attempts: int = DEFAULT_REPAIR_ATTEMPTS,
    ) -> None:
        self._transport = transport
        self._settings = settings
        self._trace = trace_writer if trace_writer is not None else NullTraceWriter()
        self._ladder = mode_ladder
        self._repair_attempts = max(0, repair_attempts)

        #: 제공업체가 거부한 모드. 프로세스 수명 동안 유지한다.
        self._unsupported_modes: set[StructuredMode] = set()
        #: 가장 최근에 검증까지 성공한 모드. 다음 호출에서 먼저 시도한다.
        self._preferred_mode: StructuredMode | None = None

    # ------------------------------------------------------------------ #
    # 공개 상태 — probe와 리포트가 읽는다.
    # ------------------------------------------------------------------ #

    @property
    def unsupported_modes(self) -> frozenset[StructuredMode]:
        return frozenset(self._unsupported_modes)

    @property
    def preferred_mode(self) -> StructuredMode | None:
        return self._preferred_mode

    def mark_unsupported(self, mode: StructuredMode) -> None:
        """probe 결과를 미리 주입해 첫 호출의 왕복 낭비를 없앤다."""
        self._unsupported_modes.add(mode)

    # ------------------------------------------------------------------ #
    # 요청 조립
    # ------------------------------------------------------------------ #

    def _candidate_modes(self) -> list[StructuredMode]:
        modes = [mode for mode in self._ladder if mode not in self._unsupported_modes]
        if self._preferred_mode and self._preferred_mode in modes:
            modes.remove(self._preferred_mode)
            modes.insert(0, self._preferred_mode)
        return modes

    def _build_request(
        self,
        *,
        mode: StructuredMode,
        messages: list[ChatMessage],
        response_model: type[TModel],
    ) -> tuple[list[ChatMessage], dict[str, Any] | None]:
        """모드에 맞는 (메시지, response_format)을 만든다."""
        if mode == "json_schema":
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": to_strict_json_schema(response_model),
                },
            }
            return list(messages), response_format

        if mode == "json_object":
            # 형식만 보장되므로 스키마를 프롬프트에도 넣어 필드와 enum을 안내한다.
            augmented = self._augment(
                messages, _json_instruction(response_model, include_schema=True)
            )
            return augmented, {"type": "json_object"}

        augmented = self._augment(
            messages, _json_instruction(response_model, include_schema=True)
        )
        return augmented, None

    @staticmethod
    def _augment(messages: list[ChatMessage], instruction: str) -> list[ChatMessage]:
        """시스템 메시지에 지시문을 덧붙인다. 없으면 앞에 새로 만든다."""
        result = list(messages)
        for index, message in enumerate(result):
            if message.role == "system":
                result[index] = ChatMessage(
                    role="system", content=f"{message.content}\n\n{instruction}"
                )
                return result
        return [ChatMessage(role="system", content=instruction), *result]

    @staticmethod
    def _repair_message(raw_text: str, error_detail: str) -> ChatMessage:
        """검증 실패를 알려주고 다시 만들게 하는 사용자 메시지."""
        return ChatMessage(
            role="user",
            content=(
                "Your previous reply did not satisfy the schema.\n\n"
                f"Previous reply:\n{raw_text}\n\n"
                f"Problem:\n{error_detail}\n\n"
                "Reply again with a corrected JSON object only. "
                "Keep every required field and use only the allowed enum values."
            ),
        )

    # ------------------------------------------------------------------ #
    # generate_structured
    # ------------------------------------------------------------------ #

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
    ) -> TModel:
        started = time.perf_counter()
        attempts: list[AttemptRecord] = []
        last_completion: RawCompletion | None = None
        transport_retry_count = 0
        schema_failures: list[str] = []
        succeeded: TModel | None = None
        success_mode: StructuredMode | None = None
        repair_count = 0

        candidates = self._candidate_modes()
        if not candidates:
            raise StructuredOutputError(
                "시도할 수 있는 structured output 모드가 없습니다. "
                f"제공업체가 거부한 모드: {sorted(self._unsupported_modes)}"
            )

        for mode in candidates:
            base_messages, response_format = self._build_request(
                mode=mode, messages=messages, response_model=response_model
            )
            attempt_messages = base_messages
            mode_failed_by_provider = False

            for repair_index in range(self._repair_attempts + 1):
                is_repair = repair_index > 0
                if is_repair:
                    repair_count += 1

                try:
                    completion = await self._transport.complete(
                        messages=attempt_messages,
                        temperature=temperature,
                        response_format=response_format,
                    )
                except LLMHTTPError as exc:
                    attempts.append(
                        AttemptRecord(
                            attempt_index=len(attempts),
                            structured_mode=mode,
                            is_repair=is_repair,
                            http_status=exc.status_code,
                            outcome="http_error",
                            error_detail=str(exc),
                        )
                    )
                    # 4xx(429 제외)는 파라미터 거부다. 이 모드는 못 쓴다.
                    if 400 <= exc.status_code < 500 and exc.status_code != 429:
                        self._unsupported_modes.add(mode)
                        mode_failed_by_provider = True
                    else:
                        # 5xx나 연결 실패는 모드 문제가 아니므로 즉시 올린다.
                        raise
                    break
                except LLMTimeoutError as exc:
                    attempts.append(
                        AttemptRecord(
                            attempt_index=len(attempts),
                            structured_mode=mode,
                            is_repair=is_repair,
                            outcome="timeout",
                            error_detail=str(exc),
                        )
                    )
                    raise
                except LLMResponseShapeError as exc:
                    attempts.append(
                        AttemptRecord(
                            attempt_index=len(attempts),
                            structured_mode=mode,
                            is_repair=is_repair,
                            outcome="shape_error",
                            error_detail=str(exc),
                        )
                    )
                    break

                last_completion = completion
                transport_retry_count += completion.transport_retries

                # ── JSON 파싱 ──────────────────────────────────────
                try:
                    payload = extract_json_object(completion.text)
                except JsonExtractionError as exc:
                    detail = str(exc)
                    schema_failures.append(detail)
                    attempts.append(
                        AttemptRecord(
                            attempt_index=len(attempts),
                            structured_mode=mode,
                            is_repair=is_repair,
                            http_status=completion.status_code,
                            latency_ms=completion.latency_ms,
                            raw_text=completion.text,
                            outcome="json_parse_error",
                            error_detail=detail,
                        )
                    )
                    if repair_index < self._repair_attempts:
                        attempt_messages = [
                            *base_messages,
                            self._repair_message(completion.text, detail),
                        ]
                        continue
                    break

                # ── 스키마 검증 ────────────────────────────────────
                try:
                    validated = response_model.model_validate(payload)
                except ValidationError as exc:
                    detail = _format_validation_error(exc)
                    schema_failures.append(detail)
                    attempts.append(
                        AttemptRecord(
                            attempt_index=len(attempts),
                            structured_mode=mode,
                            is_repair=is_repair,
                            http_status=completion.status_code,
                            latency_ms=completion.latency_ms,
                            raw_text=completion.text,
                            outcome="schema_validation_error",
                            error_detail=detail,
                        )
                    )
                    if repair_index < self._repair_attempts:
                        attempt_messages = [
                            *base_messages,
                            self._repair_message(completion.text, detail),
                        ]
                        continue
                    break

                attempts.append(
                    AttemptRecord(
                        attempt_index=len(attempts),
                        structured_mode=mode,
                        is_repair=is_repair,
                        http_status=completion.status_code,
                        latency_ms=completion.latency_ms,
                        raw_text=completion.text,
                        outcome="validated",
                    )
                )
                succeeded = validated
                success_mode = mode
                break

            if succeeded is not None:
                break
            if mode_failed_by_provider:
                continue

        total_latency_ms = int((time.perf_counter() - started) * 1000)
        final_mode = success_mode or (attempts[-1].structured_mode if attempts else None)
        # 강등이 실제로 일어났는지. 사다리 첫 칸이 아니면 강등된 것이다.
        fallback_used = bool(final_mode) and final_mode != self._ladder[0]

        record = LLMTraceRecord(
            conversation_id=conversation_id,
            turn=turn,
            node=node,
            provider=self._transport.provider_name,
            requested_model=self._settings.requested_model,
            reported_model=last_completion.reported_model if last_completion else None,
            prompt_version=prompt_version,
            schema_name=response_model.__name__,
            schema_version=schema_version_of(response_model),
            temperature=temperature,
            max_tokens=self._settings.max_tokens,
            structured_mode=final_mode,
            fallback_used=fallback_used,
            validation_success=succeeded is not None,
            retry_count=repair_count,
            transport_retry_count=transport_retry_count,
            raw_response=last_completion.text if last_completion else None,
            validated_output=succeeded.model_dump(mode="json") if succeeded else None,
            validation_errors=schema_failures,
            latency_ms=total_latency_ms,
            input_tokens=last_completion.input_tokens if last_completion else None,
            output_tokens=last_completion.output_tokens if last_completion else None,
            attempts=attempts,
            messages=[message.to_payload() for message in messages],
        )
        self._trace.write(record)

        if succeeded is None:
            raise StructuredOutputError(
                f"{response_model.__name__} 검증에 실패했습니다. "
                f"시도 {len(attempts)}회, 사유: {schema_failures or '응답 없음'}",
                attempts=list(attempts),
            )

        if success_mode is not None:
            self._preferred_mode = success_mode
        return succeeded

    # ------------------------------------------------------------------ #
    # generate_text
    # ------------------------------------------------------------------ #

    async def generate_text(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        node: str = "unknown",
        prompt_version: str = "unversioned",
        conversation_id: str | None = None,
        turn: int | None = None,
    ) -> str:
        started = time.perf_counter()
        completion = await self._transport.complete(
            messages=messages, temperature=temperature
        )
        total_latency_ms = int((time.perf_counter() - started) * 1000)

        self._trace.write(
            LLMTraceRecord(
                conversation_id=conversation_id,
                turn=turn,
                node=node,
                provider=self._transport.provider_name,
                requested_model=self._settings.requested_model,
                reported_model=completion.reported_model,
                prompt_version=prompt_version,
                temperature=temperature,
                max_tokens=self._settings.max_tokens,
                validation_success=True,
                raw_response=completion.text,
                latency_ms=total_latency_ms,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                attempts=[
                    AttemptRecord(
                        attempt_index=0,
                        structured_mode="prompt_only",
                        http_status=completion.status_code,
                        latency_ms=completion.latency_ms,
                        raw_text=completion.text,
                        outcome="validated",
                    )
                ],
                messages=[message.to_payload() for message in messages],
            )
        )
        return completion.text

    async def aclose(self) -> None:
        await self._transport.aclose()


def _format_validation_error(exc: ValidationError) -> str:
    """Pydantic 오류를 모델에게 되돌려 줄 만큼 구체적인 문장으로 만든다."""
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", ())) or "(root)"
        parts.append(f"{location}: {error.get('msg', 'invalid')}")
    return "; ".join(parts)
