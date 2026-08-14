---
title: ES-016 — Governed Structured AI Capability Execution
version: 0.6
status: In Review
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 6 Phase 5
last_updated: 2026-08-14
---

# ES-016 — Governed Structured AI Capability Execution

## Objective

Prove the one provider-neutral **Structured Task Kind Classification** capability can execute
through the frozen Skill Runtime and AI Gateway. Given one normalized task statement, it returns
exactly one task kind from the fixed contract enum `Question | Instruction | Statement` and no
generated prose. It uses an immutable prompt package, typed input, Gateway-owned structured-output
validation/repair, deterministic capability acceptance checks, bounded context, versioned offline
evaluation, rollback, and cumulative token/cost controls. This is a platform integration proof,
not product behavior.

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
| Architecture | [Command Contract](../architecture/CommandContract.md), [Service Interfaces](../architecture/ServiceInterfaces.md), [Prompt and Context Pipeline](../architecture/PromptContextPipeline.md), [AI Gateway Architecture](../architecture/AIGatewayArchitecture.md) |
| ADR | [ADR-001 — Authoritative Result Reuse](../architecture/decisions/ADR-001-Authoritative-Result-Reuse.md) |
| TDRs | [TDR-018](../runtime-architecture/decisions/TDR-018-Prompt-Context-and-Versioning.md), [TDR-020](../runtime-architecture/decisions/TDR-020-Structured-Output-and-Streaming.md), [TDR-022](../runtime-architecture/decisions/TDR-022-AI-Usage-and-Cost-Accounting.md) |
| Future specifications | Pending: a product-authorized vertical slice may consume the proven capability only after separate approval. |
| Related pull requests | [PR #29](https://github.com/mayurbhavsar04/AIEOS/pull/29) records the approved first-release rollback clarification; [PR #28](https://github.com/mayurbhavsar04/AIEOS/pull/28) remains paused and Draft pending approval and merge of this governance change, then its separate remediation and a fresh comprehensive CTO gate. |

## Version History

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 0.6 | 2026-08-14 | CTO / Architect | Returned to In Review for the approved `AuthoritativeResultId` v2 Command path; PR #28 is paused pending governance approval and merge. |
| 0.5 | 2026-08-13 | CTO / Architect | Approved the first-release rollback clarification after focused CTO review at `1c88f8fc67eb9fcb9dae6d3a0165b0d1a73322e0` with Blocking 0 / Major 0 / Minor 0. |
| 0.4 | 2026-08-13 | CTO / Architect | Returned to In Review for the first-release rollback clarification; no approval or immutable rollback baseline is asserted retroactively. |
| 0.3 | 2026-08-13 | CTO / Architect | Approved for implementation after focused CTO re-review at `63cd3ba06fceec5e664fa85070a987876ed77a40` with Blocking 0 / Major 0 / Minor 0. |
| 0.2 | 2026-08-13 | CTO / Architect | Defined the capability contract, ownership, sequencing, security propagation, evidence, and objective release gates after focused CTO review. |
| 0.1 | 2026-08-13 | CTO / Architect | Initial Phase 5 governance draft. |

## Capability contract

The only capability is `StructuredTaskKindClassification` contract version `1`. Its immutable,
implementation-local package and schema references do not create canonical identities.

| Element | Contract |
| --- | --- |
| Purpose | Classify the communicative form of one task statement. It MUST NOT infer intent, priority, sentiment, topic, authority, workflow routing, or product behavior. |
| Typed input | `{statement: string}` after existing request normalization. UTF-8 text; trimmed length `1..512` Unicode scalar values; no attachments, context collection, history, or optional fields. |
| Structured output | Exact object `{task_kind: "Question" | "Instruction" | "Statement"}`; no additional properties. `Question` requests information, `Instruction` requests an action, and `Statement` is neither. |
| Bounds | Stage 1 only; one statement; package input ceiling 256 tokens; output ceiling 16 tokens; exactly one primary Gateway invocation by default; Gateway may perform at most one schema repair within the same `AIInvocationId` and cumulative budget. |
| Deterministic bypass | Before `InvokeAI`, the capability owner MUST use only `DispatchExecutionAttempt` v2 metadata `AuthoritativeResultId: ResultId | absent`, propagated as `SkillInput.authoritative_result_id: str | None`. It is not `{statement}` payload and MUST NOT be placed in `CapabilityPolicyContext`. A valid reference yields deterministic, contract-valid reuse; absence preserves ordinary behavior. No heuristic punctuation or keyword guess may be treated as authoritative. |
| Gateway failure | Rejection, policy/budget failure, provider failure/ambiguity, schema-invalid output after the permitted Gateway repair, cancellation, or timeout returns the existing normalized terminal Result/Error. The capability MUST NOT retry or call a model to repair it. |
| Capability acceptance | After a successful canonical Gateway Result, deterministically require the exact field set and enum membership above. Failure is a normalized capability failure and MUST NOT trigger another model call. This check does not repeat provider payload parsing, schema validation, or repair. |

## Scope

The implementation PR SHALL deliver exactly one static, allowlisted structured AI capability.
It SHALL:

1. have Capability Registry resolve only the approved capability contract/version and immutable
   package/schema references declared by that contract;
2. have the capability implementation, executed by Skill Runtime, validate typed input and apply
   deterministic bypass before requesting a paid invocation;
3. have Prompt Pipeline own the static package catalog, immutable version construction, template
   selection, declared-variable binding, Stage 1 accepted-lifecycle assembly, schema lookup, and
   deterministic rollback selection;
4. pass the existing provider-neutral request and verified policy context through Skill Runtime to
   AI Gateway's existing additive `accept -> execute` seam;
5. have AI Gateway alone validate provider structured output against the immutable schema and, when
   policy permits, perform at most one repair within the original `AIInvocationId`, durable effect,
   idempotency, provider-attempt, and cumulative token/cost machinery;
6. apply only the deterministic capability acceptance check to the canonical Gateway Result;
7. record the privacy-safe execution and accounting evidence required below; and
8. have Prompt Pipeline apply the first-release rollback clarification below while Capability
   Registry continues to own only capability contracts/catalog metadata.

For the optional v2 authoritative-result path, Skill Runtime resolves `AuthoritativeResultId` via
its existing durable execution/result repository before Gateway invocation. The referenced Result
MUST be immutable and terminal `Succeeded`, originate from the authorized capability/execution
boundary, exactly match Tenant and Workspace, Capability ID and capability contract version, and
match the new normalized statement from protected durable execution evidence; a stored canonical
digest is acceptable. Its output MUST pass current capability contract validation. The caller MUST
be authorized both to read the Result and to invoke the Capability. Missing, unauthorized,
cross-scope, nonterminal, incompatible, malformed, or input-mismatched references fail closed
before Gateway invocation.

The Prompt Pipeline's prompt-package catalog is implementation-local and static. Each package SHALL carry a stable
reference, immutable version reference, owner, capability/version association, typed variables,
system-instruction reference, output-schema reference, task class, quality threshold, input/output
token ceiling, maximum cost, evaluation-set reference, rollback target, and change history.

## First-release rollback clarification

For the first governed package of a capability, where no previously approved immutable package
version exists, rollback SHALL mean disabling or not promoting the candidate package and retaining
the last frozen system state in which that capability package is not active. After the first package
is approved and frozen, every subsequent candidate package MUST identify and support rollback to a
genuinely previously approved immutable package version. No package may be designated, inferred, or
represented as previously approved retroactively.

This clarification does not approve, activate, release, tag, or freeze a package. TDR-018 is not
amended because it does not independently contain conflicting absolute rollback language.

## Boundaries and compatibility

This ES adds no Domain identity, Event, Result/Error semantic, component, ownership transfer,
provider-specific caller value, or public Gateway contract. It governs the new supported
`DispatchExecutionAttempt` v2 Command version and its optional metadata
`AuthoritativeResultId: ResultId | absent`; it is not a v1 ignorable extension. Prompt references
remain non-canonical value references. Skill Runtime remains the owner of one execution attempt;
Workflow Engine remains the only retry owner; AI Gateway remains the sole provider boundary and
owner of `AIInvocationId`, routing, provider retry/failover, authoritative provider structured-output
validation/repair, and cumulative accounting. Capability Registry owns only capability contracts and
catalog metadata. Skill Runtime executes the capability's one attempt. Prompt Pipeline owns package
construction/versioning, accepted-lifecycle assembly artifacts, schema lookup, and rollback selection;
it does not become a new component or public boundary.

The implementation MUST use the existing approved Skill Runtime → Capability Registry → AI Gateway
path and the Gateway-internal accepted lifecycle. Before Gateway invocation, Skill Runtime performs
v2 authoritative-result resolution, typed validation, authorization/isolation validation, and
deterministic bypass. Gateway acceptance
atomically creates `AIInvocationId`; only then may Prompt Pipeline bind and assemble Stage 1 within
the existing `Requested -> PolicyValidated -> ProviderSelected` path before preparation/dispatch.
This sequencing MUST preserve canonical request identity, scoped idempotency, and acceptance/replay
rules. Same-command replay returns the existing outcome under ordinary replay/idempotency semantics.
A new execution always receives a new `ExecutionId`, `CommandId`, idempotency scope, and terminal
Result even when its classification is reused. It MUST stop for architecture review if the work
cannot implement the approved v2 contract exactly.

## Token, cost, and safety requirements

- Deterministic validation, authoritative-result reuse/bypass, catalog lookup, binding, rollback
  selection, capability acceptance, and evaluation scoring MUST NOT call a model. Bypass occurs
  before `InvokeAI` wherever the frozen architecture permits and MUST NOT move product logic into
  Gateway.
- A valid authoritative reuse creates no `AIInvocationId`, Gateway call, provider call, or model
  tokens. It records source Result lineage plus avoided invocation/token/cost evidence through
  existing audit/accounting evidence; no duplicate ledger or persistence subsystem is permitted.
- Invalid, unknown, incompatible, disabled, or non-immutable package versions MUST fail closed
  before paid dispatch.
- One primary Gateway invocation is permitted per execution by default. Only Gateway may issue one
  schema repair, when existing policy permits, sharing the same `AIInvocationId`, durable-effect and
  idempotency controls, cumulative budget, and accounting. The capability owns no LLM retry, repair,
  fallback, or progressive-call loop.
- Stage 1 contains only instructions, typed current input, and schema. Retrieval, full history,
  embeddings, vector search, model-assisted compression, and semantic caching are excluded.
- Existing exact-cache/replay and cheapest-capable routing remain eligible only under their frozen
  policies. A lower price MUST NOT override quality, schema, safety, data-handling, or residency
  requirements.
- Ordinary CI SHALL use deterministic fixtures and mock transports. Model-as-judge is prohibited
  for release gating. Any real-provider conformance run MUST be manual, protected, tiny,
  SHA-guarded, credential-scoped, bounded, and report actual usage and cost.
- Raw prompts, outputs, credentials, and provider payloads MUST NOT enter default telemetry.

Verified `TenantId`, `WorkspaceId`, authorization/security context, purpose, data classification,
residency, retention, privacy, redaction, and cache constraints flow unchanged from capability
invocation into the existing Gateway request/policy context. Every owner fails closed on missing,
unknown, inconsistent, unauthorized, or scope-mismatched context. Assembly, persistence, replay,
evaluation evidence, and telemetry preserve least-data and Tenant/Workspace isolation. This ES adds
no identity or authority owner and permits no raw sensitive prompt/context/output logging by default.

Each execution SHALL expose, by reusing existing Gateway accounting and observability, a
privacy-safe evidence record containing `ExecutionId`, capability and contract version, prompt
package/version reference, invoked-versus-bypassed disposition, and terminal outcome. A bypass
records a bounded reason code and estimated avoided invocation/token/cost savings and has no
`AIInvocationId`. An accepted invocation records `AIInvocationId`; input, output, cached, and
reasoning token categories with measured/estimated/unavailable status as the canonical accounting
provides; estimated and actual governed cost with status; budget outcome; permitted abstract
route/model/provider references; cache disposition; and counts for primary dispatch, Gateway schema
repair, provider attempt/failover, and total model calls. Evidence includes Tenant/Workspace scope,
classification, and redaction status but no prompt/context/output/provider payload. Unbounded
identifiers remain trace/audit attributes, never high-cardinality metric labels. No duplicate cost
ledger or accounting authority is introduced.

## Explicit non-goals

Provider #3; a YouTube Employee workflow; product prompts; memory retrieval; embeddings or vector
storage; semantic cache; tools/function execution; browser or external effects; multimodal input or
output; prompt-management UI or remote prompt loading; autonomous prompt optimization; new Workflow
behavior; and frozen-baseline changes are out of scope.

## Evaluation and release requirements

The implementation SHALL include a protected, versioned, sanitized offline set with at least 100
balanced accepted cases (at least 30 per enum value) plus dedicated invalid, bypass, hostile,
ambiguous, malformed-provider-output, repair-budget-exhaustion, rollback, replay, and both-adapter
conformance cases. Every accepted case has one human-reviewed exact expected enum; every other case
has an exact expected terminal disposition. Fixtures define maximum primary/repair/provider calls
and token/cost ceilings.

A candidate package release SHALL achieve 100% deterministic input/bypass/failure expectations,
100% exact schema validity after Gateway processing, 100% hostile-case safe dispositions, and at
least 95% exact task-kind accuracy overall with at least 90% recall for each enum value. It SHALL
remain within every case's call/token/cost ceiling. Any safety/schema regression, any threshold
failure, or an accuracy decrease greater than 2 percentage points against the genuinely previously
approved immutable rollback target, when one exists, blocks release and selects rollback. For the
first governed package, the first-release rollback clarification applies. Model-as-judge and
subjective/editorial scoring are not release gates.

## Acceptance Criteria

- [ ] The one capability and every package/schema version resolve deterministically from static,
  allowlisted configuration.
- [ ] Invalid typed input and invalid package references create no provider dispatch.
- [ ] The resulting invocation is provider-neutral and works through deterministic mocks and both
  already-supported real-provider adapter conformance paths.
- [ ] AI Gateway, not a provider hint or capability loop, authoritatively validates and optionally
  repairs provider structured output; capability acceptance is deterministic and model-free.
- [ ] There is no more than one bounded repair; its usage/cost is cumulative and budget exhaustion
  prevents dispatch.
- [ ] Replay, cache, cancellation, ambiguity, route selection, and terminal uniqueness preserve
  every Phase 1–4 guarantee.
- [ ] First-release rollback disables or does not promote the candidate and retains the frozen
  inactive-capability state; later rollback selects a genuinely previously approved immutable
  version and is observable without a code or contract change.
- [ ] Required offline evaluation, security, formatting, type, canonical regression, and
  PostgreSQL gates pass without new skips.
- [ ] No raw prompt/output content is emitted by default observability.
- [ ] Verified scope/security/privacy policy is propagated fail-closed and per-execution
  bypass/invocation/token/cost/attempt evidence reuses canonical Gateway accounting.
- [ ] The protected evaluation set passes every objective threshold and rollback regression rule.
- [ ] The only contract change is the reviewed `DispatchExecutionAttempt` v2 metadata contract;
  Domain identity, ES-007 Result semantics, Memory contracts, AI Gateway contracts, TDR-018,
  provider adapters, and runtime implementation remain unchanged.

## Implementation sequencing

This governance PR authorizes no runtime code. Approval and merge of this v2 contract governance
are required before any implementation begins. Only after those recorded transitions may a separate
implementation Draft PR add the resolver, one capability integration, evaluations, and regression
evidence. PR #28 remains paused until then and is not amended, resumed, merged, or reviewed by this
governance PR. Gateway structured validation/repair remains existing authority, not capability
implementation scope. The implementation MUST stop on any required architectural deviation.
