---
title: Runtime Technology Decision Records
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# Runtime Technology Decision Records

These TDRs apply the repository's ADR discipline to implementation choices without changing frozen architecture or domain semantics. Status `Proposed` means review is required before Runtime Architecture v1.0 freezes. A superseding record preserves history.

| ID | Decision |
| --- | --- |
| [TDR-001](TDR-001-Python-Runtime.md) | CPython 3.13 primary runtime |
| [TDR-002](TDR-002-Monorepo-Modular-Monolith.md) | Monorepo and modular monolith |
| [TDR-003](TDR-003-UV-Workspace.md) | uv workspace/package management |
| [TDR-004](TDR-004-Command-Dispatch.md) | Directed in-process command dispatch |
| [TDR-005](TDR-005-Event-Bus.md) | Event Bus port and in-process adapter |
| [TDR-006](TDR-006-Persistence.md) | Port-based persistence with PostgreSQL adapter |
| [TDR-007](TDR-007-Configuration-Secrets.md) | Typed configuration and secret references |
| [TDR-008](TDR-008-Composition.md) | Explicit constructor injection/composition root |
| [TDR-009](TDR-009-Testing.md) | pytest/AnyIO/Hypothesis layered tests |
| [TDR-010](TDR-010-Contract-Validation.md) | Pydantic v2 and generated JSON Schema |
| [TDR-011](TDR-011-Observability.md) | Vendor-neutral ports with OpenTelemetry-compatible adapter |
| [TDR-012](TDR-012-CI-Release.md) | GitHub Actions, locked builds, protected releases |
| [TDR-013](TDR-013-Plugin-Loading.md) | Trusted static plugin/capability registration |
| [TDR-014](TDR-014-HTTP-Host.md) | FastAPI initial HTTP host |

Every record includes context, drivers, options, rationale, consequences, frozen-baseline compatibility, and revisit triggers. Changes to a frozen baseline require the higher governance path, not a TDR alone.

## Revisit-gate standard

Every TDR revisit clause names observable evidence that opens review, the accountable evaluator, and the boundary a migration must preserve. Evidence may be an adopted service objective missed for a sustained review window, a reproducible compatibility/security failure, an approved topology or compliance requirement, or an independently justified consumer need. Opening review does not reverse a decision automatically; the owner records measurements and a successor TDR before migration. Numeric service objectives are fixed during the implementation or operational phase that first owns the measured behavior rather than invented in this architecture draft.
