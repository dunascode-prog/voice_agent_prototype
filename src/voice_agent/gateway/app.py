"""Call Gateway (control plane).

Phase 1 endpoints:
  GET  /healthz           liveness
  POST /twilio/voice      Twilio "a call came in / was answered" webhook -> TwiML that opens
                          a Media Stream to the Agent, carrying a signed token
  POST /twilio/status     Twilio call status callbacks (logged for now; persisted in phase 2)
  POST /calls/outbound    start an outbound call (internal bearer token)

Not yet: database writes, recording, AMD, transfers, consent/DNC checks (later phases).
"""

import hmac
import re
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from loguru import logger
from pydantic import BaseModel, field_validator
from twilio.rest import Client as TwilioClient

from voice_agent.config import Settings, get_settings
from voice_agent.observability.logging import configure_logging, mask_phone
from voice_agent.telephony import stream_token, twiml
from voice_agent.telephony.twilio_signature import twilio_form

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
UNAVAILABLE_MESSAGE = (
    "Sorry, our assistant is not available right now. Please call back in a few minutes."
)

app = FastAPI(title="voice-gateway", version="0.1.0")


def get_twilio_client(settings: Settings = Depends(get_settings)) -> TwilioClient:
    return TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value())


def require_internal_token(
    authorization: Annotated[str, Header()] = "",
    settings: Settings = Depends(get_settings),
) -> None:
    expected = settings.internal_api_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=503, detail="INTERNAL_API_TOKEN is not configured")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid token")


def xml(body: str) -> Response:
    return Response(content=body, media_type="application/xml")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/twilio/voice")
def twilio_voice(
    form: Annotated[dict[str, str], Depends(twilio_form)],
    settings: Settings = Depends(get_settings),
) -> Response:
    call_sid = form.get("CallSid", "")
    if not call_sid:
        raise HTTPException(status_code=400, detail="missing CallSid")
    direction = "outbound" if form.get("Direction", "").startswith("outbound") else "inbound"
    caller = form.get("From") if direction == "inbound" else form.get("To")

    missing = settings.missing("stream_token_signing_key", "media_ws_url")
    if missing:
        logger.error(f"call {call_sid}: cannot connect agent, missing config {missing}")
        return xml(twiml.unavailable(UNAVAILABLE_MESSAGE))

    token = stream_token.mint(
        call_sid, settings.stream_token_signing_key.get_secret_value(), settings.stream_token_ttl_s
    )
    logger.info(f"call {call_sid}: {direction} call with {mask_phone(caller)}, connecting agent")
    return xml(twiml.connect_stream(settings.media_ws_url, token, direction))


@app.post("/twilio/status", status_code=status.HTTP_204_NO_CONTENT)
def twilio_status(form: Annotated[dict[str, str], Depends(twilio_form)]) -> Response:
    logger.info(
        f"call {form.get('CallSid', '?')}: status={form.get('CallStatus', '?')} "
        f"duration_s={form.get('CallDuration', '-')}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class OutboundCallRequest(BaseModel):
    to: str

    @field_validator("to")
    @classmethod
    def _e164(cls, v: str) -> str:
        if not E164.match(v):
            raise ValueError("`to` must be an E.164 number like +15555550123")
        return v


class OutboundCallResponse(BaseModel):
    call_sid: str


@app.post(
    "/calls/outbound",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_internal_token)],
)
def create_outbound_call(
    body: OutboundCallRequest,
    settings: Settings = Depends(get_settings),
    client: TwilioClient = Depends(get_twilio_client),
) -> OutboundCallResponse:
    # Phase 1: protected by the internal token only. Consent, DNC and calling-hours
    # checks arrive with the campaign dialer (see docs/ARCHITECTURE.md section 8).
    missing = settings.missing("twilio_account_sid", "twilio_auth_token", "twilio_phone_number")
    if missing:
        raise HTTPException(status_code=503, detail=f"missing config: {', '.join(missing)}")
    base = settings.public_base_url.rstrip("/")
    call = client.calls.create(
        to=body.to,
        from_=settings.twilio_phone_number,
        url=f"{base}/twilio/voice",
        status_callback=f"{base}/twilio/status",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
    )
    logger.info(f"call {call.sid}: outbound call to {mask_phone(body.to)} created")
    return OutboundCallResponse(call_sid=call.sid)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=True)  # noqa: S104


if __name__ == "__main__":
    main()
