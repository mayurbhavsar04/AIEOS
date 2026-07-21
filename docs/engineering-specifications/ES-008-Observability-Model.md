---
title: ES-008 — Observability Model
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# ES-008 — Observability Model

## Document Metadata

| Field | Value |
| --- | --- |
| **Document ID** | ES-008 |
| **Milestone** | Milestone 4 — Platform Contracts & Service Interfaces, Phase 5 |
| **Priority** | Critical |
| **Implementer** | Engineer (Codex) |
| **Frozen baselines** | `architecture-v1.0`, `domain-v1.0`, and ES-004 through ES-007 at their respective `contracts-v1.0` tags |
| **Architecture status** | Conforms; no ownership, identity, lifecycle, messaging, retry, result, error, or provider boundary changes. |

## Related Documents

| Relationship | Document |
| --- | --- |
| **Architecture** | [Engineering Blueprint](../03-architecture/EngineeringBlueprint.md), [System Architecture](../03-architecture/SystemArchitecture.md), and [Execution Flow](../architecture/ExecutionFlow.md) |
| **Domain** | [Domain Model](../architecture/DomainModel.md) |
| **Specifications** | [ES-001](ES-001-Execution-Core.md), [ES-002](ES-002-Execution-Flow-Architecture.md), [ES-003](ES-003-Domain-Model-and-Ubiquitous-Language.md), [ES-004](ES-004-Command-Contract-Model.md), [ES-005](ES-005-Event-Contract-Model.md), [ES-006](ES-006-Service-Interface-Contracts.md), and [ES-007](ES-007-Error-and-Result-Model.md) |
| **Contracts** | [Command](../architecture/CommandContract.md), [Event](../architecture/EventContract.md), [Service Interfaces](../architecture/ServiceInterfaces.md), and [Error and Result](../architecture/ErrorResultModel.md) |
| **Canonical deliverable** | [Observability Model](../architecture/ObservabilityModel.md) |
| **ADRs** | None required. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-07-21 | CTO / Architect | Initial Milestone 4 Phase 5 specification. |

## 1. Purpose and Scope

ES-008 SHALL define implementation-neutral observability for AIEOS logs, traces, metrics, audit records, operational signals, health evidence, and diagnostic context. Telemetry SHALL describe what happened; it MUST NOT become authoritative state, business behavior, a Workflow mechanism, retry authority, or a new transport category.

The model SHALL support a multi-agent, event-driven, provider-neutral platform while preserving Tenant and Workspace isolation. It MUST NOT redefine frozen component ownership, Commands, Events, service interfaces, Results, Errors, Workflow lifecycle, retry authority, or provider abstraction.

## 2. Pillars and Boundaries

| Pillar | Canonical role | MUST NOT become |
| --- | --- | --- |
| **Log** | Structured diagnostic observation at a component boundary. | Domain Event, Audit Record, or state store. |
| **Trace** | Causal timing graph of operations and asynchronous relationships. | Workflow truth or retry controller. |
| **Metric** | Aggregated numerical observation. | Per-entity ledger or business decision input without approved policy. |
| **Audit Record** | Immutable governance or security evidence about an action or decision. | Operational log, Domain Event, or Recorded Decision. |
| **Operational signal** | Observation of platform operating condition. | Domain Event or instruction to change business state. |
| **Health signal** | Time-bounded evidence of component or dependency condition. | Business status or authorization evidence. |
| **Diagnostic context** | Restricted troubleshooting metadata and references. | Raw secret, prompt, credential, personal data, or provider payload repository. |

Overlap MAY exist through shared identifiers, but each record SHALL retain one canonical type and purpose. Logs, traces, metrics, Audit Records, and health signals MUST NOT be carried as Event Bus payload categories.

## 3. Canonical Observability Context

The canonical context SHALL support:

| Field | Requirement |
| --- | --- |
| `TenantId`, `WorkspaceId` | Required for scoped work; verified, preserved, and never inferred from payload. |
| `CorrelationId` | Required for logical work. |
| `CausationId` | Required when a causal Command, Event, or Recorded Decision exists; no other target is valid. |
| `RequestId` | Context only; MUST NOT be causation. |
| `CommandId`, `EventId` | Required when observing that subject. |
| `WorkflowId`, `WorkflowStepId`, `ExecutionId` | Required when applicable to Workflow work. |
| `AIInvocationId`, `ResultId`, `ErrorId` | Required when observing those immutable subjects. |
| `ComponentIdentity`, `OperationName` | Required. Canonical and bounded. |
| `ContractVersion` | Required for the observed contract/interface. |
| `ObservedAt` | Required timestamp for the observation. |
| `EnvironmentIdentity`, `DeploymentIdentity` | Required abstract operational identity; MUST NOT imply topology. |
| `DataClassification`, `RedactionStatus` | Required whenever diagnostic attributes may carry protected data. |

Identifiers SHALL retain their frozen meanings. Missing optional identity MUST remain absent rather than fabricated. Context propagation conveys lineage, not authority.

## 4. Logging Contract

Log records SHALL include immutable record identity, `ObservedAt`, canonical severity, component, operation, context, safe message, machine-readable attributes, and redaction status. When source event time differs, `OccurredAt` MAY be recorded separately.

Canonical severity is exhaustive:

| Severity | Meaning |
| --- | --- |
| `Trace` | Fine-grained diagnostic detail, normally sampled. |
| `Debug` | Troubleshooting detail not required for normal operations. |
| `Info` | Expected lifecycle or operational observation. |
| `Warn` | Unexpected or degraded condition without established operation failure. |
| `Error` | One operation failed or produced an unsuccessful terminal outcome. |
| `Critical` | Platform integrity, security, or broad service continuity is at immediate risk. |

Severity MUST describe observed operational impact and MUST NOT grant retry or escalation authority. Logs MUST NOT contain secrets, credentials, raw prompts, raw provider payloads, unrestricted personal data, or language-specific exception objects. Safe normalized diagnostic references MAY link to restricted details. Re-emission creates a new log record; it does not mutate the original.

## 5. Distributed Tracing Contract

Traces SHALL use immutable `TraceId`, `SpanId`, and optional `ParentSpanId`. A root span has no parent. A span records start, optional end, status, operation, component, context, links, and sampling decision.

- synchronous nested work SHOULD use parent-child relationships;
- Command dispatch SHALL relate sender and receiver spans without implying Event Bus transport;
- Event publication and consumption SHALL use asynchronous links when producer completion and consumer work are not one call stack;
- each Execution Attempt has its own `ExecutionId` and attempt span lineage;
- a retry creates a new Execution Attempt and span; it MUST NOT reopen a terminal span;
- provider spans remain behind AI Gateway and MUST NOT leak vendor-specific structures;
- timeout and cancellation have distinct span dispositions; and
- sampling decisions SHOULD propagate, but required Audit Records MUST never be dropped by trace sampling.

## 6. Metrics Contract

Canonical metric kinds are counter, gauge, histogram/distribution, duration, and saturation/capacity indicator. Metrics SHALL cover bounded command/event throughput, Workflow and step outcomes, Execution Attempts, AI invocations, Memory operations, Capability Registry operations, Result/Error rates, and latency.

Metric names SHALL identify a stable platform concept, operation, and unit. Dimensions SHALL be bounded, documented, scope-safe, and versioned. `RequestId`, `CommandId`, `EventId`, `WorkflowId`, `ExecutionId`, `ResultId`, `ErrorId`, `AIInvocationId`, user-provided text, and other unbounded values MUST NOT be dimensions. Aggregation windows remain implementation-neutral. Counters SHALL be monotonic within their declared reset boundary.

## 7. Audit Contract

