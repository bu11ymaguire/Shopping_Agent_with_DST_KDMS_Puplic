"""Luxia Cloud 브리지의 실제 기능을 소량의 요청으로 확인한다.

실행::

    python scripts/probe_capabilities.py

결과는 기본적으로 ``reports/luxia_capability.json``에 저장한다. API 키는
``backend/.env``의 ``LLM_API_KEY`` 또는 ``LUXIA_API_KEY``에서 읽으며 요청·결과
어디에도 기록하지 않는다.

200 응답만으로 기능 지원을 판정하지 않는다. 특히 strict JSON Schema는 정상
요청과 스키마-프롬프트 충돌 대조군을 함께 보내 실제 강제 여부를 확인한다.
Rate limit은 서비스를 의도적으로 압박해 만들지 않고, 다른 probe 중 429가
자연스럽게 관측된 경우에만 응답 형태를 기록한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import LLMSettings, load_llm_settings  # noqa: E402
from app.llm import (  # noqa: E402
    LLMHTTPError,
    LLMResponseShapeError,
    LLMTimeoutError,
    LuxiaTransport,
    system,
    user,
    write_report,
)
from app.llm.base import extract_reported_model, extract_usage  # noqa: E402
from app.llm.json_utils import JsonExtractionError, extract_json_object  # noqa: E402


STRICT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "luxia_strict_probe",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["schema_enforced"],
                }
            },
            "required": ["verdict"],
            "additionalProperties": False,
        },
    },
}

JSON_OBJECT_FORMAT: dict[str, str] = {"type": "json_object"}

PROBE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "record_capability",
        "description": "Record that function calling is available.",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["tool_call_supported"],
                }
            },
            "required": ["status"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class ProbeSpec:
    name: str
    purpose: str
    messages: list[Any]
    response_format: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None
    max_tokens: int = 128


def build_probe_specs() -> list[ProbeSpec]:
    """서로 한 기능만 바꾸는 probe 목록을 만든다."""
    return [
        ProbeSpec(
            name="baseline",
            purpose="인증, 기본 요청 형식, OpenAI 호환 응답 구조 확인",
            messages=[
                system("Follow the user's instruction exactly."),
                user("Reply with exactly BASELINE_OK and nothing else."),
            ],
            max_tokens=32,
        ),
        ProbeSpec(
            name="json_schema_normal",
            purpose="strict json_schema 파라미터 수용 및 정상 준수 확인",
            messages=[
                system("Return only the structured result requested by the API."),
                user("Set verdict to schema_enforced."),
            ],
            response_format=STRICT_SCHEMA,
        ),
        ProbeSpec(
            name="json_schema_conflict_control",
            purpose=(
                "프롬프트와 enum을 충돌시켜 strict 스키마가 실제로 강제되는지 확인"
            ),
            messages=[
                system("Follow the user's literal text instruction above all else."),
                user(
                    "Do not return JSON. Reply with exactly NOT_SCHEMA_COMPLIANT. "
                    "Do not use the value schema_enforced."
                ),
            ],
            response_format=STRICT_SCHEMA,
        ),
        ProbeSpec(
            name="json_object",
            purpose="json_object 모드 수용 및 JSON object 반환 확인",
            messages=[
                system("Return a JSON object only."),
                user('Return {"format":"json_object_supported"}.'),
            ],
            response_format=JSON_OBJECT_FORMAT,
        ),
        ProbeSpec(
            name="function_calling",
            purpose="tools와 강제 tool_choice가 실제 tool call을 만드는지 확인",
            messages=[
                system("Call the supplied function. Do not answer in plain text."),
                user("Record that function calling is supported."),
            ],
            extra_body={
                "tools": [PROBE_TOOL],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "record_capability"},
                },
            },
        ),
        ProbeSpec(
            name="stream",
            purpose="stream=true 수용 및 SSE 응답 여부 확인",
            messages=[
                system("Be concise."),
                user("Reply with STREAM_OK."),
            ],
            extra_body={"stream": True},
            max_tokens=32,
        ),
    ]


def _request_payload(transport: LuxiaTransport, spec: ProbeSpec) -> dict[str, Any]:
    """키가 들어 있지 않은 요청 본문을 보고용으로 만든다."""
    return transport.build_payload(
        messages=spec.messages,
        temperature=0.0,
        max_tokens=spec.max_tokens,
        response_format=spec.response_format,
        extra_body=spec.extra_body,
    )


def _json_result(text: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if text is None:
        return None, "응답 텍스트가 없습니다."
    try:
        return extract_json_object(text), None
    except JsonExtractionError as exc:
        return None, str(exc)


def _tool_call_detected(raw: dict[str, Any] | None) -> bool:
    if not isinstance(raw, dict):
        return False
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    if not isinstance(first, dict):
        return False
    message = first.get("message")
    if not isinstance(message, dict):
        return False

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            if isinstance(function, dict) and function.get("name") == "record_capability":
                return True

    # 구형 OpenAI 호환 형태도 관측한다.
    function_call = message.get("function_call")
    return isinstance(function_call, dict) and function_call.get("name") == "record_capability"


def _decorate_observation(observation: dict[str, Any]) -> dict[str, Any]:
    """원본 관측값에서 기능별 판정 신호를 계산한다."""
    name = observation["name"]
    text = observation.get("response_text")
    parsed, parse_error = _json_result(text)
    raw = observation.get("raw_body")

    observation["json_output"] = parsed
    observation["json_parse_error"] = parse_error
    observation["valid_json_object"] = parsed is not None
    observation["strict_schema_compliant"] = (
        parsed == {"verdict": "schema_enforced"}
        if name.startswith("json_schema")
        else None
    )
    observation["json_object_compliant"] = (
        parsed == {"format": "json_object_supported"}
        if name == "json_object"
        else None
    )
    observation["tool_call_detected"] = (
        _tool_call_detected(raw) if name == "function_calling" else None
    )

    raw_text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    observation["sse_detected"] = (
        ("data:" in raw_text or "[DONE]" in raw_text) if name == "stream" else None
    )
    return observation


async def run_probe(
    transport: LuxiaTransport,
    spec: ProbeSpec,
) -> dict[str, Any]:
    """한 probe가 실패해도 나머지 probe는 계속 실행한다."""
    observation: dict[str, Any] = {
        "name": spec.name,
        "purpose": spec.purpose,
        "request_body": _request_payload(transport, spec),
        "accepted": False,
        "status_code": None,
        "latency_ms": None,
        "response_text": None,
        "raw_body": None,
        "usage_present": False,
        "reported_model": None,
        "error_type": None,
        "error_detail": None,
    }
    try:
        completion = await transport.complete(
            messages=spec.messages,
            temperature=0.0,
            max_tokens=spec.max_tokens,
            response_format=spec.response_format,
            extra_body=spec.extra_body,
            retries=0,
        )
        observation.update(
            accepted=True,
            status_code=completion.status_code,
            latency_ms=completion.latency_ms,
            response_text=completion.text,
            raw_body=completion.raw,
            usage_present=completion.has_usage,
            reported_model=completion.reported_model,
        )
    except LLMResponseShapeError as exc:
        # 강제 function call은 content가 null이고 tool_calls만 있어 정상이어도
        # 일반 텍스트 추출 단계에서 이 예외가 날 수 있다. raw를 보존해 판정한다.
        input_tokens, output_tokens = extract_usage(exc.raw)
        observation.update(
            accepted=True,
            status_code=200,
            raw_body=exc.raw,
            usage_present=input_tokens is not None or output_tokens is not None,
            reported_model=extract_reported_model(exc.raw),
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
    except LLMHTTPError as exc:
        # stream=true가 수용되면 transport의 JSON 파서가 SSE를 보고 2xx 예외를
        # 만들 수 있다. HTTP 수용 여부와 응답 해석 실패를 분리해서 기록한다.
        observation.update(
            accepted=200 <= exc.status_code < 300,
            status_code=exc.status_code,
            raw_body=exc.body,
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
    except LLMTimeoutError as exc:
        observation.update(
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
    except Exception as exc:  # probe는 모든 미지 응답을 보고서로 남겨야 한다.
        observation.update(
            error_type=type(exc).__name__,
            error_detail=str(exc),
        )
    return _decorate_observation(observation)


async def run_timeout_probe(settings: LLMSettings) -> dict[str, Any]:
    """재시도 없는 극단적으로 짧은 timeout의 예외 형태를 확인한다."""
    timeout_settings = replace(settings, timeout_seconds=0.001, max_retries=0)
    transport = LuxiaTransport(timeout_settings)
    spec = ProbeSpec(
        name="timeout_shape",
        purpose="클라이언트 timeout 시 예외와 보고 형태 확인",
        messages=[system("Be concise."), user("Reply with TIMEOUT_PROBE_OK.")],
        max_tokens=16,
    )
    try:
        result = await run_probe(transport, spec)
        result["configured_timeout_seconds"] = timeout_settings.timeout_seconds
        return result
    finally:
        await transport.aclose()


def classify(probes: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {probe["name"]: probe for probe in probes}
    baseline = by_name["baseline"]
    normal = by_name["json_schema_normal"]
    conflict = by_name["json_schema_conflict_control"]
    json_object = by_name["json_object"]
    tool = by_name["function_calling"]
    stream = by_name["stream"]

    json_schema_enforced = bool(
        normal["accepted"]
        and normal["strict_schema_compliant"]
        and conflict["accepted"]
        and conflict["strict_schema_compliant"]
    )
    json_object_supported = bool(
        json_object["accepted"] and json_object["json_object_compliant"]
    )

    required_probe_names = {
        "baseline",
        "json_schema_normal",
        "json_schema_conflict_control",
        "json_object",
        "function_calling",
        "stream",
    }
    required_probes_reached_server = all(
        probe.get("status_code") not in (None, 0)
        for probe in probes
        if probe["name"] in required_probe_names
    )
    probe_run_valid = bool(baseline["accepted"] and required_probes_reached_server)

    if not probe_run_valid:
        structured_case = "indeterminate"
        unsupported_modes = []
    elif json_schema_enforced:
        structured_case = "A"
        unsupported_modes: list[str] = []
    elif json_object_supported:
        structured_case = "B"
        unsupported_modes = ["json_schema"]
    else:
        structured_case = "C"
        unsupported_modes = ["json_schema", "json_object"]

    rate_limit_observations = [
        {
            "probe": probe["name"],
            "status_code": probe["status_code"],
            "raw_body": probe["raw_body"],
        }
        for probe in probes
        if probe.get("status_code") == 429
    ]

    return {
        "probe_run_valid": probe_run_valid,
        "baseline_reachable": bool(baseline["accepted"]),
        "structured_output_case": structured_case,
        "json_schema_enforced": json_schema_enforced if probe_run_valid else None,
        "json_object_supported": json_object_supported if probe_run_valid else None,
        "function_calling_supported": (
            bool(tool["tool_call_detected"]) if probe_run_valid else None
        ),
        "stream_supported": (
            bool(stream["accepted"] and stream["sse_detected"])
            if probe_run_valid
            else None
        ),
        "usage_observed": any(probe["usage_present"] for probe in probes),
        "reported_models": sorted(
            {
                probe["reported_model"]
                for probe in probes
                if probe.get("reported_model")
            }
        ),
        "unsupported_modes_for_client": unsupported_modes,
        "rate_limit": {
            "forced": False,
            "reason": "서비스를 압박해 429를 인위적으로 만들지 않음",
            "observations": rate_limit_observations,
        },
    }


async def execute(settings: LLMSettings, output_path: Path) -> dict[str, Any]:
    if settings.provider != "luxia":
        raise RuntimeError("capability probe는 LLM_PROVIDER=luxia에서만 실행할 수 있습니다.")
    settings.require_api_key()

    transport = LuxiaTransport(settings)
    try:
        probes: list[dict[str, Any]] = []
        for spec in build_probe_specs():
            probes.append(await run_probe(transport, spec))
    finally:
        await transport.aclose()

    probes.append(await run_timeout_probe(settings))
    report = {
        "schema_version": "luxia-capability-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": settings.provider,
        "endpoint": settings.completions_url,
        "requested_model": settings.requested_model,
        "model_field": settings.model_field,
        "api_key_recorded": False,
        "probe_count": len(probes),
        "classification": classify(probes),
        "probes": probes,
    }
    write_report(output_path, report)
    return report


def dry_run(settings: LLMSettings) -> dict[str, Any]:
    transport = LuxiaTransport(settings)
    try:
        return {
            "endpoint": settings.completions_url,
            "api_key_recorded": False,
            "requests": [
                {
                    "name": spec.name,
                    "request_body": _request_payload(transport, spec),
                }
                for spec in build_probe_specs()
            ],
        }
    finally:
        asyncio.run(transport.aclose())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=BACKEND_ROOT / "reports" / "luxia_capability.json",
        help="JSON 보고서 경로",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API를 호출하지 않고 키가 제외된 요청 본문만 출력",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_llm_settings()
    if args.dry_run:
        print(json.dumps(dry_run(settings), ensure_ascii=False, indent=2))
        return

    report = asyncio.run(execute(settings, args.output))
    classification = report["classification"]
    print(f"보고서 저장: {args.output.resolve()}")
    print(
        "구조화 출력 판정: "
        f"case {classification['structured_output_case']} "
        f"(json_schema_enforced={classification['json_schema_enforced']}, "
        f"json_object_supported={classification['json_object_supported']})"
    )
    print(
        "부가 기능: "
        f"tools={classification['function_calling_supported']}, "
        f"stream={classification['stream_supported']}, "
        f"usage={classification['usage_observed']}"
    )
    if not classification["probe_run_valid"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
