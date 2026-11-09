"""AXISAI-LAUNCH-FREE-BE-01 authoritative PostgreSQL proofs; disposable database only.

Concurrent first use of a campaign window, exhaustion under contention, and the
d8f1a3c5e7b9 scope expansion against a populated pre-migration schema.
"""
import importlib.util
import os
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

pytestmark = pytest.mark.pg_concurrency
if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires disposable PostgreSQL", allow_module_level=True)

from test_ai_allowances_pg import race, scalar  # noqa: E402

WORKERS = 8
ROOT = Path(__file__).resolve().parents[1]
NARROW = "scope IN ('STARTER', 'PREMIUM')"


def _policy():
    from app.services.ai_allowances import launch_policy
    return replace(launch_policy.LAUNCH_V1_POLICY, version="synthetic-lf-base-pg",
                   launch_free=replace(launch_policy.LAUNCH_FREE_CAMPAIGN, version="synthetic-lf-pg"))


def _windows():
    from app.services.ai_allowances import launch_policy
    campaign = launch_policy.LAUNCH_FREE_CAMPAIGN
    return [campaign.boundary(n) for n in range(4)]


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    assert url.startswith(("postgresql://", "postgresql+psycopg2://")), "disposable PostgreSQL required"
    monkeypatch.setenv("DATABASE_URL", url)
    from app import create_app
    from app.extensions import db, limiter
    from app.models import User
    app = create_app()
    app.config.update(TESTING=True, AI_ALLOWANCE_LEDGER_ENABLED=True, AI_ALLOWANCE_POLICY=_policy(),
                      APPLE_SUBSCRIPTIONS_ENABLED=False)
    limiter.enabled = False
    with app.app_context():
        assert db.engine.dialect.name == "postgresql"
        db.drop_all()
        db.create_all()
        users = [User(username=f"pglf{i}", email=f"pglf{i}@example.invalid") for i in range(2)]
        db.session.add_all(users)
        db.session.commit()
        ids = [u.id for u in users]
    yield app, ids
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_concurrent_first_use_creates_exactly_one_window_bucket(pg_app):
    from app.services import ai_allowances as A
    app, (owner, other) = pg_app
    lf1 = _windows()[0]
    results = race(app, lambda key: A.reserve(owner, "coach", key, now=lf1),
                   [(f"first-use-{i:02d}",) for i in range(WORKERS)])
    assert all(getattr(r, "scope", None) == "LAUNCH_FREE" for r in results), results
    assert scalar(app, "SELECT count(*) FROM ai_allowance_bucket WHERE user_id = :u", u=owner) == 1
    assert scalar(app, "SELECT period_key FROM ai_allowance_bucket WHERE user_id = :u", u=owner) == "LF1"
    assert scalar(app, "SELECT granted FROM ai_allowance_bucket WHERE user_id = :u", u=owner) == 30
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE user_id = :u", u=owner) == WORKERS
    assert scalar(app, "SELECT count(*) FROM ai_starter_grant") == 0
    assert scalar(app, "SELECT count(*) FROM ai_allowance_bucket WHERE user_id = :u", u=other) == 0


