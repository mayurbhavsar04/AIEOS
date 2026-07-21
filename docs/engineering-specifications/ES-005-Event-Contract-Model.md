---
title: ES-005 — Event Contract Model
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# ES-005 — Event Contract Model

## Document Metadata

| Field | Value |
| --- | --- |
| **Document ID** | ES-005 |
| **Milestone** | Milestone 4 — Platform Contracts & Service Interfaces, Phase 2 |
| **Priority** | Critical |
| **Implementer** | Engineer (Codex) |
| **Architecture baseline** | Architecture v1.0, frozen at tag `architecture-v1.0` |
| **Domain baseline** | Domain v1.0, frozen at tag `domain-v1.0` plus governance commit on `main` |
| **Command baseline** | ES-004, frozen at tag `contracts-v1.0-es004` |
| **Architecture status** | Conforms; no boundary, domain-semantic, or Command-semantic change is introduced. |

## Related Documents

| Relationship | Document |
| --- | --- |
| **PRD** | Pending — no approved PRD exists; ES-005 authorizes documentation only. |
| **Architecture** | [Engineering Blueprint](../03-architecture/EngineeringBlueprint.md) and [System Architecture](../03-architecture/SystemArchitecture.md) |
| **Canonical execution architecture** | [Execution Flow](../architecture/ExecutionFlow.md) |
| **Canonical domain reference** | [Domain Model](../architecture/DomainModel.md) |
| **Prior specifications** | [ES-001 — Execution Core](ES-001-Execution-Core.md), [ES-002 — Execution Flow Architecture](ES-002-Execution-Flow-Architecture.md), [ES-003 — Domain Model and Ubiquitous Language](ES-003-Domain-Model-and-Ubiquitous-Language.md), and [ES-004 — Command Contract Model](ES-004-Command-Contract-Model.md) |
| **Command contract** | [Command Contract Model](../architecture/CommandContract.md) |
| **Canonical deliverable** | [Event Contract Model](../architecture/EventContract.md) |
| **ADRs** | None required; this specification refines a deferred Event contract without changing frozen boundaries or semantics. |
| **Future specifications** | Error & Result and Observability specifications remain pending. |
| **Related Pull Requests** | Pending — update after Draft Pull Request creation. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-21 | CTO / Architect | Initial Milestone 4 Phase 2 specification. |

## 1. Objective

ES-005 SHALL define the canonical Event envelope and transport-neutral Event-processing contract for AIEOS. It SHALL make Event meaning, lifecycle, ownership, validation, correlation, causation, delivery, duplicate handling, ordering, replay, versioning, security, and audit expectations explicit while preserving Architecture v1.0, Domain v1.0, and ES-004.

This specification authorizes documentation only. It MUST NOT authorize product code, infrastructure, APIs, storage, deployment, transport, serialization, or provider selection.

## 2. Scope

ES-005 SHALL define:

- the canonical Event envelope and required versus conditionally required fields;
- the distinction between Event facts, Commands, delivery observations, and audit records;
- Event occurrence, construction, validation, recording, publication, delivery, consumption, projection, replay, and retention boundaries;
- fact observer, constructor, authoritative producer, validator, publisher, consumer, replay-decision owner, and projection owner responsibilities;
- correlation and causation rules aligned with Domain v1.0 and ES-004;
- a bounded Event taxonomy supported by existing architecture and domain semantics;
- duplicate-safe delivery, idempotent consumption, ordering, late-event, poison-event, and consumer-isolation expectations;
- replay identity, side-effect protection, projection rebuilding, and version compatibility;
- Event versioning, compatibility, deprecation, and contract-level transformation policy;
- validation, authorization, isolation, sensitive-data, integrity, and audit requirements;
- implementation-neutral Event error boundaries and deferrals to ES-007;
- required Mermaid diagrams; and
- traceability, acceptance criteria, and review checks.

## 3. Frozen Constraints

The deliverable MUST preserve these constraints:

1. An Event is an immutable past-tense fact with exactly one authoritative producer.
2. Event Bus transports validated Event envelopes only.
3. Event Bus does not transport Commands, make business decisions, or own authoritative domain state.
4. Commands request actions; Events record facts and MUST NOT disguise instructions to named consumers.
5. `EventId` identifies one immutable Event; correction creates a new Event and relationship.
6. `CausationId` references only the immediate causal Command, Event, or recorded decision.
7. A Request is not a valid direct `CausationId` target; `RequestId` remains separate context.
8. Every canonical Event retains its authoritative producer defined by Domain v1.0.
9. Workflow Engine retains Workflow and retry-decision ownership; Skill Runtime retains execution-attempt ownership.
10. Tenant and Workspace scope remains explicit wherever applicable.
11. ES-004 Command fields, routing, lifecycle, and causation semantics remain unchanged.
12. No component, aggregate, service, route, Event meaning, identity, producer, or owner may be added or changed.

