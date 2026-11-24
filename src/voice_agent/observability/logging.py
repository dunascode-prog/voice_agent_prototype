"""Logging setup and PII helpers.

Pipecat logs through loguru, so the services do too. Phone numbers are masked before
logging; transcripts are never logged at INFO (see docs/DATAFLOW.md section 5.2).
"""

import sys

from loguru import logger


def configure_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), backtrace=False, diagnose=False)


def mask_phone(number: str | None) -> str:
    """'+15555550123' -> '+1******0123'. Keeps enough to debug, not enough to identify."""
    if not number:
        return ""
    if len(number) <= 6:
        return "*" * len(number)
    return number[:2] + "*" * (len(number) - 6) + number[-4:]
