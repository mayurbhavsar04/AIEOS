"""Bootstrap host tests."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from aieos.adapters.persistence_postgres import (
    BufferedPostgresOutbox,
    PostgresMemoryRepository,
    PostgresOutboxStore,
)
from aieos_api.composition import FROZEN_RUNTIME_MODULES, compose
from aieos_api.main import app
from aieos_api.settings import HostSettings, RuntimeAdapter


def test_composition_registers_every_frozen_runtime_module() -> None:
    root = compose(HostSettings())
    assert root.modules == FROZEN_RUNTIME_MODULES
    assert root.health() == {"status": "ready", "module_count": 16}


def test_host_starts_and_stops_cleanly() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ready", "module_count": 16}


def test_configuration_rejects_empty_scope() -> None:
    try:
        HostSettings(tenant_id="")
    except ValueError:
        return
    raise AssertionError("empty tenant scope must fail validation")


def test_postgres_mode_selects_durable_memory_and_outbox_adapters() -> None:
    root = compose(
        HostSettings(
            runtime_adapter=RuntimeAdapter.POSTGRES,
            database_url=SecretStr("postgresql+asyncpg://aieos:aieos@localhost:5432/aieos"),
        )
    )
    assert isinstance(root.reference_runtime.memory_repository, PostgresMemoryRepository)
    assert isinstance(root.reference_runtime.outbox_store, PostgresOutboxStore)
    assert isinstance(root.reference_runtime.outbox, BufferedPostgresOutbox)
