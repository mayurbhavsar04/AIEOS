---
title: ES-002 — Execution Flow Architecture
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-20
---

# ES-002 — Execution Flow Architecture

## Document Metadata

| Field | Value |
| --- | --- |
| **Document ID** | ES-002 |
| **Title** | Execution Flow Architecture |
| **Status** | Draft |
| **Owner** | CTO / Architect |
| **Implementer** | Engineer |
| **Milestone** | 3B |
| **Priority** | Critical |
| **Repository** | `mayurbhavsar04/AIEOS` |
| **Architecture Status** | Conforms to Architecture v1.0; no architecture change or ADR is introduced |

## 1. Objective

ES-002 SHALL define the canonical execution model of AIEOS: how work enters the Platform; how the Manager and Workflow Engine coordinate it; how Workflows progress; how Skills and Capabilities are invoked; how AI providers are isolated; how Events, failures, waits, and terminal outcomes propagate; and how execution remains correlated, resumable, observable, and auditable.

This specification defines Platform behavior and ownership. It MUST NOT select implementation technology.

## 2. Related Documents

| Relationship | Artifact |
| --- | --- |
| **PRD** | Pending — no approved PRD authorizes product implementation under ES-002. |
| **Architecture Blueprint** | [Engineering Blueprint](../03-architecture/EngineeringBlueprint.md) and [System Architecture](../03-architecture/SystemArchitecture.md) |
| **Prior Specification** | [ES-001 — Execution Core](ES-001-Execution-Core.md) |
| **Canonical Architecture** | [Execution Flow](../architecture/ExecutionFlow.md) |
| **Future Component Specifications** | Planned — detailed specifications for in-scope components have not been assigned identifiers. |
| **Future ADRs** | None required by ES-002; any future deviation SHALL use an approved ADR. |
| **Related Pull Requests** | Pending — the Draft Pull Request created for this specification SHALL be recorded after creation. |

## 3. Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-20 | CTO / Architect | Initial draft. |

## 4. Background

Architecture v1.0 defines AIEOS components and their ownership. ES-002 defines their canonical runtime collaboration model without changing those boundaries. Every future AI Employee built on AIEOS MUST conform to this model unless an approved ADR and CTO approval authorize a deviation.

## 5. Scope

ES-002 covers:

- request, Workflow, Workflow-step, Skill execution-attempt, Capability, and AI invocation lifecycles;
- Workflow-step selection and command dispatch;
- Skill execution and normalized outcomes;
- Event publication and consumption;
- failure propagation, retry decisions, retry-safe execution, cancellation, and timeout handling;
- human interaction, pause, resume, completion, and follow-up;
- observability, correlation, causation, traceability, and security boundaries.

## 6. Out of Scope

ES-002 excludes implementation language, database selection, cloud provider, deployment architecture, authentication implementation, authorization implementation, user-interface implementation, product-specific Workflow logic, analytics implementation, scheduling implementation, and memory storage implementation. Future specifications MAY govern these concerns.

## 7. Design Principles

The execution model MUST be deterministic where Workflow definitions require determinism, event-driven for completed facts, observable, resumable, fault tolerant, provider agnostic, idempotent, composable, versioned, auditable, secure by boundary, and explicit about ownership.

## 8. Canonical Execution Flow

The canonical flow SHALL be:

```text
External Request
    ↓
Manager
    ↓
Workflow Engine
    ↓
Workflow Instance Created
    ↓
Next Workflow Step Selected
    ↓
Command Created
    ↓
Command Dispatched to Skill Runtime
    ↓
Skill Resolved
    ↓
Input Validated
    ↓
Execution Attempt Started
    ↓
Capability Required?
    ↓
Capability Resolved
    ↓
AI Required?
    ↓
AI Gateway
    ↓
Provider Adapter
    ↓
AI Provider
    ↓
Normalized Result
    ↓
Skill Result Produced
    ↓
Result Event Published
    ↓
Event Bus
    ↓
Workflow Engine Consumes Result Event
    ↓
Workflow State Updated
    ↓
Next Step, Waiting, Retry Decision, Completion, Cancellation, or Failure
    ↓
Manager
    ↓
Final Response or Follow-up Interaction
```

