"""Short-lived signed token that ties a Twilio Media Stream to the call the Gateway accepted.

The Gateway mints a token when it answers `/twilio/voice` and passes it to Twilio as a
`<Stream><Parameter name="token">`. Twilio echoes it back in the stream's `start` event,
and the Agent verifies it before running a pipeline. This stops anyone who discovers the
public `/media` URL from opening sessions (and spending STT/LLM/TTS money).

Format: ``<call_sid>.<expires_unix>.<base64url(hmac_sha256(key, "<call_sid>.<expires>"))>``
"""

import base64
import hashlib
import hmac
import time


class InvalidStreamToken(Exception):
    """The token is malformed, expired, forged, or for another call."""


def _sign(key: bytes, payload: str) -> str:
    digest = hmac.new(key, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def mint(call_sid: str, key: str, ttl_s: int, now: float | None = None) -> str:
    if not key:
        raise ValueError("STREAM_TOKEN_SIGNING_KEY is not set")
    if not call_sid or "." in call_sid:
        raise ValueError("invalid call_sid")
    expires = int((now if now is not None else time.time()) + ttl_s)
    payload = f"{call_sid}.{expires}"
    return f"{payload}.{_sign(key.encode(), payload)}"


def verify(token: str, call_sid: str, key: str, now: float | None = None) -> None:
    """Raise InvalidStreamToken unless the token is valid for `call_sid` right now."""
    if not key:
        raise InvalidStreamToken("signing key not configured")
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise InvalidStreamToken("malformed token")
    token_call_sid, expires_raw, signature = parts
    expected = _sign(key.encode(), f"{token_call_sid}.{expires_raw}")
    if not hmac.compare_digest(signature, expected):
        raise InvalidStreamToken("bad signature")
    if not hmac.compare_digest(token_call_sid, call_sid or ""):
        raise InvalidStreamToken("token issued for a different call")
    try:
        expires = int(expires_raw)
    except ValueError as e:
        raise InvalidStreamToken("malformed expiry") from e
    if (now if now is not None else time.time()) > expires:
        raise InvalidStreamToken("token expired")
