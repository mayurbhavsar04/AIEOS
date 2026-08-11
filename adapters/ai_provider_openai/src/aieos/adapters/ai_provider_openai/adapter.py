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

    def use_effect_boundary(self, boundary: ProviderEffectBoundary) -> None:
        self._effect_boundary = boundary

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

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
        body = cast(dict[str, object], response.json())
        content = self._output_text(body)
        if not content:
            raise ProviderFailure("AI_PROVIDER_MALFORMED_RESPONSE", retryable=False)
        return ProviderResult(content=content, usage=self._usage(body.get("usage")))

    async def stream(
        self, *, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> AsyncIterator[ProviderStreamEvent]:
        payload = self._payload(model_key, prompt, request)
        payload["stream"] = True
        try:
            async with self._client.stream("POST", "/responses", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    event = cast(dict[str, object], json.loads(line[6:]))
                    event_type = event.get("type")
                    if event_type == "response.output_text.delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            yield ProviderStreamEvent("content_delta", content=delta)
                    elif event_type == "response.completed":
                        response_value = event.get("response")
                        response_body = (
                            cast(dict[str, object], response_value)
                            if isinstance(response_value, dict)
                            else {}
                        )
                        usage = self._usage(response_body.get("usage"))
                        if usage is not None:
                            yield ProviderStreamEvent("usage", usage=usage)
                    elif event_type in {"response.failed", "response.incomplete"}:
                        raise ProviderFailure("AI_PROVIDER_STREAM_FAILED", retryable=False)
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderFailure("AI_PROVIDER_TIMEOUT", retryable=True) from error
        except httpx.HTTPStatusError as error:
            raise self._http_failure(error.response) from error
        except (httpx.RequestError, json.JSONDecodeError) as error:
            raise ProviderFailure("AI_PROVIDER_STREAM_FAILED", retryable=True) from error

    @staticmethod
    def _provider_model(model_key: str) -> str:
        try:
            return MODEL_BY_KEY[model_key].provider_model
        except KeyError as error:
            raise ProviderFailure("AI_MODEL_NOT_AVAILABLE", retryable=False) from error

    def _payload(
        self, model_key: str, prompt: str, request: AIInvocationRequest
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._provider_model(model_key),
            "input": prompt,
            "max_output_tokens": request.max_output_tokens,
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
    def _count(value: object) -> int:
        return value if isinstance(value, int) and value >= 0 else 0

    @staticmethod
    def _http_failure(response: httpx.Response) -> ProviderFailure:
        status = response.status_code
        code = ""
        try:
            body = cast(dict[str, object], response.json())
            error = body.get("error", {})
            if isinstance(error, dict):
                error_body = cast(dict[str, object], error)
                code = str(error_body.get("code") or error_body.get("type") or "")
        except ValueError:
            pass
        if status == 401:
            return ProviderFailure("AI_PROVIDER_AUTHENTICATION_FAILED", retryable=False)
        if status == 403:
            return ProviderFailure("AI_PROVIDER_PERMISSION_DENIED", retryable=False)
        if status == 429:
            return ProviderFailure("AI_PROVIDER_RATE_LIMITED", retryable=True)
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
