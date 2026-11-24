"""Shared fixtures.

Storage tests run on in-memory SQLite and fakeredis by default. CI also runs them against
real services by setting TEST_DATABASE_URL (Postgres, already migrated) and TEST_REDIS_URL.
"""

import os

import fakeredis
import pytest
from pydantic import SecretStr

from voice_agent.agent_version import agent_version_spec
from voice_agent.config import Settings, get_settings
from voice_agent.gateway.app import app as gateway_app
from voice_agent.pipeline.server import app as agent_app
from voice_agent.services import Services
from voice_agent.storage.db import Database
from voice_agent.storage.live_state import RedisLiveState
from voice_agent.storage.models import Base
from voice_agent.storage.repository import CallRepository

PUBLIC_BASE_URL = "https://voice.example.test"
AUTH_TOKEN = "test-twilio-auth-token"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        public_base_url=PUBLIC_BASE_URL,
        media_ws_url="wss://voice.example.test/media",
        internal_api_token=SecretStr("internal-secret"),
        stream_token_signing_key=SecretStr("signing-key"),
        twilio_account_sid="AC00000000000000000000000000000000",
        twilio_auth_token=SecretStr(AUTH_TOKEN),
        twilio_phone_number="+15555550100",
        deepgram_api_key=SecretStr("dg-test"),
        anthropic_api_key=SecretStr("sk-ant-test"),
        cartesia_api_key=SecretStr("cartesia-test"),
        cartesia_voice_id_en="voice-test",
        recording_enabled=False,
    )


@pytest.fixture(autouse=True)
def _override_settings(settings: Settings):
    for app in (gateway_app, agent_app):
        app.dependency_overrides[get_settings] = lambda: settings
    yield
    for app in (gateway_app, agent_app):
        app.dependency_overrides.clear()


@pytest.fixture
async def db():
    url = os.environ.get("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    database = Database(url)
    if url.startswith("sqlite"):
        await database.create_all()
    else:  # schema comes from `alembic upgrade head`; start each test empty
        async with database.engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                await conn.execute(table.delete())
    yield database
    await database.dispose()


@pytest.fixture
async def live():
    url = os.environ.get("TEST_REDIS_URL")
    state = (
        RedisLiveState.from_url(url, ttl_s=60)
        if url
        else RedisLiveState(fakeredis.FakeAsyncRedis(decode_responses=True), ttl_s=60)
    )
    yield state
    await state.close()


@pytest.fixture
async def services(db, live, settings):
    svc = Services(db=db, repo=CallRepository(db), live=live)
    svc.agent_version_id = await svc.repo.ensure_agent_version(agent_version_spec(settings))
    gateway_app.state.services = svc
    agent_app.state.services = svc
    yield svc
    del gateway_app.state.services
    del agent_app.state.services
