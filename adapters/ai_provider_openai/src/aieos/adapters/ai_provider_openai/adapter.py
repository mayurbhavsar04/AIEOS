"""OpenAI Responses API mapping behind the frozen provider-neutral port."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from typing import cast

import httpx

from aieos.adapters.ai_provider_openai.catalog import MODEL_BY_KEY
from aieos.adapters.ai_provider_openai.config import OpenAIProviderConfig
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


class OpenAIProviderAdapter:
    key = "openai-responses"

    def __init__(
        self,
        config: OpenAIProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        effect_boundary: ProviderEffectBoundary | None = None,
    ) -> None:
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={"Authorization": f"Bearer {config.api_key}"},
        )
        self._owns_client = client is None
        self._effect_boundary = effect_boundary
        self._last_http_diagnostic: dict[str, object] | None = None

    def use_effect_boundary(self, boundary: ProviderEffectBoundary) -> None:
        self._effect_boundary = boundary

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def safe_http_diagnostic(self) -> Mapping[str, object] | None:
        """Return allow-listed provider error metadata for governed validation only."""
        return dict(self._last_http_diagnostic) if self._last_http_diagnostic is not None else None

    async def invoke(
        self,
        *,
        model_key: str,
        prompt: str,
        request: AIInvocationRequest,
        effect_key: str | None = None,
    ) -> ProviderResult:
        if effect_key is not None and self._effect_boundary is not None:
            digest = hashlib.sha256(
                json.dumps(self._payload(model_key, prompt, request), sort_keys=True).encode()
            ).hexdigest()
            return await self._effect_boundary.execute(
                request=request,
                effect_key=effect_key,
                effect_type="structured_repair" if ":repair:" in effect_key else "provider_invoke",
                request_hash=digest,
                operation=lambda: self._invoke_once(model_key, prompt, request),
            )
        return await self._invoke_once(model_key, prompt, request)

    async def _invoke_once(
        self, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> ProviderResult:
        try:
            response = await self._client.post(
                "/responses", json=self._payload(model_key, prompt, request)
            )
            response.raise_for_status()
        except httpx.TimeoutException as error:
            raise ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=True) from error
        except httpx.HTTPStatusError as error:
            raise self._http_failure(error.response) from error
        except httpx.RequestError as error:
            raise ProviderFailure("AI_PROVIDER_TEMPORARILY_UNAVAILABLE", retryable=True) from error
        try:
            body_value = response.json()
        except ValueError as error:
            raise ProviderFailure("AI_PROVIDER_MALFORMED_RESPONSE", retryable=False) from error
        if not isinstance(body_value, dict):
            raise ProviderFailure("AI_PROVIDER_MALFORMED_RESPONSE", retryable=False)
        body = cast(dict[str, object], body_value)
        failure = self._terminal_failure(body)
        if failure is not None:
            raise failure
        content = self._output_text(body)
        if not content:
            raise ProviderFailure("AI_PROVIDER_MALFORMED_RESPONSE", retryable=False)
        return ProviderResult(content=content, usage=self._usage(body.get("usage")))

    async def stream(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> AsyncIterator[ProviderStreamEvent]:
        payload = self._payload(model_key, prompt, request)
        payload["stream"] = True
        terminal_seen = False
        latest_usage: AIUsage | None = None
        try:
            async with self._client.stream("POST", "/responses", json=payload) as response:
                if not response.is_success:
                    await response.aread()
                    raise self._http_failure(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    if line == "data: [DONE]":
                        if not terminal_seen:
                            raise ProviderFailure(
                                "AI_PROVIDER_INCOMPLETE_RESPONSE",
                                retryable=False,
                                usage=latest_usage,
                            )
                        continue
                    if terminal_seen:
                        raise ProviderFailure(
                            "AI_PROVIDER_MALFORMED_RESPONSE",
                            retryable=False,
                            usage=latest_usage,
                        )
                    event_value = json.loads(line[6:])
                    if not isinstance(event_value, dict):
                        raise ProviderFailure(
                            "AI_PROVIDER_MALFORMED_RESPONSE",
                            retryable=False,
                            usage=latest_usage,
                        )
                    event = cast(dict[str, object], event_value)
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            yield ProviderStreamEvent("content_delta", content=delta)
                    elif event_type == "response.completed":
                        response_value = event.get("response")
                        if not isinstance(response_value, dict):
                            raise ProviderFailure(
                                "AI_PROVIDER_MALFORMED_RESPONSE",
                                retryable=False,
                                usage=latest_usage,
                            )
                        response_body = cast(dict[str, object], response_value)
                        usage = self._usage(response_body.get("usage"))
                        latest_usage = self._latest_usage(latest_usage, usage)
                        failure = self._terminal_failure(response_body)
                        if failure is not None:
                            raise ProviderFailure(
                                failure.code,
                                retryable=failure.retryable,
                                usage=latest_usage,
                            )
                        terminal_seen = True
                        if latest_usage is not None:
                            yield ProviderStreamEvent("usage", usage=latest_usage)
                    elif event_type in {
                        "response.failed",
                        "response.incomplete",
                        "response.cancelled",
                        "response.expired",
                    }:
                        response_value = event.get("response")
                        response_body = (
                            cast(dict[str, object], response_value)
                            if isinstance(response_value, dict)
                            else {}
                        )
                        usage = self._usage(response_body.get("usage"))
                        latest_usage = self._latest_usage(latest_usage, usage)
                        failure = self._status_failure(
                            response_body.get("status")
                            or str(event_type).removeprefix("response."),
                            latest_usage,
                        )
                        raise failure
                if not terminal_seen:
                    raise ProviderFailure(
                        "AI_PROVIDER_INCOMPLETE_RESPONSE",
                        retryable=False,
                        usage=latest_usage,
                    )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=True) from error
        except (httpx.RequestError, json.JSONDecodeError) as error:
            raise ProviderFailure(
                "AI_PROVIDER_STREAM_FAILED", retryable=True, usage=latest_usage
            ) from error

    @staticmethod
    def _provider_model(model_key: str) -> str:
        try:
            return MODEL_BY_KEY[model_key].provider_model
        except KeyError as error:
            raise ProviderFailure("AI_MODEL_NOT_AVAILABLE", retryable=False) from error

    def _payload(
        self, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> dict[str, object]:
        mapping = MODEL_BY_KEY.get(model_key)
        if mapping is None:
            raise ProviderFailure("AI_MODEL_NOT_AVAILABLE", retryable=False)
        payload: dict[str, object] = {
            "model": mapping.provider_model,
            "input": prompt,
            "max_output_tokens": max(request.max_output_tokens, mapping.minimum_output_tokens),
            "reasoning": {"effort": mapping.reasoning_effort},
            "store": False,
        }
        if request.response_mode is ResponseMode.STRUCTURED:
            schema_ref = request.output_schema_ref or ""
            schema = _SCHEMAS.get(schema_ref)
            if schema is None:
                raise ProviderFailure("AI_SCHEMA_NOT_SUPPORTED", retryable=False)
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": schema_ref.replace("-", "_"),
                    "strict": True,
                    "schema": schema,
                }
            }
        return payload

    @staticmethod
    def _output_text(body: Mapping[str, object]) -> str:
        direct = body.get("output_text")
        if isinstance(direct, str):
            return direct
        parts: list[str] = []
        output = body.get("output")
        if isinstance(output, list):
            for item_value in cast(list[object], output):
                if not isinstance(item_value, dict):
                    continue
                item = cast(dict[str, object], item_value)
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part_value in cast(list[object], content):
                    if not isinstance(part_value, dict):
                        continue
                    part = cast(dict[str, object], part_value)
                    if part.get("type") == "output_text":
                        text = part.get("text")
                        if isinstance(text, str):
                            parts.append(text)
        return "".join(parts)

    @staticmethod
    def _usage(value: object) -> AIUsage | None:
        if not isinstance(value, dict):
            return None
        usage = cast(dict[str, object], value)
        input_value = usage.get("input_tokens_details")
        output_value = usage.get("output_tokens_details")
        input_details = (
            cast(dict[str, object], input_value) if isinstance(input_value, dict) else {}
        )
        output_details = (
            cast(dict[str, object], output_value) if isinstance(output_value, dict) else {}
        )
        return AIUsage(
            input_tokens=OpenAIProviderAdapter._count(usage.get("input_tokens")),
            output_tokens=OpenAIProviderAdapter._count(usage.get("output_tokens")),
            cached_tokens=OpenAIProviderAdapter._count(input_details.get("cached_tokens")),
            reasoning_tokens=OpenAIProviderAdapter._count(output_details.get("reasoning_tokens")),
        )

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

    @classmethod
    def _terminal_failure(cls, body: Mapping[str, object]) -> ProviderFailure | None:
        status = body.get("status")
        if status == "completed":
            return None
        return cls._status_failure(status, cls._usage(body.get("usage")))

    @staticmethod
    def _status_failure(status: object, usage: AIUsage | None) -> ProviderFailure:
        codes = {
            "incomplete": "AI_PROVIDER_INCOMPLETE_RESPONSE",
            "in_progress": "AI_PROVIDER_INCOMPLETE_RESPONSE",
            "queued": "AI_PROVIDER_INCOMPLETE_RESPONSE",
            "failed": "AI_PROVIDER_FAILED_RESPONSE",
            "cancelled": "AI_PROVIDER_CANCELLED",
            "expired": "AI_PROVIDER_EXPIRED_RESPONSE",
        }
        if not isinstance(status, str) or status not in codes:
            return ProviderFailure("AI_PROVIDER_MALFORMED_RESPONSE", retryable=False, usage=usage)
        return ProviderFailure(codes[status], retryable=False, usage=usage)

    @staticmethod
    def _count(value: object) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    def _http_failure(self, response: httpx.Response) -> ProviderFailure:
        status = response.status_code
        code = ""
        error_type = ""
        param = ""
        message = ""
        try:
            body = cast(dict[str, object], response.json())
            error = body.get("error", {})
            if isinstance(error, dict):
                error_body = cast(dict[str, object], error)
                code = str(error_body.get("code") or error_body.get("type") or "")
                error_type = str(error_body.get("type") or "")
                param = str(error_body.get("param") or "")
                if param == "max_output_tokens":
                    message = str(error_body.get("message") or "")[:240]
        except (ValueError, httpx.ResponseNotRead):
            pass
        self._last_http_diagnostic = {
            "status": status,
            "type": error_type,
            "code": code,
            "param": param,
            "message": message,
            "request_id": response.headers.get("x-request-id", ""),
            "retry_after": response.headers.get("retry-after", ""),
        }
        if status == 401:
            return ProviderFailure("AI_PROVIDER_AUTHENTICATION_FAILED", retryable=False)
        if status == 403:
            return ProviderFailure("AI_PROVIDER_PERMISSION_DENIED", retryable=False)
        if status == 429:
            quota_codes = {
                "insufficient_quota",
                "billing_hard_limit_reached",
                "credit_balance_exhausted",
            }
            is_quota = code in quota_codes or error_type == "insufficient_quota"
            return ProviderFailure("AI_PROVIDER_RATE_LIMITED", retryable=not is_quota)
        if status == 408:
            return ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=True)
        if status in {500, 502, 504}:
            return ProviderFailure("AI_PROVIDER_TEMPORARILY_UNAVAILABLE", retryable=True)
        if status == 503:
            return ProviderFailure("AI_PROVIDER_OVERLOADED", retryable=True)
        if status == 400 and any(token in code for token in ("context", "token", "length")):
            return ProviderFailure("AI_CONTEXT_LIMIT_EXCEEDED", retryable=False)
        if status in {400, 404, 409, 422}:
            return ProviderFailure("AI_PROVIDER_REJECTED", retryable=False)
        return ProviderFailure("AI_PROVIDER_UNKNOWN_FAILURE", retryable=status >= 500)
