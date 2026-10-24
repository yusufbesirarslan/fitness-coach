"""MON-02-BE-03B-01B on real PostgreSQL: crash boundaries, races, lock lifetime.

Synthetic providers only; disposable database only (PG_TEST_DATABASE_URL).
Every case reports operation state, binding, ledger hold, bucket counters,
canonical plan count and the number of synthetic executions.
"""
import importlib.util
import json
import threading
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app import config as app_config
from tests.test_mobile_training_generation_pg import (  # noqa: F401
    pg_generation_app, _request, _document,
)

pytestmark = pytest.mark.pg_concurrency

@pytest.fixture(autouse=True)
def _lp21_synthetic_operation_authority(monkeypatch):
    # LP21: these races use injected synthetic providers/writers; ALLOW at the
    # operation-fencing decision points only. Real D1 operation fencing of the
    # same writers is qualified in tests/test_lp21_d1_operation_fencing_pg.py.
    from consent_projection_support import allow_operation_fences
    allow_operation_fences(monkeypatch)


ROOT = Path(__file__).resolve().parents[1]


class Crash(BaseException):
    """Escapes application handlers, like abrupt worker death."""


def _metered(app, monkeypatch, units=1, cap=2):
    from ai_allowance_support import fixture_policy
    from app.services import ai_consent
    # Accounting isolation only: the real D1 preflight/final gate is qualified in
    # tests/test_training_allowance_zero_attempt_pg.py. Never a production bypass.
    monkeypatch.setattr(ai_consent, "preflight", lambda *a, **k: None)
    # Re-applied here because some tests ``monkeypatch.undo()`` mid-test.
    from consent_projection_support import allow_operation_fences
    allow_operation_fences(monkeypatch)
    app.config.update(
        AI_ALLOWANCE_LEDGER_ENABLED=True,
        AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED=True,
        AI_ALLOWANCE_POLICY=fixture_policy(
            max_billed=cap, starter_units={"training_generation": units, "coach": 1}))


def _state(user_id):
    from app.extensions import db
    from app.models import (
        AiAllowanceBucket, AiUsageReservation, TrainingPlan,
        TrainingPlanGenerationAllowance, TrainingPlanGenerationOperation)
    db.session.remove()
    bucket = AiAllowanceBucket.query.filter_by(
        user_id=user_id, feature="training_generation").one_or_none()
    return SimpleNamespace(
        operations=[(o.status, o.provider_dispatch_possible) for o in
                    TrainingPlanGenerationOperation.query.filter_by(user_id=user_id)
                    .order_by(TrainingPlanGenerationOperation.id)],
        bindings=[b.state for b in TrainingPlanGenerationAllowance.query.filter_by(user_id=user_id)
                  .order_by(TrainingPlanGenerationAllowance.operation_id)],
        holds=[(r.state, r.billing_evidence, r.provider_billed) for r in
               AiUsageReservation.query.filter_by(user_id=user_id).order_by(AiUsageReservation.id)],
        bucket=None if bucket is None else (bucket.used, bucket.reserved),
        plans=TrainingPlan.query.filter_by(user_id=user_id).count(),
    )


def _command(user_id, key):
    from app.extensions import db
    from app.models import User
    from app.services.mobile_training_generation import service
    return service.generate_and_persist(
        db.session.get(User, user_id), _request(), key, chat_fn=None, provider_guard=nullcontext)


