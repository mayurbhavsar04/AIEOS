"""Typed, immutable host configuration containing references rather than secrets."""

from enum import StrEnum

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeAdapter(StrEnum):
    IN_MEMORY = "memory"
    POSTGRES = "postgres"


class MigrationMode(StrEnum):
    CHECK = "check"
    UPGRADE = "upgrade"


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
    runtime_adapter: RuntimeAdapter = RuntimeAdapter.IN_MEMORY
    database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0.0)
    database_command_timeout_seconds: float = Field(default=30.0, gt=0.0)
    migration_mode: MigrationMode = MigrationMode.CHECK
    outbox_poll_interval_seconds: float = Field(default=0.25, gt=0.0)
    outbox_lease_seconds: float = Field(default=30.0, gt=0.0)
    outbox_batch_size: int = Field(default=50, ge=1, le=1000)
    delivery_backoff_seconds: float = Field(default=1.0, gt=0.0)

    @model_validator(mode="after")
    def require_durable_production(self) -> "HostSettings":
        if self.runtime_adapter is RuntimeAdapter.POSTGRES and self.database_url is None:
            raise ValueError("database_url is required for PostgreSQL")
        if self.environment.lower() == "production" and (
            self.runtime_adapter is not RuntimeAdapter.POSTGRES or self.database_url is None
        ):
            raise ValueError("production requires configured PostgreSQL")
        return self

    def safe_summary(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "runtime_adapter": self.runtime_adapter.value,
            "database_configured": self.database_url is not None,
            "migration_mode": self.migration_mode.value,
        }
