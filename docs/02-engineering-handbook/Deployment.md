---
title: Deployment
version: 1.0
status: Draft
owner: Founding Team
last_updated: 2026-07-20
---

# Deployment

Deployments are reproducible, observable, recoverable changes. Specific cloud and tooling choices belong in architecture records; these rules remain provider-independent.

## Environments

Use separate local, test, staging, and production contexts as risk requires. Production has separate credentials, data, storage, and external publishing targets. Non-production defaults to sandbox, mock, private, or dry-run integrations so a test cannot publish publicly.

Environment differences are expressed through validated configuration, not divergent source branches. Access follows least privilege and is reviewed periodically.

## Delivery pipeline

The delivery pipeline should:

1. build from a reviewed commit;
2. resolve pinned dependencies and produce a traceable artifact;
3. run required quality, test, evaluation, and security gates;
4. apply configuration and infrastructure through versioned mechanisms;
5. deploy progressively where impact justifies it;
6. run health and controlled smoke checks; and
7. record release identity, approver, outcome, and rollback target.

No model-generated action may bypass protected deployment approvals.

## Configuration and secrets

Validate required configuration before accepting traffic or work. Store secrets outside artifacts and inject them through approved mechanisms. Rotation must not require a source-code change. Never copy production secrets into preview environments.

## Database and event changes

Use versioned, reviewed migrations. Prefer backward-compatible expand-and-contract changes: add compatible structures, deploy readers and writers, migrate data, then remove old structures after verification. Long migrations include batching, monitoring, pause, and recovery plans.

Event and API consumers must tolerate the documented compatibility window. A rollback must account for data written by the new version.

## Rollout and rollback

Choose rolling, canary, blue-green, or immediate deployment according to risk and system maturity. Feature flags can separate code deployment from behavior activation, but require an owner and removal condition.

Every production change identifies recovery: automated rollback, forward fix, feature disablement, provider failover, or workflow pause. Practice recovery for critical paths. If data or external effects are not reversible, define compensation and stop conditions before release.

## AI-specific release controls

Model, prompt, tool, and policy configuration changes are releases even without code changes. Version them, evaluate them, attribute executions to the active version, stage consequential changes, enforce spend limits, and preserve a known-good rollback.

Provider fallback must not silently weaken safety, output contracts, data-handling commitments, or quality thresholds.

## Production readiness

Before first production use, confirm:

- ownership, access, secrets, and data classification;
- health checks, metrics, logs, traces, alerts, and runbooks;
- capacity, timeouts, bounded retries, quotas, and cost controls;
- backup and recovery needs;
- migration and rollback behavior;
- safe handling of partial workflows and duplicate delivery; and
- controlled external-effect testing, including publishing permissions.

## Emergency change

An emergency change minimizes immediate harm, uses the narrowest available scope, and retains an accountable approver. Record it in Git as soon as practical, validate the restored state, and complete a follow-up review with regression protection.

See [Git Workflow](GitWorkflow.md), [Security](Security.md), [Testing](Testing.md), and [Observability](Observability.md).
