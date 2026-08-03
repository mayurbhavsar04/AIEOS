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

## Capability registry record

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

## No-model, reuse, or model decision

```mermaid
flowchart LR
    TASK["Task"] --> DET{"Deterministic solution?"}
    DET -->|"Yes"| AVOID["Record avoided invocation"]
    DET -->|"No"| CACHE{"Valid scoped reuse?"}
    CACHE -->|"Yes"| HIT["Return approved cached Result"]
    CACHE -->|"No"| MODEL["Route cheapest capable model"]
```

## Budget scopes

| Scope | Owner/purpose |
| --- | --- |
| Invocation | AI Gateway enforces request ceiling and reservation. |
| Workflow step / workflow | Workflow Engine policy supplies remaining budget; Gateway cannot increase it. |
| Capability / AI Employee | Approved policy allocates and attributes usage. |
| Tenant/Workspace daily/monthly | Concurrency-safe ledger enforces organizational ceilings. |

Hard limits prevent reservation/invocation. Soft limits create a governed warning and allow continuation only when policy says so. Reservations use exact Tenant/Workspace and attribution keys, expire safely, and reconcile atomically to measured or conservative estimated actuals.

## Reservation and reconciliation

```mermaid
sequenceDiagram
    participant G as AI Gateway
    participant L as Budget Ledger
    participant A as Provider Adapter
    G->>L: Reserve estimated maximum under all scopes
    L-->>G: Reservation or hard-limit rejection
    G->>A: Invoke within reservation
    A-->>G: Usage report / availability markers
    G->>L: Reconcile actual normalized cost
    L-->>G: Reconciliation record
```

Concurrent reservations MUST not overspend a hard scope. Missing provider usage uses conservative calculation with explicit confidence and later reconciliation. Pricing changes never rewrite historical cost: records retain the pricing version used.

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

Circuit state is operational provider health evidence, not business state. Fallback candidate selection reruns hard filters, preserves `AIInvocationId`, creates a child provider-attempt identity, and obeys a maximum attempt/cost/deadline policy. There is no cyclic fallback graph. Circuit opening or stale health cannot cause a less safe route.

## Cache routing

Exact and deterministic caches are checked before routing. Semantic cache is disabled unless a separately approved policy proves equivalence and freshness. Cache hits retain source Result identity/provenance and create a new access/accounting record rather than pretending a provider invocation occurred.

## Quality-cost governance

Each task class has protected quality, factuality, safety, schema, and evidence thresholds. Offline datasets and deterministic mock candidates test selection and escalation. A candidate with lower cost but failing a protected threshold is ineligible. Periodic reviews examine quality, cost, latency, failure, fallback, cache, and estimator error together.

