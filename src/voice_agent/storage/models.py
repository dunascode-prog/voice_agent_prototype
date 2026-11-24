"""Relational model (subset of docs/DATAFLOW.md section 3 used from phase 2).

Contacts, campaigns, tool invocations, outcomes, transfers and eval results are added in
the phases that produce them. Types are portable (Postgres in production, SQLite in tests);
JSON becomes JSONB on Postgres.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class AgentVersion(Base):
    """Everything that defines how the agent behaves; one row per distinct combination."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_agent_versions_fingerprint"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64))
    playbook: Mapped[str] = mapped_column(String(64))
    prompt_hash: Mapped[str] = mapped_column(String(32))
    llm_model: Mapped[str] = mapped_column(String(64))
    llm_effort: Mapped[str] = mapped_column(String(16))
    stt_model: Mapped[str] = mapped_column(String(64))
    tts_voice_id: Mapped[str] = mapped_column(String(128))
    turn_config: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    twilio_call_sid: Mapped[str] = mapped_column(String(64), unique=True)
    direction: Mapped[str] = mapped_column(String(16))  # inbound | outbound
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_versions.id"))
    from_number: Mapped[str | None] = mapped_column(String(32))  # PII
    to_number: Mapped[str | None] = mapped_column(String(32))  # PII
    status: Mapped[str] = mapped_column(String(32), default="queued")
    end_reason: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    media_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_s: Mapped[int | None] = mapped_column(Integer)
    # Per-call rollups written when the media stream ends (EVALUATION.md section 2).
    greeting_ms: Mapped[int | None] = mapped_column(Integer)
    v2v_p50_ms: Mapped[int | None] = mapped_column(Integer)
    v2v_p95_ms: Mapped[int | None] = mapped_column(Integer)
    agent_turns: Mapped[int | None] = mapped_column(Integer)
    interruption_count: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)

    agent_version: Mapped[AgentVersion | None] = relationship()
    turns: Mapped[list["Turn"]] = relationship(back_populates="call", order_by="Turn.seq")
    events: Mapped[list["CallEvent"]] = relationship(back_populates="call", order_by="CallEvent.at")
    recordings: Mapped[list["Recording"]] = relationship(back_populates="call")


class Turn(Base):
    """One user or agent turn, with the per-stage latency of agent turns (ms)."""

    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("call_id", "seq", name="uq_turns_call_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(16))  # user | agent
    kind: Mapped[str] = mapped_column(String(16), default="speech")  # speech | greeting
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    interrupted: Mapped[bool] = mapped_column(Boolean, default=False)
    # Latency (agent turns only). See docs/ARCHITECTURE.md section 7 for definitions.
    v2v_ms: Mapped[int | None] = mapped_column(Integer)
    user_turn_ms: Mapped[int | None] = mapped_column(Integer)  # end of speech -> turn released
    stt_ttfb_ms: Mapped[int | None] = mapped_column(Integer)
    llm_ttft_ms: Mapped[int | None] = mapped_column(Integer)
    agg_ms: Mapped[int | None] = mapped_column(Integer)
    tts_ttfb_ms: Mapped[int | None] = mapped_column(Integer)
    latency_breakdown: Mapped[list | None] = mapped_column(JSONType)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)

    call: Mapped[Call] = relationship(back_populates="turns")
    segments: Mapped[list["TranscriptSegment"]] = relationship(back_populates="turn")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        Index("ix_transcript_segments_call_id", "call_id"),
        Index("ix_transcript_segments_created_at", "created_at"),  # retention purge
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"))
    channel: Mapped[str] = mapped_column(String(16))  # caller | agent
    text_redacted: Mapped[str] = mapped_column(Text)
    # Unredacted text. Until the post-call worker moves raw transcripts to S3 (phase 5),
    # it lives here and is purged after 30 days (`voice-maintenance purge-raw`).
    text_raw: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    turn: Mapped[Turn] = relationship(back_populates="segments")


class CallEvent(Base):
    """Append-only log: webhooks, stream start/stop, recording, failures."""

    __tablename__ = "call_events"
    __table_args__ = (Index("ix_call_events_call_at", "call_id", "at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    call: Mapped[Call] = relationship(back_populates="events")


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    twilio_recording_sid: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    channels: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    # Twilio-hosted URL until the worker copies the file to S3 (phase 5).
    source_url: Mapped[str | None] = mapped_column(String(512))
    s3_key: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    call: Mapped[Call] = relationship(back_populates="recordings")


__all__ = [
    "AgentVersion",
    "Base",
    "Call",
    "CallEvent",
    "Recording",
    "TranscriptSegment",
    "Turn",
]
