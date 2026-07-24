"""Sprint 7 PR2 — canonical workout completion mutation.

Deterministic, hermetic (in-memory SQLite) coverage of the confirmed-completion
contract: atomicity, idempotency/replay, selective IntegrityError handling,
required-side-effect rollback, preflight-skips-provider, timezone identity,
ownership, and evidence-writer separation. The opt-in real-concurrency proof
lives in ``test_workout_completion_pg.py`` (Postgres, marker+env gated).
"""
import json
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Activity,
    DailyQuest,
    Message,
    PumpCheck,
    User,
    WORKOUT_COMPLETION_MARKER,
    WorkoutLog,
)
from app.services import ai_coach
from app.services.workout_completion import (
    CompleteWorkoutCommand,
    CompletionOutcome,
    already_completed_today,
    complete_workout,
)
from app.services.workout_completion.queries import is_pump_check_day_violation
from app.services.workout_state import resolve_workout_state
from app.timeutil import app_today

from app.blueprints import training as training_bp


def _cmd(user_id, **over):
    base = dict(
        user_id=user_id,
        today=app_today(),
        image_key=None,
        location_type="salon",
        description="desc",
        workout_score=7.0,
        visibility="feed",
        shared_friend_ids=(),
        valid=True,
        fallback=False,
        base_xp=10,
        photo_bonus=25,
        activity_text="done",
        entry_path="test",
    )
    base.update(over)
    return CompleteWorkoutCommand(**base)


def _markers(user_id):
    return WorkoutLog.query.filter_by(
        user_id=user_id, exercise_name=WORKOUT_COMPLETION_MARKER
    ).count()


# ---------------------------------------------------------------------------
# Happy path + atomic write set
# ---------------------------------------------------------------------------

def test_single_completion_writes_exactly_one_of_each(app, make_user):
    user = make_user("u1")
    result = complete_workout(_cmd(user.id))

    assert result.outcome is CompletionOutcome.CREATED
    assert result.created is True
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1
    assert _markers(user.id) == 1
    assert Activity.query.filter_by(user_id=user.id, activity_type="workout_completed").count() == 1
    # No quest seeded → 35 (10 base + 25 photo), awarded once.
    assert result.new_total == 35
    assert result.xp_awarded == 35
    assert db.session.get(User, user.id).rank_points == 35
    assert result.pump_check_id is not None


def test_completion_awards_quest_once(app, make_user):
    user = make_user("uq")
    db.session.add(DailyQuest(title="Log a Workout", points_reward=50,
                              quest_type="workout_logged"))
    db.session.commit()
    result = complete_workout(_cmd(user.id))
    assert result.xp_awarded == 10 + 25 + 50
    assert result.new_total == 10 + 25 + 50


# ---------------------------------------------------------------------------
# Idempotency / replay
# ---------------------------------------------------------------------------

def test_sequential_replay_is_idempotent(app, make_user):
    user = make_user("u2")
    first = complete_workout(_cmd(user.id))
    assert first.created
    second = complete_workout(_cmd(user.id))
    assert second.outcome is CompletionOutcome.ALREADY_COMPLETED
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1
    assert _markers(user.id) == 1
    assert db.session.get(User, user.id).rank_points == 35  # XP not doubled


def test_preflight_short_circuits_existing_completion(app, make_user):
    user = make_user("u3")
    db.session.add(PumpCheck(user_id=user.id, valid=True, date_key=app_today().isoformat()))
    db.session.commit()
    assert already_completed_today(user.id, app_today()) is True
    result = complete_workout(_cmd(user.id))
    assert result.outcome is CompletionOutcome.ALREADY_COMPLETED
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1  # no second row
    assert _markers(user.id) == 0  # nothing written on the replay


def test_replay_caught_by_constraint_even_when_preflight_misses(app, make_user):
    """The unique constraint — not the preflight — is the real atomic claim.

    With an injected past ``today`` the created_at-window preflight cannot see the
    just-written row, yet the second attempt still resolves to ALREADY_COMPLETED
    because ``uq_pump_check_day`` rejects the duplicate ``date_key``.
    """
    user = make_user("u4")
    past = app_today() - timedelta(days=400)
    assert complete_workout(_cmd(user.id, today=past)).created
    second = complete_workout(_cmd(user.id, today=past))
    assert second.outcome is CompletionOutcome.ALREADY_COMPLETED
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1


# ---------------------------------------------------------------------------
# Correction #1 — only uq_pump_check_day maps to ALREADY_COMPLETED
# ---------------------------------------------------------------------------

