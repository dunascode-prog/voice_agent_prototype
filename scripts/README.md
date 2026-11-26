# scripts

| Script | What it does |
|---|---|
| `aws/push_secrets.py` | Copies the Twilio, Deepgram and Cartesia keys from `.env` into the AWS demo's `voice-agent/app` secret. Prints key names only |
| `aws/deploy.py` | Builds the image in CodeBuild from the current (pushed) commit and rolls it out to the ECS services; `--restart-only` skips the build |
| `docs/build_pdf.py` | Regenerates `docs/Voice_Agent_Architecture.pdf` |

See `docs/README.md` → "Deploying the AWS demo".
