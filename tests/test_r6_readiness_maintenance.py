"""R6-01A: health/readiness probes never trigger request-time maintenance.

A blue/green candidate is probed with ``/health`` and ``/health?deep=1``
BEFORE nginx switches traffic to it. ``maybe_weekly_rollover`` is a global
before_request hook: on an ordinary request it may take the fleet-wide
rollover/purge throttles in Redis (``SET NX``), run the weekly rollover
(PostgreSQL + leaderboard Redis) and dispatch daily maintenance. A readiness
probe must do none of that — otherwise the candidate mutates shared state
while the active revision is still serving.

Ordinary traffic keeps the maintenance contract unchanged; that half is
asserted here too so the fix cannot be "disable maintenance".

    python -m pytest tests/test_r6_readiness_maintenance.py -v
"""
import re

import pytest
import sqlalchemy as sa

_WRITE_SQL = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|REPLACE)\b",
    re.IGNORECASE)

# Every Redis command a health probe may legitimately issue: dependency
# checks and informational reads. Anything else is a write.
_READ_COMMANDS = frozenset({"ping", "exists", "get", "ttl", "zscore", "mget"})

MAINTENANCE_KEYS = ("fitx:rollover_check", "fitx:session_purge")


class RecordingRedis:
    """Shared-Redis stand-in with real ``SET NX EX`` semantics.

    ``set`` succeeds on an empty key, so a hook that reaches the throttle
    WOULD pass it — the probe is not protected by a pre-held key.
    """

    def __init__(self):
        self.strings = {}
        self.commands = []

    def set(self, key, value, nx=False, ex=None):
        self.commands.append(("set", key))
        if nx and key in self.strings:
            return None
        self.strings[key] = value
        return True

    def writes(self):
        return [c for c in self.commands if c[0] not in _READ_COMMANDS]

    def __getattr__(self, name):
        def _command(*args, **kwargs):
            self.commands.append((name, args[0] if args else None))
            return None
        return _command


@pytest.fixture
def armed(app, monkeypatch):
    """Maintenance fully armed: no throttle held anywhere, real rollover.

    Without the readiness exemption a single request here runs the weekly
    rollover (INSERT weekly_reset_log, ...) and dispatches maintenance.
    """
    from app import hooks, jobs
    from app.services import gamification

    redis = RecordingRedis()
    monkeypatch.setattr("app.extensions.redis_client", redis)
    monkeypatch.setattr(gamification, "redis_client", redis)
    monkeypatch.setattr(gamification, "_last_rollover_check", [None])
    monkeypatch.setattr(hooks, "_last_rollover_check", [None])
    monkeypatch.setattr(hooks, "_last_purge_check", [None])

    rollovers = []
    real_rollover = hooks.run_weekly_rollover

    def _spy_rollover(now):
        rollovers.append(now)
        return real_rollover(now)

    monkeypatch.setattr(hooks, "run_weekly_rollover", _spy_rollover)
    dispatched = []
    monkeypatch.setattr(
        jobs, "dispatch_background",
        lambda func, *args, **kwargs: dispatched.append(func))

    statements = []

    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    sa.event.listen(sa.engine.Engine, "before_cursor_execute", _record)
    try:
        yield {"redis": redis, "rollovers": rollovers,
               "dispatched": dispatched, "statements": statements}
    finally:
        sa.event.remove(sa.engine.Engine, "before_cursor_execute", _record)


def _assert_maintenance_free(armed):
    assert armed["rollovers"] == []
    assert armed["dispatched"] == []
    assert armed["redis"].writes() == []
    for key in MAINTENANCE_KEYS:
        assert key not in armed["redis"].strings
    assert [s for s in armed["statements"] if _WRITE_SQL.match(s)] == []


# ── 1. shallow health ──────────────────────────────────────────────────────

@pytest.mark.parametrize("method", ["get", "head"])
def test_shallow_health_runs_no_request_time_maintenance(client, armed, method):
    response = getattr(client, method)("/health")
    assert response.status_code == 200
    _assert_maintenance_free(armed)


def test_health_query_string_does_not_change_eligibility(client, armed):
    # deep=1 from an untrusted source is served the shallow body; any other
    # query string is the same endpoint. Eligibility follows the endpoint.
    for path in ("/health?deep=1", "/health?deep=0", "/health?x=1"):
        response = client.get(path, environ_base={"REMOTE_ADDR": "203.0.113.9"})
        assert response.status_code == 200
        assert "revision" not in response.get_json()
    _assert_maintenance_free(armed)


# ── 2. deep readiness ──────────────────────────────────────────────────────

def test_deep_readiness_runs_no_request_time_maintenance(client, armed):
    response = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    body = response.get_json()
    assert response.status_code == 200
    # Really the deep view: revision proof and dependency fields are served.
    assert {"revision", "redis", "login", "bedrock", "worker"} <= set(body)
    assert body["redis"] == "ok" and body["login"] == "ok"
    _assert_maintenance_free(armed)


def test_anonymous_probe_reaches_no_hook_that_writes(client, armed):
    """Every global before_request hook on an anonymous probe: CSRF (GET is a
    no-op), mobile principal (not mobile_api), limiter (memory:// here),
    rollover (exempt), update_streak (anonymous -> returns before any query),
    locale (session read only). Net effect: no write SQL, no Redis write, and
    no session cookie issued."""
    response = client.get("/health")
    deep = client.get("/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == deep.status_code == 200
    assert "Set-Cookie" not in response.headers
    assert "Set-Cookie" not in deep.headers
    _assert_maintenance_free(armed)


# ── 4. ordinary traffic keeps the maintenance contract ─────────────────────

def test_ordinary_request_still_runs_rollover_and_purge(client, armed):
    from app.jobs.tasks import run_daily_maintenance

    client.get("/login")
    assert len(armed["rollovers"]) == 1
    assert armed["dispatched"] == [run_daily_maintenance]
    assert set(MAINTENANCE_KEYS) <= set(armed["redis"].strings)
    # Non-vacuity of the probe tests above: the same armed state really
    # writes the database when maintenance runs.
    assert [s for s in armed["statements"] if _WRITE_SQL.match(s)]


def test_ordinary_request_still_respects_the_throttle(client, armed):
    client.get("/login")
    client.get("/login")
    assert len(armed["rollovers"]) == 1
    assert len(armed["dispatched"]) == 1


def test_health_probes_do_not_consume_the_throttle_window(client, armed):
    """A probe neither runs maintenance nor delays it for real traffic."""
    for _ in range(3):
        client.get("/health")
        client.get("/health?deep=1", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    client.get("/login")
    assert len(armed["rollovers"]) == 1
    assert len(armed["dispatched"]) == 1


def test_eligibility_is_endpoint_based(app):
    from app.hooks import request_runs_maintenance

    for path in ("/health", "/health?deep=1"):
        with app.test_request_context(path):
            assert request_runs_maintenance() is False
    for path in ("/login", "/", "/no-such-page"):
        with app.test_request_context(path):
            assert request_runs_maintenance() is True
