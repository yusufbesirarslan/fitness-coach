"""C1 real PostgreSQL locks and original consent; synthetic data, no SDK calls."""
import json
import os
import threading
import time
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip("requires disposable PG_TEST_DATABASE_URL", allow_module_level=True)

from app.extensions import db
from app.models import NutritionPlan, NutritionPlanEditOperation as Operation
from app.models import NutritionPlanMutationRecord as Record, TrainingPlanConfirmationProposal as Proposal
from app.services import ai_consent, nutrition_plan_store as store
from app.services.nutrition_plan_mutation import access, service, EditError
from app.services.plan_mutation import MutationContext
from tests.test_mobile_training_generation_pg import pg_generation_app  # noqa: F401
from tests.test_lp21_d1_operation_fencing_pg import disclose, consent, regrant
from tests.test_nutrition_plan_edit_service import DOCUMENT, EDIT, ESTIMATE

pytestmark = pytest.mark.pg_concurrency


@pytest.fixture
def rig(pg_generation_app, monkeypatch):
    app, owners, _ = pg_generation_app
    policies = disclose(monkeypatch)
    monkeypatch.setattr(access, "NUTRITION_EDITS_ENABLED", True)
    monkeypatch.setattr(access, "APPROVED_COACH_EDIT_DISCLOSURES", frozenset({policies["coach"].version}))
    # Use REAL D1 grants, generations, screened cohort and transaction fences.
    from d1_screened_support import screen_adult_tr
    with app.app_context():
        screen_adult_tr(*owners)
        for owner in owners:
            consent(owner, policies["coach"], "affirmative_grant", "c1-coach-grant")
            consent(owner, policies["nutrition_plan"], "affirmative_grant", "c1-nutrition-grant")
        generation = ai_consent.generation(owners[0])
        db.session.add(NutritionPlan(user_id=owners[0], plan_data=json.dumps(DOCUMENT, indent=2)))
        db.session.commit()
    authority = access.Authorities(
        ai_consent.OperationAuthority(owners[0], "coach", generation, policies["coach"].version),
        ai_consent.OperationAuthority(owners[0], "nutrition_plan", generation, policies["nutrition_plan"].version),
        # #533: synthetic Coach Nutrition Plan capability; coach/nutrition fences stay real.
        ai_consent.CapabilityAuthority(owners[0], "nutrition_plan_access", "coach.nutrition_plan_access", "synthetic-capability", generation))
    monkeypatch.setattr(ai_consent, "_fence_capabilities", lambda *a, **kw: None)
    r = SimpleNamespace(app=app, owner=owners[0], other=owners[1], authorities=authority, calls=[], policies=policies)
    def estimate(items):
        assert not db.session().in_transaction()
        r.calls.append(tuple(items))
        return dict(ESTIMATE)
    monkeypatch.setattr(service.estimation, "estimate_meal", estimate)
    return r


def prepare(r, key="c1-pg-edit-0001"):
    return service.prepare_edit(r.owner, EDIT, MutationContext(key), authorities=r.authorities)


def quote(r):
    with r.app.app_context():
        return prepare(r)


def run(r, calls):
    barrier = threading.Barrier(len(calls))
    out = []
    def worker(call):
        with r.app.app_context():
            try:
                barrier.wait(timeout=15)
                out.append(call())
            except (EditError, ai_consent.ConsentDenied) as error:
                out.append(error)
            except Exception as error:
                out.append(error)
            finally:
                db.session.remove()
    threads = [threading.Thread(target=worker, args=(call,), daemon=True) for call in calls]
    for t in threads: t.start()
    for t in threads: t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), out
    assert not [x for x in out if isinstance(x, Exception) and not isinstance(x, (EditError, ai_consent.ConsentDenied))], out
    return out


def test_same_key_preparation_claim_dispatches_once(rig):
    r = rig
    out = run(r, [lambda: prepare(r), lambda: prepare(r)])
    assert all(not isinstance(x, Exception) for x in out), out
    assert {x.status for x in out} <= {"estimating", "prepared"}
    assert len(r.calls) == 1
    with r.app.app_context():
        assert Operation.query.count() == Proposal.query.count() == 1
        assert Record.query.count() == 0 and store.newest_plan(r.owner).mutation_version == 0


def test_same_key_different_intent_conflicts(rig):
    r = rig
    from app.services.nutrition_plan_mutation import Edit
    def different():
        return service.prepare_edit(r.owner, Edit("remove", "kahvalti", "Eggs - 2 adet"),
                                    MutationContext("c1-pg-edit-0001"), authorities=r.authorities)
    out = run(r, [lambda: prepare(r), different])
    assert sum(isinstance(x, EditError) and str(x) == "idempotency_conflict" for x in out) == 1
    assert len(r.calls) == 1


