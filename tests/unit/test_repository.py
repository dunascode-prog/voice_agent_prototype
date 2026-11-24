from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from voice_agent.agent_version import agent_version_spec
from voice_agent.storage.models import Call, CallEvent, Recording, TranscriptSegment, Turn
from voice_agent.storage.repository import CallRepository, CallSummary, TurnRecord


async def test_agent_version_is_idempotent(db, settings):
    repo = CallRepository(db)
    spec = agent_version_spec(settings)
    assert await repo.ensure_agent_version(spec) == await repo.ensure_agent_version(spec)


async def test_agent_version_changes_with_prompt_inputs(db, settings):
    repo = CallRepository(db)
    first = await repo.ensure_agent_version(agent_version_spec(settings))
    settings.llm_effort = "medium"
    assert await repo.ensure_agent_version(agent_version_spec(settings)) != first


async def test_get_or_create_call_is_idempotent(db):
    repo = CallRepository(db)
    a = await repo.get_or_create_call("CA1", direction="inbound", from_number="+15555550123")
    b = await repo.get_or_create_call("CA1", direction="inbound", from_number="+15555550123")
    assert a == b
    assert await repo.get_call_id("CA1") == a
    assert await repo.get_call_id("CA-unknown") is None


async def test_status_updates_and_terminal_is_sticky(db):
    repo = CallRepository(db)
    await repo.get_or_create_call("CA1", direction="inbound")
    await repo.update_status("CA1", "in-progress")
    await repo.update_status("CA1", "completed", duration_s=42)
    await repo.update_status("CA1", "in-progress")  # late, out-of-order callback
    async with db.sessions() as s:
        call = await s.scalar(select(Call).where(Call.twilio_call_sid == "CA1"))
    assert call.status == "completed"
    assert call.duration_s == 42
    assert call.ended_at is not None
    assert await repo.update_status("CA-unknown", "completed") is None


async def test_add_turn_stores_redacted_and_raw_text(db):
    repo = CallRepository(db)
    call_id = await repo.get_or_create_call("CA1", direction="inbound")
    await repo.add_turn(call_id, TurnRecord(seq=1, speaker="user", text="call me at 555 555 0123"))
    await repo.add_turn(
        call_id,
        TurnRecord(seq=2, speaker="agent", text="Sure.", v2v_ms=740, llm_ttft_ms=280),
    )
    async with db.sessions() as s:
        turns = (await s.scalars(select(Turn).order_by(Turn.seq))).all()
        segs = (await s.scalars(select(TranscriptSegment))).all()
    assert [t.speaker for t in turns] == ["user", "agent"]
    assert turns[1].v2v_ms == 740 and turns[1].llm_ttft_ms == 280
    caller = next(s for s in segs if s.channel == "caller")
    assert caller.text_redacted == "call me at [NUMBER]"
    assert caller.text_raw == "call me at 555 555 0123"


async def test_finish_media_writes_rollups(db):
    repo = CallRepository(db)
    call_id = await repo.get_or_create_call("CA1", direction="inbound")
    await repo.mark_media_started(call_id)
    await repo.finish_media(
        call_id, CallSummary(agent_turns=3, v2v_p50_ms=700, v2v_p95_ms=900, greeting_ms=1100)
    )
    async with db.sessions() as s:
        call = await s.get(Call, call_id)
    assert (call.agent_turns, call.v2v_p50_ms, call.v2v_p95_ms) == (3, 700, 900)
    assert call.greeting_ms == 1100 and call.media_started_at is not None


async def test_recording_upsert(db):
    repo = CallRepository(db)
    await repo.get_or_create_call("CA1", direction="inbound")
    await repo.upsert_recording("CA1", "RE1", status="in-progress")
    await repo.upsert_recording(
        "CA1", "RE1", status="completed", channels=2, duration_s=30, source_url="https://x/RE1"
    )
    async with db.sessions() as s:
        recs = (await s.scalars(select(Recording))).all()
    assert len(recs) == 1
    assert (recs[0].status, recs[0].channels, recs[0].duration_s) == ("completed", 2, 30)
    assert await repo.upsert_recording("CA-unknown", "RE2", status="completed") is None


async def test_events(db):
    repo = CallRepository(db)
    call_id = await repo.get_or_create_call("CA1", direction="inbound")
    await repo.add_event(call_id, "stream.started", {"stream_sid": "MZ1"})
    async with db.sessions() as s:
        events = (await s.scalars(select(CallEvent))).all()
    assert [(e.type, e.payload) for e in events] == [("stream.started", {"stream_sid": "MZ1"})]


async def test_purge_raw_text(db):
    repo = CallRepository(db)
    call_id = await repo.get_or_create_call("CA1", direction="inbound")
    await repo.add_turn(call_id, TurnRecord(seq=1, speaker="user", text="old"))
    await repo.add_turn(call_id, TurnRecord(seq=2, speaker="user", text="new"))
    async with db.sessions() as s:
        await s.execute(
            update(TranscriptSegment)
            .where(TranscriptSegment.text_raw == "old")
            .values(created_at=datetime.now(UTC) - timedelta(days=31))
        )
        await s.commit()
    assert await repo.purge_raw_text(30) == 1
    async with db.sessions() as s:
        raws = sorted(
            (seg.text_redacted, seg.text_raw)
            for seg in (await s.scalars(select(TranscriptSegment))).all()
        )
    assert raws == [("new", "new"), ("old", None)]
