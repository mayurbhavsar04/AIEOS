---
title: Error and Result Model
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# Error and Result Model

## 1. Purpose

AIEOS uses one canonical outcome model so every component communicates acceptance, success, rejection, failure, cancellation, timeout, and partial completion without leaking local implementation or provider semantics.

This document defines representation and normalization, not authority. Workflow Engine owns Workflow state and retry decisions; Skill Runtime owns one Execution Attempt; AI Gateway owns provider isolation; Manager owns Request rejection; Memory Service owns Memory; and Capability Registry owns Capability contracts and resolution.

Results and Errors are typed service-contract representations. They are not Commands, Events, transports, or state stores. Commands express intent; Events record facts; Results describe outcomes; Errors describe unsuccessful outcomes and preserved causes.

Related sources are [ES-007](../engineering-specifications/ES-007-Error-and-Result-Model.md), [Service Interfaces](ServiceInterfaces.md), [Command Contract](CommandContract.md), [Event Contract](EventContract.md), [Execution Flow](ExecutionFlow.md), and [Domain Model](DomainModel.md).

## 2. Canonical Result Envelope

| Field | Requirement | Rule |
| --- | --- | --- |
| `ResultId` | Required | Immutable identity of one outcome record. |
| `ResultStatus` | Required | One canonical status. |
| `OutcomeCategory` | Required | Acknowledgement, Success, PartialSuccess, Rejection, Failure, Cancellation, or Timeout. |
| `SubjectReference` | Required | Typed reference to the affected operation or subject. |
| `TenantId` | Required when scoped | Verified scope, never a payload claim. |
| `WorkspaceId` | Required when scoped | Verified scope consistent with Tenant. |
| `CorrelationId` | Required | Stable across the logical work. |
| `CausationId` | Required | Command, Event, or Recorded Decision only. |
| `CommandId` | Conditional | Originating Command when applicable. |
| `EventId` | Conditional | Originating Event when applicable. |
| `ProducerComponent` | Required | Canonical component producing the Result. |
| `StartedAt` | Conditional | Absent for rejection before execution. |
| `CompletedAt` | Required when terminal | Time terminal disposition became authoritative. |
| `ValueReference` | Conditional | Typed value or Artifact reference. |
| `ErrorId` | Required when unsuccessful | Canonical Error reference. |
| `Warnings` | Optional | Structured non-fatal warnings. |
| `Metadata` | Optional | Minimized, non-authoritative context. |
| `ContractVersion` | Required | Result contract interpretation version. |

Identity, subject, producer, scope, lineage, and contract version are immutable. A terminal Result has one terminal status. An unsuccessful terminal Result references one Error. `Succeeded` means all required effects completed and validated. `PartiallySucceeded` exposes every unsuccessful subset. `Accepted` and `InProgress` are non-terminal.

## 3. Result Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Accepted: responsibility accepted
    Accepted --> InProgress: work starts
    Accepted --> Cancelled: cancellation completes first
    Accepted --> TimedOut: boundary expires
    InProgress --> Succeeded
    InProgress --> PartiallySucceeded
    InProgress --> Failed
    InProgress --> Cancelled
    InProgress --> TimedOut
    Succeeded --> [*]
    PartiallySucceeded --> [*]
    Failed --> [*]
    Cancelled --> [*]
    TimedOut --> [*]
```

Rejection precedes execution:

```mermaid
flowchart LR
    INPUT["Command or Request"] --> VALIDATE{"Accepted?"}
    VALIDATE -->|No| REJECTED["Rejected Result<br/>no execution started"]
    VALIDATE -->|Yes| START["StartedAt recorded"]
    START --> EXECUTE["Execution"]
    EXECUTE --> SUCCESS["Succeeded"]
    EXECUTE --> FAILURE["Failed"]
