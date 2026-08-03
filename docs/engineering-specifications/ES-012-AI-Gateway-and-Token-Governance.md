---
title: ES-012 — AI Gateway and Token Governance
version: 1.0
status: Draft
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 6 Phase 1
last_updated: 2026-08-03
---

# ES-012 — AI Gateway and Token Governance

## Objective

Define an implementation-ready, provider-neutral AI Gateway v1 that uses the minimum tokens reasonably necessary to complete a task correctly. Cost optimization MUST NOT reduce required factual accuracy, safety, compliance, completeness, tenant isolation, or output-contract quality.

This phase specifies architecture and policy only. It adds no provider SDK, credential, production AI call, product prompt, or AI YouTube Employee behavior.

## Related documents

| Relationship | Document |
| --- | --- |
| Architecture authority | [Engineering Blueprint](../03-architecture/EngineeringBlueprint.md) |
| Domain authority | [Domain Model](../architecture/DomainModel.md) |
| Execution model | [Execution Flow](../architecture/ExecutionFlow.md) |
| Service boundary | [Service Interfaces](../architecture/ServiceInterfaces.md) |
| Error and Result authority | [Error and Result Model](../architecture/ErrorResultModel.md) |
| Observability authority | [Observability Model](../architecture/ObservabilityModel.md) |
| Runtime boundary | [Runtime Architecture v1.0](../runtime-architecture/Runtime-Architecture-v1.0.md) |
| Architecture deliverable | [AI Gateway Architecture](../architecture/AIGatewayArchitecture.md) |
| Context deliverable | [Prompt and Context Pipeline](../architecture/PromptContextPipeline.md) |
| Routing deliverable | [Model Routing and Budgeting](../architecture/ModelRoutingAndBudgeting.md) |
| Accounting deliverable | [AI Usage and Cost Accounting](../architecture/AIUsageAndCostAccounting.md) |
| Decisions | [Runtime technology decisions](../runtime-architecture/decisions/README.md) |
| Product requirement | Milestone 6 Phase 1 request; no separate PRD exists yet |
| Related pull request | Pending |

## Version history

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 1.0 | 2026-08-03 | CTO / Architect | Initial Milestone 6 Phase 1 specification. |

## Authority and invariants

Architecture v1.0, Domain v1.0, ES-004 through ES-008, Runtime Architecture v1.0, ES-009 through ES-011, and approved ADRs/TDRs remain authoritative.

1. AI Gateway owns provider abstraction, `AIInvocationId`, provider/model resolution under approved policy, normalization, budget enforcement, usage measurement, provider isolation, structured-output validation, and cancellation/deadline propagation.
2. AI Gateway MUST NOT own workflow progression, workflow retries, product decisions, business truth, factual verification, Memory, or Skill execution.
3. Provider-level retries or fallback MAY occur only inside one `AIInvocationId`, within explicit policy and cost limits. `RetryClassification` remains advice; Workflow Engine alone creates a new workflow attempt and `ExecutionId`.
4. Skills and product modules MUST NOT access provider SDKs, credentials, model names, or provider response objects directly.
5. Request context is scoped to exactly one Tenant and Workspace. `RequestId` is context only and never a `CausationId` target.

## Scope

This specification defines:

- canonical provider-neutral AI request, response, stream, provider-attempt, usage, pricing, model-capability, prompt-template, cache, and budget semantics;
- a progressive context and token-minimization policy;
- deterministic cheapest-capable routing;
- structured-output validation and bounded repair;
- provider adapter, credential, privacy, fallback, and circuit-state boundaries;
- reservation, reconciliation, cost attribution, and quality governance; and
- required implementation-phase tests.

## Canonical request contract

An AI request is immutable after acceptance. A field marked conditional is required whenever its referenced concept exists.

