"""Build docs/Voice_Agent_Architecture.pdf from the Markdown docs.

Converts the docs to one HTML page (Mermaid diagrams rendered client-side with
mermaid.js from a CDN), checks every diagram parses, then prints to PDF with a
headless Chrome or Edge.

Usage (from the repo root):
    pip install markdown
    python scripts/docs/build_pdf.py

Requires network access (mermaid.js CDN) and Chrome or Edge installed.
Set CHROME_PATH to override browser discovery.
"""
import os
import shutil
import subprocess
import html
import json
import re
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[2]
docs = ROOT / "docs"
build_dir = Path(__file__).resolve().parent / "_build"
build_dir.mkdir(exist_ok=True)
out = build_dir / "doc.html"
pdf_path = docs / "Voice_Agent_Architecture.pdf"

ORDER = [
    ("ARCHITECTURE.md", "Architecture"),
    ("DATAFLOW.md", "Dataflow"),
    ("EVALUATION.md", "Evaluation & Observability"),
    ("README.md", "Repository, Local Run & Environment"),
]

OVERVIEW = r"""
# Executive overview

This document describes the architecture of a **production-style prototype voice AI agent** that answers inbound calls and places outbound calls. It holds natural conversations (qualification, support, booking, follow-ups), calls tools mid-call (CRM, calendar, data lookups), transfers to humans, detects voicemail, records calls, and measures its own quality.

## The AI pipeline in one picture

```mermaid
flowchart LR
    A(["Caller audio"]) --> B["Twilio Media Stream<br/>8 kHz mu-law, 20 ms frames"]
    B --> C["VAD + smart-turn<br/>end-of-turn 200 ms"]
    B --> D["Deepgram STT<br/>streaming, 60 ms"]
    C --> E["Claude LLM<br/>TTFT 280 ms"]
    D --> E
    E <--> F["Tools<br/>CRM, calendar, lookup"]
    E --> G["Sentence aggregator<br/>30 ms"]
    G --> H["Cartesia TTS<br/>TTFB 90 ms"]
    H --> I["Twilio playback<br/>50 ms"]
    I --> J(["Caller hears agent"])
    C -. "barge-in: cancel LLM, TTS, clear buffer" .-> I
```

**Voice-to-voice target: about 760 ms p50 (budget 800 ms), 1.5 s p95**, excluding the carrier/PSTN leg.

## Services needed

| # | Service | Role | Where it runs |
|---|---|---|---|
| 1 | **Twilio** Programmable Voice, Media Streams, AMD, Recording | Phone numbers, PSTN, audio streaming, transfers, voicemail detection, recordings | Twilio cloud (US1) |
| 2 | **Call Gateway** (FastAPI) | Webhooks, TwiML, outbound API, transfers, routing | ECS Fargate |
| 3 | **Voice Agent** (Pipecat) | Real-time pipeline per call: VAD, STT, LLM, TTS, barge-in, tools | ECS Fargate |
| 4 | **Deepgram** Nova-3 | Streaming speech-to-text, multilingual | SaaS |
| 5 | **Claude** (Anthropic API, Bedrock failover) | Dialogue + tool calling | SaaS / AWS Bedrock |
| 6 | **Cartesia** Sonic (ElevenLabs fallback) | Streaming text-to-speech | SaaS |
| 7 | **Tool adapters** | Google Calendar, HubSpot (then Salesforce, Zoho), customer data, knowledge base | In Agent process |
| 8 | **Post-call Worker** | Recordings to S3, transcript redaction, LLM-judge scoring, CRM sync | ECS Fargate + SQS |
| 9 | **Campaign Dialer** | Outbound scheduling, consent, DNC, calling windows, retries | ECS Fargate + EventBridge |
| 10 | **RDS PostgreSQL** | Calls, turns, transcripts (redacted), tool calls, outcomes, evals | AWS |
| 11 | **ElastiCache Redis** | Live call state, idempotency keys, AMD/transfer events | AWS |
| 12 | **S3 + KMS** | Recordings, raw and redacted transcripts | AWS |
| 13 | **SQS, Secrets Manager, ALB, WAF, Route 53, ECR** | Queueing, secrets, ingress, security, images | AWS |
| 14 | **OpenTelemetry, CloudWatch, X-Ray, Langfuse** | Traces, metrics, logs, LLM traces and evals | AWS + SaaS |

## Added beyond the original brief

Compliance (recording consent, AI disclosure, TCPA consent, DNC, calling hours), STIR/SHAKEN caller-ID reputation, prompt-injection defenses for tool use, idempotent tool writes, DTMF handling, pre-synthesized prompts, knowledge-base (RAG) tool, simulated-caller regression tests, human QA review queue, per-call cost tracking, time-zone handling, CI/CD with canary rollouts, provider failover for every stage, and data retention/PII redaction policy. Details are in Architecture §8–§9.
"""

