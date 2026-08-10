---
title: ES-013 — AI Gateway Reference Implementation
version: 1.0
status: Draft
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 6 Phase 2
last_updated: 2026-08-10
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
- PostgreSQL durably records scoped acceptance/replay fingerprints, lifecycle and routing evidence,
  hierarchical reservation snapshots, provider attempts, incremental/delayed usage, terminal
  Result/Error references, and correctness-critical exact-cache metadata.
- restart recovery resumes by `AIInvocationId`; concurrent duplicate admission is serialized; usage
  events are idempotent; expiry/release and delayed reconciliation cannot double-charge an
  invocation.
- accepted nonterminal invocations use one durable execution owner, an expiring lease, and a
  compare-and-set claim generation. Recovery reclaims stale leases and consumes durable
  provider-effect/accounting evidence instead of blindly repeating completed work.
- normalized terminal intent is separate from its authoritative checkpoint, so a transient
  persistence failure remains recoverable and cannot escape the accepted public API as a raw error.
  Intent installation is fenced by execution owner/generation, records that authorizing generation,
  and is immutable after the valid write.
- exact-cache identity is content-addressed from the canonical assembled request, including selected
  and truncated context content, provenance/stage, both token bounds, policies, route constraints,
  schema, locale, and deterministic parameters.
- structured validation measures original and repaired canonical payloads, has a finite repair limit,
  and admits every repair inside the remaining token/cost envelope. A repair effect's opaque
  idempotency key is durably reserved before provider invocation so fresh-process recovery does not
  depend on adapter memory; provider fallback remains inside one invocation.
- exact-cache lookup follows the progressive-context state machine: only the current assembled stage
  and selected route are queryable, and escalation/fallback cannot be skipped by a future-stage hit.
- stream bounds are enforced before delta visibility and provider-reported partial usage is appended
  monotonically before terminal completion; only the terminal Result is authoritative.
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
host execution, and no external network or credential requirement. Mandatory real-PostgreSQL tests
also prove migration parity and downgrade/upgrade, restart recovery at every durable boundary,
concurrent admission and execution, stale-lease reclamation, recoverable terminalization, partial and
delayed usage, reservation expiry/release, terminal replay, and no double charging; CI treats any
critical PostgreSQL skip as a failure. Repository formatting,
linting, strict typing, security, secrets, dependency boundaries, documentation, frozen-baseline
checks, coverage, and `git diff --check` MUST pass.

## Known limitations and deferrals

- In-memory storage remains available only for deterministic offline composition; PostgreSQL mode
  uses the durable Gateway adapter and explicit Alembic schema.
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
