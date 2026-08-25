"""Luxia Cloud 브리지 transport.

Luxia는 OpenAI GPT-4o-mini를 브리지 형태로 제공한다. OpenAI와 다른 점이 둘 있고,
그 때문에 openai 파이썬 패키지를 그대로 쓸 수 없다.

    1. 인증이 ``Authorization: Bearer``가 아니라 ``apikey`` 헤더다.
    2. 실제 모델은 request body의 ``model``이 아니라 URL 경로가 결정한다.
       body의 ``model``에는 ``"llm"`` 고정값이 들어간다.

그래서 httpx로 직접 호출한다. 참조: Using_Luxia_API-main/README.md
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from app.config import LLMSettings
from app.llm.base import (
    ChatMessage,
    LLMHTTPError,
    LLMTimeoutError,
    RawCompletion,
    extract_reported_model,
    extract_text,
    extract_usage,
)

#: 재시도할 상태 코드. 429는 rate limit, 5xx는 서버 측 일시 오류다.
#: 4xx(429 제외)는 요청 자체가 잘못된 것이므로 재시도하지 않는다. 그래야
#: capability probe가 "이 파라미터는 거부된다"를 즉시 확인할 수 있다.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

MAX_BACKOFF_SECONDS = 60.0


class LuxiaTransport:
    """Luxia Cloud 브리지에 chat completion 요청 한 번을 보낸다."""

    provider_name = "luxia"

    def __init__(
        self,
        settings: LLMSettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.timeout_seconds)
        )

    # ------------------------------------------------------------------ #
    # 요청 조립
    # ------------------------------------------------------------------ #

    def build_payload(
        self,
        *,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """request body를 만든다. probe가 payload만 따로 확인할 수 있게 공개한다."""
        settings = self._settings
        payload: dict[str, Any] = {
            # Luxia에서는 고정값. 실제 모델은 URL 경로가 정한다.
            "model": settings.model_field,
            "temperature": settings.temperature if temperature is None else temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_tokens if max_tokens is None else max_tokens,
            "messages": [message.to_payload() for message in messages],
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if extra_body:
            payload.update(extra_body)
        return payload

    def _headers(self) -> dict[str, str]:
        return {
            # Bearer가 아니라 apikey 헤더다.
            "apikey": self._settings.require_api_key(),
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ #
    # 호출
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
        """요청을 보내고 원본 응답을 돌려준다.

        429/5xx는 exponential backoff로 재시도한다. 그 밖의 오류 상태는
        즉시 ``LLMHTTPError``로 올린다. probe가 body를 읽어야 하므로
        예외에 응답 본문을 함께 담는다.

        ``retries=0``을 주면 재시도 없이 첫 응답을 그대로 판단할 수 있다.
        capability probe가 이 경로를 쓴다.
        """
        settings = self._settings
        max_retries = settings.max_retries if retries is None else retries
        payload = self.build_payload(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            extra_body=extra_body,
        )
        headers = self._headers()
        url = settings.completions_url

        delay = settings.initial_backoff_seconds
        attempted = 0
        last_error: LLMHTTPError | LLMTimeoutError | None = None

        while attempted <= max_retries:
            started = time.perf_counter()
            try:
                response = await self._client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = LLMTimeoutError(
                    f"{settings.timeout_seconds}초 안에 응답이 오지 않았습니다: {exc}"
                )
                if attempted >= max_retries:
                    raise last_error from exc
                attempted += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_BACKOFF_SECONDS)
                continue
            except httpx.HTTPError as exc:
                # 연결 실패 등은 재시도해도 형태가 같으므로 바로 올린다.
                raise LLMHTTPError(0, str(exc), url=url) from exc

            latency_ms = int((time.perf_counter() - started) * 1000)

            if response.status_code in RETRYABLE_STATUS and attempted < max_retries:
                attempted += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_BACKOFF_SECONDS)
                continue

            if response.status_code >= 400:
                raise LLMHTTPError(response.status_code, response.text, url=url)

            try:
                raw = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise LLMHTTPError(
                    response.status_code,
                    f"응답이 JSON이 아닙니다: {response.text[:500]}",
                    url=url,
                ) from exc

            if not isinstance(raw, dict):
                raise LLMHTTPError(
                    response.status_code,
                    f"응답 최상위가 object가 아닙니다: {type(raw).__name__}",
                    url=url,
                )

            input_tokens, output_tokens = extract_usage(raw)
            return RawCompletion(
                text=extract_text(raw),
                raw=raw,
                status_code=response.status_code,
                latency_ms=latency_ms,
                reported_model=extract_reported_model(raw),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_body=payload,
                transport_retries=attempted,
            )

        # 재시도를 모두 소진했다.
        if last_error is not None:
            raise last_error
        raise LLMHTTPError(
            0, f"{max_retries}회 재시도 후에도 응답을 받지 못했습니다.", url=url
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
