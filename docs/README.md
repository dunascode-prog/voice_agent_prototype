# Voice AI Agent Prototype

A real-time phone agent (inbound and outbound) built on **Python + Pipecat + Twilio Media Streams**, with **Deepgram** STT, **Claude** as the LLM, and **Cartesia** TTS (ElevenLabs as fallback), deployed as Docker containers on **AWS ECS Fargate**.

> **Status: design phase.** The repo contains architecture documents and an empty module skeleton. No implementation code yet. The run instructions below describe the **planned** developer workflow, which will work once phase 1 is implemented.

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
│   ├── docker/                 # Dockerfiles + docker-compose (local dev)
│   └── terraform/              # AWS: VPC, ECS, ALB, RDS, Redis, S3, SQS, Secrets
├── scripts/                    # ngrok bootstrap, Twilio number setup, seed data
├── .env.example                # Every environment variable (no real secrets)
└── README.md                   # Points to docs/README.md
```

Each module folder contains a placeholder `README.md` describing its future responsibility.

---

## Running locally (planned workflow)

### Prerequisites

* Docker Desktop 4.x (Compose v2)
* Python 3.12 + [uv](https://github.com/astral-sh/uv) (for running outside Docker and for tests)
* [ngrok](https://ngrok.com/) (or Cloudflare Tunnel) to expose local HTTPS/WSS endpoints to Twilio
* Accounts / API keys: Twilio (with one voice-capable number), Deepgram, Anthropic (or AWS Bedrock access), Cartesia (and optionally ElevenLabs), a Google Cloud project with the Calendar API enabled, a HubSpot private app token (sandbox portal)

### Steps

```bash
# 1. Configure environment
cp .env.example .env          # fill in your own keys; never commit .env

# 2. Start a public tunnel (one HTTPS URL is used for webhooks and the WSS media stream)
ngrok http 8080               # copy the https://xxxx.ngrok-free.app URL into PUBLIC_BASE_URL in .env

# 3. Start the stack: gateway, agent, worker, postgres, redis, localstack (S3 + SQS)
docker compose -f infra/docker/docker-compose.yml up --build

# 4. Apply database migrations and seed a test contact
docker compose -f infra/docker/docker-compose.yml exec gateway uv run alembic upgrade head
docker compose -f infra/docker/docker-compose.yml exec gateway uv run python scripts/seed.py

# 5. Point your Twilio number at the gateway
uv run python scripts/configure_twilio_number.py   # sets Voice URL = $PUBLIC_BASE_URL/twilio/voice

# 6. Inbound test: call your Twilio number from your phone

# 7. Outbound test
curl -X POST "$PUBLIC_BASE_URL/calls/outbound" \
  -H "Authorization: Bearer $INTERNAL_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"to": "+15555550123", "playbook": "booking"}'

# 8. Tests
uv run pytest tests/unit
uv run pytest tests/simulation -m "not live"     # loopback simulated calls, no phone charges
```

Locally, the reverse proxy in Compose routes `/twilio/*` and `/calls/*` to the gateway and `/media` to the agent, which mirrors the ALB rules in AWS.

---

## Environment variables

All variables are listed in [`../.env.example`](../.env.example). In AWS, secrets come from **Secrets Manager** (injected into ECS task definitions), not from files.

| Variable | Service(s) | Secret? | Description |
|---|---|---|---|
| `APP_ENV` | all | no | `local`, `staging` or `prod` |
| `LOG_LEVEL` | all | no | `INFO` by default |
| `PUBLIC_BASE_URL` | gateway | no | Public HTTPS base URL that Twilio calls (ngrok URL locally) |
| `MEDIA_WS_URL` | gateway | no | Public WSS URL of the agent's `/media` endpoint |
| `INTERNAL_API_TOKEN` | gateway, agent, dialer | **yes** | Bearer token for internal APIs (outbound calls, transfer) |
| `STREAM_TOKEN_SIGNING_KEY` | gateway, agent | **yes** | HMAC key for the signed Media Stream token |
| `TWILIO_ACCOUNT_SID` | gateway, worker | no | Twilio account SID |
| `TWILIO_AUTH_TOKEN` | gateway, worker | **yes** | Used for REST calls and webhook signature validation |
| `TWILIO_API_KEY_SID` / `TWILIO_API_KEY_SECRET` | gateway | **yes** | Preferred over the auth token for REST calls |
| `TWILIO_PHONE_NUMBER` | gateway, dialer | no | Default caller ID (E.164) |
| `TWILIO_REGION` / `TWILIO_EDGE` | gateway | no | `us1` / `ashburn` |
| `TRANSFER_DEFAULT_NUMBER` | gateway | no | Human queue / fallback number (E.164) |
| `AMD_ENABLED` | gateway | no | Enable async AMD on outbound calls |
| `RECORDING_ENABLED` | gateway | no | Enable dual-channel recording |
| `DEEPGRAM_API_KEY` | agent, worker | **yes** | STT |
| `DEEPGRAM_MODEL` / `DEEPGRAM_LANGUAGE` | agent | no | `nova-3` / `multi` |
| `LLM_PROVIDER` | agent, worker | no | `anthropic` or `bedrock` |
| `ANTHROPIC_API_KEY` | agent, worker | **yes** | Claude via the Anthropic API |
| `LLM_MODEL` | agent | no | `claude-opus-5-5` (`anthropic.claude-opus-5-5` on Bedrock) |
| `LLM_EFFORT` | agent | no | `low` for live turns |
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
| `MAX_CONCURRENT_CALLS_PER_TASK` | agent | no | Capacity limit, default 8 |
| `DEFAULT_TIMEZONE` | agent, dialer | no | Fallback IANA time zone |
| `CALLING_WINDOW_START` / `CALLING_WINDOW_END` | dialer | no | Local-time outbound window, e.g. `08:00` / `21:00` |
