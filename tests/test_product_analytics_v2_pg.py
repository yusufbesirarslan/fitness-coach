"""Product analytics v2 on disposable PostgreSQL: real row locks, real races.

Synthetic transport only (a gate transport parks a send in flight so the
other side of each race can run against the real lock). No vendor contact.
Run: FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... pytest -m pg_concurrency
"""
import importlib.util
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from flask import Flask

from app.extensions import db
from app.models import ProductAnalyticsConsent, ProductAnalyticsErasure, ProductAnalyticsOutbox, User
from app.services.product_analytics import FLAG, consent, dispatch, emit, pipeline, settings
from app.services.product_analytics.catalog import NOTICE_VERSION
from app.services.product_analytics.transport import SyntheticTransport, TransportFailure

pytestmark = pytest.mark.pg_concurrency
if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires explicitly disposable PostgreSQL", allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "6e25ecb68c09_product_analytics_v2.py"
HOLD = 0.6          # how long a send is parked in flight
WAIT = 15


@pytest.fixture
def pg_app(monkeypatch):
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.fail("explicit disposable PostgreSQL required")
    app = Flask("product-analytics-pg")
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=url, SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      SECRET_KEY="synthetic-analytics-pg-key", SQLALCHEMY_ENGINE_OPTIONS={"pool_size": 10})
    app.config[FLAG] = True
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        for index in (1, 2):
            db.session.add(User(id=index, username=f"analytics-pg-{index}",
                                email=f"analytics-pg-{index}@example.invalid"))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


