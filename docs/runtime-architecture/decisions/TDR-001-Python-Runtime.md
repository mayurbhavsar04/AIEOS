# TDR-001 — CPython 3.13 Primary Runtime

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

AIEOS needs strong typing, async I/O, mature AI/data ecosystems, rapid iteration, deterministic testing, and one coherent initial stack. The founding team has backend experience and the earlier product direction favored Python.

## Options

CPython 3.13; TypeScript/Node; Kotlin/JVM; Go; polyglot per component.

## Decision and rationale

Use CPython 3.13 as the only production language in Runtime v1. It offers the best AI ecosystem and delivery speed while type checking, immutable validation models, strict boundaries, and tests address dynamic-language risk. Avoid polyglot operational and contract duplication before evidence.

## Consequences and rejected alternatives

CPU-heavy work may require process workers or later extraction; async discipline and type gates are mandatory. Node has strong typing but weaker native AI/data breadth; JVM is robust but slower to bootstrap; Go is operationally simple but less suitable for model orchestration; polyglot is premature.

## Compatibility and revisit

No frozen semantics change. Revisit after measured CPU limits, library/security constraints, team capability changes, or an isolated component with independently justified runtime needs.
