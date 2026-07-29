"""Bootstrap host tests."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from aieos.adapters.event_bus_in_process import InMemoryOutboxStore, OutboxRelay
from aieos.adapters.memory_persistence import InMemoryMemoryRepository
from aieos.adapters.persistence_postgres import (
    BufferedPostgresOutbox,
    PostgresDecisionEvidenceRepository,
    PostgresExecutionRepository,
    PostgresMemoryRepository,
    PostgresOutboxStore,
    PostgresRequestRepository,
    PostgresWorkflowRepository,
)
from aieos.domain import InMemoryDecisionEvidenceRepository
from aieos.manager import InMemoryRequestRepository
from aieos.skill_runtime import InMemoryExecutionRepository
from aieos.workflow_engine import InMemoryWorkflowRepository
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


def test_postgres_mode_selects_only_durable_phase_four_adapters() -> None:
    root = compose(
        HostSettings(
            runtime_adapter=RuntimeAdapter.POSTGRES,
            database_url=SecretStr("postgresql+asyncpg://aieos:aieos@localhost:5432/aieos"),
        )
    )
    assert isinstance(root.reference_runtime.memory_repository, PostgresMemoryRepository)
    assert isinstance(root.reference_runtime.outbox_store, PostgresOutboxStore)
    assert isinstance(root.reference_runtime.outbox, BufferedPostgresOutbox)
    assert isinstance(root.reference_runtime.workflow_repository, PostgresWorkflowRepository)
    assert isinstance(root.reference_runtime.execution_repository, PostgresExecutionRepository)
    assert isinstance(root.reference_runtime.request_repository, PostgresRequestRepository)
    assert isinstance(root.reference_runtime.decisions, PostgresDecisionEvidenceRepository)
    assert len(root.reference_runtime.durable_participants) == 5
    assert (
        root.reference_runtime.durable_participants[0] is root.reference_runtime.memory_repository
    )


def test_memory_mode_selects_only_in_memory_phase_four_adapters() -> None:
    root = compose(HostSettings(runtime_adapter=RuntimeAdapter.IN_MEMORY))
    runtime = root.reference_runtime
    assert isinstance(runtime.memory_repository, InMemoryMemoryRepository)
    assert isinstance(runtime.outbox_store, InMemoryOutboxStore)
    assert isinstance(runtime.outbox, OutboxRelay)
    assert type(runtime.workflow_repository) is InMemoryWorkflowRepository
    assert type(runtime.execution_repository) is InMemoryExecutionRepository
    assert type(runtime.request_repository) is InMemoryRequestRepository
    assert type(runtime.decisions) is InMemoryDecisionEvidenceRepository
    assert runtime.durable_participants == ()
