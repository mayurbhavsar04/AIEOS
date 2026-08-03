---
title: AI Usage and Cost Accounting
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-08-03
---

# AI Usage and Cost Accounting

## Purpose

Provide auditable, tenant-scoped estimates, reservations, measured usage, normalized cost, savings, and allocation for every AI decision without making telemetry the source of execution truth.

## Usage record

An immutable usage record contains:

- usage-record identity and `AIInvocationId` (or avoided-invocation decision reference);
- Tenant/Workspace, Correlation, Workflow, Step, Execution, Skill, Capability, and product/AI Employee allocation references where applicable;
- logical model/version and provider-adapter reference;
- pricing version/effective reference and currency/reference unit;
- measured or estimated input, output, cached, and reasoning tokens with availability/confidence;
- estimated pre-invocation cost and actual normalized post-invocation cost;
- reservation identity, reconciliation status, and variance reason;
- cache disposition/savings and context-compression token/cost savings;
- route/budget policy version and decision reason; and
- timestamps, data classification, redaction status, and lineage.

Provider names may be retained as controlled catalog references but not leaked as SDK values or unbounded public metrics.

## Accounting flow

```mermaid
flowchart LR
    EST["Token and cost estimate"] --> RES["Scoped reservation"]
    RES --> INV["Invocation or cache/reuse decision"]
    INV --> USAGE["Measured/estimated usage"]
    USAGE --> REC["Reconcile reservation"]
    REC --> ALLOC["Allocate workflow/skill/capability/product"]
    ALLOC --> AUDIT["Immutable accounting and safe telemetry"]
```

## Avoided invocation and savings

An avoided-invocation record is useful when deterministic resolution, exact cache, or approved reuse prevented a planned invocation. It records task class, policy, estimate baseline, reason, and estimated savings without fabricating token usage or an `AIInvocationId`.

Compression savings compare approved before/after estimates and include compression cost. Cache savings use the pricing/catalog snapshot that would have routed the request, marked as estimate rather than realized provider billing.

## Unknown usage and pricing changes

If the provider omits usage, the adapter supplies availability status and the approved estimator calculates a conservative amount. Reconciliation remains flagged until authoritative evidence is available or policy closes it as estimated. Historical records retain their pricing version; new pricing creates a new immutable catalog version and never rewrites history.

## Budget failure

Reservation rejection yields a normalized budget Error/Result before provider invocation. Reconciliation overage is recorded and escalated operationally; it does not change an authoritative successful model Result into failure after the fact. Suspected provider/reporting anomalies are quarantined for review and cannot silently increase future budgets.

## Tenant isolation and privacy

All ledgers, reservations, records, queries, caches, and reports require exact Tenant/Workspace scope. Cross-tenant aggregation uses de-identified approved analytics paths. Raw prompts, responses, secrets, PII, and provider credentials are excluded. Cost metadata follows ES-008 classification/redaction rules.

## Metrics and audit

Bounded metrics include normalized cost, tokens, latency, cache rate, avoided invocation rate, context compression, estimate error, budget rejection, fallback, and quality-tier outcome. High-cardinality identities stay in traces/audit records. Mandatory audit acceptance precedes governed budget-policy changes, but accounting observations do not own workflow state or retry decisions.

## Quality-cost review loop

```mermaid
flowchart TD
    DATA["Usage, cost, latency, quality evidence"] --> EVAL["Versioned task-class evaluation"]
    EVAL --> FRONTIER["Quality-cost frontier"]
    FRONTIER --> DEC{"Thresholds met?"}
    DEC -->|"Yes"| POLICY["Reviewed routing/budget update"]
    DEC -->|"No"| PROTECT["Retain/escalate quality route"]
    POLICY --> DATA
    PROTECT --> DATA
```

Policy updates require evaluation evidence, accountable approval, versioning, rollback, and staged exposure. Cost savings are invalid if protected accuracy, factuality, safety, compliance, or completeness regresses.

## Retention and compatibility

Retention follows legal, finance, privacy, and tenant policy. Schema versions are explicit; readers tolerate additive fields and reject unknown incompatible major versions. Deletion/anonymization never destroys required financial/audit evidence without approved policy.

