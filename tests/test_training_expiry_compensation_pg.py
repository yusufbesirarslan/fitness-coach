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


def test_counter_contradiction_can_age_out_without_refund(expired):
    from app.extensions import db
    from app.services import ai_allowances as A
    chain, _, _, _ = expired
    with chain.app.app_context():
        db.session.execute(sa.text("UPDATE ai_allowance_bucket SET used=0 WHERE feature='training_generation'"))
        db.session.commit()
    assert apply(expired) == "COMPENSATION_COUNTER_UNAVAILABLE"
    before = state(expired)
    with chain.app.app_context():
        assert A.purge_retained(now=datetime.now(timezone.utc) + timedelta(days=91)) == (1, 0)
    after = state(expired)
    assert after[0] == before[0] and after[1:] == ([], [])
    assert apply(expired) == "COMPENSATION_EVIDENCE_UNAVAILABLE"


# Original independent reviewer assertions, preserved verbatim for P2 closure.
@pytest.mark.parametrize('ddl',[
 "ALTER TABLE training_allowance_compensation DROP CONSTRAINT ck_training_compensation_reason; ALTER TABLE training_allowance_compensation ADD CONSTRAINT ck_training_compensation_reason CHECK (reason = 'EXPIRED_ZERO_ATTEMPTS') NOT VALID",
 "ALTER TABLE training_allowance_compensation DROP CONSTRAINT ck_training_compensation_proof; ALTER TABLE training_allowance_compensation ADD CONSTRAINT ck_training_compensation_proof CHECK (proof_version = 'TRACKED_TRAINING_V1') NOT VALID",
 "ALTER TABLE training_allowance_compensation ADD CONSTRAINT reviewer_extra_owner UNIQUE(user_id)",
 "ALTER TABLE training_allowance_compensation DROP CONSTRAINT training_allowance_compensation_user_id_fkey; ALTER TABLE training_allowance_compensation ADD CONSTRAINT training_allowance_compensation_user_id_fkey FOREIGN KEY (user_id) REFERENCES \"user\"(id) ON DELETE CASCADE NOT VALID",
 "ALTER TABLE training_allowance_compensation DROP CONSTRAINT training_allowance_compensation_bucket_id_fkey; ALTER TABLE training_allowance_compensation ADD FOREIGN KEY (bucket_id) REFERENCES ai_allowance_bucket(id) ON DELETE RESTRICT",
 "ALTER TABLE training_allowance_compensation DROP CONSTRAINT ck_training_compensation_reason; ALTER TABLE training_allowance_compensation ADD CONSTRAINT ck_training_compensation_reason CHECK (reason <> 'EXPIRED_ZERO_ATTEMPTS')",
])
def test_reentry_rejects_incompatible_schema(migrate_engine,ddl):
 with migrate_engine.begin() as c:
  _b1e4_schema(c); m=_module(MIGRATION); _run(c,m.upgrade)
  c.exec_driver_sql(ddl)
  with pytest.raises(RuntimeError,match='incompatible'):
   _run(c,m.upgrade)



def test_retention_argument_does_not_shorten_compensation_audit(expired):
 from app.services import ai_allowances as A
 c,*_=expired
 assert apply(expired)=='COMPENSATION_APPLIED'
 with c.app.app_context():
  assert A.purge_retained(now=datetime.now(timezone.utc)+timedelta(days=2),retention=timedelta(days=1))==(0,0)
 assert len(state(expired)[2])==1



def test_existing_invalid_receipt_cannot_be_adopted(expired):
 from app.extensions import db
 c,*_=expired
 assert apply(expired)=='COMPENSATION_APPLIED'
 with c.app.app_context(),db.engine.begin() as conn:
  conn.exec_driver_sql('ALTER TABLE training_allowance_compensation DROP CONSTRAINT ck_training_compensation_reason')
  conn.exec_driver_sql("UPDATE training_allowance_compensation SET reason='INVALID'")
  conn.exec_driver_sql("ALTER TABLE training_allowance_compensation ADD CONSTRAINT ck_training_compensation_reason CHECK(reason='EXPIRED_ZERO_ATTEMPTS') NOT VALID")
  assert conn.exec_driver_sql("SELECT convalidated FROM pg_constraint WHERE conname='ck_training_compensation_reason'").scalar_one() is False
  with pytest.raises(RuntimeError,match='incompatible'):
   _run(conn,_module(MIGRATION).upgrade)



def test_wrong_schema_foreign_key_refused(migrate_engine):
 with migrate_engine.begin() as conn:
  _b1e4_schema(conn);m=_module(MIGRATION);_run(conn,m.upgrade)
  conn.exec_driver_sql('CREATE SCHEMA reviewer_shadow')
  conn.exec_driver_sql('CREATE TABLE reviewer_shadow."user" (id INTEGER PRIMARY KEY)')
  conn.exec_driver_sql('ALTER TABLE training_allowance_compensation DROP CONSTRAINT training_allowance_compensation_user_id_fkey')
  conn.exec_driver_sql('ALTER TABLE training_allowance_compensation ADD FOREIGN KEY (user_id) REFERENCES reviewer_shadow."user"(id) ON DELETE CASCADE')
  try:
   with pytest.raises(RuntimeError,match='incompatible'):_run(conn,m.upgrade)
  finally:conn.exec_driver_sql('DROP SCHEMA reviewer_shadow CASCADE')



