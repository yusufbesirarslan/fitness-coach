"""M4 overlap, retention and additive-schema proof on disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import threading

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.health_consent_models import HealthErasure, HealthErasureRetry
from app.services import health_erasure_monitoring as m
from test_health_consent_pg import pg_app  # noqa: F401
from test_mobile_training_allowance_pg import _module, _run

pytestmark = pytest.mark.pg_concurrency
MIGRATION = "f01a4e000003_health_erasure_retry_monitoring.py"


@pytest.mark.parametrize("slots,expected", [((100, 100), 1), ((100, 101), 2), ((101, 100), 2)])
def test_overlapping_workers_count_distinct_hours_once(pg_app, slots, expected):
    with pg_app.app_context():
        row = HealthErasure(owner_id=1, generation=1, requested_at=datetime.utcnow() - timedelta(hours=3))
        db.session.add(row)
        db.session.commit()
        key = row.id
        start = m.start_pass()
    ready = threading.Barrier(2)
    def finish(slot):
        with pg_app.app_context():
            ready.wait(timeout=5)
            return m.finish_pass(start, slot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finish, slot) for slot in slots]
        for future in futures:
            future.result(timeout=10)
    with pg_app.app_context():
        assert db.session.get(HealthErasureRetry, key).passes == expected
        assert HealthErasureRetry.query.count() == 1


def test_monitoring_evidence_expires_with_erasure_receipt_and_not_account(pg_app):
    from app.models import User
    with pg_app.app_context():
        row = HealthErasure(owner_id=1, generation=1, requested_at=datetime.utcnow() - timedelta(hours=3))
        db.session.add(row)
        db.session.commit()
        key = row.id
        m.finish_pass(m.start_pass(), 100)
        db.session.execute(sa.delete(User).where(User.id == 1))
        db.session.commit()
        assert db.session.get(HealthErasureRetry, key) is not None
        db.session.execute(sa.delete(HealthErasure).where(HealthErasure.id == key))
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(HealthErasureRetry, key) is None


def test_additive_migration_create_reentry_and_refuse_partial_schema(pg_app):
    with pg_app.app_context(), db.engine.begin() as conn:
        migration = _module(MIGRATION)
        conn.execute(sa.text("DROP TABLE health_erasure_retry"))
        before = set(sa.inspect(conn).get_table_names())
        _run(conn, migration.upgrade)
        _run(conn, migration.upgrade)
        assert set(sa.inspect(conn).get_table_names()) == before | {"health_erasure_retry"}
        assert conn.execute(sa.text("SELECT count(*) FROM health_erasure_retry")).scalar_one() == 0
        conn.execute(sa.text("ALTER TABLE health_erasure_retry DROP COLUMN last_slot"))
        with pytest.raises(RuntimeError, match="incompatible"):
            _run(conn, migration.upgrade)


def test_later_finishing_pass_publishes_its_newer_counter_snapshot(pg_app, monkeypatch):
    """An earlier process start is not the aggregate snapshot's order."""
    import json
    import fakeredis
    from app import jobs
    redis = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(jobs, "_rq_connection", lambda: redis)
    with pg_app.app_context():
        db.session.add(HealthErasure(owner_id=1, generation=1,
            requested_at=datetime.utcnow() - timedelta(hours=3)))
        db.session.commit()
        start = m.start_pass()
        engine = db.engine
    entered, release = threading.Event(), threading.Event()
    def pause(connection, cursor, statement, parameters, context, executemany):
        if (threading.current_thread().name == "earlier-start"
                and "health_erasure" in statement and "FOR UPDATE" in statement
                and not entered.is_set()):
            entered.set()
            assert release.wait(10)
    sa.event.listen(engine, "before_cursor_execute", pause)
    def finish(name, slot):
        threading.current_thread().name = name
        with pg_app.app_context():
            snapshot = m.finish_pass(start, slot)
            m.store_snapshot(snapshot)
            return snapshot
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            early = pool.submit(finish, "earlier-start", 100)
            assert entered.wait(5)
            later = pool.submit(finish, "later-start", 101)
            newer_started = later.result(timeout=10)
            release.set()
            later_finished = early.result(timeout=10)
    finally:
        release.set()
        sa.event.remove(engine, "before_cursor_execute", pause)
    with pg_app.app_context():
        assert HealthErasureRetry.query.one().passes == 2
    assert later_finished["sequence"] > newer_started["sequence"]
    assert json.loads(redis.get(m.KEY))["twice"] == 1
