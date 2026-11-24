"""Call Gateway (control plane).

Endpoints:
  GET  /healthz           liveness
  POST /twilio/voice      "a call came in / was answered" -> saves the Call, writes live state
                          to Redis, returns TwiML opening a Media Stream with a signed token
  POST /twilio/status     call status callbacks -> Call.status / ended_at / duration
  POST /twilio/recording  recording status callbacks -> Recording row
  POST /calls/outbound    start an outbound call (internal bearer token)

Persistence is best-effort: a database or Redis failure is logged and the call still connects.
Not yet: AMD, transfers, consent/DNC checks (phase 4).
"""

import hmac
import re
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from pydantic import BaseModel, field_validator
from twilio.rest import Client as TwilioClient

from voice_agent.config import Settings, get_settings
from voice_agent.observability.logging import configure_logging, mask_phone
from voice_agent.services import Services, best_effort, lifespan_for, services_from
from voice_agent.telephony import stream_token, twiml
from voice_agent.telephony.twilio_client import make_twilio_client
from voice_agent.telephony.twilio_signature import twilio_form

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
UNAVAILABLE_MESSAGE = (
    "Sorry, our assistant is not available right now. Please call back in a few minutes."
)

app = FastAPI(title="voice-gateway", version="0.2.0", lifespan=lifespan_for(get_settings))

TwilioForm = Annotated[dict[str, str], Depends(twilio_form)]
OptionalServices = Annotated[Services | None, Depends(services_from)]


def get_twilio_client(settings: Settings = Depends(get_settings)) -> TwilioClient:
    return make_twilio_client(settings)


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


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/twilio/voice")
async def twilio_voice(
    form: TwilioForm,
    services: OptionalServices,
    settings: Settings = Depends(get_settings),
) -> Response:
    call_sid = form.get("CallSid", "")
    if not call_sid:
        raise HTTPException(status_code=400, detail="missing CallSid")
    direction = "outbound" if form.get("Direction", "").startswith("outbound") else "inbound"
    caller = form.get("From") if direction == "inbound" else form.get("To")

    if services is not None:
        call_id = await best_effort(
            services.repo.get_or_create_call(
                call_sid,
                direction=direction,
                from_number=form.get("From"),
                to_number=form.get("To"),
                agent_version_id=services.agent_version_id,
                status=form.get("CallStatus", "ringing"),
            ),
            "create call",
        )
        if call_id is not None:
            await best_effort(
                services.live.put(
                    call_sid,
                    {
                        "call_id": str(call_id),
                        "direction": direction,
                        "agent_version_id": str(services.agent_version_id or ""),
                    },
                ),
                "write live state",
            )
            await best_effort(
                services.repo.add_event(call_id, "webhook.voice", {"direction": direction}),
                "webhook.voice event",
            )

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
async def twilio_status(form: TwilioForm, services: OptionalServices) -> Response:
    call_sid, call_status = form.get("CallSid", "?"), form.get("CallStatus", "?")
    duration = _int(form.get("CallDuration"))
    logger.info(f"call {call_sid}: status={call_status} duration_s={duration}")
    if services is not None:
        call_id = await best_effort(
            services.repo.update_status(call_sid, call_status, duration), "update status"
        )
        if call_id is not None:
            await best_effort(
                services.repo.add_event(
                    call_id, "webhook.status", {"status": call_status, "duration_s": duration}
                ),
                "webhook.status event",
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/twilio/recording", status_code=status.HTTP_204_NO_CONTENT)
async def twilio_recording(form: TwilioForm, services: OptionalServices) -> Response:
    call_sid, recording_sid = form.get("CallSid", ""), form.get("RecordingSid", "")
    rec_status = form.get("RecordingStatus", "unknown")
    logger.info(f"call {call_sid}: recording {recording_sid} status={rec_status}")
    if services is not None and call_sid and recording_sid:
        call_id = await best_effort(
            services.repo.upsert_recording(
                call_sid,
                recording_sid,
                status=rec_status,
                channels=_int(form.get("RecordingChannels")),
                duration_s=_int(form.get("RecordingDuration")),
                source_url=form.get("RecordingUrl"),
            ),
            "upsert recording",
        )
        if call_id is not None:
            await best_effort(
                services.repo.add_event(
                    call_id,
                    "webhook.recording",
                    {"recording_sid": recording_sid, "status": rec_status},
                ),
                "webhook.recording event",
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
async def create_outbound_call(
    body: OutboundCallRequest,
    services: OptionalServices,
    settings: Settings = Depends(get_settings),
    client: TwilioClient = Depends(get_twilio_client),
) -> OutboundCallResponse:
    # Protected by the internal token only. Consent, DNC and calling-hours checks
    # arrive with the campaign dialer (see docs/ARCHITECTURE.md section 8).
    missing = settings.missing("twilio_account_sid", "twilio_auth_token", "twilio_phone_number")
    if missing:
        raise HTTPException(status_code=503, detail=f"missing config: {', '.join(missing)}")
    base = settings.public_base_url.rstrip("/")
    call = await run_in_threadpool(
        client.calls.create,
        to=body.to,
        from_=settings.twilio_phone_number,
        url=f"{base}/twilio/voice",
        status_callback=f"{base}/twilio/status",
        status_callback_event=["initiated", "ringing", "answered", "completed"],
    )
    logger.info(f"call {call.sid}: outbound call to {mask_phone(body.to)} created")
    if services is not None:
        await best_effort(
            services.repo.get_or_create_call(
                call.sid,
                direction="outbound",
                from_number=settings.twilio_phone_number,
                to_number=body.to,
                agent_version_id=services.agent_version_id,
                status="queued",
            ),
            "create outbound call",
        )
    return OutboundCallResponse(call_sid=call.sid)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=True)  # noqa: S104


if __name__ == "__main__":
    main()
