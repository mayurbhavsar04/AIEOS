# TDR-002 — Monorepo and Modular Monolith

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

Frozen components need enforceable boundaries, atomic contract changes, simple local development, and low operating cost without premature distribution.

## Options

One modular-monolith monorepo; monorepo microservices; polyrepo services.

## Decision and rationale

Use one monorepo and one initial host process with component packages and owned ports. This matches Architecture v1.0 and enables cross-contract validation without network complexity.

## Consequences and rejected alternatives

CI must avoid becoming slow and boundary tooling is mandatory. Components cannot assume co-location. Microservices and polyrepos add version coordination, deployment, network failure, and tracing costs before measured need.

## Compatibility and revisit

Preserves every frozen boundary. Revisit only when production evidence shows a sustained scaling/service-objective conflict, incident analysis requires independent failure containment, a security/compliance boundary requires process isolation, or release history shows independently owned deployment cadence. The architecture owner evaluates the evidence through an ADR; extraction preserves contracts, ownership, lineage, and retry authority.