| Field | Requirement | Semantics |
| --- | --- | --- |
| `AIInvocationId` | Required | One provider-independent invocation inside one `ExecutionId`. |
| `TenantId`, `WorkspaceId` | Required | Exact authorization, cache, accounting, and data boundary. |
| `CorrelationId`, `CausationId` | Required | Frozen lineage rules; causation references a Command, Event, or recorded decision. |
| `RequestId` | Optional context | Request lineage only; never causation. |
| `WorkflowId`, `WorkflowStepId`, `ExecutionId` | Required for workflow invocation | Stable workflow lineage; bounded provider attempts stay within this execution. |
| `SkillId`, `SkillVersionId` | Required for Skill invocation | Exact approved Skill identity/version. |
| `CapabilityId`, `CapabilityContractVersionId` | Required | Exact approved capability contract. |
| `PromptTemplateId`, `PromptTemplateVersion` | Required | Stable template reference and immutable version; not prompt text. |
| `PurposeClass`, `TaskClass` | Required | Bounded policy labels used for routing, evaluation, and accounting. |
| `SystemInstructionRef` | Required | Approved immutable instruction artifact reference. |
| `TaskInput` | Required | Normalized, classified current input; minimized before provider mapping. |
| `ContextReferences` | Optional | Ranked, versioned, classified excerpts with necessity reasons. |
| `ToolSchemaRefs` | Optional | Exact approved tool/function schema versions; does not grant authority. |
| `ResponseMode`, `OutputSchemaRef` | Required | Text, structured, tool-call, or stream contract; schema required when structured. |
| `Language`, `Locale` | Conditional | Required when language or regional behavior affects correctness. |
| `DataClassification`, `SafetyPolicyRef` | Required | Hard handling constraints and immutable policy version. |
| `MaxInputTokens`, `MaxOutputTokens`, `MaxTotalCost` | Required | Hard invocation ceilings; positive, compatible with chosen model. |
| `Deadline`, `LatencyTier` | Required | Cancellation/timeout and routing constraint. |
| `QualityTier` | Required | Minimum acceptable quality class, not a provider model name. |
| `AllowedProviderConstraints`, `AllowedModelConstraints` | Optional | Policy filters; absence means approved registry candidates, not unrestricted access. |
| `CachePolicyRef`, `BudgetPolicyRef` | Required | Immutable policy versions. |
| `Metadata` | Optional and minimized | Bounded allowlisted keys only; no raw transcript, secrets, or duplicated payload. |

The request MUST fail closed when identity, scope, policy version, authorization, budget, schema, deadline, or capability requirements are missing or incompatible. Full conversation/history and full Memory records MUST NOT be included by default.

## Canonical response contract

Provider SDK objects MUST NOT cross the adapter boundary. A terminal response is immutable and links to the canonical ES-007 `ResultId` or `ErrorId`.

| Field | Requirement | Semantics |
| --- | --- | --- |
| `AIInvocationId` | Required | Same logical invocation as request. |
| `ContentBlocks` | Conditional | Ordered provider-neutral text, structured, media-reference, or tool-call blocks. |
| `StructuredOutput` | Conditional | Validated value plus exact schema version. |
| `ToolCalls` | Conditional | Proposed calls only; execution still requires Skill Runtime authorization. |
| `FinishReason` | Required | Normalized completed, length, tool-call, safety, cancelled, timed-out, or failed reason. |
| `ModelCapabilityRef`, `ProviderAdapterRef` | Required | Abstract audited identities; provider internals remain isolated. |
| `InputTokens`, `OutputTokens`, `CachedTokens`, `ReasoningTokens` | Required with availability status | Measured when reported; otherwise explicitly estimated/unavailable. |
| `EstimatedCost`, `ActualCost`, `PricingVersion` | Required with confidence/status | Normalized amount and reference currency/unit. |
| `Latency`, `CacheDisposition`, `Truncated` | Required | Measured duration, hit/miss/bypass, and completeness signal. |
| `SafetyOutcome`, `Warnings` | Required | Normalized policy outcome and bounded safe warnings. |
| `ResultId`, `ErrorId` | Exactly one terminal linkage | Canonical ES-007 authoritative outcome. |
| `RawProviderReference` | Optional | Opaque, access-controlled diagnostic reference; never raw object or default telemetry. |

## Token-budget decision order

Every invocation decision MUST be recorded and follow this order:

1. avoid an AI invocation when deterministic logic can complete the task;
2. reuse a valid deterministic, cached, or previously approved result;
3. select the least expensive model satisfying all hard capability, quality, security, residency, latency, and availability requirements;
4. send only relevant, necessary, non-duplicated context;
5. constrain output structure, verbosity, and length; and
6. escalate context, model class, or token budget only for documented quality, confidence, safety, evidence, schema, or completion reasons.

