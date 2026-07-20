---
title: Engineering Handbook
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
---

# Engineering Handbook

This handbook defines how AIEOS engineering work is designed, implemented, reviewed, released, and operated. It converts the [company values](../01-company/Values.md) into working rules for human and AI contributors.

The handbook applies to documentation, prompts, application code, infrastructure, data changes, and operational tooling. Product-specific specifications may add stricter requirements, but may not silently weaken these standards.

## Navigation

| Document | Governs |
| --- | --- |
| [Principles](Principles.md) | Decision rules and architectural posture |
| [Coding standards](CodingStandards.md) | Code structure, contracts, errors, dependencies, and review |
| [Prompt standards](PromptStandards.md) | Versioned prompts, structured output, evaluation, and safety |
| [Git workflow](GitWorkflow.md) | Branches, commits, pull requests, reviews, and releases |
| [Security](Security.md) | Trust boundaries, secrets, access, input handling, and incidents |
| [Testing](Testing.md) | Test layers, AI evaluations, workflow checks, and release gates |
| [Observability](Observability.md) | Logs, metrics, traces, costs, alerts, and audit records |
| [Deployment](Deployment.md) | Environments, delivery, configuration, rollback, and migrations |

Return to the [repository overview](../../README.md) or [company foundation](../01-company/README.md).

## Precedence

When guidance conflicts, use this order:

1. applicable law, contractual obligations, and approved security policy;
2. accepted Architecture Decision Records (ADRs);
3. approved product and API contracts;
4. this handbook;
5. team conventions and local preferences.

Escalate unresolved conflicts in the pull request. Do not resolve them by hiding business logic in prompts or bypassing controls.

## Definition of done

Work is done when its agreed outcome is delivered and, where applicable:

- acceptance criteria and contracts are satisfied;
- appropriate automated tests and AI evaluations pass;
- security and privacy implications are addressed;
- logs, metrics, traces, and cost attribution support operation;
- expected failures stop safely or recover predictably;
- documentation and examples reflect the change; and
- the change has been reviewed and can be rolled back or otherwise recovered.

The requirements are proportional to risk. A documentation typo does not require production load testing; publishing authority or credential handling requires stronger evidence.

## Exceptions and maintenance

An exception must name its owner, reason, affected scope, risk, compensating control, and expiry or review date. “Temporary” exceptions without an expiry are permanent debt.

The Founding Team owns this handbook. Changes use the normal pull-request process and explain what evidence or operating need changed. Material changes to frozen architecture require an ADR.
