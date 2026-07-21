# TDR-007 — Typed Configuration and Secret References

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

Configuration must be validated, environment-aware, testable, reload-controlled, scope-safe, and free of embedded secrets.

## Options

Pydantic Settings plus source adapters; raw environment reads; framework configuration; centralized remote configuration immediately.

## Decision and rationale

Use immutable Pydantic Settings models assembled in the host from defaults, versioned non-secret files, environment overrides, and secret references. Adapters resolve secret values only at the point of use.

## Consequences and rejected alternatives

Startup fails fast and every setting needs explicit schema/ownership. Raw environment access is untestable and scattered; framework config leaks inward; remote config is premature.

## Compatibility and revisit

Preserves least privilege and scope. Revisit source adapters for rotation, fleet-wide policy, or multi-tenant configuration at scale.
