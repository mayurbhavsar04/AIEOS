"""Enforce bootstrap dependency boundaries without importing project code."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_PREFIXES: dict[str, tuple[str, ...]] = {
    "domain": ("aieos.contracts", "aieos.adapters", "fastapi", "sqlalchemy"),
    "contracts": ("aieos.adapters", "fastapi", "sqlalchemy"),
    "workflow_engine": ("aieos.skill_runtime._internal",),
    "skill_runtime": ("aieos.workflow_engine._internal",),
    "event_bus": ("aieos.contracts.commands",),
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
        for forbidden in FORBIDDEN_PREFIXES.get(package_name, ()):
            if any(module == forbidden or module.startswith(f"{forbidden}.") for module in imports):
                violations.append(f"{relative}: forbidden import {forbidden}")
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
    return violations


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
