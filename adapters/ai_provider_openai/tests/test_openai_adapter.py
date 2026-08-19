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
    ProviderFailure,
    ProviderResult,
    ReferenceAIGateway,
    ReferenceGatewayStore,
    ResponseMode,
)
from aieos.contracts import AuthorizationContext, ResultStatus
from aieos.domain import SystemClock, UuidIdentifierFactory
from aieos.security_support import ScopeAuthorizer
from aieos.skill_runtime import STRUCTURED_TASK_KIND_PACKAGE


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


def _gateway(adapter: OpenAIProviderAdapter) -> ReferenceAIGateway:
    identifiers = UuidIdentifierFactory()
    return ReferenceAIGateway(
        clock=SystemClock(),
        identifiers=identifiers,
        authorizer=ScopeAuthorizer(),
        observations=InMemoryObservationRecorder(identifiers),
        store=ReferenceGatewayStore(),
        catalog=(OPENAI_MODEL_CATALOG[0].catalog,),
        adapters={adapter.key: adapter},
    )


@pytest.mark.anyio
async def test_maps_request_response_and_detailed_usage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
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
        "max_output_tokens": 16,
        "reasoning": {"effort": "minimal"},
        "store": False,
    }
    assert result.content == "OK"
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (2, 1)
    assert (result.usage.cached_tokens, result.usage.reasoning_tokens) == (1, 3)
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("incomplete", "AI_PROVIDER_INCOMPLETE_RESPONSE"),
        ("failed", "AI_PROVIDER_FAILED_RESPONSE"),
        ("cancelled", "AI_PROVIDER_CANCELLED"),
        ("expired", "AI_PROVIDER_EXPIRED_RESPONSE"),
        ("queued", "AI_PROVIDER_INCOMPLETE_RESPONSE"),
        (None, "AI_PROVIDER_MALFORMED_RESPONSE"),
        (17, "AI_PROVIDER_MALFORMED_RESPONSE"),
    ],
)
async def test_non_completed_response_never_succeeds_and_preserves_usage(
    status: object, expected: str
) -> None:
    body: dict[str, object] = {
        "output_text": "partial text is not authoritative",
        "usage": {"input_tokens": 3, "output_tokens": 2},
    }
    if status is not None:
        body["status"] = status
    client = _client(lambda _request: httpx.Response(200, json=body))
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        await adapter.invoke(model_key="economy-text-v1", prompt="x", request=make_request())
    assert raised.value.code == expected
    assert raised.value.usage is not None
    assert (raised.value.usage.input_tokens, raised.value.usage.output_tokens) == (3, 2)
    await client.aclose()


@pytest.mark.anyio
async def test_incomplete_response_exposes_only_allow_listed_terminal_diagnostic() -> None:
    excluded_marker = "raw-provider-payload-must-not-leak"
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens", "unsafe": excluded_marker},
                "output_text": excluded_marker,
                "usage": {
                    "input_tokens": 9,
                    "output_tokens": 128,
                    "total_tokens": 137,
                    "input_tokens_details": {"cached_tokens": 2},
                    "output_tokens_details": {"reasoning_tokens": 128},
                },
                "provider_private": excluded_marker,
            },
        )
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure, match="AI_PROVIDER_INCOMPLETE_RESPONSE") as raised:
        await adapter.invoke(model_key="economy-text-v1", prompt="x", request=make_request())
    assert raised.value.usage is not None
    assert raised.value.usage.reasoning_tokens == 128
    diagnostic = adapter.safe_http_diagnostic()
    assert diagnostic == {
        "terminal_status": "incomplete",
        "incomplete_reason": "max_output_tokens",
        "termination_reason": "max_output_tokens",
        "usage": {
            "input_tokens": 9,
            "output_tokens": 128,
            "cached_tokens": 2,
            "reasoning_tokens": 128,
            "total_tokens": 137,
        },
    }
    assert excluded_marker not in repr(diagnostic)
    await client.aclose()


