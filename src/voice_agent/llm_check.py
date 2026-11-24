"""`voice-llm-check`: send one real request to the configured LLM provider and time it.

Run this before the first phone call on a new provider, region or account. It uses the
same client, model id, beta header, effort and cached system prompt as the pipeline, so
if this works, the agent's LLM stage works. It prints time to first token for a cold and
a warm request, and the cache read tokens of the warm one.

    uv run voice-llm-check                      # uses LLM_PROVIDER from .env
    LLM_PROVIDER=bedrock AWS_PROFILE=demo uv run voice-llm-check
"""

import asyncio
import sys
import time

import anthropic

from voice_agent.config import Settings, get_settings
from voice_agent.pipeline.llm import make_llm_client
from voice_agent.prompts import load_prompt

# Pipecat's AnthropicLLMService sends this beta on every request.
PIPECAT_BETAS = ["interleaved-thinking-2025-05-14"]
QUESTION = "Hi, I'd like to book a plumber for tomorrow morning. What do you need from me?"

HINTS = {
    "bedrock": {
        anthropic.AuthenticationError: "AWS credentials were rejected. Check AWS_PROFILE "
        "(`aws sts get-caller-identity --profile <name>`) or run `aws sso login`.",
        anthropic.PermissionDeniedError: "The AWS identity can't invoke this model. Enable "
        "Claude in the Bedrock console (Model access) for AWS_REGION, and allow "
        "bedrock:InvokeModel* in the IAM policy.",
        anthropic.NotFoundError: "Model not found in this region. Check LLM_MODEL and AWS_REGION.",
    },
    "anthropic": {
        anthropic.AuthenticationError: "ANTHROPIC_API_KEY was rejected.",
        anthropic.PermissionDeniedError: "This API key can't use the model.",
        anthropic.NotFoundError: "Model not found. Check LLM_MODEL.",
    },
}


def _client(settings: Settings):
    return make_llm_client(settings) or anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.get_secret_value()
    )


async def _one_request(client, settings: Settings, system_prompt: str) -> dict:
    started = time.perf_counter()
    ttft_ms = None
    text: list[str] = []
    usage: dict = {}
    stream = await client.beta.messages.create(
        model=settings.llm_model_id,
        max_tokens=settings.llm_max_tokens,
        betas=PIPECAT_BETAS,
        output_config={"effort": settings.llm_effort},
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": QUESTION}],
        stream=True,
    )
    async for event in stream:
        if event.type == "message_start":
            u = event.message.usage
            usage["input"] = u.input_tokens
            usage["cache_read"] = u.cache_read_input_tokens or 0
            usage["cache_write"] = u.cache_creation_input_tokens or 0
        elif event.type == "content_block_delta" and event.delta.type == "text_delta":
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - started) * 1000
            text.append(event.delta.text)
        elif event.type == "message_delta":
            usage["output"] = event.usage.output_tokens
            usage["stop_reason"] = event.delta.stop_reason
    return {
        "ttft_ms": ttft_ms,
        "total_ms": (time.perf_counter() - started) * 1000,
        "text": "".join(text).strip(),
        **usage,
    }


async def check(settings: Settings) -> int:
    missing = settings.missing(
        "anthropic_api_key" if settings.llm_provider == "anthropic" else "aws_region"
    )
    if missing:
        print(f"missing config: {missing}")
        return 2
    where = f" in {settings.aws_region}" if settings.llm_provider == "bedrock" else ""
    profile = f" (profile {settings.aws_profile})" if settings.aws_profile else ""
    print(
        f"provider={settings.llm_provider}{where}{profile} "
        f"model={settings.llm_model_id} effort={settings.llm_effort}"
    )

    client = _client(settings)
    system_prompt = load_prompt(settings.playbook)
    try:
        for label in ("cold", "warm"):
            r = await _one_request(client, settings, system_prompt)
            ttft = f"{r['ttft_ms']:.0f}ms" if r["ttft_ms"] is not None else "n/a"
            print(
                f"{label}: ttft={ttft} total={r['total_ms']:.0f}ms "
                f"in={r.get('input')} cache_read={r.get('cache_read')} "
                f"cache_write={r.get('cache_write')} out={r.get('output')} "
                f"stop={r.get('stop_reason')}"
            )
        print(f"reply: {r['text'][:300]}")
    except anthropic.APIStatusError as e:
        hint = next(
            (h for cls, h in HINTS[settings.llm_provider].items() if isinstance(e, cls)), ""
        )
        print(f"FAILED {e.status_code}: {e.message}\n{hint}".rstrip())
        return 1
    except anthropic.APIConnectionError as e:
        print(f"FAILED to connect: {e}")
        return 1
    except Exception as e:  # noqa: BLE001 - e.g. botocore NoCredentialsError, bad profile name
        print(f"FAILED: {type(e).__name__}: {e}")
        if settings.llm_provider == "bedrock":
            print(
                "Configure AWS credentials (`aws configure sso` or `aws configure`), "
                "then set AWS_PROFILE."
            )
        return 1
    finally:
        await client.close()
    print("ok")
    return 0


def main() -> None:
    sys.exit(asyncio.run(check(get_settings())))


if __name__ == "__main__":
    main()
