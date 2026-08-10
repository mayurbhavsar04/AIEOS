"""SQLAlchemy async lifecycle isolated inside the PostgreSQL adapter."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

EXPECTED_ALEMBIC_REVISION = "20260810_0002"


class PostgresDatabase:
    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 5,
        pool_timeout_seconds: float = 10.0,
        command_timeout_seconds: float = 30.0,
    ) -> None:
        if not url.startswith("postgresql+asyncpg://"):
            raise ValueError("database URL must use postgresql+asyncpg")
        self.engine: AsyncEngine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_size=pool_size,
            pool_timeout=pool_timeout_seconds,
            connect_args={"command_timeout": command_timeout_seconds},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions.begin() as session:
            yield session

    @asynccontextmanager
    async def command_lock(self, scoped_idempotency_key: str) -> AsyncIterator[None]:
        """Serialize one target-owned logical request across workers."""
        async with self.sessions.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:scope_key, 0))"),
                {"scope_key": scoped_idempotency_key},
            )
            yield

    async def health(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def migration_readiness(self) -> dict[str, str | bool]:
        """Compare deployed schema revision with the immutable code head.

        This check is deliberately read-only. Migrations remain an operator action.
        """
        try:
            async with self.engine.connect() as connection:
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        except (ProgrammingError, DBAPIError):
            return {
                "ready": False,
                "status": "version_table_missing",
                "expected_revision": EXPECTED_ALEMBIC_REVISION,
            }
        except Exception:
            return {
                "ready": False,
                "status": "database_unreachable",
                "expected_revision": EXPECTED_ALEMBIC_REVISION,
            }
        if revision == EXPECTED_ALEMBIC_REVISION:
            return {
                "ready": True,
                "status": "compatible",
                "expected_revision": EXPECTED_ALEMBIC_REVISION,
                "deployed_revision": revision,
            }
        status = (
            "behind_expected_head"
            if isinstance(revision, str) and revision < EXPECTED_ALEMBIC_REVISION
            else "ahead_or_diverged"
        )
        return {
            "ready": False,
            "status": status,
            "expected_revision": EXPECTED_ALEMBIC_REVISION,
            "deployed_revision": str(revision),
        }

    async def close(self) -> None:
        await self.engine.dispose()
