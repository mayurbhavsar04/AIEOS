from importlib import import_module

RUNTIME_MODULES = (
    "ai_gateway",
    "analytics",
    "authentication",
    "capability_registry",
    "command_dispatcher",
    "configuration",
    "contracts",
    "domain",
    "event_bus",
    "logging",
    "manager",
    "memory_service",
    "notification",
    "observability",
    "result_error_support",
    "scheduler",
    "security_support",
    "skill_registry",
    "skill_runtime",
    "testing",
    "workflow_engine",
    "workspace",
)

ADAPTER_MODULES = (
    "ai_mock",
    "command_dispatch_in_process",
    "event_bus_in_process",
    "memory_persistence",
    "observability_default",
    "persistence_postgres",
    "secrets_environment",
)


def test_every_runtime_package_imports() -> None:
    for module in RUNTIME_MODULES:
        assert import_module(f"aieos.{module}") is not None


def test_every_adapter_package_imports() -> None:
    for module in ADAPTER_MODULES:
        assert import_module(f"aieos.adapters.{module}") is not None
