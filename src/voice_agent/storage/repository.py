"""Call persistence. All writes are idempotent where Twilio may retry a webhook."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from voice_agent.agent_version import AgentVersionSpec
from voice_agent.observability.redact import redact
from voice_agent.storage.db import Database
from voice_agent.storage.models import (
    AgentVersion,
    Call,
    CallEvent,
    Recording,
    TranscriptSegment,
    Turn,
)

TERMINAL_STATUSES = {"completed", "busy", "failed", "no-answer", "canceled"}


@dataclass
class TurnRecord:
    seq: int
    speaker: str  # user | agent
    text: str
    kind: str = "speech"
    interrupted: bool = False
    v2v_ms: int | None = None
    user_turn_ms: int | None = None
    stt_ttfb_ms: int | None = None
    llm_ttft_ms: int | None = None
    agg_ms: int | None = None
    tts_ttfb_ms: int | None = None
    latency_breakdown: list | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass
class CallSummary:
    agent_turns: int = 0
    interruption_count: int = 0
    greeting_ms: int | None = None
    v2v_p50_ms: int | None = None
    v2v_p95_ms: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    end_reason: str = "stream_closed"
    extra: dict = field(default_factory=dict)


class CallRepository:
    def __init__(self, db: Database):
        self.db = db

    # --- agent versions ---

    async def ensure_agent_version(self, spec: AgentVersionSpec) -> uuid.UUID:
        async with self.db.sessions() as s:
            existing = await s.scalar(
                select(AgentVersion.id).where(AgentVersion.fingerprint == spec.fingerprint)
            )
            if existing:
                return existing
            row = AgentVersion(fingerprint=spec.fingerprint, **spec.columns())
            s.add(row)
            try:
                await s.commit()
                return row.id
            except IntegrityError:  # created concurrently
                await s.rollback()
                return await s.scalar(
                    select(AgentVersion.id).where(AgentVersion.fingerprint == spec.fingerprint)
                )

    # --- calls ---

    async def get_or_create_call(
        self,
        call_sid: str,
        *,
        direction: str,
        from_number: str | None = None,
        to_number: str | None = None,
        agent_version_id: uuid.UUID | None = None,
        status: str = "queued",
    ) -> uuid.UUID:
        async with self.db.sessions() as s:
            existing = await s.scalar(select(Call.id).where(Call.twilio_call_sid == call_sid))
            if existing:
                if agent_version_id:
                    await s.execute(
                        update(Call)
                        .where(Call.id == existing, Call.agent_version_id.is_(None))
                        .values(agent_version_id=agent_version_id)
                    )
                    await s.commit()
                return existing
            row = Call(
                twilio_call_sid=call_sid,
                direction=direction,
                from_number=from_number,
                to_number=to_number,
                agent_version_id=agent_version_id,
                status=status,
            )
            s.add(row)
            try:
                await s.commit()
                return row.id
            except IntegrityError:  # duplicate webhook delivered concurrently
                await s.rollback()
                return await s.scalar(select(Call.id).where(Call.twilio_call_sid == call_sid))

    async def get_call_id(self, call_sid: str) -> uuid.UUID | None:
        async with self.db.sessions() as s:
            return await s.scalar(select(Call.id).where(Call.twilio_call_sid == call_sid))

    async def update_status(
        self, call_sid: str, status: str, duration_s: int | None = None
    ) -> uuid.UUID | None:
        async with self.db.sessions() as s:
            call = await s.scalar(select(Call).where(Call.twilio_call_sid == call_sid))
            if call is None:
                return None
            # Status callbacks can arrive out of order; never reopen a finished call.
            if call.status not in TERMINAL_STATUSES:
                call.status = status
            if status in TERMINAL_STATUSES:
                call.ended_at = call.ended_at or datetime.now(UTC)
                if duration_s is not None:
                    call.duration_s = duration_s
            await s.commit()
            return call.id

    async def mark_media_started(self, call_id: uuid.UUID) -> None:
        async with self.db.sessions() as s:
            await s.execute(
                update(Call).where(Call.id == call_id).values(media_started_at=datetime.now(UTC))
            )
            await s.commit()

    async def finish_media(self, call_id: uuid.UUID, summary: CallSummary) -> None:
        async with self.db.sessions() as s:
            await s.execute(
                update(Call)
                .where(Call.id == call_id)
                .values(
                    agent_turns=summary.agent_turns,
                    interruption_count=summary.interruption_count,
                    greeting_ms=summary.greeting_ms,
                    v2v_p50_ms=summary.v2v_p50_ms,
                    v2v_p95_ms=summary.v2v_p95_ms,
                    input_tokens=summary.input_tokens,
                    output_tokens=summary.output_tokens,
                    cached_tokens=summary.cached_tokens,
                    end_reason=summary.end_reason,
                )
            )
            await s.commit()

    # --- events, turns, recordings ---

    async def add_event(self, call_id: uuid.UUID, type_: str, payload: dict | None = None) -> None:
        async with self.db.sessions() as s:
            s.add(CallEvent(call_id=call_id, type=type_, payload=payload or {}))
            await s.commit()

    async def add_turn(self, call_id: uuid.UUID, record: TurnRecord) -> None:
        async with self.db.sessions() as s:
            turn = Turn(
                call_id=call_id,
                seq=record.seq,
                speaker=record.speaker,
                kind=record.kind,
                interrupted=record.interrupted,
                v2v_ms=record.v2v_ms,
                user_turn_ms=record.user_turn_ms,
                stt_ttfb_ms=record.stt_ttfb_ms,
                llm_ttft_ms=record.llm_ttft_ms,
                agg_ms=record.agg_ms,
                tts_ttfb_ms=record.tts_ttfb_ms,
                latency_breakdown=record.latency_breakdown,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                cached_tokens=record.cached_tokens,
            )
            s.add(turn)
            await s.flush()
            if record.text:
                s.add(
                    TranscriptSegment(
                        call_id=call_id,
                        turn_id=turn.id,
                        channel="caller" if record.speaker == "user" else "agent",
                        text_redacted=redact(record.text),
                        text_raw=record.text,
                    )
                )
            await s.commit()

    async def upsert_recording(
        self,
        call_sid: str,
        recording_sid: str,
        *,
        status: str,
        channels: int | None = None,
        duration_s: int | None = None,
        source_url: str | None = None,
    ) -> uuid.UUID | None:
        async with self.db.sessions() as s:
            call_id = await s.scalar(select(Call.id).where(Call.twilio_call_sid == call_sid))
            if call_id is None:
                return None
            rec = await s.scalar(
                select(Recording).where(Recording.twilio_recording_sid == recording_sid)
            )
            if rec is None:
                rec = Recording(call_id=call_id, twilio_recording_sid=recording_sid, status=status)
                s.add(rec)
            rec.status = status
            rec.channels = channels if channels is not None else rec.channels
            rec.duration_s = duration_s if duration_s is not None else rec.duration_s
            rec.source_url = source_url or rec.source_url
            await s.commit()
            return call_id

    # --- retention ---

    async def purge_raw_text(self, older_than_days: int) -> int:
        """Null out unredacted transcript text older than the retention window."""
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        async with self.db.sessions() as s:
            count = await s.scalar(
                select(func.count())
                .select_from(TranscriptSegment)
                .where(
                    TranscriptSegment.created_at < cutoff, TranscriptSegment.text_raw.isnot(None)
                )
            )
            await s.execute(
                update(TranscriptSegment)
                .where(
                    TranscriptSegment.created_at < cutoff, TranscriptSegment.text_raw.isnot(None)
                )
                .values(text_raw=None)
            )
            await s.commit()
            return int(count or 0)