@pytest.mark.parametrize('ddl', [
    # Effective uniqueness includes standalone, partial and expression indexes.
    'CREATE UNIQUE INDEX extra_owner ON training_allowance_compensation(user_id)',
    "CREATE UNIQUE INDEX extra_partial ON training_allowance_compensation(user_id) WHERE reason='EXPIRED_ZERO_ATTEMPTS'",
    'CREATE UNIQUE INDEX extra_expression ON training_allowance_compensation((user_id+0))',
    'ALTER TABLE training_allowance_compensation DROP CONSTRAINT uq_training_compensation_operation; ALTER TABLE training_allowance_compensation ADD CONSTRAINT uq_training_compensation_operation UNIQUE(operation_id,user_id)',
    'ALTER TABLE training_allowance_compensation DROP CONSTRAINT uq_training_compensation_operation; ALTER TABLE training_allowance_compensation ADD CONSTRAINT uq_training_compensation_operation UNIQUE(operation_id) DEFERRABLE INITIALLY DEFERRED',
    'ALTER TABLE training_allowance_compensation RENAME CONSTRAINT uq_training_compensation_operation TO wrong_identity',
    'ALTER TABLE training_allowance_compensation RENAME CONSTRAINT ck_training_compensation_reason TO wrong_identity',
    'ALTER TABLE training_allowance_compensation DROP CONSTRAINT training_allowance_compensation_pkey; ALTER TABLE training_allowance_compensation ADD PRIMARY KEY(reservation_id,operation_id)',
    'ALTER TABLE training_allowance_compensation DROP CONSTRAINT training_allowance_compensation_bucket_id_fkey; ALTER TABLE training_allowance_compensation ADD FOREIGN KEY(bucket_id) REFERENCES ai_usage_reservation(id) ON DELETE CASCADE',
    'ALTER TABLE ai_allowance_bucket ADD CONSTRAINT bucket_owner_unique UNIQUE(user_id); ALTER TABLE training_allowance_compensation DROP CONSTRAINT training_allowance_compensation_bucket_id_fkey; ALTER TABLE training_allowance_compensation ADD FOREIGN KEY(bucket_id) REFERENCES ai_allowance_bucket(user_id) ON DELETE CASCADE',
    'ALTER TABLE training_allowance_compensation DROP CONSTRAINT training_allowance_compensation_user_id_fkey; ALTER TABLE training_allowance_compensation ADD FOREIGN KEY(user_id) REFERENCES "user"(id) ON DELETE CASCADE ON UPDATE CASCADE',
    'ALTER TABLE training_allowance_compensation DROP CONSTRAINT training_allowance_compensation_user_id_fkey; ALTER TABLE training_allowance_compensation ADD FOREIGN KEY(user_id) REFERENCES "user"(id) ON DELETE CASCADE DEFERRABLE',
    'ALTER TABLE training_allowance_compensation ADD CHECK(user_id=1)',
    "ALTER TABLE training_allowance_compensation DROP CONSTRAINT ck_training_compensation_reason; ALTER TABLE training_allowance_compensation ADD CONSTRAINT ck_training_compensation_reason CHECK(reason='EXPIRED_ ZERO_ATTEMPTS')",
    "ALTER TABLE training_allowance_compensation DROP CONSTRAINT ck_training_compensation_proof; ALTER TABLE training_allowance_compensation ADD CONSTRAINT ck_training_compensation_proof CHECK(proof_version='TRACKED_TRAINING_V1' OR proof_version='OTHER')",
    'ALTER TABLE training_allowance_compensation ALTER COLUMN applied_at TYPE timestamp without time zone',
    'ALTER TABLE training_allowance_compensation ALTER COLUMN operation_id TYPE bigint',
    'ALTER TABLE training_allowance_compensation ALTER COLUMN reason DROP NOT NULL',
    'ALTER TABLE training_allowance_compensation ALTER COLUMN reservation_id ADD GENERATED BY DEFAULT AS IDENTITY',
    'ALTER TABLE training_allowance_compensation ENABLE ROW LEVEL SECURITY',
    'ALTER TABLE training_allowance_compensation DISABLE TRIGGER ALL',
])
def test_reentry_rejects_additional_effective_schema_changes(migrate_engine, ddl):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        _run(conn, migration.upgrade)
        conn.exec_driver_sql(ddl)
        with pytest.raises(RuntimeError, match='incompatible'):
            _run(conn, migration.upgrade)


@pytest.mark.parametrize('expression', [
    "((reason)::text = ('EXPIRED_ZERO_ATTEMPTS'::character varying)::text)",
    "(((reason))) = ((('EXPIRED_ZERO_ATTEMPTS')))",
])
def test_reentry_accepts_only_harmless_postgresql_check_formatting(migrate_engine, expression):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        _run(conn, migration.upgrade)
        conn.exec_driver_sql('ALTER TABLE training_allowance_compensation DROP CONSTRAINT ck_training_compensation_reason')
        conn.exec_driver_sql('ALTER TABLE training_allowance_compensation ADD CONSTRAINT ck_training_compensation_reason CHECK (' + expression + ')')
        _run(conn, migration.upgrade)


def test_compatible_populated_reentry_does_not_mutate_evidence(expired):
    from app.extensions import db
    c, *_ = expired
    assert apply(expired) == 'COMPENSATION_APPLIED'
    with c.app.app_context(), db.engine.begin() as conn:
        before = conn.exec_driver_sql('SELECT * FROM training_allowance_compensation').all()
        _run(conn, _module(MIGRATION).upgrade)
        _run(conn, _module(MIGRATION).upgrade)
        assert conn.exec_driver_sql('SELECT * FROM training_allowance_compensation').all() == before
    assert apply(expired) == 'COMPENSATION_ALREADY_APPLIED'


@pytest.mark.parametrize('retention', [timedelta(days=d) for d in (0, 1, 7, 89, 90, 120)])
@pytest.mark.parametrize('elapsed', [timedelta(0), timedelta(days=1), timedelta(days=7),
    timedelta(days=89), timedelta(days=90)-timedelta(microseconds=1), timedelta(days=90), timedelta(days=91)])
def test_compensation_audit_floor_boundaries(expired, retention, elapsed):
    from app.extensions import db
    from app.models import AiUsageReservation, TrainingAllowanceCompensation
    from app.services import ai_allowances as A
    c, *_ = expired
    # Late correction of an original reservation older than both retention horizons.
    applied = datetime.now(timezone.utc) + timedelta(days=200)
    c.compensation_now = applied
    assert apply(expired) == 'COMPENSATION_APPLIED'
    before = state(expired)
    now = applied + elapsed
    with c.app.app_context():
        result = A.purge_retained(now=now, retention=retention)
        # Existing inclusive cutoff retains at exact boundary; deletion is strictly after.
        retained = elapsed <= max(retention, timedelta(days=90))
        assert result == ((0, 0) if retained else (1, 0))
        assert AiUsageReservation.query.count() == int(retained)
        assert TrainingAllowanceCompensation.query.count() == int(retained)
    after = state(expired)
    assert after[0] == before[0]
    if retained:
        assert after == before
        assert apply(expired) == 'COMPENSATION_ALREADY_APPLIED'
    else:
        assert apply(expired) == 'COMPENSATION_EVIDENCE_UNAVAILABLE'


