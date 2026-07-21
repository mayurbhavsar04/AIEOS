---
title: ES-006 — Service Interface Contracts
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# ES-006 — Service Interface Contracts

## Document Metadata

| Field | Value |
| --- | --- |
| **Document ID** | ES-006 |
| **Milestone** | Milestone 4 — Platform Contracts & Service Interfaces, Phase 3 |
| **Priority** | Critical |
| **Implementer** | Engineer (Codex) |
| **Architecture baseline** | Architecture v1.0, frozen at tag `architecture-v1.0` |
| **Domain baseline** | Domain v1.0, frozen at tag `domain-v1.0` |
| **Command baseline** | ES-004, frozen at tag `contracts-v1.0-es004` |
| **Event baseline** | ES-005, frozen at tag `contracts-v1.0-es005` |
| **Architecture status** | Conforms; no component, ownership, identity, Command, Event, retry, or cross-boundary semantic is changed. |

## Related Documents

| Relationship | Document |
| --- | --- |
| **PRD** | Pending — no approved PRD exists; ES-006 authorizes documentation only. |
| **Architecture** | [Engineering Blueprint](../03-architecture/EngineeringBlueprint.md) and [System Architecture](../03-architecture/SystemArchitecture.md) |
| **Execution architecture** | [Execution Flow](../architecture/ExecutionFlow.md) |
| **Domain** | [Domain Model](../architecture/DomainModel.md) |
| **Prior specifications** | [ES-001](ES-001-Execution-Core.md), [ES-002](ES-002-Execution-Flow-Architecture.md), [ES-003](ES-003-Domain-Model-and-Ubiquitous-Language.md), [ES-004](ES-004-Command-Contract-Model.md), and [ES-005](ES-005-Event-Contract-Model.md) |
| **Messaging contracts** | [Command Contract](../architecture/CommandContract.md) and [Event Contract](../architecture/EventContract.md) |
| **Canonical deliverable** | [Service Interface Contracts](../architecture/ServiceInterfaces.md) |
| **ADRs** | None required; this specification refines approved interfaces without changing frozen boundaries. |
| **Future specifications** | ES-007 Error & Result Model and ES-008 Observability Model are pending. |
| **Related Pull Requests** | Pending — update after Draft Pull Request creation. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-21 | CTO / Architect | Initial Milestone 4 Phase 3 specification. |

## 1. Objective

ES-006 SHALL define implementation-neutral public interfaces for the approved Manager, Workflow Engine, Skill Runtime, AI Gateway, Memory Service, and Capability Registry components. It SHALL make operation ownership, inputs, outcomes, preconditions, postconditions, authorization, scope, identity propagation, idempotency, cancellation, timeout, compatibility, Command, and Event relationships explicit.

This specification authorizes documentation only. It MUST NOT define code signatures, routes, protocols, serialization, infrastructure, persistence, or provider SDKs.

## 2. Frozen Constraints

The deliverable SHALL preserve these baselines:

1. Manager owns the external Request boundary, interpretation, acceptance or rejection, and orchestration handoff; only Manager authoritatively produces `RequestRejected`.
2. `AcceptRequest` targets Manager only. Starting a Workflow is a distinct directed Command to Workflow Engine.
3. Workflow Engine alone owns Workflow and Workflow Step state, retry policy, retry decisions, and creation of each new `ExecutionId`.
4. Skill Runtime owns one retry-safe Execution Attempt at a time and MUST NOT create retries or mutate Workflow state.
5. AI Gateway owns provider-neutral AI Invocation, provider isolation, bounded provider-policy behavior, and `AIInvocationId` lifecycle.
6. Memory Service owns the Memory Aggregate and its authorized lifecycle without exposing persistence choices.
7. Capability Registry is the canonical capability component. It owns Capability identity, immutable contract versions, discovery, resolution, and compatibility metadata; it MUST NOT execute Skills or orchestrate Workflows.
8. Commands are directed to exactly one accountable target through the abstract command-dispatch contract and never pass through Event Bus.
9. Events are immutable facts with exactly one authoritative producer and Event Bus transports Events only.
10. Architecture v1.0, Domain v1.0, ES-004, and ES-005 remain authoritative.

## 3. Scope

ES-006 SHALL define:

- responsibility and dependency boundaries for the six approved components;
- public operation contracts and their callers, targets, inputs, outcomes, preconditions, postconditions, scope, authorization, idempotency, cancellation, timeout, and versioning;
- Command acceptance or emission and Event production or consumption relationships;
- request-to-Workflow, Workflow-to-Execution, Skill-to-Capability, Skill-to-AI, and Memory-access interactions;
- authority transfer, identity propagation, retry ownership, cancellation propagation, and timeout ownership;
- interface versioning, compatibility, negotiation, deprecation, and breaking-change governance;
- contract-level security, tenant and Workspace isolation, and audit context;
- bounded success and failure semantics with explicit deferral to ES-007;
- bounded correlation and audit-context propagation with explicit deferral to ES-008;
- required Mermaid diagrams; and
- acceptance, review, traceability, and governance requirements.

## 4. Public Operation Documentation Standard

Every public operation SHALL define:

| Requirement | Meaning |
| --- | --- |
| **Name and purpose** | One canonical operation name and one bounded intent. |
| **Caller and target** | The authorized caller role and exactly one accountable target component. |
| **Input** | Provider-neutral domain identities, context, contract version, and payload semantics. |
| **Outcome** | Accepted result, rejection, or normalized failure semantics without pre-empting ES-007. |
| **Messages** | Commands accepted or emitted and Events consumed or authoritatively produced. |
| **Preconditions** | Conditions required before acceptance. |
| **Postconditions** | Authoritative state or recorded decision guaranteed after success. |
| **Idempotency** | Duplicate-call and duplicate-message behavior. |
| **Authorization and scope** | Required verified caller authority, `TenantId`, and `WorkspaceId`. |
| **Trace context** | `RequestId`, `CorrelationId`, `CausationId`, and domain identities that propagate. |
| **Cancellation and timeout** | Owner and observable outcome at the operation boundary. |
| **Versioning** | Interface and referenced contract versions required for interpretation. |

An operation name defines semantic behavior, not an HTTP route, RPC method, class method, transport address, or deployment endpoint.

## 5. Required Component Contracts

### 5.1 Manager

The deliverable SHALL define operations for Request submission, interpretation and validation, acceptance, rejection, Workflow handoff, outcome receipt, clarification, and final response. It SHALL preserve `AcceptRequest` as a Manager-targeted Command and use a distinct `StartWorkflow` Command for Workflow Engine.

Manager MUST NOT own Workflow execution, retry decisions, Skill execution, AI-provider access, or durable state owned by another component.

### 5.2 Workflow Engine

The deliverable SHALL define operations for starting, advancing, pausing, resuming, cancelling, and evaluating a result Event. It SHALL define Workflow Step dispatch, retry evaluation, and new-attempt creation without allowing resurrection of terminal Execution Attempts.

Every retry SHALL create a new `ExecutionId`, increment `AttemptNumber`, retain Workflow and correlation context, and use a new logical Command. Workflow Engine MUST NOT execute Skills or call AI providers.

### 5.3 Skill Runtime

The deliverable SHALL define operations for accepting one Execution Attempt, validating and preparing it, executing the approved Skill version, invoking approved Capability boundaries, cancelling an active attempt, and reporting normalized status or outcome.

Skill Runtime MUST NOT independently retry, modify retry policy, choose Workflow steps, mutate Workflow state, invoke undeclared Tools or Capabilities, or bypass Skill Registry and Capability Registry contracts.

### 5.4 AI Gateway

The deliverable SHALL define provider-neutral AI invocation acceptance, policy validation, provider/model selection within approved policy, invocation, bounded provider retry or failover, cancellation where contractually supported, normalized result reporting, and usage metadata.

`AIInvocationId` SHALL identify one provider-independent invocation. Provider formats and credentials MUST NOT cross the Gateway boundary. Detailed failure taxonomy is deferred to ES-007.

### 5.5 Memory Service

The deliverable SHALL define authorized store, fetch, query, supersede, and lifecycle-request boundaries only as supported by Domain v1.0. It SHALL define `MemoryId`, provenance, scope, classification, retention-policy reference, authorization, audit context, and untrusted-content handling.

The contract MUST NOT select vector search, embeddings, persistence, deletion mechanics, or a new memory architecture. Cross-Workspace access and instruction trust from retrieved content are prohibited.

