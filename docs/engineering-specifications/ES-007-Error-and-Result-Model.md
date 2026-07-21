---
title: ES-007 — Error and Result Model
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# ES-007 — Error and Result Model

## Document Metadata

| Field | Value |
| --- | --- |
| **Document ID** | ES-007 |
| **Milestone** | Milestone 4 — Platform Contracts & Service Interfaces, Phase 4 |
| **Priority** | Critical |
| **Implementer** | Engineer (Codex) |
| **Architecture baseline** | Architecture v1.0, frozen at tag `architecture-v1.0` |
| **Domain baseline** | Domain v1.0, frozen at tag `domain-v1.0` |
| **Contract baselines** | ES-004, ES-005, and ES-006, frozen at their respective `contracts-v1.0` tags |
| **Architecture status** | Conforms; no ownership, identity, lifecycle, messaging, retry, or provider boundary changes. |

## Related Documents

| Relationship | Document |
| --- | --- |
| **Architecture** | [Engineering Blueprint](../03-architecture/EngineeringBlueprint.md), [System Architecture](../03-architecture/SystemArchitecture.md), and [Execution Flow](../architecture/ExecutionFlow.md) |
| **Domain** | [Domain Model](../architecture/DomainModel.md) |
| **Prior specifications** | [ES-001](ES-001-Execution-Core.md), [ES-002](ES-002-Execution-Flow-Architecture.md), [ES-003](ES-003-Domain-Model-and-Ubiquitous-Language.md), [ES-004](ES-004-Command-Contract-Model.md), [ES-005](ES-005-Event-Contract-Model.md), and [ES-006](ES-006-Service-Interface-Contracts.md) |
| **Messaging contracts** | [Command Contract](../architecture/CommandContract.md) and [Event Contract](../architecture/EventContract.md) |
| **Canonical deliverable** | [Error and Result Model](../architecture/ErrorResultModel.md) |
| **ADRs** | None required; this specification refines outcome representation without changing frozen semantics. |
| **Future specification** | ES-008 Observability Model is pending. |
| **Related Pull Requests** | Pending — update after Draft Pull Request creation. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-21 | CTO / Architect | Initial Milestone 4 Phase 4 specification. |

## 1. Objective

ES-007 SHALL define the canonical, implementation-neutral representation of successful outcomes, failures, rejection, cancellation, timeout, partial completion, and retry classification across AIEOS.

This specification defines how outcomes are represented. It MUST NOT change who owns Workflows, Execution Attempts, retries, Commands, Events, service responsibilities, or provider abstraction.

## 2. Frozen Constraints

1. Workflow Engine alone owns Workflow state, retry decisions, retry policy, and creation of every new `ExecutionId`.
2. Skill Runtime owns one retry-safe Execution Attempt and MUST NOT initiate a retry.
3. Capability Registry is the canonical capability component and MUST NOT initiate retries or execute Skills.
4. AI Gateway may perform bounded provider-level retry under approved policy but MUST NOT create Workflow retries.
5. A terminal Execution Attempt is immutable and MUST NOT be resurrected.
6. Commands express intent and have one accountable target; Events record immutable facts and have one authoritative producer.
7. Event Bus transports Events only.
8. `CausationId` identifies a Command, Event, or Recorded Decision only. A Request is context and MUST NOT be a causation target.
9. Acknowledgement or acceptance is distinct from terminal completion.
10. Architecture v1.0, Domain v1.0, ES-004, ES-005, and ES-006 remain authoritative.

## 3. Scope

ES-007 SHALL define:

- canonical Result and Error envelopes;
- status, outcome, taxonomy, severity, and retry-classification semantics;
- rejection, execution failure, cancellation, timeout, and partial-success distinctions;
- component-specific normalization rules;
- provider-neutral AI, Memory Service, and Capability Registry failures;
- correlation, causation, lineage, security, privacy, versioning, and compatibility rules;
- the relationship of Result and Error representations to Commands and Events;
- minimum observability propagation with detailed telemetry deferred to ES-008;
- required Mermaid diagrams; and
- testable acceptance and governance criteria.

## 4. Canonical Result Contract

The canonical Result SHALL contain:

