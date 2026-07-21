# TDR-005 — Event Bus Port with In-Process Adapter

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

ES-005 defines immutable Events, duplicate tolerance, producer ownership, and events-only transport. Early validation needs deterministic local delivery and a future broker boundary.

## Options

In-process adapter behind a port; embedded broker; external broker immediately; synchronous observer callbacks without event records.

## Decision and rationale

Use an Event Bus port with an in-process adapter. Authoritative producers record events/outbox intent with owned state where required; delivery invokes registered consumers and preserves metadata. Consumers are idempotent and no global order is promised.

## Consequences and rejected alternatives

The first adapter has process-level failure containment only. Broker adoption later does not change contracts. Immediate brokers are operationally premature; unrecorded callbacks cannot support reliable replay/audit expectations.

## Compatibility and revisit

Event Bus transports Events only. Revisit for multi-process topology, durable independent delivery, throughput, or replay operations.
