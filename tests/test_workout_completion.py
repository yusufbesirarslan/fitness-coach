"""Sprint 7 PR2 — canonical workout completion mutation.

Deterministic, hermetic (in-memory SQLite) coverage of the confirmed-completion
contract: atomicity, idempotency/replay, selective IntegrityError handling,
required-side-effect rollback, preflight-skips-provider, timezone identity,
ownership, and evidence-writer separation. The opt-in real-concurrency proof
lives in ``test_workout_completion_pg.py`` (Postgres, marker+env gated).
"""
import json
import secrets
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
    WORKOUT_SESSION_ABANDONED,
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
    WorkoutLog,
    WorkoutSession,
)
from app.services import ai_coach
from app.services.workout_completion import (
    CompleteWorkoutCommand,
    CompletionOutcome,
    SessionCompletionConflict,
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


# ---------------------------------------------------------------------------
# Correction #3 — completion↔session reconciliation (Sprint 7 PR3)
#
# A linked ACTIVE session is terminalized ACTIVE→COMPLETED inside the SAME
# transaction as the PumpCheck/marker/XP on EVERY path that ends the day
# completed (fresh create, preflight replay, uq_pump_check_day race-loser),
# with no duplicated artifacts and never left permanently ACTIVE — and never
# marked COMPLETED without PumpCheck authority.
# ---------------------------------------------------------------------------

def _mk_active_session(user_id, *, status=WORKOUT_SESSION_ACTIVE, version=1):
    s = WorkoutSession(
        public_id=secrets.token_urlsafe(16),
        user_id=user_id,
        status=status,
        workout_date=app_today().isoformat(),
        weekday_slot="Perşembe",
        source="scheduled",
        started_at=None,
        last_activity_at=None,
        version=version,
    )
    db.session.add(s)
    db.session.commit()
    return s


def test_fresh_completion_with_session_terminalizes_atomically(app, make_user):
    """session_id present + fresh completion (CREATED): one atomic transaction
    writes the PumpCheck/marker AND terminalizes the ACTIVE session."""
    user = make_user("sess_fresh")
    session = _mk_active_session(user.id)
    result = complete_workout(_cmd(user.id, session_id=session.id))

    assert result.outcome is CompletionOutcome.CREATED
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1
    assert _markers(user.id) == 1
    row = db.session.get(WorkoutSession, session.id)
    assert row.status == WORKOUT_SESSION_COMPLETED
    assert row.completed_at is not None
    assert row.version == 2  # bumped on the terminal transition


def test_preexisting_pumpcheck_still_terminalizes_session_no_duplicates(app, make_user):
    """ACTIVE session + a pre-existing matching PumpCheck (preflight sees it):
    the day is already completed, but the owned ACTIVE session is STILL
    terminalized — with NO duplicate PumpCheck/marker/XP."""
    user = make_user("sess_recon")
    db.session.add(PumpCheck(user_id=user.id, valid=True,
                             date_key=app_today().isoformat()))
    db.session.commit()
    before_xp = db.session.get(User, user.id).rank_points or 0
    session = _mk_active_session(user.id)

    result = complete_workout(_cmd(user.id, session_id=session.id))

    assert result.outcome is CompletionOutcome.ALREADY_COMPLETED
    # No duplicated artifacts — exactly the one pre-existing PumpCheck, no marker,
    # no XP re-award.
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1
    assert _markers(user.id) == 0
    assert (db.session.get(User, user.id).rank_points or 0) == before_xp
    # ...but the session is terminalized, never left permanently ACTIVE.
    row = db.session.get(WorkoutSession, session.id)
    assert row.status == WORKOUT_SESSION_COMPLETED
    assert row.version == 2


def test_race_loser_still_terminalizes_session(app, make_user, monkeypatch):
    """uq_pump_check_day race-loser: the preflight misses (monkeypatched), the
    INSERT hits the unique constraint, the transaction rolls back — and the
    owned ACTIVE session is STILL terminalized in the fresh reconciliation
    transaction (never left ACTIVE), with no duplicate PumpCheck."""
    user = make_user("sess_race")
    # The winner already wrote the day's PumpCheck.
    db.session.add(PumpCheck(user_id=user.id, valid=True,
                             date_key=app_today().isoformat()))
    db.session.commit()
    session = _mk_active_session(user.id)

    # Force the preflight to miss so the code proceeds to the INSERT, which then
    # loses the uq_pump_check_day race exactly like a real concurrent request.
    monkeypatch.setattr(
        "app.services.workout_completion.service.already_completed_today",
        lambda *a, **k: False,
    )
    result = complete_workout(_cmd(user.id, session_id=session.id))

    assert result.outcome is CompletionOutcome.ALREADY_COMPLETED
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1  # no duplicate
    assert _markers(user.id) == 0
    row = db.session.get(WorkoutSession, session.id)
    assert row.status == WORKOUT_SESSION_COMPLETED
    assert row.version == 2


def test_duplicate_session_completion_is_a_noop(app, make_user):
    """Completing the same session twice is deterministic: the second call is an
    ALREADY_COMPLETED no-op that neither duplicates artifacts nor re-touches the
    already-terminal session."""
    user = make_user("sess_dup")
    session = _mk_active_session(user.id)
    first = complete_workout(_cmd(user.id, session_id=session.id))
    assert first.outcome is CompletionOutcome.CREATED
    version_after_first = db.session.get(WorkoutSession, session.id).version

    second = complete_workout(_cmd(user.id, session_id=session.id))
    assert second.outcome is CompletionOutcome.ALREADY_COMPLETED
    assert PumpCheck.query.filter_by(user_id=user.id).count() == 1
    assert _markers(user.id) == 1
    row = db.session.get(WorkoutSession, session.id)
    assert row.status == WORKOUT_SESSION_COMPLETED
    assert row.version == version_after_first  # not re-bumped by the replay


def test_failed_completion_leaves_session_active(app, make_user, monkeypatch):
    """A required-side-effect failure rolls back EVERYTHING, including the session
    terminalization — the session stays ACTIVE, never falsely COMPLETED."""
    user = make_user("sess_fail")
    session = _mk_active_session(user.id)
    monkeypatch.setattr(
        "app.services.workout_completion.service.award_xp",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail")),
    )
    with pytest.raises(RuntimeError):
        complete_workout(_cmd(user.id, session_id=session.id))

    assert PumpCheck.query.filter_by(user_id=user.id).count() == 0
    assert _markers(user.id) == 0
    row = db.session.get(WorkoutSession, session.id)
    assert row.status == WORKOUT_SESSION_ACTIVE  # untouched
    assert row.version == 1


def test_completing_an_abandoned_session_conflicts_with_no_artifacts(app, make_user):
    """A session-scoped completion of an already-ABANDONED session fails closed:
    SessionCompletionConflict, rolled back, no PumpCheck/marker fabricated, and
    the session stays ABANDONED."""
    user = make_user("sess_abandoned")
    session = _mk_active_session(user.id, status=WORKOUT_SESSION_ABANDONED)
    with pytest.raises(SessionCompletionConflict):
        complete_workout(_cmd(user.id, session_id=session.id))

    assert PumpCheck.query.filter_by(user_id=user.id).count() == 0
    assert _markers(user.id) == 0
    row = db.session.get(WorkoutSession, session.id)
    assert row.status == WORKOUT_SESSION_ABANDONED


def test_legacy_completion_fabricates_no_session(app, make_user):
    """session_id=None is the unchanged legacy path — a valid completion that
    creates NO WorkoutSession (no synthetic session fabricated)."""
    user = make_user("legacy")
    result = complete_workout(_cmd(user.id))
    assert result.outcome is CompletionOutcome.CREATED
    assert WorkoutSession.query.filter_by(user_id=user.id).count() == 0


def test_session_of_another_user_is_never_terminalized(app, make_user):
    """The linked session is loaded by (user_id, session_id): a caller can never
    terminalize another user's session by supplying its id."""
    owner = make_user("owner_sess")
    attacker = make_user("attacker_sess")
    victim_session = _mk_active_session(owner.id)
    # Attacker completes their own day, pointing session_id at the victim's row.
    result = complete_workout(_cmd(attacker.id, session_id=victim_session.id))
    assert result.outcome is CompletionOutcome.CREATED
    # The victim's session is untouched (ownership filter excluded it).
    row = db.session.get(WorkoutSession, victim_session.id)
    assert row.status == WORKOUT_SESSION_ACTIVE
    assert row.version == 1
