"""MON-02-BE-03A authoritative PostgreSQL races; disposable database only."""
import importlib.util
import os
import threading
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from ai_allowance_support import fixture_policy

pytestmark = pytest.mark.pg_concurrency
if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires disposable PostgreSQL", allow_module_level=True)

WORKERS = 8


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    assert url.startswith(("postgresql://", "postgresql+psycopg2://")), "disposable PostgreSQL required"
    monkeypatch.setenv("DATABASE_URL", url)
    from app import create_app
    from app.extensions import db, limiter
    from app.models import User
    app = create_app()
    app.config.update(TESTING=True, AI_ALLOWANCE_LEDGER_ENABLED=True, AI_ALLOWANCE_POLICY=fixture_policy())
    limiter.enabled = False
    with app.app_context():
        assert db.engine.dialect.name == "postgresql"
        db.drop_all()
        db.create_all()
        users = [User(username=f"pgledger{i}", email=f"pgledger{i}@example.invalid") for i in range(2)]
        db.session.add_all(users)
        db.session.commit()
        ids = [u.id for u in users]
    yield app, ids
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def race(app, target, args_list):
    """Release every worker at once; collect results or error codes."""
    from app.services.ai_allowances import AllowanceError
    barrier = threading.Barrier(len(args_list))
    results, lock = [], threading.Lock()

    def worker(args):
        with app.app_context():
            barrier.wait(10)
            try:
                value = target(*args)
            except AllowanceError as error:
                value = error.code
            except BaseException as error:  # surfaced by the assertions below
                value = error
            with lock:
                results.append(value)
    threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(results) == len(args_list)
    return results


def scalar(app, sql, **params):
    from app.extensions import db
    with app.app_context():
        return db.session.execute(sa.text(sql), params).scalar()


def wait_until_blocked(engine, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            if conn.execute(sa.text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
                    "AND cardinality(pg_blocking_pids(pid)) > 0)")).scalar():
                return True
        time.sleep(0.02)
    return False


