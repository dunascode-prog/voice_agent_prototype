"""Validation of the `X-Twilio-Signature` header on incoming webhooks.

Twilio signs the exact public URL it requested plus the POST form fields. Behind ngrok
or the AWS load balancer, the URL our server sees (`request.url`) has an internal host,
so the URL is rebuilt from `PUBLIC_BASE_URL` + path + query string.
"""

from fastapi import Depends, HTTPException, Request
from twilio.request_validator import RequestValidator

from voice_agent.config import Settings, get_settings


def public_url(request: Request, settings: Settings) -> str:
    url = settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    return url


async def twilio_form(
    request: Request, settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    """FastAPI dependency: returns the webhook's form fields after checking the signature."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not settings.twilio_validate_signatures:
        return form
    token = settings.twilio_auth_token.get_secret_value()
    if not token:
        raise HTTPException(status_code=500, detail="TWILIO_AUTH_TOKEN is not configured")
    signature = request.headers.get("X-Twilio-Signature", "")
    if not RequestValidator(token).validate(public_url(request, settings), form, signature):
        raise HTTPException(status_code=403, detail="invalid Twilio signature")
    return form
