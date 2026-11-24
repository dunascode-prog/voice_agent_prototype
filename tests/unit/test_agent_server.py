import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_agent.pipeline import server
from voice_agent.telephony import stream_token

client = TestClient(server.app)


def start_event(call_sid: str = "CA123", token: str = "") -> str:
    return json.dumps(
        {
            "event": "start",
            "streamSid": "MZ1",
            "start": {
                "streamSid": "MZ1",
                "callSid": call_sid,
                "accountSid": "AC1",
                "customParameters": {"token": token, "direction": "inbound"},
            },
        }
    )


def expect_close(messages: list[str], code: int):
    with client.websocket_connect("/media") as ws:
        for m in messages:
            ws.send_text(m)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == code


def test_rejects_missing_token():
    expect_close([json.dumps({"event": "connected"}), start_event(token="")], 1008)


def test_rejects_token_for_other_call():
    token = stream_token.mint("CA999", "signing-key", ttl_s=60)
    expect_close([start_event(call_sid="CA123", token=token)], 1008)


def test_rejects_non_twilio_first_message():
    expect_close([json.dumps({"event": "media"})], 1008)


def test_valid_token_runs_pipeline(monkeypatch):
    calls = []

    async def fake_run_call(websocket, start, settings, services=None, call_id=None):
        calls.append(start)
        await websocket.close()

    import voice_agent.pipeline.bot as bot

    monkeypatch.setattr(bot, "run_call", fake_run_call)
    token = stream_token.mint("CA123", "signing-key", ttl_s=60)
    expect_close([json.dumps({"event": "connected"}), start_event(token=token)], 1000)
    assert len(calls) == 1
    assert calls[0].call_sid == "CA123"
    assert calls[0].stream_sid == "MZ1"


def test_missing_provider_keys_closes_with_error(settings):
    from pydantic import SecretStr

    settings.deepgram_api_key = SecretStr("")
    token = stream_token.mint("CA123", "signing-key", ttl_s=60)
    expect_close([start_event(token=token)], 1011)
