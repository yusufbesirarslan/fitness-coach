"""LP16-R4 (D3) on real PostgreSQL: the week precondition across midnight.

The guard compares the expected week with the week of the instant that dates
the row, read AFTER the owner lock. These interleavings hold one write at the
lock (events, never sleeps), move the server clock across Sunday 24:00
Europe/Istanbul, and check that PostgreSQL's arbitration plus the guard never
let an old intent become a new week's check-in, while a committed key keeps
replaying its own week.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \\
        python -m pytest tests/test_lp16r4_week_precondition_pg.py -q
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires disposable PG_TEST_DATABASE_URL", allow_module_level=True)

PATH = "/api/v1/progress/check-ins"
USERS = ("lp16r4pg1", "lp16r4pg2")
BODY = {"weight_kg": 78.0, "training_intensity": 4, "fatigue": 2,
        "sleep_quality": 5, "nutrition_adherence": 4,
        "progressive_overload": "yes"}
WEEK, NEXT_WEEK = "2026-07-20", "2026-07-27"


def _istanbul(*parts):
    from app.timeutil import APP_TZ
    return datetime(*parts, tzinfo=APP_TZ)


class _Clock:
    """The server "now" the write path reads: one instant per read, in order,
    the last one repeating. ``move_to`` replaces what every later read sees."""

    def __init__(self, monkeypatch, *instants):
        from app.services.mobile_weekly_checkin import submission
        self._instants = list(instants) or [_istanbul(2026, 7, 26, 23, 59, 59)]
        self.reads = 0
        monkeypatch.setattr(submission, "app_now", self._read)

    def move_to(self, instant):
        self._instants = [instant]
        self._offset = self.reads

    _offset = 0

    def _read(self):
        index = min(self.reads - self._offset, len(self._instants) - 1)
        self.reads += 1
        return self._instants[index]


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
    from app.extensions import db, limiter
    from app.services import gamification, mobile_auth

    app = create_app()
    app.config["TESTING"] = True
    limiter.enabled = False
    gamification._last_rollover_check[0] = datetime.utcnow()

    def authenticate(raw):
        from app.models import User
        user = User.query.filter_by(username=raw).one()
        return mobile_auth.MobilePrincipal(
            user, SimpleNamespace(id=1), {"sub": user.cognito_sub})

    monkeypatch.setattr(mobile_auth, "authenticate_access", authenticate)
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


def _post(app, username, key, week):
    return app.test_client().post(PATH, json=BODY, headers={
        "Authorization": f"Bearer {username}", "Idempotency-Key": key,
        "Check-In-Week-Start": week})


def _observe(app):
    from app.extensions import db
    with app.app_context():
        engine = db.engine
    with engine.connect() as conn:
        users = dict(conn.execute(sa.text(
            'SELECT username, weight FROM "user"')).all())
        rows = conn.execute(sa.text(
            'SELECT u.username, c.idempotency_key, c.created_at '
            'FROM weekly_check_in c JOIN "user" u ON u.id = c.user_id '
            'ORDER BY c.created_at, c.id')).all()
    return users, [tuple(r) for r in rows]


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


class _Hold:
    """Pauses the first write right after its claim (owner lock held), before
    the server clock is read."""

    def __init__(self, monkeypatch):
        from app.services import weekly_checkin

        self._real = weekly_checkin.load_context
        self.entered, self.release = threading.Event(), threading.Event()
        self._armed = True
        monkeypatch.setattr(weekly_checkin, "load_context", self._load_context)

    def _load_context(self, owner_id):
        if self._armed:
            self._armed = False
            self.entered.set()
            assert self.release.wait(10), "held write was never released"
        return self._real(owner_id)


def _contend(app, hold, first, second, before_release=lambda: None):
    with _LockProbe(app) as probe, ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(_post, app, *first)
        assert hold.entered.wait(10)

        def _second():
            probe.thread = threading.get_ident()
            return _post(app, *second)

        b = pool.submit(_second)
        try:
            assert probe.reached.wait(10), "second request never reached the lock"
            assert not b.done(), "second request crossed the held owner lock"
            before_release()
        finally:
            hold.release.set()
        return a.result(15), b.result(15)


def _code(response):
    return response.get_json()["error"]["code"]


# 1 --------------------------------------------------------------------------
def test_midnight_passes_while_an_uncommitted_intent_holds_the_lock(
        pg_app, monkeypatch):
    """Sent Sunday 23:59:59, dated after midnight: refused, and its queued
    same-key retry is refused too. No row lands in the new week."""
    clock = _Clock(monkeypatch)

    def cross_midnight():
        clock.move_to(_istanbul(2026, 7, 27, 0, 0, 1))

    first, retry = _contend(pg_app, _Hold(monkeypatch),
                            ("lp16r4pg1", "r4-pg-held-0001", WEEK),
                            ("lp16r4pg1", "r4-pg-held-0001", WEEK),
                            before_release=cross_midnight)

    assert (first.status_code, retry.status_code) == (409, 409)
    assert _code(first) == _code(retry) == "CHECKIN_WEEK_CHANGED"
    users, rows = _observe(pg_app)
    assert rows == []
    assert users["lp16r4pg1"] == 80.0


# 2 --------------------------------------------------------------------------
def test_a_commit_before_midnight_replays_its_own_week_after_it(
        pg_app, monkeypatch):
    """The winner commits on Sunday; the queued same-key retry and a later
    Monday retry both replay the Sunday row. One row, original week."""
    clock = _Clock(monkeypatch)
    first, retry = _contend(pg_app, _Hold(monkeypatch),
                            ("lp16r4pg1", "r4-pg-commit-01", WEEK),
                            ("lp16r4pg1", "r4-pg-commit-01", WEEK))
    clock.move_to(_istanbul(2026, 7, 27, 9, 0))
    late = _post(pg_app, "lp16r4pg1", "r4-pg-commit-01", WEEK)

    assert (first.status_code, retry.status_code, late.status_code) == (
        201, 200, 200)
    assert first.get_json() == retry.get_json() == late.get_json()
    assert first.get_json()["analysis_day"] == "2026-07-26"
    assert clock.reads == 1                  # replays never read the clock
    _users, rows = _observe(pg_app)
    assert [(n, k) for n, k, _at in rows] == [("lp16r4pg1", "r4-pg-commit-01")]
    assert rows[0][2] == datetime(2026, 7, 26, 20, 59, 59)    # naive UTC


# 3 --------------------------------------------------------------------------
def test_a_different_key_queued_across_midnight_cannot_land_in_the_new_week(
        pg_app, monkeypatch):
    # The held winner reads Sunday 23:59:59; whoever reads next, Monday 00:00.
    _Clock(monkeypatch, _istanbul(2026, 7, 26, 23, 59, 59),
           _istanbul(2026, 7, 27, 0, 0, 0))
    first, second = _contend(pg_app, _Hold(monkeypatch),
                             ("lp16r4pg1", "r4-pg-diff-a001", WEEK),
                             ("lp16r4pg1", "r4-pg-diff-b001", WEEK))
    fresh = _post(pg_app, "lp16r4pg1", "r4-pg-diff-c001", NEXT_WEEK)

    assert (first.status_code, second.status_code, fresh.status_code) == (
        201, 409, 201)
    assert _code(second) == "CHECKIN_WEEK_CHANGED"
    assert fresh.get_json()["analysis_day"] == "2026-07-27"
    _users, rows = _observe(pg_app)
    assert [k for _n, k, _at in rows] == ["r4-pg-diff-a001", "r4-pg-diff-c001"]


# 4 --------------------------------------------------------------------------
def test_another_owner_never_replays_a_committed_key_across_midnight(
        pg_app, monkeypatch):
    clock = _Clock(monkeypatch)
    owner = _post(pg_app, "lp16r4pg1", "r4-pg-shared-01", WEEK)
    clock.move_to(_istanbul(2026, 7, 27, 8, 0))
    other = _post(pg_app, "lp16r4pg2", "r4-pg-shared-01", WEEK)

    assert owner.status_code == 201
    assert other.status_code == 409 and _code(other) == "CHECKIN_WEEK_CHANGED"
    users, rows = _observe(pg_app)
    assert [(n, k) for n, k, _at in rows] == [("lp16r4pg1", "r4-pg-shared-01")]
    assert (users["lp16r4pg1"], users["lp16r4pg2"]) == (78.0, 80.0)
