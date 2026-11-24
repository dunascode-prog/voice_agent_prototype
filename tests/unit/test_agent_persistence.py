from types import SimpleNamespace

from voice_agent.pipeline.server import resolve_call_id
from voice_agent.telephony.recording import start_recording


async def test_call_id_from_live_state(services):
    call_id = await services.repo.get_or_create_call("CA1", direction="inbound")
    await services.live.put("CA1", {"call_id": str(call_id)})
    assert await resolve_call_id(services, "CA1") == call_id


async def test_call_id_falls_back_to_database(services):
    call_id = await services.repo.get_or_create_call("CA2", direction="inbound")
    assert await services.live.get("CA2") is None
    assert await resolve_call_id(services, "CA2") == call_id


async def test_unknown_call(services):
    assert await resolve_call_id(services, "CA-unknown") is None


async def test_no_services():
    assert await resolve_call_id(None, "CA1") is None


def test_start_recording_requests_dual_channel(settings):
    captured = {}

    class Recordings:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(sid="RE1")

    client = SimpleNamespace(calls=lambda sid: SimpleNamespace(recordings=Recordings()))
    assert start_recording(settings, "CA1", client=client) == "RE1"
    assert captured["recording_channels"] == "dual"
    assert captured["recording_status_callback"] == "https://voice.example.test/twilio/recording"
