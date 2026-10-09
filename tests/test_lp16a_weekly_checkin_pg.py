"""LP16-A on real PostgreSQL: the extraction keeps the web check-in races.

Deterministic interleavings (events, never sleeps) over the real web routes:
keyed replay and conflict stay decided by the owner lock + unique key, the
staged row/weight/session become visible all at once or not at all, and one
owner's lock never blocks another owner.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \
        python -m pytest tests/test_lp16a_weekly_checkin_pg.py -q
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires disposable PG_TEST_DATABASE_URL", allow_module_level=True)

USERS = ("lp16apg1", "lp16apg2")
FULL = {"weight": 78, "yogunluk": 4, "fatigue": 2,
        "progressive_overload": "evet", "uyku_kalitesi": 5,
        "beslenme_uyumu": 4, "note": "pg"}


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name disposable PostgreSQL")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL is unreachable")
    finally:
        probe.dispose()

    monkeypatch.setenv("DATABASE_URL", url)
    from app import create_app
    from app.blueprints import auth as auth_bp
    from app.extensions import db, limiter
    from app.services import cognito_jwt, cognito_service, gamification

    app = create_app()
    app.config["TESTING"] = True
    limiter.enabled = False
    gamification._last_rollover_check[0] = datetime.utcnow()
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    monkeypatch.setattr(cognito_service, "authenticate", lambda username, _pw: {
        "tokens": {"access_token": f"acc-{username}", "id_token": f"id-{username}",
                   "refresh_token": f"ref-{username}", "expires_in": 3600},
        "claims": {"sub": f"sub-{username}"},
    })
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda token, use, **kw: {
        "sub": f"sub-{token.removeprefix('id-').removeprefix('acc-')}"})
    with app.app_context():
        from app.models import User, UserSession
        db.drop_all()
        db.create_all()
        for name in USERS:
            user = User(username=name, email=f"{name}@example.invalid",
                        cognito_sub=f"sub-{name}", weight=80.0, height=180.0,
                        age=30, gender="male", goal="kilo verme",
                        current_activity="active", target_weight=72.0)
            db.session.add(user)
            db.session.flush()
            db.session.add(UserSession(user_id=user.id, weight=80.0, bmr=1.0,
                                       tdee=2.0, target_calories=3.0))
        db.session.commit()
    try:
        yield app
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()
            db.drop_all()


def _client(app, username):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "lp16a-pg-csrf"
    headers = {"Origin": "http://localhost", "X-CSRFToken": "lp16a-pg-csrf"}
    assert client.post("/login", json={"username": username, "password": "unused"},
                       headers=headers).status_code == 200
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "lp16a-pg-write-csrf"
    headers["X-CSRFToken"] = "lp16a-pg-write-csrf"
    return client, headers


def _observe(app):
    """A separate connection: what other transactions can see right now."""
    from app.extensions import db
    with app.app_context():
        engine = db.engine
    with engine.connect() as conn:
        users = dict(conn.execute(sa.text(
            'SELECT username, weight FROM "user"')).all())
        sessions = dict(conn.execute(sa.text(
            'SELECT u.username, s.target_calories FROM user_session s '
            'JOIN "user" u ON u.id = s.user_id')).all())
        rows = conn.execute(sa.text(
            'SELECT u.username, c.weight, c.idempotency_key FROM weekly_check_in c '
            'JOIN "user" u ON u.id = c.user_id ORDER BY c.id')).all()
    return users, sessions, [tuple(r) for r in rows]


class _LockProbe:
    """Flags when a given thread issues the owner-row ``FOR UPDATE``."""

    def __init__(self, app):
        from app.extensions import db
        with app.app_context():
            self.engine = db.engine
        self.thread = None
        self.reached = threading.Event()

    def __call__(self, _conn, _cursor, statement, _params, _context, _many):
        if (threading.get_ident() == self.thread
                and "FOR UPDATE" in statement.upper() and "user" in statement.lower()):
            self.reached.set()

    def __enter__(self):
        sa.event.listen(self.engine, "before_cursor_execute", self)
        return self

    def __exit__(self, *_exc):
        sa.event.remove(self.engine, "before_cursor_execute", self)


def _held_feedback(monkeypatch, owner_name=None):
    from app.blueprints import tracking
    entered, release, calls = threading.Event(), threading.Event(), []

    def feedback(username, *args, **kwargs):
        calls.append(username)
        if owner_name is None or username == owner_name:
            entered.set()
            assert release.wait(10), "held feedback was never released"
        return f"feedback for {username}"

    monkeypatch.setattr(tracking, "generate_checkin_feedback", feedback)
    return entered, release, calls


def test_same_key_different_intent_waits_on_the_lock_then_conflicts(
        pg_app, monkeypatch):
    entered, release, calls = _held_feedback(monkeypatch)
    first, first_headers = _client(pg_app, "lp16apg1")
    second, second_headers = _client(pg_app, "lp16apg1")
    for headers in (first_headers, second_headers):
        headers["Idempotency-Key"] = "lp16a-pg-conflict-01"

    with _LockProbe(pg_app) as probe, ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first.post, "/checkin", json=FULL, headers=first_headers)
        assert entered.wait(10)

        def _second():
            probe.thread = threading.get_ident()
            return second.post("/checkin", json={**FULL, "weight": 70},
                               headers=second_headers)

        b = pool.submit(_second)
        try:
            assert probe.reached.wait(10), "second request never reached the lock"
            assert not b.done(), "different intent crossed the held owner lock"
        finally:
            release.set()
        first_response, second_response = a.result(15), b.result(15)

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert calls == ["lp16apg1"]
    users, sessions, rows = _observe(pg_app)
    assert rows == [("lp16apg1", 78.0, "lp16a-pg-conflict-01")]
    assert users["lp16apg1"] == 78.0
    assert sessions["lp16apg1"] == 2328.0


def test_same_key_same_intent_replay_is_deterministic_across_runs(
        pg_app, monkeypatch):
    for run in range(5):
        entered, release, calls = _held_feedback(monkeypatch)
        first, first_headers = _client(pg_app, "lp16apg1")
        second, second_headers = _client(pg_app, "lp16apg1")
        key = f"lp16a-pg-replay-{run:02d}"
        first_headers["Idempotency-Key"] = second_headers["Idempotency-Key"] = key

        with _LockProbe(pg_app) as probe, ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(first.post, "/checkin", json=FULL, headers=first_headers)
            assert entered.wait(10)

            def _second():
                probe.thread = threading.get_ident()
                return second.post("/checkin", json=FULL, headers=second_headers)

            b = pool.submit(_second)
            try:
                assert probe.reached.wait(10)
                assert not b.done()
            finally:
                release.set()
            responses = [a.result(15), b.result(15)]

        assert [r.status_code for r in responses] == [200, 200]
        assert responses[0].get_json() == responses[1].get_json()
        assert calls == ["lp16apg1"]
    _users, _sessions, rows = _observe(pg_app)
    assert [key for _name, _weight, key in rows] == [
        f"lp16a-pg-replay-{run:02d}" for run in range(5)]


def test_staged_check_in_is_invisible_until_its_single_commit(pg_app, monkeypatch):
    """Row, User.weight and the derived session flush before commit (quest
    staging autoflushes them) yet no other transaction sees any of them."""
    from app.blueprints import tracking

    monkeypatch.setattr(tracking, "generate_checkin_feedback", lambda *a, **k: "fb")
    staged, release = threading.Event(), threading.Event()
    original_claim = tracking._claim_quest

    def _paused_claim(user_id, quest_type):
        result = original_claim(user_id, quest_type)  # autoflushes the staged work
        staged.set()
        assert release.wait(10)
        return result

    monkeypatch.setattr(tracking, "_claim_quest", _paused_claim)
    client, headers = _client(pg_app, "lp16apg1")
    headers["Idempotency-Key"] = "lp16a-pg-visible-01"

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/checkin", json=FULL, headers=headers)
        try:
            assert staged.wait(10)
            users, sessions, rows = _observe(pg_app)
            assert (users["lp16apg1"], sessions["lp16apg1"], rows) == (80.0, 3.0, [])
        finally:
            release.set()
        assert pending.result(15).status_code == 200

    users, sessions, rows = _observe(pg_app)
    assert (users["lp16apg1"], sessions["lp16apg1"]) == (78.0, 2328.0)
    assert rows == [("lp16apg1", 78.0, "lp16a-pg-visible-01")]


@pytest.mark.parametrize("path,body,keyed", [
    ("/checkin", FULL, True),
    ("/checkin", FULL, False),
    ("/update-weight", {"weight": 77}, False),
])
def test_persistence_failure_leaves_no_half_write(pg_app, monkeypatch, path, body, keyed):
    from app.blueprints import tracking
    from app.models import WeeklyCheckIn

    monkeypatch.setattr(tracking, "generate_checkin_feedback", lambda *a, **k: "fb")

    class _Injected(Exception):
        pass

    def _boom(*_args):
        raise _Injected("weekly_check_in insert fault")

    client, headers = _client(pg_app, "lp16apg1")
    if keyed:
        headers["Idempotency-Key"] = "lp16a-pg-fault-01"
    sa.event.listen(WeeklyCheckIn, "before_insert", _boom)
    try:
        try:
            response = client.post(path, json=body, headers=headers)
        except Exception:
            response = None
    finally:
        sa.event.remove(WeeklyCheckIn, "before_insert", _boom)

    assert response is None or response.status_code >= 500
    users, sessions, rows = _observe(pg_app)
    assert (users["lp16apg1"], sessions["lp16apg1"], rows) == (80.0, 3.0, [])


def test_one_owners_held_lock_never_blocks_another_owner(pg_app, monkeypatch):
    entered, release, calls = _held_feedback(monkeypatch, owner_name="lp16apg1")
    first, first_headers = _client(pg_app, "lp16apg1")
    second, second_headers = _client(pg_app, "lp16apg2")
    first_headers["Idempotency-Key"] = second_headers["Idempotency-Key"] = \
        "lp16a-pg-shared-key"

    with ThreadPoolExecutor(max_workers=2) as pool:
        held = pool.submit(first.post, "/checkin", json=FULL, headers=first_headers)
        try:
            assert entered.wait(10)
            other = second.post("/checkin", json={**FULL, "weight": 90},
                                headers=second_headers)
            assert other.status_code == 200
            assert not held.done()
            users, _sessions, rows = _observe(pg_app)
            assert users == {"lp16apg1": 80.0, "lp16apg2": 90.0}
            assert rows == [("lp16apg2", 90.0, "lp16a-pg-shared-key")]
        finally:
            release.set()
        assert held.result(15).status_code == 200

    users, _sessions, rows = _observe(pg_app)
    assert users == {"lp16apg1": 78.0, "lp16apg2": 90.0}
    assert sorted(rows) == [("lp16apg1", 78.0, "lp16a-pg-shared-key"),
                            ("lp16apg2", 90.0, "lp16a-pg-shared-key")]
    assert sorted(calls) == ["lp16apg1", "lp16apg2"]
