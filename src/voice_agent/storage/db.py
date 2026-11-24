"""Async SQLAlchemy engine and session factory."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from voice_agent.storage.models import Base


class Database:
    def __init__(self, url: str, echo: bool = False):
        kwargs: dict = {"echo": echo}
        if url.startswith("sqlite") and ":memory:" in url:
            # One shared connection so every session sees the same in-memory database.
            kwargs.update(poolclass=StaticPool, connect_args={"check_same_thread": False})
        elif url.startswith("postgresql"):
            kwargs.update(pool_size=10, max_overflow=10, pool_pre_ping=True)
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def create_all(self) -> None:
        """Create tables directly. Only for tests and SQLite local dev; use Alembic elsewhere."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
