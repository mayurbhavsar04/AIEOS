---
title: Model Routing and Budgeting
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-08-03
---

# Model Routing and Budgeting

## Objective

Select the cheapest model that satisfies every hard requirement and enforce concurrency-safe budgets without weakening quality or giving AI Gateway workflow-retry authority.

## AI Gateway model routing catalog

This catalog is internal to AI Gateway. It is not a new architectural component and is not the frozen Capability Registry. Gateway maintainers own reviewed catalog writes, freshness, and atomic activation; callers and the Capability Registry are read-independent of its storage.

Each immutable catalog version contains:

- logical model identifier/version and provider-adapter reference;
- capability contract version;
- input/output/context limits;
- text, vision, audio, tool, structured-output, and streaming support;
- reasoning, quality, latency, and availability/health classes;
- supported regions and provider data handling/retention properties;
- pricing reference/version, currency/unit, effective time, and freshness state;
- deprecation/availability status; and
- evidence timestamp and accountable catalog maintainer.

Stale or contradictory capability/pricing data makes the candidate ineligible by default. Catalog updates are reviewed data changes, not hidden code changes.

## Routing decision

```mermaid
flowchart TD
    REQ["Validated request"] --> CAP["Filter required capabilities"]
    CAP --> SEC["Filter security, region, retention, tenant policy"]
    SEC --> QUAL["Filter minimum quality tier"]
    QUAL --> LIM["Filter token/context/output limits"]
    LIM --> AV["Filter health, deadline, fallback eligibility"]
    AV --> EST["Estimate tokens, latency, cost"]
    EST --> BUD["Filter hard budgets"]
    BUD --> ANY{"Eligible candidate?"}
    ANY -->|"No"| FAIL["Normalized no-eligible-route failure"]
    ANY -->|"Yes"| CHEAP["Choose lowest normalized estimated cost"]
    CHEAP --> TIE["Tie: policy priority, latency, stable ID"]
    TIE --> AUDIT["Record explainable decision"]
```

The route record lists candidates considered, exclusion codes, estimates/confidence, selected capability/pricing versions, policy version, tie-break result, and hard/soft constraints. Sensitive provider details are access-controlled and never emitted as unbounded metrics.

## Pre-invocation avoidance and Gateway routing

```mermaid
flowchart LR
    TASK["Task"] --> OWNER["Manager / Workflow / Skill / capability owner"]
    OWNER --> DET{"Deterministic or approved business reuse?"}
    DET -->|"Yes"| AVOID["Record decision and Result; no InvokeAI"]
    DET -->|"No"| GATE["AI Gateway accepts request and creates AIInvocationId"]
    GATE --> CACHE{"Valid scoped content cache?"}
    CACHE -->|"Yes"| HIT["Create new Result with source provenance"]
    CACHE -->|"No"| MODEL["Route cheapest capable model"]
```

## Budget scopes

| Scope | Owner/purpose |
| --- | --- |
| Invocation | AI Gateway enforces request ceiling and reservation. |
| Workflow step / workflow | Workflow Engine policy supplies remaining budget; Gateway cannot increase it. |
| Capability / AI Employee | Approved policy allocates and attributes usage. |
| Tenant/Workspace daily/monthly | Concurrency-safe ledger enforces organizational ceilings. |

Hard limits prevent reservation/invocation. Soft limits create a governed warning and allow continuation only when policy says so. The pre-acceptance estimate is reserved atomically across every applicable scope in the same acceptance boundary that creates `AIInvocationId`. Existing scoped `CommandId` and target-owned `IdempotencyKey` provide durable replay identity; no canonical reservation ID is introduced.

## Reservation and reconciliation

```mermaid
sequenceDiagram
    participant G as AI Gateway
    participant L as Budget Ledger
    participant A as Provider Adapter
    G->>L: Idempotently reserve estimated maximum under all scopes
    L-->>G: Reservation or hard-limit rejection
    G->>G: Accept and create AIInvocationId atomically
    G->>A: Invoke within reservation
    A-->>G: Usage report / availability markers
    G->>L: Reconcile actual normalized cost
    L-->>G: Reconciliation record
```

Concurrent reservations MUST not overspend a hard scope. Reservation transitions are `pending`, `committed`, `released`, or `expired` implementation-local states. Cancellation and abandonment release unused amounts; crash recovery resumes by the same scoped command/idempotency identity. Streaming usage accumulates monotonically. Missing/delayed provider usage uses conservative calculation with explicit confidence and idempotent later reconciliation. Replays never double-charge. Pricing changes never rewrite historical cost: records retain the pricing snapshot reference used.

## Progressive escalation

Escalation can add focused context, enlarge an output budget, or choose a stronger eligible model. It requires an explicit signal and remaining scope budget. It cannot relax security, residency, safety, output schema, evidence, or quality thresholds.

```mermaid
flowchart TD
    BASE["Minimal eligible route"] --> CHECK{"Meets quality/completion evidence?"}
    CHECK -->|"Yes"| END["Complete"]
    CHECK -->|"No"| SIGNAL{"Approved escalation signal?"}
    SIGNAL -->|"No"| FAIL["Normalized failure/escalation request"]
    SIGNAL -->|"Yes"| REM{"Budget and deadline remain?"}
    REM -->|"No"| FAIL
    REM -->|"Yes"| NEXT["Add only missing context or stronger capability"]
    NEXT --> CHECK
```

Maximum stages and attempts are finite. A failed invocation does not create a workflow retry.

## Fallback and circuits

Circuit state is operational provider health evidence, not business state. Fallback candidate selection reruns hard filters, preserves `AIInvocationId`, records only an implementation-local opaque provider-attempt reference, and obeys a maximum attempt/cost/deadline policy. There is no cyclic fallback graph. Circuit opening or stale health cannot cause a less safe route.

## Cache routing

Exact content/artifact caches are checked after Gateway acceptance and before routing. Semantic cache is disabled unless a separately approved policy proves equivalence and freshness. Every hit retains bounded source provenance but creates a new `ResultId` for the current `AIInvocationId`, subject, and lineage. It never reuses source `ResultId`, `ErrorId`, `AIInvocationId`, `CommandId`, `EventId`, correlation, or causation. Product/business result reuse is decided and recorded by the accountable pre-invocation owner before Gateway is called.

## Quality-cost governance

Each task class has protected quality, factuality, safety, schema, and evidence thresholds. Offline datasets and deterministic mock candidates test selection and escalation. A candidate with lower cost but failing a protected threshold is ineligible. Periodic reviews examine quality, cost, latency, failure, fallback, cache, and estimator error together.
