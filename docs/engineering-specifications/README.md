---
title: Engineering Specifications
version: 1.7
status: Approved
owner: Founding Team
last_updated: 2026-08-21
---

# Engineering Specifications

## Purpose

An Engineering Specification (ES) is the reviewed contract for a bounded unit of engineering work. It translates approved product requirements and architecture into implementation-ready responsibilities, deliverables, constraints, acceptance criteria, and completion rules.

An ES exists to prevent implementation from becoming an unreviewed architecture exercise. It gives reviewers a stable statement of intent and gives an Engineer enough direction to execute without inventing services, ownership, technologies, or system behavior.

Codex implements approved Engineering Specifications. Codex does not invent architecture. When a specification is incomplete, ambiguous, or inconsistent with approved architecture, Codex MUST stop and request clarification rather than silently choose a new design.

## Related Documents

| Relationship | Document |
| --- | --- |
| **Company Foundation** | [Company Foundation](../01-company/README.md) |
| **Engineering Governance** | [Engineering Handbook](../02-engineering-handbook/README.md) |
| **Architecture** | [Architecture v1.0](../03-architecture/README.md) |
| **First Specification** | [ES-001 — Execution Core](ES-001-Execution-Core.md) |
| **Current review** | [ES-016 — Governed Structured AI Capability Execution](ES-016-Governed-Structured-AI-Capability-Execution.md) with authoritative-result reuse governance |
| **Related Pull Request** | [PR #3 — Establish Engineering Specification process](https://github.com/mayurbhavsar04/AIEOS/pull/3) |

## Engineering Lifecycle

Every material engineering change SHALL follow this lifecycle:

```text
Business Vision
        ↓
Product Requirement (PRD)
        ↓
Engineering Specification (ES)
        ↓
Architecture Review
        ↓
Implementation
        ↓
Pull Request
        ↓
Architecture Review
        ↓
Merge
        ↓
Release
```

An implementation MAY begin only after its ES and required architecture are approved. A Pull Request does not replace either review stage.

## ES Status Lifecycle

| Status | Meaning |
| --- | --- |
| **Draft** | The specification is being written. Its scope, ownership, or acceptance criteria may change. It MUST NOT authorize implementation. |
| **In Review** | The specification is complete enough for product and architecture review. Review findings remain open, and implementation MUST NOT begin unless explicitly authorized. |
| **Approved** | The accountable reviewer has accepted the scope, constraints, ownership, and acceptance criteria. Implementation MAY proceed against this version. |
| **Implemented** | The approved specification has been delivered, validated, reviewed, and merged. Any accepted deviations are documented. |
| **Archived** | The specification is retained for history but no longer governs active work. Its replacement or reason for retirement SHOULD be recorded. |

Status transitions MUST be explicit and reviewed. A material change to an Approved ES returns it to In Review unless an accepted decision record defines another process.

## Responsibilities

### CTO / Architect

The CTO / Architect is responsible for:

- defining architecture;
- writing Engineering Specifications;
- reviewing Pull Requests;
- approving architectural changes; and
- maintaining long-term architectural consistency.

The CTO / Architect SHALL make ownership, constraints, and unresolved decisions explicit before approving implementation.

### Engineer (Codex)

The Engineer is responsible for:

- implementing approved Engineering Specifications;
- preserving approved architecture;
- opening Draft Pull Requests; and
- asking for clarification when requirements are ambiguous.

The Engineer MUST NOT:

- invent architecture;
- rename approved components;
- change architecture without approval; or
- introduce technologies that the specification does not request.

The Engineer SHALL keep changes within specification scope and SHALL report any required deviation before implementing it.

## Naming Convention

Engineering Specifications use a permanent sequential identifier:

```text
ES-001
ES-002
ES-003
...
```

The file name SHALL use the identifier followed by a concise hyphenated title:

```text
ES-001-Execution-Core.md
```

Identifiers MUST NOT be reused, renumbered, or reassigned after publication. A replacement ES receives a new identifier and references the superseded specification.

## Required Document Relationships

Every ES SHALL include a **Related Documents** section near its metadata. The section SHALL identify:

- **PRD:** the Product Requirement that authorizes the work;
- **Architecture:** the approved architecture documents that constrain the work;
- **ADRs:** accepted or required Architecture Decision Records;
- **Future Specifications:** known specifications that will extend, implement, or supersede the ES; and
- **Related Pull Requests:** Pull Requests that establish, revise, implement, or validate the ES.

Each relationship MUST use a stable repository link or Pull Request URL when the artifact exists. If it does not yet exist, the ES MUST say `Pending` or `None` and explain the expected relationship. An ES MUST NOT invent a document identifier, approval, or link to make the section appear complete.

Traceability SHALL be updated when a related artifact is created, replaced, archived, or materially changed.

## Required Version History

Every ES SHALL include a **Version History** section near its Related Documents section. The history SHALL contain:

| Field | Requirement |
| --- | --- |
| **Version** | The document version affected by the recorded change. |
| **Date** | The change date in `YYYY-MM-DD` format. |
| **Author** | The accountable authoring role or person. |
| **Notes** | A concise description of the material change. |

The first row SHALL record the initial version. Later rows SHALL preserve earlier history and record material scope, constraint, ownership, acceptance-criteria, or status changes. Editorial corrections MAY remain within the current version when they do not change meaning.

## Repository Structure

Engineering Specifications live in one version-controlled directory:

```text
docs/
└── engineering-specifications/
    ├── README.md
    ├── ES-001-Execution-Core.md
    ├── ES-002-<Title>.md
    └── ...
```

This README defines the process. Each `ES-###` document defines one reviewable body of future engineering work.

## Engineering Principles

### Architecture First

Approved architecture defines components, responsibilities, and boundaries before implementation begins.

### Documentation Before Implementation

Material work SHALL have an Approved ES with clear acceptance criteria before code or infrastructure is created.

### Small Reviewable Pull Requests

Each Pull Request SHOULD deliver one coherent specification or an explicitly bounded part of one. Unrelated work is excluded.

### One Owner Per Responsibility

Every state, decision, command, event, retry, and failure outcome SHALL have one accountable owner.

### Provider Neutrality

Specifications SHALL describe capabilities and contracts without selecting a provider unless an approved decision explicitly requires one.

### Explicit Ownership

Component and data ownership MUST be stated. Shared deployment or storage MUST NOT imply shared authority.

### No Silent Architecture Changes

An implementation MUST NOT alter approved names, boundaries, dependencies, or ownership without review and an accepted architecture change.

### Architecture Freeze After Approval

Once architecture is approved, implementation SHALL treat it as fixed. A material change requires the approved decision process before the implementation continues.

### Domain v1.0 Freeze Governance

Changes to canonical domain concepts, aggregate ownership, identities, commands, events, or invariants require architecture review. Changes that alter established semantics or cross aggregate boundaries must be introduced through an ADR. Editorial clarifications that do not change canonical meaning do not require an ADR but still require normal review.

## Current Specifications

| Specification | Title | Status |
| --- | --- | --- |
| [ES-001](ES-001-Execution-Core.md) | Execution Core | Draft |
| [ES-004](ES-004-Command-Contract-Model.md) | Command Contract Model | Approved |
| [ES-006](ES-006-Service-Interface-Contracts.md) | Service Interface Contracts | Approved |
| [ES-010](ES-010-Executable-Reference-Workflow.md) | Executable Reference Workflow | Implemented |
| [ES-011](ES-011-Durable-Runtime-Infrastructure.md) | Durable Runtime Infrastructure | Implemented |
| [ES-012](ES-012-AI-Gateway-and-Token-Governance.md) | AI Gateway and Token Governance | Implemented |
| [ES-013](ES-013-AI-Gateway-Reference-Implementation.md) | AI Gateway Reference Implementation | Implemented |
| [ES-014](ES-014-First-Real-AI-Provider-Adapter.md) | First Real AI Provider Adapter | Implemented |
| [ES-015](ES-015-Multi-Provider-Routing-and-Failover.md) | Multi-Provider Routing and Failover | Frozen Phase 4 baseline (source status: Proposed) |
| [ES-016](ES-016-Governed-Structured-AI-Capability-Execution.md) | Governed Structured AI Capability Execution | Approved |
| [ES-017](ES-017-Governed-AI-Workflow-Execution.md) | Governed AI Workflow Execution | Approved |

ES-015's source header remains `Proposed`; the immutable Phase 4 merge/tag nevertheless freezes its
implemented governance baseline. The index records both facts rather than treating `Proposed` as an
unfrozen Phase 4 deliverable.

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.7 | 2026-08-21 | CTO / Architect | Recorded approval of ES-017 v0.2 after governance review of `8e7f09bd7a036dd8935781dba6c960b196afcfef`; the separate contract-amendment implementation gate remains in force. |
| 1.6 | 2026-08-20 | CTO / Architect | Added ES-017 as a Phase 6 governance Draft; no implementation is authorized. |
| 1.5 | 2026-08-15 | CTO / Architect | Recorded approval of ES-004 v1.2, ES-006 v1.2, and ES-016 v0.7 after focused CTO review of `210df6491397b57875edf9942da009341c8161e1`. |
| 1.4 | 2026-08-14 | CTO / Architect | Returned ES-004, ES-006, and ES-016 to In Review for `DispatchExecutionAttempt` v2 authoritative-result reuse governance. |
| 1.3 | 2026-08-13 | CTO / Architect | Recorded approval of ES-016's first-release rollback clarification. |
| 1.2 | 2026-08-13 | CTO / Architect | Updated ES-016 status to In Review for its first-release rollback clarification. |
| 1.1 | 2026-07-21 | Founding Team | Added Domain v1.0 freeze governance following Milestone 3C completion. |
| 1.0 | 2026-07-20 | Founding Team | Initial ES process, including mandatory traceability and specification version history. |

Return to the [repository overview](../../README.md), [Engineering Handbook](../02-engineering-handbook/README.md), or [Architecture](../03-architecture/README.md).
