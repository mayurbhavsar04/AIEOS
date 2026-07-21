# TDR-005 — Event Bus Port with In-Process Adapter

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

ES-005 defines immutable Events, duplicate tolerance, producer ownership, and events-only transport. Early validation needs deterministic local delivery and a future broker boundary.

## Options

In-process adapter behind a port; embedded broker; external broker immediately; synchronous observer callbacks without event records.

## Decision and rationale

Use an Event Bus port with an in-process adapter. Authoritative producers atomically record Events/outbox intent with owned state where required. The adapter claims pending delivery at startup and during a bounded loop, preserves metadata, records per-consumer disposition, and redelivers incomplete delivery. Consumers are idempotent and no global order is promised.

## Consequences and rejected alternatives

The first adapter has process-level failure containment only. Broker adoption later does not change contracts. Immediate brokers are operationally premature; unrecorded callbacks cannot support reliable replay/audit expectations.

## Compatibility and revisit

Event Bus transports Events only. Revisit when an approved multi-process topology requires independent delivery, recovery tests fail the adopted delivery objective, measured delivery backlog exceeds the service objective for a sustained review window, or replay must operate independently of the host. The runtime owner reviews these signals; migration preserves Event identity, authoritative recording, consumer disposition, duplicate tolerance, and events-only transport.