@pytest.mark.parametrize('retention', [timedelta(days=-1), timedelta(microseconds=-1), None, 1, '1 day'])
def test_invalid_retention_fails_before_sql(expired, retention):
    from app.extensions import db
    from app.services import ai_allowances as A
    c, *_ = expired
    before, statements = state(expired), []
    def record(*args): statements.append(args[2])
    with c.app.app_context():
        sa.event.listen(db.engine, 'before_cursor_execute', record)
        try:
            with pytest.raises(ValueError, match='nonnegative timedelta'):
                A.purge_retained(retention=retention)
        finally:
            sa.event.remove(db.engine, 'before_cursor_execute', record)
    assert statements == [] and state(expired) == before


@pytest.mark.parametrize('days', [0, 1, 7, 89])
def test_short_retention_preserves_uncompensated_eligibility(expired, days):
    from app.extensions import db
    from app.services import ai_allowances as A
    c, *_ = expired
    before = state(expired)
    # Durable False may be unavailable temporarily; the original window remains.
    with c.app.app_context(), db.engine.begin() as conn:
        conn.exec_driver_sql('UPDATE training_plan_generation_operation SET provider_attempt_possible=NULL')
    with c.app.app_context():
        assert A.purge_retained(now=datetime.now(timezone.utc)+timedelta(days=days, hours=1),
                                retention=timedelta(0)) == (0, 0)
    assert state(expired) == before
    # When proof returns within its window, compensation can still use it.
    with c.app.app_context(), db.engine.begin() as conn:
        conn.exec_driver_sql('UPDATE training_plan_generation_operation SET provider_attempt_possible=false')
    assert apply(expired) == 'COMPENSATION_APPLIED'


def test_repeated_short_retention_after_lost_ack(expired):
    test_commit_acknowledgment_loss_recovers_receipt(expired)
    from app.services import ai_allowances as A
    c, *_ = expired
    before = state(expired)
    with c.app.app_context():
        for days in (89, 0, 7, 1, 90, 120):
            assert A.purge_retained(now=datetime.now(timezone.utc)+timedelta(days=89),
                                    retention=timedelta(days=days)) == (0, 0)
    assert state(expired) == before
    assert apply(expired) == 'COMPENSATION_ALREADY_APPLIED'


@pytest.mark.parametrize('retention', [timedelta(0), timedelta(days=1), timedelta(days=89)])
def test_short_retention_races_correction_at_old_evidence_deadline(expired, retention):
    from app.extensions import db
    from app.services import ai_allowances as A
    c, owner, _, op = expired
    now = datetime.now(timezone.utc)+timedelta(days=200)
    barrier, errors = threading.Barrier(2), []
    def worker(action):
        with c.app.app_context():
            try:
                barrier.wait(5)
                action()
            except BaseException as exc:
                errors.append(exc)
            finally:
                db.session.remove()
    threads = [threading.Thread(target=worker, args=(action,)) for action in (
        lambda: A.purge_retained(now=now, retention=retention),
        lambda: A.compensate_training_expiry(owner, op, now=now))]
    for thread in threads: thread.start()
    for thread in threads: thread.join(10)
    assert all(not t.is_alive() for t in threads) and not errors
    assert len(state(expired)[2]) == len(state(expired)[1]) == 1
    assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]


def test_short_retention_backlog_keeps_recent_audit_and_bounds_old_cleanup(expired):
    from app.extensions import db
    from app.models import AiUsageReservation, TrainingAllowanceCompensation
    from app.services import ai_allowances as A
    c, owner, _, op = expired
    applied = datetime.now(timezone.utc)+timedelta(days=200)
    c.compensation_now = applied
    assert apply(expired) == 'COMPENSATION_APPLIED'
    with c.app.app_context(), db.engine.begin() as conn:
        # 501 old terminal holds with no operation proof must age out without credit.
        conn.exec_driver_sql("""
            INSERT INTO ai_usage_reservation
              (public_id,user_id,bucket_id,feature,key_digest,state,outcome,provider_billed,
               billing_evidence,created_at,expires_at,finalized_at)
            SELECT md5('backlog-public-'||n),r.user_id,r.bucket_id,r.feature,md5('key-'||n)||md5('key-'||n),
                   r.state,r.outcome,r.provider_billed,r.billing_evidence,r.created_at,r.expires_at,r.finalized_at
            FROM ai_usage_reservation r CROSS JOIN generate_series(1,501) AS n
        """)
    with c.app.app_context():
        assert AiUsageReservation.query.count() == 502
        assert A.purge_retained(now=applied+timedelta(days=1), retention=timedelta(0), limit=10000) == (500, 0)
        assert AiUsageReservation.query.count() == 2
        assert TrainingAllowanceCompensation.query.count() == 1
        assert A.purge_retained(now=applied+timedelta(days=7), retention=timedelta(days=1)) == (1, 0)
        assert AiUsageReservation.query.count() == TrainingAllowanceCompensation.query.count() == 1
        # More than 500 ineligible candidates cannot weaken the original proof window.
        with db.engine.begin() as conn:
            conn.exec_driver_sql("""
                INSERT INTO ai_usage_reservation
                  (public_id,user_id,bucket_id,feature,key_digest,state,outcome,provider_billed,
                   billing_evidence,created_at,expires_at,finalized_at)
                SELECT md5('recent-public-'||n),r.user_id,r.bucket_id,r.feature,md5('recent-key-'||n)||md5('recent-key-'||n),
                       r.state,r.outcome,r.provider_billed,r.billing_evidence,%s,%s,%s
                FROM ai_usage_reservation r CROSS JOIN generate_series(1,501) AS n
            """, (applied,applied+timedelta(minutes=15),applied+timedelta(minutes=16)))
        assert A.purge_retained(now=applied+timedelta(days=89), retention=timedelta(0), limit=10000) == (0, 0)
        assert AiUsageReservation.query.count() == 502
        assert TrainingAllowanceCompensation.query.count() == 1
        # Eligible backlog eventually drains in bounded invocations after the window.
        assert A.purge_retained(now=applied+timedelta(days=91), retention=timedelta(0), limit=10000) == (500, 0)
        assert A.purge_retained(now=applied+timedelta(days=91), retention=timedelta(0), limit=10000) == (2, 0)
        assert AiUsageReservation.query.count() == TrainingAllowanceCompensation.query.count() == 0
    assert apply(expired) == 'COMPENSATION_EVIDENCE_UNAVAILABLE'
    assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]


