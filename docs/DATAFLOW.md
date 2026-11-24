# Voice AI Agent Prototype: Dataflow

Companion to [ARCHITECTURE.md](ARCHITECTURE.md). The timings in the sequence diagrams match the latency budget in ARCHITECTURE.md §7: p50 values for stages 1–8, which add up to a **760 ms voice-to-voice** p50.

**Participants used throughout**

| Short name | Component |
|---|---|
| Caller | Person on the phone (inbound caller or outbound callee) |
| Twilio | Twilio Programmable Voice + Media Streams |
| GW | Call Gateway (FastAPI webhooks, Twilio REST) |
| Agent | Voice Agent Service (Pipecat pipeline for this call) |
| VAD | Silero VAD + smart-turn (in process, inside Agent) |
| STT | Deepgram streaming |
| LLM | Claude (`claude-opus-5-5`) via the Anthropic API or Bedrock |
| TTS | Cartesia streaming (ElevenLabs fallback) |
| Tools | Tool executor + adapters (Calendar, CRM, data) |
| Redis / PG / SQS / S3 | Live state / Postgres / queue / object storage |
| Worker | Post-call worker |

---

## 1. Sequence diagrams

### (a) Inbound call

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Twilio
    participant GW as Call Gateway
    participant Redis
    participant PG as Postgres
    participant Agent as Voice Agent
    participant STT as Deepgram STT
    participant LLM as Claude LLM
    participant TTS as Cartesia TTS
    participant SQS
    participant Worker

    Caller->>Twilio: Dials business number
    Twilio->>GW: POST /twilio/voice (CallSid, From, To)
    GW->>GW: Validate X-Twilio-Signature
    GW->>PG: Lookup contact by From, insert Call (status ringing)
    GW->>Redis: SET call state (playbook, language, agent_version)
    GW-->>Twilio: TwiML Connect Stream to wss /media with signed token
    Note over GW,Twilio: Webhook reply target p95 under 300 ms
    GW->>Twilio: REST start dual-channel recording (callback /twilio/recording)
    Twilio->>Agent: Open WebSocket /media, start event (streamSid, token)
    Agent->>Agent: Verify token, build pipeline for this call
    par Pre-warm providers
        Agent->>STT: Open streaming WS (mulaw 8 kHz)
    and
        Agent->>TTS: Open streaming WS (pcm_mulaw 8 kHz)
    and
        Agent->>Redis: GET call state and contact context
    end
    Agent->>Twilio: Stream cached greeting + recording disclosure audio
    Note over Caller,Agent: Answer to greeting audio target 1200 ms or less
    Twilio->>Caller: Plays greeting

    loop Each conversational turn
        Caller->>Twilio: Speaks
        Twilio->>Agent: media frames, 20 ms each (stage 1-2, about 50 ms)
        Agent->>STT: Forward audio frames
        STT-->>Agent: Interim transcripts
        Agent->>Agent: VAD silence 200 ms + smart-turn says complete (stage 3)
        Agent->>STT: Finalize
        STT-->>Agent: Final transcript (stage 4, about 60 ms)
        Agent->>LLM: Stream request (cached system prompt + tools + history)
        LLM-->>Agent: First token (stage 5, about 280 ms)
        Agent->>TTS: First clause (stage 6, about 30 ms)
        TTS-->>Agent: First audio bytes (stage 7, about 90 ms)
        Agent->>Twilio: media frames + mark events (stage 8, about 50 ms)
        Twilio->>Caller: Agent voice
        Note over Caller,Agent: Voice-to-voice p50 about 760 ms, target 800 ms or less
        Agent->>PG: Insert Turn + TranscriptSegments + stage latencies (async)
    end

    Caller->>Twilio: Hangs up
    Twilio->>Agent: stop event, WebSocket closes
    Agent->>PG: Update Call (ended_at, disposition)
    Agent->>SQS: call.ended
    Twilio->>GW: POST /twilio/status (completed, duration)
    Twilio->>GW: POST /twilio/recording (RecordingSid, url)
    GW->>SQS: recording.completed
    SQS->>Worker: Deliver events
    Worker->>Twilio: Download recording
    Worker->>Worker: Store to S3, delete from Twilio, redact transcript, score call, sync CRM
