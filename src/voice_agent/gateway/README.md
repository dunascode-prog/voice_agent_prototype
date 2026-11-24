# gateway

HTTP control plane: Twilio voice/status/AMD/recording webhooks, TwiML responses, outbound-call API, Twilio signature validation.

**Phase 1:** `app.py` serves `/healthz`, `/twilio/voice` (signed TwiML stream), `/twilio/status` and `/calls/outbound`. Database writes, recording, AMD and transfers come in later phases.
