# unit

Unit tests for adapters, tool executor, turn policy.

**Phase 1:** tests for the stream token, gateway webhooks and outbound API, agent WebSocket auth, pipeline assembly and the dev server.

**Phase 2:** repository, live state (fakeredis or real Redis), call recorder, gateway persistence, agent call lookup, redaction, and a migrations-match-models check. Set `TEST_DATABASE_URL` / `TEST_REDIS_URL` to run against real Postgres/Redis, as CI does.