def test_owner_erasure_is_explicit_exception_to_short_retention_audit(expired):
    from app.extensions import db
    from app.models import User
    from app.cli import _purge_user
    c, owner, *_ = expired
    assert apply(expired) == 'COMPENSATION_APPLIED'
    with c.app.app_context():
        _purge_user(db.session.get(User, owner))
        db.session.commit()
    assert state(expired) == ({}, [], [])
    assert apply(expired) == 'COMPENSATION_ACCOUNT_UNAVAILABLE'


def test_multiple_receipts_share_original_bucket_and_retain_independently(expired):
    from app.extensions import db
    from app.models import TrainingPlanGenerationAllowance
    from app.services import ai_allowances as A
    from app.services.mobile_training_generation import store
    c, owner, _, first = expired
    with c.app.app_context():
        authorize = store.authorize_dispatch
        def crash(operation):
            authorize(operation)
            raise Crash()
        with c.monkeypatch.context() as patch:
            patch.setattr(store, 'authorize_dispatch', crash)
            with pytest.raises(Crash):
                with c.monkeypatch.context() as reconciliation_patch:
                    from app.services.mobile_training_generation import allowance
                    reconciliation_patch.setattr(allowance, 'settle_pending', lambda *_: None)
                    generate(owner, 'second-compensation-expired')
        _age(owner)
        store.retire_abandoned(owner)
        A.expire_abandoned()
        bindings = TrainingPlanGenerationAllowance.query.filter_by(user_id=owner).all()
        assert len(bindings) == 2
        second = next(b.operation_id for b in bindings if b.operation_id != first)
        for binding in bindings: binding.state = 'EXPIRED'
        db.session.commit()
        applied = datetime.now(timezone.utc)+timedelta(days=200)
        assert A.compensate_training_expiry(owner, first, now=applied) == 'COMPENSATION_APPLIED'
        assert A.compensate_training_expiry(owner, second, now=applied+timedelta(days=7)) == 'COMPENSATION_APPLIED'
        assert A.purge_retained(now=applied+timedelta(days=91), retention=timedelta(0)) == (1, 0)
        assert len(state(expired)[2]) == len(state(expired)[1]) == 1
        assert A.compensate_training_expiry(owner, first, now=applied+timedelta(days=91)) == 'COMPENSATION_EVIDENCE_UNAVAILABLE'
        assert A.compensate_training_expiry(owner, second, now=applied+timedelta(days=91)) == 'COMPENSATION_ALREADY_APPLIED'
        assert A.purge_retained(now=applied+timedelta(days=98), retention=timedelta(days=1)) == (1, 0)
    assert list(state(expired)[0].values()) == [(0, 0, 2, 0)]
    assert state(expired)[1:] == ([], [])


def _fair_backlog(expired, count, *, multiple_owners=False):
    """Complete physical proof, one immutable historical bucket per owner."""
    from app.extensions import db
    from app.models import (AiAllowanceBucket as K, AiStarterGrant as G, AiUsageReservation as R,
        TrainingPlanGenerationOperation as O, TrainingPlanGenerationAllowance as B)
    c, owner, other, original = expired
    with c.app.app_context():
        template, binding, hold = db.session.get(O, original), db.session.get(B, original), R.query.one()
        bucket = db.session.get(K, hold.bucket_id)
        bucket.granted, bucket.used = count+10, count
        buckets = {owner: bucket.id}
        if multiple_owners:
            grant = db.session.get(G, owner)
            db.session.add(G(user_id=other, policy_version=grant.policy_version, granted_at=grant.granted_at))
            second = K(user_id=other, scope=bucket.scope, feature=bucket.feature, period_key=bucket.period_key,
                policy_version=bucket.policy_version, granted=count+10, used=count, reserved=0,
                billed_releases=0, created_at=bucket.created_at, updated_at=bucket.updated_at)
            db.session.add(second); db.session.flush(); buckets[other] = second.id
        owners = [other if multiple_owners and i%2 else owner for i in range(count-1)]
        records = [dict(user_id=u, idempotency_key=f'fair-{i}', request_fingerprint='fair',
            status='FAILED', allowance_metered=True, provider_dispatch_possible=True,
            provider_attempt_possible=False, error_code=template.error_code,
            completed_at=template.completed_at, created_at=template.created_at,
            updated_at=template.updated_at) for i,u in enumerate(owners)]
        ids = [original]
        if records:
            ids += list(db.session.execute(sa.insert(O).returning(O.id), records).scalars())
        refs = [f'{100000+i:032x}' for i in range(count-1)]
        holds = [dict(user_id=u, bucket_id=buckets[u], public_id=ref, feature=hold.feature,
            key_digest=f'{100000+i:064x}', state='EXPIRED', outcome='expired', billing_evidence='UNKNOWN',
            created_at=hold.created_at, expires_at=hold.expires_at,
            finalized_at=hold.finalized_at+timedelta(microseconds=i+1))
            for i,(u,ref) in enumerate(zip(owners,refs))]
        if holds:
            db.session.execute(sa.insert(R), holds)
            db.session.execute(sa.insert(B), [dict(operation_id=op,user_id=u,reservation_ref=ref,
                state='EXPIRED',created_at=binding.created_at,updated_at=binding.updated_at)
                for op,u,ref in zip(ids[1:],owners,refs)])
        db.session.flush()
        db.session.execute(sa.update(R).values(provider_billed=None))
        db.session.commit()
        holds = list(db.session.execute(sa.select(R.id).order_by(R.id)).scalars())
        baseline = sum(k.used for k in K.query.all())
        return ids, holds, baseline


