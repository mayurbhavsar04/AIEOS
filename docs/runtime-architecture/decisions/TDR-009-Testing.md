# TDR-009 — pytest, AnyIO, and Hypothesis Test Stack

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

AIEOS needs deterministic sync/async tests, reusable adapter conformance, state-machine properties, failure injection, and broad ecosystem support.

## Options

pytest stack; standard-library unittest; behavior-specification framework; custom harness.

## Decision and rationale

Use pytest, AnyIO, and Hypothesis. Shared fixtures provide deterministic clocks, IDs, schedulers, cancellation, provider behavior, and scoped data. Adapter ports have reusable conformance suites.

## Consequences and rejected alternatives

Property tests need seed management and shrinking literacy. unittest is viable but more verbose for fixtures/async; BDD adds translation overhead; custom harness is unnecessary.

## Compatibility and revisit

Tests enforce frozen semantics. Revisit individual tools only for maintenance, performance, or unsupported runtime changes.
