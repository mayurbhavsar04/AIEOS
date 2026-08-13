"""Governed safe cross-provider proof: injected failure occurs before any OpenAI dispatch."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from aieos.adapters.ai_mock import DeterministicMockProvider, MockProviderBehavior
from aieos.adapters.ai_provider_gemini import (
    GEMINI_MODEL_CATALOG,
    GeminiProviderAdapter,
    GeminiProviderConfig,
)
from aieos.adapters.observability_default import InMemoryObservationRecorder
from aieos.ai_gateway import AIInvocationRequest, ModelCatalogEntry, ReferenceAIGateway
from aieos.contracts import AuthorizationContext, ResultStatus
from aieos.security_support import ScopeAuthorizer
from aieos.testing import DeterministicClock, DeterministicIdentifiers

pytestmark = pytest.mark.live_provider


@pytest.mark.anyio
async def test_injected_predispatch_openai_failure_then_live_gemini() -> None:
    if os.getenv("AIEOS_RUN_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("live provider tests require explicit governed opt-in")
    identifiers = DeterministicIdentifiers()
    injected_openai = DeterministicMockProvider(
        "openai-responses",
        prefix="OpenAI",
        behaviors=(MockProviderBehavior.TRANSIENT_FAILURE,),
    )
    gemini = GeminiProviderAdapter(GeminiProviderConfig.from_environment())
    gemini_catalog = GEMINI_MODEL_CATALOG[0].catalog
    runtime = ReferenceAIGateway(
        clock=DeterministicClock(datetime(2026, 8, 12, tzinfo=UTC)),
        identifiers=identifiers,
        authorizer=ScopeAuthorizer(),
        observations=InMemoryObservationRecorder(identifiers),
        catalog=(
            ModelCatalogEntry(
                "injected-openai-v1",
                "openai-responses",
                frozenset({"text"}),
                1024,
                32,
                1,
                1,
                Decimal("0.00000001"),
                Decimal("0.00000001"),
                "injected-non-billable-v1",
            ),
            gemini_catalog,
        ),
        adapters={"openai-responses": injected_openai, gemini.key: gemini},
    )
    request = AIInvocationRequest(
        execution_id="live-execution",
        capability_contract_version_id="text-v1",
        prompt="Reply only: OK",
        tenant_id="live-tenant",
        workspace_id="live-workspace",
        correlation_id="live-correlation",
        causation_id="live-causation",
        authorization=AuthorizationContext(
            "live-validator",
            frozenset({"ai.invoke"}),
            "live-tenant",
            "live-workspace",
            "governed-live-policy",
            "v1",
        ),
        command_id="live-command",
        idempotency_key="live-cross-provider-v1",
        max_input_tokens=128,
        max_output_tokens=32,
        max_total_cost=Decimal("0.001"),
        max_provider_attempts=2,
        cache_allowed=False,
    )
    try:
        result = await runtime.invoke(request)
        assert result.result.result_status is ResultStatus.SUCCEEDED
        assert result.content is not None and result.content.strip()
        attempts = runtime.store.attempts[result.ai_invocation_id]
        assert [attempt[1] for attempt in attempts] == [
            "injected-openai-v1",
            "economy-text-gemini-v1",
        ]
        assert result.route is not None and result.route.adapter_key == gemini.key
        assert result.usage is not None
        evidence = (
            f"ai_invocation_id={result.ai_invocation_id}; sequence=OpenAI(injected-before-dispatch)"
            f"->Gemini(live); input_tokens={result.usage.input_tokens}; "
            f"output_tokens={result.usage.output_tokens}; terminal_status=succeeded"
        )
        print(evidence)
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as stream:
                stream.write(f"- {evidence}\n")
    finally:
        await gemini.close()
