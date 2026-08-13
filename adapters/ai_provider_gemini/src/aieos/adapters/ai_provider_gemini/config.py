"""Explicit opt-in Gemini configuration with isolated credentials."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeminiProviderConfig:
    api_key: str
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> GeminiProviderConfig:
        if os.getenv("AIEOS_AI_PROVIDER", "mock") != "gemini":
            raise ValueError("Gemini live mode requires AIEOS_AI_PROVIDER=gemini")
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for explicit Gemini live mode")
        return cls(api_key=api_key)

    def safe_summary(self) -> dict[str, object]:
        return {
            "provider": "gemini",
            "credential_configured": bool(self.api_key),
            "timeout_seconds": self.timeout_seconds,
        }
