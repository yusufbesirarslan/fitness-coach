"""MON-BE-APPLE-DUAL-ENV-REVIEW-01 authoritative PostgreSQL races; disposable database only.

Sandbox (App Review / TestFlight) records verified through the Production-first
fallback keep the BE-01 ownership guarantees: one owner per (environment,
original transaction), no contention with the other environment's identity,
and account deletion either wins before persistence or cascades after it.
"""
import os
import threading
import time
from uuid import uuid4

import pytest
import sqlalchemy as sa

from subscription_support import evidence
from test_apple_dual_environment import Dual

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
    app.config.update(APPLE_SUBSCRIPTIONS_ENABLED=True, APPLE_SUBSCRIPTION_ENVIRONMENT="Production",
                      APPLE_SANDBOX_FALLBACK_ENABLED=True)
    with app.app_context():
        assert db.engine.dialect.name == "postgresql"
        db.drop_all(); db.create_all()
        users = [User(username=f"pgdual{i}", email=f"pgdual{i}@example.invalid") for i in range(2)]
        db.session.add_all(users); db.session.commit()
        owners = [(u.id, str(uuid4())) for u in users]
        db.session.add_all([AppleAccountAssociation(user_id=uid, token=token) for uid, token in owners])
        db.session.commit()
    yield app, owners
    with app.app_context():
        db.session.remove(); db.drop_all(); db.engine.dispose()


def _blocked(engine, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with engine.connect() as conn:
            if conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname = current_database() "
                                    "AND cardinality(pg_blocking_pids(pid)) > 0)")).scalar():
                return True
        time.sleep(0.02)
    return False


class Held:
    """Pause the named thread right after its apple_subscription INSERT."""

    def __init__(self, engine, thread_name):
        self.engine, self.name, self.threads = engine, thread_name, []
        self.inserted, self.release = threading.Event(), threading.Event()

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        if threading.current_thread().name == self.name and statement.startswith("INSERT INTO apple_subscription"):
            self.inserted.set()
            assert self.release.wait(10), "test failed to release the held transaction"

    def add(self, thread):
        self.threads.append(thread)
        return thread

    def __enter__(self):
        sa.event.listen(self.engine, "after_cursor_execute", self)
        return self

    def __exit__(self, *exc):
        # Join before removing: a listener must not be removed while it runs.
        self.release.set()
        for thread in self.threads:
            thread.join(10)
            assert not thread.is_alive()
        sa.event.remove(self.engine, "after_cursor_execute", self)


def _run(app, results, name, fn, *args):
    def target():
        try:
            with app.app_context():
                results[name] = fn(*args)
        except Exception as error:  # noqa: BLE001 - recorded for the assertion
            results[name] = getattr(error, "code", error)
    thread = threading.Thread(target=target, name=name)
    thread.start()
    return thread


def test_review_purchase_contention_has_one_owner(pg_app):
    """Two accounts persist the same verified Sandbox identity: UNIQUE arbitrates."""
    from app.extensions import db
    from app.models import AppleSubscription
    from app.services.subscriptions import persist_verified
    app, ((a, a_token), (b, b_token)) = pg_app
    with app.app_context():
        engine = db.engine
        # The other environment's record with the same numeric identity belongs to B.
        persist_verified(b, evidence(b_token, environment="Production"), b_token)
    results = {}
    with Held(engine, "sandbox-a") as held:
        first = held.add(_run(app, results, "sandbox-a", persist_verified, a, evidence(a_token), a_token))
        assert held.inserted.wait(5)
        second = held.add(_run(app, results, "sandbox-b", persist_verified, b, evidence(b_token), b_token))
        assert _blocked(engine), "must observe actual PostgreSQL unique contention"
    first.join(10); second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert results["sandbox-a"]["effective_premium"] is True
    assert results["sandbox-b"] == "SUBSCRIPTION_OWNERSHIP_CONFLICT"
    with app.app_context():
        rows = {(r.environment, r.original_transaction_id): r.user_id for r in AppleSubscription.query}
        assert rows == {("Production", "100"): b, ("Sandbox", "100"): a}