class Gate(SyntheticTransport):
    """Parks the first send in flight until released."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.entered, self.release = threading.Event(), threading.Event()
        self.parked = 0

    def send(self, body):
        if self.parked == 0:
            self.parked += 1
            self.entered.set()
            assert self.release.wait(WAIT)
        super().send(body)


def background(app, fn, *args):
    box = {"done": threading.Event(), "error": None, "value": None}

    def run():
        try:
            with app.app_context():
                box["value"] = fn(*args)
        except BaseException as error:          # surfaced by join()
            box["error"] = error
        finally:
            with app.app_context():
                db.session.remove()
            box["done"].set()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    box["thread"] = thread
    return box


def join(box):
    box["thread"].join(WAIT)
    assert box["done"].is_set(), "background work did not finish"
    if box["error"] is not None:
        raise box["error"]
    return box["value"]


def grant(owner):
    return consent.grant(owner, NOTICE_VERSION)


def sent_events(transport):
    return [item for body in transport.sent for item in json.loads(body)["batch"]]


def expose(owner):
    row = consent.current(owner)
    row.vendor_exposed_at = datetime.utcnow()
    db.session.commit()


def test_withdrawal_waits_for_the_in_flight_send_then_fences_everything(pg_app):
    with pg_app.app_context():
        grant(1)
    gate = Gate()
    sender = background(pg_app, dispatch.dispatch_once, gate)
    assert gate.entered.wait(WAIT)
    started = time.monotonic()
    withdrawal = background(pg_app, consent.withdraw, 1)
    time.sleep(HOLD)
    assert not withdrawal["done"].is_set()          # blocked on the consent row lock
    gate.release.set()
    assert join(sender)["sent"] == 1
    join(withdrawal)
    assert time.monotonic() - started < HOLD + 5    # bounded by the send, not unbounded
    with pg_app.app_context():
        assert ProductAnalyticsOutbox.query.count() == 0
        job = ProductAnalyticsErasure.query.one()
        assert (job.reason, job.status) == ("withdrawn", "pending")   # exposed before send


def test_withdrawal_committed_before_transport_admission_sends_nothing(pg_app, monkeypatch):
    with pg_app.app_context():
        grant(1)
        emit.emit_authoritative(1, "axis_v2_onboarding_completed", "profile:1")
    original = dispatch._mark_exposure

    def mark_then_withdraw(owners, now):
        original(owners, now)
        join(background(pg_app, consent.withdraw, 1))

    monkeypatch.setattr(dispatch, "_mark_exposure", mark_then_withdraw)
    transport = SyntheticTransport()
    with pg_app.app_context():
        stats = dispatch.dispatch_once(transport)
        assert stats["sent"] == 0 and transport.sent == []
        assert ProductAnalyticsOutbox.query.count() == 0
        assert ProductAnalyticsErasure.query.one().status == "pending"   # conservative


def test_duplicate_wakeups_never_double_send_and_delivery_keeps_order(pg_app):
    with pg_app.app_context():
        grant(1)
        for ref in ("a", "b"):
            emit.emit_authoritative(1, "axis_v2_workout_completed", f"day:{ref}")
        ids = [row.event_id for row in ProductAnalyticsOutbox.query.order_by(ProductAnalyticsOutbox.id)]
    gate = Gate()
    first = background(pg_app, dispatch.dispatch_once, gate)
    assert gate.entered.wait(WAIT)
    second_transport = SyntheticTransport()
    second = background(pg_app, dispatch.dispatch_once, second_transport)
    second_stats = join(second)                     # returns while the first is in flight
    assert second_stats["sent"] == 0 and second_transport.sent == []
    gate.release.set()
    assert join(first)["sent"] == 3
    assert [item["uuid"] for item in sent_events(gate)] == ids


def test_generation_and_subject_fences_drop_stale_rows(pg_app):
    with pg_app.app_context():
        grant(1)
        old = consent.current(1)
        old_subject, old_generation = old.subject, old.generation
        consent.withdraw(1)
        grant(1)
        live = consent.current(1)
        now = datetime.utcnow()
        for generation, subject in ((old_generation, old_subject), (live.generation, old_subject),
                                    (old_generation, live.subject)):
            db.session.add(ProductAnalyticsOutbox(
                user_id=1, generation=generation, subject=subject, event_id=str(uuid.uuid4()),
                name="axis_v2_app_opened", authority="client", occurred_at=now, received_at=now,
                properties="{}", attempts=0, next_attempt_at=now))
        db.session.commit()
        transport = SyntheticTransport()
        stats = dispatch.dispatch_once(transport)
        assert stats["fenced"] == 3 and stats["sent"] == 1
        assert {item["distinct_id"] for item in sent_events(transport)} == {live.subject}


def test_withdraw_then_regrant_during_an_in_flight_send(pg_app):
    with pg_app.app_context():
        grant(1)
        old_subject = consent.current(1).subject
    gate = Gate()
    sender = background(pg_app, dispatch.dispatch_once, gate)
    assert gate.entered.wait(WAIT)

    def withdraw_and_regrant():
        consent.withdraw(1)
        return grant(1)

    churn = background(pg_app, withdraw_and_regrant)
    time.sleep(HOLD)
    assert not churn["done"].is_set()
    gate.release.set()
    join(sender)
    join(churn)
    assert {item["distinct_id"] for item in sent_events(gate)} == {old_subject}
    with pg_app.app_context():
        live = consent.current(1)
        assert live.subject != old_subject and live.vendor_exposed_at is None
        later = SyntheticTransport()
        assert dispatch.dispatch_once(later)["sent"] == 1
        assert {item["distinct_id"] for item in sent_events(later)} == {live.subject}


def test_account_deletion_racing_dispatch_waits_and_queues_erasure(pg_app, monkeypatch):
    from account_deletion_support import install_fakes
    from app.cli import _purge_user
    install_fakes(monkeypatch)
    with pg_app.app_context():
        grant(1)
        subject = consent.current(1).subject
    gate = Gate()
    sender = background(pg_app, dispatch.dispatch_once, gate)
    assert gate.entered.wait(WAIT)

    def purge():
        _purge_user(db.session.get(User, 1))
        db.session.commit()

    purger = background(pg_app, purge)
    time.sleep(HOLD)
    assert not purger["done"].is_set()
    gate.release.set()
    join(sender)
    join(purger)
    with pg_app.app_context():
        assert db.session.get(User, 1) is None
        assert ProductAnalyticsConsent.query.count() == 0 and ProductAnalyticsOutbox.query.count() == 0
        job = ProductAnalyticsErasure.query.one()
        assert (job.subject, job.reason, job.status) == (subject, "account_deleted", "pending")


def test_erasure_job_is_atomic_with_withdrawal_and_outlives_the_account(pg_app, monkeypatch):
    with pg_app.app_context():
        grant(1)
        expose(1)
        original = ProductAnalyticsOutbox.query.__class__.delete

        def broken(self, *args, **kwargs):
            raise RuntimeError("synthetic failure inside the withdrawal transaction")

        monkeypatch.setattr(ProductAnalyticsOutbox.query.__class__, "delete", broken)
        with pytest.raises(RuntimeError):
            consent.withdraw(1)
        monkeypatch.setattr(ProductAnalyticsOutbox.query.__class__, "delete", original)
        db.session.remove()
        assert ProductAnalyticsErasure.query.count() == 0
        assert consent.current(1).status == "granted"
        consent.withdraw(1)
        db.session.execute(sa.text("DELETE FROM product_analytics_consent WHERE user_id = 1"))
        db.session.execute(sa.text('DELETE FROM "user" WHERE id = 1'))
        db.session.commit()
        assert ProductAnalyticsErasure.query.one().status == "pending"


def test_lost_acknowledgement_replays_the_same_event_identity(pg_app):
    class LostAck(SyntheticTransport):
        def send(self, body):
            super().send(body)
            if len(self.sent) == 1:
                raise TransportFailure("timeout")    # delivered, acknowledgement lost

    with pg_app.app_context():
        grant(1)
        transport = LostAck()
        now = datetime.utcnow()
        assert dispatch.dispatch_once(transport, now=now)["retry"] == 1
        row = ProductAnalyticsOutbox.query.one()
        assert row.attempts == 1
        assert dispatch.dispatch_once(transport, now=now + timedelta(minutes=1))["sent"] == 1
        first, second = (json.loads(body)["batch"][0] for body in transport.sent)
        assert first["uuid"] == second["uuid"] and first == second


def test_expired_outbox_rows_are_cleaned_up_even_while_dispatch_cannot_run(pg_app):
    with pg_app.app_context():
        grant(1)
        grant(2)
        row = ProductAnalyticsOutbox.query.filter_by(user_id=1).one()
        row.occurred_at = datetime.utcnow() - timedelta(hours=49)
        db.session.commit()
        stats = pipeline.run_pass(config={})                     # no vendor configuration
        assert stats["vendor"] == "unconfigured" and stats["outbox_expired"] == 1
        assert [r.user_id for r in ProductAnalyticsOutbox.query.all()] == [2]


def test_erasure_retries_then_settles_and_concurrent_passes_skip_locked_jobs(pg_app):
    with pg_app.app_context():
        for owner in (1, 2):
            grant(owner)
            expose(owner)
            consent.withdraw(owner)
        now = datetime.utcnow()
        failing = SyntheticTransport(fail=True)
        assert dispatch.process_erasures_once(failing, now=now)["failed"] == 2
        assert {j.attempts for j in ProductAnalyticsErasure.query.all()} == {1}
    gate = Gate()
    later = now + timedelta(hours=1)
    first = background(pg_app, lambda: dispatch.process_erasures_once(gate, now=later))
    assert gate.entered.wait(WAIT)
    other = SyntheticTransport()
    assert join(background(pg_app, lambda: dispatch.process_erasures_once(other, now=later))) \
        ["materialized"] == 0 and other.sent == []
    gate.release.set()
    assert join(first)["materialized"] == 2
    with pg_app.app_context():
        settle = later + dispatch.ERASURE_SETTLE
        assert dispatch.process_erasures_once(gate, now=settle)["requested"] == 2
        gate.verify_all()
        verify = settle + dispatch.ERASURE_VERIFY_INTERVAL
        assert dispatch.process_erasures_once(gate, now=verify)["verified"] == 2
        assert {j.status for j in ProductAnalyticsErasure.query.all()} == {"verified"}


def test_dispatch_off_still_settles_owed_erasure(pg_app):
    with pg_app.app_context():
        grant(1)
        expose(1)
        consent.withdraw(1)
        grant(2)
        pg_app.config[FLAG] = False
        transport = SyntheticTransport()
        stats = pipeline.run_pass(transport=transport, config={})
        assert "sent" not in stats and stats["erasure_materialized"] == 1
        assert ProductAnalyticsOutbox.query.filter_by(user_id=2).count() == 1


def test_missing_vendor_configuration_leaves_rows_and_contacts_nothing(pg_app):
    with pg_app.app_context():
        grant(1)
        expose(1)
        consent.withdraw(1)
        grant(2)
        stats = pipeline.run_pass(config={settings.REGION: "eu"})      # token missing
        assert stats["vendor"] == "unconfigured"
        assert ProductAnalyticsOutbox.query.count() == 1
        assert ProductAnalyticsErasure.query.one().status == "pending"


def test_cross_account_isolation_under_a_held_consent_lock(pg_app):
    with pg_app.app_context():
        grant(1)
        grant(2)
    holding, release = threading.Event(), threading.Event()

    def hold_owner_one():
        consent.current(1, lock=True)               # e.g. a slow withdrawal of account 1
        holding.set()
        assert release.wait(WAIT)
        db.session.rollback()

    holder = background(pg_app, hold_owner_one)
    assert holding.wait(WAIT)
    transport = SyntheticTransport()
    with pg_app.app_context():
        stats = dispatch.dispatch_once(transport)
        db.session.remove()
    release.set()
    join(holder)
    assert stats["sent"] == 1 and stats["skipped"] == 1
    with pg_app.app_context():
        assert {item["distinct_id"] for item in sent_events(transport)} == {consent.current(2).subject}
        assert ProductAnalyticsOutbox.query.filter_by(user_id=1).count() == 1


def test_an_in_flight_send_does_not_delay_canonical_writes_or_emits(pg_app):
    with pg_app.app_context():
        grant(1)
    gate = Gate()
    sender = background(pg_app, dispatch.dispatch_once, gate)
    assert gate.entered.wait(WAIT)

    def canonical_work():
        started = time.monotonic()
        user = db.session.get(User, 1)
        user.weight = 81
        db.session.commit()                          # an ordinary product write
        emit.emit_authoritative(1, "axis_v2_workout_completed", "day:in-flight")
        consent.view(consent.current(1))             # the Settings read path
        return time.monotonic() - started

    elapsed = join(background(pg_app, canonical_work))
    assert elapsed < HOLD / 2
    gate.release.set()
    join(sender)
    with pg_app.app_context():
        assert ProductAnalyticsOutbox.query.filter_by(name="axis_v2_workout_completed").count() == 1


def test_rotation_skips_a_busy_account_and_never_waits(pg_app):
    with pg_app.app_context():
        for owner in (1, 2):
            grant(owner)
        old = {owner: consent.current(owner).subject for owner in (1, 2)}
        db.session.remove()
    holding, release = threading.Event(), threading.Event()

    def hold_owner_one():
        consent.current(1, lock=True)
        holding.set()
        assert release.wait(WAIT)
        db.session.rollback()

    holder = background(pg_app, hold_owner_one)
    assert holding.wait(WAIT)
    policy = settings.retention_policy({settings.SUBJECT_MAX_AGE: "30"})
    with pg_app.app_context():
        started = time.monotonic()
        from app.services.product_analytics import retention
        stats = retention.cleanup(policy, now=datetime.utcnow() + timedelta(days=31))
        assert time.monotonic() - started < HOLD
        db.session.remove()
    release.set()
    join(holder)
    assert stats["subjects_rotated"] == 1
    with pg_app.app_context():
        assert consent.current(1).subject == old[1]
        assert consent.current(2).subject != old[2]
        assert {j.subject for j in ProductAnalyticsErasure.query.all()} == {old[2]}


def _module(name):
    path = ROOT / "migrations/versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, fn):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with Operations.context(MigrationContext.configure(connection)):
        fn()


def test_migration_creates_verifies_and_refuses_lossy_downgrade_on_postgresql(pg_app):
    migration = _module(MIGRATION)
    with pg_app.app_context():
        with db.engine.begin() as conn:
            _run(conn, migration.upgrade)            # create_all-first: verify only
            for table in reversed(migration._TABLES):
                conn.execute(sa.text(f"DROP TABLE {table}"))
            _run(conn, migration.upgrade)            # fresh create
            _run(conn, migration.upgrade)            # re-runnable
        grant(1)
        db.session.remove()                          # no idle transaction on the table
        with pytest.raises(RuntimeError):
            with db.engine.begin() as conn:
                _run(conn, migration.downgrade)
        assert ProductAnalyticsConsent.query.count() == 1