```

### (b) Outbound call

```mermaid
sequenceDiagram
    autonumber
    participant Dialer as Campaign Dialer
    participant GW as Call Gateway
    participant PG as Postgres
    participant Twilio
    participant Callee
    participant Agent as Voice Agent
    participant LLM as Claude LLM

    Dialer->>GW: POST /calls/outbound (contact_id, campaign_id)
    GW->>PG: Check consent, DNC, calling hours in callee time zone, attempt count
    alt Not allowed to call
        GW-->>Dialer: 409 blocked (reason)
    else Allowed
        GW->>PG: Insert Call (direction outbound, status queued)
        GW->>Twilio: REST create call (to, from, url /twilio/voice, async AMD DetectMessageEnd, status callbacks)
        GW-->>Dialer: 202 accepted (call_id)
        Twilio->>Callee: Rings
        alt Busy, no answer or failed
            Twilio->>GW: POST /twilio/status (busy or no-answer)
            GW->>PG: Outcome no_answer, schedule retry per campaign rules
        else Answered
            Twilio->>GW: POST /twilio/voice (answered)
            GW-->>Twilio: TwiML Connect Stream to wss /media
            Twilio->>Agent: Open WebSocket /media, start event
            Agent->>Agent: Wait up to 2 s for callee to speak first
            Callee->>Twilio: Hello?
            Twilio->>Agent: media frames
            Agent->>LLM: Turn 1 with outbound playbook (name, reason for call, AI disclosure)
            LLM-->>Agent: Streamed reply
            Agent->>Twilio: Hi, this is the virtual assistant from the home-services company, calling about your appointment
            Note over Agent,Twilio: Same per-turn pipeline and budget as the inbound call
            opt AMD result arrives in parallel
                Twilio->>GW: POST /twilio/amd (AnsweredBy)
                Note over GW,Agent: Handled in diagram (e)
            end
        end
    end
```

### (c) Mid-call tool call: booking a calendar slot

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Agent as Voice Agent
    participant STT as Deepgram STT
    participant LLM as Claude LLM
    participant TTS as Cartesia TTS
    participant Tools as Tool Executor
    participant Redis
    participant Cal as Google Calendar
    participant CRM as HubSpot
    participant PG as Postgres

    Caller->>Agent: Can I come in Tuesday afternoon?
    Agent->>STT: Audio frames
    STT-->>Agent: Final transcript after end-of-turn
    Agent->>LLM: Stream request
    LLM-->>Agent: Text: Let me check Tuesday afternoon for you.
    Agent->>TTS: Filler sentence
    TTS-->>Caller: Filler audio starts (end-of-turn to filler 800 ms or less)
    LLM-->>Agent: tool_use find_slots(service, Tuesday 12:00-17:00, caller tz)
    Agent->>Tools: Execute find_slots (timeout 2.5 s)
    Tools->>Cal: freebusy query
    Cal-->>Tools: Free windows
    Tools-->>Agent: Slots 14:00 and 16:00 (p50 about 400 ms)
    Agent->>PG: Insert ToolInvocation (success, latency)
    Agent->>LLM: tool_result
    LLM-->>Agent: I have 2 pm or 4 pm on Tuesday. Which works better?
    Agent->>TTS: Stream reply
    TTS-->>Caller: Answer audio (end-of-turn to answer 2200 ms or less p95)

    Caller->>Agent: 4 pm please
    Agent->>LLM: Final transcript
    LLM-->>Agent: Just to confirm, Tuesday the 1st at 4 pm, is that right?
    Agent->>TTS: Confirmation read-back
    TTS-->>Caller: Confirmation audio
    Caller->>Agent: Yes
    Agent->>LLM: Final transcript
    LLM-->>Agent: tool_use book_slot(slot_id, contact, confirmed true)
    Agent->>Tools: Execute book_slot
    Tools->>Redis: SETNX idempotency key (call_id + tool + args hash)
    alt Key already exists (duplicate call)
        Redis-->>Tools: Existing booking result
    else New key
        Tools->>Cal: Insert event
        Cal-->>Tools: event_id
        Tools->>CRM: Create meeting + associate contact
        CRM-->>Tools: ok
        Tools->>Redis: Store result under idempotency key (TTL 24 h)
    end
    Tools-->>Agent: Booked (event_id)
    Agent->>PG: Insert ToolInvocation (book_slot, success)
    Agent->>LLM: tool_result
    LLM-->>Agent: You are all set for Tuesday at 4 pm. You will get a text confirmation.
    Agent->>TTS: Stream reply
    TTS-->>Caller: Confirmation audio

    opt Tool timeout or error
        Tools-->>Agent: is_error true (timeout)
        Agent->>LLM: tool_result with error
        LLM-->>Agent: I am having trouble reaching the calendar. Can I text you a booking link?
    end
```

