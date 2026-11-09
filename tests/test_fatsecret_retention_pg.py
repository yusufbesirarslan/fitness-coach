"""Opt-in PostgreSQL races for the FatSecret 24h expiry pass.

SQLite has no ``FOR UPDATE SKIP LOCKED`` and no concurrent writers, so it
cannot prove that two passes (two workers, or the worker plus daily
maintenance) fold every row exactly once, conserve every day total and never
deadlock on the retained aggregate. These tests run the real pass on real
PostgreSQL connections.

    FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://... \\
        python -m pytest tests/test_fatsecret_retention_pg.py -m pg_concurrency
"""
import os
import threading
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.pg_concurrency]

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )

THREAD_TIMEOUT = 60


@pytest.fixture(scope="module", autouse=True)
def _postgres_database_url():
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
    previous = os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = url
    try:
        yield url
    finally:
        os.environ["DATABASE_URL"] = previous


def _seed(make_user, rows_per_day=40):
    from app.extensions import db
    from app.models import MealLog
    from app.services import provider_retention

    users = [make_user("ret-pg-a"), make_user("ret-pg-b")]
    acquired = datetime.utcnow() - timedelta(hours=30)
    days = ["2026-11-01", "2026-11-02", "2026-11-03"]
    expected = {}
    for user in users:
        for day in days:
            for n in range(rows_per_day):
                kcal = float(100 + n)
                db.session.add(MealLog(
                    user_id=user.id, ogun="Öğle", yemekler=f"Provider {n}", kalori=kcal,
                    protein=1.0, karb=2.0, yag=0.5, tarih=day, source="search",
                    created_at=acquired, provider_acquired_at=acquired,
                    provider_items=[provider_retention.provider_component("f", "s", 1)]))
                expected[(user.id, day)] = expected.get((user.id, day), 0.0) + kcal
    db.session.commit()
    return users, days, expected


def _run_concurrently(app, workers):
    from app.extensions import db
    barrier = threading.Barrier(len(workers))
    errors, results = [], []

    def run(fn):
        with app.app_context():
            try:
                barrier.wait(timeout=THREAD_TIMEOUT)
                results.append(fn())
            except Exception as error:  # surfaced below
                errors.append(error)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=run, args=(fn,)) for fn in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=THREAD_TIMEOUT)
        assert not thread.is_alive(), "expiry pass hung (lock wait or deadlock)"
    assert errors == []
    return results


def test_concurrent_passes_fold_every_row_exactly_once(app, make_user, monkeypatch):
    from app.extensions import db
    from app.models import MealLog, MealLogRetainedTotal
    from app.services import provider_retention

    assert db.engine.dialect.name == "postgresql"
    monkeypatch.setattr(provider_retention, "BATCH_SIZE", 17)  # many interleaved batches
    users, days, expected = _seed(make_user)
    now = datetime.utcnow()

    results = _run_concurrently(app, [lambda: provider_retention.expire_due(now)] * 3)

    assert sum(r.folded_entries for r in results) == len(expected) * 40
    db.session.expire_all()
    assert MealLog.query.filter(MealLog.provider_expired_at.is_(None)).count() == 0
    assert MealLog.query.filter(MealLog.kalori.isnot(None)).count() == 0
    for (user_id, day), kcal in expected.items():
        total = MealLogRetainedTotal.query.filter_by(user_id=user_id, tarih=day).one()
        assert total.energy_kcal == pytest.approx(kcal)
        assert total.item_count == 40
        assert MealLog.query.filter_by(user_id=user_id, tarih=day).count() == 40


def test_a_pass_and_new_writes_never_disturb_each_other(app, make_user):
    from app.extensions import db
    from app.models import MealLog, MealLogRetainedTotal
    from app.services import provider_retention

    users, days, expected = _seed(make_user, rows_per_day=20)
    owner_id = users[0].id
    now = datetime.utcnow()

    def write_fresh_rows():
        for n in range(30):
            db.session.add(MealLog(
                user_id=owner_id, ogun="Akşam", yemekler="fresh", kalori=50.0,
                tarih=days[0], source="search", created_at=datetime.utcnow(),
                provider_acquired_at=datetime.utcnow(),
                provider_items=[provider_retention.provider_component("f", "s", 1)]))
            db.session.commit()
        return "written"

    _run_concurrently(app, [lambda: provider_retention.expire_due(now), write_fresh_rows])

    db.session.expire_all()
    fresh = MealLog.query.filter_by(user_id=owner_id, yemekler="fresh").all()
    assert len(fresh) == 30 and all(r.kalori == 50.0 and r.provider_expired_at is None
                                    for r in fresh)
    total = MealLogRetainedTotal.query.filter_by(user_id=owner_id, tarih=days[0]).one()
    assert total.energy_kcal == pytest.approx(expected[(owner_id, days[0])])
    # Day total (rows + retained) is exactly what storage held before the pass.
    rows = MealLog.query.filter_by(user_id=owner_id, tarih=days[0]).all()
    assert sum(r.kalori or 0 for r in rows) + total.energy_kcal == pytest.approx(
        expected[(owner_id, days[0])] + 30 * 50.0)