MERMAID_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.S)

diagrams: list[str] = []


def md_to_html(text: str) -> str:
    def repl(m):
        diagrams.append(m.group(1).strip())
        return f'\n\n<div class="mermaid-slot" data-idx="{len(diagrams) - 1}"></div>\n\n'

    text = MERMAID_RE.sub(repl, text)
    return markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])


sections = [("overview", "Executive overview", md_to_html(OVERVIEW))]
for fname, title in ORDER:
    raw = (docs / fname).read_text(encoding="utf-8")
    sections.append((fname.lower().replace(".md", ""), title, md_to_html(raw)))

toc = "".join(f'<li><a href="#{sid}">{html.escape(t)}</a></li>' for sid, t, _ in sections)
body = "".join(
    f'<section class="doc" id="{sid}"><div class="doc-label">{html.escape(t)}</div>{h}</section>'
    for sid, t, h in sections
)

page = f"""<!doctype html>
<html><head><meta charset="utf-8">
<title>Voice AI Agent Architecture</title>
<style>
@page {{ size: A4; margin: 16mm 14mm 16mm 14mm; }}
:root {{ --ink:#1b1f24; --muted:#5b6470; --accent:#2f5bd3; --rule:#d9dee5; --soft:#f4f6f9; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Segoe UI", Arial, sans-serif; color: var(--ink); background:#fff; font-size: 10.5pt; line-height: 1.45; margin:0; }}
.cover {{ height: 255mm; display:flex; flex-direction:column; justify-content:center; padding: 0 10mm; page-break-after: always; }}
.cover h1 {{ font-size: 32pt; margin: 0 0 6mm; border:none; letter-spacing:-0.5px; }}
.cover .sub {{ font-size: 14pt; color: var(--muted); margin-bottom: 14mm; }}
.cover .bar {{ width: 40mm; height: 3px; background: var(--accent); margin-bottom: 10mm; }}
.cover .meta {{ color: var(--muted); font-size: 10pt; }}
.toc {{ page-break-after: always; }}
.toc ol {{ font-size: 13pt; line-height: 2; }}
.toc a {{ color: var(--ink); text-decoration: none; }}
section.doc {{ page-break-before: always; }}
.doc-label {{ font-size: 8.5pt; text-transform: uppercase; letter-spacing: 1.5px; color: var(--accent); margin-bottom: 2mm; }}
h1 {{ font-size: 20pt; margin: 0 0 4mm; padding-bottom: 2mm; border-bottom: 2px solid var(--ink); }}
h2 {{ font-size: 14.5pt; margin: 8mm 0 3mm; padding-bottom: 1mm; border-bottom: 1px solid var(--rule); page-break-after: avoid; }}
h3 {{ font-size: 12pt; margin: 6mm 0 2mm; page-break-after: avoid; }}
h4 {{ font-size: 11pt; margin: 4mm 0 2mm; }}
p, li {{ orphans: 3; widows: 3; }}
a {{ color: var(--accent); }}
table {{ border-collapse: collapse; width: 100%; margin: 3mm 0 4mm; font-size: 9pt; page-break-inside: auto; }}
tr {{ page-break-inside: avoid; }}
th, td {{ border: 1px solid var(--rule); padding: 4px 6px; vertical-align: top; text-align: left; }}
th {{ background: var(--soft); font-weight: 600; }}
code {{ font-family: Consolas, "Courier New", monospace; font-size: 8.8pt; background: var(--soft); padding: 0 3px; border-radius: 3px; }}
pre {{ background: var(--soft); padding: 8px 10px; border-radius: 4px; font-size: 8.3pt; overflow: hidden; white-space: pre-wrap; page-break-inside: avoid; }}
pre code {{ background: none; padding: 0; }}
blockquote {{ border-left: 3px solid var(--accent); margin: 3mm 0; padding: 1mm 4mm; color: var(--muted); background: var(--soft); }}
.mermaid-slot {{ margin: 4mm 0 5mm; text-align: center; page-break-inside: avoid; }}
.mermaid-slot svg {{ max-width: 100% !important; max-height: 240mm; height: auto; }}
@page wide {{ size: A4 landscape; margin: 10mm; }}
.mermaid-slot.landscape {{ page: wide; break-before: page; break-after: page; margin: 0; }}
.mermaid-slot.landscape svg {{ width: 100% !important; max-width: 100% !important; max-height: 185mm; }}
.mermaid-error {{ color: #b00020; border: 1px solid #b00020; padding: 4px; white-space: pre-wrap; text-align:left; font-size: 8pt; }}
</style></head>
<body>
<div class="cover">
  <div class="bar"></div>
  <h1>Voice AI Agent Prototype</h1>
  <div class="sub">Architecture, AI pipeline, dataflow, data model and evaluation plan</div>
  <div class="meta">Inbound and outbound phone agent · Python + Pipecat · Twilio · Deepgram · Claude · Cartesia · AWS ECS<br>
  Draft v0.1 · 2026-11-23 · Design only, no implementation</div>
</div>
<div class="toc"><h1>Contents</h1><ol>{toc}</ol></div>
{body}
<pre id="mermaid-status" style="display:none"></pre>
<script id="diagrams" type="application/json">{json.dumps(diagrams).replace("</", "<\\/")}</script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"></script>
<script>
(async () => {{
  const status = document.getElementById('mermaid-status');
  const codes = JSON.parse(document.getElementById('diagrams').textContent);
  const results = [];
  try {{
    mermaid.initialize({{ startOnLoad: false, theme: 'neutral', securityLevel: 'loose',
      flowchart: {{ htmlLabels: true, useMaxWidth: true }},
      sequence: {{ useMaxWidth: true, mirrorActors: false, wrap: true }},
      er: {{ useMaxWidth: true }} }});
    for (let i = 0; i < codes.length; i++) {{
      const slot = document.querySelector('.mermaid-slot[data-idx="' + i + '"]');
      try {{
        await mermaid.parse(codes[i]);
        const {{ svg }} = await mermaid.render('mmd' + i, codes[i]);
        slot.innerHTML = svg;
        const el = slot.querySelector('svg');
        const vb = el && el.viewBox && el.viewBox.baseVal;
        if (vb && vb.width / vb.height > 1.5 && vb.width / vb.height < 5 && vb.width > 900) {{
          slot.classList.add('landscape');
        }}
        results.push('OK ' + i + ' ' + (vb ? Math.round(vb.width) + 'x' + Math.round(vb.height) : ''));
      }} catch (e) {{
        slot.innerHTML = '<div class="mermaid-error">Diagram ' + i + ' failed: ' + (e && e.message ? e.message : e) + '</div>';
        results.push('FAIL ' + i + ': ' + (e && e.message ? e.message : e));
      }}
    }}
  }} catch (e) {{ results.push('FATAL ' + e); }}
  status.textContent = 'MERMAID_RESULTS\\n' + results.join('\\n') + '\\nEND_RESULTS';
  document.title = 'RENDERED';
}})();
</script>
</body></html>"""

out.write_text(page, encoding="utf-8")
print(f"wrote {out} with {len(diagrams)} diagrams")


def find_browser() -> str:
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    sys.exit("Chrome/Edge not found; set CHROME_PATH")


browser = find_browser()
common = [
    browser, "--headless=new", "--disable-gpu", "--no-first-run",
    f"--user-data-dir={build_dir / 'profile'}", "--virtual-time-budget=30000",
]
url = out.resolve().as_uri()

dom = subprocess.run(common + ["--dump-dom", url], capture_output=True, text=True, encoding="utf-8").stdout
m = re.search(r"MERMAID_RESULTS(.*?)END_RESULTS", dom, re.S)
if not m:
    sys.exit("Could not read Mermaid render results (network or browser problem)")
failures = [line for line in m.group(1).splitlines() if line.startswith(("FAIL", "FATAL"))]
if failures:
    sys.exit("Mermaid errors:\n" + "\n".join(failures))
print(f"all {len(diagrams)} Mermaid diagrams rendered")

subprocess.run(common + ["--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", url], check=True,
               capture_output=True)
print(f"wrote {pdf_path}")
