"""Regex PII redaction for transcript text stored in Postgres.

A first line of defence. The post-call worker (phase 5) adds NER-based redaction for names
and addresses. Rules here target what a phone agent hears most: numbers and emails.
"""

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[EMAIL]"),
    (re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"), "[SSN]"),
    # 13-19 digits, optionally grouped by spaces or dashes: card numbers.
    (re.compile(r"\b(?:\d[\s-]?){12,18}\d\b"), "[CARD]"),
    # 7+ digits with optional +, spaces, dots, dashes, parentheses: phone and account numbers.
    (re.compile(r"(?<!\w)\+?\(?\d(?:[\s.()-]*\d){6,}\b"), "[NUMBER]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