def test_double_confirmation_one_journal_and_version(rig):
    r = rig; q = quote(r)
    out = run(r, [lambda: service.resolve_edit(r.owner, q.proposal_id, "confirm")] * 2)
    assert all(x.status == "applied" and x.is_current for x in out), out
    assert sorted(x.replayed for x in out) == [False, True]
    assert len({x.mutation_id for x in out}) == 1 and len(r.calls) == 1
    with r.app.app_context():
        assert Record.query.count() == 1 and store.newest_plan(r.owner).mutation_version == 1


def test_confirm_cancel_has_one_truthful_terminal_outcome(rig):
    r = rig; q = quote(r)
    out = run(r, [lambda: service.resolve_edit(r.owner, q.proposal_id, "confirm"),
                  lambda: service.resolve_edit(r.owner, q.proposal_id, "cancel")])
    with r.app.app_context():
        op, proposal = Operation.query.one(), Proposal.query.one()
        assert op.status == proposal.status
        assert op.status in {"applied", "cancelled"}
        assert Record.query.count() == (op.status == "applied")
        assert store.newest_plan(r.owner).mutation_version == (op.status == "applied")
        successes = [x for x in out if not isinstance(x, Exception)]
        assert all(x.status == op.status for x in successes), out


def test_full_replacement_cannot_be_overwritten_by_old_confirmation(rig):
    r = rig; q = quote(r)
    replacement = {**DOCUMENT, "isim": "Entirely new plan"}
    out = run(r, [lambda: service.resolve_edit(r.owner, q.proposal_id, "confirm"),
                  lambda: store.replace_nutrition_plan(r.owner, replacement, None, authority=r.authorities.nutrition)])
    with r.app.app_context():
        plan = store.newest_plan(r.owner)
        assert json.loads(plan.plan_data) == replacement and plan.mutation_version == 0
        assert plan.lineage_id != Operation.query.one().plan_lineage_id
        assert Record.query.count() <= 1
        with pytest.raises(EditError, match="plan_stale"):
            service.resolve_edit(r.owner, q.proposal_id, "confirm")


def test_simultaneous_undo_restores_once(rig):
    r = rig; q = quote(r)
    with r.app.app_context(): service.resolve_edit(r.owner, q.proposal_id, "confirm")
    def undo():
        return service.undo_last_change(r.owner, MutationContext("c1-pg-undo-0001"), authorities=r.authorities)
    out = run(r, [undo, undo])
    assert sorted(x.replayed for x in out) == [False, True]
    with r.app.app_context():
        plan = store.newest_plan(r.owner)
        assert plan.plan_data == json.dumps(DOCUMENT, indent=2) and plan.mutation_version == 2
        assert Record.query.count() == 2
        assert Record.query.filter(Record.reverts_mutation_id.isnot(None)).count() == 1
    assert len(r.calls) == 1


@pytest.mark.parametrize("feature", ["coach", "nutrition_plan"])
def test_revoke_regrant_does_not_revive_original_confirmation(rig, feature):
    r = rig; q = quote(r)
    with r.app.app_context():
        regrant(r.owner, r.policies[feature], "c1-" + feature)
        with pytest.raises(ai_consent.ConsentDenied):
            service.resolve_edit(r.owner, q.proposal_id, "confirm")
        assert Operation.query.one().status == Proposal.query.one().status == "stale"
        assert Record.query.count() == 0 and store.newest_plan(r.owner).mutation_version == 0
        db.session.remove()
        generation = ai_consent.generation(r.owner)
        r.authorities = access.Authorities(
            ai_consent.OperationAuthority(r.owner, "coach", generation, r.policies["coach"].version),
            ai_consent.OperationAuthority(r.owner, "nutrition_plan", generation, r.policies["nutrition_plan"].version),
            ai_consent.CapabilityAuthority(r.owner, "nutrition_plan_access", "coach.nutrition_plan_access", "synthetic-capability", generation))
        assert prepare(r, "c1-pg-edit-0002").status == "prepared"


def test_revoke_while_estimating_withholds_quote_and_plan(rig, monkeypatch):
    r = rig
    def estimate(items):
        assert not db.session().in_transaction()
        regrant(r.owner, r.policies["nutrition_plan"], "c1-inflight")
        return dict(ESTIMATE)
    monkeypatch.setattr(service.estimation, "estimate_meal", estimate)
    with r.app.app_context():
        with pytest.raises(ai_consent.ConsentDenied): prepare(r)
        assert Operation.query.one().status == "failed"
        assert Proposal.query.count() == Record.query.count() == 0
        assert store.newest_plan(r.owner).mutation_version == 0


