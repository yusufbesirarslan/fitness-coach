"""MON-02-BE-02 authoritative PostgreSQL races; disposable database only.

Real blocking (pg_blocking_pids), SKIP LOCKED claims and rollback durability,
none of which SQLite can prove.
"""
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
import sqlalchemy as sa

from apple_notification_support import Signer
from subscription_support import evidence

pytestmark = pytest.mark.pg_concurrency
if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires disposable PostgreSQL", allow_module_level=True)


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    assert url.startswith(("postgresql://", "postgresql+psycopg2://")), "disposable PostgreSQL required"
    monkeypatch.setenv("DATABASE_URL", url)
    from app import create_app
    from app.extensions import db, limiter
    from app.models import User, AppleAccountAssociation
    app = create_app(); app.config["TESTING"] = True; limiter.enabled = False
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_NOTIFICATIONS_ENABLED=True)
    with app.app_context():
        assert db.engine.dialect.name == "postgresql"
        db.drop_all(); db.create_all()
        users = [User(username=f"pgrecon{i}", email=f"pgrecon{i}@example.invalid") for i in range(2)]
        from subscription_support import adult_account
        for user in users:
            adult_account(user)
        db.session.add_all(users); db.session.commit()
        owners = [(u.id, str(uuid4())) for u in users]
        db.session.add_all([AppleAccountAssociation(user_id=uid, token=token) for uid, token in owners])
        db.session.commit()
    yield app, owners
    with app.app_context():
        db.session.remove(); db.drop_all(); db.engine.dispose()


def _race(app, first_target, second_target, hold_prefix, expect_block=True):
    """Run two threads; the first pauses after a statement starting with hold_prefix."""
    from app.extensions import db
    reached, release = threading.Event(), threading.Event()
    results = {}

    def hold(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name == "first" and statement.startswith(hold_prefix):
            reached.set()
            assert release.wait(10), "test failed to release the first transaction"

    def run(name, target):
        try:
            with app.app_context():
                results[name] = target()
        except BaseException as error:  # noqa: BLE001 - surfaced by the assertions
            results[name] = error

    with app.app_context():
        engine = db.engine
    sa.event.listen(engine, "after_cursor_execute", hold)
    first = threading.Thread(target=run, args=("first", first_target), name="first")
    second = threading.Thread(target=run, args=("second", second_target), name="second")
    blocked = False
    try:
        first.start(); assert reached.wait(5)
        second.start()
        deadline = time.monotonic() + (5 if expect_block else 1)
        while time.monotonic() < deadline:
            with engine.connect() as conn:
                blocked = conn.execute(sa.text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
                    "AND cardinality(pg_blocking_pids(pid)) > 0)")).scalar()
            if blocked or (not expect_block and not second.is_alive()):
                break
            time.sleep(0.02)
        if not expect_block:
            second.join(5)
    finally:
        release.set(); first.join(10); second.join(10)
        sa.event.remove(engine, "after_cursor_execute", hold)
    assert not first.is_alive() and not second.is_alive()
    return results, blocked


@pytest.fixture
def intake(pg_app, monkeypatch):
    from app.services import apple_reconciliation as r
    signer = Signer()
    monkeypatch.setattr(r, "AppleAdapter", lambda _: signer.adapter())
    return signer


def test_concurrent_duplicate_delivery_is_recorded_once(pg_app, intake):
    from app.models import AppleNotificationReceipt, AppleSubscriptionReconciliation
    from app.services import apple_reconciliation as r
    app, owners = pg_app
    signed = intake.notification(owners[0][1])[0]
    results, blocked = _race(app, lambda: r.accept_notification(signed), lambda: r.accept_notification(signed),
                             "INSERT INTO apple_notification_receipt")
    assert blocked, "must observe real unique-key contention"
    assert sorted(results.values()) == ["DUPLICATE", "ENQUEUED"]
    with app.app_context():
        assert AppleNotificationReceipt.query.count() == 1
        assert AppleSubscriptionReconciliation.query.one().generation == 1


