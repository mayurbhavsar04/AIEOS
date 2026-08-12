"""Auditable internal Gemini model mapping and replaceable price metadata."""

from dataclasses import dataclass
from decimal import Decimal

from aieos.ai_gateway import ModelCatalogEntry


@dataclass(frozen=True, slots=True)
class GeminiModelMapping:
    model_key: str
    provider_model: str
    catalog: ModelCatalogEntry
    pricing_reference: str
    data_handling: str
    version_policy: str


GEMINI_MODEL_CATALOG = (
    GeminiModelMapping(
        model_key="economy-text-gemini-v1",
        provider_model="gemini-3.5-flash-lite",
        catalog=ModelCatalogEntry(
            model_key="economy-text-gemini-v1",
            adapter_key="gemini-generate-content",
            capabilities=frozenset({"text", "structured", "stream"}),
            context_limit=1_048_576,
            max_output=65_536,
            quality_tier=1,
            latency_tier=1,
            input_cost_per_token=Decimal("0.00000030"),
            output_cost_per_token=Decimal("0.00000250"),
            pricing_version="gemini-2026-08-12",
            residencies=frozenset({"any"}),
            data_handling=frozenset({"internal", "paid-no-training", "zdr-eligible"}),
            security_tier=2,
        ),
        pricing_reference="https://ai.google.dev/gemini-api/docs/pricing",
        data_handling=(
            "Paid service content is not used to improve products; ZDR requires an approved "
            "project and excludes incompatible features"
        ),
        version_policy="GA alias; review on model update, retirement, or price/capability change",
    ),
)

MODEL_BY_KEY = {entry.model_key: entry for entry in GEMINI_MODEL_CATALOG}