### (d) Transfer to a human (cold transfer)

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Agent as Voice Agent
    participant LLM as Claude LLM
    participant TTS as Cartesia TTS
    participant GW as Call Gateway
    participant CRM as HubSpot
    participant PG as Postgres
    participant Twilio
    participant Human as Human Agent

    Caller->>Agent: I want to talk to a real person
    Agent->>LLM: Final transcript
    LLM-->>Agent: Sure, connecting you to a team member now.
    LLM-->>Agent: tool_use transfer_to_human(reason, queue sales)
    Agent->>TTS: Handoff sentence (uninterruptible)
    TTS-->>Caller: Handoff audio
    Agent->>GW: POST /internal/calls/{sid}/transfer (queue, reason, summary)
    GW->>CRM: Log call note with AI summary so the human sees context
    GW->>PG: Insert Transfer (requested), update Call status transferring
    GW->>Twilio: REST update call with TwiML Dial to queue number (action /twilio/transfer-status, timeout 25 s)
    Twilio->>Agent: stop event, Media Stream closes
    Agent->>PG: Update Call disposition transferred, flush turns
    Twilio->>Human: Rings human line
    alt Human answers
        Human->>Twilio: Answers
        Twilio-->>Caller: Bridged to human
        Twilio->>GW: POST /twilio/transfer-status (completed)
        GW->>PG: Transfer connected, outcome transferred_connected
    else No answer or busy
        Twilio->>GW: POST /twilio/transfer-status (no-answer)
        GW-->>Twilio: TwiML Say sorry + Record voicemail (or reconnect to agent)
        GW->>PG: Transfer failed, outcome callback_requested
        GW->>CRM: Create callback task
    end
    Note over Agent,Twilio: Warm transfer (phase 2) uses a Conference with a whisper summary to the human before the agent leaves
