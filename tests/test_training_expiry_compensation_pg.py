"""Founder P1-A/P2-C/P3-C/P4-A on disposable PostgreSQL, no real SDK."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from tests.test_training_allowance_zero_attempt_pg import (  # noqa: F401
    chain, pg_generation_app, Crash, consent, generate, _age, snap)

pytestmark = pytest.mark.pg_concurrency


@pytest.fixture
def expired(chain):
    from app.extensions import db
    from app.models import TrainingPlanGenerationAllowance, TrainingPlanGenerationOperation
    from app.services import ai_allowances as A
    from app.services.mobile_training_generation import store
    owner, other = chain.owners
    with chain.app.app_context():
        consent(owner, chain.policy, "affirmative_grant", "compensation-grant")
        original = store.authorize_dispatch

        def crash(op):
            original(op)
            raise Crash()
        with chain.monkeypatch.context() as patch:
            patch.setattr(store, "authorize_dispatch", crash)
            with pytest.raises(Crash):
                generate(owner, "compensation-expired")
        _age(owner)
        store.retire_abandoned(owner)
        A.expire_abandoned()
        binding = TrainingPlanGenerationAllowance.query.filter_by(user_id=owner).one()
        binding.state = "EXPIRED"  # simulate settlement already observed on main
        operation_id = binding.operation_id
        db.session.commit()
        assert snap(owner).bucket == (1, 0, 0)
    yield chain, owner, other, operation_id
    assert sum(chain.calls.values()) == 0


def apply(expired, owner=None):
    from app.services.ai_allowances import compensate_training_expiry
    chain, user_id, _, op = expired
    with chain.app.app_context():
        return compensate_training_expiry(user_id if owner is None else owner, op,
                                          now=getattr(chain, "compensation_now", None))


def state(expired):
    from app.extensions import db
    from app.models import AiAllowanceBucket, AiUsageReservation, TrainingAllowanceCompensation
    chain, owner, _, _ = expired
    with chain.app.app_context():
        db.session.remove()
        buckets = {b.id: (b.used, b.reserved, b.granted, b.billed_releases)
                   for b in AiAllowanceBucket.query.filter_by(user_id=owner, feature="training_generation")}
        holds = [(r.state, r.outcome, r.billing_evidence, r.provider_billed)
                 for r in AiUsageReservation.query.filter_by(user_id=owner)]
        receipts = [(r.reservation_id, r.operation_id, r.user_id, r.bucket_id, r.proof_version, r.reason)
                    for r in TrainingAllowanceCompensation.query.all()]
        return buckets, holds, receipts


def test_original_expiry_history_and_lost_ack_replay(expired):
    before = state(expired)
    assert apply(expired) == "COMPENSATION_APPLIED"
    after = state(expired)
    assert after[1] == before[1] == [("EXPIRED", "expired", "UNKNOWN", None)]
    assert list(after[0].values()) == [(0, 0, 2, 0)]
    assert len(after[2]) == 1 and after[2][0][4:] == ("TRACKED_TRAINING_V1", "EXPIRED_ZERO_ATTEMPTS")
    for _ in range(3):
        assert apply(expired) == "COMPENSATION_ALREADY_APPLIED"
        assert state(expired) == after


@pytest.mark.parametrize("sql", [
    "UPDATE training_plan_generation_operation SET provider_attempt_possible=true",
    "UPDATE training_plan_generation_operation SET provider_attempt_possible=NULL",
    "UPDATE training_plan_generation_operation SET status='SUCCEEDED'",
    "UPDATE training_plan_generation_operation SET status='IN_PROGRESS'",
    "UPDATE training_plan_generation_operation SET status='GENERATED'",
    "UPDATE training_plan_generation_operation SET allowance_metered=false",
    "UPDATE training_plan_generation_operation SET provider_dispatch_possible=false",
    "UPDATE training_plan_generation_operation SET completed_at=NULL",
    "UPDATE training_plan_generation_operation SET candidate_plan_data='{}'",
    "UPDATE training_plan_generation_operation SET training_plan_id=1234",
    "UPDATE training_plan_generation_operation SET quota_reserved=true",
    "DELETE FROM training_plan_generation_allowance",
    "DELETE FROM ai_usage_reservation",
    "DELETE FROM ai_allowance_bucket",
    "UPDATE ai_usage_reservation SET feature='nutrition_generation'",
    "UPDATE ai_allowance_bucket SET feature='nutrition_generation' WHERE feature='training_generation'",
    "UPDATE ai_usage_reservation SET billing_evidence='CONFIRMED_BILLED',provider_billed=true",
    "UPDATE ai_usage_reservation SET billing_evidence=NULL,provider_billed=false",
    "UPDATE ai_usage_reservation SET outcome='provider_error'",
    "UPDATE ai_usage_reservation SET finalized_at=NULL",
    "UPDATE ai_usage_reservation SET state='COMMITTED'",
    "UPDATE ai_usage_reservation SET expires_at=created_at",
    "UPDATE training_plan_generation_allowance SET reservation_ref=repeat('f',32)",
    "UPDATE training_plan_generation_allowance SET state='COMMITTED'",
    "UPDATE ai_allowance_bucket SET period_key='invalid'",
    "UPDATE ai_allowance_bucket SET used=0",
    "DELETE FROM ai_starter_grant",
    "UPDATE ai_starter_grant SET policy_version='inconsistent'",
])
def test_incomplete_or_contradictory_evidence_never_corrects(expired, sql):
    from app.extensions import db
    chain, _, _, _ = expired
    with chain.app.app_context(), db.engine.begin() as conn:
        conn.exec_driver_sql(sql)
    before = state(expired)
    assert apply(expired) not in ("COMPENSATION_APPLIED", "COMPENSATION_ALREADY_APPLIED")
    assert state(expired) == before


@pytest.mark.parametrize("authority", ["operation", "binding", "reservation", "bucket", "caller"])
def test_owner_mismatch_never_corrects(expired, authority):
    from app.extensions import db
    chain, owner, other, _ = expired
    tables = dict(operation="training_plan_generation_operation", binding="training_plan_generation_allowance",
                  reservation="ai_usage_reservation", bucket="ai_allowance_bucket")
    if authority != "caller":
        with chain.app.app_context(), db.engine.begin() as conn:
            conn.exec_driver_sql(f"UPDATE {tables[authority]} SET user_id=%s", (other,))
    before = state(expired)
    assert apply(expired, other if authority == "caller" else owner) != "COMPENSATION_APPLIED"
    assert state(expired) == before


@pytest.mark.parametrize("scope,period", [("STARTER", "NEVER"), ("PREMIUM", "2026-W42"),
                                         ("PREMIUM", "2026-09"), ("PREMIUM", "2026-W41")])
@pytest.mark.parametrize("entitlement", ["starter", "premium", "expired", "renewal", "reactivation", "new_policy"])
def test_only_original_bucket_changes_despite_current_entitlement(expired, scope, period, entitlement):
    from app.extensions import db
    from app.models import (AiAllowanceBucket, AiStarterGrant, AppleSubscription,
                            AiUsageReservation, TrainingPlanGenerationOperation,
                            TrainingPlanGenerationAllowance)
    from ai_allowance_support import premium_row, fixture_policy
    from app.services import subscriptions
    chain, owner, _, _ = expired
    # Any consultation of current entitlement would make these tests fail.
    chain.monkeypatch.setattr(subscriptions, "entitlement", lambda *_: pytest.fail("current entitlement consulted"))
    chain.app.config["AI_ALLOWANCE_POLICY"] = None
    with chain.app.app_context():
        original = AiAllowanceBucket.query.filter_by(user_id=owner, feature="training_generation").one()
        original.scope, original.period_key, original.policy_version = scope, period, "historical-policy"
        old_id = original.id
        if scope == "PREMIUM":
            reserved_at = (datetime(2026, 9, 15, tzinfo=timezone.utc) if period == "2026-09"
                           else datetime(2026, 10, 7 if period == "2026-W41" else 15,
                                         tzinfo=timezone.utc))
            chain.compensation_now = datetime(2026, 10, 20, tzinfo=timezone.utc)
            original.created_at = reserved_at
            hold = AiUsageReservation.query.filter_by(user_id=owner).one()
            binding = TrainingPlanGenerationAllowance.query.filter_by(user_id=owner).one()
            operation = TrainingPlanGenerationOperation.query.filter_by(user_id=owner).one()
            hold.created_at, hold.expires_at = reserved_at, reserved_at + timedelta(minutes=15)
            hold.finalized_at = reserved_at + timedelta(minutes=16)
            binding.created_at = operation.created_at = reserved_at.replace(tzinfo=None)
            operation.completed_at = (reserved_at + timedelta(minutes=16)).replace(tzinfo=None)
        if scope == "STARTER":
            db.session.get(AiStarterGrant, owner).policy_version = "historical-policy"
        if entitlement != "starter":
            row = premium_row(owner, expires_in=(timedelta(days=-1) if entitlement == "expired"
                                               else timedelta(days=20)))
            if entitlement == "renewal":
                row.latest_transaction_id = "renewed-fixture"
                row.expires_at += timedelta(days=30)
            elif entitlement == "reactivation":
                row.auto_renew = False
                row.status = 2
                db.session.commit()
                row.status, row.auto_renew = 1, True
            elif entitlement == "new_policy":
                chain.app.config["AI_ALLOWANCE_POLICY"] = fixture_policy(
                    premium=True, period="MONTHLY", version="replacement-policy")
        now = datetime.now(timezone.utc)
        current = AiAllowanceBucket(user_id=owner, scope="PREMIUM", feature="training_generation",
            period_key="2026-10", policy_version="changed-policy", granted=20, used=7,
            reserved=2, billed_releases=3, created_at=now, updated_at=now)
        db.session.add(current)
        db.session.commit()
        current_id = current.id
    before = state(expired)
    assert apply(expired) == "COMPENSATION_APPLIED"
    after = state(expired)
    assert after[0][old_id] == (0, 0, 2, 0)
    assert after[0][current_id] == before[0][current_id] == (7, 2, 20, 3)
    assert after[2][0][3] == old_id


@pytest.mark.parametrize("boundary", ["before_journal", "after_journal", "before_bucket", "after_bucket"])
def test_crash_rolls_back_both_facts(expired, boundary):
    from app.extensions import db
    chain, _, _, _ = expired
    before = state(expired)
    event = "before_cursor_execute" if boundary.startswith("before") else "after_cursor_execute"

    def crash(conn, cursor, statement, parameters, context, many):
        journal = statement.startswith("INSERT INTO training_allowance_compensation")
        bucket = statement.startswith("UPDATE ai_allowance_bucket")
        if ("journal" in boundary and journal) or ("bucket" in boundary and bucket):
            raise Crash()
    with chain.app.app_context():
        sa.event.listen(db.engine, event, crash)
        try:
            with pytest.raises(Crash):
                apply(expired)
        finally:
            sa.event.remove(db.engine, event, crash)
            db.session.remove()
    assert state(expired) == before
    assert apply(expired) == "COMPENSATION_APPLIED"
    assert len(state(expired)[2]) == 1


def test_commit_acknowledgment_loss_recovers_receipt(expired):
    from app.extensions import db
    from app.services.ai_allowances import compensate_training_expiry
    chain, owner, _, op = expired
    with chain.app.app_context():
        db.session.execute(sa.text("UPDATE ai_allowance_bucket SET used=2 WHERE feature='training_generation'"))
        db.session.commit()  # one unrelated consumption, plus this expired unit
        session = db.session()
        commit = session.commit
        def lost_ack():
            commit()
            raise Crash()
        with chain.monkeypatch.context() as patch:
            patch.setattr(session, "commit", lost_ack)
            with pytest.raises(Crash):
                compensate_training_expiry(owner, op)
        db.session.remove()
    assert apply(expired) == "COMPENSATION_ALREADY_APPLIED"
    assert len(state(expired)[2]) == 1 and list(state(expired)[0].values())[0][0] == 1


@pytest.mark.parametrize("competitor", ["compensation", "settlement", "expiry", "retention", "deletion"])
def test_concurrent_transactions(expired, competitor):
    from app.extensions import db
    from app.services import ai_allowances as A
    from app.services.mobile_training_generation import allowance
    chain, owner, _, op = expired
    barrier = threading.Barrier(2)
    results, errors = [], []

    def run(action):
        with chain.app.app_context():
            try:
                barrier.wait(5)
                results.append(action())
            except BaseException as error:
                errors.append(error)
            finally:
                db.session.remove()

    def delete():
        with db.engine.begin() as conn:
            conn.exec_driver_sql('SELECT id FROM "user" WHERE id=%s FOR UPDATE', (owner,))
            conn.exec_driver_sql("DELETE FROM user_session WHERE user_id=%s", (owner,))
            conn.exec_driver_sql('DELETE FROM "user" WHERE id=%s', (owner,))
        return "DELETED"

    actions = dict(compensation=lambda: A.compensate_training_expiry(owner, op),
                   settlement=lambda: allowance.settle(op), expiry=A.expire_abandoned,
                   retention=A.purge_retained, deletion=delete)
    workers = [threading.Thread(target=run, args=(action,)) for action in
               (lambda: A.compensate_training_expiry(owner, op), actions[competitor])]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)
    assert all(not worker.is_alive() for worker in workers) and not errors
    after = state(expired)
    if competitor == "deletion":
        assert after == ({}, [], [])
        assert apply(expired) == "COMPENSATION_ACCOUNT_UNAVAILABLE"
    else:
        assert len(after[2]) == 1 and list(after[0].values()) == [(0, 0, 2, 0)]
        assert apply(expired) == "COMPENSATION_ALREADY_APPLIED"


def test_pre_purge_compensation_retains_receipt_then_purges_as_one_unit(expired):
    from app.extensions import db
    from app.models import TrainingAllowanceCompensation
    from app.services import ai_allowances as A
    chain, _, _, _ = expired
    future = datetime.now(timezone.utc) + timedelta(days=91)
    with chain.app.app_context():
        assert A.purge_retained(now=future) == (0, 0)
        assert TrainingAllowanceCompensation.query.count() == 1
        assert A.purge_retained(now=future + timedelta(days=89)) == (0, 0)
        assert A.purge_retained(now=future + timedelta(days=91)) == (1, 0)
    assert state(expired)[1:] == ([], [])
    assert apply(expired) == "COMPENSATION_EVIDENCE_UNAVAILABLE"
    assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]


def test_bounded_reconcile_revisits_already_expired_binding(expired):
    from app.services.mobile_training_generation import allowance
    chain, owner, _, _ = expired
    with chain.app.app_context():
        allowance.settle_pending(owner)
        allowance.settle_pending(owner)
    assert len(state(expired)[2]) == 1 and list(state(expired)[0].values()) == [(0, 0, 2, 0)]


@pytest.mark.parametrize("flag", ["AI_ALLOWANCE_LEDGER_ENABLED", "AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED"])
def test_compensation_off_has_zero_sql(expired, flag):
    from app.extensions import db
    chain, _, _, _ = expired
    before = state(expired)
    chain.app.config[flag] = False
    statements = []
    def record(*args):
        statements.append(args[2])
    with chain.app.app_context():
        sa.event.listen(db.engine, "before_cursor_execute", record)
        try:
            assert apply(expired) == "COMPENSATION_DISABLED"
        finally:
            sa.event.remove(db.engine, "before_cursor_execute", record)
    assert statements == [] and state(expired) == before


def test_owner_then_operation_nowait_never_blocks_reverse_dispatch(expired):
    from app.extensions import db
    chain, _, _, op = expired
    with chain.app.app_context():
        blocker = db.engine.connect()
        transaction = blocker.begin()
        blocker.exec_driver_sql("SELECT id FROM training_plan_generation_operation WHERE id=%s FOR UPDATE", (op,))
        try:
            assert apply(expired) == "COMPENSATION_DEFERRED"
            assert state(expired)[2] == []
        finally:
            transaction.rollback()
            blocker.close()
    assert apply(expired) == "COMPENSATION_APPLIED"


from tests.test_mobile_training_allowance_pg import (  # noqa: E402,F401
    migrate_engine, _module, _run)
from tests.test_training_allowance_zero_attempt_pg import _b1e4_schema  # noqa: E402

MIGRATION = "f8b2d4e6a0c3_training_expiry_compensation.py"
TABLE = "training_allowance_compensation"


def test_compensation_migration_fresh_reentry_and_model_ddl(migrate_engine):
    from app.models import TrainingAllowanceCompensation
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        assert migration.down_revision == "e7a1c3d5f9b2" and migration.expand_contract == "expand"
        _run(conn, migration.upgrade)
        _run(conn, migration.upgrade)
        assert sa.inspect(conn).get_pk_constraint(TABLE)["constrained_columns"] == ["reservation_id"]
        _run(conn, migration.downgrade)
        TrainingAllowanceCompensation.__table__.create(conn)
        _run(conn, migration.upgrade)  # create_all-first compatibility


@pytest.mark.parametrize("ddl", [
    "ALTER TABLE training_allowance_compensation DROP CONSTRAINT uq_training_compensation_operation",
    "ALTER TABLE training_allowance_compensation ALTER COLUMN reason TYPE VARCHAR(64)",
    "ALTER TABLE training_allowance_compensation ALTER COLUMN applied_at DROP NOT NULL",
    "ALTER TABLE training_allowance_compensation ALTER COLUMN proof_version SET DEFAULT 'TRACKED_TRAINING_V1'",
    "ALTER TABLE training_allowance_compensation DROP CONSTRAINT ck_training_compensation_reason",
    "ALTER TABLE training_allowance_compensation ADD COLUMN unexpected TEXT",
])
def test_compensation_migration_rejects_schema_drift(migrate_engine, ddl):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        _run(conn, migration.upgrade)
        conn.exec_driver_sql(ddl)
        with pytest.raises(RuntimeError, match="incompatible"):
            _run(conn, migration.upgrade)


def test_compensation_evidence_blocks_downgrade(expired):
    from app.extensions import db
    assert apply(expired) == "COMPENSATION_APPLIED"
    chain, _, _, _ = expired
    with chain.app.app_context(), db.engine.begin() as conn:
        with pytest.raises(RuntimeError, match="must be retained"):
            _run(conn, _module(MIGRATION).downgrade)


def test_database_uniqueness_is_independent_guard(expired):
    from app.extensions import db
    from sqlalchemy.exc import IntegrityError
    assert apply(expired) == "COMPENSATION_APPLIED"
    chain, _, _, _ = expired
    with chain.app.app_context(), db.engine.connect() as conn:
        with pytest.raises(IntegrityError):
            conn.exec_driver_sql("INSERT INTO training_allowance_compensation SELECT * FROM training_allowance_compensation")
        conn.rollback()
    assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]


def test_reconcile_bound_and_rotation_do_not_starve(expired):
    from app.extensions import db
    from app.models import AiAllowanceBucket, TrainingPlanGenerationAllowance, TrainingPlanGenerationOperation
    from app.services.mobile_training_generation import allowance
    chain, owner, _, op = expired
    with chain.app.app_context():
        # Four unverifiable historical bindings before the real eligible one.
        real = db.session.get(TrainingPlanGenerationAllowance, op)
        real.updated_at = datetime.utcnow()
        for index in range(4):
            old = datetime.utcnow() - timedelta(days=2)
            operation = TrainingPlanGenerationOperation(user_id=owner,
                idempotency_key=f"missing-evidence-{index}", request_fingerprint="synthetic",
                status="FAILED", allowance_metered=True, provider_dispatch_possible=True,
                provider_attempt_possible=False, completed_at=old, error_code="synthetic",
                created_at=old, updated_at=old)
            db.session.add(operation)
            db.session.flush()
            db.session.add(TrainingPlanGenerationAllowance(operation_id=operation.id, user_id=owner,
                reservation_ref=f"{index:032x}", state="EXPIRED", created_at=old, updated_at=old))
        db.session.commit()
        allowance.settle_pending(owner)
        assert state(expired)[2] == []  # exactly the first three candidates
        allowance.settle_pending(owner)
        assert len(state(expired)[2]) == 1
        assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]


def test_expiry_and_compensation_race_retries_without_early_credit(expired):
    from app.extensions import db
    from app.services import ai_allowances as A
    from app.services.mobile_training_generation import allowance
    chain, owner, _, op = expired
    with chain.app.app_context(), db.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE ai_usage_reservation SET state='RESERVED',outcome=NULL,finalized_at=NULL")
        conn.exec_driver_sql("UPDATE ai_allowance_bucket SET used=0,reserved=1 WHERE feature='training_generation'")
        conn.exec_driver_sql("UPDATE training_plan_generation_allowance SET state='RESERVED'")
    barrier, results, errors = threading.Barrier(2), [], []
    def worker(action):
        with chain.app.app_context():
            try:
                barrier.wait(5)
                results.append(action())
            except BaseException as error:
                errors.append(error)
            finally:
                db.session.remove()
    workers = [threading.Thread(target=worker, args=(action,)) for action in
               (A.expire_abandoned, lambda: A.compensate_training_expiry(owner, op))]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(10)
    assert all(not thread.is_alive() for thread in workers) and not errors
    with chain.app.app_context():
        allowance.settle_pending(owner)
    assert apply(expired) == "COMPENSATION_ALREADY_APPLIED"
    assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]


def test_retention_and_compensation_race_at_deletion_deadline(expired):
    from app.extensions import db
    from app.services import ai_allowances as A
    chain, owner, _, op = expired
    future = datetime.now(timezone.utc) + timedelta(days=91)
    barrier, results, failures = threading.Barrier(2), [], []
    def worker(action):
        with chain.app.app_context():
            try:
                barrier.wait(5)
                results.append(action())
            except BaseException as error:
                failures.append(error)
            finally:
                db.session.remove()
    workers = [threading.Thread(target=worker, args=(action,)) for action in
               (lambda: A.purge_retained(now=future), lambda: A.compensate_training_expiry(owner, op, now=future))]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(10)
    assert all(not thread.is_alive() for thread in workers) and not failures
    assert len(state(expired)[2]) == 1 and state(expired)[1][0][0] == "EXPIRED"
    assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]


def test_current_premium_week_is_corrected_in_place(expired):
    from app.extensions import db
    from app.models import AiAllowanceBucket
    from app.services.ai_allowances import policy
    chain, owner, _, _ = expired
    with chain.app.app_context():
        bucket = AiAllowanceBucket.query.filter_by(user_id=owner, feature="training_generation").one()
        bucket.scope = "PREMIUM"
        bucket.period_key = policy.period_key("WEEKLY", bucket.created_at)
        db.session.commit()
    assert apply(expired) == "COMPENSATION_APPLIED"
    assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]


def test_conflicting_receipt_is_never_reapplied(expired):
    from app.extensions import db
    chain, _, other, _ = expired
    assert apply(expired) == "COMPENSATION_APPLIED"
    with chain.app.app_context(), db.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE training_allowance_compensation SET user_id=%s", (other,))
    before = state(expired)
    assert apply(expired) == "COMPENSATION_CONTRADICTORY_RECEIPT"
    assert state(expired) == before


def test_missing_expired_evidence_becomes_unobservable_and_stops_scan(expired):
    from app.extensions import db
    from app.models import TrainingPlanGenerationAllowance
    from app.services.mobile_training_generation import allowance
    chain, owner, _, op = expired
    with chain.app.app_context():
        db.session.execute(sa.text("DELETE FROM ai_usage_reservation"))
        db.session.commit()
        allowance.settle_pending(owner)
        assert db.session.get(TrainingPlanGenerationAllowance, op).state == "UNOBSERVABLE"
        allowance.settle_pending(owner)
    assert state(expired)[2] == []


@pytest.mark.parametrize("sql", [
    "UPDATE ai_allowance_bucket SET created_at=created_at+interval '1 day' WHERE feature='training_generation'",
    "UPDATE ai_starter_grant SET granted_at=granted_at+interval '1 day'",
    "UPDATE ai_allowance_bucket SET scope='PREMIUM',period_key='2026-01' WHERE feature='training_generation'",
])
def test_original_bucket_time_provenance_must_be_consistent(expired, sql):
    from app.extensions import db
    chain, _, _, _ = expired
    with chain.app.app_context(), db.engine.begin() as conn:
        conn.exec_driver_sql(sql)
    before = state(expired)
    assert apply(expired) not in ("COMPENSATION_APPLIED", "COMPENSATION_ALREADY_APPLIED")
    assert state(expired) == before