The Workflow Engine owns orchestration and durable Workflow state. The Skill Runtime owns a Skill execution attempt. Skills MUST NOT orchestrate other Skills or call AI providers directly. AI access MUST pass through the AI Gateway. Commands SHALL use an abstract command-dispatch contract and MUST NOT pass through the Event Bus. Events SHALL be published through the Event Bus. The Manager interprets goals and manages interaction but MUST NOT execute Skills. Every execution MUST be traceable through stable identifiers.

## 9. Request Lifecycle

| State | Meaning |
| --- | --- |
| **Received** | The Platform has observed the request. |
| **Accepted** | The request is admitted for validation or execution. |
| **Rejected** | The request cannot be admitted. Terminal. |
| **Validated** | Required request checks have passed. |
| **Running** | At least one authorized Workflow is progressing. |
| **Waiting** | External or human input is required. |
| **Paused** | Progress is deliberately suspended with state preserved. |
| **Completed** | The requested outcome is complete. Terminal. |
| **Cancelled** | Authorized cancellation ended the request. Terminal. |
| **Failed** | The request cannot complete successfully. Terminal. |

A request MUST have one current lifecycle state. Request state is distinct from Workflow state. Mapping one request to multiple Workflow instances MUST be explicitly authorized by a future specification.

## 10. Workflow Lifecycle

Workflow states are **Created**, **Running**, **Waiting**, **Paused**, **Retrying**, **Compensating**, **Completed**, **Cancelled**, and **Failed**. The Workflow Engine is the sole owner of Workflow state and SHALL validate every transition. Completed, Cancelled, and Failed are terminal. History MUST remain auditable. Resume SHALL continue from persisted execution state, not implicitly restart. Workflow definitions and instances SHALL be separately identified and versioned.

## 11. Workflow-Step Lifecycle

Workflow-step states are **Pending**, **Ready**, **Dispatched**, **Running**, **Waiting**, **Retrying**, **Succeeded**, **Failed**, **TimedOut**, **Cancelled**, **Skipped**, and **Compensated**. The Workflow Engine owns step state and evaluates every normalized execution-attempt outcome against the approved retry policy. When an attempt fails or times out and another attempt is permitted, the Workflow Engine transitions the step from Running to Retrying and creates a distinct new attempt before returning the step to Dispatched. When retry is not permitted, the Workflow Engine transitions the step to Failed or TimedOut. Succeeded, Failed, TimedOut, Cancelled, Skipped, and Compensated are terminal step dispositions only after the Workflow Engine determines that no further transition is allowed.

A Workflow step MUST NOT reuse or revive a failed or timed-out attempt. Every retry MUST create a new Execution ID and a monotonically increasing attempt number within the Workflow-step execution context. Correlation ID and Workflow ID MUST remain stable across retries. Causation ID MUST identify the Event or decision that created the new attempt. Prior failed and timed-out attempts remain immutable and auditable.

## 12. Skill Execution-Attempt Lifecycle

Attempt states are **Requested**, **Resolved**, **Validating**, **Ready**, **Executing**, **Succeeded**, **Failed**, **TimedOut**, and **Cancelled**. **Succeeded**, **Failed**, **TimedOut**, and **Cancelled** are terminal attempt states. A terminal attempt MUST NOT transition to Retrying or be revived. The Skill Registry resolves approved identity, version, contracts, Capabilities, and allowed Tools. The Skill Runtime owns attempt state, validates input before execution, and SHALL produce either a normalized success or normalized failure. Partial internal failure MUST NOT be reported as success. Retry is a Workflow-step decision that creates a distinct execution attempt; it is not an execution-attempt state transition.

## 13. Capability Invocation

A Capability is an abstract operation available to Skills. Skills declare required Capabilities. The Capability Registry resolves approved contracts and eligible implementations without product orchestration. A Capability MAY be implemented without AI; AI is one possible route. Every invocation MUST be correlated, observable, policy-constrained, and validated.

## 14. AI Invocation Lifecycle

AI invocation states are **Requested**, **PolicyValidated**, **ProviderSelected**, **Prepared**, **Invoked**, **Retrying**, **Succeeded**, **Failed**, **TimedOut**, and **Cancelled**.

