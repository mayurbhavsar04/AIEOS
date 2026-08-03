# TDR-021 — Fallback and Circuit Boundary

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were no fallback, unbounded provider retries, Workflow-owned provider retry, or bounded Gateway fallback inside one invocation.

## Decision

Allow policy-approved provider retries/fallback only inside one `AIInvocationId`, with child attempt identities, equivalent hard requirements, finite attempt/cost/deadline limits, and operational circuit evidence. Workflow Engine alone owns workflow retry and new `ExecutionId` creation.

## Consequences

Fallback improves availability but can increase cost and semantic variance; quality equivalence and lineage are mandatory. Circuits cannot decide business outcomes.

## Revisit evidence

The reliability owner reviews when fallback causes a protected-quality regression, retry amplification exceeds adopted limits, or provider incidents breach availability despite tuned circuits. Migration preserves one-invocation lineage, ES-007 normalization, budgets, and Workflow Engine authority.

