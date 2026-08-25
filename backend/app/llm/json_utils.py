"""LLM 텍스트에서 JSON을 꺼내고, Pydantic 스키마를 strict JSON Schema로 바꾸는 도구."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel

_FENCE_PATTERN = re.compile(
    r"```(?:json|JSON)?\s*(?P<body>.*?)\s*```",
    re.DOTALL,
)


class JsonExtractionError(ValueError):
    """텍스트에서 JSON object를 찾지 못했다."""


def extract_json_object(text: str) -> dict[str, Any]:
    """모델 출력에서 JSON object를 꺼낸다.

    json_object나 prompt_only 모드에서는 모델이 코드 펜스나 설명 문장을 덧붙이는
    일이 흔하다. 세 단계로 시도한다.

        1. 그대로 파싱
        2. `````json ... ````` 펜스 안쪽을 파싱
        3. 중괄호 균형을 맞춰 첫 object를 잘라내 파싱

    파싱은 되지만 object가 아니면(배열·숫자 등) 실패로 본다. 상위 계층이
    기대하는 것은 항상 object다.
    """
    candidates: list[str] = []

    stripped = text.strip()
    if stripped:
        candidates.append(stripped)

    for match in _FENCE_PATTERN.finditer(text):
        body = match.group("body").strip()
        if body:
            candidates.append(body)

    balanced = _first_balanced_object(text)
    if balanced:
        candidates.append(balanced)

    errors: list[str] = []
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(f"{exc.msg} (pos {exc.pos})")
            continue
        if isinstance(parsed, dict):
            return parsed
        errors.append(f"최상위가 object가 아닙니다: {type(parsed).__name__}")

    detail = "; ".join(errors) if errors else "후보 문자열이 없습니다"
    preview = text[:300] if text else "(빈 문자열)"
    raise JsonExtractionError(f"JSON object를 찾지 못했습니다. 사유: {detail}. 원문 앞부분: {preview}")


def _first_balanced_object(text: str) -> str | None:
    """중괄호 균형을 세어 첫 JSON object 후보를 잘라낸다.

    문자열 리터럴 안의 중괄호와 이스케이프를 무시하도록 상태를 추적한다.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    return None


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic 모델을 strict 모드가 받아들이는 JSON Schema로 바꾼다.

    strict 모드는 모든 object에 ``additionalProperties: false``를 요구하고
    모든 property가 ``required``에 있어야 한다. Pydantic의 기본 출력은
    기본값이 있는 필드를 required에서 빼므로 그대로 쓸 수 없다.

    ``$defs`` 참조 구조는 유지한다. 스키마를 펼치면 재귀 모델에서 무한히 커진다.
    """
    schema = model.model_json_schema()
    return _tighten(schema)


def _tighten(node: Any) -> Any:
    if isinstance(node, list):
        return [_tighten(item) for item in node]
    if not isinstance(node, dict):
        return node

    result = {key: _tighten(value) for key, value in node.items()}

    is_object = result.get("type") == "object" or "properties" in result
    if is_object:
        properties = result.get("properties")
        if isinstance(properties, dict):
            result["additionalProperties"] = False
            # strict 모드는 모든 property가 required여야 한다.
            # 선택 필드는 스키마 쪽에서 null을 허용해 표현한다.
            result["required"] = list(properties.keys())

    return result


def schema_version_of(model: type[BaseModel]) -> str | None:
    """모델이 선언한 schema_version을 읽는다.

    프롬프트 버전과 스키마 버전을 로그에 함께 남겨야, 나중에 성능 차이가
    모델 때문인지 스키마 변경 때문인지 분리할 수 있다.
    """
    value = getattr(model, "schema_version", None)
    return value if isinstance(value, str) else None
