---
title: Observability
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
---

# Observability

Observability must let an operator determine what happened, why, with which versions and evidence, at what cost, and what action is needed—without exposing secrets or collecting unnecessary data.

## Correlation model

Propagate stable identifiers across boundaries: request, user or service actor, workspace, workflow run, task attempt, asset, provider call, and external effect. Event and retry records must retain causation and correlation. Never use sensitive user data as an identifier.

## Structured logs

Logs use machine-readable fields, UTC timestamps, stable event names, severity, component, outcome, duration, and error code. Log state transitions and decisions at useful boundaries, not every internal token or raw payload.

Redact credentials, authorization headers, personal data, private prompts, model context, and signed URLs. Debug logging has an expiry and cannot weaken production protections.

## Metrics

At minimum, measure:

- request and workflow throughput, latency, success, and failure;
- queue delay, retry count, checkpoint age, and dead-letter volume;
- provider availability, errors, rate limits, and fallback use;
- model tokens, media-generation usage, storage, and cost by workflow and capability;
- validation, refusal, escalation, approval, and policy-denial rates;
- external effects such as publish attempts and duplicate prevention; and
- product outcome measures defined in product requirements.

Avoid high-cardinality dimensions such as raw user input or unique asset IDs in metric labels.

## Traces and AI execution records

Distributed traces connect service and provider latency. AI execution records additionally identify prompt version, model and configuration, tool names, schema-validation outcome, evidence references, safety decisions, and cost. Store raw inputs or outputs only when necessary, permitted, access-controlled, and retained for a defined period.

Reasoning traces are not a requirement. Record concise decision summaries and evidence suitable for operation and audit rather than hidden model reasoning.

## Audit records

Consequential actions require durable records of actor, authority, target, policy decision, approval if any, idempotency key, outcome, and timestamp. Audit data is access-controlled and protected from ordinary application mutation. It is distinct from diagnostic logs.

## Dashboards and alerts

Dashboards begin with user outcomes and critical workflows, then dependency and cost health. Every production alert has a clear condition, severity, owner, runbook link, and recovery expectation. Alert on actionable symptoms; group correlated failures and avoid paging on normal retries.

Initial critical alert candidates include blocked publication pipelines, unexpected public publication, repeated verification failure, credential or authorization anomalies, provider exhaustion, and unusual spend.

## Service objectives

Define indicators and objectives only after the user journey and risk are understood. Use error budgets to guide reliability work once sufficient traffic exists. Do not claim availability targets that the system cannot yet measure.

## Retention and access

Retention varies by diagnostic, audit, security, and product purpose. Document the duration and deletion process. Restrict access by role, record sensitive-log access, and remove data no longer required.

See [Security](Security.md), [Testing](Testing.md), and [Deployment](Deployment.md).