# ── Crash boundaries ───────────────────────────────────────────────────────
# boundary -> (operation, binding, hold, bucket, executions) right after the crash,
# then what the same key does next and the final ledger.
CASES = {
    "before_claim_commit": (None, [], [], None, 0, "succeeds"),
    "after_claim_commit": (("IN_PROGRESS", False), [], [], None, 0, "succeeds"),
    "during_dispatch_commit": (("IN_PROGRESS", False), [], [], None, 0, "succeeds"),
    "after_dispatch_commit": (("IN_PROGRESS", True), ["RESERVED"], ["RESERVED"], (0, 1), 0, "in_progress"),
    "in_provider": (("IN_PROGRESS", True), ["RESERVED"], ["RESERVED"], (0, 1), 1, "in_progress"),
    "during_stage_commit": (("IN_PROGRESS", True), ["RESERVED"], ["RESERVED"], (0, 1), 1, "in_progress"),
    "after_stage": (("GENERATED", True), ["RESERVED"], ["RESERVED"], (0, 1), 1, "succeeds"),
    "during_plan_insert": (("GENERATED", True), ["RESERVED"], ["RESERVED"], (0, 1), 1, "succeeds"),
    "after_plan_commit": (("SUCCEEDED", True), ["RESERVED"], ["RESERVED"], (0, 1), 1, "succeeds"),
    "during_settlement_mark": (("SUCCEEDED", True), ["RESERVED"], ["COMMITTED"], (1, 0), 1, "succeeds"),
}


@pytest.mark.parametrize("boundary", list(CASES))
def test_metered_crash_boundary_never_strands_duplicates_or_falsely_settles(
        pg_generation_app, boundary):
    from app.extensions import db
    from app.services.mobile_training_generation import allowance, service, store
    from app.services.mobile_training_generation.errors import GenerationInProgress
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch)
    operation, bindings, holds, bucket, calls, then = CASES[boundary]
    executions = []

    def candidate(*args, **kwargs):
        from app.services import ai_provider_call
        ai_provider_call._before_physical_attempt()  # like every real SDK call
        executions.append(1)
        if boundary == "in_provider":
            raise Crash()
        return SimpleNamespace(document=_document(), overall_score=8.5)
    monkeypatch.setattr(service, "generate_training_plan_candidate", candidate)

    def crash_after(name):
        real = getattr(store, name)
        def wrapper(*args, **kwargs):
            real(*args, **kwargs)
            raise Crash()
        monkeypatch.setattr(store, name, wrapper)
        return real

    def crash_in_commit(name):
        real = getattr(store, name)
        def wrapper(*args, **kwargs):
            commit = db.session.commit
            def fail():
                db.session.flush()
                raise Crash()
            monkeypatch.setattr(db.session, "commit", fail)
            try:
                return real(*args, **kwargs)
            finally:
                monkeypatch.setattr(db.session, "commit", commit)
        monkeypatch.setattr(store, name, wrapper)

    with app.app_context():
        if boundary == "before_claim_commit":
            crash_in_commit("claim")
        elif boundary == "after_claim_commit":
            crash_after("claim")
        elif boundary == "during_dispatch_commit":
            crash_in_commit("authorize_dispatch")
        elif boundary == "after_dispatch_commit":
            crash_after("authorize_dispatch")
        elif boundary == "during_stage_commit":
            crash_in_commit("stage_candidate")
        elif boundary == "after_stage":
            crash_after("stage_candidate")
        elif boundary == "during_plan_insert":
            monkeypatch.setattr(store, "_insert_plan", lambda **k: (_ for _ in ()).throw(Crash()))
        elif boundary == "after_plan_commit":
            crash_after("commit_plan")
        elif boundary == "during_settlement_mark":
            monkeypatch.setattr(allowance, "_binding", lambda *a, **k: (_ for _ in ()).throw(Crash()))
        with pytest.raises(Crash):
            _command(owners[0], "metered-crash-key")
        state = _state(owners[0])
        assert (state.operations[0] if state.operations else None) == operation
        assert state.bindings == bindings
        assert [h[0] for h in state.holds] == holds
        assert all(h[1:] == ("UNKNOWN", None) for h in state.holds)  # never False/True
        assert state.bucket in ((bucket,) if bucket else (None, (0, 0)))
        assert len(executions) == calls

        monkeypatch.undo()
        _metered(app, monkeypatch)
        monkeypatch.setattr(service, "generate_training_plan_candidate", lambda *a, **k: (
            executions.append(1) or SimpleNamespace(document=_document(), overall_score=8.5)))
        if then == "in_progress":
            with pytest.raises(GenerationInProgress):
                _command(owners[0], "metered-crash-key")
            final = _state(owners[0])
            assert len(final.holds) == 1 and final.bucket == (0, 1) and final.plans == 0
            assert len(executions) == calls  # never redispatched under the same key
        else:
            _command(owners[0], "metered-crash-key")
            _command(owners[0], "metered-crash-key")  # replay: nothing moves
            final = _state(owners[0])
            assert final.operations == [("SUCCEEDED", True)]
            assert final.bindings == ["COMMITTED"]
            assert final.holds == [("COMMITTED", "UNKNOWN", None)]
            assert final.bucket == (1, 0) and final.plans == 1
            assert len(executions) == max(calls, 1)


