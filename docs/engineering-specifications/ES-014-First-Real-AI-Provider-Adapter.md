---
title: ES-014 — First Real AI Provider Adapter
version: 1.0
status: Draft
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 6 Phase 3
last_updated: 2026-08-11
---

# ES-014 — First Real AI Provider Adapter

## Objective and authority

Add one opt-in OpenAI Responses API adapter behind the frozen provider-neutral Gateway. ES-012,
ES-013, TDR-015 through TDR-023, and all frozen Architecture, Domain, contract, and runtime baselines
remain authoritative and unchanged. OpenAI is first, not exclusive or permanent.

## Boundary, model, and configuration

`ai_provider_openai` owns credentials, model translation, HTTP payloads, stream events, usage,
errors, schema hints, and retention caveats. Only neutral Gateway types cross the boundary. Raw
prompts, responses, keys, headers, and provider payloads are not logged.

The internal `economy-text-v1` maps to a pinned snapshot. Its catalog records capabilities, limits,
tiers, replaceable prices, pricing version/reference, availability, handling notes, and deprecation
policy. Ordinary development and CI still compose mocks. Live mode requires both
`AIEOS_AI_PROVIDER=openai` and `OPENAI_API_KEY`; missing values fail before network access.

## Error mapping

| Provider condition | AIEOS normalization |
| --- | --- |
| 401 / 403 | authentication failed / permission denied; not retryable |
| invalid request/model/schema | `AI_PROVIDER_REJECTED`; not retryable |
| context/token limit | `AI_CONTEXT_LIMIT_EXCEEDED`; not retryable |
| 429 | `AI_PROVIDER_RATE_LIMITED`; retryable advisory |
| 500/502/504/network | `AI_PROVIDER_TEMPORARILY_UNAVAILABLE`; retryable advisory |
| 503 | `AI_PROVIDER_OVERLOADED`; retryable advisory |
| timeout / cancellation | timeout normalization / Gateway cancellation handling |
| malformed or failed stream | malformed response / stream failure |
| unknown durable dispatch | frozen `AI_PROVIDER_EFFECT_AMBIGUOUS`; never replay blindly |

Workflow Engine remains sole workflow retry owner.

## Cost, streaming, structure, and observability

Input/output tokens map to `AIUsage`; cached input and reasoning detail map when present. Gateway
still owns estimates, reservations, reconciliation, fallback, repair, and terminal accounting. The
adapter cannot bypass replay, cache, cheapest-capable routing, minimum context, strict bounds,
approved escalation, bounded repair/fallback, or cumulative caps.

Native JSON Schema is only a generation aid; Gateway validation stays authoritative. Text deltas
are emitted incrementally and Gateway enforces bounds before visibility, persists usage/checkpoints,
and owns the terminal Result. Telemetry uses abstract model/provider identity, latency, usage, cost
variance, lifecycle, normalized errors, rate-limit and attempt metadata—never sensitive content.

## Security, privacy, and durability

Provider retention, ZDR eligibility, and regional processing depend on account and feature. Catalog
handling/residency policy must admit a request. Tenant/workspace scoping and prompt-injection
boundaries remain unchanged; no tools execute in this phase.

No compatible provider replay key is documented. The Phase 2 durable effect boundary replays only
completed evidence and fails unknown dispatch as ambiguous. Exactly-once is not claimed.

## Validation and provider #2

Offline tests cover mappings, incremental streams, usage, credentials, errors, cancellation,
catalog isolation, and opaque effects. Frozen mock/reference and PostgreSQL suites remain mandatory.
Separate `live_provider` tests use tiny prompts and strict outputs only with reviewer opt-in and a
secret. They must pass before release; missing credentials do not block Draft CTO review after
offline completion.

Provider #2 later adds an isolated adapter, catalog, and conformance suite without changing Gateway
contracts, identities, retries, budgets, or default mock composition.
