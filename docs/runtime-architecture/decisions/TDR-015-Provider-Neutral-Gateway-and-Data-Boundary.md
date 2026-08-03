# TDR-015 — Provider-Neutral Gateway and Data Boundary

- **Status:** Proposed
- **Date:** 2026-08-03

## Context and options

Options were direct provider SDK use by callers, one universal provider library, or frozen AI Gateway ports with isolated adapters.

## Decision

Use provider-neutral Gateway contracts and provider-specific adapters. Credentials, model-name translation, SDK objects, raw response handling, provider retention/region rules, and protocol mapping terminate inside adapters. No production provider is selected in Phase 1.

## Consequences

Adapters require conformance suites and normalized feature degradation. Callers cannot use vendor-only features without a reviewed contract extension. This maximizes replacement and least-data enforcement.

## Revisit evidence

The AI Gateway owner evaluates a successor TDR when two independent adapters cannot express a required approved capability without unsafe loss, provider isolation causes a measured service-objective breach after adapter tuning, or regulation requires a different execution boundary. Migration preserves provider-neutral callers, credentials isolation, `AIInvocationId`, Results/Errors, budgets, and retry authority.