Each Audit Record SHALL have immutable `AuditRecordId`, actor/principal, action, target/resource identity, Tenant and Workspace scope, decision/outcome, applicable Policy or authorization reference, correlation and valid causation, timestamp, reason when required, minimized immutable metadata, and optional before/after references. Tamper evidence SHALL be possible without selecting storage technology.

Audit Records differ from:

- logs, which diagnose operations;
- Domain Events, which record authoritative domain facts; and
- Recorded Decisions, which may cause Commands or Events and are valid causation targets.

An Audit Record observes a decision; it does not become that Recorded Decision. Required security Audit Records MUST NOT be removed by telemetry sampling.

## 8. Operational Signals and Health

Operational signals MAY describe component start/stop, dependency degradation, backlog elevation, provider degradation, Memory latency, or Capability contract incompatibility. They MUST NOT alter business state or be transported as Domain Events unless a separately frozen contract defines that fact. Operational control paths are outside ES-008.

Canonical health states are `Healthy`, `Degraded`, `Unhealthy`, and `Unknown`. Health evidence SHALL include subject, state, evidence timestamp, freshness boundary, and dependency evidence. Readiness means ability to accept assigned work; liveness means ability to continue participating. Neither is a business outcome. Stale evidence resolves to `Unknown`, not assumed health.

## 9. Diagnostic Context and Security

Diagnostic context MAY include safe execution metadata, configuration/version references, abstract dependency references, Result/Error linkage, duplicate/replay indicators, retry lineage, cancellation/timeout reason, sampling decision, and redaction status.

All telemetry SHALL apply least-data collection, classification, masking, Tenant/Workspace isolation, least-privilege access, and auditable access to restricted diagnostics. Raw prompts, secrets, credentials, personal data, Memory contents, and provider payloads are prohibited unless an approved classification rule explicitly permits a minimized field. Human-readable messages MUST be safe for their declared audience.

## 10. Cross-Component Propagation

| Component | Accept and preserve | Add and emit |
| --- | --- | --- |
| **Manager** | Request context and verified scope. | Interaction, acceptance/rejection, and Workflow-initiation observations; no execution authority. |
| **Workflow Engine** | Scope, correlation, causation, Request and Workflow context. | Workflow/step transitions, retry decision evidence, and new Execution identity. |
| **Skill Runtime** | Directed Command, Workflow, step, Execution, and scope context. | One attempt's execution observations and normalized Result/Error linkage; no retry decision. |
| **AI Gateway** | Execution, AI invocation, policy, and scope context. | Provider-neutral invocation latency, usage, fallback, rate limit, policy rejection, timeout, cancellation, and normalized outcome. |
| **Memory Service** | Verified scope, operation, correlation, and authorization context. | Safe read/write/search, conflict, latency, and failure observations; never Memory contents. |
| **Capability Registry** | Verified scope, Capability identity, contract version, and correlation. | Discovery, lookup, compatibility, and validation observations; never Skill execution. |

Propagation MUST NOT move ownership or authority.

## 11. Command, Event, Result, Error, Workflow, and Retry Rules

Telemetry SHALL observe Command creation, validation, dispatch, receipt, acceptance/rejection, and completion. It SHALL observe Event production, recording, publication, delivery, consumption, replay, and duplicate handling. Commands remain directed intent outside Event Bus; Events remain immutable facts transported by Event Bus.

ES-007 semantics remain authoritative. `ResultId` and `ErrorId` are immutable references. Acknowledgement, progress, and terminal completion are separate Results. `ErrorSeverity` MAY map to log severity only through an explicit, documented mapping; it MUST NOT be assumed equivalent. `RetryClassification` is observed metadata, never retry authority. Partial success SHALL expose aggregate and child Result references without duplicating payloads.

Workflow Engine remains sole owner of retry decisions. Each retry creates a new `ExecutionId`; terminal attempts remain immutable. Skill Runtime telemetry MUST NOT imply retry authority. Policy and backoff references are descriptive metadata only.

## 12. AI, Memory, and Capability Observability

