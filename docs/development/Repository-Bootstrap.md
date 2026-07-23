# Repository and Tooling Bootstrap

Milestone 5 Phase 2 creates executable scaffolding only. It implements the repository shape and
toolchain frozen in [Runtime Architecture v1.0](../runtime-architecture/Runtime-Architecture-v1.0.md)
without workflows, provider integrations, production schemas, or deployment automation.

## Prerequisites

- Git
- CPython 3.13.7, as recorded in `.python-version`
- `uv` 0.8.14, as recorded in `.tool-versions`

No globally installed project dependencies are required.

## Fresh-clone path

```text
git clone https://github.com/mayurbhavsar04/AIEOS.git
cd AIEOS
./scripts/bootstrap
cp .env.example .env
./scripts/check
```

The example environment file contains only safe local placeholders and a secret reference. Never
place a production credential in the repository.

## Common commands

| Task | Command |
| --- | --- |
| Deterministic dependency sync | `uv sync --all-packages --all-groups --locked` |
| Toolchain/configuration diagnosis | `make doctor` |
| Run all validation | `make check` |
| Format | `make format` |
| Lint and format check | `make lint` |
| Strict type checking | `make type` |
| Unit/bootstrap tests | `make test` |
| Integration tests | `make test-integration` |
| Dependency boundaries | `make boundaries` |
| Documentation links/Mermaid | `make docs` |
| Start the health-only host | `make run` |
| Reset repository-owned local state | `./scripts/reset-local --confirm` |

The reset command is deliberately scoped to repository-owned generated state. It does not remove the
virtual environment, dependency cache, source files, databases, or containers.

## Configuration

The host reads an immutable typed configuration snapshot. `TenantId` and `WorkspaceId` placeholders
are required at the shape level. `SecretReference` is a reference, not a secret value. Startup fails
on invalid or empty values and never prints values during diagnostics.

## Package boundaries

Packages use the `aieos_<area>` distribution naming direction and `aieos.<area>` import namespaces.
Public exports live in package `__init__.py` and `ports.py`; `_internal` modules are private.
`tooling/dependency_rules/check_boundaries.py` enforces the frozen import and authority rules.

## Troubleshooting

- A version error means `.python-version`, `.tool-versions`, and the local executables differ.
- A lock error requires using the pinned `uv`; do not hand-edit `uv.lock`.
- A boundary failure names the forbidden import; use the owning component's public port.
- A configuration failure should be fixed with local placeholders or secret references, never a
  committed credential.
- A link or Mermaid error names the source document and target/fence to correct.