def test_concurrent_exhaustion_never_overspends_a_window(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    lf3 = _windows()[2]
    results = race(app, lambda key: A.reserve(owner, "qr_menu", key, now=lf3),
                   [(f"exhaust-race-{i:02d}",) for i in range(WORKERS)])
    held = [r for r in results if getattr(r, "scope", None) == "LAUNCH_FREE"]
    assert len(held) == 5
    assert sorted(r for r in results if isinstance(r, str)) == ["AI_ALLOWANCE_EXHAUSTED"] * (WORKERS - 5)
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE feature = 'qr_menu'") == 5
    assert scalar(app, "SELECT count(*) FROM ai_allowance_bucket") == 1
    assert scalar(app, "SELECT count(*) FROM ai_usage_reservation") == 5


def test_same_key_race_holds_once(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    lf1 = _windows()[0]
    results = race(app, lambda: A.reserve(owner, "pump_check", "same-key-race", now=lf1),
                   [() for _ in range(WORKERS)])
    fresh = [r for r in results if getattr(r, "may_execute", False)]
    assert len(fresh) == 1
    assert sorted(r for r in results if isinstance(r, str)) == ["AI_ALLOWANCE_IN_PROGRESS"] * (WORKERS - 1)
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE feature = 'pump_check'") == 1


def test_race_across_a_window_boundary_grants_each_window_once(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    lf2 = _windows()[1]
    args = [(f"boundary-race-{i:02d}", lf2 - timedelta(microseconds=1) if i % 2 else lf2)
            for i in range(WORKERS)]
    results = race(app, lambda key, now: A.reserve(owner, "coach", key, now=now), args)
    assert all(getattr(r, "scope", None) == "LAUNCH_FREE" for r in results), results
    rows = sa.text("SELECT period_key, reserved FROM ai_allowance_bucket ORDER BY period_key")
    from app.extensions import db
    with app.app_context():
        assert db.session.execute(rows).all() == [("LF1", WORKERS // 2), ("LF2", WORKERS // 2)]


def test_cross_boundary_settlement_and_sweep_hit_the_original_bucket(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    lf2 = _windows()[1]
    with app.app_context():
        held = [A.reserve(owner, "coach", f"spanning-{i}", now=lf2 - timedelta(seconds=1)) for i in range(4)]
    later = lf2 + A.RESERVATION_TTL + timedelta(seconds=1)

    def settle(i):
        return (A.expire_abandoned(now=later) if i % 2 else
                A.finalize(owner, held[i].reference, "success", now=later))
    race(app, settle, [(i,) for i in range(4)])
    assert scalar(app, "SELECT count(*) FROM ai_allowance_bucket") == 1
    assert scalar(app, "SELECT used + reserved FROM ai_allowance_bucket WHERE period_key = 'LF1'") == 4
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE period_key = 'LF1'") == 0


# ── d8f1a3c5e7b9 on PostgreSQL ─────────────────────────────────────────────
def _migration(name):
    path = ROOT / "migrations/versions" / name
    spec = importlib.util.spec_from_file_location(path.stem + "_pg", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    with Operations.context(MigrationContext.configure(conn)):
        fn()


def _definition(conn):
    return conn.execute(sa.text(
        "SELECT pg_get_constraintdef(oid), convalidated FROM pg_catalog.pg_constraint "
        "WHERE conname = 'ck_ai_allowance_bucket_scope'")).one()


def _narrow(conn, owner):
    conn.execute(sa.text("ALTER TABLE ai_allowance_bucket DROP CONSTRAINT ck_ai_allowance_bucket_scope"))
    conn.execute(sa.text(f"ALTER TABLE ai_allowance_bucket ADD CONSTRAINT ck_ai_allowance_bucket_scope "
                         f"CHECK ({NARROW})"))
    for scope, key, used in (("STARTER", "NEVER", 4), ("PREMIUM", "2026-11", 7)):
        conn.execute(sa.text(
            "INSERT INTO ai_allowance_bucket (user_id, scope, feature, period_key, policy_version, "
            "granted, used, reserved, billed_releases, created_at, updated_at) VALUES "
            "(:o, :s, 'coach', :k, 'launch-v1-usable-result', 60, :u, 1, 0, now(), now())"),
            {"o": owner, "s": scope, "k": key, "u": used})


def _insert_campaign(conn, owner, scope="LAUNCH_FREE", used=0):
    conn.execute(sa.text(
        "INSERT INTO ai_allowance_bucket (user_id, scope, feature, period_key, policy_version, "
        "granted, used, reserved, billed_releases, created_at, updated_at) VALUES "
        "(:o, :s, 'coach', 'LF1', 'launch-free-v1', 30, :u, 0, 0, now(), now())"),
        {"o": owner, "s": scope, "u": used})


def test_expand_migration_on_populated_postgresql(pg_app):
    from app.extensions import db
    app, (owner, _) = pg_app
    m = _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py")
    with app.app_context():
        with db.engine.begin() as conn:
            _narrow(conn, owner)
            before = conn.execute(sa.text("SELECT * FROM ai_allowance_bucket ORDER BY id")).all()
            with pytest.raises(sa.exc.IntegrityError):
                with conn.begin_nested():
                    _insert_campaign(conn, owner)
            _run(conn, m.upgrade)
            definition, valid = _definition(conn)
            assert valid is True and "'LAUNCH_FREE'" in definition and "'STARTER'" in definition
            _run(conn, m.upgrade)  # reentrant
            assert conn.execute(sa.text("SELECT * FROM ai_allowance_bucket ORDER BY id")).all() == before
            _insert_campaign(conn, owner)
            for bad in (lambda: _insert_campaign(conn, owner, scope="UNLIMITED"),
                        lambda: _insert_campaign(conn, owner, used=31)):
                with pytest.raises(sa.exc.IntegrityError):
                    with conn.begin_nested():
                        bad()
            with pytest.raises(RuntimeError):
                _run(conn, m.downgrade)
            assert conn.execute(sa.text(
                "SELECT count(*) FROM ai_allowance_bucket WHERE scope = 'LAUNCH_FREE'")).scalar() == 1


def test_create_all_first_schema_replays_then_widens(pg_app):
    from app.db_init import replay_shipped_predicates
    from app.extensions import db
    app, _ = pg_app
    with app.app_context():
        with db.engine.begin() as conn:
            definition, valid = _definition(conn)
            assert valid is True and "'LAUNCH_FREE'" in definition
            with pytest.raises(RuntimeError):
                with conn.begin_nested():
                    _run(conn, _migration("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)
            replay_shipped_predicates(conn, {"ai_allowance_bucket"})
            assert "'LAUNCH_FREE'" not in _definition(conn)[0]
            _run(conn, _migration("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)
            _run(conn, _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py").upgrade)
            assert _definition(conn) == (definition, True)


def test_real_fresh_boot_reaches_head_with_the_widened_scope(monkeypatch):
    """prepare_release on an empty PostgreSQL: create_all, replay, stamp, full upgrade."""
    url = os.environ["PG_TEST_DATABASE_URL"]
    reset = "DROP SCHEMA public CASCADE; CREATE SCHEMA public"
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.exec_driver_sql(reset)
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("FITX_SKIP_DB_INIT", "1")
    try:
        from app import create_app, db_init
        from app.extensions import db
        app = create_app()
        app.config.update(TESTING=True)
        db_init.prepare_release(app)
        with app.app_context():
            with db.engine.connect() as conn:
                assert conn.exec_driver_sql(
                    "SELECT version_num FROM alembic_version").scalar_one() == "e1a5f0c3d2b4"
                definition, valid = _definition(conn)
                assert valid is True and "'LAUNCH_FREE'" in definition
            db.session.remove()
            db.engine.dispose()
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(reset)
        engine.dispose()


@pytest.mark.parametrize("damage", [
    f"CHECK ({NARROW} OR 1=1)",
    "CHECK (scope IN ('STARTER', 'PREMIUM', 'LAUNCH_FREE', 'UNLIMITED'))",
    "CHECK (scope IN ('STARTER', 'PREMIUM', 'LAUNCH_FREE')) NOT VALID",
])
def test_unfamiliar_or_unvalidated_predicate_fails_closed(pg_app, damage):
    from app.extensions import db
    app, _ = pg_app
    with app.app_context():
        with db.engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE ai_allowance_bucket DROP CONSTRAINT ck_ai_allowance_bucket_scope"))
            conn.execute(sa.text(f"ALTER TABLE ai_allowance_bucket ADD CONSTRAINT ck_ai_allowance_bucket_scope {damage}"))
            with pytest.raises(RuntimeError):
                _run(conn, _migration("d8f1a3c5e7b9_ai_allowance_launch_free_scope.py").upgrade)