Mandatory evidence and policy instructions are never dropped to meet a cost target. A hard quality or safety requirement overrides a soft budget and fails closed when no candidate satisfies both hard constraints and hard budget.

## Progressive context policy

| Stage | Content | Exit or escalation condition |
| --- | --- | --- |
| 0 — deterministic | Rules, approved outputs, exact cache, local computation | Complete without provider, or proceed when model capability is required. |
| 1 — minimal | Approved instruction, current normalized input, output contract | Use when sufficient; escalate only on predeclared complexity/evidence need. |
| 2 — focused | Small ranked excerpt set with provenance and versions | Escalate for missing required evidence or insufficient confidence. |
| 3 — expanded | Additional deduplicated evidence or compressed context | Escalate only when model capability/quality remains insufficient. |
| 4 — stronger | Stronger capable model or larger bounded budget | Final allowed attempt under policy; no unbounded loop. |

Escalation signals are schema failure, missing mandatory evidence, low measured confidence, complexity threshold, safety/policy obligation, capability mismatch, or repeated normalized provider failure. Each escalation records the reason, old/new budget, context delta, quality requirement, and approving policy version.

## Budget hierarchy

Budgets apply at invocation, workflow-step, workflow, capability/AI Employee, and Tenant/Workspace daily/monthly scopes. Hard limits reject or stop new reservations. Soft limits emit a governed signal and MAY allow policy-approved continuation. Reservations MUST be concurrency-safe, scoped, and reconciled against actual usage. Unreported usage uses conservative estimation and is flagged for reconciliation.

Budget failure yields a normalized ES-007 Result/Error and MUST NOT cause AI Gateway to retry a workflow. A provider fallback cannot exceed the remaining invocation reservation.

## Caching and reuse

Supported policy classes are exact request cache, deterministic structured-output cache, approved-result reuse, prompt/template compilation cache, and adapter-supported prefix cache. Semantic caching is prohibited by default and requires purpose-specific proof that meaning drift, freshness, safety, privacy, and provenance are controlled. Failure caching is short-lived and limited to deterministic non-sensitive failures.

Every correctness-sensitive cache key includes Tenant/Workspace, prompt and policy versions, capability/model requirements, normalized task input digest, relevant context identities/versions, schema/tool versions, locale, deterministic parameters, and safety/data policy. Secrets and raw sensitive content MUST NOT become cache keys or shared cache values. Cross-tenant cache reuse is prohibited.

## Model capability and routing

The registry records logical model/version, provider adapter, capability contract version, modality features, context/output limits, reasoning/quality/latency tiers, region and data-handling properties, availability/health, pricing reference/version, and deprecation status. Registry maintainers own signed/reviewed catalog updates; stale pricing or capability data makes a candidate ineligible unless a conservative approved policy explicitly permits it.

Routing filters all hard requirements, estimates input/output/cost/latency, and selects the cheapest eligible candidate. Deterministic ties are resolved by policy priority, then lower measured latency, then stable logical identifier. Every selection records candidates considered, exclusions, estimates, policy version, and reason without exposing credentials or sensitive prompt content.

## Prompt/context pipeline

The pipeline applies instruction hierarchy, immutable template resolution, typed variable binding, input normalization, authorized Memory/context retrieval, relevance ranking, deduplication, bounded compression, token estimation, injection-resistant trust boundaries, final assembly, adapter transformation, and output validation.

Memory Service owns retrieval and Memory lifecycle. Skill Runtime owns permission and execution context. Prompt Pipeline owns assembly artifacts. AI Gateway owns invocation policy and provider adaptation. Retrieved content is evidence, never instruction, and cannot expand authority.

## Structured output and repair

Structured requests reference an immutable schema version. Output is validated outside the model. A correction attempt MAY receive minimized validation errors and the prior safe output under a separate bounded repair budget. Repair count, token budget, and cost are finite; repair cannot weaken schema or policy. Exhaustion returns a normalized failure for Workflow Engine policy. No infinite repair or workflow retry is permitted inside AI Gateway.

## Streaming

