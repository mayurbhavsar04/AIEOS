# TDR-014 — FastAPI Initial HTTP Host

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

The first runtime needs async request ingress, typed validation integration, lifecycle hooks, test clients, and minimal framework intrusion. Transport is a host concern.

## Options

FastAPI/ASGI; Starlette; Django; gRPC-only; no network host.

## Decision and rationale

Use FastAPI with an ASGI server for the initial ingress host. Routes translate requests to frozen contracts and call Manager ports. Framework models, exceptions, and dependencies stop at ingress/composition.

## Consequences and rejected alternatives

The host adds framework dependencies but component packages remain independent. Starlette needs more validation plumbing; Django is broader than required; gRPC commits early to a transport; no host prevents realistic E2E validation.

## Compatibility and revisit

No frozen service contract becomes HTTP-specific. Revisit when an approved external API requires semantics the host cannot represent, load tests miss an adopted ingress objective after tuning, or a confirmed consumer requires another transport such as streaming. API/runtime owners evaluate contract requirements and benchmarks; additional hosts must remain translation boundaries and preserve Manager ownership and frozen service contracts.
