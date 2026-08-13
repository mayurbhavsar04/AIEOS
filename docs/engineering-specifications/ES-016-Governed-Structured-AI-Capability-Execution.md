---
title: ES-016 — Governed Structured AI Capability Execution
version: 0.1
status: Draft
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 6 Phase 5
last_updated: 2026-08-13
---

# ES-016 — Governed Structured AI Capability Execution

## Objective

Prove one reusable, provider-neutral structured AI capability can execute through the frozen
Skill Runtime and AI Gateway using an immutable prompt package, typed input, application-owned
output validation, bounded context, versioned offline evaluation, rollback, and cumulative
token/cost controls. This is an integration proof, not product behavior.

## Authority

Frozen Architecture, Domain, contracts, and Runtime Architecture v1.0 remain authoritative.
[ES-012](ES-012-AI-Gateway-and-Token-Governance.md),
[ES-013](ES-013-AI-Gateway-Reference-Implementation.md),
[ES-015](ES-015-Multi-Provider-Routing-and-Failover.md), and
[TDR-018](../runtime-architecture/decisions/TDR-018-Prompt-Context-and-Versioning.md) constrain
this work. The Phase 4 merge commit is
`396faecaf917c995fd4ae65ea238da350ea8dc27` and tag is `multi-provider-routing-v1.0`.

## Related Documents

| Relationship | Document |
| --- | --- |
| PRD | None. This is a bounded platform integration proof; it MUST NOT introduce product behavior. |
| Architecture | [Service Interfaces](../architecture/ServiceInterfaces.md), [Prompt and Context Pipeline](../architecture/PromptContextPipeline.md), [AI Gateway Architecture](../architecture/AIGatewayArchitecture.md) |
| TDRs | [TDR-018](../runtime-architecture/decisions/TDR-018-Prompt-Context-and-Versioning.md), [TDR-020](../runtime-architecture/decisions/TDR-020-Structured-Output-and-Streaming.md), [TDR-022](../runtime-architecture/decisions/TDR-022-AI-Usage-and-Cost-Accounting.md) |
| Future specifications | Pending: a product-authorized vertical slice may consume the proven capability only after separate approval. |
| Related pull requests | Pending: governance-only Draft PR; implementation Draft PR after this ES and TDR-018 are approved. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 0.1 | 2026-08-13 | CTO / Architect | Initial Phase 5 governance draft. |

## Scope

The implementation PR SHALL deliver exactly one static, allowlisted structured AI capability.
It SHALL:

1. resolve one immutable prompt package for an approved capability/version;
2. validate typed input before provider dispatch;
3. bind only declared variables and assemble Stage 1 context after Gateway acceptance;
4. pass the existing provider-neutral request through Skill Runtime and AI Gateway;
5. validate the returned value against an exact versioned output schema in AIEOS code;
6. permit at most one schema-repair invocation, only within the original invocation's remaining
   cumulative token and cost budget;
7. record safe version, route, usage, cost, outcome, and evaluation evidence; and
8. select a previously approved immutable package version as a deterministic rollback target.

The prompt-package catalog is implementation-local and static. Each package SHALL carry a stable
reference, immutable version reference, owner, capability/version association, typed variables,
system-instruction reference, output-schema reference, task class, quality threshold, input/output
token ceiling, maximum cost, evaluation-set reference, rollback target, and change history.

## Boundaries and compatibility

This ES adds no canonical Domain identity, Command, Event, Result/Error semantic, component,
ownership transfer, provider-specific caller value, or public Gateway contract. Prompt references
remain non-canonical value references. Skill Runtime remains the owner of one execution attempt;
Workflow Engine remains the only retry owner; AI Gateway remains the sole provider boundary and
owner of `AIInvocationId`, routing, provider retry/failover, and cumulative accounting.

The implementation MUST use the existing approved Skill Runtime → Capability Registry → AI Gateway
path. It MUST stop for architecture review before implementation if this cannot be done additively
or if post-acceptance assembly, immutable versioning, schema validation, or rollback requires a
frozen-contract change.

## Token, cost, and safety requirements

- Deterministic validation, catalog lookup, binding, rollback selection, and evaluation scoring
  MUST NOT call a model.
- Invalid, unknown, incompatible, disabled, or non-immutable package versions MUST fail closed
  before paid dispatch.
- One primary invocation is permitted per execution. One repair invocation is the maximum and it
  shares the same `AIInvocationId` budget, provider-attempt limits, idempotency, and accounting.
- Stage 1 contains only instructions, typed current input, and schema. Retrieval, full history,
  embeddings, vector search, model-assisted compression, and semantic caching are excluded.
- Existing exact-cache/replay and cheapest-capable routing remain eligible only under their frozen
  policies. A lower price MUST NOT override quality, schema, safety, data-handling, or residency
  requirements.
- Ordinary CI SHALL use deterministic fixtures and mock transports. Model-as-judge is prohibited
  for release gating. Any real-provider conformance run MUST be manual, protected, tiny,
  SHA-guarded, credential-scoped, bounded, and report actual usage and cost.
- Raw prompts, outputs, credentials, and provider payloads MUST NOT enter default telemetry.

## Explicit non-goals

Provider #3; a YouTube Employee workflow; product prompts; memory retrieval; embeddings or vector
storage; semantic cache; tools/function execution; browser or external effects; multimodal input or
output; prompt-management UI or remote prompt loading; autonomous prompt optimization; new Workflow
behavior; and frozen-baseline changes are out of scope.

## Evaluation and release requirements

The implementation SHALL include versioned, sanitized offline cases for normal input, ambiguity,
hostile/untrusted content, missing or invalid typed input, malformed model output, repair-budget
exhaustion, provider-neutral structured completion, rollback, and replay. Each case SHALL define
expected schema validity, safe terminal disposition, maximum primary/repair calls, and allowed
token/cost envelope.

A candidate package release SHALL pass protected schema, safety, and quality thresholds without a
material regression against its rollback target. Subjective or editorial quality is not approved by
this ES and requires human-reviewed product evaluation in a later specification.

## Acceptance Criteria

- [ ] The one capability and every package/schema version resolve deterministically from static,
  allowlisted configuration.
- [ ] Invalid typed input and invalid package references create no provider dispatch.
- [ ] The resulting invocation is provider-neutral and works through deterministic mocks and both
  already-supported real-provider adapter conformance paths.
- [ ] AIEOS code, not a provider schema hint, authoritatively validates structured output.
- [ ] There is no more than one bounded repair; its usage/cost is cumulative and budget exhaustion
  prevents dispatch.
- [ ] Replay, cache, cancellation, ambiguity, route selection, and terminal uniqueness preserve
  every Phase 1–4 guarantee.
- [ ] Rollback selects an approved immutable version and is observable without a code or contract
  change.
- [ ] Required offline evaluation, security, formatting, type, canonical regression, and
  PostgreSQL gates pass without new skips.
- [ ] No raw prompt/output content is emitted by default observability.
- [ ] No frozen contract or ownership changes are present.

## Implementation sequencing

This governance PR authorizes no runtime code. After CTO approval of this ES and TDR-018, one
separate implementation Draft PR may add the static resolver, one capability integration, schema
validation/repair, evaluations, and regression evidence. It MUST remain within this specification
and stop on any required architectural deviation.