```

An asynchronous AI invocation returns `Accepted` with `AIInvocationId`; terminal completion arrives separately. A synchronous invocation returns a terminal Result and MUST NOT call it an acknowledgement.

## 4. Canonical Error Envelope

| Field | Requirement | Rule |
| --- | --- | --- |
| `ErrorId` | Required | Immutable identity of one Error. |
| `ErrorCode` | Required | Stable machine-oriented code. |
| `ErrorCategory` | Required | Provider-neutral canonical category. |
| `ErrorSeverity` | Required | Impact classification, not retry authority. |
| `RetryClassification` | Required | Advisory evidence for Workflow Engine policy. |
| `Message` | Required | Safe human-facing summary. |
| `DiagnosticReference` | Optional | Restricted reference, not raw sensitive detail. |
| `OriginatingComponent` | Required | Canonical origin boundary. |
| `AffectedSubject` | Required | Typed subject reference. |
| `TenantId` / `WorkspaceId` | Required when scoped | Verified isolation context. |
| `CorrelationId` / `CausationId` | Required | Valid lineage; Request is not causation. |
| `OccurredAt` | Required | When the unsuccessful fact became known. |
| `ExternalErrorReference` | Optional | Opaque, minimized diagnostic reference. |
| `ParentErrorId` / `RootErrorId` | Optional | Preserved cause chain. |
| `ContractVersion` | Required | Error contract interpretation version. |

Errors are immutable. Wrapping adds context with a new `ErrorId`, preserves root-cause linkage, and never mutates or erases the upstream Error.

## 5. Error Taxonomy

| Family | Categories |
| --- | --- |
| Boundary | `Validation`, `Authentication`, `Authorization`, `NotFound`, `Conflict`, `Concurrency`, `PolicyViolation` |
| Capacity and dependency | `RateLimit`, `Quota`, `DependencyUnavailable`, `DependencyFailure`, `Timeout`, `Cancellation` |
| Capability | `UnsupportedCapability`, `CapabilityCompatibility` |
| Memory | `MemoryRead`, `MemoryWrite` |
| AI | `AIProviderUnavailable`, `AIProviderRejected`, `AIContentPolicy`, `AIInvalidResponse` |
| Execution and integrity | `WorkflowState`, `ExecutionFailure`, `InternalInvariant`, `Unknown` |

The most specific category applies. Names never include providers, SDK types, protocols, databases, or frameworks. `NotFound` applies only after authorized lookup. `Conflict` describes authoritative-state conflict; `Concurrency` describes a failed version/concurrency precondition. `DependencyUnavailable` means a dependency cannot currently serve; `DependencyFailure` means it returned an unsuccessful outcome.

## 6. Retry Classification and New Attempts

Classifications are `NeverRetry`, `Retryable`, `RetryableAfterDelay`, `RetryableAfterCondition`, and `RequiresPolicyEvaluation`. They are evidence, never authority.

```mermaid
flowchart LR
    OUTCOME["Terminal attempt Result"] --> CLASSIFY["Advisory retry classification"]
    CLASSIFY --> POLICY["Workflow Engine evaluates policy"]
    POLICY -->|No retry| TERMINAL["Terminal step disposition"]
    POLICY -->|Retry allowed| DECISION["Recorded retry decision"]
    DECISION --> COMMAND["New Command"]
    COMMAND --> ATTEMPT["New ExecutionId<br/>AttemptNumber + 1"]
```

```mermaid
sequenceDiagram
    participant Runtime as Skill Runtime
    participant Bus as Event Bus
    participant Workflow as Workflow Engine
    Runtime-->>Bus: AttemptFailed Event references ResultId and ErrorId
    Bus-->>Workflow: Deliver immutable Event
    Workflow->>Workflow: Evaluate policy and classification
    alt retry allowed
        Workflow->>Workflow: Record retry decision
        Workflow->>Runtime: New Command with new ExecutionId
    else retry not allowed
        Workflow->>Workflow: Set terminal step disposition
    end
