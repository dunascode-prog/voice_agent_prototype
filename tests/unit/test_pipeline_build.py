"""Builds the real Pipecat pipeline with fake keys. No network: services connect on StartFrame."""

from types import SimpleNamespace

import pytest
from pipecat.processors.aggregators.llm_response_universal import (
    LLMAssistantAggregator,
    LLMUserAggregator,
)
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketInputTransport,
    FastAPIWebsocketOutputTransport,
)

from voice_agent.pipeline.bot import build_call_pipeline
from voice_agent.prompts import load_prompt
from voice_agent.telephony.media_stream import StreamStart


def test_pipeline_stages_in_order(settings):
    start = StreamStart(stream_sid="MZ1", call_sid="CA123", account_sid="AC1")
    call = build_call_pipeline(SimpleNamespace(headers={}), start, settings)

    expected = [
        FastAPIWebsocketInputTransport,
        DeepgramSTTService,
        LLMUserAggregator,
        AnthropicLLMService,
        CartesiaTTSService,
        FastAPIWebsocketOutputTransport,
        LLMAssistantAggregator,
    ]
    # Pipeline wraps the list with its own source/sink processors at both ends.
    inner = [p for p in call.pipeline.processors if isinstance(p, tuple(expected))]
    assert [type(p) for p in inner] == expected
    assert len(call.prompt_version) == 12


def test_unknown_playbook_rejected():
    with pytest.raises(ValueError):
        load_prompt("does-not-exist")


def test_booking_prompt_has_disclosures():
    prompt = load_prompt("booking")
    assert "AI assistant" in prompt
    assert "recorded" in prompt
