"""Authoritative PostgreSQL ownership/locking tests; disposable database only."""
import os
import threading
import time
from dataclasses import replace
from uuid import uuid4

import pytest
import sqlalchemy as sa

from subscription_support import evidence
from test_apple_subscription_migration import UNSAFE_CHECKS

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


@pytest.mark.parametrize("domain,predicate", UNSAFE_CHECKS)
def test_postgres_rejects_unsafe_named_checks(pg_app, domain, predicate):
    from app.extensions import db
    from test_apple_subscription_migration import unsafe_schema
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    app, _ = pg_app
    with app.app_context(), db.engine.begin() as conn:
        before = conn.execute(sa.text('SELECT id, is_premium FROM "user" ORDER BY id')).all()
        conn.execute(sa.text('DROP TABLE apple_subscription'))
        conn.execute(sa.text('DROP TABLE apple_account_association'))
        m = unsafe_schema(conn, domain, predicate)
        with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError):
            m.upgrade()
        assert conn.execute(sa.text('SELECT id, is_premium FROM "user" ORDER BY id')).all() == before


def test_postgres_fresh_and_reentrant_migration(pg_app):
    from app.extensions import db
    from test_apple_subscription_migration import migration
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    app, _ = pg_app
    with app.app_context(), db.engine.begin() as conn:
        conn.execute(sa.text('UPDATE "user" SET is_premium = true WHERE id = (SELECT min(id) FROM "user")'))
        before = conn.execute(sa.text('SELECT id, is_premium FROM "user" ORDER BY id')).all()
        with Operations.context(MigrationContext.configure(conn)):
            migration().upgrade()  # real create_all-first predicates
            migration().upgrade()
            conn.execute(sa.text('DROP TABLE apple_subscription'))
            conn.execute(sa.text('DROP TABLE apple_account_association'))
            migration().upgrade()  # fresh tables
            migration().upgrade()
        assert conn.execute(sa.text('SELECT id, is_premium FROM "user" ORDER BY id')).all() == before


def test_postgres_apple_io_has_no_transaction_or_owner_locks(pg_app, monkeypatch):
    from app.extensions import db
    from app.services import subscriptions, ai_gate
    from subscription_support import adapter
    app, owners = pg_app
    a, *_ = adapter(owners[0][1])
    app.config["APPLE_SUBSCRIPTIONS_ENABLED"] = True
    monkeypatch.setattr(subscriptions, "AppleAdapter", lambda _: a)
    baseline = ai_gate.capacity_snapshot()
    calls = []
    with app.app_context():
        for obj, name in [(a.client, "get_transaction_info"),
                          (a.client, "get_all_subscription_statuses"),
                          (a.verifier, "verify_and_decode_signed_transaction"),
                          (a.verifier, "verify_and_decode_renewal_info")]:
            original = getattr(obj, name)
            def check(*args, _fn=original, _name=name):
                assert not db.session().in_transaction()
                assert ai_gate.capacity_snapshot()["ai_active"] == baseline["ai_active"] + 1
                # Actual PG session has no outstanding transaction or row lock.
                with db.engine.connect() as conn:
                    assert not conn.execute(sa.text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                        "AND pid <> pg_backend_pid() AND xact_start IS NOT NULL)"
                    )).scalar()
                calls.append(_name)
                return _fn(*args)
            monkeypatch.setattr(obj, name, check)
        original_owner = subscriptions._owner
        def owner(*args):
            assert ai_gate.capacity_snapshot() == baseline
            return original_owner(*args)
        monkeypatch.setattr(subscriptions, "_owner", owner)
        assert subscriptions.verify_purchase(owners[0][0], "101")["effective_premium"] is True
    assert len(calls) == 5
    assert ai_gate.capacity_snapshot() == baseline


CHECK_DOMAINS = [
    ("status", "status IN (1, 2, 3, 4, 5)", 42),
    ("environment", "environment IN ('Sandbox', 'Production')", "Other"),
]


