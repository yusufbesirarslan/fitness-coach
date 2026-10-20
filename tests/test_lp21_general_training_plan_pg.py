"""Opt-in PostgreSQL races for the LP21 non-AI general first plan.

Interleavings are forced, not hoped for: an external transaction holds the
owner row (``FOR NO KEY UPDATE``, the ``lock_plan_owner`` mode) while workers
start, and the test waits until PostgreSQL reports them blocked on a lock
before releasing it. SQLite admits one writer at a time and can prove none of
this.
"""
import os
import threading
import time
from types import SimpleNamespace

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )

BODY = {"template": "general_full_body_v1", "gun_sayisi": 3, "ekipman": "ev"}


@pytest.fixture
def pg_general_app():
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    probe = sa.create_engine(url)
    try:
        with probe.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
    except Exception:
        pytest.skip("disposable PostgreSQL database is not reachable")
    finally:
        probe.dispose()

    from flask import Flask
    from app.extensions import db
    from app.models import User

    app = Flask("lp21-general-plan-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-general-plan-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        users = [
            User(username=f"pg-general-{index}",
                 email=f"pg-general-{index}@example.invalid",
                 cognito_sub=f"pg-general-sub-{index}")
            for index in range(2)
        ]
        db.session.add_all(users)
        db.session.commit()
        user_ids = [user.id for user in users]
    try:
        yield app, user_ids
    finally:
        with app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def _general(app, user_id, key, outcomes, index, body=BODY):
    from app.extensions import db
    from app.services.general_training_plan import (
        GeneralPlanSuperseded, create_general_plan, parse_general_request,
    )
    from app.services.mobile_training_generation import (
        ExistingPlanRefused, GenerationInProgress, IdempotencyConflict,
    )
    try:
        with app.app_context():
            try:
                result = create_general_plan(
                    SimpleNamespace(id=user_id, language="tr"),
                    parse_general_request(body), key)
                outcomes[index] = ("ok", result.replayed, result.plan.lineage_id)
            except ExistingPlanRefused:
                outcomes[index] = ("existing",)
            except IdempotencyConflict:
                outcomes[index] = ("conflict",)
            except GenerationInProgress:
                outcomes[index] = ("in_progress",)
            except GeneralPlanSuperseded:
                outcomes[index] = ("superseded",)
            finally:
                db.session.remove()
    except Exception as error:  # pragma: no cover - surfaced by assertions
        outcomes[index] = ("unexpected", type(error).__name__, str(error))


def _browser_create(app, user_id, outcomes, index):
    from app.extensions import db
    from app.services.plan_replacement import PlanExpectation, replace_training_plan
    try:
        with app.app_context():
            try:
                replace_training_plan(
                    user_id, PlanExpectation(absent=True),
                    plan_data='{"program": []}', score=8)
                outcomes[index] = ("browser_ok",)
            except Exception as error:
                outcomes[index] = ("browser_refused", type(error).__name__)
            finally:
                db.session.remove()
    except Exception as error:  # pragma: no cover
        outcomes[index] = ("unexpected", type(error).__name__, str(error))


