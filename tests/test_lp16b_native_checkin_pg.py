"""LP16-B on real PostgreSQL: native check-in races, isolation and atomicity.

Deterministic interleavings (events + a FOR UPDATE probe, never sleeps) over
the real native route. PostgreSQL — the owner row lock and
``uq_weekly_checkin_user_key`` — is the only arbiter; nothing process-local.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \\
        python -m pytest tests/test_lp16b_native_checkin_pg.py -q
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
USERS = ("lp16bpg1", "lp16bpg2")
BODY = {"weight_kg": 78.0, "training_intensity": 4, "fatigue": 2,
        "sleep_quality": 5, "nutrition_adherence": 4,
        "progressive_overload": "yes"}


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


def _post(app, username, key, body=BODY):
    return app.test_client().post(PATH, json=body, headers={
        "Authorization": f"Bearer {username}", "Idempotency-Key": key})


def _observe(app):
    """A separate connection: what other transactions can see right now."""
    from app.extensions import db
    with app.app_context():
        engine = db.engine
    with engine.connect() as conn:
        users = dict(conn.execute(sa.text(
            'SELECT username, weight FROM "user"')).all())
        targets = dict(conn.execute(sa.text(
            'SELECT u.username, s.target_calories FROM user_session s '
            'JOIN "user" u ON u.id = s.user_id')).all())
        rows = conn.execute(sa.text(
            'SELECT u.username, c.weight, c.idempotency_key, c.created_at '
            'FROM weekly_check_in c JOIN "user" u ON u.id = c.user_id '
            'ORDER BY c.created_at, c.id')).all()
    return users, targets, [tuple(r) for r in rows]


def _target(weight):
    from app.services.calculations import (calculate_bmr, calculate_target,
                                           calculate_tdee)
    return calculate_target(calculate_tdee(
        calculate_bmr(weight, 180.0, 30, "male"), "active"), "kilo verme")


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
    """Pauses one owner's next write right after its claim (owner lock held)."""

    def __init__(self, monkeypatch):
        from app.services import weekly_checkin

        self._real = weekly_checkin.load_context
        self._owner = None
        self.entered = self.release = None
        monkeypatch.setattr(weekly_checkin, "load_context", self._load_context)

    def arm(self, owner_name):
        self._owner = owner_name
        self.entered, self.release = threading.Event(), threading.Event()
        return self.entered, self.release

    def _load_context(self, owner_id):
        from app.models import User
        name = User.query.with_entities(User.username).filter_by(
            id=owner_id).scalar()
        if self._owner is not None and name == self._owner:
            self._owner = None
            self.entered.set()
            assert self.release.wait(10), "held write was never released"
        return self._real(owner_id)


def _contend(app, hold, first, second):
    """Run ``first`` holding the owner lock; ``second`` must queue behind it."""
    entered, release = hold.arm(first[0])
    with _LockProbe(app) as probe, ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(_post, app, *first)
        assert entered.wait(10)

        def _second():
            probe.thread = threading.get_ident()
            return _post(app, *second)

        b = pool.submit(_second)
        try:
            assert probe.reached.wait(10), "second request never reached the lock"
            assert not b.done(), "second request crossed the held owner lock"
        finally:
            release.set()
        return a.result(15), b.result(15)


# 1 --------------------------------------------------------------------------
def test_same_key_same_intent_is_one_write_and_a_deterministic_replay(
        pg_app, monkeypatch):
    hold = _Hold(monkeypatch)
    for run in range(3):
        key = f"lp16b-pg-same-{run:02d}"
        first, second = _contend(pg_app, hold,
                                 ("lp16bpg1", key), ("lp16bpg1", key))
        assert (first.status_code, second.status_code) == (201, 200)
        assert first.get_json() == second.get_json()

    users, targets, rows = _observe(pg_app)
    assert [(n, w, k) for n, w, k, _at in rows] == [
        ("lp16bpg1", 78.0, f"lp16b-pg-same-{run:02d}") for run in range(3)]
    assert users["lp16bpg1"] == 78.0
    assert targets["lp16bpg1"] == _target(78.0)


# 2 --------------------------------------------------------------------------
def test_same_key_different_intent_is_one_success_and_one_409(pg_app, monkeypatch):
    first, second = _contend(
        pg_app, _Hold(monkeypatch),
        ("lp16bpg1", "lp16b-pg-conflict"),
        ("lp16bpg1", "lp16b-pg-conflict", {**BODY, "weight_kg": 70.0}))

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.get_json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    users, targets, rows = _observe(pg_app)
    assert [(n, w, k) for n, w, k, _at in rows] == [
        ("lp16bpg1", 78.0, "lp16b-pg-conflict")]
    assert users["lp16bpg1"] == 78.0
    assert targets["lp16bpg1"] == _target(78.0)


# 3 --------------------------------------------------------------------------
def test_two_keys_are_two_observations_and_weight_follows_commit_order(
        pg_app, monkeypatch):
    """Serialized by the owner lock: the clock is read after the lock, so the
    later commit is also the later ``checked_in_at`` and its weight is current.
    NOT one-check-in-per-day: both rows stay."""
    first, second = _contend(
        pg_app, _Hold(monkeypatch),
        ("lp16bpg1", "lp16b-pg-two-a", {**BODY, "weight_kg": 77.0}),
        ("lp16bpg1", "lp16b-pg-two-b", {**BODY, "weight_kg": 76.0}))

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.get_json()["checked_in_at"] < second.get_json()["checked_in_at"]
    users, targets, rows = _observe(pg_app)
    assert [(w, k) for _n, w, k, _at in rows] == [
        (77.0, "lp16b-pg-two-a"), (76.0, "lp16b-pg-two-b")]
    assert users["lp16bpg1"] == 76.0
    assert targets["lp16bpg1"] == _target(76.0)


