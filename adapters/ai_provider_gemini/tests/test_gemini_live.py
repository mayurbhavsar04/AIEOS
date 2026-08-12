"""Governed tiny Gemini checks; excluded unless explicitly selected."""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest
from test_gemini_adapter import make_request

from aieos.adapters.ai_provider_gemini import (
    GEMINI_MODEL_CATALOG,
    GeminiProviderAdapter,
    GeminiProviderConfig,
)
from aieos.ai_gateway import AIUsage, ProviderFailure, ResponseMode

pytestmark = pytest.mark.live_provider

_TEXT_OUTPUT_BUDGET = 32
_STRUCTURED_OUTPUT_BUDGET = 64


def _live_adapter() -> GeminiProviderAdapter:
    if os.getenv("AIEOS_RUN_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("live provider tests require explicit governed opt-in")
    return GeminiProviderAdapter(GeminiProviderConfig.from_environment())


def _evidence(value: str) -> None:
    print(value)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"- {value}\n")


def _usage(label: str, usage: AIUsage) -> None:
    model = GEMINI_MODEL_CATALOG[0]
    cost = model.catalog.estimate_cost(usage.input_tokens, usage.output_tokens)
    _evidence(
        f"{label}: input_tokens={usage.input_tokens}, output_tokens={usage.output_tokens}, "
        f"reasoning_tokens={usage.reasoning_tokens}, cached_tokens={usage.cached_tokens}, "
        f"cost_usd={cost.quantize(Decimal('0.00000001'))}"
    )


@pytest.mark.anyio
async def test_live_tiny_text_and_usage() -> None:
    adapter = _live_adapter()
    try:
        result = await adapter.invoke(
            model_key="economy-text-gemini-v1",
            prompt="Reply only: OK",
            request=make_request(max_output_tokens=_TEXT_OUTPUT_BUDGET),
        )
        assert result.content.strip() and result.usage is not None
        _evidence("text: terminal_status=completed")
        _usage("text", result.usage)
    except ProviderFailure:
        _evidence(f"text: safe_http_diagnostic={adapter.safe_http_diagnostic()}")
        raise
    finally:
        await adapter.close()


@pytest.mark.anyio
async def test_live_tiny_structured_output() -> None:
    adapter = _live_adapter()
    try:
        result = await adapter.invoke(
            model_key="economy-text-gemini-v1",
            prompt="Set answer to OK and model to reference.",
            request=make_request(
                response_mode=ResponseMode.STRUCTURED,
                output_schema_ref="answer-v1",
                max_output_tokens=_STRUCTURED_OUTPUT_BUDGET,
            ),
        )
        assert json.loads(result.content) == {"answer": "OK", "model": "reference"}
        assert result.usage is not None
        _evidence("structured: terminal_status=completed, schema_validated=true")
        _usage("structured", result.usage)
    finally:
        await adapter.close()


@pytest.mark.anyio
async def test_live_tiny_stream_and_usage() -> None:
    adapter = _live_adapter()
    try:
        events = [
            event
            async for event in adapter.stream(
                model_key="economy-text-gemini-v1",
                prompt="Reply only: OK",
                request=make_request(max_output_tokens=_TEXT_OUTPUT_BUDGET),
            )
        ]
        assert any(event.kind == "content_delta" for event in events)
        usage = next(
            event.usage
            for event in reversed(events)
            if event.kind == "usage" and event.usage is not None
        )
        _evidence("stream: terminal_status=completed")
        _usage("stream", usage)
    finally:
        await adapter.close()
