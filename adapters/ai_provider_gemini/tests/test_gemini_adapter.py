from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from aieos.adapters.ai_provider_gemini import (
    GEMINI_MODEL_CATALOG,
    GeminiProviderAdapter,
    GeminiProviderConfig,
)
from aieos.ai_gateway import AIInvocationRequest, ProviderFailure, ResponseMode
from aieos.contracts import AuthorizationContext


def make_request(**changes: object) -> AIInvocationRequest:
    values: dict[str, object] = {
        "execution_id": "execution-1",
        "capability_contract_version_id": "cap-v1",
        "prompt": "Reply only: OK",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "correlation_id": "correlation-1",
        "causation_id": "causation-1",
        "authorization": AuthorizationContext(
            actor_id="actor-1",
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            permissions=frozenset({"ai.invoke"}),
            policy_id="policy-1",
            policy_version_id="v1",
        ),
        "command_id": "command-1",
        "idempotency_key": "idempotency-1",
        "deadline": datetime(2030, 1, 1, tzinfo=UTC),
        "max_total_cost": Decimal("0.01"),
    }
    values.update(changes)
    return AIInvocationRequest(**values)  # type: ignore[arg-type]


def response_body(*, text: str = "OK", finish: str = "STOP") -> dict[str, object]:
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": finish}
        ],
        "usageMetadata": {
            "promptTokenCount": 7,
            "candidatesTokenCount": 2,
            "cachedContentTokenCount": 3,
            "thoughtsTokenCount": 1,
            "totalTokenCount": 10,
        },
    }


@pytest.mark.anyio
async def test_text_request_response_and_usage_mapping() -> None:
    captured: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, json=response_body())

    client = httpx.AsyncClient(
        base_url="https://example.test/v1beta",
        headers={"x-goog-api-key": "secret"},
        transport=httpx.MockTransport(handler),
    )
    adapter = GeminiProviderAdapter(GeminiProviderConfig("secret"), client=client)
    result = await adapter.invoke(
        model_key="economy-text-gemini-v1", prompt="Reply only: OK", request=make_request()
    )
    assert captured is not None
    assert captured.headers["x-goog-api-key"] == "secret"
    assert captured.url.path.endswith("/models/gemini-3.5-flash-lite:generateContent")
    payload = json.loads(captured.content)
    assert payload["contents"][0]["parts"][0]["text"] == "Reply only: OK"
    assert payload["generationConfig"] == {"maxOutputTokens": 128, "temperature": 0}
    assert result.content == "OK"
    assert result.usage is not None
    assert result.usage.input_tokens == 7
    assert result.usage.output_tokens == 2
    assert result.usage.cached_tokens == 3
    assert result.usage.reasoning_tokens == 1
    await client.aclose()


@pytest.mark.anyio
async def test_structured_schema_is_native_hint_but_content_remains_neutral() -> None:
    payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        payload.update(json.loads(request.content))
        return httpx.Response(200, json=response_body(text='{"answer":"OK","model":"reference"}'))

    client = httpx.AsyncClient(
        base_url="https://example.test/v1beta", transport=httpx.MockTransport(handler)
    )
    adapter = GeminiProviderAdapter(GeminiProviderConfig("secret"), client=client)
    result = await adapter.invoke(
        model_key="economy-text-gemini-v1",
        prompt="structured",
        request=make_request(response_mode=ResponseMode.STRUCTURED, output_schema_ref="answer-v1"),
    )
    config = payload["generationConfig"]
    assert isinstance(config, dict)
    assert config["responseMimeType"] == "application/json"
    assert isinstance(config["responseJsonSchema"], dict)
    assert json.loads(result.content) == {"answer": "OK", "model": "reference"}
    await client.aclose()


