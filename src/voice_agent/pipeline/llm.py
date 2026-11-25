"""Claude for the voice pipeline, through the Claude API or Amazon Bedrock.

Both providers use Pipecat's `AnthropicLLMService`, so the request shape (streaming,
effort, prompt caching, tools) is identical. For Bedrock we hand it the Anthropic SDK's
Bedrock client (`AsyncAnthropicBedrockMantle`, the Messages API on Bedrock), which signs
requests with AWS SigV4 from the default credential chain: an `aws_profile` locally, the
ECS task role in AWS. No AWS keys are stored in settings.
"""

from anthropic import AsyncAnthropicBedrockMantle
from pipecat.services.anthropic.llm import AnthropicLLMService

from voice_agent.config import Settings


def make_llm_client(settings: Settings) -> AsyncAnthropicBedrockMantle | None:
    """The SDK client for the configured provider, or None for the Claude API default."""
    if settings.llm_provider == "bedrock":
        return AsyncAnthropicBedrockMantle(
            aws_region=settings.aws_region,
            aws_profile=settings.aws_profile or None,
        )
    return None


def build_llm(settings: Settings, system_prompt: str) -> AnthropicLLMService:
    return AnthropicLLMService(
        # Ignored when `client` is given (Bedrock).
        api_key=settings.anthropic_api_key.get_secret_value(),
        client=make_llm_client(settings),
        settings=AnthropicLLMService.Settings(
            model=settings.llm_model_id,
            system_instruction=system_prompt,
            effort=settings.llm_effort,
            max_tokens=settings.llm_max_tokens,
            enable_prompt_caching=True,
        ),
    )
