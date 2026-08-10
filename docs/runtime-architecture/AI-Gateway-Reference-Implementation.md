---
title: AI Gateway Reference Implementation
version: 1.0
status: Draft
owner: AI Gateway
last_updated: 2026-08-10
---

# AI Gateway Reference Implementation

## Purpose

This document describes the offline implementation of
[ES-013](../engineering-specifications/ES-013-AI-Gateway-Reference-Implementation.md). It proves the
frozen [AI Gateway architecture](../architecture/AIGatewayArchitecture.md) without a real provider.

## Runtime structure

```mermaid
flowchart LR
    HOST["Reference host"] --> GW["ReferenceAIGateway"]
    GW --> STORE["Atomic reference state / PostgreSQL"]
    GW --> CATALOG["Gateway-internal model catalog"]
    GW --> PROMPT["Minimum-context assembler"]
    GW --> CACHE["Scoped exact cache"]
    GW --> ADAPTER["Provider-neutral adapter port"]
    ADAPTER --> E["Economy mock"]
    ADAPTER --> Q["Quality mock"]
    GW --> OBS["ES-008 observation port"]
```

`ReferenceAIGateway` implements the established `invoke` port and adds explicit `accept`, `execute`,
and `stream` operations for conformance tests and the reference host. `ReferenceGatewayStore`
atomically owns admission replay, invocation records, budget reservations, exact content cache, and
usage reconciliation for the offline runtime. It is an adapter seam, not a new platform component.
`PostgresAIGatewayStore` implements the same port with durable compare-and-set execution claims,
expiring leases, claim generations, provider-effect evidence, monotonic usage events, recoverable
terminal intents, and exactly-once authoritative terminal checkpoints.

## Invocation flow

```mermaid
sequenceDiagram
    participant C as Caller
    participant G as AI Gateway
    participant S as Reference store
    participant A as Mock adapter
    C->>G: provider-neutral request
    G->>G: bounded admission preflight
    G->>S: atomic accept + AIInvocationId
    G-->>C: immutable acknowledgement Result
    G->>G: policy, context, routing
    G->>S: reserve under AIInvocationId
    G->>S: claim lease + prepare effect key
    G->>A: normalized mock invocation + effect key
    A-->>G: normalized content and usage
    G->>S: effect evidence + monotonic reconciliation
    G->>S: terminal intent + terminal checkpoint
    G-->>C: fresh immutable terminal Result
```

Admission replay is keyed by Tenant/Workspace and scoped `IdempotencyKey`; payload mismatch fails.
Every post-acceptance budget transition is keyed by `AIInvocationId`. A stale execution lease can be
reclaimed, but recovery continues the same invocation and never becomes a Workflow retry decision.
Concurrent workers either own the active claim or wait for its immutable terminal outcome.

## Routing and token minimization

Candidates are removed when unhealthy, deprecated, incapable, below quality, outside context/output
limits, disallowed by adapter/residency policy, or above the hard cost ceiling. Remaining candidates
sort by estimated cost, latency tier, then stable internal key. Context is sorted by mandatory status,
relevance, and stable reference; duplicate reference/version pairs are removed. Optional content is
truncated before mandatory policy/evidence content.

## Cache and lineage

The exact cache key includes scope, template version, the exact canonical assembled system/task and
selected/truncated context content, context provenance and stage, input/output bounds, capability and
route constraints, schema, safety/data/cache policy, locale, and deterministic parameters. Values
contain only validated content, usage, expiry, and bounded provenance. A hit follows acceptance and
creates a new `AIInvocationId` and `ResultId`; prior authoritative identity or causation is never
reused.

## Structured and streaming paths

Structured JSON is validated and measured outside the mock model before success. A malformed value
receives at most the configured repair attempts; every repair is admitted against the remaining token
and invocation cost envelope and its actual usage is reconciled. Streaming checks the cumulative
output bound before exposing each delta and durably records monotonic provider usage as it arrives.
Provider-reported partial usage outranks later estimates. Streaming produces acknowledgement, stream
start, ordered deltas, usage, and one terminal Result; deltas are never authoritative.

## Mock provider matrix

Two adapters model economy and quality tiers. Configurable behavior covers success, structured output,
streaming, transient/permanent failure, timeout, cancellation, malformed output, low confidence,
missing usage, and policy rejection. Both run entirely in process and have no SDK, credential, DNS, or
HTTP dependency.

## Local use

Run canonical validation with `./scripts/check`. Start the host with `./scripts/run-host`, then submit
`POST /reference/ai` with a prompt and idempotency key. The response exposes only safe invocation,
Result, route, usage, and cache metadata; it does not expose provider objects or credentials.

## Deferred work

Production provider adapters, semantic cache, embeddings/vector retrieval, provider tokenizer
calibration, tool execution, product prompts, and AI Employee logic require later reviewed milestones.