@pytest.mark.anyio
async def test_incomplete_non_stream_terminalizes_failed_with_provider_usage() -> None:
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={
                "status": "incomplete",
                "output_text": "partial",
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    response = await _gateway(adapter).invoke(
        make_request(deadline=None, max_total_cost=Decimal("0.01"), allow_fallback=False)
    )
    assert response.result.result_status is ResultStatus.FAILED
    assert response.content is None
    assert response.error is not None
    assert response.error.error_code == "AI_PROVIDER_INCOMPLETE_RESPONSE"
    assert response.usage is not None
    assert (response.usage.input_tokens, response.usage.output_tokens) == (3, 2)
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("body", [b"not-json", b"[]", b'"completed"'])
async def test_malformed_non_stream_body_fails_closed(body: bytes) -> None:
    client = _client(
        lambda _request: httpx.Response(
            200, content=body, headers={"content-type": "application/json"}
        )
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure, match="AI_PROVIDER_MALFORMED_RESPONSE"):
        await adapter.invoke(model_key="economy-text-v1", prompt="x", request=make_request())
    await client.aclose()


@pytest.mark.anyio
async def test_maps_structured_output_without_replacing_gateway_validation() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"status": "completed", "output_text": '{"answer":"OK","model":"x"}'}
        )

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
async def test_maps_governed_task_kind_schema_to_native_payload_offline() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200, json={"status": "completed", "output_text": '{"task_kind":"Question"}'}
        )

    client = _client(handler)
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    result = await adapter.invoke(
        model_key="economy-text-v1",
        prompt="What is the status?",
        request=make_request(
            response_mode=ResponseMode.STRUCTURED,
            output_schema_ref="structured-task-kind-schema-v1",
            output_schema=STRUCTURED_TASK_KIND_PACKAGE.output_schema,
            output_schema_identity=STRUCTURED_TASK_KIND_PACKAGE.identity,
        ),
    )
    text = seen["text"]
    assert isinstance(text, dict)
    schema = text["format"]["schema"]  # type: ignore[index]
    assert schema == {  # type: ignore[index]
        "type": "object",
        "properties": {
            "task_kind": {
                "type": "string",
                "enum": ["Question", "Instruction", "Statement"],
            }
        },
        "required": ["task_kind"],
        "additionalProperties": False,
    }
    assert result.content == '{"task_kind":"Question"}'
    await client.aclose()


@pytest.mark.anyio
async def test_provider_minimum_does_not_change_caller_facing_output_limit() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"status": "completed", "output_text": "OK"})

    client = _client(handler)
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    request = make_request(max_output_tokens=1)
    result = await adapter.invoke(model_key="economy-text-v1", prompt="Reply OK", request=request)
    assert seen["max_output_tokens"] == 16
    assert result.content == "OK"
    assert request.max_output_tokens == 1
    await client.aclose()


@pytest.mark.anyio
async def test_streams_incremental_neutral_events_and_terminal_usage() -> None:
    data = "\n".join(
        (
            'data: {"type":"response.output_text.delta","delta":"O"}',
            'data: {"type":"response.output_text.delta","delta":"K"}',
            "data: "
            '{"type":"response.completed","response":{"status":"completed","usage":'
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
    "tail",
    [
        "data: [DONE]",
        "",
    ],
)
async def test_stream_end_without_completed_event_fails_closed(tail: str) -> None:
    data = "\n".join(('data: {"type":"response.output_text.delta","delta":"partial"}', tail))
    client = _client(lambda _request: httpx.Response(200, text=data))
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure, match="AI_PROVIDER_INCOMPLETE_RESPONSE"):
        _ = [
            event
            async for event in adapter.stream(
                model_key="economy-text-v1", prompt="x", request=make_request()
            )
        ]
    await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event_type", "status", "expected"),
    [
        ("response.incomplete", "incomplete", "AI_PROVIDER_INCOMPLETE_RESPONSE"),
        ("response.failed", "failed", "AI_PROVIDER_FAILED_RESPONSE"),
        ("response.cancelled", "cancelled", "AI_PROVIDER_CANCELLED"),
    ],
)
async def test_explicit_stream_failure_preserves_terminal_usage(
    event_type: str, status: str, expected: str
) -> None:
    data = "\n".join(
        (
            'data: {"type":"response.output_text.delta","delta":"partial"}',
            "data: "
            + json.dumps(
                {
                    "type": event_type,
                    "response": {
                        "status": status,
                        "usage": {"input_tokens": 4, "output_tokens": 2},
                    },
                }
            ),
            "",
        )
    )
    client = _client(lambda _request: httpx.Response(200, text=data))
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        _ = [
            event
            async for event in adapter.stream(
                model_key="economy-text-v1", prompt="x", request=make_request()
            )
        ]
    assert raised.value.code == expected
    assert raised.value.usage is not None
    assert (raised.value.usage.input_tokens, raised.value.usage.output_tokens) == (4, 2)
    await client.aclose()


