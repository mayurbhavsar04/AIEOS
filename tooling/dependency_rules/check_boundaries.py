"""Enforce bootstrap dependency boundaries without importing project code."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SUPPORT = {"configuration", "logging", "observability", "result_error_support", "security_support"}
COMPONENT_IMPORTS: dict[str, set[str]] = {
    "ai_gateway": {"domain", "contracts", *SUPPORT},
    "analytics": {"domain", "contracts", *SUPPORT},
    "authentication": {"domain", "contracts", *SUPPORT},
    "capability_registry": {"domain", "contracts", *SUPPORT},
    "configuration": {"domain", "contracts", *SUPPORT},
    "logging": {"domain", "contracts", *SUPPORT},
    "manager": {"domain", "contracts", "workflow_engine", *SUPPORT},
    "memory_service": {"domain", "contracts", *SUPPORT},
    "notification": {"domain", "contracts", *SUPPORT},
    "observability": {"domain", "contracts", *SUPPORT},
    "result_error_support": {"domain", "contracts", *SUPPORT},
    "scheduler": {"domain", "contracts", *SUPPORT},
    "security_support": {"domain", "contracts", *SUPPORT},
    "skill_registry": {"domain", "contracts", *SUPPORT},
    "workflow_engine": {"domain", "contracts", "command_dispatcher", "event_bus", *SUPPORT},
    "skill_runtime": {
        "domain",
        "contracts",
        "skill_registry",
        "capability_registry",
        "ai_gateway",
        "memory_service",
        "event_bus",
        *SUPPORT,
    },
    "event_bus": {"domain", "contracts", *SUPPORT},
    "command_dispatcher": {"domain", "contracts", *SUPPORT},
    "workspace": {"domain", "contracts", *SUPPORT},
}

PROVIDER_IMPORTS = ("openai", "anthropic", "google.genai")
PROVIDER_ADAPTER_PREFIX = ROOT / "adapters" / "ai_provider_"


def imported_modules(path: Path) -> set[str]:
    """Return absolute import names used by one Python file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def check() -> list[str]:
    """Return human-readable boundary violations."""
    violations: list[str] = []
    for path in sorted(ROOT.glob("**/*.py")):
        if any(part in {".venv", "__pycache__"} for part in path.parts):
            continue
        imports = imported_modules(path)
        relative = path.relative_to(ROOT)
        package_name = relative.parts[1] if relative.parts[0] == "packages" else ""
        for module in imports:
            if not module.startswith("aieos.") or module.startswith("aieos.adapters."):
                continue
            target = module.split(".")[1]
            if "_internal" in module and target != package_name:
                violations.append(f"{relative}: cross-package private import {module}")
            if package_name == "domain" and target != "domain":
                violations.append(f"{relative}: domain must not import {module}")
            elif package_name == "contracts" and target not in {"contracts", "domain"}:
                violations.append(f"{relative}: contracts must not import {module}")
            elif package_name in COMPONENT_IMPORTS and target not in (
                COMPONENT_IMPORTS[package_name] | {package_name}
            ):
                violations.append(f"{relative}: {package_name} may not import {module}")
        if any(
            module == provider or module.startswith(f"{provider}.")
            for module in imports
            for provider in PROVIDER_IMPORTS
        ) and not str(path).startswith(str(PROVIDER_ADAPTER_PREFIX)):
            violations.append(f"{relative}: provider SDK import outside approved provider adapter")
        if package_name != "testing" and any(
            module == "aieos.testing" or module.startswith("aieos.testing.") for module in imports
        ):
            violations.append(f"{relative}: production package depends on test-only support")
    violations.extend(check_declared_dependencies())
    violations.extend(check_cycles())
    return violations


def package_dependencies() -> dict[str, set[str]]:
    """Return declared AIEOS workspace dependencies by import package name."""
    graph: dict[str, set[str]] = {}
    for manifest in sorted((ROOT / "packages").glob("*/pyproject.toml")):
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        name = manifest.parent.name
        dependencies = data.get("project", {}).get("dependencies", [])
        graph[name] = {
            dependency.removeprefix("aieos-").replace("-", "_")
            for dependency in dependencies
            if dependency.startswith("aieos-")
        }
    return graph


def check_declared_dependencies() -> list[str]:
    """Require every cross-package import to be declared in package metadata."""
    violations: list[str] = []
    graph = package_dependencies()
    for package, declared in graph.items():
        for path in sorted((ROOT / "packages" / package / "src").rglob("*.py")):
            for module in imported_modules(path):
                if module.startswith("aieos.") and not module.startswith("aieos.adapters."):
                    target = module.split(".")[1]
                    if target not in {package, "testing"} and target not in declared:
                        violations.append(
                            f"{path.relative_to(ROOT)}: undeclared workspace dependency {target}"
                        )
    return violations


def check_cycles() -> list[str]:
    """Reject cycles in declared production package dependencies."""
    graph = package_dependencies()
    violations: list[str] = []

    def visit(node: str, path: tuple[str, ...]) -> None:
        for target in graph.get(node, set()):
            if target in path:
                cycle = " -> ".join((*path, target))
                violations.append(f"workspace dependency cycle: {cycle}")
            else:
                visit(target, (*path, target))

    for package in graph:
        visit(package, (package,))
    return sorted(set(violations))


def main() -> int:
    """Run the boundary gate."""
    violations = check()
    if violations:
        print("\n".join(violations))
        return 1
    print("dependency boundaries: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
