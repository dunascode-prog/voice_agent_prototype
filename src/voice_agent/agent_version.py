"""The settings that define agent behaviour, fingerprinted so every call records its version.

Changing the prompt, model, effort, voice or turn-taking config produces a new version,
which is what lets evaluation compare quality and latency across versions
(docs/ARCHITECTURE.md section 6.8).
"""

import hashlib
import json
from dataclasses import asdict, dataclass

from voice_agent.config import Settings
from voice_agent.prompts import load_prompt, prompt_hash


@dataclass(frozen=True)
class AgentVersionSpec:
    playbook: str
    prompt_hash: str
    llm_model: str
    llm_effort: str
    stt_model: str
    tts_voice_id: str
    turn_config: dict

    @property
    def fingerprint(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def columns(self) -> dict:
        return asdict(self)


def agent_version_spec(settings: Settings) -> AgentVersionSpec:
    return AgentVersionSpec(
        playbook=settings.playbook,
        prompt_hash=prompt_hash(load_prompt(settings.playbook)),
        llm_model=settings.llm_model,
        llm_effort=settings.llm_effort,
        stt_model=f"deepgram/{settings.deepgram_model}/{settings.deepgram_language}",
        tts_voice_id=f"cartesia/{settings.cartesia_voice_id_en}",
        turn_config={"vad_stop_secs": settings.vad_stop_secs, "stop_strategy": "smart_turn_v3"},
    )
