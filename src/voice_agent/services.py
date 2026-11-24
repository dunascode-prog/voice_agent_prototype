"""Long-lived resources shared by the request handlers of one process.

Created in each app's lifespan and stored on `app.state.services`. Persistence is
best-effort on the call path: if the database or Redis fails, the call still goes ahead
and the failure is logged (docs/ARCHITECTURE.md section 6.6).
"""

import uuid
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from loguru import logger
from starlette.requests import HTTPConnection

from voice_agent.agent_version import agent_version_spec
from voice_agent.config import Settings
from voice_agent.storage.db import Database
from voice_agent.storage.live_state import LiveState, MemoryLiveState, RedisLiveState
from voice_agent.storage.repository import CallRepository


@dataclass
class Services:
    db: Database
    repo: CallRepository
    live: LiveState
    agent_version_id: uuid.UUID | None = None

    @classmethod
    async def start(cls, settings: Settings) -> "Services":
        db = Database(settings.async_database_url)
        if db.url.startswith("sqlite"):
            # Local dev convenience; Postgres schemas are managed by Alembic migrations.
            await db.create_all()
        live: LiveState = (
            RedisLiveState.from_url(settings.redis_url, settings.live_state_ttl_s)
            if settings.redis_url
            else MemoryLiveState(settings.live_state_ttl_s)
        )
        services = cls(db=db, repo=CallRepository(db), live=live)
        services.agent_version_id = await best_effort(
            services.repo.ensure_agent_version(agent_version_spec(settings)),
            "register agent version",
        )
        return services

    async def close(self) -> None:
        await self.live.close()
        await self.db.dispose()


def services_from(conn: HTTPConnection) -> Services | None:
    """FastAPI dependency (works for HTTP and WebSocket routes)."""
    return getattr(conn.app.state, "services", None)


async def best_effort[T](awaitable: Awaitable[T], what: str) -> T | None:
    try:
        return await awaitable
    except Exception as e:  # noqa: BLE001 - persistence must never break a live call
        logger.error(f"persistence failed ({what}): {type(e).__name__}: {e}")
        return None


def lifespan_for(get_settings: Any):
    """Build a FastAPI lifespan that starts and stops `Services`."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        services = await Services.start(get_settings())
        app.state.services = services
        try:
            yield
        finally:
            await services.close()

    return lifespan