@pytest.mark.anyio
async def test_stream_incomplete_preserves_safe_reason_and_detailed_usage() -> None:
    excluded_marker = "raw-stream-payload-must-not-leak"
    data = "\n".join(
        (
            "data: "
            + json.dumps(
                {
                    "type": "response.incomplete",
                    "response": {
                        "status": "incomplete",
                        "incomplete_details": {
                            "reason": "max_output_tokens",
                            "unsafe": excluded_marker,
                        },
                        "output_text": excluded_marker,
                        "usage": {
                            "input_tokens": 6,
                            "output_tokens": 128,
                            "input_tokens_details": {"cached_tokens": 1},
                            "output_tokens_details": {"reasoning_tokens": 128},
                        },
                    },
                }
            ),
            "",
        )
    )
    client = _client(lambda _request: httpx.Response(200, text=data))
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure, match="AI_PROVIDER_INCOMPLETE_RESPONSE") as raised:
        _ = [
            event
            async for event in adapter.stream(
                model_key="economy-text-v1", prompt="x", request=make_request()
            )
        ]
    assert raised.value.usage is not None
    assert (raised.value.usage.cached_tokens, raised.value.usage.reasoning_tokens) == (1, 128)
    diagnostic = adapter.safe_http_diagnostic()
    assert diagnostic is not None
    assert diagnostic["terminal_status"] == "incomplete"
    assert diagnostic["incomplete_reason"] == "max_output_tokens"
    assert excluded_marker not in repr(diagnostic)
    await client.aclose()


@pytest.mark.anyio
async def test_truncated_gateway_stream_terminalizes_once_with_partial_usage() -> None:
    data = "\n".join(
        (
            'data: {"type":"response.output_text.delta","delta":"partial"}',
            'data: {"type":"response.incomplete","response":{"status":"incomplete",'
            '"usage":{"input_tokens":4,"output_tokens":2}}}',
            "",
        )
    )
    client = _client(lambda _request: httpx.Response(200, text=data))
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    chunks = [
        chunk
        async for chunk in _gateway(adapter).stream(
            make_request(
                deadline=None,
                response_mode=ResponseMode.STREAM,
                required_capabilities=frozenset({"text", "stream"}),
                max_total_cost=Decimal("0.01"),
            )
        )
    ]
    terminals = [chunk for chunk in chunks if chunk.kind == "terminal"]
    assert len(terminals) == 1
    terminal = terminals[0]
    assert terminal.terminal is not None
    assert terminal.terminal.result.result_status is ResultStatus.FAILED
    assert terminal.terminal.error is not None
    assert terminal.terminal.error.error_code == "AI_PROVIDER_INCOMPLETE_RESPONSE"
    assert terminal.usage is not None
    assert (terminal.usage.input_tokens, terminal.usage.output_tokens) == (4, 2)
    await client.aclose()


