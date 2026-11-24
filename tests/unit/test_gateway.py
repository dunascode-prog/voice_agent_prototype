from types import SimpleNamespace
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from twilio.request_validator import RequestValidator

from tests.conftest import AUTH_TOKEN, PUBLIC_BASE_URL
from voice_agent.gateway.app import app, get_twilio_client
from voice_agent.telephony import stream_token

client = TestClient(app)


def signed_post(path: str, form: dict[str, str], token: str = AUTH_TOKEN):
    signature = RequestValidator(token).compute_signature(PUBLIC_BASE_URL + path, form)
    return client.post(path, data=form, headers={"X-Twilio-Signature": signature})


INBOUND = {"CallSid": "CA123", "From": "+15555550123", "To": "+15555550100", "Direction": "inbound"}


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_voice_returns_stream_twiml_with_valid_token(settings):
    resp = signed_post("/twilio/voice", INBOUND)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")

    root = ElementTree.fromstring(resp.text)
    stream = root.find("./Connect/Stream")
    assert stream is not None
    assert stream.get("url") == settings.media_ws_url
    params = {p.get("name"): p.get("value") for p in stream.findall("Parameter")}
    assert params["direction"] == "inbound"
    stream_token.verify(params["token"], "CA123", "signing-key")


def test_voice_outbound_direction():
    resp = signed_post("/twilio/voice", {**INBOUND, "Direction": "outbound-api"})
    assert 'name="direction" value="outbound"' in resp.text


def test_voice_rejects_bad_signature():
    resp = signed_post("/twilio/voice", INBOUND, token="wrong-token")
    assert resp.status_code == 403


def test_voice_rejects_missing_signature():
    assert client.post("/twilio/voice", data=INBOUND).status_code == 403


def test_voice_missing_call_sid():
    assert signed_post("/twilio/voice", {"From": "+15555550123"}).status_code == 400


def test_voice_falls_back_when_misconfigured(settings):
    settings.stream_token_signing_key = SecretStr("")
    resp = signed_post("/twilio/voice", INBOUND)
    assert resp.status_code == 200
    assert "<Say>" in resp.text and "<Hangup" in resp.text and "<Stream" not in resp.text


def test_status_callback():
    resp = signed_post("/twilio/status", {"CallSid": "CA123", "CallStatus": "completed"})
    assert resp.status_code == 204


class FakeCalls:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(sid="CA777")


@pytest.fixture
def fake_twilio():
    fake = SimpleNamespace(calls=FakeCalls())
    app.dependency_overrides[get_twilio_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_twilio_client, None)


def test_outbound_requires_token(fake_twilio):
    resp = client.post("/calls/outbound", json={"to": "+15555550123"})
    assert resp.status_code == 401
    resp = client.post(
        "/calls/outbound", json={"to": "+15555550123"}, headers={"Authorization": "Bearer nope"}
    )
    assert resp.status_code == 401
    assert fake_twilio.calls.kwargs is None


def test_outbound_creates_call(fake_twilio):
    resp = client.post(
        "/calls/outbound",
        json={"to": "+15555550123"},
        headers={"Authorization": "Bearer internal-secret"},
    )
    assert resp.status_code == 202
    assert resp.json() == {"call_sid": "CA777"}
    kwargs = fake_twilio.calls.kwargs
    assert kwargs["to"] == "+15555550123"
    assert kwargs["from_"] == "+15555550100"
    assert kwargs["url"] == f"{PUBLIC_BASE_URL}/twilio/voice"
    assert kwargs["status_callback"] == f"{PUBLIC_BASE_URL}/twilio/status"


@pytest.mark.parametrize("number", ["5555550123", "+0123", "+1 555 555 0123", "abc"])
def test_outbound_rejects_non_e164(fake_twilio, number):
    resp = client.post(
        "/calls/outbound", json={"to": number}, headers={"Authorization": "Bearer internal-secret"}
    )
    assert resp.status_code == 422