def test_dispatch_marker_hold_and_binding_share_one_stored_instant(pg_generation_app):
    """R1 probe kept: retirement accounting relies on this exact instant (naive UTC vs timestamptz)."""
    from app.extensions import db
    from app.services import ai_allowances
    from app.services.mobile_training_generation import store
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch)
    assert store.ABANDON_AFTER >= ai_allowances.RESERVATION_TTL
    with app.app_context():
        operation_id = store.claim(owners[0], "same-instant", _request().fingerprint).id
        store.authorize_dispatch(operation_id)
        db.session.remove()
        row = db.session.execute(sa.text(
            "SELECT o.updated_at, r.created_at AT TIME ZONE 'UTC', r.expires_at AT TIME ZONE 'UTC', "
            "b.created_at, o.provider_dispatch_possible, o.allowance_metered, r.billing_evidence, "
            "r.provider_billed, pg_typeof(o.updated_at)::text, pg_typeof(r.expires_at)::text "
            "FROM training_plan_generation_operation o "
            "JOIN training_plan_generation_allowance b ON b.operation_id = o.id "
            "JOIN ai_usage_reservation r ON r.public_id = b.reservation_ref WHERE o.id = :o"),
            {"o": operation_id}).one()
    marker, created, expires, bound, dispatched, metered, evidence, billed, naive, aware = row
    assert marker == created == bound  # microsecond-exact
    assert expires - marker == ai_allowances.RESERVATION_TTL
    assert (dispatched, metered, evidence, billed) == (True, True, "UNKNOWN", None)
    assert (naive, aware) == ("timestamp without time zone", "timestamp with time zone")


