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
as an additive member of an immutable `WorkflowDefinitionVersionId`, governed by exact
`PolicyId`/`PolicyVersionId` and exact Tenant/Workspace scope. Its governing prose and compatibility
matrix are [Workflow AI Budget Envelope Contract](../WorkflowAIBudgetEnvelopeContract.md).

Workflow Engine persists the complete validated envelope as an immutable snapshot when accepting the
Workflow and owns serialized admission. AI Gateway remains authoritative for actual cost, usage,
reservation, reconciliation, provider failover, structured repair, and ambiguous-effect accounting
under existing `AIInvocationId` semantics. The Workflow projection only references Gateway evidence;
it is not a new identity, reservation, or second ledger.

An envelope is mandatory for an AI-capable Workflow step. Legacy absence, unsupported version,
scope/source mismatch, unit mismatch, or incomplete/inconsistent accounting evidence fails closed
before Gateway/provider dispatch. Deterministic bypass and valid authoritative-result reuse consume
zero provider cost and fabricate no `AIInvocationId`.

## Consequences and review boundary

The focused frozen-artifact amendment is [Service Interfaces](../ServiceInterfaces.md), which moves
to In Review for `StartWorkflow` acceptance/binding semantics only. ES-017 and TDR-025 remain
approved upstream governance and are not reopened. Runtime implementation remains prohibited until
this ADR, schema, and interface amendment are approved and merged, followed by a separate
implementation Draft PR.

## Revisit evidence

The CTO/Architecture and Finance/Gateway owners must open a successor review if PostgreSQL evidence
shows double admission/charge, accounting cannot determine a safe remaining amount, a policy needs
multi-currency behavior, or invoice reconciliation breaches an adopted threshold. Any successor
preserves exact scope, immutable source/version binding, Workflow Engine admission ownership,
Gateway accounting authority, existing canonical identities, and fail-closed ambiguity behavior.
