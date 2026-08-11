"""Governed tiny paid-provider checks; excluded unless explicitly selected."""

from __future__ import annotations

import os

import pytest
from test_openai_adapter import make_request

from aieos.adapters.ai_provider_openai import OpenAIProviderAdapter, OpenAIProviderConfig
from aieos.ai_gateway import ResponseMode

pytestmark = pytest.mark.live_provider


def _live_adapter() -> OpenAIProviderAdapter:
    if os.getenv("AIEOS_RUN_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("live provider tests require explicit governed opt-in")
    return OpenAIProviderAdapter(OpenAIProviderConfig.from_environment())


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
        assert any(event.kind == "usage" and event.usage is not None for event in events)
    finally:
        await adapter.close()
