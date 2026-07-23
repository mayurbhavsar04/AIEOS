from __future__ import annotations

from pathlib import Path

import pytest
from tooling.dependency_rules import check_boundaries
from tooling.docs_validation import validate_docs

from aieos_api.settings import HostSettings


def _package(root: Path, name: str, source: str, dependencies: tuple[str, ...] = ()) -> None:
    package = root / "packages" / name
    module = package / "src" / "aieos" / name
    module.mkdir(parents=True)
    (module / "__init__.py").write_text(source, encoding="utf-8")
    dependency_lines = ", ".join(f'"aieos-{item.replace("_", "-")}"' for item in dependencies)
    (package / "pyproject.toml").write_text(
        f'[project]\nname = "aieos-{name.replace("_", "-")}"\n'
        f'version = "0.0.0"\ndependencies = [{dependency_lines}]\n',
        encoding="utf-8",
    )


def test_dependency_gate_rejects_manager_skill_runtime_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _package(tmp_path, "manager", "from aieos.skill_runtime import ExecutionAttemptRunner\n")
    _package(tmp_path, "skill_runtime", "")
    monkeypatch.setattr(check_boundaries, "ROOT", tmp_path)
    assert any("manager may not import" in item for item in check_boundaries.check())


def test_dependency_gate_rejects_cycles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _package(tmp_path, "alpha", "", ("beta",))
    _package(tmp_path, "beta", "", ("alpha",))
    monkeypatch.setattr(check_boundaries, "ROOT", tmp_path)
    assert any("dependency cycle" in item for item in check_boundaries.check())


def test_docs_validation_ignores_local_dependency_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text("# Root\n", encoding="utf-8")
    (docs / "Owned.md").write_text("# Owned\n", encoding="utf-8")
    cache = tmp_path / ".local" / "uv-cache"
    cache.mkdir(parents=True)
    (cache / "ThirdParty.md").write_text("[broken](missing.md)\n", encoding="utf-8")
    monkeypatch.setattr(validate_docs, "ROOT", tmp_path)
    monkeypatch.setattr(validate_docs, "DOCUMENTATION_ROOTS", (tmp_path / "README.md", docs))
    assert validate_docs.main() == 0


def test_frozen_envelopes_are_not_reduced_concrete_copies() -> None:
    from aieos.contracts import commands, events

    assert not hasattr(commands, "CommandEnvelope")
    assert not hasattr(events, "EventEnvelope")


def test_dotenv_is_local_source_and_environment_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "AIEOS_TENANT_ID=dotenv-tenant\n"  # pragma: allowlist secret
        "AIEOS_WORKSPACE_ID=dotenv-workspace\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AIEOS_TENANT_ID", "environment-tenant")
    settings = HostSettings()
    assert settings.tenant_id == "environment-tenant"
    assert settings.workspace_id == "dotenv-workspace"


def test_security_checks_are_in_canonical_command() -> None:
    check_script = (Path(__file__).parents[2] / "scripts" / "check").read_text(encoding="utf-8")
    assert "bandit" in check_script
    assert "detect-secrets-hook" in check_script