@pytest.mark.anyio
async def test_stream_rejects_duplicate_or_post_terminal_events() -> None:
    completed = (
        'data: {"type":"response.completed","response":'
        '{"status":"completed","usage":{"input_tokens":1,"output_tokens":1}}}'
    )
    client = _client(
        lambda _request: httpx.Response(
            200,
            text="\n".join((completed, completed, "")),
        )
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure, match="AI_PROVIDER_MALFORMED_RESPONSE"):
        _ = [
            event
            async for event in adapter.stream(
                model_key="economy-text-v1", prompt="x", request=make_request()
            )
        ]
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


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": "invalid_request_error"}},
        "upstream rejected the request",
    ],
)
async def test_stream_http_error_body_is_consumed_and_normalized(body: object) -> None:
    encoded = json.dumps(body).encode() if isinstance(body, dict) else str(body).encode()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            content=encoded,
            headers={
                "content-type": "application/json" if isinstance(body, dict) else "text/plain"
            },
        )

    client = httpx.AsyncClient(
        base_url="https://example.invalid/v1", transport=httpx.MockTransport(handler)
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        _ = [
            event
            async for event in adapter.stream(
                model_key="economy-text-v1", prompt="x", request=make_request()
            )
        ]
    assert (raised.value.code, raised.value.retryable) == ("AI_PROVIDER_REJECTED", False)
    await client.aclose()


@pytest.mark.anyio
async def test_http_failure_accepts_an_already_consumed_stream_body() -> None:
    response = httpx.Response(
        400,
        json={"error": {"code": "invalid_request_error"}},
    )
    await response.aread()
    client = _client(lambda _: response)
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        await adapter.invoke(model_key="economy-text-v1", prompt="x", request=make_request())
    assert (raised.value.code, raised.value.retryable) == ("AI_PROVIDER_REJECTED", False)
    await client.aclose()


@pytest.mark.anyio
async def test_quota_429_is_not_treated_as_transient() -> None:
    client = _client(
        lambda _request: httpx.Response(
            429,
            json={
                "error": {
                    "type": "insufficient_quota",
                    "code": "credit_balance_exhausted",
                }
            },
        )
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        await adapter.invoke(model_key="economy-text-v1", prompt="x", request=make_request())
    assert (raised.value.code, raised.value.retryable) == ("AI_PROVIDER_RATE_LIMITED", False)
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
    assert mapping.catalog.capabilities == frozenset({"text", "structured", "stream"})


@pytest.mark.anyio
@pytest.mark.parametrize("unsupported", ["tools", "vision"])
async def test_catalog_makes_unimplemented_capabilities_ineligible(unsupported: str) -> None:
    client = _client(
        lambda _request: httpx.Response(
            200, json={"status": "completed", "output_text": "must not be called"}
        )
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    gateway = _gateway(adapter)
    response = await gateway.invoke(
        make_request(
            deadline=None,
            required_capabilities=frozenset({"text", unsupported}),
            max_total_cost=Decimal("0.01"),
        )
    )
    assert response.result.result_status is ResultStatus.FAILED
    assert response.route is None
    await client.aclose()


@pytest.mark.anyio
async def test_real_adapter_conforms_through_frozen_gateway_budget_and_accounting() -> None:
    client = _client(
        lambda _request: httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": "OK",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            },
        )
    )
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    gateway = _gateway(adapter)
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
        return httpx.Response(200, json={"status": "completed", "output_text": "OK"})

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


@pytest.mark.anyio
async def test_post_dispatch_read_timeout_is_ambiguity_safe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("response outcome unknown", request=request)

    client = _client(handler)
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        await adapter.invoke(model_key="economy-text-v1", prompt="x", request=make_request())
    assert raised.value.code == "AI_PROVIDER_EFFECT_AMBIGUOUS"
    assert raised.value.retryable is False
    await client.aclose()


@pytest.mark.anyio
async def test_pre_dispatch_connect_timeout_remains_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("not dispatched", request=request)

    client = _client(handler)
    adapter = OpenAIProviderAdapter(OpenAIProviderConfig("secret"), client=client)
    with pytest.raises(ProviderFailure) as raised:
        await adapter.invoke(model_key="economy-text-v1", prompt="x", request=make_request())
    assert raised.value.code == "AI_PROVIDER_TIMEOUT"
    assert raised.value.retryable is True
    await client.aclose()
