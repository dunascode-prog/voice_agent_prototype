from types import SimpleNamespace

from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMTokenUsage, LLMUsageMetricsData
from pipecat.observers.user_bot_latency_observer import (
    LatencyBreakdown,
    LatencyContribution,
    LatencyOwnerKind,
    MeasuredFrom,
    TextAggregationBreakdownMetrics,
    TTFBBreakdownMetrics,
)
from sqlalchemy import select

from voice_agent.pipeline.call_recorder import CallRecorder, UsageObserver, percentile
from voice_agent.storage.models import Turn
from voice_agent.storage.repository import CallRepository

NAMES = {"stt_name": "stt#0", "llm_name": "llm#0", "tts_name": "tts#0"}


def breakdown(total: float, measured_from=MeasuredFrom.USER_SILENCE) -> LatencyBreakdown:
    return LatencyBreakdown(
        ttfb=[
            TTFBBreakdownMetrics(processor="stt#0", start_time=0, duration_secs=0.06),
            TTFBBreakdownMetrics(processor="llm#0", start_time=0, duration_secs=0.28),
            TTFBBreakdownMetrics(processor="tts#0", start_time=0, duration_secs=0.09),
        ],
        contributions=[
            LatencyContribution(
                key="llm",
                label="LLM",
                owner="llm#0",
                owner_kind=LatencyOwnerKind.SERVICE,
                start_time=0,
                duration_secs=total,
            )
        ],
        measured_from=measured_from,
        total_secs=total,
        text_aggregation=TextAggregationBreakdownMetrics(
            processor="tts#0", start_time=0, duration_secs=0.03
        ),
        user_turn_secs=0.26 if measured_from == MeasuredFrom.USER_SILENCE else None,
    )


def test_percentile():
    assert percentile([], 50) is None
    assert percentile([100], 95) == 100
    assert percentile([100, 200, 300, 400], 50) == 200
    assert percentile(list(range(1, 101)), 95) == 95


async def test_turns_latency_and_summary_are_persisted(db):
    repo = CallRepository(db)
    call_id = await repo.get_or_create_call("CA1", direction="inbound")
    rec = CallRecorder(call_id, repo, **NAMES)
    rec.start()

    rec.on_latency(breakdown(1.1, MeasuredFrom.CLIENT_CONNECTED))  # greeting
    rec.on_usage(900, 40, 800)
    rec.on_agent_turn("Hi, this is Acme's AI assistant.", interrupted=False)

    rec.on_user_turn("I need my heating fixed")
    rec.on_latency(breakdown(0.74))
    rec.on_usage(950, 30, 900)
    rec.on_agent_turn("Sure, what's your ZIP code?", interrupted=False)

    rec.on_user_turn("nine oh two one oh")
    rec.on_latency(breakdown(0.9))
    rec.on_agent_turn("Thanks, and", interrupted=True)

    summary = await rec.close()
    assert summary.agent_turns == 3
    assert summary.interruption_count == 1
    assert summary.greeting_ms == 1100
    assert summary.v2v_p50_ms == 740  # greeting excluded from v2v percentiles
    assert summary.v2v_p95_ms == 900
    assert (summary.input_tokens, summary.output_tokens, summary.cached_tokens) == (1850, 70, 1700)

    async with db.sessions() as s:
        turns = (await s.scalars(select(Turn).order_by(Turn.seq))).all()
    assert [(t.seq, t.speaker, t.kind) for t in turns] == [
        (1, "agent", "greeting"),
        (2, "user", "speech"),
        (3, "agent", "speech"),
        (4, "user", "speech"),
        (5, "agent", "speech"),
    ]
    agent = turns[2]
    assert (agent.v2v_ms, agent.stt_ttfb_ms, agent.llm_ttft_ms, agent.tts_ttfb_ms) == (
        740,
        60,
        280,
        90,
    )
    assert agent.agg_ms == 30 and agent.user_turn_ms == 260
    assert agent.input_tokens == 950 and agent.cached_tokens == 900
    assert agent.latency_breakdown == [{"key": "llm", "owner": "llm#0", "ms": 740}]
    assert turns[4].interrupted is True


async def test_without_call_id_nothing_is_written(db):
    rec = CallRecorder(None, CallRepository(db), **NAMES)
    rec.start()
    rec.on_user_turn("hello")
    rec.on_latency(breakdown(0.7))
    rec.on_agent_turn("hi", interrupted=False)
    summary = await rec.close()
    assert summary.agent_turns == 1
    async with db.sessions() as s:
        assert (await s.scalars(select(Turn))).all() == []


async def test_usage_observer_counts_first_push_only():
    rec = CallRecorder(None, None, **NAMES)
    frame = MetricsFrame(
        data=[
            LLMUsageMetricsData(
                processor="llm#0",
                value=LLMTokenUsage(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    cache_read_input_tokens=8,
                ),
            )
        ]
    )
    observer = UsageObserver(rec)
    await observer.on_push_frame(SimpleNamespace(frame=frame, first_push=True))
    await observer.on_push_frame(SimpleNamespace(frame=frame, first_push=False))
    assert rec.summary().input_tokens == 10
    assert rec.summary().cached_tokens == 8
