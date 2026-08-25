---
title: Workflow AI Budget Envelope Contract
version: 0.3
status: Approved
owner: CTO / Architect
last_updated: 2026-08-25
---

# Workflow AI Budget Envelope Contract

## Version history

| Version | Date | Author | Notes |
| --- | --- | --- | --- |
| 0.3 | 2026-08-25 | CTO / Architect | Approved after exact-SHA governance review of `8d7e55317818a4c4491dd985d1d639f6a7d956a5` with Blocking 0 / Major 0 / Minor 0 / Notes 0; activation remains bounded by PR #32 merge and no Phase 6 implementation is authorized. |
| 0.2 | 2026-08-24 | CTO / Architect | Added the In Review admission binding, conservative exposure, exact-decimal comparison, compatibility, and hosted behavioral validation requirements; no implementation was authorized. |

## Purpose and governed boundary

This is the smallest additive contract required by
[ES-017](../engineering-specifications/ES-017-Governed-AI-Workflow-Execution.md) and
[TDR-025](../runtime-architecture/decisions/TDR-025-Workflow-AI-Budget-and-Recovery.md).
It defines `WorkflowAIBudgetEnvelope` v1, a typed member of one immutable
`WorkflowDefinitionVersionId`, governed by one exact `PolicyVersionId`. The normative serialized
shape is [Workflow AI Budget Envelope v1](schemas/workflow-ai-budget-envelope-v1.schema.json),
normatively enclosed by [Workflow Definition Contract v2](WorkflowDefinitionContract.md).

It is not a new canonical identity, a new aggregate, a provider contract, an execution command,
or an accounting subsystem. `WorkflowId`, `ExecutionId`, `ResultId`, `AIInvocationId`, correlation,
causation, and idempotency keep their frozen meanings. Generic metadata and a `PolicyId` or
`PolicyVersionId` alone are rejected as substitutes: metadata is not durable authoritative budget
semantics, and a policy reference identifies a policy but cannot carry this typed immutable ceiling.

## Envelope v1

`WorkflowAIBudgetEnvelope` v1 has exactly these required members:

| Member | Semantics |
| --- | --- |
| `ContractVersion` | Exactly `1`; unknown versions fail closed. |
| `GatewayNormalizedCostUnitRegistryVersion` | Exactly `1`; its closed, case-sensitive vocabulary is `USD` only. Unknown versions/units fail closed. |
| `WorkflowDefinitionVersionId` | Exact immutable definition version containing the envelope. |
| `PolicyId`, `PolicyVersionId` | Existing policy identity and exact immutable revision that authorize the envelope. A policy change requires a new definition version and a new accepted Workflow. |
| `TenantId`, `WorkspaceId` | Exact security/allocation scope. They must match the accepted Workflow and every Gateway accounting record used for admission. |
| `BudgetCeiling.Amount` | Canonical positive base-10 decimal: 1–18 total digits, 0–6 fractional digits, minimum `0.000001`, maximum `999999999999999999`. No sign, exponent, leading integer zero, trailing fractional zero, integer decimal point, whitespace, NaN, or Infinity. |
| `BudgetCeiling.CurrencyOrReferenceUnit` | Exact case-sensitive `USD`, the sole member of Gateway normalized-cost unit registry v1. Gateway actual cost, estimates, reservations, and reconciliations used for this Workflow allocation must use it. |

The full validated value is immutable. At `StartWorkflow` acceptance, Workflow Engine writes it with
the new `WorkflowId`, exact definition/policy versions, and scope as a durable Workflow snapshot.
This prevents an in-flight Workflow from silently adopting a later definition or policy revision.
There is no implicit mutation or override. A future explicit supersession mechanism requires separate
governance; this version defines none.

Amount producers MUST serialize the unique shortest canonical form above. Consumers parse the
decimal as an exact non-negative integer coefficient at common scale 6 (right-padding the fractional
digits with zeros for comparison only); they MUST NOT use binary floating point. Addition,
subtraction, ordering, and equality are exact at scale 6. No rounding, truncation, unit conversion,
or normalization of invalid input is permitted. Non-canonical input rejects instead of being
rewritten. Any mixed, unavailable, or ambiguous unit evidence fails closed.

