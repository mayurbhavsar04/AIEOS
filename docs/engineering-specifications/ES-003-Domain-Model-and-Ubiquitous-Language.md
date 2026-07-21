---
title: ES-003 — Domain Model and Ubiquitous Language
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# ES-003 — Domain Model and Ubiquitous Language

## Document Metadata

| Field | Value |
| --- | --- |
| **Document ID** | ES-003 |
| **Milestone** | 3C |
| **Priority** | Critical |
| **Implementer** | Engineer (Codex) |
| **Architecture baseline** | `architecture-v1.0` at `9579547081c15d077b7e79bb6ae7265c88555b0e` |
| **Architecture status** | Frozen |

## Related Documents

| Relationship | Document |
| --- | --- |
| **Architecture** | [Engineering Blueprint](../03-architecture/EngineeringBlueprint.md) |
| **Architecture** | [System Architecture](../03-architecture/SystemArchitecture.md) |
| **Architecture** | [Execution Flow](../architecture/ExecutionFlow.md) |
| **Specification** | [ES-001 — Execution Core](ES-001-Execution-Core.md) |
| **Specification** | [ES-002 — Execution Flow Architecture](ES-002-Execution-Flow-Architecture.md) |
| **Canonical domain reference** | [Domain Model](../architecture/DomainModel.md) |
| **ADRs** | None required; this specification does not change frozen boundaries. |
| **Related Pull Requests** | Pending |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-21 | CTO / Architect | Initial Milestone 3C specification. |

## 1. Objective

Milestone 3C SHALL establish the canonical domain model and ubiquitous language for AIEOS. It SHALL give product documents, architecture, contracts, prompts, code, telemetry, and reviews one precise meaning for each core term.

This specification refines semantics inside Architecture v1.0. It MUST NOT create a platform service, move responsibility between frozen components, or select implementation technology.

## 2. Scope

The milestone SHALL define:

- the canonical vocabulary listed in this specification;
- entity, value-object, aggregate, aggregate-root, reference, and immutable-record classifications;
- aggregate and consistency boundaries;
- identifiers and identity propagation;
- canonical Command and Event naming and ownership;
- formal lifecycle models;
- cross-domain relationships and invariants;
- prohibited and ambiguous synonyms; and
- extension rules for later specifications.

## 3. Out of Scope

This milestone SHALL NOT define:

- persistence schemas, transactions, tables, indexes, or databases;
- API, serialization, or wire schemas;
- programming languages, frameworks, queues, brokers, or cloud services;
- deployment or process boundaries;
- authentication or authorization implementation;
- tenant provisioning, billing, or exposed multi-tenant SaaS behavior;
- product-specific YouTube workflows;
- complete Command, Event, Error, Policy, or Idempotency envelopes;
- retention periods, archival media, or deletion mechanisms;
- detailed Memory taxonomies or retrieval algorithms; or
- reconciliation algorithms for late external results.

Deferred choices MUST NOT be inferred from diagrams or examples.

## 4. Required Ubiquitous Language

The canonical domain reference SHALL define exactly one preferred meaning for:

`Request`, `Manager`, `Workflow Definition`, `Workflow Instance`, `Workflow Step`, `Command`, `Event`, `Skill`, `Skill Version`, `Skill Runtime`, `Execution Attempt`, `Capability`, `Capability Contract`, `AI Gateway`, `Provider Adapter`, `AI Provider`, `Artifact`, `Memory`, `Context`, `Session`, `User`, `Tenant`, `Workspace`, `Human Approval`, and `Policy`.

Each entry SHALL state its classification, owner, identity where applicable, lifecycle where applicable, relationships, and material invariants. The reference SHALL list prohibited or ambiguous synonyms and prescribe replacements.

## 5. Domain Classification Rules

- An **Entity** has stable identity across state changes.
- A **Value Object** is identified by its validated value and has no independent lifecycle.
- An **Aggregate** is a consistency boundary containing related domain objects.
- An **Aggregate Root** is the only entry point for changes governed by its aggregate invariants.
- A **Reference** identifies an object owned outside the current aggregate; it MUST NOT imply local mutation authority.
- An **Immutable Record** preserves a fact or evidence item after creation. Corrections SHALL create a new record or explicit superseding relationship.

