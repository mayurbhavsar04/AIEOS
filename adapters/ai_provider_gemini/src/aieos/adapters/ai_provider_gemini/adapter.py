"""Gemini GenerateContent mapping behind the frozen provider-neutral port."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from typing import cast

import httpx

from aieos.adapters.ai_provider_gemini.catalog import MODEL_BY_KEY
from aieos.adapters.ai_provider_gemini.config import GeminiProviderConfig
from aieos.ai_gateway import (
    AIInvocationRequest,
    AIUsage,
    ProviderEffectBoundary,
    ProviderFailure,
    ProviderResult,
    ProviderStreamEvent,
    ResponseMode,
)

_SCHEMAS: dict[str, dict[str, object]] = {
    "answer-v1": {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "model": {"type": "string"}},
        "required": ["answer", "model"],
        "additionalProperties": False,
    },
    "reference-answer-v1": {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "model": {"type": "string"}},
        "required": ["answer", "model"],
        "additionalProperties": False,
    },
    "analysis-v1": {
        "type": "object",
        "properties": {
            "result": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["summary", "items"],
                "additionalProperties": False,
            }
        },
        "required": ["result"],
        "additionalProperties": False,
    },
}


class GeminiProviderAdapter:
    key = "gemini-generate-content"

    def __init__(
        self,
        config: GeminiProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        effect_boundary: ProviderEffectBoundary | None = None,
    ) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={"x-goog-api-key": config.api_key},
        )
        self._owns_client = client is None
        self._effect_boundary = effect_boundary
        self._last_diagnostic: dict[str, object] | None = None

    def use_effect_boundary(self, boundary: ProviderEffectBoundary) -> None:
        self._effect_boundary = boundary

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def safe_http_diagnostic(self) -> Mapping[str, object] | None:
        return dict(self._last_diagnostic) if self._last_diagnostic is not None else None

    async def invoke(
        self,
        *,
        model_key: str,
        prompt: str,
        request: AIInvocationRequest,
        effect_key: str | None = None,
    ) -> ProviderResult:
        payload = self._payload(model_key, prompt, request)
        if effect_key is not None and self._effect_boundary is not None:
            digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            return await self._effect_boundary.execute(
                request=request,
                effect_key=effect_key,
                effect_type="structured_repair" if ":repair:" in effect_key else "provider_invoke",
                request_hash=digest,
                operation=lambda: self._invoke_once(model_key, payload),
            )
        return await self._invoke_once(model_key, payload)

    async def _invoke_once(self, model_key: str, payload: Mapping[str, object]) -> ProviderResult:
        self._last_diagnostic = None
        try:
            response = await self._client.post(self._path(model_key, stream=False), json=payload)
            response.raise_for_status()
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as error:
            raise ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=True) from error
        except httpx.TimeoutException as error:
            raise ProviderFailure("AI_PROVIDER_EFFECT_AMBIGUOUS", retryable=False) from error
        except httpx.HTTPStatusError as error:
            raise self._http_failure(error.response) from error
        except httpx.RequestError as error:
            raise ProviderFailure("AI_PROVIDER_EFFECT_AMBIGUOUS", retryable=False) from error
        body = self._response_body(response)
        return self._result(body)

    async def stream(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> AsyncIterator[ProviderStreamEvent]:
        self._last_diagnostic = None
        terminal_seen = False
        latest_usage: AIUsage | None = None
        try:
            async with self._client.stream(
                "POST",
                self._path(model_key, stream=True),
                json=self._payload(model_key, prompt, request),
            ) as response:
                if not response.is_success:
                    await response.aread()
                    raise self._http_failure(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        value = json.loads(line[6:])
                    except json.JSONDecodeError as error:
                        raise ProviderFailure(
                            "AI_PROVIDER_MALFORMED_RESPONSE",
                            retryable=False,
                            usage=latest_usage,
                        ) from error
                    if not isinstance(value, dict):
                        raise ProviderFailure(
                            "AI_PROVIDER_MALFORMED_RESPONSE",
                            retryable=False,
                            usage=latest_usage,
                        )
                    body = cast(dict[str, object], value)
                    failure = self._terminal_failure(body)
                    if failure is not None:
                        raise failure
                    usage = self._usage(body.get("usageMetadata"))
                    latest_usage = self._latest_usage(latest_usage, usage)
                    content = self._content(body)
                    if content:
                        yield ProviderStreamEvent("content_delta", content=content)
                    finish_reason = self._finish_reason(body)
                    if finish_reason == "STOP":
                        terminal_seen = True
                        if latest_usage is not None:
                            yield ProviderStreamEvent("usage", usage=latest_usage)
                    elif finish_reason:
                        raise self._finish_failure(finish_reason, latest_usage)
            if not terminal_seen:
                raise ProviderFailure(
                    "AI_PROVIDER_INCOMPLETE_RESPONSE", retryable=False, usage=latest_usage
                )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderFailure(
                "AI_PROVIDER_TIMEOUT", retryable=True, usage=latest_usage
            ) from error
        except httpx.RequestError as error:
            # A streaming dispatch may have reached the provider; another call is unsafe.
            raise ProviderFailure(
                "AI_PROVIDER_EFFECT_AMBIGUOUS", retryable=False, usage=latest_usage
            ) from error

    @staticmethod
    def _path(model_key: str, *, stream: bool) -> str:
        mapping = MODEL_BY_KEY.get(model_key)
        if mapping is None:
            raise ProviderFailure("AI_MODEL_NOT_AVAILABLE", retryable=False)
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"/models/{mapping.provider_model}:{method}"

    @staticmethod
    def _payload(model_key: str, prompt: str, request: AIInvocationRequest) -> dict[str, object]:
        if model_key not in MODEL_BY_KEY:
            raise ProviderFailure("AI_MODEL_NOT_AVAILABLE", retryable=False)
        generation: dict[str, object] = {
            "maxOutputTokens": request.max_output_tokens,
            "temperature": 0,
        }
        if request.response_mode is ResponseMode.STRUCTURED:
            schema = _SCHEMAS.get(request.output_schema_ref or "")
            if schema is None:
                raise ProviderFailure("AI_SCHEMA_NOT_SUPPORTED", retryable=False)
            generation.update(
                {"responseMimeType": "application/json", "responseJsonSchema": schema}
            )
        return {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation,
        }

    @classmethod
    def _response_body(cls, response: httpx.Response) -> dict[str, object]:
        try:
            value = response.json()
        except ValueError as error:
            raise ProviderFailure("AI_PROVIDER_MALFORMED_RESPONSE", retryable=False) from error
        if not isinstance(value, dict):
            raise ProviderFailure("AI_PROVIDER_MALFORMED_RESPONSE", retryable=False)
        return cast(dict[str, object], value)

    @classmethod
    def _result(cls, body: Mapping[str, object]) -> ProviderResult:
        failure = cls._terminal_failure(body)
        if failure is not None:
            raise failure
        finish = cls._finish_reason(body)
        usage = cls._usage(body.get("usageMetadata"))
        if finish != "STOP":
            raise cls._finish_failure(finish, usage)
        content = cls._content(body)
        if not content:
            raise ProviderFailure("AI_PROVIDER_MALFORMED_RESPONSE", retryable=False, usage=usage)
        return ProviderResult(content=content, usage=usage)

    @staticmethod
    def _content(body: Mapping[str, object]) -> str:
        candidates = body.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return ""
        candidate_value = cast(list[object], candidates)[0]
        if not isinstance(candidate_value, dict):
            return ""
        candidate = cast(dict[str, object], candidate_value)
        content = candidate.get("content")
        if not isinstance(content, dict):
            return ""
        parts = cast(dict[str, object], content).get("parts")
        if not isinstance(parts, list):
            return ""
        texts: list[str] = []
        for part_value in cast(list[object], parts):
            if isinstance(part_value, dict):
                text = cast(dict[str, object], part_value).get("text")
                if isinstance(text, str):
                    texts.append(text)
        return "".join(texts)

    @staticmethod
    def _finish_reason(body: Mapping[str, object]) -> str:
        candidates = body.get("candidates")
        if (
            not isinstance(candidates, list)
            or not candidates
            or not isinstance(candidates[0], dict)
        ):
            return ""
        candidate = cast(dict[str, object], cast(list[object], candidates)[0])
        value = candidate.get("finishReason")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _usage(value: object) -> AIUsage | None:
        if not isinstance(value, dict):
            return None
        usage = cast(dict[str, object], value)
        return AIUsage(
            input_tokens=GeminiProviderAdapter._count(usage.get("promptTokenCount")),
            output_tokens=GeminiProviderAdapter._count(usage.get("candidatesTokenCount")),
            cached_tokens=GeminiProviderAdapter._count(usage.get("cachedContentTokenCount")),
            reasoning_tokens=GeminiProviderAdapter._count(usage.get("thoughtsTokenCount")),
        )

    @staticmethod
    def _count(value: object) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    @staticmethod
    def _latest_usage(current: AIUsage | None, reported: AIUsage | None) -> AIUsage | None:
        if reported is None:
            return current
        if current is not None and (
            reported.input_tokens < current.input_tokens
            or reported.output_tokens < current.output_tokens
        ):
            raise ProviderFailure("AI_NON_MONOTONIC_USAGE", retryable=False, usage=current)
        return reported

    @staticmethod
    def _terminal_failure(body: Mapping[str, object]) -> ProviderFailure | None:
        feedback = body.get("promptFeedback")
        if isinstance(feedback, dict) and cast(dict[str, object], feedback).get("blockReason"):
            return ProviderFailure("AI_PROVIDER_PERMISSION_DENIED", retryable=False)
        return None

    @staticmethod
    def _finish_failure(reason: str, usage: AIUsage | None) -> ProviderFailure:
        mapping = {
            "MAX_TOKENS": "AI_OUTPUT_LIMIT_EXCEEDED",
            "SAFETY": "AI_PROVIDER_PERMISSION_DENIED",
            "RECITATION": "AI_PROVIDER_PERMISSION_DENIED",
            "BLOCKLIST": "AI_PROVIDER_PERMISSION_DENIED",
            "PROHIBITED_CONTENT": "AI_PROVIDER_PERMISSION_DENIED",
            "SPII": "AI_PROVIDER_PERMISSION_DENIED",
            "MALFORMED_FUNCTION_CALL": "AI_PROVIDER_MALFORMED_RESPONSE",
            "UNEXPECTED_TOOL_CALL": "AI_PROVIDER_MALFORMED_RESPONSE",
            "NO_IMAGE": "AI_PROVIDER_INCOMPLETE_RESPONSE",
        }
        return ProviderFailure(
            mapping.get(reason, "AI_PROVIDER_INCOMPLETE_RESPONSE"),
            retryable=False,
            usage=usage,
        )

    def _http_failure(self, response: httpx.Response) -> ProviderFailure:
        status = response.status_code
        provider_status = ""
        message = ""
        try:
            value = response.json()
            if isinstance(value, dict):
                body = cast(dict[str, object], value)
                error_value = body.get("error")
                if isinstance(error_value, dict):
                    error = cast(dict[str, object], error_value)
                    provider_status = str(error.get("status") or "")
                    message = str(error.get("message") or "")[:240]
        except (ValueError, httpx.ResponseNotRead):
            pass
        self._last_diagnostic = {
            "status": status,
            "provider_status": provider_status,
            "message": message,
            "request_id": response.headers.get("x-request-id", ""),
            "retry_after": response.headers.get("retry-after", ""),
        }
        if status == 401:
            return ProviderFailure("AI_PROVIDER_AUTHENTICATION_FAILED", retryable=False)
        if status == 403:
            return ProviderFailure("AI_PROVIDER_PERMISSION_DENIED", retryable=False)
        if status == 404:
            return ProviderFailure("AI_MODEL_NOT_AVAILABLE", retryable=False)
        if status == 408:
            return ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=True)
        if status == 429:
            code = (
                "AI_PROVIDER_QUOTA_EXHAUSTED"
                if provider_status == "RESOURCE_EXHAUSTED" and "quota" in message.lower()
                else "AI_PROVIDER_RATE_LIMITED"
            )
            return ProviderFailure(code, retryable=True)
        if status in {500, 502, 504}:
            return ProviderFailure("AI_PROVIDER_TRANSIENT_FAILURE", retryable=True)
        if status == 503:
            return ProviderFailure("AI_PROVIDER_OVERLOADED", retryable=True)
        if status in {400, 409, 413, 422}:
            code = (
                "AI_CONTEXT_LIMIT_EXCEEDED"
                if "token" in message.lower() or "context" in message.lower()
                else "AI_PROVIDER_INVALID_REQUEST"
            )
            return ProviderFailure(code, retryable=False)
        return ProviderFailure("AI_PROVIDER_UNKNOWN_ERROR", retryable=False)
