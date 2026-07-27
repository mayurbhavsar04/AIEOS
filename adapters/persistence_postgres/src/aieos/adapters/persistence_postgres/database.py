"""SQLAlchemy async lifecycle isolated inside the PostgreSQL adapter."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


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

    async def health(self) -> bool:
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def close(self) -> None:
        await self.engine.dispose()