```

### (e) Voicemail / answering-machine detection (outbound)

```mermaid
sequenceDiagram
    autonumber
    participant Twilio
    participant GW as Call Gateway
    participant Redis
    participant Agent as Voice Agent
    participant TTS as Cartesia TTS
    participant PG as Postgres
    participant Dialer as Campaign Dialer

    Note over Twilio,Agent: Call placed with async AMD (DetectMessageEnd). Media Stream is already connected (see diagram b)
    Twilio->>GW: POST /twilio/amd (AnsweredBy)
    GW->>PG: Store amd_result and amd_latency on Call
    alt AnsweredBy is human
        GW->>Redis: PUBLISH call events amd human
        Redis-->>Agent: amd human
        Agent->>Agent: No change, conversation continues
    else AnsweredBy is machine_end_beep or machine_end_silence
        GW->>Redis: PUBLISH call events amd machine
        Redis-->>Agent: amd machine
        Agent->>Agent: Interrupt pipeline, cancel LLM and TTS, send clear to Twilio
        alt Campaign policy leave_voicemail
            Agent->>TTS: Voicemail script with contact name (or cached audio)
            TTS-->>Agent: Audio
            Agent->>Twilio: media frames (message after the beep)
            Agent->>GW: end_call(disposition voicemail_left)
        else Campaign policy hang_up
            Agent->>GW: end_call(disposition voicemail_no_message)
        end
        GW->>Twilio: REST update call status completed
        GW->>PG: Outcome voicemail
        GW->>Dialer: Schedule retry (for example in 4 h, max 3 attempts)
    else AnsweredBy is fax or unknown
        GW->>Twilio: REST hang up
        GW->>PG: Outcome unreachable
    end
    Note over GW,TTS: While AMD is pending, the agent is told not to say personal details. A greeting that is clearly a machine (long monologue, beep tone) can also trigger the voicemail path early
```

**Inbound voicemail** (no agent, or after hours): the Gateway returns TwiML `Say` + `Record` with a transcription callback. The Worker stores the audio in S3 and creates a `Call` with `disposition = voicemail_received` plus a CRM task.

---

## 2. Audio path

### 2.1 End-to-end frame path

```mermaid
flowchart LR
    subgraph caller_side["Caller side"]
        phone["Caller phone"]
    end

    subgraph twilio["Twilio"]
        edge["Media edge<br/>8 kHz mu-law"]
        buf["Playback buffer"]
    end

    subgraph agent["Voice Agent: Pipecat pipeline for one call"]
        direction LR
        tin["Transport in<br/>WS media event decode"]
        vadbr["Upsample to 16 kHz<br/>VAD branch only"]
        vad["Silero VAD<br/>speech start and stop"]
        turn["Smart-turn<br/>end-of-utterance model"]
        sttsvc["STT service<br/>forward mu-law"]
        agg["User context aggregator<br/>final transcript to history"]
        llmsvc["LLM service<br/>streamed tokens + tool calls"]
        toolx["Tool executor"]
        sent["Sentence aggregator<br/>first clause flush"]
        ttssvc["TTS service<br/>context per turn"]
        tout["Transport out<br/>20 ms frames + mark"]
        intr{{"Interruption controller"}}
    end

    subgraph prov["Providers"]
        dg["Deepgram"]
        cl["Claude"]
        ca["Cartesia"]
    end

    phone --> edge --> tin
    tin --> sttsvc --> dg
    dg -- "interim + final" --> agg
    tin --> vadbr --> vad --> turn
    turn -- "turn complete" --> agg
    agg --> llmsvc
    llmsvc <--> cl
    llmsvc -- "tool_use" --> toolx
    toolx -- "tool_result" --> llmsvc
    llmsvc -- "text deltas" --> sent --> ttssvc
    ttssvc <--> ca
    ttssvc -- "mu-law audio" --> tout --> buf --> phone

    vad -- "speech start while bot speaking" --> intr
    dg -- "interim words" --> intr
    intr -. "cancel" .-> llmsvc
    intr -. "cancel context" .-> ttssvc
    intr -. "flush queue + clear" .-> tout
    tout -. "clear event" .-> buf
