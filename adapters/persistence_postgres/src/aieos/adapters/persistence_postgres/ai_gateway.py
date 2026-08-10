"""PostgreSQL persistence and recovery for the provider-neutral AI Gateway."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import TypeAdapter
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from aieos.ai_gateway.gateway import (
    Acceptance,
    AIInvocationRequest,
    AIUsage,
    CachedContent,
    GatewayInvocation,
    GatewayReservation,
    InvocationState,
    ReferenceGatewayStore,
)
from aieos.contracts import ResultEnvelope

from .database import PostgresDatabase
from .models import (
    AIGatewayAttemptRow,
    AIGatewayBudgetRow,
    AIGatewayCacheRow,
    AIGatewayInvocationRow,
    AIGatewayUsageLedgerRow,
)

_INVOCATION = TypeAdapter(GatewayInvocation)
_CACHE = TypeAdapter(CachedContent)
_USAGE = TypeAdapter(AIUsage)


class PostgresAIGatewayStore(ReferenceGatewayStore):
    """Durable Gateway store with scoped, atomic admission and restart recovery."""

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        tenant_limit: Decimal = Decimal("100"),
        workspace_limit: Decimal | None = None,
    ) -> None:
        super().__init__(tenant_limit=tenant_limit, workspace_limit=workspace_limit)
        self._database = database

    @staticmethod
    def _scope_key(request: AIInvocationRequest) -> str:
        return "\x1f".join((request.tenant_id, request.workspace_id, request.idempotency_key))

    async def accept(
        self,
        request: AIInvocationRequest,
        *,
        invocation_id: str,
        acknowledgement: ResultEnvelope,
        now: datetime,
    ) -> Acceptance:
        # The annotation is kept storage-neutral in the base port; validate through the
        # snapshot below so malformed acknowledgements cannot be persisted.
        candidate = GatewayInvocation(
            request, invocation_id, acknowledgement, InvocationState.REQUESTED, now
        )
        payload = _INVOCATION.dump_json(candidate).decode()
        digest = self.request_digest(request)
        async with self._database.transaction() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope_key, 0))"),
                {"scope_key": self._scope_key(request)},
            )
            existing = await session.scalar(
                select(AIGatewayInvocationRow).where(
                    AIGatewayInvocationRow.tenant_id == request.tenant_id,
                    AIGatewayInvocationRow.workspace_id == request.workspace_id,
                    AIGatewayInvocationRow.idempotency_key == request.idempotency_key,
                )
            )
            if existing is not None:
                if existing.intent_fingerprint != digest:
                    raise ValueError("IdempotencyKey payload conflict")
                recovered = _INVOCATION.validate_json(existing.request_payload)
                self.invocations[recovered.invocation_id] = recovered
                return Acceptance(recovered.invocation_id, recovered.acknowledgement, replay=True)
            await session.execute(
                insert(AIGatewayInvocationRow).values(
                    tenant_id=request.tenant_id,
                    workspace_id=request.workspace_id,
                    ai_invocation_id=invocation_id,
                    idempotency_key=request.idempotency_key,
                    intent_fingerprint=digest,
                    state="Requested",
                    request_payload=payload,
                    acknowledgement_payload=payload,
                    accepted_at=now,
                    updated_at=now,
                )
            )
        self.invocations[invocation_id] = candidate
        return Acceptance(invocation_id, candidate.acknowledgement)

    async def load(self, invocation_id: str) -> GatewayInvocation:
        cached = self.invocations.get(invocation_id)
        if cached is not None:
            return cached
        async with self._database.transaction() as session:
            row = await session.scalar(
                select(AIGatewayInvocationRow).where(
                    AIGatewayInvocationRow.ai_invocation_id == invocation_id
                )
            )
        if row is None:
            raise KeyError(invocation_id)
        invocation = _INVOCATION.validate_json(row.request_payload)
        self.invocations[invocation_id] = invocation
        return invocation

    async def checkpoint(self, invocation: GatewayInvocation) -> None:
        encoded = _INVOCATION.dump_json(invocation).decode()
        async with self._database.transaction() as session:
            result = await session.execute(
                update(AIGatewayInvocationRow)
                .where(
                    AIGatewayInvocationRow.tenant_id == invocation.request.tenant_id,
                    AIGatewayInvocationRow.workspace_id == invocation.request.workspace_id,
                    AIGatewayInvocationRow.ai_invocation_id == invocation.invocation_id,
                )
                .values(
                    state=invocation.state.value,
                    request_payload=encoded,
                    route_payload=encoded if invocation.route is not None else None,
                    terminal_payload=encoded if invocation.terminal is not None else None,
                    terminal_result_id=(
                        invocation.terminal.result.result_id
                        if invocation.terminal is not None
                        else None
                    ),
                    terminal_error_id=(
                        invocation.terminal.error.error_id
                        if invocation.terminal is not None and invocation.terminal.error is not None
                        else None
                    ),
                    updated_at=datetime.now(invocation.accepted_at.tzinfo),
                )
            )
            if result.rowcount != 1:
                raise RuntimeError("AI Gateway persistence checkpoint failed")
        self.invocations[invocation.invocation_id] = invocation

    async def reserve(
        self,
        invocation_id: str,
        tenant_id: str,
        workspace_id: str,
        amount: Decimal,
        *,
        now: datetime,
        pricing_version: str | None = None,
    ) -> GatewayReservation:
        expires_at = now.replace(microsecond=0) + timedelta(minutes=5)
        async with self._database.transaction() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope_key, 0))"),
                {"scope_key": "\x1f".join((tenant_id, workspace_id, "budget"))},
            )
            existing = await session.get(
                AIGatewayBudgetRow, (tenant_id, workspace_id, invocation_id)
            )
            if existing is None:
                # Sum in SQL while retaining Decimal semantics across drivers.
                tenant_rows = await session.scalars(
                    select(AIGatewayBudgetRow.reserved_amount).where(
                        AIGatewayBudgetRow.tenant_id == tenant_id,
                        AIGatewayBudgetRow.state.in_(("pending", "committed", "usage_pending")),
                    )
                )
                tenant_used = sum(tenant_rows, Decimal("0"))
                workspace_rows = await session.scalars(
                    select(AIGatewayBudgetRow.reserved_amount).where(
                        AIGatewayBudgetRow.tenant_id == tenant_id,
                        AIGatewayBudgetRow.workspace_id == workspace_id,
                        AIGatewayBudgetRow.state.in_(("pending", "committed", "usage_pending")),
                    )
                )
                workspace_used = sum(workspace_rows, Decimal("0"))
                if tenant_used + amount > self.tenant_limit:
                    raise ValueError("hard budget exceeded")
                if workspace_used + amount > self.workspace_limit:
                    raise ValueError("workspace hard budget exceeded")
                await session.execute(
                    insert(AIGatewayBudgetRow).values(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        ai_invocation_id=invocation_id,
                        reserved_amount=amount,
                        state="pending",
                        pricing_version=pricing_version,
                        allocation_payload=(
                            '{"levels":["tenant","workspace"],'
                            f'"tenant_limit":"{self.tenant_limit}",'
                            f'"workspace_limit":"{self.workspace_limit}"}}'
                        ),
                        expires_at=expires_at,
                    )
                )
                actual = None
                state = "pending"
            else:
                amount = existing.reserved_amount
                expires_at = existing.expires_at
                actual = existing.actual_amount
                state = existing.state
        reservation = GatewayReservation(
            invocation_id, tenant_id, workspace_id, amount, state, expires_at, actual
        )
        self.reservations[invocation_id] = reservation
        return reservation

    async def reconcile(self, invocation_id: str, actual: Decimal, usage: AIUsage) -> None:
        invocation = await self.load(invocation_id)
        async with self._database.transaction() as session:
            budget = await session.get(
                AIGatewayBudgetRow,
                (
                    invocation.request.tenant_id,
                    invocation.request.workspace_id,
                    invocation_id,
                ),
            )
            already_recorded = budget.actual_amount if budget is not None else Decimal("0")
        await self.record_usage(
            invocation_id,
            usage=usage,
            cost=max(Decimal("0"), actual - (already_recorded or Decimal("0"))),
            event_key="terminal-reconciliation",
            kind="actual" if not usage.estimated else "estimated",
            final=True,
        )

    async def record_attempt(
        self,
        invocation_id: str,
        *,
        attempt_number: int,
        model_key: str,
        state: str,
        usage: AIUsage | None,
        cost: Decimal,
    ) -> None:
        invocation = await self.load(invocation_id)
        route = invocation.route
        async with self._database.transaction() as session:
            await session.execute(
                insert(AIGatewayAttemptRow)
                .values(
                    tenant_id=invocation.request.tenant_id,
                    workspace_id=invocation.request.workspace_id,
                    ai_invocation_id=invocation_id,
                    attempt_number=attempt_number,
                    model_key=model_key,
                    adapter_key=route.adapter_key if route is not None else "unresolved",
                    state=state,
                    usage_payload=(_USAGE.dump_json(usage).decode() if usage is not None else None),
                    cost_amount=cost,
                    recorded_at=datetime.now(invocation.accepted_at.tzinfo),
                )
                .on_conflict_do_update(
                    index_elements=[
                        "tenant_id",
                        "workspace_id",
                        "ai_invocation_id",
                        "attempt_number",
                    ],
                    set_={
                        "state": state,
                        "usage_payload": (
                            _USAGE.dump_json(usage).decode() if usage is not None else None
                        ),
                        "cost_amount": cost,
                    },
                )
            )

    async def record_usage(
        self,
        invocation_id: str,
        *,
        usage: AIUsage,
        cost: Decimal,
        event_key: str,
        kind: str,
        final: bool,
        attempt_number: int | None = None,
    ) -> None:
        """Idempotently append usage and reconcile the cumulative invocation cost."""
        invocation = await self.load(invocation_id)
        now = datetime.now(invocation.accepted_at.tzinfo)
        async with self._database.transaction() as session:
            await session.execute(
                insert(AIGatewayUsageLedgerRow)
                .values(
                    tenant_id=invocation.request.tenant_id,
                    workspace_id=invocation.request.workspace_id,
                    ai_invocation_id=invocation_id,
                    usage_event_key=event_key,
                    attempt_number=attempt_number,
                    kind=kind,
                    usage_payload=_USAGE.dump_json(usage).decode(),
                    cost_amount=cost,
                    final=final,
                    observed_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        "tenant_id",
                        "workspace_id",
                        "ai_invocation_id",
                        "usage_event_key",
                    ]
                )
            )
            cumulative = await session.scalar(
                select(func.coalesce(func.sum(AIGatewayUsageLedgerRow.cost_amount), 0)).where(
                    AIGatewayUsageLedgerRow.tenant_id == invocation.request.tenant_id,
                    AIGatewayUsageLedgerRow.workspace_id == invocation.request.workspace_id,
                    AIGatewayUsageLedgerRow.ai_invocation_id == invocation_id,
                )
            )
            if cumulative is None:
                raise RuntimeError("AI Gateway usage reconciliation failed")
            await session.execute(
                update(AIGatewayBudgetRow)
                .where(
                    AIGatewayBudgetRow.tenant_id == invocation.request.tenant_id,
                    AIGatewayBudgetRow.workspace_id == invocation.request.workspace_id,
                    AIGatewayBudgetRow.ai_invocation_id == invocation_id,
                )
                .values(
                    actual_amount=cumulative,
                    state="committed" if final else "usage_pending",
                    usage_payload=_USAGE.dump_json(usage).decode(),
                    reconciled_at=now if final else None,
                )
            )
        reservation = self.reservations.get(invocation_id)
        if reservation is not None:
            reservation.state = "committed" if final else "usage_pending"
            reservation.actual = Decimal(cumulative)
        self.usage[invocation_id] = usage

    async def pending_reconciliation(self) -> tuple[str, ...]:
        """List accepted invocations whose usage is delayed or still partial."""
        async with self._database.transaction() as session:
            values = await session.scalars(
                select(AIGatewayBudgetRow.ai_invocation_id)
                .where(AIGatewayBudgetRow.state.in_(("pending", "usage_pending")))
                .order_by(AIGatewayBudgetRow.ai_invocation_id)
            )
            return tuple(values)

    async def release(self, invocation_id: str, *, state: str = "released") -> None:
        invocation = await self.load(invocation_id)
        async with self._database.transaction() as session:
            await session.execute(
                update(AIGatewayBudgetRow)
                .where(
                    AIGatewayBudgetRow.tenant_id == invocation.request.tenant_id,
                    AIGatewayBudgetRow.workspace_id == invocation.request.workspace_id,
                    AIGatewayBudgetRow.ai_invocation_id == invocation_id,
                    AIGatewayBudgetRow.state == "pending",
                )
                .values(state=state)
                .values(
                    released_at=datetime.now(invocation.accepted_at.tzinfo),
                    release_reason=state,
                )
            )
        await super().release(invocation_id, state=state)

    async def release_expired(self, *, now: datetime) -> tuple[str, ...]:
        async with self._database.transaction() as session:
            rows = tuple(
                await session.scalars(
                    select(AIGatewayBudgetRow).where(
                        AIGatewayBudgetRow.state == "pending",
                        AIGatewayBudgetRow.expires_at <= now,
                    )
                )
            )
            for row in rows:
                row.state = "expired"
                row.released_at = now
                row.release_reason = "expired"
        for row in rows:
            local = self.reservations.get(row.ai_invocation_id)
            if local is not None:
                local.state = "expired"
        return tuple(row.ai_invocation_id for row in rows)

    async def cached(
        self, tenant_id: str, workspace_id: str, cache_key: str, *, now: datetime
    ) -> CachedContent | None:
        async with self._database.transaction() as session:
            row = await session.get(AIGatewayCacheRow, (tenant_id, workspace_id, cache_key))
        if row is None or row.invalidated_at is not None or row.expires_at <= now:
            return None
        return _CACHE.validate_json(row.metadata_payload)

    async def cache_content(
        self, tenant_id: str, workspace_id: str, cache_key: str, value: CachedContent
    ) -> None:
        async with self._database.transaction() as session:
            await session.execute(
                insert(AIGatewayCacheRow)
                .values(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    cache_key=cache_key,
                    content=value.content,
                    metadata_payload=_CACHE.dump_json(value).decode(),
                    provenance_ai_invocation_id=value.provenance,
                    expires_at=value.expires_at,
                )
                .on_conflict_do_update(
                    index_elements=["tenant_id", "workspace_id", "cache_key"],
                    set_={
                        "content": value.content,
                        "metadata_payload": _CACHE.dump_json(value).decode(),
                        "provenance_ai_invocation_id": value.provenance,
                        "expires_at": value.expires_at,
                        "invalidated_at": None,
                    },
                )
            )

    async def invalidate_cache(
        self, tenant_id: str, workspace_id: str, *, cache_key: str | None = None
    ) -> int:
        statement = (
            update(AIGatewayCacheRow)
            .where(
                AIGatewayCacheRow.tenant_id == tenant_id,
                AIGatewayCacheRow.workspace_id == workspace_id,
                AIGatewayCacheRow.invalidated_at.is_(None),
            )
            .values(invalidated_at=datetime.now().astimezone())
        )
        if cache_key is not None:
            statement = statement.where(AIGatewayCacheRow.cache_key == cache_key)
        async with self._database.transaction() as session:
            result = await session.execute(statement)
            return result.rowcount


__all__ = ("PostgresAIGatewayStore",)
