"""Typed, immutable host configuration containing references rather than secrets."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HostSettings(BaseSettings):
    """Minimal validated local bootstrap settings."""

    model_config = SettingsConfigDict(
        env_prefix="AIEOS_",
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
        extra="ignore",
    )

    environment: str = Field(default="local", min_length=1)
    host_name: str = Field(default="aieos-local", min_length=1)
    tenant_id: str = Field(default="local-tenant", min_length=1)
    workspace_id: str = Field(default="local-workspace", min_length=1)
    secret_reference: str = Field(default="env://AIEOS_LOCAL_SECRET", pattern=r"^[a-z]+://.+")
    mock_ai_failures_before_success: int = Field(default=0, ge=0)
    mock_ai_delay_seconds: float = Field(default=0.0, ge=0.0)
    reference_timeout_seconds: float = Field(default=1.0, gt=0.0)