```

**Step by step (normal turn)**

1. **Caller audio → Twilio media stream**: Twilio sends JSON `media` events, each carrying 20 ms of base64 8 kHz μ-law audio (160 bytes).
2. **Transport in**: Pipecat's `TwilioFrameSerializer` decodes them into `InputAudioRawFrame`s.
3. **STT streaming**: μ-law frames go straight to Deepgram (no resampling). Interim transcripts come back continuously.
4. **VAD**: a copy of each frame is upsampled to 16 kHz PCM for Silero. The VAD emits `UserStartedSpeaking` / `UserStoppedSpeaking` (200 ms stop window).
5. **Turn detection**: on stop, the smart-turn model scores the last utterance. If complete, the turn ends: the agent sends Deepgram `Finalize` and the final transcript is appended to the LLM context. If incomplete, it waits up to 1.5 s more.
6. **LLM**: a streaming request with a cached prefix (tools + system prompt) and the rolling history. Text deltas flow out immediately.
7. **Tool calls**: any preamble text is spoken first (filler). `tool_use` blocks go to the tool executor; results return as `tool_result` and the LLM continues (diagram c).
8. **Sentence aggregation**: the first clause (punctuation or ≥ 6 words) goes to TTS immediately; later text goes sentence by sentence.
9. **TTS streaming**: Cartesia returns `pcm_mulaw` 8 kHz chunks plus word timestamps.
10. **Transport out**: chunks are re-framed to 20 ms `media` events. A `mark` event is sent after each sentence so we know when Twilio has played it.
11. **Caller hears the agent.**

### 2.2 Barge-in cancellation

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Twilio
    participant Agent as Agent transport
    participant VAD as VAD + Interruption controller
    participant STT as Deepgram STT
    participant LLM as Claude LLM
    participant TTS as Cartesia TTS

    Agent->>Twilio: media frames (agent is speaking)
    Twilio->>Caller: Agent voice
    Caller->>Twilio: Starts talking over the agent
    Twilio->>Agent: media frames
    Agent->>VAD: Audio
    Agent->>STT: Audio
    VAD->>VAD: 120 ms or more of speech detected
    STT-->>VAD: Interim transcript with 1 or more words (about 80 ms later)
    Note over VAD: Backchannel filter ignores uh-huh, yeah, okay
    VAD->>LLM: Cancel in-flight stream
    VAD->>TTS: Cancel context_id for this turn
    VAD->>Agent: Flush outbound audio queue
    Agent->>Twilio: clear event (streamSid)
    Twilio-->>Caller: Agent audio stops
    Note over Caller,Twilio: Caller speech start to agent silent 250 ms or less
    Agent->>Agent: Truncate last assistant message to words already played (mark events + TTS word timestamps)
    Agent->>Agent: Log interruption on Turn (interrupted true, played_ms)
    Note over Agent,LLM: The new caller utterance continues through the normal turn path
```

Segments flagged `uninterruptible` (legal disclosures, transfer hand-off sentence) skip steps 9–12: caller speech is buffered and handled after the segment ends.

---

## 3. Data model

