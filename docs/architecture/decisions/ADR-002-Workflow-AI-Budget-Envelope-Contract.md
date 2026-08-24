---
title: ADR-002 — Workflow AI Budget Envelope Contract
status: In Review
owner: CTO / Architect
date: 2026-08-21
---

# ADR-002 — Workflow AI Budget Envelope Contract

## Context

Approved ES-017 and TDR-025 require Workflow Engine to govern a durable cumulative AI-budget
ceiling. Frozen contracts define immutable Workflow Definition and Policy versions, exact scope,
Command/Execution/Result identities, and Gateway-owned `AIInvocationId` accounting. They do not
define a typed, immutable, durable Workflow budget envelope. Reusing generic metadata, a policy
reference alone, correlation/causation, identity, `AuthoritativeResultId`, or idempotency would
either discard typed authority semantics or alter a frozen identity/contract meaning.

## Decision

Introduce, subject to approval, [Workflow AI Budget Envelope v1](../schemas/workflow-ai-budget-envelope-v1.schema.json)
as an additive member of the versioned [Workflow Definition Contract v2](../WorkflowDefinitionContract.md),
contained by an immutable `WorkflowDefinitionVersionId` and governed by exact
`PolicyId`/`PolicyVersionId` and exact Tenant/Workspace scope. Its governing prose and compatibility
matrix are [Workflow AI Budget Envelope Contract](../WorkflowAIBudgetEnvelopeContract.md).

Workflow Engine persists the complete validated envelope as an immutable snapshot when accepting the
Workflow and owns serialized admission. AI Gateway remains authoritative for actual cost, usage,
reservation, reconciliation, provider failover, structured repair, and ambiguous-effect accounting
under existing `AIInvocationId` semantics. The Workflow projection only references Gateway evidence;
it is not a new identity, reservation, or second ledger.

For an AI-capable exact immutable Skill/Capability route, Workflow Engine's atomic committed
admission also records [Workflow AI Budget Admission Binding v1](../schemas/workflow-ai-budget-admission-binding-v1.schema.json).
The binding's durable logical key is the existing
`(TenantId, WorkspaceId, WorkflowId, WorkflowStepId, CommandId, ExecutionId)` tuple. It carries the
existing Workflow transition version only as a fence, plus exact definition/policy/scope/capability
source, conservative committed exposure, and existing scoped Gateway idempotency key. Skill Runtime
validates and propagates this provider-neutral value unchanged; Gateway atomically accepts/replays it
with the same key and creates at most one `AIInvocationId`. It is not a new canonical identity,
digest, reservation, or accounting record.

Workflow's conservative commitment survives Gateway acceptance. It is substituted only by matching
same-scope/same-unit evidence for that binding: before terminal reconciliation Workflow counts the
greater of its committed exposure and Gateway reservation/provider-effect exposure; only Gateway
terminal reconciliation may replace it with settled actual cost and release any difference. Missing,
mismatched, stale, non-monotonic, ambiguous, or unit-incompatible evidence fails closed. The
composition preserves Workflow admission ownership and Gateway accounting ownership without a
distributed transaction or a second ledger.

An envelope is mandatory for an AI-capable Workflow step. Legacy absence, unsupported version,
scope/source mismatch, unit mismatch, or incomplete/inconsistent accounting evidence fails closed
before Gateway/provider dispatch. Deterministic bypass and valid authoritative-result reuse consume
zero provider cost and fabricate no `AIInvocationId`.

`PolicyId` identifies the stable logical policy lineage; `PolicyVersionId` binds the exact immutable
budget meaning. Both are required and neither grants current authorization. `WorkflowId` is only the
runtime association/allocation created from the accepted definition; it is not envelope identity or
source. Version 1 has no supersession: changing ceiling, unit, source, or scope requires a new
Workflow Definition version and applies only to a new accepted Workflow. Revocation or lost
authority blocks dispatch without replacing that snapshot.

## Rejected alternatives

- **A new envelope ID or content digest:** rejected because the content-immutable enclosing
  `WorkflowDefinitionVersionId` plus exact source/scope validation already supplies authoritative
  version binding. A second identity/digest would create competing equality and lifecycle rules.
- **`PolicyId` or `PolicyVersionId` alone:** rejected because a reference does not carry the typed,
  canonical ceiling/unit and source/scope binding. `PolicyId` supplies lineage while
  `PolicyVersionId` supplies the exact revision; neither may be omitted or conflated.
- **`WorkflowId` as budget identity:** rejected because it is created only at runtime and is an
  association/allocation for one accepted instance, not definition authority.
- **A Workflow-owned cost/reservation ledger:** rejected because Gateway already owns reservation,
  provider usage/cost, reconciliation, failover, repair, and ambiguity accounting. Workflow owns
  only serialized conservative commitment and an evidence-referencing projection.
- **An unbound Skill Runtime to Gateway call:** rejected because an AI-capable route must carry one
  committed Workflow admission binding matching its exact Command/Execution/Capability lineage and
  existing scoped Gateway idempotency key. Missing or mismatched evidence must fail closed rather
  than become an implicit later admission.
- **Caller-declared AI/non-AI metadata:** rejected because only the authoritative immutable
  Skill/Capability catalog can classify a route. Workflow Definition v2 binds that route exactly and
  the hosted behavioral validator derives the envelope requirement from it.
- **Generic metadata:** rejected because ignorable/free-form metadata cannot activate authoritative
  security and budget behavior.
- **Policy-only representation:** rejected because it could let an immutable definition adopt a
  different ceiling implicitly and lacks the required enclosing activation/version boundary.

## Consequences and review boundary

The focused frozen-artifact amendments are the Workflow Definition v2 contract/schema,
the governed `DispatchExecutionAttempt` v2 schema/metadata interpretation, and
[Service Interfaces](../ServiceInterfaces.md), which remains In Review for `StartWorkflow` and
AI-admission binding semantics only. ES-017 and TDR-025 remain approved upstream governance and are
not reopened. Runtime implementation remains prohibited until this ADR, schema, and interface
amendment are approved and merged, followed by a separate implementation Draft PR.

## Revisit evidence

The CTO/Architecture and Finance/Gateway owners must open a successor review if PostgreSQL evidence
shows double admission/charge, accounting cannot determine a safe remaining amount, a policy needs
multi-currency behavior, or invoice reconciliation breaches an adopted threshold. Any successor
preserves exact scope, immutable source/version binding, Workflow Engine admission ownership,
Gateway accounting authority, existing canonical identities, and fail-closed ambiguity behavior.
