import pytest

from voice_agent.observability.redact import redact


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("email me at jane.doe+x@example.co.uk please", "email me at [EMAIL] please"),
        ("my ssn is 123-45-6789", "my ssn is [SSN]"),
        ("card 4111 1111 1111 1111 thanks", "card [CARD] thanks"),
        ("card 4111111111111111", "card [CARD]"),
        ("call me on +1 (555) 555-0123", "call me on [NUMBER]"),
        ("my number is 555.555.0123.", "my number is [NUMBER]."),
        ("account 12345678", "account [NUMBER]"),
    ],
)
def test_redacts(text, expected):
    assert redact(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "My ZIP is 90210 and I need heating help",
        "Tuesday at 4 pm works",
        "I have 2 kids and 3 dogs",
    ],
)
def test_keeps_short_numbers(text):
    assert redact(text) == text
