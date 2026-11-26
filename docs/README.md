# Voice AI Agent Prototype

A real-time phone agent (inbound and outbound) built on **Python + Pipecat + Twilio Media Streams**, with **Deepgram** STT, **Claude** as the LLM, and **Cartesia** TTS (ElevenLabs as fallback), deployed as Docker containers on **AWS ECS Fargate**.

> **Status: phase 2 + Bedrock + AWS demo.** A caller can phone the Twilio number and talk to the agent (Deepgram → Claude → Cartesia, with barge-in and smart-turn end-of-turn detection), and outbound calls can be started through the API. Every call is saved with its turns, redacted transcript, per-stage latency, token usage, status history and dual-channel recording. Claude runs either on the Claude API or on Amazon Bedrock (`LLM_PROVIDER`). The stack deploys to AWS with Terraform behind CloudFront, either on one low-cost EC2 server or on ECS Fargate (see [Deploying the AWS demo](#deploying-the-aws-demo)). Not yet built: tools (calendar/CRM), transfers, voicemail detection, the post-call worker and the dialer.

| Phase | Scope | Status |
|---|---|---|
| 1 | Gateway webhooks + signed media stream, Pipecat pipeline, outbound API, Docker, tests | done (PR #1) |
| 2 | Postgres + Redis, call/turn persistence, per-stage latency metrics, recording, CI | done (PR #2) |
| 2b | Claude on Amazon Bedrock (`LLM_PROVIDER=bedrock`), `voice-llm-check` | done (PR #3, #4) |
| 3 | Tools: Google Calendar + HubSpot, booking flow with idempotency | next |
| 4 | Transfers, voicemail detection, campaign dialer | |
| 5 | Worker: transcripts, redaction, LLM-judge scoring; simulated-caller evals | |
| 6 | AWS demo: Terraform, ECS Fargate, CloudFront + ALB (HTTPS/WSS), RDS, ElastiCache, Secrets Manager, CodeBuild | done (PR #5) |
| 6b | Low-cost AWS demo: one EC2 server running docker compose behind CloudFront | **this PR** |

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
│   ├── docker/                 # Dockerfile, docker-compose, Caddy proxy (local dev; *.aws variants for the EC2 demo)
│   ├── iam/                    # Bedrock policy for a developer's own AWS identity
│   └── terraform/
│       ├── ec2/                # Low-cost AWS demo: one server + CloudFront (default)
│       └── ecs/                # Production-shaped demo: Fargate, ALB, RDS, Redis, CodeBuild
├── scripts/
│   ├── aws/                    # push_secrets.py, deploy_ec2.py, deploy_ecs.py (AWS demo)
│   └── docs/                   # build_pdf.py
├── pyproject.toml              # Dependencies (uv), ruff and pytest config
├── uv.lock                     # Locked dependency versions
├── alembic.ini, migrations/    # Database migrations (Alembic)
├── .github/workflows/ci.yml    # CI: lint, tests on SQLite and on Postgres + Redis, Docker build, Terraform validate
├── .env.example                # Every environment variable (no real secrets)
└── README.md                   # Points to docs/README.md
```

Each module folder has a `README.md` describing its responsibility; folders not yet implemented say so.

---

## Running locally

### Prerequisites

* Python 3.12 and [uv](https://github.com/astral-sh/uv)
* [ngrok](https://ngrok.com/) (or Cloudflare Tunnel) so Twilio can reach your machine. Not needed for the AWS demo, which has its own public address
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
2. Give your AWS identity permission to call it. The agent uses Bedrock Runtime's **InvokeModel** API (`AsyncAnthropicBedrock`), so it needs `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` on the inference profile and on the Anthropic foundation models it routes to. Attach [`infra/iam/bedrock-claude-policy.json`](../infra/iam/bedrock-claude-policy.json) (replace `ACCOUNT_ID`) to the IAM user or role, or as an inline policy on the IAM Identity Center permission set if you sign in with SSO. An admin role also works.
3. Log in with the AWS CLI and note the profile name: `aws configure sso` (preferred) or `aws configure`. Check it with `aws sts get-caller-identity --profile <name>`.
4. In `.env`:
   ```bash
   LLM_PROVIDER=bedrock
   AWS_REGION=us-east-1
   AWS_PROFILE=<name>          # leave empty to use the default credential chain
   LLM_MODEL=claude-opus-5-5   # becomes us.anthropic.claude-opus-5-5
   BEDROCK_INFERENCE_PROFILE=us   # or global; empty = bare in-region id
   ```
   In `us-east-1`, InvokeModel serves current Claude models only through a cross-region inference profile, which is why the default adds `us.`. Bedrock model ids: Opus 5.5 = `claude-opus-5-5`, Opus 4.6 = `claude-opus-4-6-v1` (so `LLM_MODEL=claude-opus-4-6-v1` → `us.anthropic.claude-opus-4-6-v1`). A full id containing `anthropic.` is used unchanged.
   Models older than Claude 4.7 (e.g. Opus 4.6) work on Bedrock only with API version `2023-06-01`, which is what the pinned `anthropic` 1.x SDK sends. Keep the `anthropic[bedrock]<2` pin while using one: the 2.x SDK sends `2026-10-21`, which Bedrock accepts only for 4.7 and later.
5. Check access before placing a call. This sends two short real requests (a few cents at most) shaped like the pipeline's, and prints time to first token and prompt-cache reads:
   ```bash
   uv run voice-llm-check
   # provider=bedrock in us-east-1 (profile demo) model=us.anthropic.claude-opus-5-5 effort=low
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

## Deploying the AWS demo

Twilio reaches the AWS demo through **CloudFront**, which provides a valid HTTPS and WSS address (`https://dxxxx.cloudfront.net`) with no domain or certificate to manage, so **ngrok is only needed for local runs**. There are two Terraform stacks. They create resources with the same names, so apply **one** of them:

| | [`infra/terraform/ec2`](../infra/terraform/ec2/) (**default, low cost**) | [`infra/terraform/ecs`](../infra/terraform/ecs/) (production-shaped) |
|---|---|---|
| Runs on | One EC2 `t3.small` running the local `docker compose` stack (Caddy, gateway, agent, Postgres, Redis) | ECS Fargate gateway + agent behind an ALB, RDS Postgres, ElastiCache Redis |
| Cost while running | **about $21 a month** (instance $15, public IP $3.65, disk $1.60, secrets $0.80); about $6 while the instance is stopped | about $100–110 a month |
| Deploys | `scripts/aws/deploy_ec2.py`: the server pulls the commit and rebuilds (SSM, no SSH) | `scripts/aws/deploy_ecs.py`: CodeBuild builds the image, ECS rolls it out |
| Use when | Demos and testing (decision D15) | A pilot that needs managed databases and rolling deploys (D14) |

### Low-cost demo on one server (`infra/terraform/ec2`)

```text
Twilio ──HTTPS webhooks / WSS media──> CloudFront ──HTTP + X-Origin-Verify──> EC2 :80
   EC2 (Elastic IP, port 80 open to CloudFront only, no SSH):
     Caddy ── /media ──> agent ──> Deepgram, Bedrock (Claude), Cartesia
           └─ /*     ──> gateway
     Postgres + Redis containers (data on the instance's disk)
   secrets: Secrets Manager -> .env on the server, rewritten on every deploy
```

You need Terraform 1.9+, the AWS CLI, an **admin** AWS CLI profile (the Bedrock-only role can't create infrastructure) and Bedrock model access for `llm_model` in `us-east-1` (default `claude-sonnet-4-6`).

1. **Log in:** `aws sso login --profile AdministratorAccess-<account-id>`
2. **Create the infrastructure** (about 5–10 minutes, mostly CloudFront):
   ```bash
   cd infra/terraform/ec2
   cp terraform.tfvars.example terraform.tfvars   # set aws_profile
   terraform init
   terraform apply
   ```
   On first boot the server installs Docker and clones the repo. The app starts in step 4.
3. **Store the provider keys.** Fill the Twilio, Deepgram and Cartesia values in your local `.env`, then copy them into Secrets Manager (key names are printed, values never are):
   ```bash
   uv run python scripts/aws/push_secrets.py --profile AdministratorAccess-<account-id>
   ```
   `INTERNAL_API_TOKEN`, `STREAM_TOKEN_SIGNING_KEY`, the CloudFront origin header and the non-secret settings (`PUBLIC_BASE_URL`, `LLM_MODEL`, …) are written by Terraform into the `voice-agent/internal` secret.
4. **Deploy.** The server checks out your current commit from GitHub (push it first), writes its `.env` from both secrets and rebuilds the stack. The first run takes about 5 minutes, later ones less:
   ```bash
   uv run python scripts/aws/deploy_ec2.py --profile AdministratorAccess-<account-id>
   ```
   Re-run it after every code change and after `push_secrets.py`. `--ref <branch>` deploys another branch.
5. **Point Twilio at it.** `terraform output` prints `twilio_voice_webhook` and `twilio_status_webhook`. Set them on the number (both **HTTP POST**) as in step 4 of the local setup. Check the stack is up with `curl https://dxxxx.cloudfront.net/healthz`.
6. **Call the number.** For logs, open a shell on the server and follow the compose logs:
   ```bash
   aws ssm start-session --target "$(terraform output -raw instance_id)" --profile AdministratorAccess-<account-id>
   sudo docker compose -f /opt/voice-agent/infra/docker/docker-compose.yml logs -f agent gateway
   ```
   (`start-session` needs the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) for the AWS CLI.)
7. **Outbound calls** use the generated token:
   ```bash
   TOKEN=$(aws secretsmanager get-secret-value --secret-id voice-agent/internal \
     --query SecretString --output text --profile AdministratorAccess-<account-id> | jq -r .INTERNAL_API_TOKEN)
   curl -X POST "$(terraform -chdir=infra/terraform/ec2 output -raw public_base_url)/calls/outbound" \
     -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"to": "+1..."}'
   ```
8. **Between demos:** stop the instance (`aws ec2 stop-instances --instance-ids <id>`; about $6 a month while stopped, and the Elastic IP keeps the CloudFront origin valid). Start it again and the stack comes back by itself. `terraform destroy` removes everything, including the call database on the instance.

Demo trade-offs: one server, so a reboot or deploy drops live calls, and the database lives on the instance's disk with no backups. `t3.small` has burstable CPU: fine for a few calls at a time, `instance_type = "t3.medium"` for more.

### Production-shaped demo on ECS Fargate (`infra/terraform/ecs`)

```text
Twilio ──HTTPS webhooks / WSS media──> CloudFront ──HTTP + secret header──> ALB
   ALB  /media ──> agent service (Fargate, 1 vCPU / 2 GB) ──> Deepgram, Bedrock (Claude), Cartesia
   ALB  /*     ──> gateway service (Fargate, 0.25 vCPU / 0.5 GB, runs migrations on start)
   both ──> RDS Postgres 16 (db.t4g.micro) + ElastiCache Redis 7 (cache.t4g.micro), private subnets
   secrets: Secrets Manager -> ECS env vars    image: CodeBuild (from GitHub) -> ECR    logs: CloudWatch
```

Trade-offs compared with the production design in [ARCHITECTURE.md](ARCHITECTURE.md) §5 (decision D14): single-AZ database and cache, no NAT gateway (tasks sit in public subnets with public IPs, and their security group admits only the ALB), no WAF, one task per service, and a 120 s call drain on deploys instead of 1800 s.

**Cost while running:** roughly $100–110 a month (Fargate about $45, ALB about $18, RDS about $14, Redis about $12, public IPv4 addresses about $15, the rest under $5). `desired_count = 0` in `terraform.tfvars` stops the tasks between demos (saves about $50 a month).

Steps are the same as above with these differences: run Terraform in `infra/terraform/ecs` (about 15 minutes, mostly RDS); the services' tasks fail until the first image exists; deploy with `uv run python scripts/aws/deploy_ecs.py --profile ...` (add `--restart-only` after changing only secrets); logs are in CloudWatch: `aws logs tail /ecs/voice-agent --follow --profile ...`.

Both stacks keep Terraform state locally (`terraform.tfstate` in the stack's folder, gitignored). It contains the generated secrets, so keep it on the machine that ran `apply`, and move it to an S3 backend before anyone else runs Terraform.

### Troubleshooting the AWS demo

| Symptom | Likely cause |
|---|---|
| `deploy_ec2.py` says no running instance | The instance is stopped (start it), or `terraform apply` hasn't run |
| `deploy_ec2.py` fails at `git fetch` (or CodeBuild does) | The commit isn't on GitHub yet: `git push` it, or pass `--ref <branch>` |
| `deploy_ec2.py` fails with `docker: command not found` right after `apply` | First-boot setup is still running (a few minutes). Retry |
| `https://dxxxx.cloudfront.net/healthz` returns 403 `forbidden` | The request reached the server or ALB without CloudFront's header: wait for the distribution to finish deploying |
| CloudFront returns 502/504 | The stack isn't running yet (EC2: run `deploy_ec2.py`; ECS: run `deploy_ecs.py`) |
| ECS tasks stop with `ResourceInitializationError` or `CannotPullContainerError` | Secrets missing (re-run `apply`) or no image yet (`deploy_ecs.py`) |
| Calls connect but the agent is silent | `push_secrets.py` not run, or no deploy since (EC2: `deploy_ec2.py`; ECS: `deploy_ecs.py --restart-only`). Check the agent log for `missing config` |
| `AccessDeniedException` from Bedrock in the agent log | Model access not enabled for `llm_model`, or `llm_model` changed without `terraform apply` (the role allows only that model) |

## Environment variables

All variables are listed in [`../.env.example`](../.env.example). In AWS, values come from **Secrets Manager**: third-party keys from `voice-agent/app` (filled by `scripts/aws/push_secrets.py`) and generated tokens from `voice-agent/internal` (Terraform). On the EC2 demo the server writes both into its `.env` on each deploy (`voice-agent/internal` also carries the non-secret settings); on ECS they are injected into the task definitions, and the other variables are set in `infra/terraform/ecs/ecs.tf`.

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
| `LLM_MODEL` | agent | no | `claude-opus-5-5`; on Bedrock it becomes `<BEDROCK_INFERENCE_PROFILE>.anthropic.<LLM_MODEL>` (Opus 4.6: `claude-opus-4-6-v1`). Ids that already contain `anthropic.` are used as is |
| `BEDROCK_INFERENCE_PROFILE` | agent | no | Cross-region inference profile prefix for Bedrock: `us` (default) or `global`; empty = bare in-region model id |
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
