"""Voice Agent service: accepts Twilio Media Streams on `/media` and runs one pipeline per call."""

import asyncio

import uvicorn
from fastapi import Depends, FastAPI, WebSocket, status
from loguru import logger

from voice_agent.config import Settings, get_settings
from voice_agent.observability.logging import configure_logging
from voice_agent.telephony import stream_token
from voice_agent.telephony.media_stream import StreamStartError, read_stream_start

START_TIMEOUT_S = 10
REQUIRED_FOR_CALLS = (
    "stream_token_signing_key",
    "deepgram_api_key",
    "anthropic_api_key",
    "cartesia_api_key",
    "cartesia_voice_id_en",
)

app = FastAPI(title="voice-agent", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/media")
async def media(websocket: WebSocket, settings: Settings = Depends(get_settings)) -> None:
    await websocket.accept()
    try:
        start = await asyncio.wait_for(read_stream_start(websocket), timeout=START_TIMEOUT_S)
    except (StreamStartError, TimeoutError) as e:
        logger.warning(f"rejecting media stream: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        stream_token.verify(
            start.custom_parameters.get("token", ""),
            start.call_sid,
            settings.stream_token_signing_key.get_secret_value(),
        )
    except stream_token.InvalidStreamToken as e:
        logger.warning(f"call {start.call_sid}: rejecting media stream, {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    missing = settings.missing(*REQUIRED_FOR_CALLS)
    if missing:
        logger.error(f"call {start.call_sid}: cannot start pipeline, missing config {missing}")
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        return

    # Imported here so the server starts fast and tests of the auth path don't load models.
    from voice_agent.pipeline.bot import run_call

    await run_call(websocket, start, settings)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(app, host="0.0.0.0", port=8001, proxy_headers=True)  # noqa: S104


if __name__ == "__main__":
    main()
