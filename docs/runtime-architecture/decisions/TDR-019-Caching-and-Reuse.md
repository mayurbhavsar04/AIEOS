# TDR-019 — Caching and Reuse

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were no cache, broad semantic cache, or correctness-sensitive exact/approved caches with semantic reuse disabled by default.

## Decision

Permit exact request, deterministic structured-output, approved-result, template, and adapter prefix caches under tenant-scoped versioned keys. Semantic caching requires a later purpose-specific review. Negative caching is bounded to deterministic non-sensitive failures.

## Consequences

Keys are larger and invalidation stricter, but privacy and correctness are explicit. Cache savings remain estimates, not fabricated usage.

## Revisit evidence

The Gateway/security owners review semantic caching only when an evaluation dataset demonstrates equivalence, freshness, provenance, isolation, and protected-quality thresholds, with measured savings that justify risk. Any confirmed cross-scope exposure disables the class immediately. Migration preserves tenant isolation and source Result provenance.

