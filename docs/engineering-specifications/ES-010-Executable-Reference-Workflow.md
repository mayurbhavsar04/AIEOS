---
title: ES-010 — Executable Reference Workflow
version: 1.0
status: Implemented
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 5 Phase 3
last_updated: 2026-07-23
---

# ES-010 — Executable Reference Workflow

## Objective

Implement `HelloAIEOSWorkflow` as the smallest end-to-end proof that Runtime Architecture v1.0 and
the frozen ES-004 through ES-008 contracts can execute together. The workflow is a development
reference only. It contains no AI YouTube Employee or other product behavior.

## Related Documents

| Relationship | Document |
| --- | --- |
| **Runtime baseline** | [Runtime Architecture v1.0](../runtime-architecture/Runtime-Architecture-v1.0.md) |
| **Domain** | [Domain v1.0](../architecture/DomainModel.md) |
| **Execution** | [Execution Flow](../architecture/ExecutionFlow.md) |
| **Commands** | [ES-004](ES-004-Command-Contract-Model.md) |
| **Events** | [ES-005](ES-005-Event-Contract-Model.md) |
| **Interfaces** | [ES-006](ES-006-Service-Interface-Contracts.md) |
| **Results and Errors** | [ES-007](ES-007-Error-and-Result-Model.md) |
| **Observability** | [ES-008](ES-008-Observability-Model.md) |
| **Predecessor** | [ES-009](ES-009-Repository-and-Tooling-Bootstrap.md) |
| **Implementation guide** | [Executable Reference Workflow](../development/Executable-Reference-Workflow.md) |
| **ADRs** | None required; the implementation conforms to frozen baselines. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-23 | CTO / Architect | Initial executable reference-workflow contract. |

## Scope

The implementation SHALL:

- accept one scoped reference Request through Manager;
- dispatch directed Commands outside Event Bus;
- keep authorization, semantic validation, and idempotency with accountable targets;
- create and advance one Workflow Instance in Workflow Engine;
- resolve Skill metadata and Capability contracts through separate registries;
- execute exactly one attempt at a time in Skill Runtime;
- use a deterministic mock AI Gateway and in-memory Memory Service adapter;
- record immutable attempt and Workflow Events through a recoverable in-memory outbox;
- let Workflow Engine alone decide retry and create each new `ExecutionId`;
- normalize success, failure, timeout, and retry evidence through ES-007 Results and Errors;
- propagate verified Tenant, Workspace, correlation, causation, and observability context;
- expose a runnable reference-host endpoint; and
- provide unit, integration, end-to-end, isolation, idempotency, retry, outbox-recovery, timeout, and
  observability tests.

## Frozen Constraints

Commands MUST NOT pass through Event Bus. Event Bus MUST transport Events only. Manager MUST NOT
execute Skills. Skill Runtime MUST NOT orchestrate Workflows or choose retries. Capability Registry
and Skill Registry MUST NOT execute implementations. Provider-specific details MUST remain behind AI
Gateway. Every scoped operation MUST preserve Tenant and Workspace isolation.

## Non-goals

This milestone does not add a real provider, production persistence, a message broker, deployment
automation, dynamic untrusted extensions, the AI YouTube Employee, or other business workflows.

## Acceptance Criteria

- A successful Request reaches a terminal Workflow Result through every required runtime boundary.
- A retryable failed attempt remains terminal and Workflow Engine creates a distinct next attempt.
- Duplicate Command delivery is safe and does not duplicate Workflow effects.
- Cross-Workspace Memory access is rejected.
- Recorded but undelivered Events remain discoverable to a reconstructed outbox relay.
- Timeout produces a terminal attempt outcome and cannot be overwritten by late completion.
- Every emitted log has immutable identity, verified scope, correlation, classification, and
  redaction state.
- The reference host starts, runs the workflow, reports its outcome, and stops.
- The canonical repository check passes without changing frozen baseline documents.

## Definition of Done

The implementation, tests, documentation, and ES-010 record are committed on a feature branch,
validated through `./scripts/check`, and submitted as an unmerged Draft Pull Request.