def _catalog_check_state(conn, domain):
    return conn.execute(sa.text(
        "SELECT convalidated FROM pg_catalog.pg_constraint "
        "WHERE conrelid = 'apple_subscription'::regclass "
        "AND contype = 'c' AND conname = :name"
    ), {"name": f"ck_apple_subscription_{domain}"}).scalar_one()


def _seed_migration_row(conn, owner):
    conn.execute(sa.text(
        "INSERT INTO apple_subscription (id,user_id,environment,original_transaction_id,"
        "latest_transaction_id,product_id,subscription_group_id,status,expires_at,auto_renew,"
        "transaction_signed_at,renewal_signed_at,last_verified_at,created_at,updated_at) "
        "VALUES (1,:owner,'Sandbox','100','101','monthly','group',1,"
        "now()+interval '1 day',true,now(),now(),now(),now(),now())"
    ), {"owner": owner})


def _replace_not_valid_check(conn, domain, predicate):
    # Identifiers/predicates come exclusively from the fixed test parameter set.
    assert (domain, predicate) in [(d, p) for d, p, _ in CHECK_DOMAINS]
    conn.execute(sa.text(f'ALTER TABLE apple_subscription DROP CONSTRAINT ck_apple_subscription_{domain}'))
    conn.execute(sa.text(f'ALTER TABLE apple_subscription ADD CONSTRAINT ck_apple_subscription_{domain} '
                         f'CHECK ({predicate}) NOT VALID'))
    assert _catalog_check_state(conn, domain) is False


def _assert_migration_rejected(conn):
    from test_apple_subscription_migration import migration
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    with Operations.context(MigrationContext.configure(conn)), pytest.raises(RuntimeError) as error:
        migration().upgrade()
    assert str(error.value) == "Apple subscription table has incompatible check constraints"


@pytest.mark.parametrize("domain,predicate,bad_value", CHECK_DOMAINS)
@pytest.mark.parametrize("invalid_history", [True, False], ids=["invalid-history", "valid-history"])
def test_postgres_rejects_not_valid_checks_preserving_rows(pg_app, domain, predicate, bad_value, invalid_history):
    from app.extensions import db
    app, owners = pg_app
    with app.app_context(), db.engine.begin() as conn:
        _seed_migration_row(conn, owners[0][0])
        conn.execute(sa.text(f'ALTER TABLE apple_subscription DROP CONSTRAINT ck_apple_subscription_{domain}'))
        if invalid_history:
            conn.execute(sa.text(f'UPDATE apple_subscription SET {domain} = :value'), {"value": bad_value})
        conn.execute(sa.text(f'ALTER TABLE apple_subscription ADD CONSTRAINT ck_apple_subscription_{domain} '
                             f'CHECK ({predicate}) NOT VALID'))
        assert _catalog_check_state(conn, domain) is False
        before = conn.execute(sa.text('SELECT * FROM apple_subscription')).all()
        users = conn.execute(sa.text('SELECT id, is_premium FROM "user" ORDER BY id')).all()
        _assert_migration_rejected(conn)
        assert _catalog_check_state(conn, domain) is False  # migration never auto-validates
        assert conn.execute(sa.text('SELECT * FROM apple_subscription')).all() == before
        assert conn.execute(sa.text('SELECT id, is_premium FROM "user" ORDER BY id')).all() == users


@pytest.mark.parametrize("domain,predicate,_", CHECK_DOMAINS)
def test_postgres_explicit_fixture_validation_changes_acceptance(pg_app, domain, predicate, _):
    from app.extensions import db
    from test_apple_subscription_migration import migration
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    app, owners = pg_app
    with app.app_context(), db.engine.begin() as conn:
        _seed_migration_row(conn, owners[0][0])
        before = conn.execute(sa.text('SELECT * FROM apple_subscription')).all()
        _replace_not_valid_check(conn, domain, predicate)
        _assert_migration_rejected(conn)
        # Explicit repair is permitted only within this disposable fixture.
        conn.execute(sa.text(f'ALTER TABLE apple_subscription VALIDATE CONSTRAINT ck_apple_subscription_{domain}'))
        assert all(_catalog_check_state(conn, d) is True for d, _, _ in CHECK_DOMAINS)
        with Operations.context(MigrationContext.configure(conn)):
            migration().upgrade()
            migration().upgrade()
        assert conn.execute(sa.text('SELECT * FROM apple_subscription')).all() == before


