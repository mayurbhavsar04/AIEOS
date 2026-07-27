---
title: ES-011 — Durable Runtime Infrastructure
version: 1.0
status: Implemented
owner: CTO / Architect
implementer: Engineer (Codex)
milestone: 5 Phase 4
last_updated: 2026-07-27
---

# ES-011 — Durable Runtime Infrastructure

## Objective

Implement production-grade PostgreSQL persistence, transactional outbox, recovery, and scoped Memory
adapters behind frozen runtime abstractions while preserving the Phase 3 reference behavior.

## Authority and traceability

This record implements Architecture v1.0, Domain v1.0, ES-004 through ES-010, Runtime Architecture
v1.0, TDR-005, TDR-006, TDR-007, TDR-008, and TDR-009. PostgreSQL and SQLAlchemy async are already
selected. Alembic and asyncpg are reversible adapter details. No external broker is selected and no
new TDR is required.

## Acceptance criteria

- Ordered migrations create scoped workflow, step, execution, idempotency, outcome, outbox,
  delivery, decision-evidence, and memory storage.
- State and producer-owned outbox intent can share one transaction.
- Concurrent relays use safe PostgreSQL claiming, stale leases are reclaimable, poison deliveries
  are visible/retryable, and delivery is explicitly at least once.
- Duplicate Commands distinguish incomplete work from authoritative completion.
- Recovery resumes incomplete work without inventing retries; every Workflow retry retains lineage
  and receives a new `ExecutionId`.
- Memory reads require exact Tenant and Workspace scope and writes are append-safe.
- Typed configuration preserves `.env` precedence, redacts the database URL, and fails closed in
  production.
- Host composition can select local in-memory or PostgreSQL infrastructure without ORM/API leakage.
- PostgreSQL composition selects durable Memory and outbox adapters for the executable path.
- Historical Alembic revisions use explicit immutable operations rather than mutable ORM metadata.
- Readiness compares the deployed revision to code head without mutating the database.
- Mandatory CI PostgreSQL tests cover migration, claiming, stale leases, dedupe, scope, and
  resumability and cannot silently skip.
- Frozen baseline files are byte-for-byte unchanged.

## Transactions, reversibility, and non-goals

Rows are private adapter types. Explicit short transactions protect owner invariants; producer state
and its Event are committed together. Migrations are deterministic and rollback-safe for this
pre-production additive schema. A later destructive migration must use expand/migrate/contract.

No product workflow, real AI provider, vector search, external broker, production deployment,
Terraform, Kubernetes, production auth, multi-region topology, UI, or untrusted plugin is included.
