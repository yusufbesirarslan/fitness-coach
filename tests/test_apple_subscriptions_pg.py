"""Authoritative PostgreSQL ownership/locking tests; disposable database only."""
import os
import threading
import time
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa

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
    with app.app_context():
        assert db.engine.dialect.name == "postgresql"
        db.drop_all(); db.create_all()
        users = [User(username=f"pgapple{i}", email=f"pgapple{i}@example.invalid") for i in range(2)]
        db.session.add_all(users); db.session.commit()
        owners = [(u.id, str(uuid4())) for u in users]
        db.session.add_all([AppleAccountAssociation(user_id=uid, token=token) for uid, token in owners])
        db.session.commit()
    yield app, owners
    with app.app_context():
        db.session.remove(); db.drop_all(); db.engine.dispose()


@pytest.mark.parametrize("cross_owner", [False, True])
def test_real_contention_and_single_owner(pg_app, cross_owner):
    from app.extensions import db
    from app.models import AppleSubscription
    from app.services.subscriptions import persist_verified
    from app.services.apple_subscriptions import SubscriptionError
    app, owners = pg_app
    inserted, release = threading.Event(), threading.Event()
    results, failures = [], []
    ev = evidence(owners[0][1])
    def hold_insert(conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name == "apple-first" and statement.startswith("INSERT INTO apple_subscription"):
            inserted.set()
            assert release.wait(10), "test failed to release first transaction"
    with app.app_context():
        engine = db.engine
    sa.event.listen(engine, "after_cursor_execute", hold_insert)
    def claim(owner, token):
        try:
            with app.app_context():
                results.append(persist_verified(owner, replace(ev, account_token=token), token))
        except SubscriptionError as error:
            failures.append(error.code)
        except BaseException as error:
            failures.append(error)
    first = threading.Thread(target=claim, args=owners[0], name="apple-first")
    second = threading.Thread(target=claim, args=owners[int(cross_owner)], name="apple-second")
    try:
        first.start(); assert inserted.wait(5)
        second.start()
        deadline = time.monotonic() + 5
        blocked = False
        while time.monotonic() < deadline:
            with engine.connect() as conn:
                blocked = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                               "WHERE datname = current_database() "
                                               "AND cardinality(pg_blocking_pids(pid)) > 0)")).scalar()
            if blocked:
                break
            time.sleep(0.02)
        assert blocked, "must observe actual PostgreSQL lock/unique contention"
    finally:
        release.set(); first.join(10)
        if second.ident is not None:
            second.join(10)
        sa.event.remove(engine, "after_cursor_execute", hold_insert)
    assert not first.is_alive() and not second.is_alive()
    assert len(results) == (1 if cross_owner else 2)
    assert failures == (["SUBSCRIPTION_OWNERSHIP_CONFLICT"] if cross_owner else [])
    with app.app_context():
        assert AppleSubscription.query.count() == 1
        assert AppleSubscription.query.one().user_id == owners[0][0]


def test_postgres_constraints_migration_and_rollback(pg_app):
    from app.extensions import db
    from app.models import AppleSubscription
    from app.services.subscriptions import persist_verified
    from app.services.apple_subscriptions import SubscriptionError
    from test_apple_subscription_migration import migration
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    app, owners = pg_app
    with app.app_context():
        with db.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            migration().upgrade()
        ev = evidence(owners[0][1])
        persist_verified(*owners[0][:1], ev, owners[0][1])
        with pytest.raises(SubscriptionError):
            persist_verified(owners[1][0], replace(ev, account_token=owners[1][1]), owners[1][1])
        assert AppleSubscription.query.one().user_id == owners[0][0]
        persist_verified(owners[0][0], replace(ev, environment="Production"), owners[0][1])
        assert AppleSubscription.query.count() == 2
        # Direct SQL cannot bypass the durable original-identity UNIQUE.
        with pytest.raises(sa.exc.IntegrityError), db.engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO apple_subscription SELECT id + 1000, user_id, environment, "
                                 "original_transaction_id, latest_transaction_id, product_id, subscription_group_id, "
                                 "status, expires_at, revoked_at, grace_expires_at, auto_renew, transaction_signed_at, "
                                 "renewal_signed_at, last_verified_at, created_at, updated_at FROM apple_subscription LIMIT 1"))