### 5.6 Capability Registry

The deliverable SHALL use the exact canonical name `Capability Registry`. It SHALL define discovery, immutable contract-version lookup, requirement validation, eligible-implementation resolution, compatibility checks, and invocation handoff metadata using `CapabilityId` and `CapabilityContractVersionId`.

Capability Registry MUST NOT execute Skills, orchestrate Workflows, act as AI Gateway, or introduce a Capability Runtime. Actual invocation remains inside Skill Runtime's restricted context and routes to the approved implementation boundary.

## 6. Cross-Component Rules

The canonical deliverable SHALL define:

- directed Command dispatch with one accountable target;
- Event publication through Event Bus only after an authoritative fact exists;
- abstract synchronous or asynchronous completion semantics without choosing transport;
- immutable propagation of identity, authorization scope, correlation, causation, policy-version, and contract-version context;
- explicit authority retention or handoff at every boundary;
- component-local idempotency within the owned effect boundary;
- cancellation propagation without confusing cancellation with timeout;
- timeout ownership for ingress, Workflow waiting, Execution Attempt, AI Invocation, and Memory or Capability operation boundaries;
- Workflow Engine ownership of all Workflow retry decisions;
- tenant and Workspace validation at every trust boundary; and
- compatibility negotiation that fails deliberately rather than weakening semantics.

## 7. Result and Error Boundary

ES-006 SHALL define only these interface-level categories:

- accepted or rejected before authoritative work begins;
- completed successfully with a typed outcome;
- failed with normalized boundary context;
- cancelled; and
- timed out.

Operations MUST preserve the original cause and accountable boundary, MUST NOT report partial failure as success, and MUST NOT expose secrets. The canonical result envelope, error taxonomy, stable codes, retry classification, remediation, and provider-neutral failure model are deferred to ES-007.

## 8. Observability Boundary

Interfaces SHALL propagate applicable `RequestId`, `CorrelationId`, `CausationId`, `TenantId`, `WorkspaceId`, Workflow, step, Execution, AI Invocation, Capability contract, Skill version, Policy version, actor, timing, and authorization-decision context.

ES-006 MUST NOT define logging schemas, trace formats, metrics, audit-record structure, telemetry storage, retention, alerts, or dashboards. Those details are deferred to ES-008.

## 9. Versioning and Compatibility

The deliverable SHALL define:

- one explicit service-interface version for each operation contract;
- immutable interpretation of a published version;
- additive compatibility only when existing consumers may safely ignore optional, non-authoritative information and meaning is unchanged;
- a new major interface version for changed meaning, required input, authority, target, outcome, scope, or security semantics;
- producer and consumer declaration of supported versions;
- deliberate rejection of unsupported versions;
- owner, replacement, migration guidance, compatibility window, and removal criteria for deprecation; and
- exact Capability and other immutable contract-version negotiation without silent downgrade.

## 10. Security Requirements

The deliverable SHALL require:

- verified caller identity and authorization before authoritative work;
- least privilege and operation-specific authority;
- explicit, matching `TenantId` and `WorkspaceId` for scoped operations;
- trusted identity and authorization metadata supplied by approved boundaries, never arbitrary payload claims;
- preservation of immutable identifiers and contract versions;
- sensitive-data minimization and no credentials in payloads, Events, errors, or telemetry context;
- output validation before state transitions or consequential use; and
- protected, auditable evidence for decisions, approvals, denials, side effects, and cross-boundary access.

## 11. Required Mermaid Diagrams

The canonical deliverable SHALL include GitHub-compatible Mermaid diagrams for:

1. overall component interaction;
2. Request-to-Workflow handoff;
3. Workflow-to-Skill execution;
4. Skill-to-AI Gateway invocation;
5. Memory access;
6. Capability discovery, resolution, and invocation handoff;
7. cancellation propagation;
8. retry ownership and new-Execution creation; and
9. interface-version compatibility.

Diagrams MUST match prose and MUST NOT imply Commands through Event Bus, a new Capability Runtime, shared state ownership, provider leakage, transport products, or Skill Runtime retry decisions.

## 12. Non-Goals

ES-006 excludes:

- REST, HTTP, gRPC, RPC, routes, status codes, or transport protocols;
- programming languages, classes, code interfaces, frameworks, or SDKs;
- broker, queue, service-discovery, or serialization choices;
- database, schema, object-store, vector-store, or embedding choices;
- deployment topology, microservices, containers, or cloud providers;
- vendor-specific AI concepts;
- a new platform component or component rename;
- complete result, error, retry-classification, logging, tracing, metrics, audit-record, or telemetry standards; and
- product-specific Workflow, Skill, Capability, Memory, or AI payloads.

## 13. Governance and Traceability

The deliverable SHALL trace every interface to Architecture v1.0, Domain v1.0, and ES-001 through ES-005.

Changes to component ownership, authoritative decisions, canonical identities, Command targets, Event producers, retry ownership, scope, or cross-boundary responsibilities require the applicable architecture or domain review and ADR process before work continues. ES-006 MUST NOT silently resolve such conflicts.

## 14. Acceptance Criteria

- [ ] Both required documents exist and follow repository conventions.
- [ ] Only the six approved components receive interface contracts.
- [ ] `Capability Registry` is used consistently and no Capability Runtime is introduced.
- [ ] Every operation has one canonical name, caller, accountable target, input, outcome, preconditions, postconditions, idempotency rule, authorization, scope, trace, cancellation, timeout, and version rule.
- [ ] Manager alone owns Request acceptance or rejection and produces `RequestRejected`.
- [ ] `AcceptRequest` targets Manager only; Workflow start is a distinct Command to Workflow Engine.
- [ ] Workflow Engine alone owns Workflow state and retry decisions.
- [ ] Every retry creates a new `ExecutionId`, `AttemptNumber`, and logical Command.
- [ ] Skill Runtime owns retry-safe attempt execution and never invents retries.
- [ ] AI Gateway remains provider-neutral and owns `AIInvocationId` lifecycle.
- [ ] Memory Service enforces Tenant and Workspace isolation without selecting persistence.
- [ ] Capability Registry owns discovery, immutable contracts, resolution, and compatibility, not execution.
- [ ] Commands remain outside Event Bus and every Event has one authoritative producer.
- [ ] Result and Error semantics do not pre-empt ES-007.
- [ ] Observability semantics do not pre-empt ES-008.
- [ ] Versioning, compatibility, deprecation, and security requirements are testable.
- [ ] All nine Mermaid diagrams are valid and consistent with prose.
- [ ] Relative links resolve and `git diff --check` passes.
- [ ] Frozen baselines remain unchanged and no ADR is required.

## 15. Review Checklist

Reviewers SHALL verify:

- each responsibility and authoritative decision has one owner;
- no interface grants a caller authority merely because it presents an identifier;
- no public operation bypasses Command, Event, Skill, Capability, AI, Memory, or Workflow ownership;
- no operation conflates acknowledgement with completion;
- redelivery does not create a new logical Command, while Workflow retry does;
- terminal Execution Attempts remain immutable;
- cancellation and timeout remain distinct and late outcomes cannot overwrite terminal state;
- Capability resolution cannot weaken required policy or contract compatibility;
- AI Gateway does not acquire product logic or Workflow authority;
- Memory retrieval remains untrusted input and never crosses scope;
- unsupported versions fail deliberately without silent downgrade;
- provider, transport, persistence, language, framework, and deployment choices remain deferred; and
- all tables, prose, and Mermaid diagrams agree.

## 16. Definition of Done

- [ ] Only the two requested documentation files are created.
- [ ] Acceptance criteria and review checks pass.
- [ ] No frozen-boundary conflict requires an ADR.
- [ ] Validation evidence is recorded in a Draft Pull Request.
- [ ] The Draft Pull Request targets `main`, remains unmerged, and creates no tag or release.

## 17. Implementation Instructions

The Engineer SHALL work from current `main`, create `docs/es-006-service-interface-contracts`, change only the two ES-006 documentation files, validate the deliverables, commit with `docs: add ES-006 Service Interface Contracts`, push the branch, and open a Draft Pull Request with the same title.

If implementation would alter a frozen component name, ownership boundary, domain identity, Command target, Event producer, retry decision, or cross-boundary semantic, the Engineer MUST stop and report the conflict instead of creating an ADR or inventing a resolution.

Return to the [Engineering Specifications process](README.md).
