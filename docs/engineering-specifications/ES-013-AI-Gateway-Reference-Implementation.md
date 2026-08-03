---
title: ES-013 — AI Gateway Reference Implementation
version: 1.0
status: Draft
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 6 Phase 2
last_updated: 2026-08-03
---

# ES-013 — AI Gateway Reference Implementation

## Objective

Implement an offline, deterministic reference runtime for the frozen
[AI Gateway architecture](../architecture/AIGatewayArchitecture.md). The runtime proves the
acceptance lifecycle, minimum-sufficient token policy, provider-neutral routing, exact caching,
budget reconciliation, structured output, streaming, fallback, security, and observability without
external credentials, provider SDKs, network calls, or product behavior.

## Authority

[ES-012](ES-012-AI-Gateway-and-Token-Governance.md), Architecture v1.0, Domain v1.0, ES-004 through
ES-008, Runtime Architecture v1.0, Durable Runtime v1.0, and TDR-015 through TDR-022 remain
authoritative. This implementation adds no canonical identity and changes no frozen boundary.

## Delivered behavior

- `AIInvocationId` is created by AI Gateway atomically with acceptance.
- acknowledgement and terminal Results are distinct immutable ES-007 records.
- admission is limited to envelope, scope, authorization, idempotency replay, and coarse budget
  feasibility.
- accepted invocations progress through `Requested`, `PolicyValidated`, `ProviderSelected`,
  `Prepared`, invocation/streaming/fallback, and one terminal state.
- the internal catalog selects the cheapest model satisfying every hard capability, quality,
  context, output, adapter, residency, health, and budget constraint using deterministic ties.
- prompt assembly ranks, deduplicates, bounds, labels, and versions context supplied by callers;
  it never retrieves Memory or treats evidence as instruction.
- output tokens are reserved before optional context and estimates are explicitly distinguishable
  from provider usage.
- exact cache entries are Tenant/Workspace scoped and contain content/provenance only. Each hit has
  a fresh invocation and Result lineage.
- hierarchical reference reservations use `AIInvocationId` for idempotent post-acceptance
  reconciliation and never create a canonical reservation identity.
- structured validation has a finite repair limit; provider fallback is finite and remains inside
  one invocation.
- stream deltas are observations; only the terminal Result is authoritative.
- two deterministic mock adapters exercise different capability, cost, and latency profiles.

## Compatibility

The existing `AIGateway.invoke()` port remains compatible with the executable reference workflow.
The richer `accept`, `execute`, and `stream` reference API is additive and provider-neutral. Provider
objects and adapter-local attempt references never cross the Gateway boundary.

## Security and cost rules

Raw prompts, responses, credentials, and unbounded identifiers are absent from default telemetry.
Authorization and exact Tenant/Workspace scope fail closed. Cost optimization follows this order:
owner-side deterministic avoidance, admission replay, exact cache, least-cost capable routing,
minimum relevant context, bounded output, then policy-approved escalation. Cost never relaxes a hard
quality, safety, capability, residency, or schema requirement.

## Validation requirements

The milestone is complete only when offline tests prove identity ownership, lifecycle order,
routing, constraints, token limits, context minimization, fresh cache lineage, scope isolation,
budget idempotency, bounded fallback/repair, streaming, error normalization, safe observability,
host execution, and no external network or credential requirement. Repository formatting, linting,
strict typing, security, secrets, dependency boundaries, documentation, frozen-baseline checks,
coverage, and `git diff --check` MUST pass.

## Known limitations and deferrals

- The reference state store is process-local; a production durable adapter follows the existing
  PostgreSQL patterns in a separately governed milestone.
- Token estimation is deterministic and conservative, not a provider tokenizer.
- The exact cache is implemented; semantic/vector cache remains prohibited.
- Mock streaming is deterministic and offline; real provider backpressure/protocol mapping is
  deferred to provider adapters.
- No provider SDK, real provider, embeddings, browser/tool execution, UI, deployment automation, or
  AI Employee behavior is included.

## Definition of done

- implementation and tests are reviewable in one Draft PR;
- all frozen baselines remain byte-for-byte unchanged;
- canonical repository validation passes; and
- no merge, tag, release, production provider call, or credential is introduced.