# ── Races ──────────────────────────────────────────────────────────────────
def test_concurrent_dispatch_authorizations_create_exactly_one_hold(pg_generation_app):
    from app.extensions import db
    from app.services.mobile_training_generation import store
    from app.services.mobile_training_generation.errors import GenerationInProgress
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch, units=2)
    with app.app_context():
        operation_id = store.claim(owners[0], "contested-dispatch", _request().fingerprint).id
        db.session.remove()
    barrier, outcomes = threading.Barrier(2), []

    def contend():
        with app.app_context():
            barrier.wait()
            try:
                store.authorize_dispatch(operation_id)
                outcomes.append("authorized")
            except GenerationInProgress:
                outcomes.append("in_progress")
            finally:
                db.session.remove()
    threads = [threading.Thread(target=contend) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert sorted(outcomes) == ["authorized", "in_progress"]
    with app.app_context():
        state = _state(owners[0])
        assert state.holds == [("RESERVED", "UNKNOWN", None)] and state.bucket == (0, 1)
        assert state.bindings == ["RESERVED"]


def test_concurrent_holds_for_one_owner_cannot_overspend(pg_generation_app):
    from app.extensions import db
    from app.services import ai_allowances
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch, units=1)
    barrier, outcomes = threading.Barrier(2), []

    def hold(index):
        with app.app_context():
            barrier.wait()
            try:
                ai_allowances.reserve_within(owners[0], "training_generation", f"overspend-{index:04d}")
                db.session.commit()
                outcomes.append("held")
            except ai_allowances.AllowanceError as error:
                db.session.rollback()
                outcomes.append(error.code)
            finally:
                db.session.remove()
    threads = [threading.Thread(target=hold, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert sorted(outcomes) == ["AI_ALLOWANCE_EXHAUSTED", "held"]
    with app.app_context():
        assert _state(owners[0]).bucket == (0, 1)


def test_refused_reservation_rolls_back_the_lazy_grant_on_postgresql(pg_generation_app):
    from app.extensions import db
    from app.models import AiAllowanceBucket, AiStarterGrant, AiUsageReservation
    from app.services import ai_allowances
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch)
    with app.app_context():
        ai_allowances.reserve_within(owners[0], "training_generation", "pg-rollback-0001")
        db.session.rollback()
        assert (AiUsageReservation.query.count(), AiAllowanceBucket.query.count(),
                AiStarterGrant.query.count()) == (0, 0, 0)


def test_no_lock_or_transaction_spans_provider_io_while_metered(pg_generation_app):
    from app.extensions import db
    from app.services.mobile_training_generation import service
    from app.services.mobile_training_generation.locking import _advisory_key
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch)
    probed = []

    def candidate(*args, **kwargs):
        assert not db.session().in_transaction()
        with db.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            key = _advisory_key(owners[0])
            assert conn.exec_driver_sql("SELECT pg_try_advisory_lock(%s)", (key,)).scalar()
            conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (key,))
        with db.engine.begin() as conn:
            for sql in (
                    'SELECT id FROM "user" WHERE id=%s FOR UPDATE NOWAIT',
                    "SELECT id FROM training_plan_generation_operation WHERE user_id=%s FOR UPDATE NOWAIT",
                    "SELECT operation_id FROM training_plan_generation_allowance WHERE user_id=%s FOR UPDATE NOWAIT",
                    "SELECT id FROM ai_usage_reservation WHERE user_id=%s FOR UPDATE NOWAIT",
                    "SELECT id FROM ai_allowance_bucket WHERE user_id=%s FOR UPDATE NOWAIT"):
                probed.append(len(conn.exec_driver_sql(sql, (owners[0],)).all()))
        return SimpleNamespace(document=_document(), overall_score=8.5)
    monkeypatch.setattr(service, "generate_training_plan_candidate", candidate)
    with app.app_context():
        _command(owners[0], "metered-no-lock-key")
        assert probed[:4] == [1, 1, 1, 1] and probed[4] >= 1  # all present, all lockable NOWAIT
        assert _state(owners[0]).bucket == (1, 0)


def test_distinct_key_after_retirement_overlaps_but_late_output_is_fenced(pg_generation_app):
    from app.extensions import db
    from app.models import AiUsageReservation, TrainingPlanGenerationOperation
    from app.services import ai_allowances
    from app.services.mobile_training_generation import service
    from app.services.mobile_training_generation.errors import GenerationPersistenceUnavailable
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch, units=2)
    entered, release, results, executions = threading.Event(), threading.Event(), [], []

    def candidate(*args, **kwargs):
        executions.append(1)
        if len(executions) == 1:
            entered.set()
            assert release.wait(timeout=20)
        return SimpleNamespace(document=_document(), overall_score=8.5)
    monkeypatch.setattr(service, "generate_training_plan_candidate", candidate)

    def old_worker():
        with app.app_context():
            try:
                _command(owners[0], "paused-old-key")
                results.append("ok")
            except GenerationPersistenceUnavailable:
                results.append("fenced")
            finally:
                db.session.remove()
    worker = threading.Thread(target=old_worker)
    worker.start()
    assert entered.wait(timeout=20)
    with app.app_context():
        old = TrainingPlanGenerationOperation.query.one()
        aged = datetime.now(timezone.utc) - timedelta(minutes=16)
        old.updated_at = aged.replace(tzinfo=None)
        AiUsageReservation.query.one().expires_at = aged + ai_allowances.RESERVATION_TTL
        db.session.commit()
        _command(owners[0], "explicit-new-key")
        release.set()
        worker.join(timeout=20)
        assert results == ["fenced"]
        state = _state(owners[0])
        assert state.operations == [("FAILED", True), ("SUCCEEDED", True)]
        assert state.bindings == ["EXPIRED", "COMMITTED"]
        assert [h[0] for h in state.holds] == ["EXPIRED", "COMMITTED"]
        assert state.bucket == (2, 0) and state.plans == 1 and len(executions) == 2


