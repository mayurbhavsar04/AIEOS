# TDR-010 — Pydantic v2 Contract Validation and JSON Schema

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

Frozen immutable Commands, Events, Results, Errors, service inputs, and telemetry require strict typed validation, versioned schemas, and compatibility fixtures.

## Options

Pydantic v2; dataclasses plus manual validators; attrs/marshmallow; generated protobuf models.

## Decision and rationale

Use strict, frozen Pydantic v2 models at trust boundaries and generate JSON Schema as a reproducible artifact. Domain logic may use frozen dataclasses/value types where validation serialization is unnecessary.

## Consequences and rejected alternatives

Validation models must not become persistence or provider models. Pydantic is a dependency in contracts; manual validation risks drift; protobuf is premature without transport selection.

## Compatibility and revisit

Schema snapshots enforce frozen meaning. Revisit when an approved non-Python consumer requires a language-neutral source model, validation profiling misses an adopted boundary-latency objective, or a formally approved wire format cannot be represented without semantic loss. Contract owners evaluate compatibility fixtures and benchmarks; migration preserves frozen identities, validation semantics, versions, and generated-schema provenance.
