"""OpenAI adapter exports; provider details terminate in this package."""

from aieos.adapters.ai_provider_openai.adapter import OpenAIProviderAdapter
from aieos.adapters.ai_provider_openai.catalog import OPENAI_MODEL_CATALOG
from aieos.adapters.ai_provider_openai.config import OpenAIProviderConfig

__all__ = ("OPENAI_MODEL_CATALOG", "OpenAIProviderAdapter", "OpenAIProviderConfig")