@pytest.mark.parametrize('count', [4, 500, 501, 1003])
@pytest.mark.parametrize('multiple_owners', [False, True])
def test_fair_purge_bounded_cycles_and_conservation(expired, count, multiple_owners):
    from app.extensions import db
    from app.models import AiAllowanceBucket as K, TrainingAllowanceCompensation as C, AiUsageReservation as R
    from app.services import ai_allowances as A
    c, *_ = expired
    ops, holds, baseline = _fair_backlog(expired, count, multiple_owners=multiple_owners)
    now = datetime.now(timezone.utc)+timedelta(days=200)
    with c.app.app_context():
        for _ in range((count+499)//500+1):
            before = C.query.count()
            assert A.purge_retained(now=now) == (0,0)
            assert 0 <= C.query.count()-before <= 500
        assert C.query.count() == R.query.count() == count
        assert sum(k.used for k in K.query.all()) == baseline-count
        # Repeated sweeps after mandatory audit floor remove only retained facts.
        for _ in range((count+499)//500+1): A.purge_retained(now=now+timedelta(days=91))
        assert C.query.count() == R.query.count() == 0
        assert sum(k.used for k in K.query.all()) == baseline-count


@pytest.mark.parametrize('mode', ['operation', 'owner', 'intermittent', 'audit', 'ineligible', 'erase', 'insert_remove', 'release_during_next'])
def test_fair_purge_locked_prefix_and_deferred_revisit(expired, mode):
    from app.extensions import db
    from app.models import (User, AiAllowanceBucket as K, AiUsageReservation as R,
        TrainingAllowanceCompensation as C, AiAllowancePurgeProgress as P)
    from app.services import ai_allowances as A
    c,owner,other,_ = expired
    ops, holds, baseline = _fair_backlog(expired, 1003, multiple_owners=mode in ('owner','erase'))
    now = datetime.now(timezone.utc)+timedelta(days=200)
    with c.app.app_context():
        if mode == 'audit':
            assert A.compensate_training_expiry(owner,ops[500],now=now) == 'COMPENSATION_APPLIED'
        if mode == 'ineligible':
            db.session.execute(sa.text('UPDATE training_plan_generation_operation SET provider_attempt_possible=NULL WHERE id=:id'),{'id':ops[500]})
            db.session.commit()
        blocker=db.engine.connect();tx=blocker.begin()
        if mode=='owner':
            blocker.execute(sa.select(User.id).where(User.id==owner).with_for_update())
        else:
            blocker.execute(sa.text('SELECT id FROM training_plan_generation_operation WHERE id=ANY(:ids) AND (:all_owners OR user_id=:owner) FOR UPDATE'), {'ids':ops[:500], 'all_owners':mode!='erase', 'owner':owner})
        try:
            assert A.purge_retained(now=now,retention=timedelta(0)) == (0,0)
            db.session.remove()  # no process-local cursor survives the next call
            if mode=='erase':
                from app.cli import _purge_user
                _purge_user(db.session.get(User,other));db.session.commit()
            if mode=='insert_remove':
                # A selected ID disappearing does not shift a keyset page.
                db.session.execute(sa.delete(R).where(R.id==holds[700]));db.session.commit()
                db.session.execute(sa.text('''INSERT INTO ai_usage_reservation
                    (public_id,user_id,bucket_id,feature,key_digest,state,outcome,provider_billed,billing_evidence,created_at,expires_at,finalized_at)
                    SELECT repeat('f',32),user_id,bucket_id,feature,repeat('f',64),state,outcome,NULL,billing_evidence,created_at,expires_at,finalized_at FROM ai_usage_reservation LIMIT 1'''))
                db.session.commit()
            if mode=='intermittent':
                tx.rollback();tx=blocker.begin()
                blocker.execute(sa.text('SELECT id FROM training_plan_generation_operation WHERE id=ANY(:ids) FOR UPDATE'),{'ids':ops[500:510]})
            if mode=='release_during_next':
                from app.services.ai_allowances import service
                original_claim=service._claim_purge_candidates
                def release_after_claim(*args):
                    candidates=original_claim(*args)
                    tx.rollback()  # older deferred rows become available after next call began
                    return candidates
                with c.monkeypatch.context() as patch:
                    patch.setattr(service,'_claim_purge_candidates',release_after_claim)
                    A.purge_retained(now=now,retention=timedelta(days=1))
            for _ in range(4): A.purge_retained(now=now,retention=timedelta(days=1))
            db.session.expire_all()
            tail = C.query.filter_by(operation_id=ops[-2] if mode=='erase' else ops[-1]).one()
            assert tail is not None
            if mode=='audit':
                assert db.session.get(R,holds[500]) is not None
                assert C.query.filter_by(operation_id=ops[500]).count()==1
            if mode=='ineligible':
                assert C.query.filter_by(operation_id=ops[500]).count()==0
                assert db.session.get(R,holds[500]) is None
            # A persistent prefix never acquired receipts except owner-mode's unlocked owner.
            if mode not in ('owner','intermittent','erase','release_during_next'):
                assert C.query.filter(C.operation_id.in_(ops[:500])).count()==0
        finally:
            db.session.rollback();tx.rollback();blocker.close()
        for _ in range(4): A.purge_retained(now=now)
        assert C.query.filter(C.operation_id.in_(ops[:500])).count()==(251 if mode=='erase' else 500)
        assert sum(k.used for k in K.query.all()) >= 0
        # Physical NULL is not an estimated refund; every decrement has one receipt.
        if mode != 'erase': assert sum(k.used for k in K.query.all()) + C.query.count() == baseline


def test_fair_purge_claim_crash_restarts_from_durable_progress(expired):
    from app.extensions import db
    from app.models import AiUsageReservation as R, TrainingAllowanceCompensation as C, AiAllowancePurgeProgress as P
    from app.services import ai_allowances as A
    from app.services.ai_allowances import service
    c,*_=expired
    ops,holds,_ = _fair_backlog(expired,1003)
    now=datetime.now(timezone.utc)+timedelta(days=200)
    with c.app.app_context():
        original=service._claim_purge_candidates
        def lost_claim(*args):
            result=original(*args)
            raise Crash()
        with c.monkeypatch.context() as patch:
            patch.setattr(service,'_claim_purge_candidates',lost_claim)
            with pytest.raises(Crash):A.purge_retained(now=now)
        db.session.remove()
        assert db.session.get(P,1).after_id==holds[499]
        assert C.query.count()==0 and R.query.count()==1003
        db.session.rollback()
        for _ in range(4): A.purge_retained(now=now)
        assert C.query.count()==R.query.count()==1003


def test_fair_purge_workers_and_cursor_lock_release(expired):
    from app.extensions import db
    from app.models import AiAllowanceBucket as K, TrainingAllowanceCompensation as C, AiAllowancePurgeProgress as P
    from app.services import ai_allowances as A
    c,*_=expired
    _,_,baseline=_fair_backlog(expired,1003)
    now=datetime.now(timezone.utc)+timedelta(days=200)
    barrier=threading.Barrier(2);errors=[]
    def worker():
        with c.app.app_context():
            try:
                barrier.wait(5)
                for _ in range(3): A.purge_retained(now=now)
            except A.AllowanceError as exc:
                if exc.code != 'AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE': errors.append(exc)
            except BaseException as exc:errors.append(exc)
            finally:db.session.remove()
    workers=[threading.Thread(target=worker) for _ in range(2)]
    for w in workers:w.start()
    for w in workers:w.join(45)
    assert all(not w.is_alive() for w in workers) and not errors
    with c.app.app_context():
        for _ in range(3): A.purge_retained(now=now)
        assert C.query.count()==1003
        assert sum(k.used for k in K.query.all())+C.query.count()==baseline
        db.session.rollback()
        with db.engine.begin() as conn:
            conn.execute(sa.select(P.id).with_for_update(nowait=True)).all()


def test_fair_purge_preserves_lock_order_and_releases_cursor_before_owner(expired):
    from app.extensions import db
    from app.models import AiAllowancePurgeProgress as P
    from app.services import ai_allowances as A
    c,*_=expired
    statements=[]
    with c.app.app_context():
        def observe(conn,cursor,statement,parameters,context,many):
            if 'FOR UPDATE' in statement:
                statements.append(statement)
                if 'FROM "user"' in statement:
                    with db.engine.begin() as independent:
                        independent.execute(sa.select(P.id).with_for_update(nowait=True)).all()
        sa.event.listen(db.engine,'before_cursor_execute',observe)
        try:A.purge_retained(now=datetime.now(timezone.utc)+timedelta(days=200))
        finally:sa.event.remove(db.engine,'before_cursor_execute',observe)
    financial=[s for s in statements if 'FROM ai_allowance_purge_progress' not in s]
    names=['FROM "user"','FROM training_plan_generation_operation','FROM training_plan_generation_allowance',
           'FROM ai_usage_reservation','FROM ai_allowance_bucket']
    positions=[next(i for i,s in enumerate(financial) if name in s) for name in names]
    assert positions==sorted(positions)
    assert 'NOWAIT' in financial[positions[0]] and 'NOWAIT' in financial[positions[1]]


def test_fair_purge_real_process_exit_after_claim(expired):
    import subprocess,sys
    from app.extensions import db
    from app.models import AiAllowancePurgeProgress as P, TrainingAllowanceCompensation as C
    from app.services import ai_allowances as A
    c,*_=expired
    _,holds,_=_fair_backlog(expired,501)
    now=datetime.now(timezone.utc)+timedelta(days=200)
    with c.app.app_context():
        url=db.engine.url.render_as_string(hide_password=False)
        db.session.remove()
        program = """import os,sys
from datetime import datetime,timedelta
from flask import Flask
from app.extensions import db
from app.services.ai_allowances.service import _claim_purge_candidates
app=Flask('crashed-purge-worker')
app.config.update(SQLALCHEMY_DATABASE_URI=sys.argv[1],SQLALCHEMY_TRACK_MODIFICATIONS=False)
db.init_app(app)
with app.app_context():
    cutoff=datetime.fromisoformat(sys.argv[2])-timedelta(days=90)
    _claim_purge_candidates(cutoff,cutoff,500)
    os._exit(37)
"""
        result=subprocess.run([sys.executable,'-c',program,url,now.isoformat()],timeout=20)
        assert result.returncode==37
        assert db.session.get(P,1).after_id==holds[499]
        assert C.query.count()==0
        db.session.rollback()
        for _ in range(3):A.purge_retained(now=now)
        assert C.query.count()==501


# Review B exact scenarios; replay now requires ALREADY_APPLIED after fair purge.
@pytest.mark.parametrize('blocked', [True, False])
def test_review_purge_progress_past_500_locked_candidates(expired, blocked):
    from app.extensions import db
    from app.models import AiAllowanceBucket, AiUsageReservation, TrainingPlanGenerationAllowance as B
    from app.models import TrainingPlanGenerationOperation as O, TrainingAllowanceCompensation as C
    from app.services import ai_allowances as A
    c, owner, _, original_op = expired
    future = datetime.now(timezone.utc) + timedelta(days=91)
    with c.app.app_context():
        template = db.session.get(O, original_op)
        binding = db.session.get(B, original_op)
        hold = AiUsageReservation.query.filter_by(user_id=owner).one()
        bucket = db.session.get(AiAllowanceBucket, hold.bucket_id)
        bucket.granted = 600
        bucket.used = 501
        ids = [original_op]
        for i in range(500):
            op = O(user_id=owner, idempotency_key=f'review-{i}', request_fingerprint='review',
                status='FAILED', allowance_metered=True, provider_dispatch_possible=True,
                provider_attempt_possible=False, error_code=template.error_code,
                completed_at=template.completed_at, created_at=template.created_at,
                updated_at=template.updated_at)
            db.session.add(op)
            db.session.flush()
            ref = f'{i+1000:032x}'
            r = AiUsageReservation(user_id=owner, bucket_id=hold.bucket_id, public_id=ref,
                feature=hold.feature, key_digest=f'{i+1000:064x}', state='EXPIRED',
                outcome='expired', billing_evidence='UNKNOWN', provider_billed=None,
                created_at=hold.created_at, expires_at=hold.expires_at,
                finalized_at=hold.finalized_at + timedelta(microseconds=i+1))
            db.session.add(r)
            db.session.add(B(operation_id=op.id, user_id=owner, reservation_ref=ref,
                state='EXPIRED', created_at=binding.created_at, updated_at=binding.updated_at))
            ids.append(op.id)
        db.session.flush()
        # ORM column's insert default is False; reproduce stored UNKNOWN NULL.
        db.session.execute(sa.text("UPDATE ai_usage_reservation SET provider_billed=NULL WHERE user_id=:owner"), {'owner': owner})
        if not blocked:
            # A mixed >500 backlog: historical NULL and missing bindings age
            # out, while complete eligible proof is compensated.
            db.session.execute(sa.text('UPDATE training_plan_generation_operation SET provider_attempt_possible=NULL WHERE id=ANY(:ids)'), {'ids': ids[:125]})
            db.session.execute(sa.text('DELETE FROM training_plan_generation_allowance WHERE operation_id=ANY(:ids)'), {'ids': ids[125:250]})
        db.session.commit()
        db.session.remove()
        blocker = db.engine.connect()
        tx = blocker.begin()
        if blocked:
            blocker.execute(sa.text('SELECT id FROM training_plan_generation_operation WHERE id = ANY(:ids) FOR UPDATE'),
                            {'ids': ids[:500]})
        try:
            results = [A.purge_retained(now=future) for _ in range(3)]
            receipt = C.query.filter_by(operation_id=ids[-1]).one_or_none()
            print('REVIEW_BATCH', results, 'later_receipt', receipt is not None,
                  'remaining', AiUsageReservation.query.count())
            db.session.rollback()
            # This candidate is provably eligible and its operation is unlocked.
            assert A.compensate_training_expiry(owner, ids[-1], now=future) == 'COMPENSATION_ALREADY_APPLIED'
            assert receipt is not None, 'three sweeps never reach eligible row 501'
        finally:
            tx.rollback()
            blocker.close()


def test_review_migration_rejects_extra_bucket_unique(migrate_engine):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn)
        m = _module(MIGRATION)
        _run(conn, m.upgrade)
        conn.exec_driver_sql('ALTER TABLE training_allowance_compensation ADD CONSTRAINT review_bad_bucket UNIQUE(bucket_id)')
        with pytest.raises(RuntimeError, match='incompatible'):
            _run(conn, m.upgrade)



def test_review_unique_operation_independent_of_reservation(expired):
    from app.extensions import db
    from sqlalchemy.exc import IntegrityError
    c, owner, _, _ = expired
    assert apply(expired) == 'COMPENSATION_APPLIED'
    with c.app.app_context(), db.engine.connect() as conn:
        new_id = conn.exec_driver_sql("""INSERT INTO ai_usage_reservation
            (public_id,user_id,bucket_id,feature,key_digest,state,outcome,provider_billed,billing_evidence,created_at,expires_at,finalized_at)
            SELECT repeat('e',32),user_id,bucket_id,feature,repeat('e',64),state,outcome,provider_billed,billing_evidence,created_at,expires_at,finalized_at
            FROM ai_usage_reservation LIMIT 1 RETURNING id""").scalar_one()
        with pytest.raises(IntegrityError):
            conn.exec_driver_sql("""INSERT INTO training_allowance_compensation
                SELECT %s,operation_id,user_id,bucket_id,proof_version,reason,applied_at
                FROM training_allowance_compensation""", (new_id,))
        conn.rollback()



@pytest.mark.parametrize('ddl', [
    'ALTER TABLE training_allowance_compensation ADD CONSTRAINT extra_composite UNIQUE(user_id,bucket_id)',
    'CREATE UNIQUE INDEX extra_bucket ON training_allowance_compensation(bucket_id)',
    'ALTER TABLE training_allowance_compensation DROP CONSTRAINT uq_training_compensation_operation; ALTER TABLE training_allowance_compensation ADD CONSTRAINT uq_training_compensation_operation UNIQUE(bucket_id)',
    'ALTER TABLE training_allowance_compensation RENAME CONSTRAINT training_allowance_compensation_pkey TO wrong_pk',
    'ALTER TABLE training_allowance_compensation RENAME CONSTRAINT training_allowance_compensation_user_id_fkey TO wrong_fk',
])
def test_reentry_rejects_r1_unique_semantics_and_authority_identity(migrate_engine, ddl):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn);migration=_module(MIGRATION);_run(conn,migration.upgrade)
        conn.exec_driver_sql(ddl)
        with pytest.raises(RuntimeError,match='incompatible'):_run(conn,migration.upgrade)