def test_other_environment_identity_never_waits(pg_app):
    """A held Sandbox insert does not block the Production identity of another owner."""
    from app.extensions import db
    from app.models import AppleSubscription
    from app.services.subscriptions import persist_verified
    app, ((a, a_token), (b, b_token)) = pg_app
    with app.app_context():
        engine = db.engine
    results = {}
    with Held(engine, "sandbox-a") as held:
        first = held.add(_run(app, results, "sandbox-a", persist_verified, a, evidence(a_token), a_token))
        assert held.inserted.wait(5)
        second = held.add(_run(app, results, "production-b", persist_verified, b,
                               evidence(b_token, environment="Production"), b_token))
        second.join(5)
        assert not second.is_alive(), "Production identity waited on a Sandbox insert"
        assert results["production-b"]["effective_premium"] is True
    first.join(10)
    assert results["sandbox-a"]["effective_premium"] is True
    with app.app_context():
        assert {(r.environment, r.user_id) for r in AppleSubscription.query} == {("Sandbox", a), ("Production", b)}


def test_concurrent_restores_through_fallback_are_idempotent(pg_app, monkeypatch):
    from app.extensions import db
    from app.models import AppleSubscription
    from app.services import subscriptions
    app, ((a, a_token), _) = pg_app
    dual = Dual(a_token)
    monkeypatch.setattr(subscriptions, "AppleAdapter", lambda _: dual.p)
    with app.app_context():
        engine = db.engine
    results = {}
    with Held(engine, "restore-1") as held:
        first = held.add(_run(app, results, "restore-1", subscriptions.verify_purchase, a, "201"))
        assert held.inserted.wait(5)
        second = held.add(_run(app, results, "restore-2", subscriptions.verify_purchase, a, "201"))
        assert _blocked(engine), "second restore must wait on the owner/identity lock"
    first.join(10); second.join(10)
    assert results["restore-1"]["effective_premium"] and results["restore-2"]["effective_premium"]
    with app.app_context():
        row = AppleSubscription.query.one()
        assert (row.environment, row.original_transaction_id, row.user_id) == ("Sandbox", "200", a)
    # Both lookups found the transaction only after Production's exact 4040010.
    assert dual.p.client.calls == [("info", "201"), ("info", "201")]
    assert [c for c in dual.s.client.calls if c[0] == "info"] == [("info", "201"), ("info", "201")]


def test_deletion_during_apple_io_wins_before_persistence(pg_app, monkeypatch):
    from app.extensions import db
    from app.models import AppleSubscription
    from app.services import subscriptions
    from app.services.apple_subscriptions import SubscriptionError
    app, ((a, a_token), _) = pg_app
    dual = Dual(a_token)
    monkeypatch.setattr(subscriptions, "AppleAdapter", lambda _: dual.p)
    lookup = dual.s.client.get_all_subscription_statuses
    with app.app_context():
        engine = db.engine

    def delete_then_answer(original_id):
        with engine.begin() as conn:
            conn.execute(sa.text('DELETE FROM "user" WHERE id = :id'), {"id": a})
        return lookup(original_id)
    dual.s.client.get_all_subscription_statuses = delete_then_answer
    with app.app_context():
        with pytest.raises(SubscriptionError) as error:
            subscriptions.verify_purchase(a, "201")
        assert (error.value.code, error.value.status) == ("SUBSCRIPTION_ACCOUNT_UNAVAILABLE", 409)
        assert AppleSubscription.query.count() == 0


def test_deletion_after_persistence_lock_cascades(pg_app, monkeypatch):
    from app.extensions import db
    from app.models import AppleAccountAssociation, AppleSubscription
    from app.services import subscriptions
    app, ((a, a_token), _) = pg_app
    dual = Dual(a_token)
    monkeypatch.setattr(subscriptions, "AppleAdapter", lambda _: dual.p)
    with app.app_context():
        engine = db.engine
    results, deleted = {}, threading.Event()

    def delete():
        with engine.begin() as conn:
            conn.execute(sa.text('DELETE FROM "user" WHERE id = :id'), {"id": a})
        deleted.set()
    with Held(engine, "verify") as held:
        verify = held.add(_run(app, results, "verify", subscriptions.verify_purchase, a, "201"))
        assert held.inserted.wait(5)
        deleter = held.add(threading.Thread(target=delete))
        deleter.start()
        assert _blocked(engine), "deletion must wait for the owner lock"
        assert not deleted.is_set()
    verify.join(10); deleter.join(10)
    assert results["verify"]["effective_premium"] is True and deleted.is_set()
    with app.app_context():
        assert AppleSubscription.query.count() == 0
        assert AppleAccountAssociation.query.filter_by(user_id=a).count() == 0
