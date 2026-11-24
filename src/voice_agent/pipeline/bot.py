"""Builds and runs the Pipecat pipeline for one phone call.

    Twilio WS in -> Deepgram STT -> user aggregator (Silero VAD + smart-turn v3)
      -> Claude -> Cartesia TTS -> Twilio WS out -> assistant aggregator

Barge-in comes from Pipecat's defaults: when the user starts a turn while the bot is
speaking, the LLM and TTS are interrupted and the serializer sends Twilio a `clear`.
Audio runs at 8 kHz end to end; the Twilio serializer converts mu-law <-> PCM.
"""

from dataclasses import dataclass

from fastapi import WebSocket
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.workers.runner import WorkerRunner

from voice_agent.config import Settings
from voice_agent.prompts import load_prompt, prompt_hash
from voice_agent.telephony.media_stream import StreamStart

SAMPLE_RATE = 8000  # Twilio Media Streams are 8 kHz mu-law


@dataclass
class CallPipeline:
    transport: FastAPIWebsocketTransport
    pipeline: Pipeline
    worker: PipelineWorker
    prompt_version: str


def build_call_pipeline(
    websocket: WebSocket, start: StreamStart, settings: Settings
) -> CallPipeline:
    serializer = TwilioFrameSerializer(
        stream_sid=start.stream_sid,
        call_sid=start.call_sid,
        account_sid=settings.twilio_account_sid,
        auth_token=settings.twilio_auth_token.get_secret_value(),
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
            session_timeout=settings.max_call_duration_s,
            # Twilio sends no Origin header; the signed stream token authenticates instead.
            allowed_origins=[],
        ),
    )

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key.get_secret_value(),
        settings=DeepgramSTTService.Settings(
            model=settings.deepgram_model,
            language=settings.deepgram_language,
            smart_format=True,
            interim_results=True,
        ),
    )

    system_prompt = load_prompt(settings.playbook)
    llm = AnthropicLLMService(
        api_key=settings.anthropic_api_key.get_secret_value(),
        settings=AnthropicLLMService.Settings(
            model=settings.llm_model,
            system_instruction=system_prompt,
            effort=settings.llm_effort,
            max_tokens=settings.llm_max_tokens,
            enable_prompt_caching=True,
        ),
    )

    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key.get_secret_value(),
        settings=CartesiaTTSService.Settings(voice=settings.cartesia_voice_id_en),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            # 200 ms of silence before the smart-turn model (Pipecat's default stop
            # strategy) decides whether the caller finished (latency budget stage 3).
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=settings.vad_stop_secs)),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        # The prompt tells the model to open with the greeting and disclosures.
        # Pre-synthesized greeting audio is a later optimisation (ARCHITECTURE.md 9).
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info(f"call {start.call_sid}: media stream closed")
        await worker.cancel()

    return CallPipeline(
        transport=transport,
        pipeline=pipeline,
        worker=worker,
        prompt_version=prompt_hash(system_prompt),
    )


async def run_call(websocket: WebSocket, start: StreamStart, settings: Settings) -> None:
    call = build_call_pipeline(websocket, start, settings)
    logger.info(
        f"call {start.call_sid}: pipeline starting (model={settings.llm_model}, "
        f"playbook={settings.playbook}, prompt={call.prompt_version})"
    )
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(call.worker)
    await runner.run()
    logger.info(f"call {start.call_sid}: pipeline finished")
