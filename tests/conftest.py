import pytest
from pydantic import SecretStr

from voice_agent.config import Settings, get_settings
from voice_agent.gateway.app import app as gateway_app
from voice_agent.pipeline.server import app as agent_app

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
    )


@pytest.fixture(autouse=True)
def _override_settings(settings: Settings):
    for app in (gateway_app, agent_app):
        app.dependency_overrides[get_settings] = lambda: settings
    yield
    for app in (gateway_app, agent_app):
        app.dependency_overrides.clear()
