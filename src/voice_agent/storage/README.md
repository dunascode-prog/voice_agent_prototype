# storage

Persistence: Postgres repositories (calls, turns, tool invocations), S3 writer (recordings, transcripts), Redis live-call state.


**Phase 2:** `models.py` (calls, turns, transcript segments, call events, recordings, agent versions), `db.py` (async engine), `repository.py` (idempotent writes), `live_state.py` (Redis / in-memory `call:{CallSid}` state), `maintenance.py` (`voice-maintenance purge-raw`). S3 arrives with the worker in phase 5.
