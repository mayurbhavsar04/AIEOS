"""Bootstrap host tests."""

from fastapi.testclient import TestClient

from aieos_api.composition import FROZEN_RUNTIME_MODULES, compose
from aieos_api.main import app
from aieos_api.settings import HostSettings


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
