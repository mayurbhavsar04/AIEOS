---
title: Architecture
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
related_documents:
  - ../01-company/README.md
  - ../02-engineering-handbook/README.md
  - EngineeringBlueprint.md
  - SystemArchitecture.md
---

# Architecture

This section defines the stable responsibilities, boundaries, interactions, and constraints of AIEOS. It provides a shared model for product decisions, detailed specifications, implementation, testing, and operation.

Architecture answers **what responsibilities exist, who owns them, how they interact, and which constraints must remain true**. It does not select programming languages, frameworks, cloud vendors, or deployment products. Those choices require separate evidence and, when material, an Architecture Decision Record (ADR).

## Navigation

| Document | Purpose |
| --- | --- |
| [Engineering Blueprint](EngineeringBlueprint.md) | Defines AIEOS, its layers, core platform components, governing principles, Version 1 boundaries, and success criteria. |
| [System Architecture](SystemArchitecture.md) | Defines actors, major systems, communication patterns, trust boundaries, non-functional expectations, and system-level diagrams. |

Return to the [repository overview](../../README.md), [Company Foundation](../01-company/README.md), or [Engineering Handbook](../02-engineering-handbook/README.md).

## Relationship to the Engineering Handbook

The [Engineering Handbook](../02-engineering-handbook/README.md) defines how contributors make decisions and produce, review, release, and operate work. This architecture applies those rules to AIEOS's system structure. In particular, the architecture preserves the handbook's separation between probabilistic AI reasoning and deterministic software control, explicit contracts, least privilege, resumability, and observability.

If an architecture proposal conflicts with the handbook, the conflict must be made explicit and resolved through review. It must not be hidden in implementation.

## Relationship to ADRs

Architecture documents describe the current approved direction. ADRs record material decisions, their context, alternatives, consequences, and status. An ADR is required to adopt or change a consequential technology choice, core boundary, communication rule, trust assumption, or frozen architecture principle.

ADRs may refine this architecture but do not silently contradict it. When an accepted ADR changes the architecture, affected documents and links must be updated in the same change or a clearly tracked follow-up.

## Relationship to API contracts

Architecture identifies component responsibilities and permitted communication. API and event contracts specify the exact requests, responses, errors, versions, idempotency behavior, and compatibility rules that realize those boundaries.

An API contract must have a clear architectural owner. It may expose only responsibilities assigned to that owner and must not leak provider-specific or persistence-specific models into unrelated components.

## Relationship to database design

Architecture establishes data ownership, state boundaries, trust requirements, and lifecycle expectations. Database design translates those decisions into entities, relationships, constraints, indexes, retention rules, and migrations.

The database may be physically shared in the Version 1 modular monolith, but each data domain has one logical owner. Direct cross-domain writes are not permitted merely because tables share a database.

## Change control

These documents remain `Draft` until architecture review completes. After Architecture v1.0 is approved, changes to core boundaries or principles require an ADR. Clarifications that preserve meaning use the normal documentation review process.

Review architecture against the [Company Foundation](../01-company/README.md) and [Engineering Principles](../02-engineering-handbook/Principles.md). The first product remains the evidence source for the platform; Architecture v1.0 must not become permission to build unused platform features.
