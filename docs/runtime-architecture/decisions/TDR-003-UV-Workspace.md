# TDR-003 — uv Workspace and Package Management

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

The monorepo requires fast reproducible dependency resolution, Python/toolchain pinning, workspaces, locked CI, and conventional package artifacts.

## Options

uv; Poetry; PDM; pip-tools with virtual environments.

## Decision and rationale

Use a pinned `uv` release, root `pyproject.toml`, workspace packages, and committed `uv.lock`. uv combines runtime acquisition, locking, environments, scripts, and workspace support with low bootstrap overhead.

## Consequences and rejected alternatives

The team depends on a newer tool and must pin/verify it. Poetry adds project-specific conventions and slower resolution; PDM is viable but offers less benefit here; pip-tools requires more custom workspace orchestration.

## Compatibility and revisit

Build tooling only. Revisit if workspace semantics, supply-chain controls, platform support, or maintenance materially fail requirements.
