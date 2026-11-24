import pytest

from voice_agent.telephony import stream_token
from voice_agent.telephony.stream_token import InvalidStreamToken

KEY = "signing-key"
CALL = "CA123"
NOW = 1_800_000_000.0


def test_round_trip():
    token = stream_token.mint(CALL, KEY, ttl_s=120, now=NOW)
    stream_token.verify(token, CALL, KEY, now=NOW + 60)


def test_expired():
    token = stream_token.mint(CALL, KEY, ttl_s=120, now=NOW)
    with pytest.raises(InvalidStreamToken, match="expired"):
        stream_token.verify(token, CALL, KEY, now=NOW + 121)


def test_wrong_call():
    token = stream_token.mint(CALL, KEY, ttl_s=120, now=NOW)
    with pytest.raises(InvalidStreamToken, match="different call"):
        stream_token.verify(token, "CA999", KEY, now=NOW)


def test_wrong_key():
    token = stream_token.mint(CALL, KEY, ttl_s=120, now=NOW)
    with pytest.raises(InvalidStreamToken, match="bad signature"):
        stream_token.verify(token, CALL, "other-key", now=NOW)


def test_tampered_expiry():
    call, _exp, sig = stream_token.mint(CALL, KEY, ttl_s=120, now=NOW).split(".")
    with pytest.raises(InvalidStreamToken, match="bad signature"):
        stream_token.verify(f"{call}.{int(NOW) + 99999}.{sig}", CALL, KEY, now=NOW)


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c.d"])
def test_malformed(token):
    with pytest.raises(InvalidStreamToken):
        stream_token.verify(token, CALL, KEY, now=NOW)


def test_mint_requires_key():
    with pytest.raises(ValueError):
        stream_token.mint(CALL, "", ttl_s=120)
