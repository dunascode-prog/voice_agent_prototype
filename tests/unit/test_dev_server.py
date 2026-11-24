from voice_agent.dev import app


def test_dev_app_serves_gateway_and_agent_routes():
    paths = {getattr(r, "path", None) for r in app.router.routes}
    assert {"/healthz", "/twilio/voice", "/twilio/status", "/calls/outbound", "/media"} <= paths