```mermaid
erDiagram
    CONTACT ||--o{ CALL : "places or receives"
    CAMPAIGN ||--o{ CALL : "originates"
    AGENT_VERSION ||--o{ CALL : "handled by"
    CALL ||--o{ TURN : "contains"
    CALL ||--o{ TRANSCRIPT_SEGMENT : "transcribed as"
    TURN ||--o{ TRANSCRIPT_SEGMENT : "spoken in"
    TURN ||--o{ TOOL_INVOCATION : "triggers"
    CALL ||--o{ TOOL_INVOCATION : "records"
    CALL ||--o{ RECORDING : "has"
    CALL ||--|| OUTCOME : "ends with"
    CALL ||--o{ TRANSFER : "may have"
    CALL ||--o{ CALL_EVENT : "emits"
    CALL ||--o{ EVAL_RESULT : "scored by"

    CONTACT {
        uuid id PK
        string phone_e164 UK "hashed copy used for lookup"
        string full_name "PII"
        string email "PII"
        string crm_provider
        string crm_external_id
        string timezone "IANA"
        string preferred_language
        bool consent_outbound_ai
        bool dnc
        timestamptz created_at
    }
    CAMPAIGN {
        uuid id PK
        string name
        string playbook
        string voicemail_policy "leave_voicemail or hang_up"
        int max_attempts
        int max_concurrency
        jsonb calling_window
        timestamptz created_at
    }
    AGENT_VERSION {
        uuid id PK
        string playbook
        string prompt_hash
        string tools_version
        string llm_model
        string stt_model
        string tts_voice_id
        jsonb turn_config
        timestamptz created_at
    }
    CALL {
        uuid id PK
        string twilio_call_sid UK
        string direction "inbound or outbound"
        uuid contact_id FK
        uuid campaign_id FK
        uuid agent_version_id FK
        string from_number "PII"
        string to_number
        string language
        string status
        string amd_result
        int amd_latency_ms
        timestamptz started_at
        timestamptz answered_at
        timestamptz ended_at
        int duration_s
        string trace_id
        float cost_usd
    }
    TURN {
        uuid id PK
        uuid call_id FK
        int seq
        string speaker "user, agent or system"
        string kind "speech, disclosure, filler, voicemail"
        timestamptz started_at
        timestamptz ended_at
        bool interrupted
        int played_ms
        int eot_ms "end-of-turn detection"
        int stt_final_ms
        int llm_ttft_ms
        int tts_ttfb_ms
        int v2v_ms "voice-to-voice"
        int input_tokens
        int output_tokens
        int cached_tokens
    }
    TRANSCRIPT_SEGMENT {
        uuid id PK
        uuid call_id FK
        uuid turn_id FK
        string channel "caller or agent"
        int start_ms
        int end_ms
        text text_redacted
        string text_raw_s3_key "raw text kept in S3 only"
        float confidence
        string language
        bool is_final
    }
    TOOL_INVOCATION {
        uuid id PK
        uuid call_id FK
        uuid turn_id FK
        string tool_name
        string tool_use_id
        jsonb args_redacted
        string idempotency_key UK
        string status "success, error, timeout, rejected"
        string error_code
        jsonb result_summary
        int latency_ms
        timestamptz started_at
    }
    RECORDING {
        uuid id PK
        uuid call_id FK
        string twilio_recording_sid UK
        string s3_key
        int channels
        int duration_s
        string format
        timestamptz expires_at
    }
    OUTCOME {
        uuid id PK
        uuid call_id FK
        string disposition
        bool task_success
        string success_criteria
        text summary_redacted
        string crm_sync_status
        timestamptz created_at
    }
    TRANSFER {
        uuid id PK
        uuid call_id FK
        string type "cold or warm"
        string target
        string reason
        string status
        int ring_s
        timestamptz requested_at
    }
    CALL_EVENT {
        uuid id PK
        uuid call_id FK
        string type
        jsonb payload
        timestamptz at
    }
    EVAL_RESULT {
        uuid id PK
        uuid call_id FK
        string evaluator "llm_judge, human, rule"
        string rubric_version
        float score
        jsonb dimension_scores
        text notes
        timestamptz created_at
    }
```

**Disposition values (`OUTCOME.disposition`)**: `booked`, `qualified`, `not_qualified`, `resolved`, `unresolved`, `transferred_connected`, `transfer_failed`, `callback_requested`, `voicemail_left`, `voicemail_no_message`, `voicemail_received`, `no_answer`, `busy`, `opt_out`, `caller_hung_up`, `error`.

**Relationships in words**: a `Call` belongs to at most one `Contact` (unknown callers create a stub contact), one `Campaign` (outbound only) and exactly one `AgentVersion`. A call has many `Turn`s in order (`seq`). Each turn has 1..n `TranscriptSegment`s and 0..n `ToolInvocation`s. A call has 0..n `Recording`s (usually one dual-channel recording), exactly one `Outcome`, 0..n `Transfer`s, an append-only `CallEvent` log (webhooks, AMD, interruptions, failovers) and 0..n `EvalResult`s.

---

## 4. What happens to data after a call