def test_concurrent_grants_create_exactly_one(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    results = race(app, A.grant_starter, [(owner,)] * WORKERS)
    assert sorted(results) == ["ALREADY_GRANTED"] * (WORKERS - 1) + ["GRANTED"]
    assert scalar(app, "SELECT count(*) FROM ai_starter_grant") == 1
    assert scalar(app, "SELECT count(*) FROM ai_allowance_bucket") == 5


def test_second_grant_waits_on_the_owner_lock(pg_app):
    from app.extensions import db
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    inserted, release = threading.Event(), threading.Event()
    results = []

    def hold(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name == "ledger-first" and statement.startswith("INSERT INTO ai_starter_grant"):
            inserted.set()
            assert release.wait(10)
    with app.app_context():
        engine = db.engine
    sa.event.listen(engine, "after_cursor_execute", hold)

    def grant():
        with app.app_context():
            results.append(A.grant_starter(owner))
    first = threading.Thread(target=grant, name="ledger-first")
    second = threading.Thread(target=grant, name="ledger-second")
    try:
        first.start()
        assert inserted.wait(5)
        second.start()
        assert wait_until_blocked(engine), "second grant did not wait for the owner lock"
    finally:
        release.set()
        first.join(10)
        second.join(10)
        sa.event.remove(engine, "after_cursor_execute", hold)
    assert sorted(results) == ["ALREADY_GRANTED", "GRANTED"]


def test_concurrent_last_unit_has_exactly_one_winner(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    results = race(app, A.reserve, [(owner, "pump_check", f"pump-key-{i:04d}") for i in range(WORKERS)])
    winners = [r for r in results if isinstance(r, A.Reservation)]
    assert len(winners) == 1 and winners[0].may_execute
    assert sorted(r for r in results if isinstance(r, str)) == ["AI_ALLOWANCE_EXHAUSTED"] * (WORKERS - 1)
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE feature = 'pump_check'") == 1
    assert scalar(app, "SELECT count(*) FROM ai_usage_reservation") == 1
    assert scalar(app, "SELECT count(*) FROM ai_starter_grant") == 1


def test_concurrent_replays_of_one_key_hold_one_unit(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    results = race(app, A.reserve, [(owner, "coach", "same-turn-key")] * WORKERS)
    fresh = [r for r in results if isinstance(r, A.Reservation)]
    assert len(fresh) == 1 and fresh[0].may_execute
    assert sorted(r for r in results if isinstance(r, str)) == ["AI_ALLOWANCE_IN_PROGRESS"] * (WORKERS - 1)
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE feature = 'coach'") == 1


def test_concurrent_finalize_consumes_once(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    with app.app_context():
        held = A.reserve(owner, "coach", "finalize-key-1")
    results = race(app, A.finalize, [(owner, held.reference, "success")] * WORKERS)
    assert all(isinstance(r, A.Settlement) for r in results), results
    assert sorted(r.replayed for r in results) == [False] + [True] * (WORKERS - 1)
    assert scalar(app, "SELECT used FROM ai_allowance_bucket WHERE feature = 'coach'") == 1
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE feature = 'coach'") == 0


def test_mixed_finalize_outcomes_settle_once(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    with app.app_context():
        held = A.reserve(owner, "coach", "mixed-key-001")
    args = [(owner, held.reference, "success")] * 4 + [(owner, held.reference, "provider_error")] * 4
    results = race(app, A.finalize, args)
    settled = [r for r in results if isinstance(r, A.Settlement) and not r.replayed]
    assert len(settled) == 1
    loser = "provider_error" if settled[0].outcome == "success" else "success"
    assert all(r == "AI_ALLOWANCE_RESERVATION_CONFLICT" for r in results
               if not isinstance(r, A.Settlement)), results
    assert all(r.outcome != loser for r in results if isinstance(r, A.Settlement))
    used, reserved = (scalar(app, f"SELECT {c} FROM ai_allowance_bucket WHERE feature = 'coach'")
                      for c in ("used", "reserved"))
    assert reserved == 0 and used == (1 if settled[0].outcome == "success" else 0)


def test_cross_account_races_stay_isolated(pg_app):
    from app.services import ai_allowances as A
    app, owners = pg_app
    args = [(owners[i % 2], "qr_menu", f"menu-key-{i:04d}") for i in range(WORKERS)]
    results = race(app, A.reserve, args)
    assert sum(isinstance(r, A.Reservation) for r in results) == 2
    for uid in owners:
        assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE user_id = :u AND feature = 'qr_menu'",
                      u=uid) == 1
    reference = next(r for r in results if isinstance(r, A.Reservation)).reference
    holder = scalar(app, "SELECT user_id FROM ai_usage_reservation WHERE public_id = :p", p=reference)
    intruder = owners[1] if holder == owners[0] else owners[0]
    with app.app_context(), pytest.raises(A.AllowanceError) as error:
        A.finalize(intruder, reference, "success")
    assert error.value.code == "AI_ALLOWANCE_RESERVATION_NOT_FOUND"


def test_database_constraints_forbid_negative_or_over_granted_balances(pg_app):
    from app.extensions import db
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    with app.app_context():
        A.reserve(owner, "coach", "constraint-key")
        for statement in ("UPDATE ai_allowance_bucket SET used = granted + 1",
                          "UPDATE ai_allowance_bucket SET reserved = -1",
                          "UPDATE ai_allowance_bucket SET used = granted, reserved = 1",
                          "UPDATE ai_usage_reservation SET state = 'LOST'"):
            with pytest.raises(sa.exc.IntegrityError):
                db.session.execute(sa.text(statement))
                db.session.flush()
            db.session.rollback()


def test_account_row_deletion_cascades_on_postgresql(pg_app):
    from app.extensions import db
    from app.services import ai_allowances as A
    app, (owner, other) = pg_app
    with app.app_context():
        for uid in (owner, other):
            A.reserve(uid, "coach", "cascade-key-1")
        db.session.execute(sa.text('DELETE FROM "user" WHERE id = :u'), {"u": owner})
        db.session.commit()
    for table in ("ai_starter_grant", "ai_allowance_bucket", "ai_usage_reservation"):
        assert scalar(app, f"SELECT count(*) FROM {table} WHERE user_id = :u", u=owner) == 0
        assert scalar(app, f"SELECT count(*) FROM {table} WHERE user_id = :u", u=other) > 0


def _migration():
    path = Path(__file__).resolve().parents[1] / "migrations/versions/a9c1d2e3f4b5_ai_allowance_ledger.py"
    spec = importlib.util.spec_from_file_location("ai_allowance_migration_pg", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("damage", [
    None,
    "ALTER TABLE ai_allowance_bucket DROP CONSTRAINT ck_ai_allowance_bucket_within",
    "ALTER TABLE ai_allowance_bucket DROP CONSTRAINT ck_ai_allowance_bucket_within; "
    "ALTER TABLE ai_allowance_bucket ADD CONSTRAINT ck_ai_allowance_bucket_within "
    "CHECK (used + reserved <= granted) NOT VALID",
    "ALTER TABLE ai_usage_reservation DROP CONSTRAINT ck_ai_usage_reservation_state; "
    "ALTER TABLE ai_usage_reservation ADD CONSTRAINT ck_ai_usage_reservation_state "
    "CHECK (state IN ('RESERVED', 'COMMITTED', 'RELEASED', 'EXPIRED', 'LOST'))",
    "ALTER TABLE ai_allowance_bucket DROP CONSTRAINT ck_ai_allowance_bucket_scope; "
    "ALTER TABLE ai_allowance_bucket ADD CONSTRAINT ck_ai_allowance_bucket_scope "
    "CHECK (scope IN ('STARTER', 'PREMIUM') OR 1=1)",
    "DROP INDEX ix_ai_usage_reservation_open",
])
def test_create_all_first_boot_on_postgresql(pg_app, damage):
    from app.extensions import db
    app, _ = pg_app
    with app.app_context():
        with db.engine.begin() as conn:
            if damage:
                conn.execute(sa.text(damage))
            with Operations.context(MigrationContext.configure(conn)):
                if damage is None:
                    _migration().upgrade()
                else:
                    with pytest.raises(RuntimeError):
                        _migration().upgrade()


def test_billed_failure_cap_is_atomic_across_parallel_settlements(pg_app):
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    with app.app_context():
        held = [A.reserve(owner, "coach", f"billed-race-{i}") for i in range(WORKERS)]
    def settle(reference):
        return A.finalize(owner, reference, "provider_timeout", provider_billed=True)
    results = race(app, settle, [(h.reference,) for h in held])
    assert sum(getattr(r, "state", None) == "RELEASED" for r in results) == 2
    assert sum(getattr(r, "state", None) == "COMMITTED" for r in results) == WORKERS - 2
    assert scalar(app, "SELECT billed_releases FROM ai_allowance_bucket WHERE feature = 'coach'") == 2
    assert scalar(app, "SELECT used FROM ai_allowance_bucket WHERE feature = 'coach'") == WORKERS - 2
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE feature = 'coach'") == 0


def test_sweep_and_late_finalize_race_consumes_once(pg_app):
    from datetime import datetime, timedelta, timezone
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    now = datetime.now(timezone.utc)
    with app.app_context():
        held = A.reserve(owner, "coach", "sweep-finalize-race", now=now)
    later = now + A.RESERVATION_TTL + timedelta(seconds=1)
    def settle(sweep):
        return (A.expire_abandoned(now=later) if sweep else
                A.finalize(owner, held.reference, "success", now=later))
    results = race(app, settle, [(i % 2 == 0,) for i in range(WORKERS)])
    assert all(isinstance(r, int) or getattr(r, "state", None) == "EXPIRED" for r in results)
    assert scalar(app, "SELECT used FROM ai_allowance_bucket WHERE feature = 'coach'") == 1
    assert scalar(app, "SELECT reserved FROM ai_allowance_bucket WHERE feature = 'coach'") == 0
    assert scalar(app, "SELECT count(*) FROM ai_usage_reservation WHERE state = 'EXPIRED'") == 1


def test_premium_rollover_parallel_requests_and_old_key_replay(pg_app, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from ai_allowance_support import enable_subscriptions, premium_row
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    app.config["AI_ALLOWANCE_POLICY"] = fixture_policy(premium=True)
    enable_subscriptions(app, monkeypatch)
    before = datetime(2026, 10, 18, 20, 59, tzinfo=timezone.utc)
    after = before + timedelta(minutes=2)  # Istanbul Monday rollover
    with app.app_context():
        premium_row(owner)
        held = A.reserve(owner, "coach", "old-period-key", now=before)
        A.finalize(owner, held.reference, "success", now=before)
    def reserve(key):
        return A.reserve(owner, "coach", key, now=after)
    results = race(app, reserve, [(f"new-period-{i}",) for i in range(WORKERS)])
    assert sum(getattr(r, "may_execute", False) for r in results) == 3
    assert results.count("AI_ALLOWANCE_EXHAUSTED") == WORKERS - 3
    with app.app_context():
        replay = A.reserve(owner, "coach", "old-period-key", now=after)
        assert replay.replayed and not replay.may_execute and replay.reference == held.reference
    assert scalar(app, "SELECT count(*) FROM ai_allowance_bucket WHERE scope = 'PREMIUM'") == 2
    assert scalar(app, "SELECT sum(used + reserved) FROM ai_allowance_bucket WHERE scope = 'PREMIUM'") == 4


def test_populated_postgresql_downgrade_preserves_ledger(pg_app):
    from app.extensions import db
    from app.services import ai_allowances as A
    app, (owner, _) = pg_app
    with app.app_context():
        A.reserve(owner, "coach", "downgrade-preserve")
        with db.engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                with pytest.raises(RuntimeError, match="refuses populated"):
                    _migration().downgrade()
            assert conn.execute(sa.text("SELECT count(*) FROM ai_usage_reservation")).scalar() == 1
            assert conn.execute(sa.text("SELECT count(*) FROM ai_starter_grant")).scalar() == 1
