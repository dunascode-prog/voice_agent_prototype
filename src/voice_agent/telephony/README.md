# telephony

Telephony adapter interface + Twilio implementation: transfers (cold/warm), call updates, recording control, AMD result handling, DTMF. Plivo/Telnyx adapters would live here.

**Phase 1:** `twilio_signature.py` (webhook validation), `twiml.py` (TwiML builders), `stream_token.py` (signed media-stream token), `media_stream.py` (parses the stream's `start` event). Transfers, AMD and DTMF come later.
