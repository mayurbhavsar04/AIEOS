"""Generate the mechanical workspace package skeleton defined by Runtime Architecture v1.0."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PACKAGES = (
    "domain",
    "contracts",
    "manager",
    "authentication",
    "workspace",
    "workflow_engine",
    "skill_registry",
    "skill_runtime",
    "ai_gateway",
    "memory_service",
    "capability_registry",
    "scheduler",
    "analytics",
    "notification",
    "logging",
    "configuration",
    "command_dispatcher",
    "event_bus",
    "result_error_support",
    "observability",
    "security_support",
    "testing",
)

ADAPTERS = (
    "persistence_postgres",
    "ai_mock",
    "event_bus_in_process",
    "command_dispatch_in_process",
    "memory_persistence",
    "observability_default",
    "secrets_environment",
)

PYPROJECT = """[project]
name = "aieos-{distribution}"
version = "0.0.0"
description = "{description}"
requires-python = "==3.13.*"
dependencies = []

[build-system]
requires = ["hatchling==1.27.0"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{import_path}"]

[tool.hatch.build.targets.wheel.sources]
"src" = ""
"""


def write_if_missing(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def create_package(base: str, name: str, import_name: str | None = None) -> None:
    module = import_name or name
    root = ROOT / base / name
    namespace = Path("aieos", *module.split(".")) if base != "apps" else Path(module)
    distribution = f"{base[:-1]}-{name}" if base == "adapters" else name
    write_if_missing(
        root / "pyproject.toml",
        PYPROJECT.format(
            distribution=distribution.replace("_", "-"),
            description=f"AIEOS {name.replace('_', ' ')} bootstrap package.",
            import_path=namespace.as_posix(),
        ),
    )
    package_dir = root / "src" / namespace
    if base != "apps":
        write_if_missing(
            root / "src" / "aieos" / "__init__.py",
            '"""AIEOS shared namespace package."""\n\n'
            "from pkgutil import extend_path\n\n"
            "__path__ = extend_path(__path__, __name__)\n",
        )
    if base == "adapters":
        write_if_missing(
            root / "src" / "aieos" / "adapters" / "__init__.py",
            '"""AIEOS adapter namespace package."""\n\n'
            "from pkgutil import extend_path\n\n"
            "__path__ = extend_path(__path__, __name__)\n",
        )
    write_if_missing(
        package_dir / "__init__.py",
        f'"""Public bootstrap surface for AIEOS {name.replace("_", " ")}."""\n\n'
        "__all__: tuple[str, ...] = ()\n",
    )
    write_if_missing(
        package_dir / "_internal" / "__init__.py", '"""Private implementation namespace."""\n'
    )
    write_if_missing(root / "tests" / ".gitkeep", "")


for package in PACKAGES:
    create_package("packages", package)

for adapter in ADAPTERS:
    create_package("adapters", adapter, f"adapters.{adapter}")

for path in (
    "examples/hello_world_employee",
    "tests/integration",
    "tests/e2e",
    "tests/architecture",
    "tests/compatibility",
    "tests/concurrency",
    "tests/security",
    "tooling/contract_codegen",
):
    write_if_missing(ROOT / path / ".gitkeep", "")
