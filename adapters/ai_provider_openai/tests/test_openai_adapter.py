from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from aieos.adapters.ai_provider_openai import (
    OPENAI_MODEL_CATALOG,
    OpenAIProviderAdapter,
    OpenAIProviderConfig,
)
from aieos.adapters.observability_default import InMemoryObservationRecorder
from aieos.ai_gateway import (
    AIInvocationRequest,
    ProviderAdapter,
    ProviderFailure,
    ProviderResult,
    ReferenceAIGateway,
    ReferenceGatewayStore,
    ResponseMode,
)
from aieos.contracts import AuthorizationContext
from aieos.domain import SystemClock, UuidIdentifierFactory
from aieos.security_support import ScopeAuthorizer


def make_request(**changes: object) -> AIInvocationRequest:
    values: dict[str, object] = {
        "execution_id": "execution-1",
        "capability_contract_version_id": "text-generation-v1",
        "prompt": "Reply OK",
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "correlation_id": "correlation-1",
        "causation_id": "causation-1",
        "authorization": AuthorizationContext(
            actor_id="actor-1",
            permissions=frozenset({"ai.invoke"}),
            tenant_id="tenant-1",
            workspace_id="workspace-1",
            policy_id="policy-1",
            policy_version_id="policy-v1",
        ),
        "command_id": "command-1",
        "idempotency_key": "idem-1",
        "max_output_tokens": 8,
        "deadline": datetime(2026, 8, 11, tzinfo=UTC),
    }
    values.update(changes)
    return AIInvocationRequest(**values)  # type: ignore[arg-type]


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://example.invalid/v1", transport=httpx.MockTransport(handler)
    )


@pytest.mark.anyio
async def test_maps_request_response_and_detailed_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "output_text": "OK",
                "usage": {
                    "input_tokens": 2,
                    "output_tokens": 1,
                    "input_tokens_details": {"cached_tokens": 1},
                    "output_tokens_details": {"reasoning_tokens": 3},
                },
            },
        )

    client = _client(handler)
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    result = await adapter.invoke(
        model_key="economy-text-v1", prompt="Reply OK", request=make_request()
    )
    assert seen == {
        "model": "gpt-5-mini-2025-08-07",
        "input": "Reply OK",
        "max_output_tokens": 8,
        "store": False,
    }
    assert result.content == "OK"
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (2, 1)
    assert (result.usage.cached_tokens, result.usage.reasoning_tokens) == (1, 3)
    await client.aclose()


@pytest.mark.anyio
async def test_maps_structured_output_without_replacing_gateway_validation() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"output_text": '{"answer":"OK","model":"x"}'})

    client = _client(handler)
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    await adapter.invoke(
        model_key="economy-text-v1",
        prompt="Reply OK",
        request=make_request(response_mode=ResponseMode.STRUCTURED, output_schema_ref="answer-v1"),
    )
    assert seen["text"] == {
        "format": {
            "type": "json_schema",
            "name": "answer_v1",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "model": {"type": "string"},
                },
                "required": ["answer", "model"],
                "additionalProperties": False,
            },
        }
    }
    await client.aclose()


@pytest.mark.anyio
async def test_streams_incremental_neutral_events_and_terminal_usage() -> None:
    data = "\n".join(
        (
            'data: {"type":"response.output_text.delta","delta":"O"}',
            'data: {"type":"response.output_text.delta","delta":"K"}',
            "data: "
            '{"type":"response.completed","response":{"usage":'
            '{"input_tokens":2,"output_tokens":1}}}',
            "data: [DONE]",
            "",
        )
    )
    client = _client(lambda _request: httpx.Response(200, text=data))
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    events = [
        event
        async for event in adapter.stream(
            model_key="economy-text-v1", prompt="Reply OK", request=make_request()
        )
    ]
    assert [event.kind for event in events] == ["content_delta", "content_delta", "usage"]
    assert "".join(event.content or "" for event in events) == "OK"
    assert events[-1].usage is not None and events[-1].usage.output_tokens == 1
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "native_code", "expected", "retryable"),
    [
        (401, "invalid_api_key", "AI_PROVIDER_AUTHENTICATION_FAILED", False),
        (403, "policy", "AI_PROVIDER_PERMISSION_DENIED", False),
        (400, "context_length_exceeded", "AI_CONTEXT_LIMIT_EXCEEDED", False),
        (429, "rate_limit_exceeded", "AI_PROVIDER_RATE_LIMITED", True),
        (503, "overloaded", "AI_PROVIDER_OVERLOADED", True),
    ],
)
async def test_normalizes_provider_errors(
    status: int, native_code: str, expected: str, retryable: bool
) -> None:
    client = _client(lambda _request: httpx.Response(status, json={"error": {"code": native_code}}))
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        await adapter.invoke(model_key="economy-text-v1", prompt="Reply OK", request=make_request())
    assert (raised.value.code, raised.value.retryable) == (expected, retryable)
    await client.aclose()


