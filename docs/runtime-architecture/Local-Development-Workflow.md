---
title: Local Development Workflow
version: 1.0
status: Draft
owner: CTO / Architect
last_updated: 2026-07-21
---

# Local Development Workflow

## 1. Supported toolchain

- Git;
- CPython 3.13 (exact patch recorded in `.python-version`);
- pinned `uv` release;
- container runtime only when running local PostgreSQL/integration dependencies;
- optional editor with Python language-server support.

The repository bootstrap checks versions and fails with a remediation message. No globally installed Python packages are required.

## 2. Bootstrap

```text
clone repository
verify Python and uv
uv sync --all-packages --all-groups
copy the documented local configuration template to an ignored file
start optional local dependencies
run the repository doctor
run fast validation
```

Exact commands will be introduced in Phase 2 scripts. The doctor validates tool versions, lockfile state, configuration schema, reserved ports, dependency availability, and accidental production credentials.

## 3. Configuration

Local defaults select in-memory Command Dispatcher/Event Bus, deterministic mock AI/provider/tool adapters, local persistence, no external publication, and safe observability sinks. `.env`-style files are ignored and may hold only local values or secret references. Production credentials are prohibited.

Configuration precedence and source are printed without values at startup. Tests construct typed settings explicitly and do not inherit developer environment values unless a marked integration fixture requires them.

## 4. Run and debug

The host runs through one repository script that invokes the workspace environment. Debug profiles cover:

- HTTP ingress to Manager;
- Workflow state transition and Command dispatch;
- one Skill Runtime attempt;
- mock AI invocation;
- Memory operation;
- Event publication/consumption;
- cancellation, timeout, and retry/new `ExecutionId`;
- correlated Result/Error and observability records.

Structured local logs are safe by default. A developer can select a `CorrelationId`, `WorkflowId`, or `ExecutionId` to render a diagnostic timeline without exposing secret or raw prompt data.

## 5. Test commands

The developer workflow exposes stable commands for:

- fast format/lint/type/unit checks;
- package-specific tests;
- contract/schema compatibility;
- dependency boundary validation;
- adapter contract tests;
- integration tests with local persistence;
- concurrency/idempotency/security tests;
- observability conformance;
- E2E reference workflow;
- documentation/link/Mermaid validation;
- the same aggregate gate used by CI.

Tests print deterministic seeds and artifact locations on failure. External provider tests are opt-in, sandboxed, cost-bounded, and never part of the default workflow.

## 6. Reset local state

The reset script requires explicit confirmation and acts only on repository-owned local containers, volumes, caches, generated schemas, and test databases. It never targets a broad directory or unknown database. Generated contracts are regenerated after reset; dependency caches may be retained unless a clean-room check is requested.

## 7. Common troubleshooting

| Symptom | Check | Recovery |
| --- | --- | --- |
| Lock mismatch | Python/uv version and lockfile diff | use pinned tools; resync without editing dependencies |
| Startup config failure | missing/invalid typed setting | inspect safe validation output; set local reference |
| Duplicate handler | command target/type registration | remove duplicate composition registration |
| Event not consumed | consumer registration and recorded event | inspect local event timeline; do not convert to Command |
| Retry not occurring | Workflow retry decision and policy | inspect Workflow state; never retry from Skill Runtime |
| Cross-scope denial | verified Tenant/Workspace context | correct test fixture or authorization; never bypass filter |
| Mermaid/link failure | changed heading/path/block | update relative link or diagram syntax |
| Flaky timing test | wall-clock/sleep usage | inject deterministic clock and scheduler |

## 8. Contribution loop

Create a short-lived branch, implement the smallest approved slice, run focused checks, run the aggregate pre-push gate, self-review dependency and frozen-contract effects, then open a draft PR with scope, validation, risks, and governance links. No generated or AI-authored change receives reduced review.
