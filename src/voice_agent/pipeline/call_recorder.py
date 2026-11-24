"""Collects turns, per-stage latency and token usage for one call, and persists them.

Inputs (wired in bot.py):
  * user aggregator `on_user_turn_stopped`           -> caller turn text
  * UserBotLatencyObserver `on_latency_breakdown`    -> stage latencies for the next agent turn
  * MetricsFrame(LLMUsageMetricsData) via UsageObserver -> token counts
  * assistant aggregator `on_assistant_turn_stopped` -> agent turn text + interrupted flag

Writes go through a queue drained by one background task, so a slow or failing database
never blocks the audio pipeline.
"""

import asyncio
import math
import uuid
from dataclasses import dataclass, field

from loguru import logger
from pipecat.frames.frames import MetricsFrame
from pipecat.metrics.metrics import LLMUsageMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.observers.user_bot_latency_observer import LatencyBreakdown, MeasuredFrom

from voice_agent.services import best_effort
from voice_agent.storage.repository import CallRepository, CallSummary, TurnRecord


def _ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * 1000)


def percentile(values: list[int], pct: float) -> int | None:
    """Nearest-rank percentile; None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


@dataclass
class _Pending:
    """Latency and usage collected for the agent turn that is being produced."""

    latency: dict = field(default_factory=dict)
    is_greeting: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class CallRecorder:
    def __init__(
        self,
        call_id: uuid.UUID | None,
        repo: CallRepository | None,
        *,
        stt_name: str,
        llm_name: str,
        tts_name: str,
    ):
        self.call_id = call_id
        self.repo = repo if call_id is not None else None
        self._names = {"stt": stt_name, "llm": llm_name, "tts": tts_name}
        self._seq = 0
        self._pending = _Pending()
        self._agent_turns: list[TurnRecord] = []
        self._greeting_ms: int | None = None
        self._totals = {"input": 0, "output": 0, "cached": 0}
        self._queue: asyncio.Queue[TurnRecord | None] = asyncio.Queue()
        self._writer: asyncio.Task | None = None

    # --- lifecycle ---

    def start(self) -> None:
        if self.repo is not None and self._writer is None:
            self._writer = asyncio.create_task(self._drain(), name=f"call-recorder-{self.call_id}")

    async def close(self) -> CallSummary:
        if self._writer is not None:
            await self._queue.put(None)
            await self._writer
            self._writer = None
        return self.summary()

    async def _drain(self) -> None:
        while (record := await self._queue.get()) is not None:
            await best_effort(self.repo.add_turn(self.call_id, record), f"turn {record.seq}")

    # --- inputs ---

    def on_user_turn(self, text: str) -> None:
        self._append(TurnRecord(seq=self._next_seq(), speaker="user", text=text or ""))

    def on_latency(self, breakdown: LatencyBreakdown) -> None:
        by_stage: dict[str, float] = {}
        for ttfb in breakdown.ttfb:
            for stage, name in self._names.items():
                if ttfb.processor == name and stage not in by_stage:
                    by_stage[stage] = ttfb.duration_secs
        greeting = breakdown.measured_from == MeasuredFrom.CLIENT_CONNECTED
        self._pending.is_greeting = greeting
        self._pending.latency = {
            "v2v_ms": _ms(breakdown.total_secs) if breakdown.contributions else None,
            "user_turn_ms": _ms(breakdown.user_turn_secs),
            "stt_ttfb_ms": _ms(by_stage.get("stt")),
            "llm_ttft_ms": _ms(by_stage.get("llm")),
            "tts_ttfb_ms": _ms(by_stage.get("tts")),
            "agg_ms": _ms(breakdown.text_aggregation.duration_secs)
            if breakdown.text_aggregation
            else None,
            "latency_breakdown": [
                {"key": c.key, "owner": c.owner, "ms": _ms(c.duration_secs)}
                for c in breakdown.contributions
            ],
        }
        if greeting and self._greeting_ms is None:
            self._greeting_ms = self._pending.latency["v2v_ms"]

    def on_usage(self, prompt: int, completion: int, cached: int | None) -> None:
        self._pending.input_tokens += prompt
        self._pending.output_tokens += completion
        self._pending.cached_tokens += cached or 0
        self._totals["input"] += prompt
        self._totals["output"] += completion
        self._totals["cached"] += cached or 0

    def on_agent_turn(self, text: str, interrupted: bool) -> None:
        p = self._pending
        record = TurnRecord(
            seq=self._next_seq(),
            speaker="agent",
            text=text or "",
            kind="greeting" if p.is_greeting else "speech",
            interrupted=interrupted,
            input_tokens=p.input_tokens or None,
            output_tokens=p.output_tokens or None,
            cached_tokens=p.cached_tokens or None,
            **p.latency,
        )
        self._agent_turns.append(record)
        self._append(record)
        self._pending = _Pending()

    # --- outputs ---

    def summary(self) -> CallSummary:
        v2v = [t.v2v_ms for t in self._agent_turns if t.kind == "speech" and t.v2v_ms is not None]
        return CallSummary(
            agent_turns=len(self._agent_turns),
            interruption_count=sum(1 for t in self._agent_turns if t.interrupted),
            greeting_ms=self._greeting_ms,
            v2v_p50_ms=percentile(v2v, 50),
            v2v_p95_ms=percentile(v2v, 95),
            input_tokens=self._totals["input"],
            output_tokens=self._totals["output"],
            cached_tokens=self._totals["cached"],
        )

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _append(self, record: TurnRecord) -> None:
        if record.speaker == "agent":
            logger.info(
                f"call {self.call_id}: turn {record.seq} v2v={record.v2v_ms}ms "
                f"stt={record.stt_ttfb_ms} llm={record.llm_ttft_ms} tts={record.tts_ttfb_ms} "
                f"interrupted={record.interrupted}"
            )
        if self.repo is not None:
            self._queue.put_nowait(record)


class UsageObserver(BaseObserver):
    """Feeds LLM token usage from MetricsFrames into the recorder."""

    def __init__(self, recorder: CallRecorder, **kwargs):
        super().__init__(**kwargs)
        self._recorder = recorder

    async def on_push_frame(self, data: FramePushed):
        if not data.first_push or not isinstance(data.frame, MetricsFrame):
            return
        for item in data.frame.data:
            if isinstance(item, LLMUsageMetricsData):
                usage = item.value
                self._recorder.on_usage(
                    usage.prompt_tokens, usage.completion_tokens, usage.cache_read_input_tokens
                )
