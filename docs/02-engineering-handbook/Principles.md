---
title: Engineering Principles
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
---

# Engineering Principles

These principles guide decisions when a detailed specification does not. They support AIEOS's mission to build reliable AI Employees without allowing the platform vision to overtake the first validated product.

## 1. Customer outcomes define value

Measure the complete operating outcome, including human effort, failures, safety, quality, latency, and cost. Generated output is an intermediate result, not proof of value.

## 2. AI reasons; software controls

Use models for probabilistic work such as synthesis, ranking, classification, and generation. Deterministic software owns permissions, state transitions, quotas, validation gates, idempotency, and hard business constraints. Business rules must not exist only inside prompts.

## 3. Treat model and tool output as untrusted

Validate structure, provenance, policy compliance, and required facts before consequential use. An agent's confidence is evidence to examine, not authorization to act.

## 4. Reliability precedes autonomy and scale

Expand permissions, volume, and customers only after operational evidence supports the change. Design explicit timeouts, retries, checkpoints, stop conditions, and escalation paths for critical workflows.

## 5. Prefer the smallest clear design

Choose a modular monolith and direct contracts until evidence requires distribution. Create abstractions around demonstrated volatility or repeated behavior, not imagined reuse. AIEOS is extracted from working employees; products are not forced into a speculative platform.

## 6. Make boundaries and contracts explicit

Components communicate through typed, versioned inputs, outputs, and events. Ownership of state and side effects must be clear. Agents do not call one another implicitly; orchestration controls the sequence.

## 7. Design for replacement where risk justifies it

Keep provider-specific details behind adapters and preserve portable data. Do not build every adapter in advance. Prove an interface with the primary provider and at least a test double; add alternatives when cost, reliability, capability, or risk warrants them.

## 8. Build resumable, idempotent workflows

Persist progress at meaningful boundaries. Retrying a command with the same idempotency key must not duplicate a publication, charge, or other external effect. Resume from the last valid checkpoint rather than repeating completed expensive work.

## 9. Observability is part of behavior

Record enough context to answer what happened, why, at what cost, and with which inputs and versions. Protect secrets and sensitive content while preserving auditability. A workflow that cannot be diagnosed is not production-ready.

## 10. Secure capabilities by default

Grant least privilege, short-lived access where possible, and explicit approval for consequential tools. Separate model context from credentials. A generated instruction never overrides authorization policy.

## 11. Evidence changes decisions

State assumptions, define measures before experiments, and record material choices in ADRs or product decision records. Change direction when results contradict the hypothesis; do not defend complexity because it already exists.

## 12. Own operation end to end

The team that ships a workflow owns its alerts, recovery, documentation, cost, and recurring manual work. Deployment begins learning; it does not end responsibility.

## Decision checklist

Before approving a material design, ask:

- Which validated outcome improves?
- What deterministic boundary contains AI uncertainty?
- What can fail, and what is the safe resulting state?
- How will we observe and recover the operation?
- Is the abstraction required now?
- What security capability or data is exposed?
- What evidence would cause us to revisit the decision?

See [Coding Standards](CodingStandards.md), [Security](Security.md), and [Observability](Observability.md) for implementation rules.