def legacy_insert(r):
    """Simulate the actual old writer's original column set after migration."""
    with r.app.app_context():
        NutritionPlan.query.filter_by(user_id=r.owner).delete()
        db.session.execute(sa.text(
            "INSERT INTO nutrition_plan (user_id, plan_data, created_at) "
            "VALUES (:owner, :raw, CURRENT_TIMESTAMP)"),
            {"owner": r.owner, "raw": json.dumps(DOCUMENT, indent=2)})
        db.session.commit()
        row = store.newest_plan(r.owner)
        assert row.lineage_id is None and row.mutation_version == 0


def test_old_column_insert_double_confirm_and_distinct_key_undo(rig):
    r = rig
    legacy_insert(r)
    q = quote(r)
    with r.app.app_context():
        assert store.newest_plan(r.owner).lineage_id is None
        assert store.newest_plan(r.owner).plan_data == json.dumps(DOCUMENT, indent=2)
    out = run(r, [lambda: service.resolve_edit(r.owner, q.proposal_id, "confirm")] * 2)
    assert all(not isinstance(x, Exception) and x.is_current for x in out), out
    assert sorted(x.replayed for x in out) == [False, True]
    def undo(key):
        return lambda: service.undo_last_change(r.owner, MutationContext(key), authorities=r.authorities)
    out = run(r, [undo("c1-old-undo-key1"), undo("c1-old-undo-key2")])
    assert sum(not isinstance(x, Exception) for x in out) == 1, out
    assert sum(isinstance(x, EditError) and str(x) == "undo_unavailable" for x in out) == 1, out
    with r.app.app_context():
        row = store.newest_plan(r.owner)
        assert row.lineage_id == Operation.query.one().plan_lineage_id
        assert row.plan_data == json.dumps(DOCUMENT, indent=2) and row.mutation_version == 2
        assert Record.query.count() == 2
    assert len(r.calls) == 1


@pytest.mark.parametrize("change", ["targeted", "legacy_replacement"])
def test_confirmation_observably_waits_for_owner_then_rejects_stale_state(rig, change):
    from app.services.plan_owner_lock import lock_plan_owner
    r = rig
    legacy_insert(r)
    q = quote(r)
    ready = threading.Event()
    worker_pid, out = [], []
    def confirm():
        with r.app.app_context():
            try:
                worker_pid.append(db.session.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())
                ready.set()
                out.append(service.resolve_edit(r.owner, q.proposal_id, "confirm"))
            except Exception as error:
                out.append(error)
            finally:
                db.session.remove()
    observer = sa.create_engine(os.environ["PG_TEST_DATABASE_URL"], poolclass=sa.pool.NullPool)
    worker = threading.Thread(target=confirm, daemon=True)
    try:
        with r.app.app_context():
            lock_plan_owner(r.owner)
            holder_pid = db.session.execute(sa.text("SELECT pg_backend_pid()")).scalar_one()
            plan = store.newest_plan_query(r.owner).with_for_update().one()
            worker.start()
            assert ready.wait(10)
            deadline, observed = time.monotonic() + 15, False
            while time.monotonic() < deadline:
                with observer.connect() as conn:
                    observed = conn.execute(sa.text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE pid=:pid "
                        "AND wait_event_type='Lock' AND :holder=ANY(pg_blocking_pids(pid)))"),
                        {"pid": worker_pid[0], "holder": holder_pid}).scalar_one()
                if observed: break
                time.sleep(0.02)
            assert observed, "the confirming backend must actually wait on the holder"
            if change == "targeted":
                store.write_targeted_locked(plan, lineage_id=Operation.query.one().plan_lineage_id,
                    before_lineage_id=None, version=0, before=plan.plan_data, after=plan.plan_data + " ")
            else:
                old_id = plan.id
                db.session.delete(plan)
                db.session.flush()
                db.session.execute(sa.text(
                    "INSERT INTO nutrition_plan (user_id, plan_data, created_at) "
                    "VALUES (:owner, :raw, CURRENT_TIMESTAMP)"),
                    {"owner": r.owner, "raw": json.dumps(DOCUMENT, indent=2)})
                assert store.newest_plan(r.owner).id != old_id
            db.session.commit()
        worker.join(timeout=20)
        assert not worker.is_alive(), out
        assert len(out) == 1 and isinstance(out[0], EditError) and str(out[0]) == "plan_stale", out
        with r.app.app_context():
            assert Operation.query.one().status == Proposal.query.one().status == "stale"
            assert Record.query.count() == 0
            row = store.newest_plan(r.owner)
            assert row.mutation_version == (1 if change == "targeted" else 0)
            assert row.lineage_id is None if change == "legacy_replacement" else row.lineage_id is not None
    finally:
        if worker.ident is not None: worker.join(timeout=20)
        observer.dispose()
