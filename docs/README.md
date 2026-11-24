# Voice AI Agent Prototype

A real-time phone agent (inbound and outbound) built on **Python + Pipecat + Twilio Media Streams**, with **Deepgram** STT, **Claude** as the LLM, and **Cartesia** TTS (ElevenLabs as fallback), deployed as Docker containers on **AWS ECS Fargate**.

> **Status: phase 1 (scaffold).** A caller can phone the Twilio number and talk to the agent (Deepgram → Claude → Cartesia, with barge-in and smart-turn end-of-turn detection), and outbound calls can be started through the API. Not yet built: database, recording, tools (calendar/CRM), transfers, voicemail detection, the worker and the dialer.

| Phase | Scope | Status |
|---|---|---|
| 1 | Gateway webhooks + signed media stream, Pipecat pipeline, outbound API, Docker, tests | **this PR** |
| 2 | Postgres + Redis, call/turn persistence, per-stage latency metrics, recording | next |
| 3 | Tools: Google Calendar + HubSpot, booking flow with idempotency | |
| 4 | Transfers, voicemail detection, campaign dialer | |
| 5 | Worker: transcripts, redaction, LLM-judge scoring; simulated-caller evals | |
| 6 | AWS: Terraform, ECS, CI/CD | |

| Document | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System context, components, deployment on AWS, design decisions, latency budget, assumptions, prototype decisions |
| [DATAFLOW.md](DATAFLOW.md) | Sequence diagrams (inbound, outbound, tool call, transfer, voicemail), audio path with barge-in, data model, retention and PII |
| [EVALUATION.md](EVALUATION.md) | Per-call metrics, LLM-judge rubric, simulated-caller tests, observability, transcript storage |
| `Voice_Agent_Architecture.pdf` | All of the above rendered with diagrams, for sharing |

**Regenerating the PDF** after editing any doc (needs Chrome or Edge and network access for mermaid.js):

```bash
pip install markdown
python scripts/docs/build_pdf.py     # fails if any Mermaid diagram does not parse
```

## Workflow

