# TDR-017 — Token Estimation and Budgets

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were post-hoc accounting only, provider quota reliance, or preflight estimates plus hierarchical reservations and reconciliation.

## Decision

Estimate input/output/cost before acceptance; atomically reserve concurrency-safe hard budgets across invocation, step, workflow, capability/AI Employee, and Tenant/Workspace scopes; then create `AIInvocationId` and reconcile measured or conservatively estimated actual usage. Existing scoped Command/idempotency context makes reservation replay safe without a new canonical identity. Reservations expire/release on abandonment, recover after crashes, accumulate streaming usage monotonically, and reconcile delayed usage idempotently without double charge. Reserve output before allocating context. Soft limits never override hard quality/safety requirements.

## Consequences

Estimators and pricing need versioned accuracy monitoring. Reservations add state and failure modes but prevent concurrent overspend.

## Revisit evidence

The cost owner reviews when estimate error breaches an adopted tolerance for a sustained sample, reservations materially reduce throughput despite unused budget, or provider billing cannot be reconciled within the adopted close window. Migration preserves hard-limit enforcement, scope isolation, immutable historical pricing, and Workflow Engine retry authority.