| Field | Requirement | Semantics |
| --- | --- | --- |
| `ResultId` | Required | Immutable identity of exactly one acknowledgement, progress, or terminal outcome record. A later record MUST use a new `ResultId`. |
| `ResultStatus` | Required | Canonical status defined by this specification. |
| `OutcomeCategory` | Required | Success, Failure, Rejection, Cancellation, Timeout, PartialSuccess, Progress, or Acknowledgement. |
| `SubjectReference` | Required | Typed identity of the Command, operation, Workflow, step, Execution Attempt, AI Invocation, Memory, or Capability operation described. |
| `TenantId` | Required when scoped | Verified Tenant scope; never inferred from payload. |
| `WorkspaceId` | Required when scoped | Verified Workspace scope; MUST agree with Tenant scope. |
| `CorrelationId` | Required | Stable correlation across the logical work. |
| `CausationId` | Required | Command, Event, or Recorded Decision that caused this Result. |
| `CommandId` | Conditional | Originating Command when the Result answers a Command. |
| `EventId` | Conditional | Originating Event when evaluation of an Event produced the Result. |
| `ProducerComponent` | Required | Canonical component that authoritatively produced the Result. |
| `StartedAt` | Conditional | Start of authoritative work; absent for rejection before work starts. |
| `CompletedAt` | Required for terminal Result | Time the terminal disposition became authoritative. |
| `ValueReference` | Conditional | Typed value or Artifact reference for successful content; minimized and scope-safe. |
| `ErrorId` | Required for `Rejected`, `Failed`, `Cancelled`, and `TimedOut`; conditional for `PartiallySucceeded` | Reference to one canonical Error. Partial-success rules define when an aggregate Error is permitted. |
| `Warnings` | Optional | Non-fatal structured warnings that do not conceal failure. |
| `Metadata` | Optional | Minimized, non-authoritative context; no secrets or credentials. |
| `ContractVersion` | Required | Version used to interpret the envelope. |
| `PredecessorResultId` | Conditional | Immediately preceding Result for the same subject when an acknowledgement or progress record is followed by another immutable Result. |
| `ParentResultId` | Conditional | Aggregate Result that owns this child Result, or the parent Result for a derived sub-operation. |
| `ChildResultReferences` | Required for `PartiallySucceeded` | Complete, immutable references to every direct child Result represented by the aggregate disposition. |
| `DispositionCounts` | Required for `PartiallySucceeded` | Counts for `Total`, `Succeeded`, `Failed`, `Cancelled`, `TimedOut`, `Rejected`, and `PartiallySucceeded`. |

Result invariants:

- `ResultId`, scope, subject, producer, identity lineage, and contract version are immutable after creation.
- A Result record never changes status. Acknowledgement, optional progress, and terminal completion are distinct immutable Results with distinct `ResultId` values.
- Results for one subject MAY form a lineage through `PredecessorResultId`; all records retain the same subject and correlation context.
- A terminal Result has exactly one terminal status.
- A `Rejected`, `Failed`, `Cancelled`, or `TimedOut` Result references an Error. A `PartiallySucceeded` Result follows the aggregate Error rules in Section 10.
- A successful Result MUST NOT contain a hidden unsuccessful sub-operation unless its status is `PartiallySucceeded`.
- Acknowledgement MUST NOT be represented as terminal completion.
- A Result is not a Command or Event and does not create a new transport category.

## 5. Result Status Model

Canonical statuses are:

| Status | Terminal | Meaning |
| --- | --- | --- |
| `Accepted` | No | The target accepted responsibility; authoritative work may not have completed. |
| `InProgress` | No | Work is active when an interface explicitly exposes progress. |
| `Succeeded` | Yes | All required effects completed and validated. |
| `PartiallySucceeded` | Yes | A declared aggregate permits partial completion and item outcomes expose every failure. |
| `Rejected` | Yes | Work was not accepted and execution did not begin. |
| `Failed` | Yes | Accepted work began but could not complete successfully. |
| `Cancelled` | Yes | Accepted work reached an authoritative cancellation disposition. |
| `TimedOut` | Yes | The owning timeout boundary declared terminal expiration. |

An asynchronous AI acknowledgement returns `Accepted` with `AIInvocationId`; its later terminal completion is a distinct Result. A synchronous AI operation returns a terminal Result and MUST NOT label it an acknowledgement.