def test_concurrent_distinct_notifications_coalesce(pg_app, intake):
    from app.models import AppleNotificationReceipt, AppleSubscriptionReconciliation
    from app.services import apple_reconciliation as r
    app, owners = pg_app
    one, two = (intake.notification(owners[0][1], kind=k)[0] for k in ("DID_RENEW", "DID_CHANGE_RENEWAL_STATUS"))
    results, blocked = _race(app, lambda: r.accept_notification(one), lambda: r.accept_notification(two),
                             "INSERT INTO apple_subscription_reconciliation")
    assert blocked and sorted(results.values()) == ["ENQUEUED", "ENQUEUED"]
    with app.app_context():
        assert AppleNotificationReceipt.query.count() == 2
        assert AppleSubscriptionReconciliation.query.one().generation == 2


def test_intake_commit_failure_leaves_nothing(pg_app, intake, monkeypatch):
    from app.extensions import db
    from app.models import AppleNotificationReceipt, AppleSubscriptionReconciliation
    from app.services import apple_reconciliation as r
    from app.services.apple_subscriptions import SubscriptionError
    app, owners = pg_app
    with app.app_context():
        def fail():
            raise sa.exc.OperationalError("commit", {}, Exception("synthetic"))
        monkeypatch.setattr(db.session, "commit", fail)
        with pytest.raises(SubscriptionError, match="TEMPORARILY_UNAVAILABLE"):
            r.accept_notification(intake.notification(owners[0][1])[0])
    with app.app_context():
        assert AppleNotificationReceipt.query.count() == 0
        assert AppleSubscriptionReconciliation.query.count() == 0


def _due(app, *originals):
    from app.extensions import db
    from app.services import apple_reconciliation as r
    with app.app_context():
        for original in originals:
            r.request_reconciliation("Sandbox", original, None, datetime.now(timezone.utc))
        db.session.commit()


@pytest.mark.parametrize("count", [1, 2])
def test_workers_never_claim_the_same_request(pg_app, count):
    from app.services import apple_reconciliation as r
    app, _ = pg_app
    _due(app, *["100", "200"][:count])
    claim = lambda: r._claim("Sandbox", datetime.now(timezone.utc))  # noqa: E731
    results, blocked = _race(app, claim, claim, "SELECT apple_subscription_reconciliation", expect_block=False)
    assert not blocked, "SKIP LOCKED must not wait"
    claimed = [c for c in results.values() if c is not None]
    assert len(claimed) == count and len({c[0] for c in claimed}) == count


def test_worker_and_client_verify_converge_monotonically(pg_app, monkeypatch):
    from app.models import AppleSubscription, AppleSubscriptionReconciliation
    from app.services import apple_reconciliation as r, subscriptions as s
    app, owners = pg_app
    uid, token = owners[0]
    now = datetime.now(timezone.utc)
    older, newer = now - timedelta(seconds=20), now
    with app.app_context():
        s.persist_verified(uid, evidence(token, transaction_signed_at=older - timedelta(seconds=5),
                                         renewal_signed_at=older - timedelta(seconds=5)), token)
    _due(app, "100")
    worker_view = evidence(token, status=5, revoked_at=older, transaction_signed_at=older, renewal_signed_at=older)
    monkeypatch.setattr(r, "AppleAdapter", lambda _: NS(environment="Sandbox", environments=("Sandbox",), status=lambda *_: worker_view))
    client = lambda: s.persist_verified(uid, evidence(token, transaction_signed_at=newer,  # noqa: E731
                                                      renewal_signed_at=newer), token)
    results, blocked = _race(app, client, lambda: r.run(limit=1, sweep_limit=0), "UPDATE apple_subscription ")
    assert blocked, "worker must wait on the subscription row lock"
    assert results["second"][r.SUPERSEDED] == 1
    with app.app_context():
        row = AppleSubscription.query.one()
        assert row.status == 1 and row.revoked_at is None
        assert s.utc(row.transaction_signed_at) == newer
        assert AppleSubscriptionReconciliation.query.count() == 0


