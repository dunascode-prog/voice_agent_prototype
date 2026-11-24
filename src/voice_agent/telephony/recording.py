"""Call recording through the Twilio REST API.

Started by the Agent once the media stream is live: at that point the call is certainly
in progress, which Twilio requires. Dual-channel keeps caller and agent on separate tracks
for QA and re-transcription. Twilio posts `/twilio/recording` when the file is ready.
"""

from voice_agent.config import Settings
from voice_agent.telephony.twilio_client import make_twilio_client


def start_recording(settings: Settings, call_sid: str, client=None) -> str:
    """Blocking (Twilio SDK is sync): call via asyncio.to_thread. Returns the RecordingSid."""
    client = client or make_twilio_client(settings)
    recording = client.calls(call_sid).recordings.create(
        recording_channels="dual",
        recording_status_callback=f"{settings.public_base_url.rstrip('/')}/twilio/recording",
        recording_status_callback_event=["completed", "absent"],
    )
    return recording.sid
