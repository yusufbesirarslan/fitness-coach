"""Opt-in PostgreSQL race: two workers confirm the same proposal.

SQLite serializes writers, so it cannot prove this. Mirrors
tests/test_plan_mutation_history_pg.py: barrier, no sleep, outcome invariant.
"""
import os
import threading

import pytest
import sqlalchemy as sa


pytestmark = pytest.mark.pg_concurrency

if os.environ.get("FITX_PG_CONCURRENCY_TEST") != "1":
    pytest.skip(
        "set FITX_PG_CONCURRENCY_TEST=1 with a disposable PG_TEST_DATABASE_URL",
        allow_module_level=True,
    )


PROGRAM = (
    '{"program": ['
    '{"gun": "Pazartesi", "tip": "antrenman", "odak": "Itis", '
    '"egzersizler": [{"isim": "Bench Press", "set": 3, "tekrar": "8-12"}, '
    '{"isim": "Shoulder Press", "set": 4, "tekrar": "10-12"}]}, '
    '{"gun": "Sali", "tip": "dinlenme", "egzersizler": []}, '
    '{"gun": "Carsamba", "tip": "antrenman", "egzersizler": ['
    '{"isim": "Barbell Row", "set": 4, "tekrar": "6-10"}]}, '
    '{"gun": "Persembe", "tip": "dinlenme", "egzersizler": []}, '
    '{"gun": "Cuma", "tip": "antrenman", "egzersizler": ['
    '{"isim": "Squat", "set": 5, "tekrar": "5"}]}, '
    '{"gun": "Cumartesi", "tip": "dinlenme", "egzersizler": []}, '
    '{"gun": "Pazar", "tip": "dinlenme", "egzersizler": []}'
    '], "haftalik_ozet": {"yogunluk_skoru": 7}}'
)


@pytest.fixture
def pg_confirm_app():
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
    from app.models import TrainingPlan, User

    app = Flask("plan-confirmation-pg-race")
    app.config.update(
        TESTING=True,
        SECRET_KEY="disposable-pg-plan-confirmation-test",
        SQLALCHEMY_DATABASE_URI=url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        AI_COACH_PLAN_MUTATION_TOOLS_ENABLED=True,
    )
    db.init_app(app)
    with app.app_context():
        db.drop_all()
        db.create_all()
        user = User(username="pg-confirm", email="pg-confirm@example.invalid",
                    cognito_sub="pg-confirm-sub")
        db.session.add(user)
        db.session.commit()
        db.session.add(TrainingPlan(
            user_id=user.id, score=8, plan_data=PROGRAM))
        db.session.commit()
        yield app, user.id
        db.session.remove()
        db.drop_all()


def test_two_workers_confirming_the_same_proposal_mutate_once(pg_confirm_app):
    app, user_id = pg_confirm_app
    from app.observability import assign_request_id
    from app.services import coach_plan_tools, plan_confirmation
    from app.models import PlanMutationRecord, TrainingPlan

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        coach_plan_tools.begin_turn()
        staged = coach_plan_tools.execute_plan_tool(
            user_id, "remove_training_plan_exercise",
            {"day": "Pazartesi", "exercise": "Bench Press"})
        assert staged["status"] == "confirmation_required"

    barrier = threading.Barrier(2)
    results = [None, None]
    errors = []

    def worker(index):
        try:
            with app.test_request_context("/ask", method="POST"):
                assign_request_id()
                coach_plan_tools.begin_turn("evet")
                barrier.wait(timeout=10)
                results[index] = coach_plan_tools.execute_plan_tool(
                    user_id, coach_plan_tools.CONFIRM_TOOL, {})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    statuses = {payload["status"] for payload in results if payload}
    assert "applied" in statuses or "replayed" in statuses
    with app.app_context():
        assert PlanMutationRecord.query.filter_by(user_id=user_id).count() == 1
        plan = TrainingPlan.query.filter_by(user_id=user_id).one()
        assert "Bench Press" not in plan.plan_data
        assert plan_confirmation.get_pending(user_id) is None


