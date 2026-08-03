# TDR-022 — AI Usage and Cost Accounting

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were provider invoices only, telemetry-only estimates, or immutable invocation-level accounting with pricing versions, reservation, reconciliation, and allocation.

## Decision

Record estimated and actual normalized usage/cost per invocation, retain pricing versions, reconcile reservations, attribute to workflow/skill/capability/product, and record cache/compression/avoidance savings separately. Missing usage is conservatively estimated and labeled.

## Consequences

Accounting adds durable records and catalog governance. It never becomes workflow state and does not rewrite authoritative Results.

## Revisit evidence

Finance and Gateway owners review when provider invoices cannot reconcile within an adopted variance/window, allocation cannot support approved pricing decisions, or storage/retention violates a documented obligation. Migration preserves historical pricing identity, Tenant/Workspace scope, ES-008 privacy, and result authority.

