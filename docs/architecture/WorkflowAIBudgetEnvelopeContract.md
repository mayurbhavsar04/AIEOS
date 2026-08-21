---
title: Workflow AI Budget Envelope Contract
version: 0.1
status: In Review
owner: CTO / Architect
last_updated: 2026-08-21
---

# Workflow AI Budget Envelope Contract

## Purpose and governed boundary

This is the smallest additive contract required by
[ES-017](../engineering-specifications/ES-017-Governed-AI-Workflow-Execution.md) and
[TDR-025](../runtime-architecture/decisions/TDR-025-Workflow-AI-Budget-and-Recovery.md).
It defines `WorkflowAIBudgetEnvelope` v1, a typed member of one immutable
`WorkflowDefinitionVersionId`, governed by one exact `PolicyVersionId`. The normative serialized
shape is [Workflow AI Budget Envelope v1](schemas/workflow-ai-budget-envelope-v1.schema.json).

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
| `WorkflowDefinitionVersionId` | Exact immutable definition version containing the envelope. |
| `PolicyId`, `PolicyVersionId` | Existing policy identity and exact immutable revision that authorize the envelope. A policy change requires a new definition version and a new accepted Workflow. |
| `TenantId`, `WorkspaceId` | Exact security/allocation scope. They must match the accepted Workflow and every Gateway accounting record used for admission. |
| `BudgetCeiling.Amount` | Positive, base-10 decimal normalized-cost ceiling. Absence, zero, a negative value, or an implicit unlimited value is invalid. |
| `BudgetCeiling.CurrencyOrReferenceUnit` | The controlled Gateway normalized-cost currency/reference unit. Gateway `ActualCost`, conservative estimated charges, reservations, and reconciliations used for this Workflow allocation must use this exact unit. |

The full validated value is immutable. At `StartWorkflow` acceptance, Workflow Engine writes it with
the new `WorkflowId`, exact definition/policy versions, and scope as a durable Workflow snapshot.
This prevents an in-flight Workflow from silently adopting a later definition or policy revision.
There is no implicit mutation or override. A future explicit supersession mechanism requires separate
governance; this version defines none.

## Admission, ownership, and accounting

Workflow Engine owns serialized, durable admission of each AI-capable Workflow step. Before a new
provider dispatch, it uses exact-scope Gateway accounting evidence to calculate:

```text
remaining = immutable BudgetCeiling
  - Gateway-authoritative settled actual spend
  - conservative committed/reserved exposure
```

Gateway remains authoritative for actual provider usage/cost, `AIInvocationId`, reservation,
reconciliation, provider failover, structured repair, expiry/release, and ambiguous-effect repair.
Workflow Engine neither re-prices provider usage nor creates a reservation/cost ledger. Its durable
admission record is a decision/audit projection that references Gateway evidence; it is not a second
accounting ledger.

Missing, stale, cross-scope, non-monotonic, inconsistent, or unit-incompatible Gateway evidence
fails closed before another AI dispatch. A budget exhaustion decision also fails closed before
Gateway/provider dispatch. Gateway exact-cache remains an accepted invocation under frozen
accounting semantics.

## Replay, concurrency, and recovery

| Situation | Required contract behavior |
| --- | --- |
| Same logical command/replay | Reuse the recorded Workflow admission/terminal disposition. It does not double-admit, reserve, charge, create an `AIInvocationId`, or dispatch a provider call. |
| Concurrent workers | Workflow Engine durable concurrency control serializes admission for the exact `WorkflowId`; competitors observe the recorded decision/conflict and cannot oversubscribe the ceiling. |
| Crash before dispatch | Recovery resolves the persisted admission/step checkpoint. A recorded admission with no Gateway acceptance is safely released as non-committing; an existing Gateway evidence reference is reconciled instead. Restart alone creates no provider effect or charge. |
| Crash after Gateway/provider completion | Recovery resolves the existing `AIInvocationId`, provider-effect, usage, reservation/reconciliation, and terminal-intent evidence. It does not dispatch again. |
| Ambiguous provider effect | Preserve frozen `AI_PROVIDER_EFFECT_AMBIGUOUS`; do not permit an unsafe Workflow retry, Gateway failover, or additional spend while effect/accounting is ambiguous. |
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

This projection must not become a second cost ledger. Gateway records remain the sole source for
provider cost, reservation, reconciliation, failover, and repair accounting.

## Compatibility and activation

| Definition/envelope state | AI-capable Workflow step | Non-AI Workflow behavior | Compatibility rule |
| --- | --- | --- | --- |
| Legacy definition without an envelope | Reject before Gateway/provider dispatch | Unchanged | Absence is never interpreted as unlimited spend. An explicit later policy/version is required before AI can run. |
| v1 envelope, exact supported version and scope | Admit only after serialized evidence-backed check | Unchanged | Enforce this document and the v1 schema. |
| v1 envelope with malformed, mismatched, or unsupported source/version/unit | Reject before Gateway/provider dispatch | Unchanged | No field stripping, fallback, metadata substitution, or silent downgrade. |
| Future envelope version | Reject before Gateway/provider dispatch | Unchanged | A future version requires separate governed compatibility and implementation support. |

This contract is proposed/In Review only and is not activated. It changes no historical Workflow,
does not make a legacy Workflow spend-capable, and does not authorize Phase 6 implementation.

## Review gate and non-goals

The frozen [Service Interfaces](ServiceInterfaces.md) artifact is In Review solely for the
`StartWorkflow` acceptance/binding amendment below. ES-017 and TDR-025 remain the approved upstream
governance direction and are not reopened by this PR.

Out of scope: runtime or persistence implementation; Workflow Engine, Skill Runtime, Gateway, or
provider changes; product Workflows; a new ledger or identity; merge/release/tag/freeze; and any
Phase 6 implementation work. The exact next step is focused GPT-5.6 Sol contract-governance review.