The AI Gateway SHALL own provider selection, policy-approved model selection, the provider authentication boundary, request transformation, response normalization, usage accounting, provider-specific error translation, permitted provider retry and failover, and applicable safety or policy hooks. Skills MUST NOT contact providers. Provider formats MUST NOT escape the AI Gateway. The AI Gateway MUST NOT contain product business logic. Failover MUST preserve correlation and idempotency context.

## 15. Command Model

Commands represent requested actions. A Command SHALL have one accountable target and MAY be accepted or rejected. Commands are not historical facts and MAY produce zero or more Events. They SHALL be dispatched through an abstract command-dispatch contract, MUST NOT pass through the Event Bus, and SHALL carry Command ID, type and version, target, correlation, causation, idempotency, authorization context, payload metadata, and applicable time constraints. Transport details remain out of scope and SHALL be defined by a future shared standard.

## 16. Event Lifecycle

Every Event SHALL include or reference Event ID, Event Type, Event Version, timestamp, producer, subject, Correlation ID, Causation ID, Request ID, Workflow ID when applicable, Execution ID when applicable, payload, and metadata.

Events are immutable facts published through the Event Bus. They MUST NOT be disguised Commands or direct named components to act. Corrections SHALL use new Events. Consumers MUST tolerate duplicate delivery, and global ordering MUST NOT be assumed. A future Event Envelope standard SHALL define full details.

## 17. Failure Model and Propagation

The logical path SHALL be:

```text
Provider or Capability Failure
    ↓
AI Gateway or Capability Boundary
    ↓
Skill Runtime
    ↓
Workflow Engine
    ↓
Manager
    ↓
Calling Client or Human Operator
```

Each boundary MUST add context without destroying the original cause. Failures SHALL use normalized categories and MUST NOT be swallowed. A failure MAY be retriable, non-retriable, compensatable, or terminal. User-facing messages MUST protect secrets and sensitive internals. A future shared Error Model SHALL define details.

## 18. Retry Ownership

Retry decision ownership is not the same as retry-safe execution ownership.

| Layer | Ownership |
| --- | --- |
| **Workflow Engine** | Owns retry decisions, step policy, maximum attempts, Workflow-level backoff, the next disposition, and creation of a new attempt. |
| **Skill Runtime** | Safely executes the requested attempt, owns its lifecycle, timeout and cancellation handling, and returns a normalized outcome. It MUST NOT independently create another attempt or change policy. |
| **AI Gateway** | MAY perform policy-approved provider invocation retries that do not create Workflow attempts and preserve correlation and idempotency. |
| **Infrastructure** | MAY redeliver messages but MUST NOT make Workflow-level retry decisions. |
| **Calling client or ingress boundary** | Owns a new or repeated client request under its contract. |

Nested retry amplification MUST be prevented. Every retry SHALL be bounded. Exhaustion SHALL produce a normalized failure. A failed or timed-out attempt remains terminal and immutable; the Workflow Engine represents an approved retry by transitioning the Workflow step to Retrying and creating a new attempt with a new Execution ID and attempt number.

## 19. Idempotency

Every Command and execution request MUST have an idempotency strategy. Duplicate delivery MUST NOT cause uncontrolled duplicate effects. Components SHALL distinguish redelivery from a new logical request. Keys MUST be scoped and versioned appropriately. A future Idempotency standard SHALL define detailed rules.

## 20. Timeout and Cancellation

Cancellation is an explicit request to stop; timeout is failure to complete within an allowed duration. Cancellation SHOULD propagate downstream where supported. Terminal Events MUST distinguish failure, timeout, and cancellation. Late results MUST NOT overwrite terminal Workflow state without explicit reconciliation policy.

## 21. Human Interaction

Execution SHALL support waiting for approval, confirmation, missing input, authentication, review, and exception handling. Human waiting MUST preserve Workflow state. Responses MUST correlate to the correct Workflow and step, and decisions MUST be auditable. Expiration and escalation MAY be defined by Workflow configuration or future specifications.

## 22. Observability and Traceability

Every significant transition MUST be observable. Execution context SHALL carry Request ID, Correlation ID, Causation ID, Workflow ID and version, Step ID, Execution ID, Skill ID and version when applicable, Capability ID when applicable, provider invocation ID when applicable, start and end time, duration, status, and owner.

Logs are not execution state. Telemetry MUST exclude secrets. Trace context MUST cross component boundaries, and metrics and traces SHALL use normalized Platform concepts.

