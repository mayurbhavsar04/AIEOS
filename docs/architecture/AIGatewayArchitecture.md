---
title: AI Gateway Architecture v1
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-08-03
---

# AI Gateway Architecture v1

## Purpose and authority

This document implements [ES-012](../engineering-specifications/ES-012-AI-Gateway-and-Token-Governance.md) within the frozen [Service Interfaces](../architecture/ServiceInterfaces.md), [Error and Result Model](../architecture/ErrorResultModel.md), and [Observability Model](../architecture/ObservabilityModel.md). It selects no production provider.

## Component boundary

AI Gateway owns provider-neutral invocation acceptance, `AIInvocationId`, policy validation, capability/model resolution, budget reservation, provider adaptation, bounded in-invocation fallback, normalized output/error, usage reconciliation, structured validation, and cancellation/deadline propagation.

It does not own product prompts, Memory, Skill execution, factual verification, workflow progression, or workflow retries.

```mermaid
flowchart LR
    SR["Skill Runtime"] -->|"InvokeAI"| GW["AI Gateway"]
    GW --> POL["Policy and Budget"]
    GW --> REG["Model Capability Registry"]
    GW --> PP["Prompt and Context Pipeline"]
    GW --> CA["Cache Boundary"]
    GW --> PA["Provider Adapter Port"]
    PA --> EXT["External AI Provider"]
    GW --> VAL["Output Validator"]
    GW --> ACC["Usage and Cost Accounting"]
    GW --> OBS["Observability"]
```

## End-to-end invocation

```mermaid
sequenceDiagram
    participant S as Skill Runtime
    participant G as AI Gateway
    participant P as Prompt Pipeline
    participant R as Model Registry
    participant B as Budget Ledger
    participant A as Provider Adapter
    participant V as Validator
    S->>G: InvokeAI scoped request
    G->>G: Validate identity, policy, authorization
    G->>P: Build minimum sufficient context
    P-->>G: Versioned assembly and token estimate
    G->>R: Resolve eligible candidates
    R-->>G: Capability/pricing snapshots
    G->>B: Reserve hard budget
    B-->>G: Reservation accepted
    G->>A: Provider-neutral invocation mapping
    A-->>G: Normalized response and measured usage
    G->>V: Validate schema and safety outcome
    V-->>G: Valid or bounded repair evidence
    G->>B: Reconcile actual usage/cost
    G-->>S: Immutable terminal Result or Error
```

## Provider adapter isolation

```mermaid
flowchart TB
    GW["Provider-neutral Gateway"] --> PORT["Adapter Port"]
    subgraph BOUNDARY["Provider boundary"]
        PORT --> AD["Provider Adapter"]
        AD --> AUTH["Credential Reference"]
        AD --> MAP["Request/Response Mapping"]
        AD --> EXT["Provider Endpoint"]
    end
    AD -->|"normalized only"| GW
```

Adapters own credential use, logical-to-provider model translation, protocol mapping, usage mapping, error translation, streaming conversion, cancellation/timeout, and minimized provider safety metadata. Raw response retention is prohibited by default; an opaque encrypted reference requires explicit diagnostic policy and retention.

## Canonical lifecycle

AI Invocation states remain `Requested`, `PolicyValidated`, `ProviderSelected`, `Prepared`, `Invoked`, `Retrying`, `Succeeded`, `Failed`, `TimedOut`, and `Cancelled`. Provider attempts are child operational records. Their retry/fallback does not create a new `AIInvocationId` or `ExecutionId`.

```mermaid
stateDiagram-v2
    [*] --> Requested
    Requested --> PolicyValidated
    PolicyValidated --> ProviderSelected
    ProviderSelected --> Prepared
    Prepared --> Invoked
    Invoked --> Retrying: bounded provider failure
    Retrying --> Invoked: eligible attempt
    Invoked --> Succeeded
    Invoked --> Failed
    Invoked --> TimedOut
    Invoked --> Cancelled
    Succeeded --> [*]
    Failed --> [*]
    TimedOut --> [*]
    Cancelled --> [*]
```

## Structured output and repair

```mermaid
flowchart TD
    OUT["Normalized output"] --> CHECK{"Schema and policy valid?"}
    CHECK -->|"Yes"| OK["Terminal success"]
    CHECK -->|"No"| LIMIT{"Repair allowed and budget remains?"}
    LIMIT -->|"No"| FAIL["Normalized failure"]
    LIMIT -->|"Yes"| REPAIR["Minimized repair request"]
    REPAIR --> OUT
```

Repair is finite, separately metered, and cannot weaken the schema or policy. Exhaustion returns an ES-007 failure; Workflow Engine alone decides a workflow retry.

## Streaming contract

```mermaid
sequenceDiagram
    participant S as Skill Runtime
    participant G as AI Gateway
    participant A as Adapter
    S->>G: InvokeAI streaming request
    G-->>S: Immutable acceptance / AIInvocationId
    G->>A: Start provider stream
    A-->>G: Provider deltas
    G-->>S: StreamStarted and normalized deltas
    A-->>G: Usage updates and completion
    G-->>S: Immutable terminal Result/Error
    opt cancellation or timeout
        S->>G: CancelAIInvocation
        G->>A: Propagate cancellation
        G-->>S: Terminal cancellation/timeout
    end
```

Deltas are observations, not authoritative Results. Tool deltas propose arguments only. Late provider data cannot replace a terminal outcome.

## Fallback lineage

```mermaid
sequenceDiagram
    participant G as AI Gateway
    participant A1 as Provider Attempt 1
    participant A2 as Provider Attempt 2
    G->>A1: Same AIInvocationId / child attempt 1
    A1-->>G: Normalized retry-eligible failure
    G->>G: Check policy, semantics, budget, deadline, circuit
    G->>A2: Same AIInvocationId / child attempt 2
    A2-->>G: Normalized terminal outcome
```

Fallback is disabled unless allowed by immutable policy. It has a maximum attempt count and cost ceiling, cannot loop, and cannot relax hard capability, quality, safety, residency, schema, or data-handling constraints.

## Security and failure behavior

- Authorization, Tenant/Workspace scope, data classification, residency, retention compatibility, and budgets fail closed.
- Retrieved/user content is untrusted and cannot change instruction hierarchy or authority.
- Raw prompts/responses and credentials are not logged by default.
- Provider or telemetry failure is normalized without inventing success.
- Accounting reconciliation failure preserves the authoritative provider outcome but creates governed operational evidence; a hard reservation failure prevents invocation.
- Gateway cancellation does not imply provider cancellation until confirmed; terminal semantics follow ES-007.

## Extension and governance

A provider adapter preserving the port is a runtime change with a TDR for its dependency and handling boundary. Any ownership change, new identity, cross-tenant cache, changed retry authority, or provider type leakage requires architecture/contract review.

