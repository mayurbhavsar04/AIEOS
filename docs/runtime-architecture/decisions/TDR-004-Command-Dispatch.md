# TDR-004 — Directed In-Process Command Dispatch

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

ES-004 requires directed Commands with exactly one accountable target and prohibits Event Bus transport. V1 needs testable dispatch without premature infrastructure.

## Options

In-process typed handler registry; HTTP/RPC between modules; queue-based command bus; direct service calls only.

## Decision and rationale

Define a Command Dispatcher port and initial in-process async registry keyed by target plus command type/version. Startup rejects duplicate handlers. Dispatch validates immutable envelope integrity, routing metadata, target/version registration, and context propagation before one-handler invocation. The accountable target revalidates semantics, authorization, invariants, scope, time constraints, and target-owned idempotency before accepting execution.

## Consequences and rejected alternatives

Process failure is shared initially; the port preserves later remote dispatch. HTTP/RPC and queues add failure modes; direct calls do not consistently enforce frozen envelopes and routing.

## Compatibility and revisit

Commands never enter Event Bus. Revisit when an approved component-extraction ADR requires cross-process dispatch, or when recorded reliability tests show that process loss cannot meet the adopted command-delivery objective. The runtime owner evaluates this evidence at each topology review; migration must preserve the ES-004 envelope, one accountable target, and target-owned authorization/idempotency.