## Admission, ownership, and accounting

Workflow Engine owns serialized, durable admission of each AI-capable Workflow step. The durable
serialization key is the exact `(TenantId, WorkspaceId, WorkflowId)` owned by Workflow Engine. For
one logical admission, `(WorkflowStepId, CommandId, ExecutionId)` is immutable lineage and the
Gateway idempotency context is fixed before any call. The check and transition to committed exposure
occur in one serialized Workflow state transition. Before a new provider dispatch, it calculates:

```text
remaining = immutable BudgetCeiling
  - Gateway-authoritative settled actual spend
  - conservative Workflow commitments not yet represented by Gateway evidence
  - Gateway-authoritative reserved/unsettled exposure
```

Gateway remains authoritative for actual provider usage/cost, `AIInvocationId`, reservation,
reconciliation, provider failover, structured repair, expiry/release, and ambiguous-effect repair.
Workflow Engine neither re-prices provider usage nor creates a reservation/cost ledger. Its durable
admission record is a decision/audit projection that references Gateway evidence; it is not a second
accounting ledger. Its pre-acceptance commitment is the exact approved conservative maximum exposure
for that logical admission in the envelope unit. It is not provider repricing or a Gateway
reservation. Once Gateway acceptance exists, the commitment points to the resulting
`AIInvocationId` and Gateway reservation evidence; the same exposure is never counted twice.

### Admission-to-Gateway binding and fenced handoff

The durable logical admission key is the existing tuple
`(TenantId, WorkspaceId, WorkflowId, WorkflowStepId, CommandId, ExecutionId)`; no admission,
budget, reservation, or digest identity is introduced. The committed Workflow transition records
this tuple, exact definition/policy/scope, exact immutable Skill/Capability binding, conservative
committed exposure, the existing scoped Gateway idempotency key, and the Workflow's existing durable
transition version. That transition version is a fence only, not an identity. The normative
serialized value is [Workflow AI Budget Admission Binding v1](schemas/workflow-ai-budget-admission-binding-v1.schema.json).

Workflow Engine creates the binding in the same atomic transition that changes the admission to
`Committed`. Skill Runtime may only propagate the exact validated binding from the dispatched
`DispatchExecutionAttempt`; it MUST NOT create, replace, downgrade, or use generic metadata as a
substitute. Before calling Gateway, Skill Runtime resolves the exact immutable Skill/Capability
route. A resolved AI Gateway route without a matching committed binding fails closed. A resolved
non-AI route never calls Gateway and does not require a binding.

Gateway atomically accepts or replays the existing scoped idempotency key together with the exact
binding and either records one `AIInvocationId` or a rejection/no-acceptance outcome. Gateway
validates that the binding matches the durable committed Workflow admission, current fence, request
scope/lineage, exact Capability binding, and its idempotency context. A missing, stale, released,
rejected, cross-scope, malformed, unknown-version, or mismatched binding/request/evidence is
rejected before acceptance and provider preparation. This is a fenced composition of two local
atomic transitions, not a distributed replacement of ownership: Workflow owns the commitment;
Gateway owns acceptance, reservation, provider effect, and accounting.

The Workflow commitment remains counted after acceptance. It is replaced only by matching,
same-Tenant/Workspace/Workflow, same-unit Gateway evidence under that one `AIInvocationId`. Before
terminal reconciliation, the counted contribution is the greater of the committed conservative
exposure and Gateway's durable reservation or provider-effect exposure; a smaller reservation never
creates an undercount. Only Gateway terminal reconciliation proving the remaining effect is closed
may replace that contribution with settled actual cost and release the difference. Missing,
mismatched, non-monotonic, ambiguous, or unit-incompatible evidence retains the conservative
commitment and fails closed for later admissions.

