"""Auditable internal-to-provider model mapping and replaceable price metadata."""

from dataclasses import dataclass
from decimal import Decimal

from aieos.ai_gateway import ModelCatalogEntry


@dataclass(frozen=True, slots=True)
class OpenAIModelMapping:
    model_key: str
    provider_model: str
    catalog: ModelCatalogEntry
    pricing_reference: str
    data_handling: str
    version_policy: str
    minimum_output_tokens: int
    reasoning_effort: str


OPENAI_MODEL_CATALOG = (
    OpenAIModelMapping(
        model_key="economy-text-v1",
        provider_model="gpt-5-mini-2025-08-07",
        catalog=ModelCatalogEntry(
            model_key="economy-text-v1",
            adapter_key="openai-responses",
            capabilities=frozenset({"text", "structured", "stream"}),
            context_limit=400_000,
            max_output=128_000,
            quality_tier=1,
            latency_tier=1,
            input_cost_per_token=Decimal("0.00000025"),
            output_cost_per_token=Decimal("0.000002"),
            pricing_version="openai-2026-08-11",
            residencies=frozenset({"any"}),
            data_handling=frozenset({"internal", "zdr-eligible"}),
            security_tier=2,
        ),
        pricing_reference="https://developers.openai.com/api/docs/models/gpt-5-mini",
        data_handling="API data controls apply; ZDR eligibility is feature/account dependent",
        version_policy="Pinned snapshot; review before deprecation or price/capability change",
        minimum_output_tokens=16,
        reasoning_effort="minimal",
    ),
)

MODEL_BY_KEY = {entry.model_key: entry for entry in OPENAI_MODEL_CATALOG}
