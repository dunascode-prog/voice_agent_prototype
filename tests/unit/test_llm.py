"""LLM provider selection. No network: the SDK clients only connect on the first request."""

from anthropic import AsyncAnthropic, AsyncAnthropicBedrockMantle
from pipecat.services.anthropic.llm import _model_name
from pydantic import SecretStr

from voice_agent.agent_version import agent_version_spec
from voice_agent.pipeline.llm import build_llm, make_llm_client


def test_anthropic_provider_uses_claude_api(settings):
    assert make_llm_client(settings) is None
    llm = build_llm(settings, "system prompt")
    assert isinstance(llm._client, AsyncAnthropic)
    assert llm._settings.model == "claude-opus-5-5"


def test_bedrock_provider_uses_mantle_client(settings):
    settings.llm_provider = "bedrock"
    settings.aws_region = "us-west-2"
    llm = build_llm(settings, "system prompt")
    assert isinstance(llm._client, AsyncAnthropicBedrockMantle)
    assert "bedrock-mantle.us-west-2" in str(llm._client.base_url)
    assert llm._settings.model == "anthropic.claude-opus-5-5"
    # Pipecat maps the Bedrock id back to the model, so its Opus defaults still apply.
    assert _model_name(llm._settings.model) == "claude-opus-5-5"
    assert llm._settings.effort == "low"
    assert llm._settings.enable_prompt_caching is True


def test_bedrock_profile_is_passed_through(settings):
    settings.llm_provider = "bedrock"
    settings.aws_profile = "voice-demo"
    client = make_llm_client(settings)
    assert client.aws_profile == "voice-demo"


def test_bedrock_model_id_prefix(settings):
    settings.llm_provider = "bedrock"
    assert settings.llm_model_id == "anthropic.claude-opus-5-5"
    settings.llm_model = "us.anthropic.claude-opus-5-5"  # inference profile ids pass through
    assert settings.llm_model_id == "us.anthropic.claude-opus-5-5"


def test_required_keys_depend_on_provider(settings):
    assert "anthropic_api_key" in settings.required_for_calls()
    settings.llm_provider = "bedrock"
    settings.anthropic_api_key = SecretStr("")
    assert "anthropic_api_key" not in settings.required_for_calls()
    assert settings.missing(*settings.required_for_calls()) == []


def test_provider_is_part_of_agent_version(settings):
    direct = agent_version_spec(settings)
    settings.llm_provider = "bedrock"
    bedrock = agent_version_spec(settings)
    assert bedrock.llm_model == "anthropic.claude-opus-5-5"
    assert direct.fingerprint != bedrock.fingerprint
