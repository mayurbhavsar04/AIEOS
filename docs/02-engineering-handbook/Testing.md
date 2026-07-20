---
title: Testing
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
---

# Testing

Testing provides evidence proportional to the risk of a change. Deterministic code and probabilistic AI behavior require related but different controls.

## Test layers

### Unit tests

Cover domain rules, validation, state transitions, error mapping, and pure transformations. They should be fast, deterministic, isolated, and readable as behavior.

### Contract tests

Verify API, event, tool, storage, and provider-adapter schemas. Include compatibility and unsupported-version behavior. Use recorded or simulated provider responses without embedding secrets.

### Integration tests

Exercise real boundaries such as persistence, queues, object storage, and provider sandboxes where justified. Tests own and clean their data and must not depend on execution order.

### Workflow tests

Cover successful runs, checkpoints, retry exhaustion, timeout, cancellation, compensation, duplicate delivery, partial provider failure, and safe resume. Assert both final state and emitted evidence.

### End-to-end tests

Use a small number for critical user journeys. External effects default to sandbox or dry-run mode. A production smoke test must use controlled targets and explicit authorization.

### Non-functional tests

Use targeted security, performance, load, recovery, and cost tests when a risk or service objective requires them. Measure before optimizing.

## AI evaluations

AI behavior is evaluated with versioned datasets and scoring guidance. Cover normal, boundary, ambiguous, adversarial, stale-source, insufficient-evidence, and malformed-output cases. Track:

- schema-valid completion rate;
- task-specific quality and factuality;
- citation or provenance correctness where required;
- unsafe-action and prompt-injection resistance;
- refusal and escalation correctness;
- latency, token use, and cost; and
- variance across repeated runs when material.

Automated judges may assist but cannot be the sole authority for high-impact or subjective release decisions. Calibrate them against human-reviewed examples. Prevent evaluation leakage into prompts and report confidence or sample size with aggregate scores.

## Test data

Use synthetic or sanitized data by default. Label fixtures by source and permitted use. Keep production credentials and private customer content out of tests. Copyright-sensitive assets require documented rights or purpose.

## Regression policy

A defect fix includes the smallest test that would have caught it when practical. An AI failure that matters becomes a versioned evaluation case after sensitive content is removed. Flaky tests are defects: quarantine only with an owner, reason, and removal deadline.

## Release gates

Before merge, required formatting, linting, type checks, unit tests, contract tests, security checks, and relevant evaluations pass. Before production, verify migrations, configuration, rollback, smoke tests, monitoring, and known-risk acceptance.

Thresholds belong to product and service specifications. A failure to meet a threshold blocks release unless an accountable owner approves a time-bounded exception.

## Test review checklist

- Does evidence cover the changed behavior and likely failure modes?
- Are external effects isolated and idempotent?
- Are AI evaluations representative and versioned?
- Can failures be reproduced locally or in CI?
- Are assertions about outcomes rather than implementation trivia?
- Is cost and execution time appropriate for the test layer?

See [Prompt Standards](PromptStandards.md), [Git Workflow](GitWorkflow.md), and [Deployment](Deployment.md).
