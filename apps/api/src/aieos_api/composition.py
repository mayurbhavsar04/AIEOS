"""Explicit composition root for bootstrap-only runtime module registrations."""

from dataclasses import dataclass

from aieos_api.settings import HostSettings

FROZEN_RUNTIME_MODULES = (
    "Authentication",
    "Workspace",
    "Manager",
    "Workflow Engine",
    "Skill Registry",
    "Skill Runtime",
    "AI Gateway",
    "Memory Service",
    "Capability Registry",
    "Scheduler",
    "Analytics",
    "Notification",
    "Logging",
    "Configuration",
    "Command Dispatcher",
    "Event Bus",
)


@dataclass(frozen=True, slots=True)
class CompositionRoot:
    """Validated module registry; behavior arrives in later milestones."""

    settings: HostSettings
    modules: tuple[str, ...]

    def health(self) -> dict[str, object]:
        """Return minimal startup readiness without disclosing configuration values."""
        return {"status": "ready", "module_count": len(self.modules)}


def compose(settings: HostSettings | None = None) -> CompositionRoot:
    """Create the explicit bootstrap composition root."""
    resolved = settings or HostSettings()
    return CompositionRoot(settings=resolved, modules=FROZEN_RUNTIME_MODULES)