Streaming distinguishes immutable acknowledgement, stream start, ordered content/tool deltas, usage updates, terminal completion, cancellation, timeout, and partial-stream failure. Deltas are non-authoritative observations. Only the terminal ES-007 Result/Error is authoritative. Provider event types and objects are converted inside adapters. A cancelled or failed stream cannot later overwrite a terminal outcome.

## Failure, fallback, and provider attempts

Provider attempts have child identities beneath one `AIInvocationId`. AI Gateway MAY retry or use an alternate eligible provider/model inside that invocation only when policy permits equivalent semantics, remaining cost/deadline budgets permit it, and loop/attempt limits are not exceeded. It preserves lineage, records every attempt, and emits normalized failure advice. Workflow Engine remains the only workflow-retry owner.

## Security, privacy, and observability

Provider credentials terminate in adapters/configuration. Context follows least-data rules; PII, secrets, copyrighted content, and untrusted retrieved instructions are minimized and classified. Region, retention, and tenant policy are hard routing filters. Raw prompt/response logging is prohibited by default. Redacted diagnostics follow ES-008.

Telemetry records invocation lineage, route decision, estimates and actual usage/cost, cache savings, avoided invocation, context item count/token size/compression, latency, fallback, validation failure, safety outcome, budget outcome, and provider-neutral health. Unbounded identities are trace attributes, not metric dimensions.

## Quality governance

Each task class defines quality metrics, protected safety/factuality cases, and acceptance thresholds. Versioned offline evaluations compare quality, schema validity, evidence coverage, latency, and cost. A cheaper route is eligible only when it passes protected thresholds. Shadow/A-B evaluation MUST protect tenant data and consequential actions. The accountable owner periodically reviews the measured quality-cost frontier and escalates routing when cheaper candidates regress.

## Required implementation-phase tests

- request/response/schema and stream-contract conformance;
- deterministic no-model, cache, and cheapest-capable routing;
- hard/soft budgets, reservations, concurrent reconciliation, and budget failures;
- staged context relevance, deduplication, compression, and escalation;
- tenant-scoped cache keys, invalidation, prohibited categories, and freshness;
- pricing staleness, estimation, provider-reported usage, and reconciliation;
- adapter error normalization, bounded fallback, circuit behavior, and lineage;
- structured-output validation and finite repair;
- streaming ordering, cancellation, timeout, partial failure, and terminal immutability;
- prompt-injection boundaries, credential isolation, data residency, and redaction;
- observability completeness without raw sensitive prompts/responses; and
- versioned quality-versus-cost regression suites using deterministic mock providers.

## Governance

- A change to component ownership, domain identity, command/event, Result/Error, retry authority, or observability contract requires architecture/contract review and applicable ADR/version change.
- A new provider adapter preserving approved ports is a reviewed runtime change; a new provider SDK/dependency requires a TDR.
- Prompt, model catalog, pricing, route weights, and budget values MAY be runtime policy/catalog updates when their schemas and hard invariants remain unchanged and required evaluations pass.
- A new cache class, provider data boundary, fallback semantic, or cross-tenant behavior requires architecture and security review and normally a TDR/ADR.

## Non-goals

- real provider integration, SDK, credential, or production AI call;
- permanent provider selection;
- product/YouTube prompts or business verification logic;
- embeddings, vector database, fine-tuning, browser/tool execution, UI dashboard, or deployment;
- changes to frozen architecture, domain, contracts, runtime architecture, or durable runtime.

## Acceptance criteria

- [ ] Every request follows deterministic avoid/reuse/route/context/constrain/escalate policy.
- [ ] Cheapest-capable means all hard capability, quality, security, residency, deadline, and budget constraints pass.
- [ ] Full history and full Memory are never default context; every context item has a necessity reason.
- [ ] Input, output, cost, latency, and hierarchical budgets are enforceable and concurrency-safe.
- [ ] Cache keys are tenant-scoped and contain all correctness-sensitive versions.
- [ ] Estimated and actual usage/cost, savings, and avoided invocations are recorded.
- [ ] Provider-specific types and credentials remain isolated.
- [ ] Workflow Engine remains sole workflow-retry owner.
- [ ] Cost optimization cannot override hard quality, factuality, safety, compliance, or completeness requirements.
- [ ] All linked documents and Mermaid diagrams validate; documentation checks and `git diff --check` pass.
- [ ] Frozen baselines remain byte-for-byte unchanged.