def test_r3_skip_locked_claims_fewest_attempts_first(pg_app):
    """A held retrying row is skipped; a free one with fewer attempts wins over a
    longer-overdue retry, so a repeatedly failing row never starves fresh work."""
    from app.extensions import db
    from app.models import AppleSubscriptionReconciliation as Request
    from app.services import apple_reconciliation as r
    app, _ = pg_app
    now = datetime.now(timezone.utc)
    with app.app_context():
        for original, attempts, due in (("1", 3, now - timedelta(hours=2)), ("2", 5, now - timedelta(hours=3)),
                                        ("3", 0, now)):
            db.session.add(Request(environment="Sandbox", original_transaction_id=original, generation=1,
                                   attempts=attempts, next_attempt_at=due, created_at=now, updated_at=now))
        db.session.commit()
    holder = sa.create_engine(os.environ["PG_TEST_DATABASE_URL"])
    try:
        with holder.connect() as conn, conn.begin():
            conn.execute(sa.text("SELECT 1 FROM apple_subscription_reconciliation "
                                 "WHERE original_transaction_id = '3' FOR UPDATE"))
            with app.app_context():
                assert r._claim("Sandbox", datetime.now(timezone.utc))[0] == "1"  # 3 held: skipped
        with app.app_context():
            assert r._claim("Sandbox", datetime.now(timezone.utc))[0] == "3"
            assert r._claim("Sandbox", datetime.now(timezone.utc))[0] == "2"
    finally:
        holder.dispose()


def test_r3_receipt_purge_is_bounded_and_concurrency_safe(pg_app):
    from app.extensions import db
    from app.models import AppleNotificationReceipt
    from app.services import apple_reconciliation as r
    app, _ = pg_app
    now = datetime.now(timezone.utc)
    old = now - r.RECEIPT_RETENTION - timedelta(days=1)
    with app.app_context():
        for at in [old] * 5 + [now]:
            db.session.add(AppleNotificationReceipt(
                notification_uuid=str(uuid4()), environment="Sandbox", notification_type="DID_RENEW",
                disposition="ENQUEUED", signed_at=at, received_at=at))
        db.session.commit()
        assert r.purge_receipts("Sandbox", now, limit=2) == 2
    results, _ = _race(app, lambda: r.purge_receipts("Sandbox", now), lambda: r.purge_receipts("Sandbox", now),
                       "DELETE FROM apple_notification_receipt", expect_block=False)
    # Overlapping passes never fail and never delete a row twice.
    assert all(isinstance(v, int) for v in results.values()) and sum(results.values()) == 3, results
    with app.app_context():
        assert [x.received_at > old for x in AppleNotificationReceipt.query] == [True]


def test_postgres_fresh_reentrant_and_unsafe_migration(pg_app):
    from app.extensions import db
    from test_apple_lifecycle_migration import migration, TABLES
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    app, _ = pg_app
    with app.app_context(), db.engine.begin() as conn:
        before = conn.execute(sa.text('SELECT id, is_premium FROM "user" ORDER BY id')).all()
        with Operations.context(MigrationContext.configure(conn)):
            migration().upgrade()  # create_all-first tables with PostgreSQL deparse forms
            for table in TABLES:
                conn.execute(sa.text(f"DROP TABLE {table}"))
            migration().upgrade(); migration().upgrade()  # fresh, then reentrant
        conn.execute(sa.text("ALTER TABLE apple_subscription_reconciliation DROP CONSTRAINT "
                             "ck_apple_reconciliation_environment"))
        conn.execute(sa.text("ALTER TABLE apple_subscription_reconciliation ADD CONSTRAINT "
                             "ck_apple_reconciliation_environment CHECK (environment IN ('Sandbox', 'Production')) "
                             "NOT VALID"))
        with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError):
            migration().upgrade()
        conn.execute(sa.text("ALTER TABLE apple_subscription_reconciliation VALIDATE CONSTRAINT "
                             "ck_apple_reconciliation_environment"))
        conn.execute(sa.text("CREATE UNIQUE INDEX extra_unique ON apple_notification_receipt (disposition)"))
        with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError):
            migration().upgrade()
        assert conn.execute(sa.text('SELECT id, is_premium FROM "user" ORDER BY id')).all() == before
