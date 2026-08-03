# TDR-019 — Caching and Reuse

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were no cache, broad semantic cache, or correctness-sensitive exact/approved caches with semantic reuse disabled by default.

## Decision

Permit exact request-content, deterministic structured-output content, template, and adapter prefix caches under tenant-scoped versioned keys. Gateway acceptance and a new `AIInvocationId` precede a cache hit; every operation creates a new immutable `ResultId`. Cache entries retain content/artifact provenance but never reuse canonical outcome, invocation, command, event, correlation, causation, or Error identities. Product/business approved-result reuse belongs to the accountable pre-invocation Manager/Workflow/Skill/capability owner. Semantic caching requires a later purpose-specific review. Negative caching is bounded to deterministic non-sensitive failure content and never reuses an Error identity.

## Consequences

Keys are larger and invalidation stricter, but privacy and correctness are explicit. Cache savings remain estimates, not fabricated usage.

## Revisit evidence

The Gateway/security owners review semantic caching only when an evaluation dataset demonstrates equivalence, freshness, provenance, isolation, and protected-quality thresholds, with measured savings that justify risk. Any confirmed cross-scope exposure disables the class immediately. Migration preserves tenant isolation and source Result provenance.
