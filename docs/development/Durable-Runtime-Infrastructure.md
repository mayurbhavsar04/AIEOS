# Durable Runtime Infrastructure

Milestone 5 Phase 4 adds PostgreSQL adapters behind the existing runtime boundaries. It does not
change frozen Commands, Events, Results, Errors, ownership, retry authority, or the in-memory
reference mode. PostgreSQL polling is the local/default durable Event Bus delivery implementation;
no final external broker is selected.

## Local operation

```text
docker compose up -d postgres
export AIEOS_DATABASE_URL=postgresql+asyncpg://aieos:aieos_local@localhost:5432/aieos # pragma: allowlist secret
uv run alembic upgrade head
docker compose stop postgres
docker compose down
docker compose down --volumes
```

Set `AIEOS_RUNTIME_ADAPTER=postgres` to select durable infrastructure. Production mode fails closed
unless PostgreSQL is explicitly configured. The URL is a secret setting and is omitted from safe
configuration summaries. The deterministic credentials above are local only.

## Ownership and schema

| Table | Logical owner | Authority |
| --- | --- | --- |
| `workflows`, `workflow_steps` | Workflow Engine | current orchestration state |
| `executions` | Skill Runtime | one instructed attempt and retry lineage |
| `command_idempotency` | accountable Command target | in-progress/completed deduplication |
| `outcomes` | producing owner | immutable Result/Error payloads |
| `outbox_events` | authoritative Event producer | immutable Event plus publish intent |
| `delivery_receipts` | infrastructure | non-authoritative consumer delivery evidence |
| `decision_evidence` | deciding component | immutable causation evidence |
| `memory_records` | Memory Service | scoped, append-safe versions |

Every scoped table requires Tenant and Workspace. Adapters issue parameterized SQL through
SQLAlchemy and apply both predicates to scoped reads. Rows do not escape the adapter.

## State transaction and outbox

```mermaid
sequenceDiagram
    participant Owner as "Authoritative owner"
    participant DB as "PostgreSQL transaction"
    participant Relay as "Outbox relay"
    participant Bus as "Events-only Event Bus"
    Owner->>DB: "Lock/check owned state"
    Owner->>DB: "Persist transition and immutable Event"
    DB-->>Owner: "Commit both or neither"
    Relay->>DB: "Claim pending Events"
    Relay->>Bus: "Publish same Event envelope"
    Relay->>DB: "Record non-authoritative delivery marker"
```

## Relay leases and poison delivery

```mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> Leased: "FOR UPDATE SKIP LOCKED"
    Leased --> Delivered: "publish succeeds"
    Leased --> Backoff: "publish fails"
    Backoff --> Pending: "available_at reached"
    Leased --> Pending: "lease expires after crash"
```

Claims use short transactions and PostgreSQL row locks with `SKIP LOCKED`, so concurrent relays
cannot claim the same row. A lease is non-authoritative and may expire after a crash. Publication is
therefore explicitly at least once. Consumers retain idempotency responsibility. Failure type and
attempt count remain queryable; errors are bounded and never contain payloads or credentials.

## Crash recovery and retries

```mermaid
flowchart LR
    C["Crash"] --> S["Scan incomplete owned state"]
    S --> I{"Completed receipt?"}
    I -->|"Yes"| D["Return prior immutable outcome"]
    I -->|"No"| R["Resume missing effect"]
    R --> W{"Workflow retry decision?"}
    W -->|"No"| A["Same ExecutionId"]
    W -->|"Yes"| N["New CommandId and ExecutionId"]
```

Recovery resumes unfinished effects and never invents a Workflow retry. Only Workflow Engine can
record a retry decision and create the next Execution identity. Unique step/attempt constraints make
lineage queryable and prevent duplicate advancement.

## Health and limitations

Readiness is a non-mutating comparison of the deployed `alembic_version` value with the immutable
code head. It reports database-unreachable, missing-version-table, behind-head,
ahead-or-diverged, or compatible states without running migrations or exposing connection
credentials. Relay health reports only counts for pending work and stale leases. Health is
descriptive and cannot change authoritative state.

CI provisions PostgreSQL, upgrades it to Alembic head, and runs the live durability suite as a
mandatory release gate. Local runs without `AIEOS_TEST_DATABASE_URL` report an explicit skip; CI
fails when the URL or service is unavailable.

This phase does not provide an external broker, vectors, multi-region operation, production
deployment, production authentication, or arbitrary runtime SQL. Operators should use separate
least-privilege roles for migrations and runtime access; runtime roles need only owned-table DML.