If satisfying ES-005 would violate a constraint, work MUST stop for architecture or domain review rather than introduce an ADR or solution within this milestone.

## 4. Canonical Envelope Requirements

The canonical deliverable SHALL define these fields:

| Field | Required status |
| --- | --- |
| `EventId` | Required |
| `EventType` | Required |
| `EventVersion` | Required |
| `OccurredAt` | Required |
| `RecordedAt` | Required |
| `Producer` | Required; exactly one authoritative producer |
| `TenantId` | Required for Tenant-scoped facts |
| `WorkspaceId` | Required for Workspace-scoped facts |
| `CorrelationId` | Required |
| `CausationId` | Required except for a declared root Event |
| `RequestId` | Required when a Request provides context; never causation |
| `WorkflowId` | Required when the fact concerns an existing Workflow Instance |
| `WorkflowStepId` | Required when the fact concerns a Workflow Step |
| `ExecutionId` | Required when the fact concerns an Execution Attempt |
| `AIInvocationId` | Required when the fact concerns an AI Invocation |
| `Subject` | Required; canonical identity of the fact's primary domain subject or Aggregate |
| `Payload` | Required; governed by `EventType` and `EventVersion` |
| `Metadata` | Required; structured, versioned, and non-authoritative unless explicitly verified |

The contract SHALL define conditional presence, field relationships, immutability, root-Event causation, event-time versus record-time semantics, duplicate identity behavior, correction behavior, replay behavior, and scope propagation.

## 5. Event Semantics and Taxonomy

The deliverable SHALL preserve the rule: **Commands express intent; Events record facts.**

It SHALL distinguish, without inventing duplicate canonical concepts:

- **Domain Events:** authoritative facts about a canonical domain lifecycle or invariant;
- **Integration Events:** deliberately published, compatibility-governed representations of authoritative facts for consumers outside the producer's local contract boundary;
- **System or runtime Events:** facts about platform execution and operational lifecycles already owned by approved components; and
- **Audit records:** protected evidence of actions and decisions, distinct from Event transport and not automatically Events.

Classification MUST NOT change an Event's authoritative producer or create a second authoritative Event for the same fact without an explicit derived relationship. Audit records MUST NOT be treated as authoritative domain state merely because they preserve evidence.

## 6. Lifecycle and Ownership Requirements

The canonical deliverable SHALL define these implementation-neutral stages:

1. fact occurrence;
2. Event construction;
3. envelope and payload validation;
4. authoritative recording;
5. publication through Event Bus;
6. delivery;
7. consumer validation and consumption;
8. projection or local update;
9. replay when authorized; and
10. archival or retention boundary.

The immutable Event itself does not change lifecycle state. Publication, delivery, consumption, and replay are observations or operations associated with `EventId`.

Ownership SHALL be explicit:

- the component owning the state or confirmed fact observes and authoritatively establishes it;
- that same authoritative producer owns construction of the canonical Event or delegates construction without delegating authority;
- the producer validates the canonical envelope, payload, scope, identity, and version before recording and publication;
- Event Bus validates transport-level eligibility and preserves the envelope but MUST NOT reinterpret the fact;
- each consumer validates supported version, scope, semantics, and local invariants at its trust boundary;
- the owner of a projection owns projection correctness and duplicate handling;
- the owner of a replay purpose authorizes replay, while the Event source owner determines whether the source is authoritative and replayable; and
- no consumer may retroactively mutate the Event or its producer-owned fact.

## 7. Correlation and Causation Requirements

The deliverable SHALL define:

- `CorrelationId` as the stable operation-chain identity required by Domain v1.0;
- `CausationId` as the identifier of the immediate causal Command, Event, or recorded decision;
- `RequestId` as context only, never a direct causation target;
- root Events as exceptional contract-declared facts that have no prior in-platform cause;
- absent causation as valid only for a declared root Event and represented explicitly rather than with a fabricated identifier;
- recorded-decision causation as a stable reference to protected, retrievable decision evidence; and
- derived Events as new Events whose `CausationId` references the source Event or recorded decision that directly caused them.

An Event MUST NOT use `RequestId`, `CorrelationId`, a timestamp, a free-form string, or its own `EventId` as a substitute for `CausationId`.

## 8. Delivery and Processing Requirements

The deliverable SHALL specify:

- consumers MUST tolerate at-least-once delivery and duplicate Event envelopes;
- redelivery of the same Event preserves `EventId` and all immutable envelope content;
- duplicate detection and idempotent consumption are owned by each consumer within its local effect boundary;
- global ordering MUST NOT be assumed;
- ordering MAY be relied upon only within an explicitly versioned ordering scope based on stable subject identity and only to the extent promised by a future implementation contract;
- late or out-of-order Events MUST be validated against authoritative state, version, and projection position rather than arrival order alone;
- malformed, unauthorized, unsupported, or semantically unprocessable Events MUST be isolated without blocking unrelated consumers;
- one consumer's failure MUST NOT convert the Event into failure for other consumers or mutate the authoritative fact; and
- external or consequential side effects during consumption require a separate authorized, idempotent action contract and MUST NOT be triggered blindly during replay.

No broker, queue, topic, partition implementation, protocol, or persistence technology may be selected.

## 9. Replay Requirements

Replay SHALL be defined as re-presenting previously recorded immutable Events from an authoritative source to an authorized consumer or projection purpose.

The deliverable SHALL specify:

- only authoritative, integrity-verified, version-interpretable Events may be replayed;
- replay reuses the original Event envelope and `EventId` and MUST NOT create a replacement historical Event;
- replay operations require a distinct operation or trace identity outside the immutable Event envelope;
- replay MUST be distinguishable from live delivery through delivery context that does not mutate the Event;
- consumers MUST prevent duplicate side effects and SHOULD support a side-effect-disabled projection-rebuild mode where applicable;
- projection rebuild begins from a declared boundary and produces a verifiable position or completion record;
- replay order follows only the Event source's documented ordering scope and MUST NOT imply global order;
- incompatible historical versions require an approved compatibility transformation at the consumer boundary; and
- any derived fact created during replay receives a new `EventId` and appropriate causation, rather than altering the replayed Event.

Replay policy, retention duration, storage, and operational mechanisms remain deferred.

## 10. Versioning and Compatibility Requirements

`EventVersion` SHALL version the complete interpretation of one `EventType`, including envelope requirements and payload schema. The deliverable SHALL define:

- published Event versions and historical interpretation are immutable;
- additive changes are compatible only when existing consumers are explicitly allowed to ignore the new non-authoritative fields and meaning is unchanged;
- breaking changes to required fields, meaning, producer, subject, scope, authority, or payload require a new version and may require domain or architecture review;
- producers publish only declared versions and consumers declare supported versions;
- unsupported versions fail deliberately and are isolated;
- deprecation names the owner, replacement, affected consumers, migration guidance, compatibility window, and removal criteria;
- upcasting MAY create an in-memory compatible interpretation without rewriting the historical Event;
- downcasting MUST NOT invent, discard, or weaken authoritative semantics and is disallowed unless a reviewed lossless contract exists; and
- compatibility evidence SHALL be testable before a version is retired.

## 11. Validation and Security Requirements

Validation SHALL distinguish:

1. **Schema validation:** required fields, types, structure, nullability, version, and payload form.
2. **Semantic validation:** field relationships, Event meaning, subject identity, lifecycle fact, and producer ownership.
3. **Producer authorization:** verified producer identity and authority to assert this Event type and subject.
4. **Isolation validation:** Tenant, Workspace, subject, and referenced identifiers share an authorized scope.
5. **Integrity validation:** immutable envelope and protected recording or delivery evidence have not been changed.

The contract SHALL require sensitive-data minimization, no credentials, field-specific metadata trust, protected audit evidence, and contract-level tamper-evidence expectations without selecting a signing or storage technology.

## 12. Error Boundaries and Deferrals

The deliverable SHALL distinguish invalid, unauthorized, malformed, incompatible, duplicate, late, and unprocessable Events as stable conceptual conditions. It SHALL identify the component responsible for detecting and isolating each condition without defining the final Error envelope, code taxonomy, remediation workflow, or operator response.

Detailed Error and Result semantics are deferred to ES-007. Observability, telemetry, and audit transport details are deferred to ES-008. Deferral MUST NOT permit silent dropping, mutation, unauthorized processing, or conversion of an Event into a Command.

## 13. Required Mermaid Diagrams

The canonical deliverable SHALL include GitHub-compatible Mermaid diagrams for:

1. Event lifecycle;
2. producer-to-consumer flow;
3. authoritative-producer ownership;
4. correlation and causation;
5. duplicate and idempotent consumption;
6. replay flow; and
7. version compatibility.

