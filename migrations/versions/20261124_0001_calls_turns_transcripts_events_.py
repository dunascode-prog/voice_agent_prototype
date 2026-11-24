"""calls turns transcripts events recordings

Revision ID: 0001
Revises:
Create Date: 2026-11-24 10:26:40.917353
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:

    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("playbook", sa.String(length=64), nullable=False),
        sa.Column("prompt_hash", sa.String(length=32), nullable=False),
        sa.Column("llm_model", sa.String(length=64), nullable=False),
        sa.Column("llm_effort", sa.String(length=16), nullable=False),
        sa.Column("stt_model", sa.String(length=64), nullable=False),
        sa.Column("tts_voice_id", sa.String(length=128), nullable=False),
        sa.Column(
            "turn_config",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint", name="uq_agent_versions_fingerprint"),
    )
    op.create_table(
        "calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("twilio_call_sid", sa.String(length=64), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("agent_version_id", sa.Uuid(), nullable=True),
        sa.Column("from_number", sa.String(length=32), nullable=True),
        sa.Column("to_number", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("end_reason", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("media_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("greeting_ms", sa.Integer(), nullable=True),
        sa.Column("v2v_p50_ms", sa.Integer(), nullable=True),
        sa.Column("v2v_p95_ms", sa.Integer(), nullable=True),
        sa.Column("agent_turns", sa.Integer(), nullable=True),
        sa.Column("interruption_count", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("twilio_call_sid"),
    )
    op.create_table(
        "call_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("call_events", schema=None) as batch_op:
        batch_op.create_index("ix_call_events_call_at", ["call_id", "at"], unique=False)

    op.create_table(
        "recordings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("twilio_recording_sid", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.String(length=512), nullable=True),
        sa.Column("s3_key", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("twilio_recording_sid"),
    )
    op.create_table(
        "turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interrupted", sa.Boolean(), nullable=False),
        sa.Column("v2v_ms", sa.Integer(), nullable=True),
        sa.Column("user_turn_ms", sa.Integer(), nullable=True),
        sa.Column("stt_ttfb_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ttft_ms", sa.Integer(), nullable=True),
        sa.Column("agg_ms", sa.Integer(), nullable=True),
        sa.Column("tts_ttfb_ms", sa.Integer(), nullable=True),
        sa.Column(
            "latency_breakdown",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id", "seq", name="uq_turns_call_seq"),
    )
    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("text_redacted", sa.Text(), nullable=False),
        sa.Column("text_raw", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.create_index("ix_transcript_segments_call_id", ["call_id"], unique=False)
        batch_op.create_index("ix_transcript_segments_created_at", ["created_at"], unique=False)


def downgrade() -> None:

    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.drop_index("ix_transcript_segments_created_at")
        batch_op.drop_index("ix_transcript_segments_call_id")

    op.drop_table("transcript_segments")
    op.drop_table("turns")
    op.drop_table("recordings")
    with op.batch_alter_table("call_events", schema=None) as batch_op:
        batch_op.drop_index("ix_call_events_call_at")

    op.drop_table("call_events")
    op.drop_table("calls")
    op.drop_table("agent_versions")