class _Diag:
    def __init__(self, name):
        self.constraint_name = name


class _PgOrig:
    def __init__(self, name):
        self.diag = _Diag(name)


def _pg_err(name):
    return IntegrityError("INSERT ...", {}, _PgOrig(name))


def _sqlite_err(msg):
    return IntegrityError("INSERT ...", {}, Exception(msg))


def test_is_pump_check_day_violation_identity():
    assert is_pump_check_day_violation(_pg_err("uq_pump_check_day")) is True
    assert is_pump_check_day_violation(_pg_err("uq_user_email")) is False
    assert is_pump_check_day_violation(
        _sqlite_err("UNIQUE constraint failed: pump_check.user_id, pump_check.date_key")
    ) is True
    assert is_pump_check_day_violation(
        _sqlite_err("UNIQUE constraint failed: user.email")
    ) is False
    assert is_pump_check_day_violation(_sqlite_err("FOREIGN KEY constraint failed")) is False


def test_unrelated_integrity_error_is_reraised_not_already_completed(app, make_user, monkeypatch):
    """A non-pump-check IntegrityError must surface as an internal error, never as
    a silent 'already completed'."""
    user = make_user("u5")

    def _raise_other(*a, **k):
        raise _pg_err("some_other_unique_constraint")

    monkeypatch.setattr("app.services.workout_completion.service.award_xp", _raise_other)
    with pytest.raises(IntegrityError):
        complete_workout(_cmd(user.id))
    # Rolled back — no partial completion left behind.
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 0
    assert _markers(user.id) == 0


# ---------------------------------------------------------------------------
# Correction #3 — required-side-effect failure rolls back the whole completion
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("helper", ["_claim_quest", "award_xp", "log_activity"])
def test_required_helper_failure_rolls_back_everything(app, make_user, monkeypatch, helper):
    user = make_user("u6")

    def _boom(*a, **k):
        raise RuntimeError("injected failure in " + helper)

    monkeypatch.setattr("app.services.workout_completion.service." + helper, _boom)
    with pytest.raises(RuntimeError):
        complete_workout(_cmd(user.id))
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 0
    assert _markers(user.id) == 0
    assert Activity.query.filter_by(user_id=user.id).count() == 0
    assert (db.session.get(User, user.id).rank_points or 0) == 0


