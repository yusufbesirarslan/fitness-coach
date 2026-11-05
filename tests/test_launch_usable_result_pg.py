"""Real PostgreSQL reconciliation races and atomic Coach completion receipt."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import threading

import pytest

from app.extensions import db
from app.models import AiAllowanceBucket, AiUsageReservation, WorkoutCompletionDay
from app.services.ai_allowances import service as ledger, execution as meter
from app.services.ai_spend_guard import subject_scope
from physical_budget_support import before_attempt
from test_premium_allowance_execution_pg import isolated, pump_provider, run_pump
from test_ai_allowances_pg import race

pytestmark = pytest.mark.pg_concurrency


def enable_refunds(app):
    app.config["AI_ALLOWANCE_POLICY"] = replace(app.config["AI_ALLOWANCE_POLICY"],
        refund_unusable_results=True)


def test_concurrent_expired_outcome_reconciliation_refunds_only_original_bucket_once(isolated):
    with isolated.app_context():
        enable_refunds(isolated)
        hold = ledger.reserve_dispatch_within(1, "coach", "reconcile-original-key", "a" * 64, 8)
        db.session.commit()
        ledger.record_dispatch_attempt(1, hold.reference)
        later = datetime.now(timezone.utc) + timedelta(hours=1)
        assert ledger.expire_abandoned(now=later) == 1
        assert AiAllowanceBucket.query.one().used == 1

    def reconcile():
        return ledger.finalize(1, hold.reference, "provider_error", billing_evidence="UNKNOWN",
                               result_known=True, now=later)

    results = race(isolated, reconcile, [()] * 8)
    assert all(r.state == "RELEASED" for r in results), results
    assert sum(not r.replayed for r in results) == 1
    with isolated.app_context():
        assert (AiAllowanceBucket.query.one().used, AiAllowanceBucket.query.one().reserved) == (0, 0)
        assert AiUsageReservation.query.one().physical_attempts == 1
        with pytest.raises(ledger.AllowanceError):
            ledger.record_dispatch_attempt(1, hold.reference)
        with pytest.raises(ledger.AllowanceError):
            ledger.reserve_dispatch_within(1, "coach", "reconcile-original-key", "a" * 64, 8)


def test_coach_photo_completion_lost_commit_ack_keeps_atomic_charge(isolated, monkeypatch):
    from app.services.workout_completion import CompleteWorkoutCommand, complete_workout
    from app.timeutil import app_today
    with isolated.app_context(), subject_scope(1):
        enable_refunds(isolated)
        action = meter.prepare(1, "coach", {"photo": "synthetic"}, key="coach-photo-ack-key")
        action.start()
        with meter.scope(action):
            before_attempt(1, "vision")
            original_commit = db.session.commit
            calls = []

            def lose_ack():
                original_commit()
                calls.append(True)
                raise RuntimeError("synthetic lost commit acknowledgement")

            with monkeypatch.context() as patch:
                patch.setattr(db.session, "commit", lose_ack)
                with pytest.raises(RuntimeError):
                    complete_workout(CompleteWorkoutCommand(user_id=1, today=app_today(),
                        image_key="synthetic/1/photo.jpg", location_type="ai_tool",
                        description="synthetic gym", workout_score=5, visibility="private",
                        shared_friend_ids=(), valid=True, fallback=False, base_xp=10,
                        photo_bonus=25, activity_text="synthetic", entry_path="ai_tool"))
            assert len(calls) == 1
            assert WorkoutCompletionDay.query.count() == 1
            assert AiUsageReservation.query.one().canonical_committed is True
            action.result_known = True  # Coach handled the tool's transport error.
            action.finish()
        record = AiUsageReservation.query.one()
        assert (record.state, record.outcome, record.physical_attempts) == ("COMMITTED", "success", 1)
        assert AiAllowanceBucket.query.one().used == 1


@pytest.mark.parametrize("invalid", [False, True])
def test_native_pump_terminal_failure_refunds_without_losing_attempt_evidence(
        isolated, pump_provider, monkeypatch, invalid):
    from app.services.mobile_pump_checks import service
    original_analyze = service.analyze_image

    def unusable(*args, **kwargs):
        original_analyze(*args, **kwargs)
        if invalid:
            raise service.InvalidAnalysis("synthetic invalid result")
        raise TimeoutError("synthetic provider timeout")

    monkeypatch.setattr(service, "analyze_image", unusable)
    with isolated.app_context(), subject_scope(1):
        enable_refunds(isolated)
        error = service.AnalysisInvalid if invalid else service.ProviderUnavailable
        with pytest.raises(error):
            run_pump("native-pump-unusable-key")
        row = AiUsageReservation.query.one()
        assert (row.state, row.physical_attempts, row.billing_evidence) == ("RELEASED", 1, "UNKNOWN")
        assert (AiAllowanceBucket.query.one().used, AiAllowanceBucket.query.one().reserved) == (0, 0)
        with pytest.raises(meter.ExecutionRefused):
            run_pump("native-pump-unusable-key")
        assert len(pump_provider) == 1
        assert AiUsageReservation.query.count() == 1


def test_coach_and_ordinary_completion_keep_day_then_owner_lock_order(isolated, monkeypatch):
    from app.services.workout_completion import service
    from app.timeutil import app_today
    day_owned, coach_waiting_for_day = threading.Event(), threading.Event()
    real_lock, real_award = service.lock_completion_day, service.award_xp
    with isolated.app_context():
        enable_refunds(isolated)
        action = meter.prepare(1, "coach", {"photo": "synthetic"}, key="coach-completion-race-key")
        action.start()

    def lock_day(*args):
        if meter.current_action() is not None:
            coach_waiting_for_day.set()
        real_lock(*args)
        if meter.current_action() is None:
            day_owned.set()

    def award(*args):
        if meter.current_action() is None:
            assert coach_waiting_for_day.wait(5)
        return real_award(*args)

    monkeypatch.setattr(service, "lock_completion_day", lock_day)
    monkeypatch.setattr(service, "award_xp", award)

    def complete(coach):
        command = service.CompleteWorkoutCommand(user_id=1, today=app_today(),
            image_key="synthetic/1/photo.jpg", location_type="ai_tool",
            description="synthetic", workout_score=5, visibility="private",
            shared_friend_ids=(), valid=True, fallback=False, base_xp=10,
            photo_bonus=25, activity_text="synthetic", entry_path="ai_tool")
        if coach:
            assert day_owned.wait(5)
            with meter.scope(action):
                result = service.complete_workout(command)
                action.finish()
        else:
            result = service.complete_workout(command)
        return result.outcome.value

    results = race(isolated, complete, [(False,), (True,)])
    assert sorted(results) == ["already_completed", "created"], results
    with isolated.app_context():
        assert WorkoutCompletionDay.query.count() == 1
        assert AiUsageReservation.query.one().state == "RELEASED"
