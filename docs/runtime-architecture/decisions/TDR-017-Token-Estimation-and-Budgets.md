# TDR-017 — Token Estimation and Budgets

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were post-hoc accounting only, provider quota reliance, or preflight estimates plus hierarchical reservations and reconciliation.

## Decision

Before acceptance, perform only coarse budget-feasibility admission with no durable hold. Acceptance atomically creates `AIInvocationId`; post-acceptance context-dependent estimation then atomically reserves concurrency-safe hard budgets across invocation, step, workflow, capability/AI Employee, and Tenant/Workspace scopes under that identity. Existing scoped Command/idempotency context governs pre-acceptance replay, while `AIInvocationId` makes reservation and reconciliation idempotent without a new canonical identity. Reservations expire/release on abandonment, recover after crashes, accumulate streaming usage monotonically, and reconcile delayed usage without double reservation or charge. A post-acceptance reservation failure produces the canonical failed Result/Error and no provider attempt or workflow retry. Reserve output before allocating context. Soft limits never override hard quality/safety requirements.

## Consequences

Estimators and pricing need versioned accuracy monitoring. Reservations add state and failure modes but prevent concurrent overspend.

## Revisit evidence

The cost owner reviews when estimate error breaches an adopted tolerance for a sustained sample, reservations materially reduce throughput despite unused budget, or provider billing cannot be reconciled within the adopted close window. Migration preserves hard-limit enforcement, scope isolation, immutable historical pricing, and Workflow Engine retry authority.
