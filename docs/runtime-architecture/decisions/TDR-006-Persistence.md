# TDR-006 — Port-Based Persistence with PostgreSQL Adapter

- **Status:** Proposed
- **Date:** 2026-07-21

## Context and drivers

Workflow state, immutable records, optimistic concurrency, idempotency, tenant scope, audit acceptance, and migrations need transactional persistence without leaking storage into domain/contracts.

## Options

PostgreSQL; SQLite initially; document database; event store; provider-managed proprietary database.

## Decision and rationale

Define repositories/units of work as owned ports and implement the first durable adapter with PostgreSQL via SQLAlchemy 2.x async APIs. Logical owners use separate namespaces and prohibit cross-owner writes.

## Consequences and rejected alternatives

Local integration requires a database service and disciplined migrations. SQLite differs in concurrency/types; document stores weaken relational invariants; event sourcing adds complexity; proprietary storage increases lock-in.

## Compatibility and revisit

Domain/contracts remain storage-neutral. Revisit adapters for measured workload, tenancy, availability, or regulatory needs; schema design remains a later phase.
