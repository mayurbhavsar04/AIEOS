"""Explicit opt-in configuration with credential isolation."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenAIProviderConfig:
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> OpenAIProviderConfig:
        if os.getenv("AIEOS_AI_PROVIDER", "mock") != "openai":
            raise ValueError("OpenAI live mode requires AIEOS_AI_PROVIDER=openai")
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for explicit OpenAI live mode")
        return cls(api_key=api_key)

    def safe_summary(self) -> dict[str, object]:
        return {
            "provider": "openai",
            "credential_configured": bool(self.api_key),
            "timeout_seconds": self.timeout_seconds,
        }
