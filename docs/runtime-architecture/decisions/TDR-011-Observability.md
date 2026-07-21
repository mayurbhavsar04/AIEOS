# TDR-011 — Vendor-Neutral Observability Ports

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

ES-008 requires logs, traces, metrics, audit, health, identities, redaction, and failure behavior without granting telemetry authority or vendor lock-in.

## Options

Narrow AIEOS ports with OpenTelemetry-compatible adapter; direct OpenTelemetry imports everywhere; vendor SDK; standard logging only.

## Decision and rationale

Components depend on AIEOS observability ports. The initial adapter may use OpenTelemetry APIs for traces/metrics and structured Python logging, translating ES-008 records at the edge. Audit uses a separate durable port.

## Consequences and rejected alternatives

Translation code is required but frozen semantics remain stable. Direct standards/vendor SDK imports couple components; logging alone cannot meet traces/metrics/audit requirements.

## Compatibility and revisit

Observability remains descriptive and provider-neutral. Revisit adapter choice for ecosystem, performance, or operational platform selection.
