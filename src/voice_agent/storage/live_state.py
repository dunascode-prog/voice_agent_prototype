"""Short-lived per-call state shared by Gateway and Agent (Redis in Docker/AWS).

The Gateway writes it when it answers the webhook; the Agent reads it when the media
stream starts, so it learns the call's database id without a database round trip.
"""

import json
import time
from typing import Protocol


class LiveState(Protocol):
    async def put(self, call_sid: str, data: dict) -> None: ...
    async def get(self, call_sid: str) -> dict | None: ...
    async def close(self) -> None: ...


def _key(call_sid: str) -> str:
    return f"call:{call_sid}"


class RedisLiveState:
    def __init__(self, client, ttl_s: int):
        self.client = client
        self.ttl_s = ttl_s

    @classmethod
    def from_url(cls, url: str, ttl_s: int) -> "RedisLiveState":
        from redis.asyncio import Redis

        return cls(Redis.from_url(url, decode_responses=True), ttl_s)

    async def put(self, call_sid: str, data: dict) -> None:
        await self.client.set(_key(call_sid), json.dumps(data), ex=self.ttl_s)

    async def get(self, call_sid: str) -> dict | None:
        raw = await self.client.get(_key(call_sid))
        return json.loads(raw) if raw else None

    async def close(self) -> None:
        await self.client.aclose()


class MemoryLiveState:
    """In-process store for single-process local dev and tests."""

    def __init__(self, ttl_s: int):
        self.ttl_s = ttl_s
        self._data: dict[str, tuple[float, dict]] = {}

    async def put(self, call_sid: str, data: dict) -> None:
        self._data[_key(call_sid)] = (time.monotonic() + self.ttl_s, data)

    async def get(self, call_sid: str) -> dict | None:
        item = self._data.get(_key(call_sid))
        if item is None:
            return None
        expires, data = item
        if time.monotonic() > expires:
            del self._data[_key(call_sid)]
            return None
        return data

    async def close(self) -> None:
        self._data.clear()