def _stage_remove(app, user_id):
    from app.observability import assign_request_id
    from app.services import coach_plan_tools

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        coach_plan_tools.begin_turn()
        staged = coach_plan_tools.execute_plan_tool(
            user_id, "remove_training_plan_exercise",
            {"day": "Pazartesi", "exercise": "Bench Press"})
        assert staged["status"] == "confirmation_required"


def _confirm_cancel_workers(app, user_id, before_confirm=None, before_cancel=None):
    """Run confirm and cancel against the same pending proposal.

    Linearization point: the proposal-row lock. Allowed outcomes:

    A. CONFIRM wins — mutation committed once, proposal APPLIED, cancel does
       not claim the plan was unchanged.
    B. CANCEL wins — proposal CANCELLED, plan unchanged, confirm does not
       later mutate.

    ``before_confirm`` / ``before_cancel`` run inside each worker after
    ``begin_turn`` and before the tool call, so a test can force lock order
    without sleeps.
    """
    from app.observability import assign_request_id
    from app.services import coach_plan_tools

    start = threading.Barrier(2)
    payloads = {}
    errors = []

    def confirm_worker():
        try:
            with app.test_request_context("/ask", method="POST"):
                assign_request_id()
                coach_plan_tools.begin_turn("evet")
                start.wait(timeout=10)
                if before_confirm is not None:
                    before_confirm()
                payloads["confirm"] = coach_plan_tools.execute_plan_tool(
                    user_id, coach_plan_tools.CONFIRM_TOOL, {})
        except Exception as exc:  # noqa: BLE001
            errors.append(("confirm", exc))

    def cancel_worker():
        try:
            with app.test_request_context("/ask", method="POST"):
                assign_request_id()
                coach_plan_tools.begin_turn("iptal")
                start.wait(timeout=10)
                if before_cancel is not None:
                    before_cancel()
                payloads["cancel"] = coach_plan_tools.execute_plan_tool(
                    user_id, coach_plan_tools.CANCEL_PENDING_TOOL, {})
        except Exception as exc:  # noqa: BLE001
            errors.append(("cancel", exc))

    threads = [
        threading.Thread(target=confirm_worker, daemon=True),
        threading.Thread(target=cancel_worker, daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), payloads
    assert not errors, errors
    return payloads


def _assert_linearizable_confirm_cancel(app, user_id, payloads):
    from app.models import PlanMutationRecord, TrainingPlan
    from app.models import TrainingPlanConfirmationProposal
    from app.services import plan_confirmation

    confirm = payloads["confirm"]
    cancel = payloads["cancel"]
    assert confirm is not None and cancel is not None
    assert "IntegrityError" not in str(confirm) and "IntegrityError" not in str(cancel)

    with app.app_context():
        mutations = PlanMutationRecord.query.filter_by(user_id=user_id).count()
        plan = TrainingPlan.query.filter_by(user_id=user_id).one()
        pending = plan_confirmation.get_pending(user_id)
        row = (
            TrainingPlanConfirmationProposal.query
            .filter_by(user_id=user_id)
            .order_by(TrainingPlanConfirmationProposal.id.desc())
            .one()
        )
        bench_present = "Bench Press" in plan.plan_data

        assert pending is None
        assert mutations in (0, 1)

        if row.status == plan_confirmation.STATUS_CANCELLED:
            # CANCEL linearized first: no subsequent mutation.
            assert mutations == 0
            assert bench_present
            assert cancel["status"] == "cancelled"
            assert "aynı kaldı" in cancel.get("summary", "").lower()
            assert confirm.get("status") != "applied"
            assert confirm.get("error") in (
                "NO_PENDING_CONFIRMATION", "CONFIRMATION_NOT_AUTHORIZED",
                "PENDING_CONFIRMATION_STALE") or confirm.get("status") == "error"
        elif row.status == plan_confirmation.STATUS_APPLIED:
            # CONFIRM linearized first: mutation once, cancel must not lie.
            assert mutations == 1
            assert not bench_present
            assert confirm["status"] in ("applied", "replayed")
            assert cancel["status"] in ("applied", "replayed")
            assert "aynı kaldı" not in cancel.get("summary", "").lower()
        else:
            raise AssertionError(
                f"unexpected proposal status {row.status!r} payloads={payloads}")


def test_confirm_vs_cancel_is_linearizable(pg_confirm_app):
    """Natural start-barrier race: one authoritative outcome, never both."""
    app, user_id = pg_confirm_app
    _stage_remove(app, user_id)
    payloads = _confirm_cancel_workers(app, user_id)
    _assert_linearizable_confirm_cancel(app, user_id, payloads)


def test_cancel_winning_the_proposal_lock_blocks_later_mutation(
        pg_confirm_app, monkeypatch):
    """CANCEL acquires the proposal lock first; confirm must not mutate after."""
    app, user_id = pg_confirm_app
    _stage_remove(app, user_id)

    from app.services.coach_plan_tools import executor

    cancel_holds_lock = threading.Event()
    original_confirm_lock = executor.lock_proposal
    original_cancel_lock = executor.lock_latest_proposal

    def gated_confirm_lock(locked_user_id, public_id):
        assert cancel_holds_lock.wait(timeout=10)
        return original_confirm_lock(locked_user_id, public_id)

    def gated_cancel_lock(locked_user_id):
        row = original_cancel_lock(locked_user_id)
        cancel_holds_lock.set()
        return row

    monkeypatch.setattr(executor, "lock_proposal", gated_confirm_lock)
    monkeypatch.setattr(
        executor, "lock_latest_proposal", gated_cancel_lock)

    payloads = _confirm_cancel_workers(app, user_id)
    _assert_linearizable_confirm_cancel(app, user_id, payloads)
    assert payloads["cancel"]["status"] == "cancelled"
    assert payloads["confirm"].get("status") != "applied"


def test_confirm_winning_then_cancel_does_not_claim_plan_unchanged(
        pg_confirm_app, monkeypatch):
    """CONFIRM acquires the lock first; cancel converges, never lies."""
    app, user_id = pg_confirm_app
    _stage_remove(app, user_id)

    from app.services.coach_plan_tools import executor

    confirm_holds_lock = threading.Event()
    original_confirm_lock = executor.lock_proposal
    original_cancel_lock = executor.lock_latest_proposal

    def gated_confirm_lock(locked_user_id, public_id):
        row = original_confirm_lock(locked_user_id, public_id)
        confirm_holds_lock.set()
        return row

    def gated_cancel_lock(locked_user_id):
        assert confirm_holds_lock.wait(timeout=10)
        return original_cancel_lock(locked_user_id)

    monkeypatch.setattr(executor, "lock_proposal", gated_confirm_lock)
    monkeypatch.setattr(
        executor, "lock_latest_proposal", gated_cancel_lock)

    payloads = _confirm_cancel_workers(app, user_id)
    _assert_linearizable_confirm_cancel(app, user_id, payloads)
    assert payloads["confirm"]["status"] in ("applied", "replayed")
    assert payloads["cancel"]["status"] in ("applied", "replayed")
    assert "aynı kaldı" not in payloads["cancel"].get("summary", "").lower()


def test_cancel_started_after_confirm_completed_reports_applied(pg_confirm_app):
    """A later cancel must not claim an already-mutated plan stayed unchanged."""
    app, user_id = pg_confirm_app
    _stage_remove(app, user_id)

    from app.observability import assign_request_id
    from app.services import coach_plan_tools
    from app.models import PlanMutationRecord, TrainingPlan

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        coach_plan_tools.begin_turn("evet")
        confirm = coach_plan_tools.execute_plan_tool(
            user_id, coach_plan_tools.CONFIRM_TOOL, {})

    with app.test_request_context("/ask", method="POST"):
        assign_request_id()
        coach_plan_tools.begin_turn("iptal")
        cancel = coach_plan_tools.execute_plan_tool(
            user_id, coach_plan_tools.CANCEL_PENDING_TOOL, {})

    assert confirm["status"] in ("applied", "replayed")
    assert cancel["status"] in ("applied", "replayed")
    assert "ayn\u0131 kald\u0131" not in cancel.get("summary", "").lower()
    with app.app_context():
        assert PlanMutationRecord.query.filter_by(user_id=user_id).count() == 1
