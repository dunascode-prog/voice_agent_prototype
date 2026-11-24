"""Parsing of the first messages on a Twilio Media Stream WebSocket.

Twilio sends `{"event": "connected"}` and then `{"event": "start", "start": {...}}`, which
carries the stream/call ids and the `<Parameter>` values set in TwiML (our signed token).
"""

import json
from dataclasses import dataclass, field
from typing import Protocol


class _ReceivesText(Protocol):
    async def receive_text(self) -> str: ...


class StreamStartError(Exception):
    """The socket did not open like a Twilio Media Stream."""


@dataclass(frozen=True)
class StreamStart:
    stream_sid: str
    call_sid: str
    account_sid: str
    custom_parameters: dict[str, str] = field(default_factory=dict)


async def read_stream_start(ws: _ReceivesText, max_messages: int = 3) -> StreamStart:
    """Read until the `start` event. Callers should wrap this in a timeout."""
    for _ in range(max_messages):
        try:
            message = json.loads(await ws.receive_text())
        except json.JSONDecodeError as e:
            raise StreamStartError("non-JSON message before start") from e
        event = message.get("event")
        if event == "connected":
            continue
        if event != "start":
            raise StreamStartError(f"unexpected event before start: {event!r}")
        start = message.get("start") or {}
        stream_sid = start.get("streamSid") or message.get("streamSid")
        call_sid = start.get("callSid")
        if not stream_sid or not call_sid:
            raise StreamStartError("start event missing streamSid or callSid")
        return StreamStart(
            stream_sid=stream_sid,
            call_sid=call_sid,
            account_sid=start.get("accountSid", ""),
            custom_parameters={
                str(k): str(v) for k, v in (start.get("customParameters") or {}).items()
            },
        )
    raise StreamStartError("no start event received")
