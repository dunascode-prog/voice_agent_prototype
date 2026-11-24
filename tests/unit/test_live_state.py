from voice_agent.storage import live_state as ls


async def test_redis_round_trip(live):
    await live.put("CA1", {"call_id": "abc", "direction": "inbound"})
    assert await live.get("CA1") == {"call_id": "abc", "direction": "inbound"}
    assert await live.get("CA-missing") is None


async def test_redis_sets_ttl(live):
    await live.put("CA2", {"call_id": "x"})
    ttl = await live.client.ttl("call:CA2")
    assert 0 < ttl <= 60


async def test_memory_expiry(monkeypatch):
    state = ls.MemoryLiveState(ttl_s=10)
    now = [1000.0]
    monkeypatch.setattr(ls.time, "monotonic", lambda: now[0])
    await state.put("CA1", {"call_id": "abc"})
    assert await state.get("CA1") == {"call_id": "abc"}
    now[0] += 11
    assert await state.get("CA1") is None