The canonical status-to-category mapping is exhaustive:

| `ResultStatus` | Required `OutcomeCategory` | Validity rule |
| --- | --- | --- |
| `Accepted` | `Acknowledgement` | Non-terminal acknowledgement only. |
| `InProgress` | `Progress` | Non-terminal progress record only when the interface declares progress support. |
| `Succeeded` | `Success` | Terminal; no unsuccessful child outcome. |
| `PartiallySucceeded` | `PartialSuccess` | Terminal aggregate with explicit child Results and disposition counts. |
| `Rejected` | `Rejection` | Terminal; authoritative execution did not begin. |
| `Failed` | `Failure` | Terminal; accepted execution began and failed. |
| `Cancelled` | `Cancellation` | Terminal cancellation disposition. |
| `TimedOut` | `Timeout` | Terminal timeout disposition. |

All other `ResultStatus` and `OutcomeCategory` combinations are invalid and MUST be rejected during contract validation. `Progress` is a canonical category only for `InProgress`; it MUST NOT be used for acknowledgement or completion.

## 6. Canonical Error Contract

The canonical Error SHALL contain:

- immutable `ErrorId`;
- stable `ErrorCode`;
- `ErrorCategory`;
- `ErrorSeverity`;
- advisory `RetryClassification`;
- safe human-facing `Message`;
- optional restricted `DiagnosticReference`;
- canonical `OriginatingComponent`;
- typed `AffectedSubject`;
- verified `TenantId` and `WorkspaceId` when scoped;
- `CorrelationId` and valid `CausationId`;
- `OccurredAt`;
- optional minimized opaque `ExternalErrorReference`;
- optional `ParentErrorId` and `RootErrorId`;
- immutable `ContractVersion`; and
- minimized metadata.

Errors are immutable facts about an unsuccessful boundary outcome. A component may wrap an upstream Error with a new `ErrorId`, but MUST preserve root-cause linkage and MUST NOT erase the original category, cause, or scope.

## 7. Error Taxonomy

| Category | Applies when |
| --- | --- |
| `Validation` | Contract shape, semantic input, or invariant validation fails before execution. |
| `Authentication` | Verified caller identity is absent or invalid. |
| `Authorization` | Verified identity lacks required authority or scope. |
| `NotFound` | An authorized lookup cannot find the requested canonical subject. |
| `Conflict` | Requested change conflicts with current authoritative state. |
| `Concurrency` | Version or concurrency precondition fails. |
| `RateLimit` | An approved boundary refuses work due to request rate. |
| `Quota` | A configured usage entitlement or capacity quota is exhausted. |
| `DependencyUnavailable` | A required dependency cannot currently be reached or selected. |
| `DependencyFailure` | A dependency returns a normalized unsuccessful outcome. |
| `Timeout` | The accountable timeout boundary reaches its allowed duration. |
| `Cancellation` | Work ends because cancellation became authoritative. |
| `PolicyViolation` | Approved policy prohibits the requested action or result. |
| `UnsupportedCapability` | No approved Capability satisfies the requirement. |
| `CapabilityCompatibility` | Capability contract version or constraints are incompatible. |
| `MemoryRead` | An authorized Memory read or query fails. |
| `MemoryWrite` | An authorized Memory store, supersede, or lifecycle request fails. |
| `AIProviderUnavailable` | No eligible AI provider is available under approved policy. |
| `AIProviderRejected` | Provider-neutral invocation is rejected for a non-content-policy reason. |
| `AIContentPolicy` | Safety or content policy rejects the request or response. |
| `AIInvalidResponse` | Provider output is malformed, incomplete, or fails required validation. |
| `WorkflowState` | Requested Workflow or step transition is invalid. |
| `ExecutionFailure` | A Skill Execution Attempt fails without a more specific category. |
| `InternalInvariant` | A platform invariant is violated. |
| `Unknown` | Classification is impossible after safe normalization. |

Taxonomy codes MUST remain provider-neutral and stable. Components SHOULD choose the most specific valid category.

## 8. Retry Classification

Canonical advisory classifications are:

| Classification | Canonical meaning |
| --- | --- |
| `NeverRetry` | Repeating the same logical operation under unchanged inputs and policy cannot produce an acceptable outcome. |
| `Retryable` | A new Workflow attempt may be considered immediately; no minimum delay or external prerequisite is asserted. |
| `RetryableAfterDelay` | A new Workflow attempt may be considered only after the policy-selected delay or backoff requirement is satisfied. |
| `RetryableAfterCondition` | A new Workflow attempt may be considered only after a named, observable prerequisite changes or becomes true. |
| `RequiresPolicyEvaluation` | The producing boundary cannot safely classify retryability without Workflow Engine policy and context; this is not permission to retry. |

Canonical severities are:

| `ErrorSeverity` | Canonical meaning |
| --- | --- |
| `Informational` | The unsuccessful outcome is recorded for completeness but does not threaten integrity, security, or availability beyond the affected operation. |
| `Warning` | The affected operation is unsuccessful or degraded and requires attention, but platform integrity and broader service availability remain intact. |
| `Error` | The operation cannot complete and requires caller, policy, or operational handling within its current scope. |
| `Critical` | The failure threatens security, data integrity, isolation, or sustained availability and requires immediate escalation under approved policy. |

`ErrorSeverity` MUST describe impact, not retryability. `RetryClassification` MUST describe retry evidence, not operational urgency. Both fields MUST contain exactly one canonical value. Unknown values MUST fail strict producer validation; a compatible consumer MAY preserve an unknown additive value but MUST normalize handling conservatively without treating it as retry permission. `Critical` MUST NOT imply retry, and `Retryable` MUST NOT reduce severity.

Retry classification is evidence supplied to Workflow Engine policy; it is never authority to retry. Workflow Engine alone decides whether a new Workflow attempt is allowed and creates its new `ExecutionId`, `AttemptNumber`, and logical Command. Skill Runtime and Capability Registry never initiate retries. AI Gateway retry is bounded inside one AI Invocation and one Workflow Execution Attempt. Terminal attempts remain immutable.

## 9. Cancellation and Timeout

Cancellation has four observable decisions: requested, accepted, completed, and not possible because work is terminal or cannot be cancelled. Cancellation request and terminal `Cancelled` Result are distinct.

Timeout detection belongs to the boundary defined in ES-006; reporting uses `TimedOut`. Timeout may trigger downstream cancellation, but timeout and cancellation remain distinct. A late completion MUST NOT overwrite terminal state. Reconciliation requires an approved future policy and preserves both outcomes.

If completion and cancellation race, the component that owns the affected lifecycle performs one atomic authoritative transition. The losing observation is recorded without replacing the terminal disposition.

## 10. Partial Success

`PartiallySucceeded` is valid only for a contract that explicitly declares aggregate partial completion. It requires:

- complete `ChildResultReferences` to immutable direct child Results;
- `DispositionCounts` containing `Total`, `Succeeded`, `Failed`, `Cancelled`, `TimedOut`, `Rejected`, and `PartiallySucceeded`;
- at least one success and one unsuccessful outcome;
- an Error for every unsuccessful subset;
- no claim that required atomic effects succeeded;
- immutable audit lineage; and
- Workflow Engine policy evaluation before retrying a failed subset.

Retrying a subset creates new Execution identities where a Workflow retry is involved. A plain `Succeeded` Result MUST NOT hide partial failure.

Partial-success invariants are:

- `Total` MUST equal the number of `ChildResultReferences` and the sum of all disposition counts.
- `Succeeded` MUST be greater than zero, and the sum of all non-success disposition counts MUST be greater than zero.
- Every child reference MUST resolve to a terminal Result in the same Tenant and Workspace scope and under the aggregate subject or declared sub-operation lineage.
- A child `PartiallySucceeded` Result counts once in the aggregate `PartiallySucceeded` disposition; its descendants MUST NOT be double-counted by the parent.
- `ValueReference` MAY describe only successful aggregate output and MUST NOT contain or conceal child failure data.
- Every unsuccessful child MUST expose its own `ErrorId` according to its terminal status.
- The top-level `ErrorId` is optional for `PartiallySucceeded`. When present, it MUST reference an aggregate Error that summarizes aggregate-level impact and links to child Errors without replacing them. It MUST NOT be the only representation of child failures.
- The top-level `ErrorId` is prohibited when no aggregate-level Error exists; metadata and warnings MUST NOT substitute for child Errors.