The durable state machine is:

1. `Requested`: logical lineage and conservative maximum exposure recorded; provider dispatch is prohibited.
2. `PendingAdmission`: current authorization, scope, source, unit, and Gateway evidence validation is in progress; provider dispatch is prohibited.
3. `Committed`: serialized capacity check succeeded and the conservative Workflow exposure is counted before the Gateway call.
4. `GatewayAccepted`: the same Gateway idempotency context resolved an `AIInvocationId`; the commitment is correlated to Gateway reservation/effect evidence.
5. `Settling`: provider/recovery evidence exists but Gateway accounting is not terminal; conservative exposure remains.
6. `Reconciled`: Gateway terminal accounting is referenced and settled actual cost supplies cumulative spend.
7. `Released`: permitted only when Gateway idempotency recovery proves no acceptance/provider effect, or after Gateway authoritative release/expiry evidence. A timeout or missing response alone cannot release exposure.
8. `Rejected`: no commitment and no provider dispatch occurred.

Only `Committed` may initiate the idempotent Gateway handoff. The binding, the dispatch request,
and Gateway acceptance must agree on the fixed idempotency context and every logical-admission field.
Gateway must atomically resolve that context to either rejection/no acceptance or one existing/new
`AIInvocationId`. The Workflow checkpoint may lag that acceptance; recovery therefore queries the
same Gateway idempotency context and binding and never creates a fresh logical invocation. Missing or
ambiguous handoff evidence retains conservative exposure and prohibits new dispatch. This invariant
bounds every dispatch by one committed admission or its corresponding sufficient Gateway evidence and
prevents two workers from jointly exceeding the remaining ceiling.

Missing, stale, cross-scope, non-monotonic, inconsistent, or unit-incompatible Gateway evidence
fails closed before another AI dispatch. A budget exhaustion decision also fails closed before
Gateway/provider dispatch. Gateway exact-cache remains an accepted invocation under frozen
accounting semantics.

## Replay, concurrency, and recovery

| Situation | Required contract behavior |
| --- | --- |
| Same logical command/replay | Reuse the recorded Workflow admission/terminal disposition. It does not double-admit, reserve, charge, create an `AIInvocationId`, or dispatch a provider call. |
| Concurrent workers | Workflow Engine durable concurrency control serializes admission for the exact `WorkflowId`; competitors observe the recorded decision/conflict and cannot oversubscribe the ceiling. |
| Crash before admission commit | Provider dispatch is prohibited. `Requested`/`PendingAdmission` may be retried under Workflow rules; no exposure is released because none was committed. |
| Crash after commit, before Gateway call | Provider dispatch has not occurred. Takeover reuses the same committed admission, binding, fence, and fixed Gateway idempotency context; it may perform that one handoff under Workflow rules. It must not create another commitment or binding. |
| Crash during Gateway acceptance / `AIInvocationId` creation | Outcome is ambiguous to Workflow. Commitment remains conservative; recovery resolves the same idempotency context and exact binding. New dispatch is prohibited until Gateway proves rejection/no acceptance or returns the one accepted `AIInvocationId` with matching evidence. |
| Crash after Gateway acceptance, before Workflow checkpoint | Recovery obtains the existing `AIInvocationId` and matching reservation/effect evidence through the same idempotency context and binding, advances to `GatewayAccepted`/`Settling`, and never dispatches again. |
| Crash after provider completion, before Workflow reconciliation | Recovery reuses existing `AIInvocationId`, provider-effect, usage, reservation/reconciliation, and terminal-intent evidence. Conservative exposure remains until Gateway settlement; no new dispatch. |
| Ambiguous provider effect | Preserve frozen `AI_PROVIDER_EFFECT_AMBIGUOUS`; do not permit an unsafe Workflow retry, Gateway failover, or additional spend while effect/accounting is ambiguous. |
| Same-command replay | Reuse the exact committed admission, binding, fence, and Gateway idempotency/recovery evidence. It cannot create a second commitment, `AIInvocationId`, reservation, charge, acceptance, or provider dispatch. |
| Worker takeover | Acquire serialization for the same scope key, load the durable admission, and follow its state. It reuses the exact binding and idempotency context; it cannot infer release from lease loss, timeout, or missing checkpoint. |
| Repair/failover cumulative accounting | Gateway owns all repair/failover reservation and actual-cost accumulation under the existing invocation semantics. Workflow retains conservative exposure and admits no new step until monotonic same-unit Gateway evidence proves safe remaining capacity. |
| Deterministic bypass or valid `AuthoritativeResultId` reuse | Zero Gateway/provider cost, no fabricated `AIInvocationId`, and bounded avoided-call evidence only. |

