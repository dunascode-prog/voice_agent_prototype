# Voice AI Agent Prototype

A real-time phone agent (inbound and outbound) built on **Python + Pipecat + Twilio Media Streams**, with **Deepgram** STT, **Claude** as the LLM, and **Cartesia** TTS (ElevenLabs as fallback), deployed as Docker containers on **AWS ECS Fargate**.

> **Status: phase 2 + Bedrock.** A caller can phone the Twilio number and talk to the agent (Deepgram → Claude → Cartesia, with barge-in and smart-turn end-of-turn detection), and outbound calls can be started through the API. Every call is saved with its turns, redacted transcript, per-stage latency, token usage, status history and dual-channel recording. Claude runs either on the Claude API or on Amazon Bedrock (`LLM_PROVIDER`). Not yet built: tools (calendar/CRM), transfers, voicemail detection, the post-call worker and the dialer.

| Phase | Scope | Status |
|---|---|---|
| 1 | Gateway webhooks + signed media stream, Pipecat pipeline, outbound API, Docker, tests | done (PR #1) |
| 2 | Postgres + Redis, call/turn persistence, per-stage latency metrics, recording, CI | done (PR #2) |
| 2b | Claude on Amazon Bedrock (`LLM_PROVIDER=bedrock`), `voice-llm-check` | **this PR** |
| 3 | Tools: Google Calendar + HubSpot, booking flow with idempotency | |
| 4 | Transfers, voicemail detection, campaign dialer | |
| 5 | Worker: transcripts, redaction, LLM-judge scoring; simulated-caller evals | |
| 6 | AWS demo: Terraform, ECS Fargate, ALB (HTTPS/WSS), RDS, ElastiCache, Secrets Manager | next |

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
│   ├── services.py             # Per-process DB + Redis resources, best-effort persistence
│   ├── agent_version.py        # Fingerprint of prompt/model/voice/turn config per call
│   ├── llm_check.py            # voice-llm-check: one real request to the configured LLM provider
│   ├── gateway/                # FastAPI: Twilio webhooks, TwiML, outbound-call API
│   ├── telephony/              # Telephony adapter (Twilio): transfer, AMD, recording, DTMF
│   ├── pipeline/               # Pipecat pipeline assembly per call + barge-in wiring
│   ├── turn/                   # VAD, smart-turn, interruption + silence policy
│   ├── stt/                    # Deepgram config + STT failover
│   ├── llm/                    # Tool loop and failover (later); provider selection is in pipeline/llm.py
│   ├── tts/                    # Cartesia / ElevenLabs config, voices per language
│   ├── tools/                  # Tool schemas + executor (timeouts, idempotency)
│   │   ├── calendar/           # Google Calendar (then Microsoft 365 / Cal.com)
│   │   ├── crm/                # HubSpot (then Salesforce / Zoho) behind one interface
│   │   └── lookup/             # Customer, order, knowledge-base lookups
│   ├── prompts/                # Versioned playbook prompts
│   ├── storage/                # Models, repository, live state (Redis), retention CLI
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
├── alembic.ini, migrations/    # Database migrations (Alembic)
├── .github/workflows/ci.yml    # CI: lint, tests on SQLite and on Postgres + Redis, Docker build
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

#### Claude on Amazon Bedrock instead of the Claude API

The pipeline is the same either way; only the client changes. On Bedrock, requests are signed with your AWS credentials, so no `ANTHROPIC_API_KEY` is needed and no AWS keys go in `.env`.

1. In the AWS console, open **Amazon Bedrock → Model access** in your region (default `us-east-1`) and enable **Claude Opus 5.5**. Anthropic models ask for a short use-case form the first time.
2. Give your AWS identity permission to call it (`bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`; for a demo account, an admin user is fine).
3. Log in with the AWS CLI and note the profile name: `aws configure sso` (preferred) or `aws configure`. Check it with `aws sts get-caller-identity --profile <name>`.
4. In `.env`:
   ```bash
   LLM_PROVIDER=bedrock
   AWS_REGION=us-east-1
   AWS_PROFILE=<name>          # leave empty to use the default credential chain
   LLM_MODEL=claude-opus-5-5   # the anthropic. prefix is added for Bedrock
   ```
5. Check access before placing a call. This sends two short real requests (a few cents at most) shaped like the pipeline's, and prints time to first token and prompt-cache reads:
   ```bash
   uv run voice-llm-check
   # provider=bedrock in us-east-1 (profile demo) model=anthropic.claude-opus-5-5 effort=low
   # cold: ttft=...ms total=...ms in=... cache_read=0 cache_write=... out=... stop=end_turn
   # warm: ttft=...ms ... cache_read=...
   ```
   A `403` means model access or IAM permission is missing; `ProfileNotFound` or `NoCredentialsError` means the AWS CLI login is missing.

In the AWS demo (phase 6) the containers use the ECS task role instead of a profile.

### 3a. Run without Docker (single port)

```bash
uv run voice-dev             # Gateway + Agent routes on http://127.0.0.1:8080
```

With the defaults (`DATABASE_URL` pointing at SQLite, `REDIS_URL` empty), calls are saved to `voice_local.db` in the repo folder and live call state is kept in memory. The tables are created on start-up.

### 3b. Or run with Docker (separate services behind a proxy, like AWS)

```bash
docker compose -f infra/docker/docker-compose.yml up --build    # proxy on :8080
```

This also starts Postgres 16 and Redis 7 and runs `alembic upgrade head` before the services start.

### 4. Point your Twilio number at the gateway

In the Twilio console, under **Phone Numbers → your number → Voice configuration**, set:
* "A call comes in" → Webhook → `POST {PUBLIC_BASE_URL}/twilio/voice`
* "Call status changes" → `POST {PUBLIC_BASE_URL}/twilio/status`

Recording callbacks need no console setup: the agent registers `{PUBLIC_BASE_URL}/twilio/recording` when it starts each recording.

### 5. Call it

* **Inbound:** phone your Twilio number. The agent greets you, says it is an AI and that the call may be recorded, and runs the booking playbook (`src/voice_agent/prompts/booking.md`).
* **Outbound:**

  ```bash
  curl -X POST "$PUBLIC_BASE_URL/calls/outbound" \
    -H "Authorization: Bearer $INTERNAL_API_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"to": "+15555550123"}'
  ```

  There are no consent or DNC checks yet (phase 4), so only call your own phone.

### How a call flows

1. Twilio posts to `/twilio/voice`. The Gateway checks the `X-Twilio-Signature` against `PUBLIC_BASE_URL` + path, saves the `Call` (idempotent on `CallSid`), writes live state `call:{CallSid}` to Redis, and answers with TwiML `<Connect><Stream>` to `MEDIA_WS_URL`, carrying a signed token that expires after `STREAM_TOKEN_TTL_S`.
2. Twilio opens the WebSocket to `/media`. The Agent reads the `start` event and checks that the token is valid and matches this `CallSid`; otherwise it closes the socket (code 1008). It looks up the call id (Redis first, then the database) and starts a dual-channel recording through the Twilio REST API.
3. The Agent runs the Pipecat pipeline for the call: Twilio audio → Deepgram (Nova-3, multilingual) → Silero VAD + smart-turn v3 → Claude (`LLM_MODEL`, effort `LLM_EFFORT`, prompt caching on) → Cartesia → Twilio. When the caller speaks over the agent, LLM and TTS are cancelled and Twilio's audio buffer is cleared.
4. After every agent turn, a `Turn` row is saved with its latency breakdown (voice-to-voice, user-turn/end-of-turn time, STT, LLM time-to-first-token, sentence aggregation, TTS time-to-first-byte), its token usage and the redacted transcript. Writes go through a background queue, so the database never blocks the audio.
5. When the caller hangs up, the stream closes, the pipeline is cancelled, and per-call rollups are written: greeting latency, p50/p95 voice-to-voice, interruptions and tokens. Twilio then posts `/twilio/status` (final status, duration) and `/twilio/recording` (the recording file).

Database or Redis failures are logged and never stop a call.

### Looking at the data

```bash
# SQLite (no Docker)
sqlite3 voice_local.db "select seq, speaker, kind, v2v_ms, llm_ttft_ms, tts_ttfb_ms, interrupted from turns order by ended_at desc limit 20;"

# Postgres (Docker)
docker compose -f infra/docker/docker-compose.yml exec postgres psql -U voice -c \
  "select twilio_call_sid, status, duration_s, greeting_ms, v2v_p50_ms, v2v_p95_ms, agent_turns, interruption_count from calls order by started_at desc limit 10;"
```

Each agent turn's log line also shows its stage latencies, e.g. `turn 3 v2v=742ms stt=61 llm=284 tts=93 interrupted=False`.

### Database migrations and retention

```bash
uv run alembic upgrade head                                   # apply migrations (DATABASE_URL)
uv run alembic revision --autogenerate -m "describe change"   # after editing storage/models.py
uv run voice-maintenance purge-raw --days 30                  # remove unredacted transcript text past retention
```

Postgres schemas only change through Alembic. A test checks that the migrations build exactly the schema the models describe.

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
| `RECORDING_ENABLED` | agent | no | Start a dual-channel recording on every call, default `true` |
| `DEEPGRAM_API_KEY` | agent, worker | **yes** | STT |
| `DEEPGRAM_MODEL` / `DEEPGRAM_LANGUAGE` | agent | no | `nova-3` / `multi` |
| `LLM_PROVIDER` | agent, worker | no | `anthropic` (Claude API, default) or `bedrock` (Amazon Bedrock, AWS credentials) |
| `ANTHROPIC_API_KEY` | agent, worker | **yes** (if `anthropic`) | Claude API key; not needed on Bedrock |
| `LLM_MODEL` | agent | no | `claude-opus-5-5`; on Bedrock the `anthropic.` prefix is added automatically (ids that already contain it, e.g. `us.anthropic.…` inference profiles, are used as is) |
| `LLM_EFFORT` | agent | no | `low` for live turns |
| `LLM_MAX_TOKENS` | agent | no | Max tokens per reply, default 1024 |
| `LLM_FALLBACK_PROVIDER` | agent | no | Not used yet; automatic failover between providers comes later |
| `JUDGE_MODEL` | worker | no | Model used for post-call LLM-judge scoring |
| `AWS_REGION` | all | **yes** (if `bedrock`) | Bedrock region, default `us-east-1` |
| `AWS_PROFILE` | agent | no | AWS CLI profile for local runs on Bedrock; empty = default credential chain (env vars, SSO cache, ECS task role) |
| `CARTESIA_API_KEY` | agent | **yes** | Primary TTS |
| `CARTESIA_VOICE_ID_EN` / `CARTESIA_VOICE_ID_ES` | agent | no | Voices per language |
| `ELEVENLABS_API_KEY` | agent | **yes** | Fallback TTS |
| `ELEVENLABS_VOICE_ID` | agent | no | Fallback voice |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | agent (tools) | **yes** | Calendar access (or OAuth client below) |
| `GOOGLE_CALENDAR_ID` | agent (tools) | no | Calendar to book into |
| `HUBSPOT_PRIVATE_APP_TOKEN` | agent (tools), worker | **yes** | CRM |
| `SALESFORCE_*`, `ZOHO_*` | agent (tools), worker | **yes** | Placeholders for later adapters |
| `CRM_PROVIDER` | agent, worker | no | `hubspot`, `salesforce` or `zoho` |
| `DATABASE_URL` | gateway, agent, worker, dialer | **yes** (Postgres) | `postgresql://...` (the asyncpg driver is added automatically) or `sqlite+aiosqlite:///./voice_local.db` for local dev |
| `REDIS_URL` | gateway, agent, dialer | **yes** (if auth) | Redis DSN; empty = in-memory live state, only valid with `voice-dev` |
| `LIVE_STATE_TTL_S` | gateway | no | Lifetime of the `call:{CallSid}` live state, default 7200 |
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