## 11. Rejection Versus Failure

- Rejection occurs before authoritative execution begins.
- Validation, authentication, authorization, unsupported-version, or policy checks may cause rejection.
- Failure occurs after acceptance and execution start.
- Manager alone owns Request acceptance or rejection and authoritatively produces `RequestRejected`.
- Workflow-level refusal or failure uses its own existing Workflow semantics and MUST NOT impersonate `RequestRejected`.
- A rejected Command remains distinct from the Event that records its rejection.

## 12. Component Normalization Requirements

| Component | Required normalization |
| --- | --- |
| **Manager** | Normalize Request validation, authentication, authorization, and acceptance outcomes; preserve Request context; produce safe caller-facing detail; remain sole owner of `RequestRejected`. |
| **Workflow Engine** | Normalize invalid transitions, Workflow failures, retry-policy dispositions, cancellation, timeout, and step outcomes; preserve prior attempt Error lineage; alone decide retry. |
| **Skill Runtime** | Normalize validation, execution, Capability invocation, cancellation, and timeout for one attempt; preserve upstream causes; never create a retry or mutate Workflow state. |
| **AI Gateway** | Translate provider-specific outcomes into provider-neutral AI categories; isolate credentials and formats; preserve `AIInvocationId`; never create Workflow retries. |
| **Memory Service** | Normalize not-found, conflict/version mismatch, read, write, authorization, and scope failures without exposing persistence details. |
| **Capability Registry** | Normalize unsupported Capability, contract-version incompatibility, unavailable implementation, and pre-invocation validation; never execute Skills or initiate retries. |

Every component propagates scope, correlation, causation, and root cause; minimizes detail; and never upgrades an upstream failure to success.

## 13. Provider-Neutral AI Failures

AI Gateway SHALL normalize provider unavailable, rate limit, quota, rejected request, content-policy rejection, timeout, malformed response, transient dependency failure, and non-retryable request error. Provider names, SDK types, raw payloads, credentials, and sensitive policy detail MUST NOT cross the boundary. An optional opaque provider reference MAY be retained only in an authorized diagnostic boundary.

## 14. Memory and Capability Failures

Memory semantics include `NotFound`, version or concurrency `Conflict`, `MemoryRead`, `MemoryWrite`, authorization, and scope failure.

Capability Registry semantics include `UnsupportedCapability`, `CapabilityCompatibility`, `DependencyUnavailable` for no currently eligible implementation, and `Validation` before invocation. Capability resolution does not execute the Capability and does not duplicate Skill Runtime ownership.

## 15. Identity, Correlation, Causation, and Lineage

- `ResultId` identifies one canonical outcome record.
- `ErrorId` identifies one canonical Error.
- `CorrelationId` remains stable across the logical work and retries.
- `CausationId` references only a Command, Event, or Recorded Decision.
- `RequestId` is context only and MUST NOT be a causation target.
- Parent and child Results use explicit Result references without changing identity.
- Wrapped Errors use `ParentErrorId` and `RootErrorId`.
- Each Workflow retry creates a new `ExecutionId`; lineage references the prior attempt and the Recorded Decision or Event that caused the new Command.
- Prior terminal Results and Errors remain immutable and auditable.

## 16. Security, Privacy, and Compatibility

Human-facing messages MUST be safe, minimal, and non-sensitive. Secrets, credentials, tokens, raw provider payloads, cross-Workspace data, and unrestricted diagnostic detail are prohibited. Trusted diagnostics require an authorized boundary and redaction. Tenant and Workspace scope are validated at every boundary.

Result and Error contracts are independently versioned. Additive optional fields are compatible only when meaning and authority remain unchanged. Breaking semantic, required-field, taxonomy-code, identity, or security changes require a new major version. Consumers MUST tolerate unknown additive fields and safely handle unknown categories as `Unknown` without treating them as success. Deprecated codes retain documented interpretation through their compatibility window.

## 17. Commands, Events, and Observability Boundary

Commands express intent. Events record facts. Results and Errors are canonical outcome representations used by service contracts and referenced by lifecycle Events where appropriate. They are not a new transport category, MUST NOT bypass Command/Event boundaries, and MUST NOT cause Event Bus to carry Commands or arbitrary Result traffic.