```

Workflow Engine alone creates the new attempt. Skill Runtime and Capability Registry never initiate retries. AI Gateway provider retry stays inside one `AIInvocationId` and one Execution Attempt. A terminal attempt is never resurrected.

## 7. Cancellation

Cancellation request, acceptance, and terminal completion are separate.

```mermaid
stateDiagram-v2
    [*] --> Running
    Running --> CancellationRequested
    CancellationRequested --> CancellationAccepted
    CancellationRequested --> CancellationNotPossible
    CancellationAccepted --> Cancelled
    Running --> Succeeded: completion wins race
    CancellationRequested --> Succeeded: completion already authoritative
    CancellationNotPossible --> Succeeded: already completed
    Cancelled --> [*]
    Succeeded --> [*]
```

The lifecycle owner makes one atomic terminal transition. The losing observation remains auditable but cannot replace terminal state. Cancellation propagates only through approved interfaces.

## 8. Timeout and Late Completion

```mermaid
sequenceDiagram
    participant Owner as Timeout Boundary Owner
    participant Work as Downstream Work
    participant State as Authoritative Lifecycle
    Owner->>Work: Begin bounded operation
    alt completed before deadline
        Work-->>Owner: Terminal outcome
        Owner->>State: Commit terminal Result
    else deadline expires
        Owner->>State: Commit TimedOut Result
        Owner-->>Work: Request cancellation where supported
        Work-->>Owner: Late completion
        Owner->>State: Preserve TimedOut; record late observation
    end
```

Timeout detection belongs to the ES-006 boundary owner. `TimedOut` and `Cancelled` remain distinct. Late completion cannot resurrect an attempt or overwrite terminal Workflow state.

## 9. Partial Success

```mermaid
flowchart TB
    AGG["Declared aggregate operation"] --> I1["Item: Succeeded"]
    AGG --> I2["Item: Failed + ErrorId"]
    AGG --> I3["Item: Cancelled + ErrorId"]
    I1 --> SUMMARY["Aggregate: PartiallySucceeded"]
    I2 --> SUMMARY
    I3 --> SUMMARY
    SUMMARY --> AUDIT["Immutable lineage and counts"]
```

Partial success is valid only when the contract permits independent item outcomes. It requires at least one success and one unsuccessful child, exposes each disposition, and never claims atomic success. Workflow Engine decides whether failed subsets produce new attempts.

## 10. AI Provider Error Normalization

```mermaid
flowchart LR
    PROVIDER["Provider-specific outcome"] --> GATEWAY["AI Gateway"]
    GATEWAY --> UNAVAILABLE["AIProviderUnavailable"]
    GATEWAY --> LIMIT["RateLimit or Quota"]
    GATEWAY --> REJECTED["AIProviderRejected"]
    GATEWAY --> POLICY["AIContentPolicy"]
    GATEWAY --> TIMEOUT["Timeout"]
    GATEWAY --> INVALID["AIInvalidResponse"]
    GATEWAY --> DEPENDENCY["DependencyFailure"]
    UNAVAILABLE --> RESULT["Provider-neutral Result + Error"]
    LIMIT --> RESULT
    REJECTED --> RESULT
    POLICY --> RESULT
    TIMEOUT --> RESULT
    INVALID --> RESULT
    DEPENDENCY --> RESULT
```

AI Gateway preserves `AIInvocationId`, correlation, scope, and safe cause. Provider formats, SDK types, credentials, and raw payloads do not escape. An authorized diagnostic boundary may retain an opaque reference.

## 11. Memory and Capability Registry Outcomes

Memory Service normalizes `NotFound`, `Conflict` or `Concurrency`, `MemoryRead`, `MemoryWrite`, authentication, authorization, and validation failures without exposing persistence.

Capability Registry normalizes `UnsupportedCapability`, `CapabilityCompatibility`, `DependencyUnavailable` when no eligible implementation is available, and pre-handoff `Validation`. It does not execute Skills, invoke providers, or initiate retries.

## 12. Component Normalization

| Component | Own normalization | Must preserve |
| --- | --- | --- |
| **Manager** | Request validation, acceptance, rejection, and safe interaction outcomes | Request context and upstream Workflow outcome |
| **Workflow Engine** | Invalid transitions, Workflow/step disposition, retry-policy decision | Attempt lineage and prior Errors |
| **Skill Runtime** | Attempt validation, execution, timeout, cancellation, Capability outcome | Upstream cause and declared contract context |
| **AI Gateway** | Provider-neutral invocation and policy outcomes | `AIInvocationId`, usage context, safe cause |
| **Memory Service** | Memory authorization, scope, read, write, conflict | Memory identity and provenance |
| **Capability Registry** | Discovery, compatibility, availability, pre-handoff validation | Capability identity and contract version |

No component converts upstream failure to success or removes root-cause lineage.

## 13. Correlation, Causation, and Lineage

```mermaid
flowchart LR
    CMD["CommandId"] --> RES1["ResultId A"]
    RES1 --> ERR1["ErrorId A<br/>RootErrorId A"]
    ERR1 --> WRAP["ErrorId B<br/>ParentErrorId A<br/>RootErrorId A"]
    WRAP --> EVT["EventId references outcome"]
    EVT --> DEC["Recorded retry decision"]
    DEC --> CMD2["New CommandId"]
    CMD2 --> RES2["ResultId B<br/>new ExecutionId"]
