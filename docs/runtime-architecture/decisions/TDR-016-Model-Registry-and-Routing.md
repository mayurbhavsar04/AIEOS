# TDR-016 — Model Registry and Routing

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were hard-coded model names, provider-owned selection, lowest-price-only routing, or a versioned capability catalog with deterministic policy routing.

## Decision

Maintain a provider-neutral, versioned model routing/pricing catalog as an internal AI Gateway data port. It is not a top-level component and not the frozen Capability Registry. Gateway maintainers own reviewed writes, freshness, and atomic activation. Filter hard requirements and choose the lowest estimated-cost eligible model; deterministic ties use policy priority, measured latency, then stable catalog value. Record explainable decisions using existing recorded-decision semantics without inventing a canonical routing identity.

## Consequences

Catalog freshness becomes operationally critical. Cheapest overall is not necessarily eligible. Quality/safety/residency remain hard constraints.

## Revisit evidence

The Gateway and quality owners review when catalog staleness causes a confirmed bad route, routing misses an adopted cost/latency objective for a sustained window without protected-quality gains, or evaluation shows deterministic policy materially underperforms another explainable policy. Migration preserves capability-first callers, auditability, hard constraints, and provider neutrality.