class _OwnerLock:
    """An external transaction holding the owner row the way creators do."""

    def __init__(self, app, user_id):
        from app.extensions import db
        with app.app_context():
            self.engine = db.engine
        self.connection = self.engine.connect()
        self.transaction = self.connection.begin()
        self.connection.execute(
            sa.text('SELECT id FROM "user" WHERE id = :id FOR NO KEY UPDATE'),
            {"id": user_id})

    def insert_plan(self, user_id):
        self.connection.execute(sa.text(
            "INSERT INTO training_plan (user_id, plan_data, lineage_id, "
            "mutation_version, created_at) VALUES (:user, '{}', 'external-lineage', 0, now())"),
            {"user": user_id})

    def waiters(self):
        with self.engine.connect() as probe:
            return probe.execute(sa.text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )).scalar_one()

    def wait_for_waiters(self, count, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.waiters() >= count:
                return
            time.sleep(0.05)
        raise AssertionError(f"expected {count} lock waiters, saw {self.waiters()}")

    def release(self, commit=False):
        if commit:
            self.transaction.commit()
        else:
            self.transaction.rollback()
        self.connection.close()


def _threads(targets):
    threads = [threading.Thread(target=target, args=args, daemon=True)
               for target, args in targets]
    for thread in threads:
        thread.start()
    return threads


def _join(threads):
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive(), "worker did not finish"


def _rows(app, user_id):
    from app.models import TrainingPlan, TrainingPlanGenerationOperation
    with app.app_context():
        plans = TrainingPlan.query.filter_by(user_id=user_id).all()
        receipts = TrainingPlanGenerationOperation.query.filter_by(user_id=user_id).all()
        return plans, receipts


def test_same_key_concurrent_requests_create_one_plan_and_replay_it(pg_general_app):
    app, (user_id, _other) = pg_general_app
    lock = _OwnerLock(app, user_id)
    outcomes = {}
    threads = _threads([(_general, (app, user_id, "pg-general-same", outcomes, i))
                        for i in range(3)])
    # One worker waits on the owner lock, the others on its receipt's key.
    lock.wait_for_waiters(3)
    lock.release()
    _join(threads)

    plans, receipts = _rows(app, user_id)
    assert len(plans) == 1 and len(receipts) == 1
    assert sorted((o[0], o[1]) for o in outcomes.values()) == [
        ("ok", False), ("ok", True), ("ok", True)]
    assert {o[2] for o in outcomes.values()} == {plans[0].lineage_id}
    assert receipts[0].training_plan_id == plans[0].id


def test_same_key_with_different_intent_conflicts_under_contention(pg_general_app):
    app, (user_id, _other) = pg_general_app
    lock = _OwnerLock(app, user_id)
    outcomes = {}
    threads = _threads([
        (_general, (app, user_id, "pg-general-intent", outcomes, 0)),
        (_general, (app, user_id, "pg-general-intent", outcomes, 1,
                    {**BODY, "ekipman": "spor_salonu"})),
    ])
    lock.wait_for_waiters(2)
    lock.release()
    _join(threads)

    assert sorted(o[0] for o in outcomes.values()) == ["conflict", "ok"]
    plans, receipts = _rows(app, user_id)
    assert len(plans) == 1 and len(receipts) == 1


def test_different_keys_concurrently_create_exactly_one_plan(pg_general_app):
    app, (user_id, _other) = pg_general_app
    lock = _OwnerLock(app, user_id)
    outcomes = {}
    threads = _threads([(_general, (app, user_id, f"pg-general-key-{i}", outcomes, i))
                        for i in range(4)])
    lock.wait_for_waiters(4)
    lock.release()
    _join(threads)

    assert sorted(o[0] for o in outcomes.values()) == [
        "existing", "existing", "existing", "ok"]
    plans, receipts = _rows(app, user_id)
    # Refusals rolled their receipts back: the winner's is the only one.
    assert len(plans) == 1 and len(receipts) == 1


def test_general_and_browser_create_serialize_on_the_owner_lock(pg_general_app):
    app, (user_id, _other) = pg_general_app
    lock = _OwnerLock(app, user_id)
    outcomes = {}
    threads = _threads([
        (_general, (app, user_id, "pg-general-browser", outcomes, 0)),
        (_browser_create, (app, user_id, outcomes, 1)),
    ])
    lock.wait_for_waiters(2)
    lock.release()
    _join(threads)

    plans, _receipts = _rows(app, user_id)
    assert len(plans) == 1
    kinds = sorted(o[0] for o in outcomes.values())
    assert kinds in (["browser_refused", "ok"], ["browser_ok", "existing"]), outcomes


def test_absence_is_decided_under_the_owner_lock_not_before_it(pg_general_app):
    """The holder creates a plan, then commits: the waiter must refuse.

    A writer that checked for an active plan before locking would have seen
    none and inserted a second plan once the lock was released.
    """
    app, (user_id, _other) = pg_general_app
    lock = _OwnerLock(app, user_id)
    lock.insert_plan(user_id)
    outcomes = {}
    threads = _threads([(_general, (app, user_id, "pg-general-probe", outcomes, 0))])
    lock.wait_for_waiters(1)
    lock.release(commit=True)
    _join(threads)

    assert outcomes[0] == ("existing",)
    plans, receipts = _rows(app, user_id)
    assert [plan.lineage_id for plan in plans] == ["external-lineage"]
    assert receipts == []


def test_failure_after_flush_rolls_back_plan_and_receipt(pg_general_app, monkeypatch):
    from app.extensions import db
    from app.services.mobile_training_generation import store
    app, (user_id, _other) = pg_general_app
    original = store._insert_plan

    def failing(**kwargs):
        original(**kwargs)
        db.session.flush()
        raise RuntimeError("injected after plan and receipt flush")

    monkeypatch.setattr(store, "_insert_plan", failing)
    outcomes = {}
    _general(app, user_id, "pg-general-rollback", outcomes, 0)
    assert outcomes[0][0] == "unexpected"
    assert outcomes[0][1] == "GenerationPersistenceUnavailable"
    assert _rows(app, user_id) == ([], [])

    monkeypatch.setattr(store, "_insert_plan", original)
    _general(app, user_id, "pg-general-rollback", outcomes, 1)
    assert outcomes[1][:2] == ("ok", False)


def test_owners_are_independent_under_the_same_key(pg_general_app):
    app, user_ids = pg_general_app
    outcomes = {}
    threads = _threads([(_general, (app, user_id, "pg-general-shared", outcomes, i))
                        for i, user_id in enumerate(user_ids)])
    _join(threads)

    assert [outcomes[i][:2] for i in range(2)] == [("ok", False), ("ok", False)]
    for user_id in user_ids:
        plans, receipts = _rows(app, user_id)
        assert len(plans) == 1 and len(receipts) == 1


def test_plan_origin_is_read_from_the_ledger_on_postgresql(pg_general_app):
    """The read-side origin query on real PostgreSQL: general, then replaced."""
    from app.extensions import db
    from app.models import TrainingPlan
    from app.services.plan_replacement import PlanExpectation, replace_training_plan
    from app.services.training_plan_origin import resolve_plan_origin

    app, (user_id, other_id) = pg_general_app
    outcomes = {}
    _general(app, user_id, "pg-general-origin", outcomes, 0)
    assert outcomes[0][:2] == ("ok", False)

    with app.app_context():
        try:
            plan = TrainingPlan.query.filter_by(user_id=user_id).one()
            assert resolve_plan_origin(plan, user_id) == {
                "kind": "general_template", "template": BODY["template"],
                "personalized": False}
            # Owner scope is in the statement: another owner never matches.
            assert resolve_plan_origin(plan, other_id)["kind"] == "unknown"
            lineage, version = plan.lineage_id, plan.mutation_version
        finally:
            db.session.remove()
        try:
            replace_training_plan(
                user_id, PlanExpectation(False, lineage, version),
                plan_data='{"program": []}', score=None)
            replacement = TrainingPlan.query.filter_by(user_id=user_id).one()
            assert replacement.lineage_id != lineage
            assert resolve_plan_origin(replacement, user_id)["kind"] == "unknown"
        finally:
            db.session.remove()