ES-007 defines only the identity, scope, time, producer, lineage, and minimized context required for propagation. Logging schemas, spans, metrics, audit-record schema, operational telemetry, health signals, storage, retention, alerts, and dashboards are deferred to ES-008.

## 18. Required Diagrams

The canonical deliverable SHALL include valid Mermaid diagrams for:

1. acknowledgement to terminal Result;
2. rejection versus execution failure;
3. retry classification feeding Workflow Engine policy;
4. retry creation of a new `ExecutionId`;
5. cancellation race handling;
6. timeout and late completion;
7. AI provider error normalization;
8. partial-success aggregation; and
9. Error causation and root-cause lineage.

## 19. Non-Goals

ES-007 excludes REST or HTTP status mappings, programming-language exceptions, framework error types, transport serialization, database schemas, broker configuration, vendor SDK error classes, retry scheduling implementation, logging/tracing/metrics implementation, UI copy, deployment topology, new components, and changes to frozen ownership or lifecycle semantics.

## 20. Governance and Traceability

This specification traces to Architecture v1.0, Domain v1.0, and ES-001 through ES-006. Changes to component ownership, Command targets, Event producers, retry authority, canonical identities, lifecycle semantics, scope, or provider abstraction require the applicable architecture/domain review and ADR process.

## 21. Acceptance Criteria

- [ ] Both requested documents exist and follow repository conventions.
- [ ] Result and Error fields, requiredness, immutability, and invariants are explicit.
- [ ] Acknowledgement, terminal completion, rejection, failure, cancellation, timeout, and partial success are distinct.
- [ ] Acknowledgement, progress, and terminal completion are distinct immutable Results with distinct `ResultId` values and explicit lineage.
- [ ] Every `ResultStatus` maps to exactly one valid `OutcomeCategory`; invalid combinations are rejected.
- [ ] Every `Rejected`, `Failed`, `Cancelled`, or `TimedOut` Result references a canonical Error; `PartiallySucceeded` follows its explicit aggregate Error rule.
- [ ] Every `ErrorSeverity` and `RetryClassification` value has provider-neutral semantics and validation rules; classification remains advisory only.
- [ ] Partial success exposes complete child Result references, consistent disposition counts, child Errors, and explicit top-level Error rules.
- [ ] Taxonomy is stable and provider-neutral.
- [ ] `CausationId` permits only Command, Event, or Recorded Decision; `RequestId` remains context.
- [ ] Workflow Engine alone owns retry decisions and every Workflow retry creates a new `ExecutionId`.
- [ ] Terminal attempts are never resurrected.
- [ ] Skill Runtime and Capability Registry never initiate retries.
- [ ] AI Gateway provider retry remains bounded within one Invocation and attempt.
- [ ] Manager and `RequestRejected` ownership remain unchanged.
- [ ] Result/Error representation does not bypass Command/Event contracts.
- [ ] Tenant and Workspace isolation and redaction are explicit.
- [ ] ES-008 is not pre-empted.
- [ ] All Mermaid diagrams match prose and parse.
- [ ] Relative links resolve and `git diff --check` passes.
- [ ] Frozen baselines remain unchanged and no ADR is required.

## 22. Review Checklist

Reviewers SHALL verify that no outcome status revives terminal work; retry classification is advisory; partial success exposes every failure; upstream causes survive normalization; producer and scope are unambiguous; timestamps match lifecycle semantics; provider details do not leak; unknown versions fail safely; Command/Event boundaries remain intact; and every diagram agrees with the contract.

## 23. Definition of Done and Implementation Instructions

The Engineer SHALL work from current `main`, create branch `docs/es-007-error-result-model`, create only the two ES-007 documents, validate them, commit with `docs: add ES-007 Error and Result Model`, push, and open an unmerged Draft Pull Request with the same title.

The Pull Request SHALL record scope, files, validation, frozen-baseline preservation, canonical `Capability Registry` naming, and ES-008 deferrals. No tag or release is authorized.

If the work requires a frozen-boundary or domain-semantic change, the Engineer MUST stop and report the conflict rather than inventing a resolution or ADR.

Return to the [Engineering Specifications process](README.md).
