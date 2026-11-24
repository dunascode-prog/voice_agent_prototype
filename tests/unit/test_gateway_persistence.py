"""Gateway webhooks with storage attached (same event loop as the db fixture)."""

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from twilio.request_validator import RequestValidator

from tests.conftest import AUTH_TOKEN, PUBLIC_BASE_URL
from voice_agent.gateway.app import app, get_twilio_client
from voice_agent.storage.models import Call, CallEvent, Recording

INBOUND = {
    "CallSid": "CA123",
    "From": "+15555550123",
    "To": "+15555550100",
    "Direction": "inbound",
    "CallStatus": "ringing",
}


@pytest.fixture
async def client(services):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=PUBLIC_BASE_URL) as c:
        yield c


async def signed_post(client, path: str, form: dict[str, str]):
    signature = RequestValidator(AUTH_TOKEN).compute_signature(PUBLIC_BASE_URL + path, form)
    return await client.post(path, data=form, headers={"X-Twilio-Signature": signature})


async def test_voice_webhook_creates_call_and_live_state(client, services):
    resp = await signed_post(client, "/twilio/voice", INBOUND)
    assert resp.status_code == 200 and "<Stream" in resp.text

    async with services.db.sessions() as s:
        call = await s.scalar(select(Call).where(Call.twilio_call_sid == "CA123"))
        events = (await s.scalars(select(CallEvent.type))).all()
    assert call.direction == "inbound"
    assert call.from_number == "+15555550123"
    assert call.agent_version_id == services.agent_version_id
    assert events == ["webhook.voice"]
    assert await services.live.get("CA123") == {
        "call_id": str(call.id),
        "direction": "inbound",
        "agent_version_id": str(services.agent_version_id),
    }


async def test_duplicate_voice_webhook_keeps_one_call(client, services):
    await signed_post(client, "/twilio/voice", INBOUND)
    await signed_post(client, "/twilio/voice", INBOUND)
    async with services.db.sessions() as s:
        calls = (await s.scalars(select(Call))).all()
    assert len(calls) == 1


async def test_status_callback_updates_call(client, services):
    await signed_post(client, "/twilio/voice", INBOUND)
    resp = await signed_post(
        client,
        "/twilio/status",
        {"CallSid": "CA123", "CallStatus": "completed", "CallDuration": "37"},
    )
    assert resp.status_code == 204
    async with services.db.sessions() as s:
        call = await s.scalar(select(Call).where(Call.twilio_call_sid == "CA123"))
    assert (call.status, call.duration_s) == ("completed", 37)
    assert call.ended_at is not None


async def test_recording_callback_stores_recording(client, services):
    await signed_post(client, "/twilio/voice", INBOUND)
    resp = await signed_post(
        client,
        "/twilio/recording",
        {
            "CallSid": "CA123",
            "RecordingSid": "RE1",
            "RecordingStatus": "completed",
            "RecordingChannels": "2",
            "RecordingDuration": "36",
            "RecordingUrl": "https://api.twilio.com/recordings/RE1",
        },
    )
    assert resp.status_code == 204
    async with services.db.sessions() as s:
        rec = await s.scalar(select(Recording))
    assert (rec.twilio_recording_sid, rec.status, rec.channels, rec.duration_s) == (
        "RE1",
        "completed",
        2,
        36,
    )


async def test_recording_callback_requires_signature(client):
    resp = await client.post("/twilio/recording", data={"CallSid": "CA123", "RecordingSid": "RE1"})
    assert resp.status_code == 403


async def test_outbound_call_is_saved(client, services):
    fake = SimpleNamespace(calls=SimpleNamespace(create=lambda **kw: SimpleNamespace(sid="CA777")))
    app.dependency_overrides[get_twilio_client] = lambda: fake
    try:
        resp = await client.post(
            "/calls/outbound",
            json={"to": "+15555550123"},
            headers={"Authorization": "Bearer internal-secret"},
        )
    finally:
        app.dependency_overrides.pop(get_twilio_client, None)
    assert resp.status_code == 202
    async with services.db.sessions() as s:
        call = await s.scalar(select(Call).where(Call.twilio_call_sid == "CA777"))
    assert (call.direction, call.status, call.to_number) == ("outbound", "queued", "+15555550123")


async def test_webhook_still_connects_when_database_fails(client, services, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(services.repo, "get_or_create_call", boom)
    resp = await signed_post(client, "/twilio/voice", INBOUND)
    assert resp.status_code == 200 and "<Stream" in resp.text