Diagrams MUST agree with prose and MUST NOT imply a broker, queue, database, topic, serialization format, deployment unit, multiple authoritative producers, Event mutation, Commands through Event Bus, or replayed external effects.

## 14. Non-Goals

ES-005 explicitly excludes:

- Event broker or messaging-product selection;
- transport protocols;
- REST or gRPC;
- databases and persistence schemas;
- queue, stream, partition, or topic naming;
- serialization formats;
- programming languages and frameworks;
- deployment topology;
- retention durations and storage mechanisms;
- complete Error, Result, Idempotency, Policy, authorization, or Observability standards;
- product-specific Event payloads beyond frozen canonical examples; and
- changes to Architecture v1.0, Domain v1.0, or ES-004.

## 15. Governance and Traceability

The deliverable SHALL trace every refined requirement to Architecture v1.0, Domain v1.0, ES-001, ES-002, ES-003, and ES-004.

Any proposed semantic change to canonical Event identity, type meaning, authoritative producer, lifecycle ownership, aggregate meaning, causation, Event Bus responsibility, or another frozen boundary requires the applicable architecture or domain review and ADR process before work proceeds.

## 16. Acceptance Criteria

- [ ] `docs/engineering-specifications/ES-005-Event-Contract-Model.md` exists and follows the ES process.
- [ ] `docs/architecture/EventContract.md` exists as the canonical deliverable.
- [ ] Every envelope field is classified as required or conditionally required with explicit conditions.
- [ ] Every Event is an immutable fact with exactly one authoritative producer.
- [ ] Events and Commands remain distinct, and Event Bus transports Events only.
- [ ] `RequestId` is context only and never a `CausationId` target.
- [ ] Correlation, causation, root-Event, and recorded-decision rules match Domain v1.0 and ES-004.
- [ ] Event taxonomy does not duplicate canonical concepts or conflate audit evidence with domain state.
- [ ] Lifecycle stages and all accountable owners are explicit.
- [ ] Duplicate, ordering, late-event, poison-event, and consumer-isolation behavior is defined.
- [ ] Replay preserves historical Event identity and prevents duplicate external effects.
- [ ] Versioning, compatibility, deprecation, upcasting, and historical interpretation rules are defined.
- [ ] Schema, semantic, authorization, isolation, and integrity validation are distinct.
- [ ] Error conditions are bounded without pre-empting ES-007.
- [ ] All seven required Mermaid diagrams are present and valid.
- [ ] No excluded technology or implementation choice is introduced.
- [ ] Architecture v1.0, Domain v1.0, and ES-004 remain unchanged.
- [ ] Terminology is consistent and relative links resolve.
- [ ] `git diff --check` passes.

## 17. Review Checklist

Reviewers SHALL verify:

- one authoritative producer for every Event;
- no Event disguises a Command or directs a named consumer;
- Event Bus owns transport only and no Event consumer gains mutation authority;
- `OccurredAt` and `RecordedAt` are distinct and correctly used;
- `CausationId` names only a Command, Event, or recorded decision;
- root-Event exceptions are explicit and not a causation loophole;
- Event classification does not create parallel truth;
- replay reuses historical `EventId` and separates replay-operation identity;
- replay and redelivery cannot repeat consequential external effects blindly;
- ordering claims are bounded and do not imply global order;
- consumers own local idempotency and projection correctness;
- compatibility transforms never rewrite historical Events or weaken authority;
- Tenant and Workspace scope is explicit without claiming exposed multi-tenant SaaS behavior;
- audit records remain distinct from diagnostic logs and authoritative domain Events;
- transport, persistence, language, serialization, deployment, and vendor choices remain deferred; and
- prose, tables, and Mermaid diagrams agree.

## 18. Definition of Done

- [ ] Only the two intended documentation files are created.
- [ ] All acceptance criteria and review checks are satisfied.
- [ ] No architecture, domain, or Command-contract conflict requires an ADR.
- [ ] Validation evidence is recorded in the Draft Pull Request.
- [ ] A Draft Pull Request is opened against `main` and is not merged.

## 19. Implementation Instructions

The Engineer SHALL work from current `main`, create `docs/es-005-event-contract-model`, change only the two ES-005 documentation files, validate the deliverables, commit with `docs: add ES-005 Event Contract Model`, push the branch, and open a Draft Pull Request titled `docs: add ES-005 Event Contract Model`.

If implementation would alter an Architecture v1.0 boundary, Domain v1.0 semantic, or ES-004 Command semantic, the Engineer MUST stop and report the conflict. The Engineer MUST NOT resolve it by inventing a component, owner, route, technology, Event meaning, or domain identity.

Return to the [Engineering Specifications process](README.md).