@pytest.mark.anyio
async def test_real_incremental_sse_requires_stop_and_maps_usage() -> None:
    stream = "".join(
        [
            f"data: {json.dumps(response_body(text='O', finish=''))}\n\n",
            f"data: {json.dumps(response_body(text='K'))}\n\n",
        ]
    )
    client = httpx.AsyncClient(
        base_url="https://example.test/v1beta",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=stream)),
    )
    adapter = GeminiProviderAdapter(GeminiProviderConfig("secret"), client=client)
    events = [
        event
        async for event in adapter.stream(
            model_key="economy-text-gemini-v1", prompt="stream", request=make_request()
        )
    ]
    assert [event.content for event in events if event.kind == "content_delta"] == ["O", "K"]
    assert events[-1].kind == "usage" and events[-1].usage is not None
    await client.aclose()


@pytest.mark.anyio
async def test_eof_without_stop_is_incomplete() -> None:
    event = f"data: {json.dumps(response_body(text='partial', finish=''))}\n\n"
    client = httpx.AsyncClient(
        base_url="https://example.test/v1beta",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=event)),
    )
    adapter = GeminiProviderAdapter(GeminiProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure, match="AI_PROVIDER_INCOMPLETE_RESPONSE"):
        _ = [
            item
            async for item in adapter.stream(
                model_key="economy-text-gemini-v1", prompt="stream", request=make_request()
            )
        ]
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "provider_status", "message", "code", "retryable"),
    [
        (401, "UNAUTHENTICATED", "bad key", "AI_PROVIDER_AUTHENTICATION_FAILED", False),
        (403, "PERMISSION_DENIED", "denied", "AI_PROVIDER_PERMISSION_DENIED", False),
        (400, "INVALID_ARGUMENT", "token context", "AI_CONTEXT_LIMIT_EXCEEDED", False),
        (429, "RESOURCE_EXHAUSTED", "rate limit", "AI_PROVIDER_RATE_LIMITED", True),
        (429, "RESOURCE_EXHAUSTED", "quota exhausted", "AI_PROVIDER_QUOTA_EXHAUSTED", True),
        (503, "UNAVAILABLE", "overloaded", "AI_PROVIDER_OVERLOADED", True),
        (500, "INTERNAL", "server", "AI_PROVIDER_TRANSIENT_FAILURE", True),
    ],
)
async def test_error_normalization(
    status: int, provider_status: str, message: str, code: str, retryable: bool
) -> None:
    client = httpx.AsyncClient(
        base_url="https://example.test/v1beta",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status, json={"error": {"status": provider_status, "message": message}}
            )
        ),
    )
    adapter = GeminiProviderAdapter(GeminiProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        await adapter.invoke(
            model_key="economy-text-gemini-v1", prompt="text", request=make_request()
        )
    assert raised.value.code == code and raised.value.retryable is retryable
    await client.aclose()


@pytest.mark.anyio
async def test_transport_uncertainty_is_ambiguity_safe() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("unknown dispatch outcome")

    client = httpx.AsyncClient(
        base_url="https://example.test/v1beta", transport=httpx.MockTransport(handler)
    )
    adapter = GeminiProviderAdapter(GeminiProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        await adapter.invoke(
            model_key="economy-text-gemini-v1", prompt="text", request=make_request()
        )
    assert raised.value.code == "AI_PROVIDER_EFFECT_AMBIGUOUS"
    assert raised.value.retryable is False
    await client.aclose()


def test_credentials_are_explicit_and_catalog_is_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("AIEOS_AI_PROVIDER", "gemini")
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        GeminiProviderConfig.from_environment()
    monkeypatch.setenv("GEMINI_API_KEY", "private")
    assert GeminiProviderConfig.from_environment().safe_summary() == {
        "provider": "gemini",
        "credential_configured": True,
        "timeout_seconds": 30.0,
    }
    mapping = GEMINI_MODEL_CATALOG[0]
    assert mapping.catalog.capabilities == frozenset({"text", "structured", "stream"})
    assert "gemini" not in AIInvocationRequest.__annotations__
    assert replace(mapping.catalog, healthy=False).healthy is False
