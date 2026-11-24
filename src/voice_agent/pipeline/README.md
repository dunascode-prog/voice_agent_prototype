# pipeline

Pipecat pipeline assembly per call: transport -> VAD -> STT -> context aggregator -> LLM -> TTS -> transport, plus barge-in/interruption wiring.

**Phase 1:** `server.py` accepts `/media` WebSockets and verifies the stream token; `bot.py` builds and runs the per-call Pipecat pipeline.

**Phase 2:** `call_recorder.py` turns Pipecat latency breakdowns, turn transcripts and token usage into `Turn` rows plus per-call rollups, written through a background queue.