def test_later_check_in_equal_to_the_pre_lock_weight_is_not_a_lost_update(
        pg_app, monkeypatch):
    """The second writer authenticated (loaded weight 80) BEFORE the first
    committed 77, and submits 80. Staged against that stale snapshot the ORM
    would see "no change" and skip the UPDATE, leaving the EARLIER 77 current.
    The owner is re-read under the lock, so the later observation wins."""
    first, second = _contend(
        pg_app, _Hold(monkeypatch),
        ("lp16bpg1", "lp16b-pg-stale-a", {**BODY, "weight_kg": 77.0}),
        ("lp16bpg1", "lp16b-pg-stale-b", {**BODY, "weight_kg": 80.0}))

    assert (first.status_code, second.status_code) == (201, 201)
    users, targets, rows = _observe(pg_app)
    assert [w for _n, w, _k, _at in rows] == [77.0, 80.0]
    assert users["lp16bpg1"] == 80.0
    assert targets["lp16bpg1"] == _target(80.0)


def test_free_running_distinct_keys_keep_weight_equal_to_latest_check_in(pg_app):
    for run in range(10):
        barrier = threading.Barrier(4)

        def _go(index):
            barrier.wait(10)
            return _post(pg_app, "lp16bpg1", f"lp16b-pg-free-{run}-{index}",
                         {**BODY, "weight_kg": 70.0 + index})

        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(_go, range(4)))
        assert [r.status_code for r in responses] == [201] * 4

        users, targets, rows = _observe(pg_app)
        latest_weight = rows[-1][1]
        assert len(rows) == 4 * (run + 1)
        assert users["lp16bpg1"] == latest_weight
        assert targets["lp16bpg1"] == _target(latest_weight)


# 4 --------------------------------------------------------------------------
def test_two_owners_are_isolated_and_never_block_each_other(pg_app, monkeypatch):
    entered, release = _Hold(monkeypatch).arm("lp16bpg1")
    shared = "lp16b-pg-shared-key"
    with ThreadPoolExecutor(max_workers=1) as pool:
        held = pool.submit(_post, pg_app, "lp16bpg1", shared)
        try:
            assert entered.wait(10)
            other = _post(pg_app, "lp16bpg2", shared, {**BODY, "weight_kg": 90.0})
            assert other.status_code == 201
            assert not held.done()
            users, _targets, rows = _observe(pg_app)
            assert users == {"lp16bpg1": 80.0, "lp16bpg2": 90.0}
            assert [(n, w) for n, w, _k, _at in rows] == [("lp16bpg2", 90.0)]
        finally:
            release.set()
        assert held.result(15).status_code == 201

    users, targets, rows = _observe(pg_app)
    assert users == {"lp16bpg1": 78.0, "lp16bpg2": 90.0}
    assert targets == {"lp16bpg1": _target(78.0), "lp16bpg2": _target(90.0)}
    assert sorted((n, k) for n, _w, k, _at in rows) == [
        ("lp16bpg1", shared), ("lp16bpg2", shared)]


# 5 --------------------------------------------------------------------------
def test_failure_during_derived_session_update_leaves_no_partial_write(
        pg_app, monkeypatch):
    from app.services.weekly_checkin import service as checkin_service

    def _boom(*_args):
        raise RuntimeError("derived target fault")

    monkeypatch.setattr(checkin_service, "calculate_target", _boom)
    response = _post(pg_app, "lp16bpg1", "lp16b-pg-fault")

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "CHECKIN_TEMPORARILY_UNAVAILABLE"
    users, targets, rows = _observe(pg_app)
    assert (users["lp16bpg1"], targets["lp16bpg1"], rows) == (80.0, 3.0, [])


# 6 --------------------------------------------------------------------------
def test_replay_after_a_lost_committed_response_writes_nothing_twice(
        pg_app, monkeypatch):
    from app.services import mobile_weekly_checkin

    real_submit = mobile_weekly_checkin.submit
    lost = {"once": True}

    def submit_then_lose_response(*args, **kwargs):
        result = real_submit(*args, **kwargs)       # durable commit happened
        if lost["once"]:
            lost["once"] = False
            raise ConnectionResetError("response lost after commit")
        return result

    monkeypatch.setattr(mobile_weekly_checkin, "submit", submit_then_lose_response)
    lost_response = _post(pg_app, "lp16bpg1", "lp16b-pg-lost")
    assert lost_response.status_code == 503      # the client never saw the 201
    users, _targets, rows = _observe(pg_app)
    assert users["lp16bpg1"] == 78.0 and len(rows) == 1
    committed_at = rows[0][3]

    # Another authority moves the weight; the retry must not move it back.
    from app.extensions import db
    with pg_app.app_context():
        with db.engine.begin() as conn:
            conn.execute(sa.text(
                "UPDATE \"user\" SET weight = 85.0 WHERE username = 'lp16bpg1'"))

    retry = _post(pg_app, "lp16bpg1", "lp16b-pg-lost")

    assert retry.status_code == 200
    body = retry.get_json()
    assert body["weight_kg"] == 78.0 and body["contract_version"] == 1
    users, _targets, rows = _observe(pg_app)
    assert len(rows) == 1 and rows[0][3] == committed_at
    assert users["lp16bpg1"] == 85.0