def test_live_configuration_is_explicit_and_secret_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AIEOS_AI_PROVIDER", raising=False)
    with pytest.raises(ValueError, match="OpenAI live mode requires"):
        OpenAIProviderConfig.from_environment()
    monkeypatch.setenv("AIEOS_AI_PROVIDER", "openai")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIProviderConfig.from_environment()
    config = OpenAIProviderConfig("do-not-log")
    assert "do-not-log" not in repr(config.safe_summary())


def test_catalog_keeps_provider_identity_internal_and_prices_auditable() -> None:
    mapping = OPENAI_MODEL_CATALOG[0]
    assert mapping.model_key == "economy-text-v1"
    assert mapping.catalog.model_key == "economy-text-v1"
    assert mapping.provider_model.startswith("gpt-")
    assert mapping.catalog.input_cost_per_token == Decimal("0.00000025")
    assert mapping.catalog.pricing_version == "openai-2026-08-11"


@pytest.mark.anyio
async def test_real_adapter_conforms_through_frozen_gateway_budget_and_accounting() -> None:
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={
                "output_text": "OK",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    provider_port: ProviderAdapter = adapter
    identifiers = UuidIdentifierFactory()
    gateway = ReferenceAIGateway(
        clock=SystemClock(),
        identifiers=identifiers,
        authorizer=ScopeAuthorizer(),
        observations=InMemoryObservationRecorder(identifiers),
        store=ReferenceGatewayStore(),
        catalog=(OPENAI_MODEL_CATALOG[0].catalog,),
        adapters={adapter.key: provider_port},
    )
    response = await gateway.invoke(make_request(deadline=None, max_total_cost=Decimal("0.01")))
    assert response.content == "OK"
    assert response.usage is not None and response.usage.output_tokens == 1
    assert response.route is not None and response.route.model_key == "economy-text-v1"
    await client.aclose()


class _Boundary:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        *,
        request: AIInvocationRequest,
        effect_key: str,
        effect_type: str,
        request_hash: str,
        operation: Callable[[], Awaitable[ProviderResult]],
    ) -> ProviderResult:
        del request, effect_key, effect_type, request_hash
        self.calls += 1
        return await operation()


@pytest.mark.anyio
async def test_opaque_effect_key_uses_frozen_process_independent_boundary() -> None:
    provider_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal provider_calls
        provider_calls += 1
        return httpx.Response(200, json={"output_text": "OK"})

    boundary = _Boundary()
    client = _client(handler)
    adapter = OpenAIProviderAdapter(
        OpenAIProviderConfig("secret"), client=client, effect_boundary=boundary
    )
    await adapter.invoke(
        model_key="economy-text-v1",
        prompt="Reply OK",
        request=make_request(),
        effect_key="opaque-effect-1",
    )
    assert boundary.calls == provider_calls == 1
    await client.aclose()


@pytest.mark.anyio
async def test_cancellation_remains_cancellation() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200, json={"output_text": "late"})

    client = httpx.AsyncClient(
        base_url="https://example.invalid/v1", transport=httpx.MockTransport(handler)
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    task = asyncio.create_task(
        adapter.invoke(model_key="economy-text-v1", prompt="x", request=make_request())
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.aclose()
