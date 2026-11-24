"""Local single-port server: Gateway + Agent routes on :8080, for running without Docker.

One ngrok tunnel (`ngrok http 8080`) then serves both the Twilio webhooks and `/media`.
In Docker and AWS the two services run separately behind a proxy / load balancer.
"""

import uvicorn
from fastapi import FastAPI

from voice_agent.config import get_settings
from voice_agent.gateway.app import app as gateway_app
from voice_agent.observability.logging import configure_logging
from voice_agent.pipeline.server import app as agent_app

app = FastAPI(title="voice-dev", version="0.1.0")
_seen: set[str] = set()
for route in [*gateway_app.router.routes, *agent_app.router.routes]:
    path = getattr(route, "path", None)
    if path in _seen:  # both apps define /healthz
        continue
    _seen.add(path)
    app.router.routes.append(route)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(app, host="127.0.0.1", port=8080, proxy_headers=True)


if __name__ == "__main__":
    main()