def test_account_deletion_during_inference_leaves_no_allowance_rows(pg_generation_app):
    from app.extensions import db
    from app.models import (
        AiAllowanceBucket, AiStarterGrant, AiUsageReservation, TrainingPlanGenerationAllowance,
        TrainingPlanGenerationOperation)
    from app.services.mobile_training_generation import service
    from app.services.mobile_training_generation.errors import GenerationPersistenceUnavailable
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch)
    entered, release, results = threading.Event(), threading.Event(), []

    def candidate(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=20)
        return SimpleNamespace(document=_document(), overall_score=8.5)
    monkeypatch.setattr(service, "generate_training_plan_candidate", candidate)

    def worker():
        with app.app_context():
            try:
                _command(owners[0], "deleted-in-flight")
                results.append("ok")
            except GenerationPersistenceUnavailable:
                results.append("unavailable")
            finally:
                db.session.remove()
    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(timeout=20)
    with app.app_context():
        with db.engine.begin() as conn:
            conn.exec_driver_sql('DELETE FROM user_session WHERE user_id=%s', (owners[0],))
            conn.exec_driver_sql('DELETE FROM "user" WHERE id=%s', (owners[0],))
        release.set()
        thread.join(timeout=20)
        assert results == ["unavailable"]
        db.session.remove()
        for model in (TrainingPlanGenerationOperation, TrainingPlanGenerationAllowance,
                      AiUsageReservation, AiAllowanceBucket, AiStarterGrant):
            assert model.query.filter_by(user_id=owners[0]).count() == 0
        assert _state(owners[1]).holds == []  # the other owner is untouched


# ── Migration on PostgreSQL ────────────────────────────────────────────────
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