```mermaid
flowchart LR
    live["Live call<br/>Redis state + in-memory context"] -->|"each turn, async"| pg[("Postgres<br/>Turn, Segment, ToolInvocation")]
    live -->|"call.ended"| q["SQS"]
    tw["Twilio recording"] -->|"recording.completed"| q
    q --> w["Worker"]
    w -->|"download, then delete at Twilio"| s3raw[("S3 recordings/<br/>KMS, 90 days")]
    w -->|"assemble"| s3t[("S3 transcripts/raw<br/>30 days")]
    w -->|"PII redaction"| s3r[("S3 transcripts/redacted<br/>1 year")]
    w -->|"LLM judge + rules"| ev[("Postgres EVAL_RESULT")]
    w -->|"summary + disposition"| crm["CRM"]
    w -->|"metrics"| an["Analytics views"]
```

---

## 5. Data storage, retention and PII handling

### 5.1 What is stored where

| Data | Store | Contains PII? | Retention (default, to confirm with client) |
|---|---|---|---|
| Live call state, context checkpoints | Redis | Yes (transient) | TTL: call end + 1 h |
| Idempotency keys + tool results | Redis | Minimal | 24 h |
| Call, Turn, Outcome, Transfer, CallEvent rows | Postgres | Phone numbers only | 2 years (metadata, analytics) |
| Transcript segments (redacted text) | Postgres | Redacted | 1 year |
| Raw transcripts (unredacted) | S3 `transcripts/raw/` (until phase 5: the `transcript_segments.text_raw` column in Postgres, purged daily by `voice-maintenance purge-raw`) | **Yes** | **30 days**, then lifecycle delete |
| Redacted transcripts (JSON) | S3 `transcripts/redacted/` | Redacted | 1 year |
| Call recordings (dual-channel) | S3 `recordings/` | **Yes (voice = biometric in some states)** | **90 days**, then delete. Deleted from Twilio right after the S3 copy is verified |
| Tool invocation args/results | Postgres (redacted JSONB) | Redacted | 1 year |
| Contacts | Postgres (mirror of the CRM) | **Yes** | While the CRM relationship is active; deleted on request |
| Eval results | Postgres | No | 2 years |
| LLM traces | Langfuse | Redacted inputs/outputs | 30 days |
| Application logs | CloudWatch Logs | Redacted (no raw transcripts) | 30 days |
| Metrics / traces | CloudWatch / X-Ray | No | 15 months (CloudWatch default) / 30 days |

### 5.2 PII handling rules

1. **Encryption**: TLS 1.2+ in transit everywhere (Twilio WSS, provider WebSockets, internal ALB). At rest: RDS and ElastiCache encryption, and S3 SSE-KMS with a customer-managed key per data class (recordings, transcripts).
2. **Redaction**: the Worker runs an entity redactor (Deepgram `redact` on the post-call transcript plus a regex and NER pass, e.g. Microsoft Presidio) for names, phone numbers, emails, addresses, dates of birth, card and SSN patterns. Postgres, logs and Langfuse only ever hold redacted text.
3. **Logging hygiene**: the structured logger has a deny-list of fields (`transcript`, `text`, `from_number`, `email`) that are hashed or dropped. Live transcripts are never written to CloudWatch.
4. **Provider data retention**: set vendor options so providers do not keep audio or text beyond processing where available (Deepgram and Cartesia opt-out of data retention / model improvement; Anthropic API zero-retention terms if eligible; Bedrock does not store prompts). This must be confirmed per contract.
5. **Access control**: IAM roles per service; recordings and raw transcripts readable only by the Worker role and a break-glass QA role; access is logged with CloudTrail S3 data events. The call viewer streams recordings through short-lived pre-signed URLs.
6. **Data subject requests**: `DELETE /contacts/{id}` removes the contact, redacts linked rows, and deletes S3 objects by the `call_id` prefix.
7. **Minimization**: the LLM only receives the contact fields the playbook needs (first name, upcoming appointment), never the full CRM record.
8. **Sensitive inputs**: payments use Twilio `<Pay>` (outside the LLM path). If HIPAA applies, recordings and transcripts must be covered by BAAs and the retention table must be revised.
