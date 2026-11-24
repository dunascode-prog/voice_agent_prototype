"""TwiML builders used by the Gateway."""

from twilio.twiml.voice_response import Connect, VoiceResponse


def connect_stream(media_ws_url: str, token: str, direction: str) -> str:
    """Answer the call by opening a bidirectional Media Stream to the Agent."""
    response = VoiceResponse()
    connect = Connect()
    stream = connect.stream(url=media_ws_url)
    stream.parameter(name="token", value=token)
    stream.parameter(name="direction", value=direction)
    response.append(connect)
    return str(response)


def unavailable(message: str) -> str:
    """Fallback when the agent can't take the call: apologise and hang up."""
    response = VoiceResponse()
    response.say(message)
    response.hangup()
    return str(response)