@pytest.fixture
def migrate_engine():
    import os
    url = os.environ.get("PG_TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        pytest.skip("PG_TEST_DATABASE_URL must name a disposable PostgreSQL database")
    engine = sa.create_engine(url)
    reset = 'DROP SCHEMA public CASCADE; CREATE SCHEMA public'
    with engine.begin() as conn:
        conn.exec_driver_sql(reset)
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(reset)
        engine.dispose()


def _b0d3_schema(conn):
    conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
    conn.exec_driver_sql('CREATE TABLE training_plan (id INTEGER PRIMARY KEY)')
    _run(conn, _module("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)
    _run(conn, _module("d3e4f5a6b7c8_add_training_plan_generation_operations.py").upgrade)
    _run(conn, _module("b0d3e4f5a6b7_training_dispatch_boundary.py").upgrade)


def test_upgrade_from_d1_preserves_history_and_downgrade_refuses_evidence(migrate_engine):
    head = _module("b1e4f5a6c7d8_training_allowance_association.py")
    assert (head.down_revision, head.expand_contract) == ("d1c0a15e0001", "expand")
    with migrate_engine.begin() as conn:
        _b0d3_schema(conn)
        _run(conn, _module("d1c0a15e0001_ai_consent_authority.py").upgrade)
        conn.exec_driver_sql('INSERT INTO "user" (id) VALUES (1)')
        conn.exec_driver_sql(
            "INSERT INTO training_plan_generation_operation (user_id, idempotency_key, "
            "request_fingerprint, status, attempt_count, quota_reserved, created_at, updated_at) "
            "VALUES (1, 'historical-key', 'f', 'IN_PROGRESS', 1, false, now(), now())")
        conn.exec_driver_sql(
            "INSERT INTO ai_allowance_bucket (user_id, scope, feature, period_key, policy_version, "
            "granted, used, reserved, billed_releases, created_at, updated_at) "
            "VALUES (1, 'STARTER', 'coach', 'NEVER', 'v', 3, 0, 1, 0, now(), now())")
        conn.exec_driver_sql(
            "INSERT INTO ai_usage_reservation (public_id, user_id, bucket_id, feature, key_digest, "
            "state, provider_billed, created_at, expires_at) VALUES "
            "('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 1, 1, 'coach', 'd', 'RESERVED', false, now(), now())")
        _run(conn, head.upgrade)
        _run(conn, head.upgrade)  # reentrant
        assert conn.exec_driver_sql(
            "SELECT provider_dispatch_possible, allowance_metered FROM "
            "training_plan_generation_operation").one() == (None, None)
        assert conn.exec_driver_sql(
            "SELECT provider_billed, billing_evidence FROM ai_usage_reservation").one() == (False, None)
        assert conn.exec_driver_sql(
            "SELECT count(*) FROM training_plan_generation_allowance").scalar() == 0
        columns = {c["name"]: c for c in sa.inspect(conn).get_columns("ai_usage_reservation")}
        assert columns["provider_billed"]["nullable"] is True
        # Old serving code still writes its Boolean rows.
        conn.exec_driver_sql(
            "INSERT INTO ai_usage_reservation (public_id, user_id, bucket_id, feature, key_digest, "
            "state, provider_billed, created_at, expires_at) VALUES "
            "('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 1, 1, 'coach', 'e', 'RELEASED', true, now(), now())")
        conn.exec_driver_sql(
            "UPDATE training_plan_generation_operation SET allowance_metered = true")
        with pytest.raises(RuntimeError, match="must be retained"):
            _run(conn, head.downgrade)
        assert conn.exec_driver_sql(
            "SELECT count(*) FROM training_plan_generation_operation "
            "WHERE allowance_metered").scalar() == 1


def test_empty_schema_round_trips_and_binding_constraints_hold(migrate_engine):
    head = _module("b1e4f5a6c7d8_training_allowance_association.py")
    with migrate_engine.begin() as conn:
        _b0d3_schema(conn)
        _run(conn, head.upgrade)
        _run(conn, head.downgrade)
        assert not sa.inspect(conn).has_table("training_plan_generation_allowance")
        _run(conn, head.upgrade)
        conn.exec_driver_sql('INSERT INTO "user" (id) VALUES (1)')
        conn.exec_driver_sql(
            "INSERT INTO training_plan_generation_operation (user_id, idempotency_key, "
            "request_fingerprint, status, attempt_count, quota_reserved, created_at, updated_at, "
            "provider_dispatch_possible, allowance_metered) VALUES "
            "(1, 'k1', 'f', 'SUCCEEDED', 1, false, now(), now(), true, true), "
            "(1, 'k2', 'f', 'FAILED', 1, false, now(), now(), true, true)")
    insert = ("INSERT INTO training_plan_generation_allowance (operation_id, user_id, "
              "reservation_ref, state, created_at, updated_at) VALUES ({}, 1, '{}', '{}', now(), now())")
    with migrate_engine.begin() as conn:
        conn.exec_driver_sql(insert.format(1, "r" * 32, "RESERVED"))
    for operation_id, ref, state in ((2, "r" * 32, "RESERVED"),   # one hold, two operations
                                     (1, "s" * 32, "RESERVED"),   # two holds, one operation
                                     (2, "t" * 32, "UNRESERVED")):  # closed state vocabulary
        with pytest.raises(sa.exc.IntegrityError):
            with migrate_engine.begin() as conn:
                conn.exec_driver_sql(insert.format(operation_id, ref, state))
    with migrate_engine.begin() as conn:
        conn.exec_driver_sql('DELETE FROM training_plan_generation_operation WHERE id = 1')
        assert conn.exec_driver_sql(
            "SELECT count(*) FROM training_plan_generation_allowance").scalar() == 0


def test_create_all_first_schema_passes_both_verifiers(migrate_engine):
    from app.models import (
        AiAllowanceBucket, AiStarterGrant, AiUsageReservation, TrainingPlanGenerationAllowance,
        TrainingPlanGenerationOperation)
    with migrate_engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('CREATE TABLE training_plan (id INTEGER PRIMARY KEY)')
        for model in (AiStarterGrant, AiAllowanceBucket, AiUsageReservation,
                      TrainingPlanGenerationOperation, TrainingPlanGenerationAllowance):
            model.__table__.create(conn)
        _run(conn, _module("a9c1d2e3f4b5_ai_allowance_ledger.py").upgrade)
        _run(conn, _module("d3e4f5a6b7c8_add_training_plan_generation_operations.py").upgrade)
        _run(conn, _module("b0d3e4f5a6b7_training_dispatch_boundary.py").upgrade)
        _run(conn, _module("b1e4f5a6c7d8_training_allowance_association.py").upgrade)
        checks = {c["name"] for c in sa.inspect(conn).get_check_constraints(
            "training_plan_generation_allowance")}
        assert "ck_training_plan_generation_allowance_state" in checks


@pytest.mark.parametrize("recipient", ["openai", "bedrock"])
@pytest.mark.parametrize("registry_present", [False, True])
def test_premium_allowance_dispatch_never_substitutes_for_d1_consent(
        pg_generation_app, recipient, registry_present):
    """Real hold/marker + Premium still yields zero physical SDK calls."""
    from unittest.mock import Mock
    from ai_allowance_support import enable_subscriptions, premium_row
    from app.extensions import db
    from app.services import ai, ai_consent, ai_provider_call, ai_spend_guard, subscriptions
    from app.services.ai_allowances import AllowancePolicy, ScopePolicy
    from app.services.mobile_training_generation import store
    app, owners, monkeypatch = pg_generation_app
    _metered(app, monkeypatch)
    enable_subscriptions(app, monkeypatch)
    app.config["AI_ALLOWANCE_POLICY"] = AllowancePolicy(
        version="synthetic-premium-d1", premium=ScopePolicy(
            "MONTHLY", {"training_generation": 2}, 0))
    assert dict(ai_consent.POLICIES) == {}
    if registry_present:
        purpose, categories = ai_consent.BINDINGS["training_plan"]
        policy = ai_consent.Disclosure("synthetic-only", purpose, categories,
            (("bedrock", app_config.BEDROCK_MODEL), ("openai", ai.OPENAI_MODEL)), (("en", "a" * 64),))
        monkeypatch.setattr(ai_consent, "POLICIES", {policy.version: policy})
    sdk = Mock()
    with app.app_context():
        premium_row(owners[0])
        assert subscriptions.entitlement(owners[0])["effective_premium"] is True
        operation = store.claim(owners[0], "synthetic-d1-no-grant", _request().fingerprint)
        operation_id = operation.id
        db.session.rollback()
        store.authorize_dispatch(operation_id)
        assert not db.session().in_transaction()
        with ai_spend_guard.subject_scope(owners[0]), pytest.raises(ai_consent.ConsentDenied):
            with ai_provider_call.admit(feature="training_plan", provider=recipient,
                    payload=dict(model=ai.OPENAI_MODEL if recipient == "openai" else app_config.BEDROCK_MODEL,
                        messages=[{"role": "user", "content": "SYNTHETIC_OWN_FITNESS"}], max_tokens=100),
                    gate=False, charge=False) as admission:
                admission.create(sdk)
        sdk.assert_not_called()
        state = _state(owners[0])
        assert state.operations == [("IN_PROGRESS", True)]
        assert state.bindings == ["RESERVED"]
        assert state.holds == [("RESERVED", "UNKNOWN", None)]
        assert state.bucket == (0, 1)
        assert state.plans == 0
        # No grant was inferred from the entitlement, hold or marker.
        from app.models import AIConsentState, AIConsentEvent
        assert AIConsentState.query.count() == AIConsentEvent.query.count() == 0


@pytest.mark.parametrize("start", ["base", "d1c0a15e0001", "e7a1c3d5f9b2"])
def test_post_d1_full_chain_has_no_drift_and_preserves_authorities(migrate_engine, start):
    """Run real Alembic ancestry, preserving synthetic main-era evidence."""
    from flask import Flask
    from flask_migrate import Migrate, upgrade, check
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from app.extensions import db
    from app.models import User, AIConsentState, AIConsentEvent
    from ai_allowance_support import premium_row
    app = Flask("synthetic-post-d1-migrations")
    app.config.update(SQLALCHEMY_DATABASE_URI=migrate_engine.url.render_as_string(hide_password=False),
                      SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    Migrate(app, db, directory=str(ROOT / "migrations"))
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["a4d6f8b0c2e1"]
    assert len(list(script.walk_revisions())) == 58
    assert script.get_revision("a4d6f8b0c2e1").down_revision == "d7e3a9c1f5b8"
    assert script.get_revision("d7e3a9c1f5b8").down_revision == "f9c6d0a1b2e3"
    assert script.get_revision("f9c6d0a1b2e3").down_revision == "f8b2d4e6a0c3"
    assert script.get_revision("f8b2d4e6a0c3").down_revision == "e7a1c3d5f9b2"
    assert script.get_revision("e7a1c3d5f9b2").down_revision == "b1e4f5a6c7d8"
    assert script.get_revision("b1e4f5a6c7d8").down_revision == "d1c0a15e0001"
    tables = ("user", "apple_subscription", "ai_consent_state", "ai_consent_event",
              "ai_starter_grant", "ai_allowance_bucket", "ai_usage_reservation",
              "training_plan_generation_operation")
    with app.app_context():
        before = {}
        if start != "base":
            upgrade(revision=start)
            db.session.add(User(id=1, username="synthetic-migration", email="migration@example.invalid"))
            db.session.commit()
            premium_row(1)
            db.session.add(AIConsentState(owner_id=1, generation=2,
                grants={"synthetic": {"state": "revoked", "evidence": "retain-exactly"}},
                changed_at=datetime(2026, 1, 1)))
            db.session.add(AIConsentEvent(owner_id=1, request_id="synthetic-revocation", request_digest="a"*64,
                generation=2, purpose="synthetic", version="synthetic", action="revoke", locale="en",
                text_digest="b"*64, contract_digest="c"*64, occurred_at=datetime(2026, 1, 1)))
            db.session.commit()
            with db.engine.begin() as conn:
                conn.exec_driver_sql("INSERT INTO ai_starter_grant VALUES (1, 'synthetic', now())")
                conn.exec_driver_sql("INSERT INTO ai_allowance_bucket (id,user_id,scope,feature,period_key,policy_version,granted,used,reserved,billed_releases,created_at,updated_at) VALUES (1,1,'STARTER','training_generation','NEVER','synthetic',5,1,1,0,now(),now())")
                conn.exec_driver_sql("INSERT INTO ai_usage_reservation (public_id,user_id,bucket_id,feature,key_digest,state,provider_billed,created_at,expires_at) VALUES (repeat('a',32),1,1,'training_generation','synthetic','RESERVED',false,now(),now())")
                conn.exec_driver_sql("INSERT INTO training_plan_generation_operation (user_id,idempotency_key,request_fingerprint,status,attempt_count,quota_reserved,created_at,updated_at,provider_dispatch_possible) VALUES (1,'synthetic','fingerprint','IN_PROGRESS',1,false,now(),now(),true)")
                for name in tables:
                    result = conn.exec_driver_sql(f'SELECT * FROM "{name}"')
                    before[name] = (list(result.keys()), result.all())
        db.session.remove()
        upgrade()
        check()  # model/schema comparison against the actual full chain
        with db.engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == "a4d6f8b0c2e1"
            for name, (columns, rows) in before.items():
                projection = ','.join('"' + c + '"' for c in columns)
                assert conn.exec_driver_sql(f'SELECT {projection} FROM "{name}"').all() == rows
            assert conn.exec_driver_sql("SELECT count(*) FROM training_plan_generation_allowance").scalar_one() == 0
            assert conn.exec_driver_sql("SELECT count(*) FROM training_plan_generation_operation WHERE allowance_metered IS NOT NULL").scalar_one() == 0
            assert conn.exec_driver_sql("SELECT count(*) FROM ai_usage_reservation WHERE billing_evidence IS NOT NULL").scalar_one() == 0
            assert conn.exec_driver_sql("SELECT count(*) FROM training_plan_generation_operation WHERE provider_attempt_possible IS NOT NULL").scalar_one() == 0
        # Compatible create_all/main-era table reentry must preserve the same evidence.
        with db.engine.begin() as conn:
            _run(conn, _module("b1e4f5a6c7d8_training_allowance_association.py").upgrade)
            _run(conn, _module("e7a1c3d5f9b2_training_attempt_evidence.py").upgrade)
        db.session.remove()
        db.engine.dispose()