```

`CorrelationId` remains stable. `CausationId` references only Command, Event, or Recorded Decision. `RequestId` is context only. New retries use new Command, Result, and Execution identities; earlier outcomes remain immutable.

## 14. Security, Privacy, and Compatibility

Human messages are safe and minimal. Secrets, credentials, tokens, raw provider payloads, cross-Workspace data, and unrestricted diagnostics are prohibited. Tenant and Workspace scope are verified at each boundary. Error chains cannot cross Workspace scope.

Result and Error contracts are independently versioned. Additive optional information is compatible only when meaning, authority, validation, and security remain unchanged. Changed required fields, identity/status/taxonomy meaning, scope, or security require a new major version. Unknown categories normalize to `Unknown`, never success.

## 15. Command and Event Relationship

```mermaid
flowchart LR
    COMMAND["Command<br/>intent"] --> TARGET["Accountable component"]
    TARGET --> RESULT["Result/Error<br/>outcome representation"]
    TARGET --> EVENT["Lifecycle Event<br/>immutable fact"]
    EVENT --> BUS["Event Bus<br/>Events only"]
    RESULT -. "referenced by" .-> EVENT
```

A Result is returned or retained through the service interface. An Event may reference it. Neither Result nor Error is an independent Event Bus message category or a path around directed Commands.

## 16. Observability Boundary

ES-007 propagates only outcome/error identity, subject, producer, scope, correlation, causation, lifecycle times, status/category, contract version, and minimized context. ES-008 defines logs, traces, spans, metrics, audit schemas, telemetry, health signals, storage, retention, alerts, and dashboards.

## 17. Architectural Invariants

1. Outcome representation does not transfer behavioral authority.
2. Workflow Engine alone decides Workflow retries.
3. Every Workflow retry creates a new `ExecutionId`.
4. Terminal Attempts and Results remain immutable.
5. Skill Runtime and Capability Registry never initiate retries.
6. AI Gateway never creates a Workflow retry.
7. Acknowledgement is not terminal completion.
8. Rejection precedes execution; failure follows acceptance.
9. Manager remains sole authoritative owner of `RequestRejected`.
10. `CausationId` references Command, Event, or Recorded Decision only; `RequestId` is context.
11. Event Bus transports Events only.
12. Results and Errors are not a new transport category.
13. Partial success never hides unsuccessful subsets.
14. Provider-specific formats do not escape AI Gateway.
15. Tenant and Workspace isolation applies to outcomes and cause chains.
16. Semantic deviations require applicable review and ADR governance.

## 18. Non-Goals and Review Checklist

This model does not define HTTP mappings, language exceptions, framework classes, serialization, persistence, brokers, vendor SDK errors, retry scheduling, telemetry implementation, UI copy, or deployment topology.

Reviewers verify field requiredness and immutability; non-overlapping statuses; Error references for unsuccessful terminal Results; advisory-only retry classification; safe cancellation/timeout races; explicit partial outcomes; provider-neutral normalization; preserved upstream cause and scope; intact Command/Event boundaries; ES-008 deferral; and diagram/prose consistency.
