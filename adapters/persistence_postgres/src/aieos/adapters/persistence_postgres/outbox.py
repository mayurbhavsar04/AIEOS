"""Durable at-least-once PostgreSQL outbox and polling relay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import TypeAdapter
from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from aieos.contracts.events import EventEnvelope
from aieos.event_bus import EventBus

from .database import PostgresDatabase
from .models import OutboxEventRow


def utc_now() -> datetime:
    return datetime.now(UTC)


_EVENT_ADAPTER = TypeAdapter(EventEnvelope)


def encode_event(event: EventEnvelope) -> bytes:
    return _EVENT_ADAPTER.dump_json(event)


def decode_event(payload: bytes) -> EventEnvelope:
    return _EVENT_ADAPTER.validate_json(payload)


class PostgresOutboxStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def record(self, event: EventEnvelope) -> None:
        """Idempotently record immutable event content in its own transaction."""
        async with self._database.transaction() as session:
            await self.record_in_transaction(session, event)

    async def record_in_transaction(self, session: AsyncSession, event: EventEnvelope) -> None:
        """Record beside producer-owned state in the caller's transaction."""
        encoded = encode_event(event)
        statement = insert(OutboxEventRow).values(
            event_id=event.event_id,
            producer=event.producer,
            event_type=event.event_type,
            tenant_id=event.tenant_id,
            workspace_id=event.workspace_id,
            payload=encoded,
            recorded_at=event.recorded_at,
            available_at=event.recorded_at,
        )
        statement = statement.on_conflict_do_nothing(
            index_elements=["tenant_id", "workspace_id", "event_id"]
        )
        await session.execute(statement)
        stored = await session.get(
            OutboxEventRow, (event.tenant_id, event.workspace_id, event.event_id)
        )
        if stored is None or stored.payload != encoded:
            raise ValueError("EventId cannot be reused with changed content")

    async def claim(
        self,
        *,
        owner: str,
        batch_size: int,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> tuple[EventEnvelope, ...]:
        instant = now or utc_now()
        async with self._database.transaction() as session:
            statement = (
                select(OutboxEventRow)
                .where(
                    OutboxEventRow.delivered_at.is_(None),
                    OutboxEventRow.available_at <= instant,
                    or_(
                        OutboxEventRow.lease_expires_at.is_(None),
                        OutboxEventRow.lease_expires_at < instant,
                    ),
                )
                .order_by(OutboxEventRow.recorded_at, OutboxEventRow.event_id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            rows = tuple((await session.scalars(statement)).all())
            expiry = instant + timedelta(seconds=lease_seconds)
            for row in rows:
                row.lease_owner = owner
                row.lease_expires_at = expiry
                row.delivery_attempts += 1
            return tuple(decode_event(row.payload) for row in rows)

    async def mark_delivered(
        self,
        event_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
        owner: str,
        now: datetime | None = None,
    ) -> bool:
        async with self._database.transaction() as session:
            row = await session.get(
                OutboxEventRow,
                (tenant_id, workspace_id, event_id),
                with_for_update=True,
            )
            if row is None or row.lease_owner != owner or row.delivered_at is not None:
                return False
            row.delivered_at = now or utc_now()
            row.lease_owner = None
            row.lease_expires_at = None
            return True

    async def release_failed(
        self,
        event_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
        owner: str,
        error: str,
        backoff_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        instant = now or utc_now()
        async with self._database.transaction() as session:
            row = await session.get(
                OutboxEventRow,
                (tenant_id, workspace_id, event_id),
                with_for_update=True,
            )
            if row is None or row.lease_owner != owner or row.delivered_at is not None:
                return False
            row.last_error = error[:2000]
            row.available_at = instant + timedelta(seconds=backoff_seconds)
            row.lease_owner = None
            row.lease_expires_at = None
            return True

    async def health(self, *, now: datetime | None = None) -> dict[str, int | bool]:
        instant = now or utc_now()
        async with self._database.transaction() as session:
            pending = await session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(OutboxEventRow.delivered_at.is_(None))
            )
            stale = await session.scalar(
                select(func.count())
                .select_from(OutboxEventRow)
                .where(
                    and_(
                        OutboxEventRow.delivered_at.is_(None),
                        OutboxEventRow.lease_expires_at < instant,
                    )
                )
            )
        return {"healthy": True, "pending": int(pending or 0), "stale_leases": int(stale or 0)}


class PostgresOutboxRelay:
    def __init__(
        self,
        store: PostgresOutboxStore,
        event_bus: EventBus,
        *,
        owner: str,
        batch_size: int,
        lease_seconds: float,
        backoff_seconds: float,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._owner = owner
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._backoff_seconds = backoff_seconds

    async def drain(self) -> int:
        events = await self._store.claim(
            owner=self._owner,
            batch_size=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        delivered = 0
        for event in events:
            if event.tenant_id is None or event.workspace_id is None:
                raise ValueError("durable Event delivery requires tenant and workspace scope")
            try:
                await self._event_bus.publish(event)
            except Exception as failure:
                await self._store.release_failed(
                    event.event_id,
                    tenant_id=event.tenant_id,
                    workspace_id=event.workspace_id,
                    owner=self._owner,
                    error=type(failure).__name__,
                    backoff_seconds=self._backoff_seconds,
                )
            else:
                if await self._store.mark_delivered(
                    event.event_id,
                    tenant_id=event.tenant_id,
                    workspace_id=event.workspace_id,
                    owner=self._owner,
                ):
                    delivered += 1
        return delivered


class BufferedPostgresOutbox:
    """Synchronous producer port backed by a durable store at drain time.

    Runtime producers remain transport-agnostic. Each drain first records every
    pending immutable Event in PostgreSQL and only then attempts delivery.
    """

    def __init__(self, store: PostgresOutboxStore, relay: PostgresOutboxRelay) -> None:
        self._store = store
        self._relay = relay
        self._pending: dict[str, EventEnvelope] = {}

    def record(self, event: EventEnvelope) -> None:
        existing = self._pending.get(event.event_id)
        if existing is not None and existing != event:
            raise ValueError("EventId cannot be reused with changed content")
        self._pending[event.event_id] = event

    async def drain(self) -> int:
        for event in tuple(self._pending.values()):
            await self._store.record(event)
            self._pending.pop(event.event_id, None)
        return await self._relay.drain()
