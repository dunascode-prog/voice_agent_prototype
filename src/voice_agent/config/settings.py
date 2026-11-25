"""Typed settings loaded from environment variables (and `.env` in local dev).

Names match `.env.example`. In AWS, secrets are injected by ECS from Secrets Manager.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- App ---
    app_env: str = "local"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8080"
    media_ws_url: str = "ws://localhost:8080/media"
    internal_api_token: SecretStr = SecretStr("")
    stream_token_signing_key: SecretStr = SecretStr("")
    stream_token_ttl_s: int = 120
    max_call_duration_s: int = 1800

    # --- Twilio ---
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = SecretStr("")
    twilio_phone_number: str = ""
    twilio_validate_signatures: bool = True

    # --- STT ---
    deepgram_api_key: SecretStr = SecretStr("")
    deepgram_model: str = "nova-3"
    deepgram_language: str = "multi"

    # --- LLM ---
    # "anthropic" = Claude API with ANTHROPIC_API_KEY; "bedrock" = Claude on Amazon Bedrock,
    # signed with AWS credentials (profile locally, ECS task role in AWS).
    llm_provider: Literal["anthropic", "bedrock"] = "anthropic"
    anthropic_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "low"
    llm_max_tokens: int = 1024
    aws_region: str = "us-east-1"
    # Named profile from ~/.aws for local runs. Empty = default AWS credential chain
    # (env vars, SSO cache, or the ECS task role).
    aws_profile: str = ""

    # --- TTS ---
    cartesia_api_key: SecretStr = SecretStr("")
    cartesia_voice_id_en: str = ""

    # --- Agent behaviour ---
    playbook: str = "booking"
    vad_stop_secs: float = 0.2
    recording_enabled: bool = True

    # --- Storage ---
    # Postgres in Docker/AWS; SQLite is fine for single-process local dev and tests.
    database_url: str = "sqlite+aiosqlite:///./voice_local.db"
    # Empty = in-process memory store (only valid when gateway and agent share a process).
    redis_url: str = ""
    live_state_ttl_s: int = 7200

    @property
    def async_database_url(self) -> str:
        """Add the async driver to plain URLs, e.g. postgresql:// -> postgresql+asyncpg://."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
        if url.startswith("postgres://"):
            return "postgresql+asyncpg://" + url.removeprefix("postgres://")
        if url.startswith("sqlite:///"):
            return "sqlite+aiosqlite:///" + url.removeprefix("sqlite:///")
        return url

    @property
    def llm_model_id(self) -> str:
        """Model id as the provider expects it: Bedrock ids carry an `anthropic.` prefix."""
        model = self.llm_model
        if self.llm_provider == "bedrock" and "anthropic." not in model:
            return f"anthropic.{model}"
        return model

    def required_for_calls(self) -> tuple[str, ...]:
        """Settings that must be set before the agent can take a call."""
        llm = ("anthropic_api_key",) if self.llm_provider == "anthropic" else ("aws_region",)
        return (
            "stream_token_signing_key",
            "deepgram_api_key",
            *llm,
            "cartesia_api_key",
            "cartesia_voice_id_en",
        )

    def missing(self, *names: str) -> list[str]:
        """Return the env var names (upper case) of settings that are empty."""
        out = []
        for name in names:
            value = getattr(self, name)
            if isinstance(value, SecretStr):
                value = value.get_secret_value()
            if not value:
                out.append(name.upper())
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