def test_session_not_poisoned_after_rollback(app, make_user, monkeypatch):
    """A rolled-back mutation must leave the SQLAlchemy session usable, and a
    subsequent legitimate completion must still succeed."""
    user = make_user("u7")
    calls = {"n": 0}
    real_award = __import__(
        "app.services.workout_completion.service", fromlist=["award_xp"]
    ).award_xp

    def _flaky(uid, amount, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first attempt fails")
        return real_award(uid, amount, *a, **k)

    monkeypatch.setattr("app.services.workout_completion.service.award_xp", _flaky)
    with pytest.raises(RuntimeError):
        complete_workout(_cmd(user.id))
    # Session usable after rollback.
    assert User.query.count() >= 1
    # Second, non-failing attempt completes cleanly.
    result = complete_workout(_cmd(user.id))
    assert result.created
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1


# ---------------------------------------------------------------------------
# Side-effect fan-out (friends) + AI path (private)
# ---------------------------------------------------------------------------

def test_friends_visibility_creates_share_messages(app, make_user):
    user = make_user("owner")
    f1 = make_user("f1")
    f2 = make_user("f2")
    result = complete_workout(_cmd(
        user.id, visibility="friends", shared_friend_ids=(f1.id, f2.id)))
    assert result.created
    msgs = Message.query.filter_by(sender_id=user.id, message_type="pump_check").all()
    assert {m.receiver_id for m in msgs} == {f1.id, f2.id}


def test_private_visibility_creates_no_messages(app, make_user):
    user = make_user("solo")
    complete_workout(_cmd(user.id, visibility="private"))
    assert Message.query.filter_by(message_type="pump_check").count() == 0


# ---------------------------------------------------------------------------
# Timezone / identity
# ---------------------------------------------------------------------------

def test_completion_writes_istanbul_date_key(app, make_user):
    user = make_user("tz")
    today = app_today()
    complete_workout(_cmd(user.id, today=today))
    check = PumpCheck.query.filter_by(user_id=user.id).one()
    assert check.date_key == today.isoformat()


def test_client_supplied_ids_cannot_override_trusted_identity(app, make_user):
    """The command carries only server-trusted user_id/today; there is no field a
    client could set to complete another user's day."""
    victim = make_user("victim")
    attacker = make_user("attacker")
    complete_workout(_cmd(attacker.id))
    # Attacker's completion never touched the victim.
    assert PumpCheck.query.filter_by(user_id=victim.id).count() == 0
    assert resolve_workout_state(victim.id).completed_today is False


# ---------------------------------------------------------------------------
# PR1 read-model compatibility
# ---------------------------------------------------------------------------

def test_pr1_resolver_reports_completed_after_canonical_completion(app, make_user):
    user = make_user("pr1a")
    complete_workout(_cmd(user.id))
    snap = resolve_workout_state(user.id)
    assert snap.completed_today is True
    assert snap.execution_state == "completed"


def test_pr1_resolver_unchanged_after_failed_completion(app, make_user, monkeypatch):
    user = make_user("pr1b")
    monkeypatch.setattr(
        "app.services.workout_completion.service.award_xp",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    with pytest.raises(RuntimeError):
        complete_workout(_cmd(user.id))
    snap = resolve_workout_state(user.id)
    assert snap.completed_today is False
    assert snap.execution_state != "completed"


# ---------------------------------------------------------------------------
# Correction #2 — obvious replays skip expensive provider work
# ---------------------------------------------------------------------------

def test_route_replay_skips_pump_check_validation(app, client, auth_user, monkeypatch):
    client.post("/training-plan/save", json={"plan": {"v": 1}, "score": 7.0})
    monkeypatch.setattr(training_bp, "validate_pump_check_image",
                        lambda *a, **k: (b"jpeg", "image/jpeg", None))
    calls = {"n": 0}

    def _validate(*a, **k):
        calls["n"] += 1
        return {"valid": True, "fallback": False}

    monkeypatch.setattr(training_bp, "validate_pump_check", _validate)

    first = client.post("/workout/complete", json={"image": "x", "location_type": "salon"})
    assert first.status_code == 200
    assert calls["n"] == 1
    # Replay: preflight returns already_completed BEFORE the Bedrock validation.
    second = client.post("/workout/complete", json={"image": "x", "location_type": "salon"})
    assert second.status_code == 400
    assert second.get_json()["code"] == "already_completed"
    assert calls["n"] == 1  # provider validation NOT invoked again


def test_tool_replay_skips_bedrock(app, make_user, monkeypatch):
    import s3_helper
    user = make_user("photo_replay")
    key = f"pump-checks/{user.id}/2026/06/x.jpg"
    monkeypatch.setattr(ai_coach, "BEDROCK_ENABLED", True)
    monkeypatch.setattr(ai_coach, "_anthropic", object())
    monkeypatch.setattr(s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(s3_helper, "get_object_bytes", lambda k, expected_user_id=None: b"jpeg")
    calls = {"n": 0}

    def _bedrock(*a, **k):
        calls["n"] += 1
        return '{"is_gym": true, "form_rating": 7, "reason": "salon"}'

    monkeypatch.setattr(ai_coach, "_bedrock_validate_image", _bedrock)

    first = json.loads(ai_coach._tool_analyze_gym_photo(user.id, key))
    assert first["status"] == "committed"
    assert calls["n"] == 1
    second = json.loads(ai_coach._tool_analyze_gym_photo(user.id, key))
    assert second["status"] == "already_done" and second["awarded"] is False
    assert calls["n"] == 1  # Bedrock NOT called on the replay


# ---------------------------------------------------------------------------
# Evidence-only writers must never create confirmed-completion artifacts
# ---------------------------------------------------------------------------

def test_ai_coach_exercise_logging_stays_evidence_only(app, auth_user):
    json.loads(ai_coach._tool_stage_workout_log(auth_user.id, "Squat", 5, 5, 100))
    ai_coach._begin_coach_turn()
    result = json.loads(ai_coach._tool_confirm_and_commit_workout_log(auth_user.id))
    assert result["status"] == "committed"
    # A non-marker WorkoutLog exists, but no completion artifacts.
    assert WorkoutLog.query.filter_by(user_id=auth_user.id).count() == 1
    assert _markers(auth_user.id) == 0
    assert PumpCheck.query.filter_by(user_id=auth_user.id).count() == 0
    # PR1 classifies it as recorded evidence, never completed.
    snap = resolve_workout_state(auth_user.id)
    assert snap.completed_today is False
    assert snap.execution_state == "execution_recorded"
