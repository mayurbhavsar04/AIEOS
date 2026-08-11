"""Governed tiny paid-provider checks; excluded unless explicitly selected."""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import pytest
from test_openai_adapter import make_request

from aieos.adapters.ai_provider_openai import (
    OPENAI_MODEL_CATALOG,
    OpenAIProviderAdapter,
    OpenAIProviderConfig,
)
from aieos.ai_gateway import AIUsage, ResponseMode

pytestmark = pytest.mark.live_provider


def _live_adapter() -> OpenAIProviderAdapter:
    if os.getenv("AIEOS_RUN_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("live provider tests require explicit governed opt-in")
    return OpenAIProviderAdapter(OpenAIProviderConfig.from_environment())


def _record_usage(label: str, usage: AIUsage) -> None:
    mapping = OPENAI_MODEL_CATALOG[0]
    cost = mapping.catalog.estimate_cost(usage.input_tokens, usage.output_tokens)
    evidence = (
        f"{label}: input_tokens={usage.input_tokens}, output_tokens={usage.output_tokens}, "
        f"cost_usd={cost.quantize(Decimal('0.00000001'))}"
    )
    print(evidence)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write(f"- {evidence}\n")


@pytest.mark.anyio
async def test_live_tiny_text_and_usage() -> None:
    adapter = _live_adapter()
    try:
        result = await adapter.invoke(
            model_key="economy-text-v1",
            prompt="Reply only: OK",
            request=make_request(max_output_tokens=8),
        )
        assert result.content.strip()
        assert result.usage is not None
        assert result.usage.input_tokens > 0 and result.usage.output_tokens > 0
        _record_usage("text", result.usage)
    finally:
        await adapter.close()


@pytest.mark.anyio
async def test_live_tiny_structured_output() -> None:
    adapter = _live_adapter()
    try:
        result = await adapter.invoke(
            model_key="economy-text-v1",
            prompt="Set answer to OK and model to reference.",
            request=make_request(
                response_mode=ResponseMode.STRUCTURED,
                output_schema_ref="answer-v1",
                max_output_tokens=24,
            ),
        )
        assert '"answer"' in result.content
        assert result.usage is not None
        _record_usage("structured", result.usage)
    finally:
        await adapter.close()


@pytest.mark.anyio
async def test_live_tiny_stream_and_usage() -> None:
    adapter = _live_adapter()
    try:
        events = [
            event
            async for event in adapter.stream(
                model_key="economy-text-v1",
                prompt="Reply only: OK",
                request=make_request(max_output_tokens=8),
            )
        ]
        assert any(event.kind == "content_delta" for event in events)
        usage = next(
            event.usage
            for event in reversed(events)
            if event.kind == "usage" and event.usage is not None
        )
        _record_usage("stream", usage)
    finally:
        await adapter.close()