def test_nonunique_performance_index_is_compatible(migrate_engine):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn);migration=_module(MIGRATION);_run(conn,migration.upgrade)
        conn.exec_driver_sql('CREATE INDEX harmless_bucket ON training_allowance_compensation(bucket_id)')
        _run(conn,migration.upgrade)


def test_premium_bucket_cleanup_has_same_bounded_work_cap(expired):
    from app.extensions import db
    from app.models import AiAllowanceBucket as K
    from app.services import ai_allowances as A
    c,owner,*_=expired
    now=datetime.now(timezone.utc)+timedelta(days=200)
    with c.app.app_context():
        db.session.execute(sa.insert(K), [dict(user_id=owner,scope='PREMIUM',feature='training_generation',
            period_key=f'old-{i}',policy_version='synthetic',granted=1,used=0,reserved=0,billed_releases=0,
            created_at=now-timedelta(days=200),updated_at=now-timedelta(days=200)) for i in range(501)])
        db.session.commit()
        assert A.purge_retained(now=now,limit=10000)==(0,500)
        assert K.query.filter_by(scope='PREMIUM').count()==1
        assert A.purge_retained(now=now)==(0,1)



def test_populated_reentry_retention_and_reconciliation_preserve_receipt(expired):
    from app.services import ai_allowances as A
    from app.services.mobile_training_generation import allowance
    c,owner,*_=expired
    test_compatible_populated_reentry_does_not_mutate_evidence(expired)
    before=state(expired)
    with c.app.app_context():
        assert A.purge_retained(now=datetime.now(timezone.utc)+timedelta(days=7),retention=timedelta(0))==(0,0)
        allowance.settle_pending(owner)
    assert state(expired)==before
    assert apply(expired)=='COMPENSATION_ALREADY_APPLIED'


