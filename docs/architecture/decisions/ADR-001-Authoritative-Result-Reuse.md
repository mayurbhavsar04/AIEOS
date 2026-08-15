---
title: ADR-001 — Authoritative Result Reuse
status: Accepted
owner: CTO / Architect
date: 2026-08-15
---

# ADR-001 — Authoritative Result Reuse

## Context

ES-016 requires a deterministic, zero-model bypass when a prior classification is authoritative.
The frozen invocation path had no authorized durable-result reference. Process-local lookup cannot
survive restart or cross-worker execution, and existing identifiers have meanings that must not be
overloaded.

## Decision

Introduce a new supported `DispatchExecutionAttempt` command version, v2. It adds optional typed
metadata `AuthoritativeResultId: ResultId | absent`, as specified by the
[v2 schema](../schemas/dispatch-execution-attempt-v2.schema.json). It propagates only to
`SkillInput.authoritative_result_id: str | None`; it is metadata, not `{statement}` payload and not
`CapabilityPolicyContext`.

Skill Runtime owns resolution against its existing durable execution/result repository. A supplied
reference is valid only when it identifies an immutable terminal `Succeeded` Result from the
authorized capability/execution boundary; Tenant and Workspace exactly match; Capability ID and
capability contract version exactly match; protected durable execution evidence proves the new
normalized source statement exactly matches (a stored canonical digest is acceptable); its output
passes the current capability contract; and the caller is authorized both to read the Result and
invoke the Capability. Any missing, unauthorized, cross-scope, nonterminal, incompatible,
malformed, or input-mismatched reference fails closed before Gateway invocation.

Valid reuse creates no `AIInvocationId`, Gateway call, provider call, or model tokens. Skill Runtime
records source-result lineage and avoided-cost evidence through the existing audit/accounting
evidence. It creates no duplicate ledger or persistence subsystem. Same-command replay returns its
existing outcome under normal replay/idempotency. A new execution nevertheless has a new
`ExecutionId`, `CommandId`, idempotency scope, and terminal Result.

## Rejected substitutes

| Candidate | Rejected because |
| --- | --- |
| `CausationId` | Identifies the immediate cause, not an optional reusable authoritative result. |
| `CorrelationId` | Groups related work; it is not evidence or Result identity. |
| `ExecutionId` | Identifies exactly one attempt and cannot be reused by another execution. |
| `IdempotencyKey` | Deduplicates the same logical request; it cannot request reuse by a new execution. |
| `MemoryId` | Memory content is untrusted context and Memory does not own classifications. |
| `ValueReference` | Belongs to a Result but is not exposed through the invocation envelope. |
| Authorization or policy reference | Governs permission or policy, not a classification result. |
| Free-form metadata | Is non-authoritative unless explicitly typed, versioned, validated, and governed. |

Existing `ResultId` is reused because it already identifies the immutable terminal authoritative
outcome. Inventing a separate canonical identity would duplicate that identity and create unclear
ownership or lineage.

## Compatibility and ownership

This is deliberately not a v1 ignorable extension. A v2 producer requires a v2-capable Skill
Runtime; unsupported v2 is rejected with no downgrade or field stripping. v1 remains supported and
its behavior is unchanged. The v2 field does not alter Domain identity, ES-007 Result semantics,
Memory ownership/contracts, AI Gateway contracts, TDR-018, provider adapters, runtime
implementation, or persistence design.

Lookup, authorization, isolation, replay, idempotency, lineage, and avoided-cost evidence are
Skill Runtime responsibilities at its existing execution boundary. AI Gateway remains outside valid
reuse and retains its existing invocation/provider authority for ordinary execution. Workflow Engine
retains retry authority.

## Consequences

The v2 path gives ES-016 an explicit durable, provider-neutral authorization reference for
deterministic reuse. It requires focused contract governance and implementation support before use;
PR #28 remains paused until this ADR and related contract changes are approved and merged.

## Review and revisit

Review must confirm that the durable repository can supply every required protected evidence check
without changing persistence ownership. If not, implementation stops for governance rather than
using process-local state, another existing identifier, Memory, or free-form metadata as a
workaround.