@pytest.mark.parametrize("domain,predicate,_", CHECK_DOMAINS)
def test_postgres_catalog_uses_resolved_table_identity(pg_app, domain, predicate, _):
    from app.extensions import db
    from test_apple_subscription_migration import migration
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    app, _ = pg_app
    with app.app_context(), db.engine.begin() as conn:
        # Same-named, validated constraints on a different table and on a
        # same-named table in a different schema must not certify this table.
        conn.execute(sa.text(f'CREATE TABLE unrelated (status INTEGER, environment VARCHAR(10), '
                             f'CONSTRAINT ck_apple_subscription_{domain} CHECK ({predicate}))'))
        conn.execute(sa.text('CREATE SCHEMA "check decoy"'))
        conn.execute(sa.text('CREATE SCHEMA "check target"'))
        conn.execute(sa.text('CREATE SCHEMA "check empty"'))
        conn.execute(sa.text('CREATE TABLE "check decoy".apple_subscription '
                             '(LIKE public.apple_subscription INCLUDING ALL)'))
        conn.execute(sa.text('ALTER TABLE public.apple_subscription SET SCHEMA "check target"'))
        # The intended table is in the second searched schema, not current_schema().
        conn.execute(sa.text('SET LOCAL search_path TO "check empty", "check target", "check decoy", public'))
        _replace_not_valid_check(conn, domain, predicate)
        _assert_migration_rejected(conn)
        conn.execute(sa.text(f'ALTER TABLE apple_subscription VALIDATE CONSTRAINT ck_apple_subscription_{domain}'))
        with Operations.context(MigrationContext.configure(conn)):
            migration().upgrade()
        # Roll back all schema movement/decoys so pg_app cleanup stays in public.
        conn.rollback()


@pytest.mark.parametrize("catalog_result", [
    [],
    [("ck_apple_subscription_environment", True), ("ck_apple_subscription_status", None)],
    "unavailable",
], ids=["missing", "unknown", "unavailable"])
def test_postgres_unknown_catalog_state_fails_closed(pg_app, monkeypatch, catalog_result):
    from app.extensions import db
    app, _ = pg_app
    with app.app_context(), db.engine.begin() as conn:
        execute = conn.execute
        def unavailable(statement, *args, **kwargs):
            if "SELECT conname, convalidated FROM pg_catalog.pg_constraint" in str(statement):
                if catalog_result == "unavailable":
                    raise sa.exc.SQLAlchemyError("private database detail")
                class Result:
                    def all(self):
                        return catalog_result
                return Result()
            return execute(statement, *args, **kwargs)
        monkeypatch.setattr(conn, "execute", unavailable)
        _assert_migration_rejected(conn)


def test_postgres_unexpected_check_reflection_fails_closed(pg_app, monkeypatch):
    from app.extensions import db
    app, _ = pg_app
    original = sa.engine.reflection.Inspector.get_check_constraints
    def unexpected(inspector, table_name, *args, **kwargs):
        checks = original(inspector, table_name, *args, **kwargs)
        return [dict(c, sqltext="unrecognized reflection") for c in checks]
    monkeypatch.setattr(sa.engine.reflection.Inspector, "get_check_constraints", unexpected)
    with app.app_context(), db.engine.begin() as conn:
        assert all(_catalog_check_state(conn, d) is True for d, _, _ in CHECK_DOMAINS)
        _assert_migration_rejected(conn)