* `main` only changes through pull requests, and the repository owner reviews and merges every PR.
* Work happens on feature branches (`claude/<topic>` for Claude's work), one focused PR per step.
* Every PR keeps this README up to date with what was added: layout, run steps, environment variables.

---

## Repository layout

```text
voice-agent-prototype/
├── docs/                       # Architecture, dataflow, evaluation docs (this folder)
├── src/voice_agent/
│   ├── dev.py                  # Single-port local server (Gateway + Agent on :8080)
│   ├── gateway/                # FastAPI: Twilio webhooks, TwiML, outbound-call API
│   ├── telephony/              # Telephony adapter (Twilio): transfer, AMD, recording, DTMF
│   ├── pipeline/               # Pipecat pipeline assembly per call + barge-in wiring
│   ├── turn/                   # VAD, smart-turn, interruption + silence policy
│   ├── stt/                    # Deepgram config + STT failover
│   ├── llm/                    # Claude adapter (Anthropic API / Bedrock), caching, tool loop
│   ├── tts/                    # Cartesia / ElevenLabs config, voices per language
│   ├── tools/                  # Tool schemas + executor (timeouts, idempotency)
│   │   ├── calendar/           # Google Calendar (then Microsoft 365 / Cal.com)
│   │   ├── crm/                # HubSpot (then Salesforce / Zoho) behind one interface
│   │   └── lookup/             # Customer, order, knowledge-base lookups
│   ├── prompts/                # Versioned playbook prompts
│   ├── storage/                # Postgres, S3 and Redis access
│   ├── observability/          # OpenTelemetry, metrics, PII-safe logging
│   ├── worker/                 # Post-call jobs: recordings, transcripts, scoring, CRM sync
│   ├── campaigns/              # Outbound dialer: windows, DNC, pacing, retries
│   └── config/                 # Typed settings + feature flags
├── tests/
│   ├── unit/
│   ├── integration/            # Against provider sandboxes
│   └── simulation/             # LLM-driven simulated callers
├── evals/
│   ├── scenarios/              # Caller personas + expected outcomes
│   └── rubrics/                # LLM-judge rubrics, human QA checklists
├── infra/
│   ├── docker/                 # Dockerfile, docker-compose, Caddy proxy (local dev)
│   └── terraform/              # AWS: VPC, ECS, ALB, RDS, Redis, S3, SQS, Secrets
├── scripts/                    # ngrok bootstrap, Twilio number setup, seed data
├── pyproject.toml              # Dependencies (uv), ruff and pytest config
├── uv.lock                     # Locked dependency versions
├── .env.example                # Every environment variable (no real secrets)
└── README.md                   # Points to docs/README.md
```

Each module folder has a `README.md` describing its responsibility; folders not yet implemented say so.

---

## Running locally

### Prerequisites

* Python 3.12 and [uv](https://github.com/astral-sh/uv)
* [ngrok](https://ngrok.com/) (or Cloudflare Tunnel) so Twilio can reach your machine
* API keys: Twilio (with one voice-capable number), Deepgram, Anthropic, Cartesia (plus a Cartesia voice id)
* Optional: Docker Desktop, to run the services the way they run in AWS

### 1. Install and test

```bash
uv sync                      # creates .venv with runtime + dev dependencies
uv run pytest                # unit tests, no network or keys needed
uv run ruff check src tests
```

### 2. Configure

```bash
cp .env.example .env         # fill in the "Phase 1 needs" values listed at the top
ngrok http 8080              # copy the https URL into PUBLIC_BASE_URL and wss://<same-host>/media into MEDIA_WS_URL
```

Generate random values for `INTERNAL_API_TOKEN` and `STREAM_TOKEN_SIGNING_KEY`, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

### 3a. Run without Docker (single port)

```bash
uv run voice-dev             # Gateway + Agent routes on http://127.0.0.1:8080
```

### 3b. Or run with Docker (separate services behind a proxy, like AWS)

```bash
docker compose -f infra/docker/docker-compose.yml up --build    # proxy on :8080
```

### 4. Point your Twilio number at the gateway

In the Twilio console, under **Phone Numbers → your number → Voice configuration**, set:
* "A call comes in" → Webhook → `POST {PUBLIC_BASE_URL}/twilio/voice`
* "Call status changes" → `POST {PUBLIC_BASE_URL}/twilio/status`

### 5. Call it

* **Inbound:** phone your Twilio number. The agent greets you, says it is an AI and that the call may be recorded, and runs the booking playbook (`src/voice_agent/prompts/booking.md`).
* **Outbound:**

  ```bash
  curl -X POST "$PUBLIC_BASE_URL/calls/outbound"     -H "Authorization: Bearer $INTERNAL_API_TOKEN"     -H "Content-Type: application/json"     -d '{"to": "+15555550123"}'
  ```

  Phase 1 has no consent or DNC checks yet, so only call your own phone.

### How a call flows in phase 1

1. Twilio posts to `/twilio/voice`. The Gateway checks the `X-Twilio-Signature` against `PUBLIC_BASE_URL` + path, then answers with TwiML `<Connect><Stream>` to `MEDIA_WS_URL`, carrying a signed token that expires after `STREAM_TOKEN_TTL_S`.
2. Twilio opens the WebSocket to `/media`. The Agent reads the `start` event and checks that the token is valid and matches this `CallSid`; otherwise it closes the socket (code 1008).
3. The Agent runs the Pipecat pipeline for the call: Twilio audio → Deepgram (Nova-3, multilingual) → Silero VAD + smart-turn v3 → Claude (`LLM_MODEL`, effort `LLM_EFFORT`, prompt caching on) → Cartesia → Twilio. When the caller speaks over the agent, LLM and TTS are cancelled and Twilio's audio buffer is cleared.
4. When the caller hangs up, the stream closes and the pipeline is cancelled.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Twilio plays "an application error has occurred" | Gateway returned 403 (signature check failed): `PUBLIC_BASE_URL` must exactly match the URL configured in Twilio, scheme included |
| The agent says it is unavailable, then hangs up | `STREAM_TOKEN_SIGNING_KEY` or `MEDIA_WS_URL` is missing in `.env` |
| The call connects but stays silent | Check the agent log for `missing config` (provider keys) or a Deepgram/Anthropic/Cartesia auth error |

## Environment variables

All variables are listed in [`../.env.example`](../.env.example). In AWS, secrets come from **Secrets Manager** (injected into ECS task definitions), not from files.

| Variable | Service(s) | Secret? | Description |
|---|---|---|---|
| `APP_ENV` | all | no | `local`, `staging` or `prod` |
| `LOG_LEVEL` | all | no | `INFO` by default |
| `PUBLIC_BASE_URL` | gateway | no | Public HTTPS base URL that Twilio calls (ngrok URL locally) |
| `MEDIA_WS_URL` | gateway | no | Public WSS URL of the agent's `/media` endpoint |
| `INTERNAL_API_TOKEN` | gateway, agent, dialer | **yes** | Bearer token for internal APIs (outbound calls, transfer) |
| `STREAM_TOKEN_TTL_S` | gateway | no | Lifetime of the signed media-stream token, default 120 |
| `STREAM_TOKEN_SIGNING_KEY` | gateway, agent | **yes** | HMAC key for the signed Media Stream token |
| `TWILIO_ACCOUNT_SID` | gateway, worker | no | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | gateway, worker | **yes** | Used for REST calls and webhook signature validation |
| `TWILIO_API_KEY_SID` / `TWILIO_API_KEY_SECRET` | gateway | **yes** | Preferred over the auth token for REST calls |
| `TWILIO_PHONE_NUMBER` | gateway, dialer | no | Default caller ID (E.164) |
| `TWILIO_REGION` / `TWILIO_EDGE` | gateway | no | `us1` / `ashburn` |
| `TWILIO_VALIDATE_SIGNATURES` | gateway | no | Webhook signature check, default `true`; never disable outside tests |
| `TRANSFER_DEFAULT_NUMBER` | gateway | no | Human queue / fallback number (E.164) |
| `AMD_ENABLED` | gateway | no | Enable async AMD on outbound calls |
| `RECORDING_ENABLED` | gateway | no | Enable dual-channel recording |
| `DEEPGRAM_API_KEY` | agent, worker | **yes** | STT |
| `DEEPGRAM_MODEL` / `DEEPGRAM_LANGUAGE` | agent | no | `nova-3` / `multi` |
| `LLM_PROVIDER` | agent, worker | no | `anthropic` or `bedrock` |
| `ANTHROPIC_API_KEY` | agent, worker | **yes** | Claude via the Anthropic API |
| `LLM_MODEL` | agent | no | `claude-opus-5-5` (`anthropic.claude-opus-5-5` on Bedrock) |
| `LLM_EFFORT` | agent | no | `low` for live turns |
| `LLM_MAX_TOKENS` | agent | no | Max tokens per reply, default 1024 |
| `LLM_FALLBACK_PROVIDER` | agent | no | `bedrock` |
| `JUDGE_MODEL` | worker | no | Model used for post-call LLM-judge scoring |
| `AWS_REGION` | all | no | `us-east-1` |
| `CARTESIA_API_KEY` | agent | **yes** | Primary TTS |
| `CARTESIA_VOICE_ID_EN` / `CARTESIA_VOICE_ID_ES` | agent | no | Voices per language |
| `ELEVENLABS_API_KEY` | agent | **yes** | Fallback TTS |
| `ELEVENLABS_VOICE_ID` | agent | no | Fallback voice |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | agent (tools) | **yes** | Calendar access (or OAuth client below) |
| `GOOGLE_CALENDAR_ID` | agent (tools) | no | Calendar to book into |
| `HUBSPOT_PRIVATE_APP_TOKEN` | agent (tools), worker | **yes** | CRM |
| `SALESFORCE_*`, `ZOHO_*` | agent (tools), worker | **yes** | Placeholders for later adapters |
| `CRM_PROVIDER` | agent, worker | no | `hubspot`, `salesforce` or `zoho` |
| `DATABASE_URL` | gateway, agent, worker, dialer | **yes** | Postgres DSN |
| `REDIS_URL` | gateway, agent, dialer | **yes** (if auth) | Redis DSN |
| `S3_BUCKET_RECORDINGS` / `S3_BUCKET_TRANSCRIPTS` | worker | no | Bucket names |
| `SQS_QUEUE_URL_CALL_EVENTS` | gateway, agent, worker | no | Post-call queue |
| `AWS_ENDPOINT_URL` | all | no | LocalStack URL for local dev only |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | all | no | OTel Collector endpoint |
| `OTEL_SERVICE_NAME` | all | no | Set per container |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | agent, worker | **yes** | LLM tracing |
| `MAX_CALL_DURATION_S` | agent | no | Hard stop, default 1800 |
| `PLAYBOOK` | agent | no | Prompt file in `src/voice_agent/prompts/`, default `booking` |
| `VAD_STOP_SECS` | agent | no | Silence before end-of-turn check, default 0.2 |
| `MAX_CONCURRENT_CALLS_PER_TASK` | agent | no | Capacity limit, default 8 |
| `DEFAULT_TIMEZONE` | agent, dialer | no | Fallback IANA time zone |
| `CALLING_WINDOW_START` / `CALLING_WINDOW_END` | dialer | no | Local-time outbound window, e.g. `08:00` / `21:00` |