Budget evidence survives restart and replay with the Workflow snapshot and referenced canonical
Gateway records. A Workflow retry remains a new `CommandId`/`ExecutionId` under the same
`WorkflowId` and must pass admission again.

## Durable audit projection

The Workflow-owned durable projection retains, without storing raw prompts, outputs, credentials,
or provider payloads:

- initial full envelope value, `ContractVersion`, definition/policy source, and Tenant/Workspace binding;
- every admission decision, decision lineage, Workflow step/command/execution references, and replay/recovery disposition;
- Gateway evidence references, settled spend, conservative committed/reserved exposure where available, and computed remaining amount/unit;
- settled cost correlated to the existing `AIInvocationId` where an invocation exists;
- calls made, calls avoided, zero-cost bypass/reuse, and budget-exhaustion rejection; and
- reconciliation-required/fail-closed reason where evidence cannot establish a safe remaining budget.

Provider-attempt/fallback detail is recorded with its explicit availability status. Unavailable
detail is recorded as unavailable; it is never fabricated, inferred as zero, or used to reduce
conservative exposure.

This projection must not become a second cost ledger. Gateway records remain the sole source for
provider cost, reservation, reconciliation, failover, and repair accounting.

## Compatibility and activation

| Definition/envelope state | AI-capable Workflow step | Non-AI Workflow behavior | Compatibility rule |
| --- | --- | --- | --- |
| Legacy definition without an envelope | Reject before Gateway/provider dispatch when the resolved immutable Skill/Capability route is AI Gateway or cannot be proved non-AI | Unchanged | Absence is never interpreted as unlimited spend. An explicit later definition/policy version is required before AI can run. |
| v1 envelope, exact supported version and scope | Admit only after serialized evidence-backed check | Unchanged | Enforce this document and the v1 schema. |
| v1 envelope with malformed, mismatched, or unsupported source/version/unit | Reject before Gateway/provider dispatch | Unchanged | No field stripping, fallback, metadata substitution, or silent downgrade. |
| Future envelope version | Reject before Gateway/provider dispatch | Unchanged | A future version requires separate governed compatibility and implementation support. |

This approved contract activates only when PR #32 is merged. It changes no historical Workflow,
does not make a legacy Workflow spend-capable, and does not authorize Phase 6 implementation.

The accepted envelope and definition snapshot freeze budget meaning, not authorization. Current
operation-specific authority, Tenant/Workspace membership, and the active/not-revoked state of the
exact `PolicyId`/`PolicyVersionId` are revalidated before each AI admission and again before Gateway
dispatch. Revocation, disablement, lost authority, stale/incompatible policy, or cross-scope evidence
fails closed without mutating, substituting, or silently upgrading the accepted snapshot.

## Review gate and non-goals

The [Service Interfaces](ServiceInterfaces.md) artifact is approved for the `StartWorkflow`
acceptance/binding amendment below. ES-017 and TDR-025 remain the approved upstream governance
direction and are not reopened by this PR.

Out of scope: runtime or persistence implementation; Workflow Engine, Skill Runtime, Gateway, or
provider changes; product Workflows; a new ledger or identity; merge/release/tag/freeze; and any
Phase 6 implementation work. The exact next step is focused GPT-5.6 Sol contract-governance review.
