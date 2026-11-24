"""Typed settings loaded from environment variables (and `.env` in local dev).

Names match `.env.example`. In AWS, secrets are injected by ECS from Secrets Manager.
"""

from functools import lru_cache

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
    anthropic_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "low"
    llm_max_tokens: int = 1024

    # --- TTS ---
    cartesia_api_key: SecretStr = SecretStr("")
    cartesia_voice_id_en: str = ""

    # --- Agent behaviour ---
    playbook: str = "booking"
    vad_stop_secs: float = 0.2

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
