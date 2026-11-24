"""PD3 finishes the selected strength workout through the canonical claim."""
from datetime import timedelta

import pytest

from app.blueprints.mobile_workout_sessions import _no_photo_completion
from app.extensions import db
from app.models import TrainingSessionOutcome, User, WorkoutCompletionDay, WorkoutSession
from app.services import mobile_workout_sessions
from app.services.training_generation.time_series_model import build_performance_history
from app.timeutil import audit_clock
from tests.test_g4_cardio_sessions import (
    CURRENT, MONDAY, SESSIONS, as_owner, document, get, post, save,
)
from tests.test_mobile_workout_sessions_api import (
    FIXED_DAY, FIXED_NOW, SESSIONS_PATH, _checkpoint, _start, as_mobile,
    owner, plan, sessions_enabled, stranger, workout_ref,
)
from tests.test_priv_k6_no_photo_completion import (
    _completed_photo_free, _rows, forbidden, pinned_completion_clock,
)

FINISH = f"{SESSIONS_PATH}/finish"


def finish(client, headers, ref, *, day=FIXED_DAY, when=FIXED_NOW):
    return post(client, FINISH, {
        **headers, "Idempotency-Key": f"finish:{day}:{ref}",
    }, when=when, json={"workout_ref": ref, "workout_date": day})


def test_finish_without_start_replays_and_reads_back(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    assert WorkoutSession.query.count() == 0

    first = finish(client, headers, workout_ref)
    assert first.status_code == 200, first.json
    assert first.json == {"outcome": "created", "workout_ref": workout_ref,
                          "workout_date": FIXED_DAY}
    assert first.headers["Idempotency-Replayed"] == "false"
    replay = finish(client, headers, workout_ref)
    assert replay.status_code == 200 and replay.json["outcome"] == "already_completed"
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert _rows(owner.id) == _completed_photo_free()
    assert forbidden == []

    session = WorkoutSession.query.filter_by(user_id=owner.id).one()
    assert session.workout_ref == workout_ref
    db.session.remove()
    assert get(client, "/api/v1/today", headers, FIXED_NOW).json["today"]["status"] == "completed"
    assert get(client, f"{SESSIONS_PATH}/current", headers, FIXED_NOW).json["session"] is None
    read = get(client, f"{SESSIONS_PATH}/{session.public_id}", headers, FIXED_NOW)
    assert read.json["session"]["status"] == "completed"
    with audit_clock(FIXED_NOW):
        assert build_performance_history(owner.id).weekly_training_sessions[-1] >= 1


def test_foreign_reference_and_late_replay_cannot_claim_a_day(
        client, owner, stranger, as_mobile, workout_ref):
    foreign = finish(client, as_mobile(stranger), workout_ref)
    assert foreign.status_code in (404, 409)
    headers = as_mobile(owner)
    assert finish(client, headers, workout_ref).status_code == 200
    delayed = finish(
        client, headers, workout_ref, when=FIXED_NOW + timedelta(days=7))
    assert delayed.status_code == 409
    assert _rows(owner.id) == _completed_photo_free()
    assert WorkoutCompletionDay.query.filter_by(user_id=stranger.id).count() == 0
    assert WorkoutSession.query.filter_by(user_id=stranger.id).count() == 0


def test_direct_finish_leaves_active_checkpoint_for_the_session_contract(
        client, owner, as_mobile, workout_ref):
    headers = as_mobile(owner)
    session_ref = _start(client, headers, workout_ref).json["session"]["session_ref"]
    assert _checkpoint(client, headers, session_ref, 0, "pd3-checkpoint-01").status_code == 200
    result = finish(client, headers, workout_ref)
    assert result.status_code == 409
    assert result.json["error"]["code"] == "TRAINING_SESSION_ALREADY_ACTIVE"
    session = WorkoutSession.query.filter_by(user_id=owner.id).one()
    assert (session.status, session.checkpoint_revision) == ("active", 1)
    assert WorkoutCompletionDay.query.filter_by(user_id=owner.id).count() == 0


def test_failed_canonical_commit_leaves_no_session_to_start_or_resume(
        app, owner, workout_ref, monkeypatch):
    from app.services import workout_completion

    def unavailable(_command):
        raise RuntimeError("test persistence failure")

    monkeypatch.setattr(workout_completion, "complete_workout", unavailable)
    with audit_clock(FIXED_NOW), pytest.raises(RuntimeError):
        mobile_workout_sessions.finish_workout(
            owner.id, app.config["SECRET_KEY"], workout_ref, FIXED_DAY,
            **_no_photo_completion(),
        )
    assert WorkoutSession.query.filter_by(user_id=owner.id).count() == 0
    assert WorkoutCompletionDay.query.filter_by(user_id=owner.id).count() == 0


@pytest.mark.parametrize("answer", ["complete", "skip"])
def test_finish_only_answers_strength_on_a_g4_day(raw_client, make_user, as_owner, answer):
    user = make_user(f"pd3-g4-{answer}")
    user_id = user.id
    save(user_id, document())
    headers = as_owner(user)
    plan = get(raw_client, CURRENT, headers, include_sessions="1").json["plan"]
    day = plan["days"][0]
    cardio_ref = day["cardio_session"]["workout_ref"]
    assert finish(
        raw_client, headers, cardio_ref, day=MONDAY.date().isoformat(), when=MONDAY,
    ).status_code == 409
    assert finish(
        raw_client, headers, day["workout_ref"],
        day=MONDAY.date().isoformat(), when=MONDAY,
    ).status_code == 200
    progress = get(raw_client, CURRENT, headers, include_sessions="1").json["plan"]["today_progress"]
    assert [item["state"] for item in progress["sessions"]] == ["completed", "incomplete"]
    assert TrainingSessionOutcome.query.filter_by(user_id=user_id).count() == 0
    xp = db.session.get(User, user_id).rank_points
    result = post(raw_client, f"{SESSIONS}/{cardio_ref}/{answer}", headers)
    assert result.status_code == 200
    assert result.json["state"] == {"complete": "completed", "skip": "skipped"}[answer]
    assert WorkoutCompletionDay.query.filter_by(user_id=user_id).count() == 1
    assert db.session.get(User, user_id).rank_points == xp == 10
