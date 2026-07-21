# TDR-004 — Directed In-Process Command Dispatch

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

ES-004 requires directed Commands with exactly one accountable target and prohibits Event Bus transport. V1 needs testable dispatch without premature infrastructure.

## Options

In-process typed handler registry; HTTP/RPC between modules; queue-based command bus; direct service calls only.

## Decision and rationale

Define a Command Dispatcher port and initial in-process async registry keyed by target plus command type/version. Startup rejects duplicate handlers. Dispatch performs validation, authorization, idempotency, and one-handler invocation.

## Consequences and rejected alternatives

Process failure is shared initially; the port preserves later remote dispatch. HTTP/RPC and queues add failure modes; direct calls do not consistently enforce frozen envelopes and routing.

## Compatibility and revisit

Commands never enter Event Bus. Revisit when a component is extracted or durable remote command delivery becomes necessary.
