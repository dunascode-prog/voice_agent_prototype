from twilio.rest import Client as TwilioClient

from voice_agent.config import Settings


def make_twilio_client(settings: Settings) -> TwilioClient:
    return TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value())