AI Gateway telemetry MAY describe invocation acceptance/completion, latency, normalized Result/Error category, allowed usage, abstract provider-selection reference, approved fallback occurrence, quota/rate-limit state, policy rejection, timeout, and cancellation. Vendor metric names, SDK objects, raw prompts, and sensitive provider payloads are prohibited.

Memory telemetry MAY describe operation kind, version conflict, latency, and normalized outcome but never stored contents. Capability Registry telemetry MAY describe discovery, contract/version lookup, compatibility, and validation outcome but MUST NOT imply execution ownership.

## 13. Sampling and Retention

Sampling policy SHALL be explicit, versioned, propagated, and observable. Deterministic sampling SHOULD be preferred for correlated work. Security-critical Audit Records and other policy-required evidence MUST always be captured and MUST NOT be dropped by log or trace sampling.

Retention SHALL follow abstract data-classification, legal, security, minimization, and deletion constraints. ES-008 does not choose durations, tiers, or storage products.

## 14. Versioning and Compatibility

Every observability schema SHALL have stable identity and version. Additive optional fields are compatible when consumers tolerate unknown fields. Removing, renaming, changing meaning/requiredness, changing canonical severity or health semantics, or changing metric type/unit is breaking. Deprecated fields remain interpretable for the declared compatibility window. Producers emit a supported version; consumers reject unsafe ambiguity and tolerate documented extensions.

## 15. Observability Failure Behavior

Telemetry failure MUST NOT silently change a business outcome, create a retry decision, or convert failure to success. Critical Audit Record emission failure SHALL be surfaced according to approved policy; ES-008 does not decide whether business work may proceed. Backpressure/drop policy SHALL prioritize required evidence, bound resource use, expose degraded observability, and prevent recursive failure storms.

## 16. Required Diagrams

The canonical deliverable SHALL include valid Mermaid diagrams for:

1. context propagation;
2. request-to-Result trace;
3. Command dispatch and Event publish/consume relationships;
4. retry with new `ExecutionId`;
5. immutable acknowledgement/progress/terminal Results;
6. provider-neutral AI telemetry;
7. Audit Record boundary;
8. health aggregation;
9. sampling propagation; and
10. cancellation/timeout observations.

## 17. Non-Goals

ES-008 SHALL NOT select OpenTelemetry or another telemetry standard; a logging library; Prometheus, Grafana, Datadog, CloudWatch, Azure Monitor, ELK, OpenSearch, or another named monitoring/APM product; an exporter, collector, agent, storage backend, dashboard, alert implementation, query language, HTTP health endpoint, deployment topology, infrastructure product, or code-level instrumentation API.

## 18. Governance and Acceptance Criteria

Changes to component ownership, Command targets, Event producers, retry authority, canonical Result/Error semantics, provider abstraction, or business lifecycle require applicable architecture/domain review and ADR governance.

The milestone is accepted only when:

- [ ] Canonical component names, including Capability Registry, are preserved.
- [ ] No new component, authority, business behavior, or transport category is introduced.
- [ ] Event Bus remains events-only; telemetry is never a Domain Event by implication.
- [ ] `CausationId` targets only Command, Event, or Recorded Decision; `RequestId` is context only.
- [ ] Workflow Engine remains sole retry-decision owner and every retry creates a new `ExecutionId`.
- [ ] ES-007 types are referenced without redefinition.
- [ ] severity, health, metric, audit, sampling, security, and failure semantics are testable.
- [ ] Tenant/Workspace isolation and redaction are explicit.
- [ ] no vendor or implementation choice appears.
- [ ] all Mermaid diagrams parse, relative links resolve, terminology is consistent, and `git diff --check` passes.

## 19. Definition of Done

ES-008 and `ObservabilityModel.md` exist, cross-reference frozen baselines, contain all required contracts and diagrams, pass repository validation, introduce no conflict or ADR requirement, and are submitted in an unmerged Draft Pull Request for CTO review.
