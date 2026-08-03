# TDR-022 — AI Usage and Cost Accounting

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were provider invoices only, telemetry-only estimates, or immutable invocation-level accounting with pricing versions, reservation, reconciliation, and allocation.

## Decision

Record estimated and actual normalized usage/cost per invocation and retain immutable pricing snapshot references as non-canonical catalog values. Before acceptance, `CommandId` and the scoped `IdempotencyKey` govern admission and replay lookup only. Acceptance atomically creates the Gateway-owned `AIInvocationId`; after acceptance, that `AIInvocationId` is the authoritative subject for durable hierarchical reservation, reservation replay, recovery, streaming or partial usage accumulation, reconciliation, release or expiry, overrun handling, audit, and observability. A different `CommandId` replaying the same accepted logical request resolves to the existing `AIInvocationId` and cannot create a second reservation or charge. Attribute cost to workflow/skill/capability/product and record cache/compression/avoidance savings separately. Missing usage is conservatively estimated and labeled. Recovery, expiry/release, streaming accumulation, delayed usage, and replay cannot double-charge. No new canonical reservation or accounting identity is introduced.

## Consequences

Accounting adds durable records and catalog governance. It never becomes workflow state and does not rewrite authoritative Results.

## Revisit evidence

Finance and Gateway owners review when provider invoices cannot reconcile within an adopted variance/window, allocation cannot support approved pricing decisions, or storage/retention violates a documented obligation. Migration preserves historical pricing identity, Tenant/Workspace scope, ES-008 privacy, and result authority.