Shared deployment or storage MUST NOT merge aggregate ownership.

## 6. Required Aggregate Boundaries

The canonical reference SHALL define at least these logical aggregates:

| Aggregate | Required root or owner | Required boundary rule |
| --- | --- | --- |
| **Workflow** | Workflow Instance / Workflow Engine | Owns Workflow and Workflow-step state and retry decisions. |
| **Execution** | Execution Attempt / Skill Runtime | Owns one immutable attempt history and normalized outcome; it does not own retry decisions. |
| **Skill** | Skill / Skill Registry | Owns identity and version metadata, not execution. |
| **Capability** | Capability / Capability Registry | Owns provider-neutral contracts and eligible implementation references, not invocation orchestration. |
| **Memory** | Memory / Memory Service | Owns scoped memory lifecycle, provenance, and access boundary. |
| **Artifact** | Artifact / owning Artifact domain | Owns produced evidence and lifecycle without becoming Workflow state. |
| **Session** | Session / Authentication | Owns authentication-session lifecycle; authorization remains explicit elsewhere. |
| **Tenant** | Tenant / Workspace boundary | Preserves future organizational isolation without exposing Version 1 multi-tenant behavior. |
| **Workspace** | Workspace / Workspace component | Owns resource scope, membership references, and policy scope. |

Cross-aggregate relationships SHALL use stable references. The specification MUST NOT imply one atomic transaction across aggregate boundaries.

## 7. Identity Model

The reference SHALL define these typed identities and value objects:

- `RequestId`
- `WorkflowId`
- `WorkflowDefinitionId`
- `WorkflowStepId`
- `ExecutionId`
- `AttemptNumber`
- `CommandId`
- `EventId`
- `SkillId`
- `SkillVersionId`
- `CapabilityId`
- `ArtifactId`
- `SessionId`
- `TenantId`
- `WorkspaceId`
- `CorrelationId`
- `CausationId`

Identifiers MUST be opaque and stable within their defined scope. `AttemptNumber` SHALL increase monotonically within a Workflow-step execution context and MUST NOT replace `ExecutionId`. `CorrelationId` SHALL remain stable across one Workflow's work. `CausationId` SHALL identify the Command, Event, or recorded decision that directly caused the new record or transition.

## 8. Commands and Events

Commands SHALL be directed, imperative requests named with a verb and domain object, for example `StartWorkflow`, `DispatchExecutionAttempt`, `PauseWorkflow`, `ResumeWorkflow`, `CancelWorkflow`, `RecordHumanApproval`, and `PublishArtifact`.

Events SHALL be immutable past-tense facts named with a domain object and outcome, for example `WorkflowStarted`, `ExecutionAttemptFailed`, `HumanApprovalGranted`, and `ArtifactPublished`.

Every Command SHALL have one accountable target and owner. Every Event SHALL have one authoritative producer. Commands MUST NOT pass through the Event Bus. Events SHALL be published through the Event Bus and MUST NOT disguise an instruction to a named consumer.

ES-003 SHALL define names and ownership only. Complete envelopes remain deferred.

## 9. Required State Models

The canonical reference SHALL define state diagrams and transition ownership for:

- Request;
- Workflow Instance;
- Workflow Step;
- Execution Attempt;
- AI Invocation;
- Human Approval; and
- Artifact.

The first five models MUST reproduce ES-002 semantics without broadening them. A failed or timed-out Execution Attempt is terminal. A retry is a Workflow Engine decision that creates a distinct `ExecutionId` and incremented `AttemptNumber`.

Human Approval SHALL distinguish at least `Requested`, `Pending`, `Granted`, `Rejected`, `Expired`, and `Cancelled`. Artifact lifecycle SHALL distinguish creation and validation from readiness and external publication; publication failure MUST NOT be represented as successful publication.

## 10. Required Domain Invariants

The canonical reference SHALL state and preserve at least these invariants:

1. One Workflow Engine owns a Workflow Instance.
2. One Execution Attempt belongs to one Workflow Step.
3. Retries create distinct Execution Attempts.
4. Failed and timed-out attempts remain immutable.
5. Commands are directed.
6. Events are immutable facts.
7. Event Bus transports Events only.
8. Manager does not execute Skills.
9. Skills do not orchestrate Skills.
10. AI Providers are accessed only through AI Gateway.
11. Tenant and Workspace isolation is explicit in every scoped reference.
12. Correlation remains stable across a Workflow.
13. Causation identifies the triggering Command, Event, or recorded decision.
14. Workflow Engine owns Workflow state and retry decisions.
15. Skill Runtime owns retry-safe execution attempts and MUST NOT invent retries.
16. Human waiting preserves Workflow state.
17. Provider-specific formats do not cross the AI Gateway boundary.

## 11. Required Diagrams

The canonical reference SHALL use GitHub-compatible Mermaid for:

1. domain relationships;
2. aggregate boundaries;
3. identity and correlation flow;
4. Command dispatch and Event publication;
5. Request lifecycle;
6. Workflow Instance lifecycle;
7. Workflow Step lifecycle;
8. Execution Attempt lifecycle;
9. AI Invocation lifecycle;
10. Human Approval lifecycle; and
11. Artifact lifecycle.

Diagrams MUST agree with prose and MUST NOT imply a new transport, persistence, service, or ownership boundary.

## 12. Documentation Requirements

`DomainModel.md` SHALL include:

- purpose and architectural context;
- classification rules;
- canonical glossary;
- prohibited or ambiguous synonyms;
- aggregate catalog and ownership;
- relationship and aggregate diagrams;
- identity catalog and identity-flow diagram;
- canonical Commands and Events with owner/producer;
- all required lifecycle diagrams;
- domain invariants;
- tenant and Workspace isolation rules;
- extension rules;
- non-goals and deferred decisions;
- open questions; and
- valid traceability links.

## 13. Acceptance Criteria

- [ ] Every required term has one canonical definition.
- [ ] Every stateful concept has one lifecycle owner.
- [ ] Entity, Value Object, Aggregate, Aggregate Root, Reference, and Immutable Record are used precisely.
- [ ] Every required aggregate has an explicit boundary and owner.
- [ ] Cross-aggregate relationships do not imply mutation authority.
- [ ] Every required identifier has a stated scope and purpose.
- [ ] Commands and Events are distinguished, named consistently, and assigned owners.
- [ ] Event Bus transports Events only.
- [ ] Manager is the only approved component name used.
- [ ] Workflow Engine owns Workflow state and retry decisions.
- [ ] Skill Runtime owns retry-safe attempts and does not invent retries.
- [ ] Every retry creates a new `ExecutionId` and incremented `AttemptNumber`.
- [ ] AI Provider access crosses AI Gateway only.
- [ ] Human waiting preserves Workflow state.
- [ ] Tenant and Workspace scope is explicit without claiming exposed multi-tenant support.
- [ ] Lifecycle diagrams agree with ES-002.
- [ ] No frozen boundary changes or implementation choices are introduced.
- [ ] Relative links resolve and `git diff --check` passes.

## 14. Review Checklist

Reviewers SHALL verify:

- semantic consistency with Architecture v1.0, ES-001, and ES-002;
- single ownership of identities, state, transitions, Commands, and Events;
- no aggregate used as a synonym for service or deployment;
- no direct Skill-to-Skill orchestration or provider access;
- no Command shown traveling through Event Bus;
- no terminal Execution Attempt revived during retry;
- explicit Tenant and Workspace scope at trust boundaries;
- Mermaid agreement with prose; and
- explicit deferral rather than accidental technology selection.

## 15. Definition of Done

- [ ] This specification and `DomainModel.md` exist.
- [ ] Only the two intended documentation files changed.
- [ ] All required sections, glossary entries, classifications, identities, aggregates, lifecycles, diagrams, invariants, and non-goals are present.
- [ ] No architecture conflict requires an ADR.
- [ ] Validation passes.
- [ ] A Draft Pull Request is opened against `main` and is not merged.

## 16. Implementation Instructions

The Engineer SHALL work from the frozen architecture baseline, change only the two Milestone 3C files, and stop if a boundary conflict is discovered. The Engineer MUST NOT resolve a conflict by inventing a service, renaming a component, or modifying Architecture v1.0. A required boundary change SHALL be reported as an ADR requirement before further work.

Return to the [Engineering Specifications process](README.md).