@pytest.mark.parametrize("table,column", [
    (TABLE, "reason"), ("ai_allowance_purge_progress", "after_id"),
])
@pytest.mark.parametrize("definition", ["nullable", "not_valid", "no_inherit"])
def test_expiry_verifier_rejects_noncanonical_not_null(migrate_engine, table, column, definition):
    """Real SQL drift: attnotnull alone does not prove a validated authority."""
    with migrate_engine.begin() as conn:
        if int(conn.exec_driver_sql("SHOW server_version_num").scalar_one()) < 180000:
            pytest.skip("PostgreSQL18 named NOT NULL syntax")
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        _run(conn, migration.upgrade)
        if definition == "no_inherit":
            conn.exec_driver_sql(f"ALTER TABLE {table} ALTER CONSTRAINT {table}_{column}_not_null NO INHERIT")
        else:
            conn.exec_driver_sql(f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL")
            if definition == "not_valid":
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD CONSTRAINT drift NOT NULL {column} NOT VALID")
        before = conn.exec_driver_sql(
            "SELECT to_jsonb(c) FROM pg_constraint c WHERE conrelid=%s::regclass ORDER BY oid", (table,)).all()
        with pytest.raises(RuntimeError, match="incompatible"):
            _run(conn, migration.upgrade)
        assert conn.exec_driver_sql(
            "SELECT to_jsonb(c) FROM pg_constraint c WHERE conrelid=%s::regclass ORDER BY oid", (table,)).all() == before


@pytest.mark.parametrize("table,column", [
    (TABLE, "reason"), ("ai_allowance_purge_progress", "after_id"),
])
@pytest.mark.parametrize("assignment", [
    "convalidated=false", "conenforced=false", "condeferrable=true", "condeferred=true",
    "conkey=NULL", "conkey=ARRAY[0]::smallint[]", "conkey=ARRAY[1]::smallint[]",
    "conkey=ARRAY[1,2]::smallint[]", "conkey=ARRAY[99]::smallint[]",
    "conislocal=false", "coninhcount=1",
    "conparentid=1", "contypid=23", "conindid=1", "confrelid=1",
    "connamespace=11", "contype='x'",
])
def test_expiry_verifier_fails_closed_on_not_null_catalog_drift(
        migrate_engine, table, column, assignment):
    """Rollback-only corrupt catalog evidence must never be silently filtered out."""
    with migrate_engine.begin() as conn:
        if int(conn.exec_driver_sql("SHOW server_version_num").scalar_one()) < 180000:
            pytest.skip("PostgreSQL18 NOT NULL catalog authorities")
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        _run(conn, migration.upgrade)
        with conn.begin_nested() as probe:
            conn.exec_driver_sql(f"UPDATE pg_constraint SET {assignment} "
                                "WHERE conrelid=%s::regclass AND conname=%s",
                                (table, f"{table}_{column}_not_null"))
            before = conn.exec_driver_sql(
                "SELECT to_jsonb(c) FROM pg_constraint c WHERE conrelid=%s::regclass ORDER BY oid", (table,)).all()
            with pytest.raises(RuntimeError, match="incompatible"):
                _run(conn, migration.upgrade)
            assert conn.exec_driver_sql(
                "SELECT to_jsonb(c) FROM pg_constraint c WHERE conrelid=%s::regclass ORDER BY oid", (table,)).all() == before
            probe.rollback()
        _run(conn, migration.upgrade)  # the unmodified authority remains valid


@pytest.mark.parametrize("table,column", [
    (TABLE, "reason"), ("ai_allowance_purge_progress", "after_id"),
])
def test_expiry_verifier_cross_checks_not_null_attribute(migrate_engine, table, column):
    with migrate_engine.begin() as conn:
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        _run(conn, migration.upgrade)
        with conn.begin_nested() as probe:
            conn.exec_driver_sql("UPDATE pg_attribute SET attnotnull=false "
                                 "WHERE attrelid=%s::regclass AND attname=%s", (table, column))
            with pytest.raises(RuntimeError, match="incompatible"):
                _run(conn, migration.upgrade)
            probe.rollback()
        _run(conn, migration.upgrade)


def test_expiry_verifier_requires_enforced_checks_pg18(migrate_engine):
    with migrate_engine.begin() as conn:
        if int(conn.exec_driver_sql("SHOW server_version_num").scalar_one()) < 180000:
            pytest.skip("PostgreSQL18 NOT ENFORCED checks")
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        _run(conn, migration.upgrade)
        conn.exec_driver_sql(f"ALTER TABLE {TABLE} DROP CONSTRAINT ck_training_compensation_reason")
        conn.exec_driver_sql(f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_training_compensation_reason "
                             "CHECK (reason='EXPIRED_ZERO_ATTEMPTS') NOT ENFORCED")
        with pytest.raises(RuntimeError, match="incompatible"):
            _run(conn, migration.upgrade)


@pytest.mark.parametrize("table,column", [
    (TABLE, "reason"), ("ai_allowance_purge_progress", "after_id"),
])
def test_expiry_verifier_requires_each_live_column_authority(migrate_engine, table, column):
    with migrate_engine.begin() as conn:
        if int(conn.exec_driver_sql("SHOW server_version_num").scalar_one()) < 180000:
            pytest.skip("PostgreSQL18 NOT NULL catalog authorities")
        _b1e4_schema(conn)
        migration = _module(MIGRATION)
        _run(conn, migration.upgrade)
        with conn.begin_nested() as probe:
            # Keep attnotnull=true: losing the actual validated constraint must
            # still fail. A harmless rename, conversely, changes no authority.
            conn.exec_driver_sql("DELETE FROM pg_constraint WHERE conrelid=%s::regclass AND conname=%s",
                                 (table, f"{table}_{column}_not_null"))
            with pytest.raises(RuntimeError, match="incompatible"):
                _run(conn, migration.upgrade)
            probe.rollback()
        conn.exec_driver_sql(f"ALTER TABLE {table} RENAME CONSTRAINT {table}_{column}_not_null TO equivalent_not_null")
        _run(conn, migration.upgrade)
        _run(conn, migration.upgrade)