## 23. Component Responsibility Boundaries

- **Manager** SHALL interpret goals, initiate approved Workflows, manage interaction, receive outcomes, request clarification, and present responses. It MUST NOT execute Skills, own Workflow state, call providers, or replace the Workflow Engine.
- **Workflow Engine** SHALL create instances, select steps, validate transitions, own Workflow state and retry decisions, dispatch Commands, consume result Events, pause and resume, and decide terminal disposition. It MUST NOT execute Skill logic, contain provider logic, call providers, or act as the Event Bus.
- **Skill Runtime** SHALL validate execution requests, resolve approved Skills, enforce runtime boundaries, execute attempts, handle attempt timeout and cancellation, and normalize results. It MUST NOT orchestrate Workflows, select steps, define provider routing, mutate Workflow state, or independently retry failed steps.
- **Skill Registry** SHALL expose Skill identity, versions, contracts, metadata, and resolution. It MUST NOT execute Skills or orchestrate Workflows.
- **Capability Registry** SHALL expose Capability contracts, eligible implementations, discovery, and validation. It MUST NOT orchestrate Workflows, execute product logic, or act as the AI Gateway.
- **AI Gateway** SHALL isolate providers, normalize requests and responses, apply approved selection policy, own provider-level retries and failover, and record usage. It MUST NOT orchestrate Workflows, execute Skills, or contain product decisions.
- **Event Bus** SHALL transport and deliver Event envelopes with preserved metadata. It MUST NOT transport Commands, contain business logic, determine transitions, transform failure into success, or become Workflow truth.

## 24. Security Boundaries

Execution SHALL enforce least privilege, secret isolation, provider credential isolation, workspace context propagation, input and output validation, restricted Capability and Tool access, auditable human approvals, and prevention of sensitive-data leakage in Events or telemetry. ES-002 does not select authentication or authorization technology.

## 25. Required Architecture Diagrams

[Execution Flow](../architecture/ExecutionFlow.md) SHALL contain Mermaid diagrams for:

1. canonical execution flow;
2. end-to-end execution sequence;
3. request lifecycle;
4. Workflow lifecycle;
5. Workflow-step lifecycle;
6. Skill execution-attempt lifecycle;
7. AI invocation sequence;
8. Command dispatch and Event publication;
9. failure propagation;
10. retry decision and execution ownership;
11. human approval and resume; and
12. component responsibility boundaries.

No diagram may show Commands passing through the Event Bus.

## 26. Acceptance Criteria

- [ ] Every lifecycle state and owner is defined, and terminal states are identified.
- [ ] No unexplained ownership overlap remains.
- [ ] The Manager does not execute Skills.
- [ ] Skills do not orchestrate Skills or directly access AI providers.
- [ ] Workflow Engine owns orchestration, durable state, and retry decisions.
- [ ] Skill Runtime owns execution-attempt state and retry-safe execution only.
- [ ] Failed and timed-out execution attempts are terminal and never transition to Retrying.
- [ ] Workflow-step retryability is evaluated only by the Workflow Engine after a normalized attempt outcome.
- [ ] Every retry creates a distinct Execution ID with a monotonically increasing attempt number while preserving Correlation ID and Workflow ID and recording the creating decision through Causation ID.
- [ ] Prior failed and timed-out attempts remain immutable and auditable.
- [ ] AI Gateway owns provider interaction.
- [ ] Event Bus transports Events only.
- [ ] Commands and Events are distinct.
- [ ] Failure, timeout, cancellation, human waiting, and completion behavior are defined.
- [ ] Correlation, causation, and execution identifiers are defined.
- [ ] All diagrams agree with this specification.
- [ ] No implementation technology is selected.

## 27. Definition of Done

- [ ] ES-002 follows the repository ES format.
- [ ] `ExecutionFlow.md` exists and contains all required sections and diagrams.
- [ ] Mermaid diagrams render correctly.
- [ ] Relative links are valid.
- [ ] Terminology matches Architecture v1.0.
- [ ] No implementation technology is introduced.
- [ ] No contradiction with ES-001 or Architecture v1.0 remains.
- [ ] A Draft Pull Request is opened for CTO review and is not merged.

Return to the [Engineering Specifications process](README.md).
