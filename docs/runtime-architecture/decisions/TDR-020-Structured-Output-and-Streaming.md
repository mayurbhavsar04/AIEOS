# TDR-020 — Structured Output and Streaming

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were provider-native objects, text-only normalization, or provider-neutral content/stream contracts with external validation.

## Decision

Normalize content, tool proposals, usage, finish reasons, and stream deltas. Validate immutable schema versions outside the model. Permit finite budgeted repair without schema weakening. Stream deltas are non-authoritative; one immutable terminal ES-007 Result/Error completes the invocation.

## Consequences

Some provider features need adapter translation or are unavailable. Repair and streaming require conformance tests and cancellation race handling.

## Revisit evidence

The contract owner reviews when multiple adapters cannot preserve an approved modality, stream ordering/cancellation fails an adopted reliability objective, or bounded repair materially worsens quality/cost versus deterministic rejection. Migration preserves terminal immutability, schema validation, and provider isolation.

