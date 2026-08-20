# TDR-025 — Workflow AI Budget Envelope and Recovery Semantics

- **Status:** Proposed
- **Date:** 2026-08-20

## Context

ES-017 puts an AI-capable step in a durable Workflow. Frozen Gateway governance already makes
`AIInvocationId` authoritative for provider usage, reservations, recovery, reconciliation,
bounded failover, and repair. Frozen accounting already allocates to Workflow references. It does
not define who owns a cumulative per-Workflow AI ceiling, how that ceiling survives restart and
cross-worker execution, or how another AI-capable step is admitted without duplicate accounting.

The decision preserves deterministic Workflow ownership, provider neutrality, Phase 5
authoritative-result reuse, and fail-closed ambiguous-effect semantics. It must not make Gateway
a Workflow orchestrator or Workflow Engine a provider-cost authority.

## Decision

Workflow Engine owns the immutable workflow-level AI budget envelope and every decision to admit a
new AI-capable Workflow step. AI Gateway remains authoritative for actual provider usage/cost and
the durable per-`AIInvocationId` reservation/reconciliation lifecycle. The envelope is enforced by
Workflow Engine as a durable Workflow state/policy decision using Gateway accounting evidence
allocated to the exact `WorkflowId`; it is not a second cost ledger, an `AIInvocationId`
substitute, or free-form Command metadata.

Before another AI-capable step is dispatched, Workflow Engine serializes admission with the
Workflow's durable concurrency control and calculates:

```text
remaining workflow budget = immutable envelope
  - settled actual charges
  - conservative unresolved committed/reserved exposure
```

The calculation uses canonical Gateway accounting evidence for exact Tenant, Workspace, and
Workflow allocation. It never re-prices provider usage, creates a second reservation, or changes
an already-reconciled Gateway charge. A new step is allowed only if remaining budget is sufficient
for approved conservative admission exposure; otherwise Workflow Engine produces the existing
deterministic budget failure/disposition before any Gateway/provider dispatch.

Gateway records actual/estimated usage, provider attempts, fallback, structured repair,
reservation release/expiry, and reconciliation under one `AIInvocationId`. Provider attempts and
repair share the same invocation and cumulative budget; no attempt resets Workflow spend. Gateway
has no Workflow retry loop. Only Workflow Engine evaluates a terminal attempt and, if policy
permits, creates a new Command/Execution attempt that must pass the budget check again.

Deterministic validation and valid `AuthoritativeResultId` reuse occur before `InvokeAI`. They
have zero provider cost and no `AIInvocationId`; avoided-call/savings evidence is recorded through
existing accounting/audit evidence and is not spend. Gateway exact-cache remains an accepted
invocation under frozen accounting semantics.

## Recovery and ambiguity

| Failure point | Required recovery behavior |
| --- | --- |
| Before Gateway/provider dispatch | Recover persisted Workflow admission/step state. Do not create an invocation or charge solely because a worker restarted. |
| After Gateway acceptance, before provider effect | Resolve existing `AIInvocationId` and Gateway reservation/checkpoint; do not create another reservation or invocation. |
| After provider completion, before accounting or Workflow terminalization | Resolve durable provider-effect, usage, reconciliation, and terminal-intent evidence under existing `AIInvocationId`; reconcile/terminalize once without another provider call. |
| Unknown provider dispatch or response/read timeout after dispatch may begin | Preserve `AI_PROVIDER_EFFECT_AMBIGUOUS`; do not Gateway-retry/fail over or manufacture a charge/result; Workflow Engine applies approved retry policy only after terminal evidence. |
| Accounting evidence missing, stale, cross-scope, non-monotonic, or inconsistent | Fail closed before another provider dispatch. Preserve existing outcome evidence, surface normalized governed operational failure, and require reconciliation; never guess remaining budget. |

Across workers, durable Workflow transition/admission serialization and existing scoped Gateway
invocation/reservation idempotency are both required. Concurrent workers can observe existing
admission/outcome but cannot reserve Workflow capacity twice, create two effective dispatches, or
double-charge. Same-command redelivery resolves the recorded disposition; a Workflow retry is a
new `CommandId`/`ExecutionId`, never an implicit rerun.

## Required evidence

The durable, privacy-safe audit/accounting projection exposes Workflow allocation and
remaining-envelope decision alongside existing Gateway evidence: calls made/avoided, token
categories and availability, cumulative Workflow cost, remaining budget, and exposed provider
attempt/fallback counts. Traceability is:

```text
WorkflowId -> WorkflowStepId -> CommandId -> ExecutionId -> capability execution -> AIInvocationId -> ResultId
```

For reuse it is:

```text
WorkflowId -> WorkflowStepId -> ExecutionId -> AuthoritativeResultId -> new ResultId
```

Reuse fabricates no `AIInvocationId`. PostgreSQL tests must prove normal completion, zero-cost
bypass/reuse, duplicate and cross-worker admission, crash/restart at every point above, unknown
effects, timeout/cancellation, budget exhaustion before a further call, cumulative failover/repair,
repair exhaustion, scope/authorization rejection, and one immutable Workflow terminal Result with
no duplicate accounting.

## Consequences and governance gate

Workflow Engine gains a narrow responsibility: enforcing an approved Workflow budget envelope; it
does not become a provider/accounting authority. Gateway remains the only provider boundary and
charge authority. The proposal creates no canonical identity or duplicate ledger.

Frozen contracts lack a typed, immutable, durable Workflow budget-envelope representation. Before
implementation, focused governance must approve the smallest additive versioned Workflow
definition/policy member for maximum governed cost, currency/reference unit, and policy version,
persisted with the Workflow instance. It must not overload `WorkflowId`, `ExecutionId`,
`AIInvocationId`, `AuthoritativeResultId`, idempotency key, or untyped metadata. This TDR does
not amend a frozen contract or authorize runtime changes.

## Revisit evidence

The CTO/Architecture and Finance/Gateway owners revisit this decision if PostgreSQL evidence shows
duplicate admission/charge, accounting cannot deterministically produce a safe remaining budget,
an approved policy needs multi-currency behavior, or a provider invoice variance breaches an
adopted threshold. A successor preserves Workflow Engine transition/retry authority, Gateway
`AIInvocationId` accounting authority, exact Tenant/Workspace scope, immutable lineage, and
fail-closed ambiguity behavior.
