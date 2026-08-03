"""Real PostgreSQL concurrency/recovery checks, enabled by AIEOS_TEST_DATABASE_URL."""

import os
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from sqlalchemy import text

from aieos.adapters.persistence_postgres import PostgresDatabase, PostgresOutboxStore
from aieos.contracts.events import EventEnvelope, EventMetadata

pytestmark = [pytest.mark.integration, pytest.mark.postgres_required]


def event(event_id: str) -> EventEnvelope:
    now = datetime.now(UTC)
    return EventEnvelope(
        event_id=event_id,
        event_type="ExecutionAttemptSucceeded",
        event_version="1.0",
        occurred_at=now,
        recorded_at=now,
        producer="Skill Runtime",
        correlation_id="correlation-1",
        subject="execution-1",
        tenant_id="tenant-1",
        workspace_id="workspace-1",
        payload={"result_id": "result-1"},
        metadata=EventMetadata(),
    )


@pytest.mark.anyio
async def test_concurrent_claims_do_not_overlap_and_stale_lease_is_reclaimed() -> None:
    url = os.environ.get("AIEOS_TEST_DATABASE_URL")
    if url is None:
        if os.environ.get("CI"):
            pytest.fail("mandatory AIEOS_TEST_DATABASE_URL is not configured in CI")
        pytest.skip("live PostgreSQL suite not executed: set AIEOS_TEST_DATABASE_URL")
    database = PostgresDatabase(url)
    store = PostgresOutboxStore(database)
    try:
        async with database.transaction() as session:
            await session.execute(text("TRUNCATE delivery_receipts, outbox_events CASCADE"))
        for index in range(4):
            await store.record(event(f"event-{index}"))
        claimed: list[set[str]] = []

        async def claim(owner: str) -> None:
            rows = await store.claim(owner=owner, batch_size=2, lease_seconds=30)
            claimed.append({item.event_id for item in rows})

        async with anyio.create_task_group() as group:
            group.start_soon(claim, "relay-a")
            group.start_soon(claim, "relay-b")
        assert len(claimed) == 2
        assert claimed[0].isdisjoint(claimed[1])
        assert len(claimed[0] | claimed[1]) == 4

        later = datetime.now(UTC) + timedelta(seconds=31)
        reclaimed = await store.claim(owner="relay-c", batch_size=4, lease_seconds=30, now=later)
        assert {item.event_id for item in reclaimed} == {f"event-{index}" for index in range(4)}
    finally:
        await database.close()
