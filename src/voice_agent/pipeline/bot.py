"""Builds and runs the Pipecat pipeline for one phone call.

    Twilio WS in -> Deepgram STT -> user aggregator (Silero VAD + smart-turn v3)
      -> Claude -> Cartesia TTS -> Twilio WS out -> assistant aggregator

Barge-in comes from Pipecat's defaults: when the user starts a turn while the bot is
speaking, the LLM and TTS are interrupted and the serializer sends Twilio a `clear`.
Audio runs at 8 kHz end to end; the Twilio serializer converts mu-law <-> PCM.

Each turn's latency breakdown, transcript and token usage go to a CallRecorder, which
persists them (phase 2). Recording is started through the Twilio REST API once the media
stream is live.
"""

import asyncio
import uuid
from dataclasses import dataclass

from fastapi import WebSocket
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.workers.runner import WorkerRunner

from voice_agent.config import Settings
from voice_agent.pipeline.call_recorder import CallRecorder, UsageObserver
from voice_agent.pipeline.llm import build_llm
from voice_agent.prompts import load_prompt, prompt_hash
from voice_agent.services import Services, best_effort
from voice_agent.storage.repository import CallRepository
from voice_agent.telephony.media_stream import StreamStart
from voice_agent.telephony.recording import start_recording

SAMPLE_RATE = 8000  # Twilio Media Streams are 8 kHz mu-law


@dataclass
class CallPipeline:
    transport: FastAPIWebsocketTransport
    pipeline: Pipeline
    worker: PipelineWorker
    recorder: CallRecorder
    prompt_version: str


def build_call_pipeline(
    websocket: WebSocket,
    start: StreamStart,
    settings: Settings,
    *,
    call_id: uuid.UUID | None = None,
    repo: CallRepository | None = None,
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
    llm = build_llm(settings, system_prompt)

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

    recorder = CallRecorder(call_id, repo, stt_name=stt.name, llm_name=llm.name, tts_name=tts.name)
    latency_observer = UserBotLatencyObserver()

    @latency_observer.event_handler("on_latency_breakdown")
    async def on_latency_breakdown(_observer, breakdown):
        recorder.on_latency(breakdown)

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(_aggregator, _strategy, message):
        recorder.on_user_turn(message.content or "")

    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_assistant_turn_stopped(_aggregator, message):
        recorder.on_agent_turn(message.content, message.interrupted)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[latency_observer, UsageObserver(recorder)],
        # RTVI is a protocol for web/app clients; a phone line has none.
        enable_rtvi=False,
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
        recorder=recorder,
        prompt_version=prompt_hash(system_prompt),
    )


async def run_call(
    websocket: WebSocket,
    start: StreamStart,
    settings: Settings,
    services: Services | None = None,
    call_id: uuid.UUID | None = None,
) -> None:
    repo = services.repo if services is not None and call_id is not None else None
    call = build_call_pipeline(websocket, start, settings, call_id=call_id, repo=repo)
    logger.info(
        f"call {start.call_sid}: pipeline starting "
        f"(llm={settings.llm_provider}/{settings.llm_model_id}, "
        f"playbook={settings.playbook}, prompt={call.prompt_version}, call_id={call_id})"
    )
    if repo is not None:
        await best_effort(repo.mark_media_started(call_id), "mark media started")
        await best_effort(
            repo.add_event(call_id, "stream.started", {"stream_sid": start.stream_sid}),
            "stream.started event",
        )
    if settings.recording_enabled:
        recording_sid = await best_effort(
            asyncio.to_thread(start_recording, settings, start.call_sid), "start recording"
        )
        if repo is not None and recording_sid:
            await best_effort(
                repo.add_event(call_id, "recording.started", {"recording_sid": recording_sid}),
                "recording.started event",
            )

    call.recorder.start()
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(call.worker)
    try:
        await runner.run()
    finally:
        summary = await call.recorder.close()
        logger.info(
            f"call {start.call_sid}: pipeline finished, agent_turns={summary.agent_turns} "
            f"v2v_p50={summary.v2v_p50_ms}ms v2v_p95={summary.v2v_p95_ms}ms "
            f"greeting={summary.greeting_ms}ms interruptions={summary.interruption_count}"
        )
        if repo is not None:
            await best_effort(repo.finish_media(call_id, summary), "finish media")
            await best_effort(repo.add_event(call_id, "stream.stopped"), "stream.stopped event")
