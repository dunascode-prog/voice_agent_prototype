# observability

OpenTelemetry setup, per-stage latency metrics, structured logging with PII redaction, LLM trace export.

**Phase 1:** `logging.py` (loguru setup, phone-number masking). OpenTelemetry and latency metrics come in phase 2.

**Phase 2:** `redact.py`, regex redaction of emails, SSNs, card and phone/account numbers in stored transcripts.
