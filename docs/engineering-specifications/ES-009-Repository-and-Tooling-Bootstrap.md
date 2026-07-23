---
title: ES-009 — Repository and Tooling Bootstrap
version: 1.0
status: Approved
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 5 Phase 2
last_updated: 2026-07-23
---

# ES-009 — Repository and Tooling Bootstrap

## Objective

Create the executable monorepo and tooling foundation defined by Runtime Architecture v1.0. The
milestone SHALL make every approved package importable, provide a health-only reference host, enforce
dependency boundaries, and establish deterministic local and CI validation. It SHALL NOT implement
business workflows or external integrations.

## Related Documents

| Relationship | Document |
| --- | --- |
| **PRD** | [Product Strategy](../01-company/ProductStrategy.md) |
| **Architecture** | [Runtime Architecture v1.0](../runtime-architecture/Runtime-Architecture-v1.0.md) |
| **Repository shape** | [Repository Layout](../runtime-architecture/Repository-Layout.md) |
| **Dependency rules** | [Runtime Dependency Rules](../runtime-architecture/Dependency-Rules.md) |
| **Build strategy** | [Build, Test, and Release Strategy](../runtime-architecture/Build-Test-Release-Strategy.md) |
| **Local workflow** | [Local Development Workflow](../runtime-architecture/Local-Development-Workflow.md) |
| **TDRs** | [Runtime Technology Decisions](../runtime-architecture/decisions/README.md) |
| **ADRs** | None required; this work implements frozen runtime decisions. |
| **Future specifications** | Runtime component behavior and reference workflow specifications are pending. |
| **Related Pull Requests** | Pending creation of the Milestone 5 Phase 2 Draft PR. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-23 | CTO / Architect | Approved repository and tooling bootstrap scope. |

## Scope

The implementation SHALL include:

- a Python 3.13.7 and `uv` 0.8.14 workspace with a committed lockfile;
- approved component, support, adapter, host, test, example, tooling, and script locations;
- importable skeleton packages for every Runtime Architecture v1.0 module;
- explicit public/private namespaces and a single host composition root;
- a typed configuration snapshot using safe local placeholders and secret references;
- a health-only FastAPI host with startup and shutdown validation;
- Ruff formatting/linting, strict Pyright, pytest/AnyIO/Hypothesis foundations, and coverage settings;
- deterministic test-only clock and identifier utilities;
- automated frozen dependency and authority checks;
- Markdown-link and Mermaid-structure validation;
- pinned, least-privilege GitHub Actions validation;
- a documented fresh-clone bootstrap path and safe local reset; and
- tests for package imports, host lifecycle, configuration, Command/Event separation, and retry
  authority.

## Frozen Constraints

The implementation MUST preserve:

- canonical component names, including **Capability Registry**;
- events-only Event Bus transport;
- directed Command dispatch;
- target-owned authorization and idempotency;
- Workflow Engine retry-decision ownership;
- Skill Runtime single-attempt execution;
- inward dependency direction;
- provider SDK isolation;
- tenant/workspace context shape;
- explicit constructor composition; and
- trusted static extension loading.

Any conflict with a frozen baseline SHALL stop implementation and require the applicable governance
process.

## Non-goals

This milestone MUST NOT add:

- AI YouTube Employee behavior;
- real workflows or Skills;
- AI provider SDKs or integrations;
- production database schemas or broker integrations;
- infrastructure provisioning or deployment automation;
- production secrets;
- dashboards; or
- dynamic remote or untrusted extension loading.

## Acceptance Criteria

- A clean environment completes locked workspace synchronization.
- Every approved runtime and adapter package imports successfully.
- The reference host starts, reports minimal health, and stops cleanly.
- Configuration rejects invalid scope and stores only secret references.
- Formatting, linting, strict types, tests, boundary rules, docs validation, and whitespace checks
  pass.
- Tests prove Commands are not routed through Event Bus and Skill Runtime owns no retry decision.
- CI invokes the same repository-owned validation path.
- No frozen baseline file changes.
- No production business logic, provider integration, production schema, or deployment automation.

## Definition of Done

The feature branch is pushed and a Draft PR documents scope, package tree, tool versions, validation
evidence, frozen-baseline traceability, known local limitations, and deferred work. The PR remains
unmerged and no tag or release is created.
