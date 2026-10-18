"""PRIV-K6-B1: photo-free, AI-consent-independent native workout completion.

Founder Privacy Decision K6: a user who declines AI processing keeps core
workout tracking, completed-session history and photo-free completion. These
tests drive the real ``POST /api/v1/training/workout-sessions/<ref>/complete``
surface against real persistence, with the D1 consent authority left at its
REAL default (deny) unless a test explicitly asks for the synthetic layer to
reach the photo path. Every external boundary a photo-free completion must
never touch is replaced by a recorder that fails the test if called.

Real-PostgreSQL races, the locked re-check and the migration verifier live in
``tests/test_priv_k6_no_photo_completion_pg.py``.
"""
import ast
import io
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models import (
    WORKOUT_COMPLETION_MARKER,
    WORKOUT_COMPLETION_PROOFS,
    Activity,
    DailyQuest,
    FeedItem,
    Message,
    PumpCheck,
    User,
    UserQuestProgress,
    WorkoutCompletionDay,
    WorkoutLog,
    WorkoutSession,
)
from app.services.workout_completion import (
    PROOF_NONE,
    PROOF_PHOTO,
    CompleteWorkoutCommand,
    CompletionOutcome,
    complete_workout,
)
from app.services.workout_completion import models as completion_models
from app.services.workout_completion import queries as completion_queries
from app.services.workout_completion import service as completion_service
from app.timeutil import APP_TZ, audit_clock
from tests.test_mobile_workout_sessions_api import (  # noqa: F401 - fixtures
    FIXED_DAY, FIXED_NOW, SESSIONS_PATH, as_mobile, completion_proof, lifecycle_events,
    owner, plan, sessions_enabled, stranger, workout_ref, _checkpoint, _start,
)

ROUTE_PATH = Path("app/blueprints/mobile_workout_sessions.py")
SERVICE_PATH = Path("app/services/workout_completion/service.py")
MIGRATION = Path("migrations/versions/f9c6d0a1b2e3_workout_completion_day_claim.py")
DAY = date.fromisoformat(FIXED_DAY)


class _CompletionClock(datetime):
    """``completed_at`` on the pinned Istanbul day (TI-03 hazard note)."""

    @classmethod
    def utcnow(cls):
        return FIXED_NOW.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture(autouse=True)
def pinned_completion_clock(monkeypatch):
    monkeypatch.setattr(completion_service, "datetime", _CompletionClock)


@pytest.fixture
def forbidden(monkeypatch):
    """Every boundary a photo-free completion must never reach, as recorders.

    A call is recorded AND raises, so a hidden fallback can neither succeed
    silently nor be swallowed into a false pass.
    """
    from app.blueprints import ai_consent as consent_routes
    from app.blueprints import mobile_workout_sessions as routes
    from app.services import ai_consent, ai_provider_call

    calls = []

    def recorder(name):
        def _called(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"photo-free completion reached {name}")
        return _called

    monkeypatch.setattr(consent_routes, "require_legacy_ai", recorder("require_legacy_ai"))
    monkeypatch.setattr(routes, "validate_uploaded_pump_check_image", recorder("image_validation"))
    monkeypatch.setattr(routes, "validate_pump_check", recorder("vision_validation"))
    monkeypatch.setattr(routes, "latest_training_plan_score", recorder("plan_score"))
    monkeypatch.setattr(routes.s3_helper, "upload_image", recorder("s3_upload"))
    monkeypatch.setattr(routes.s3_helper, "is_enabled", recorder("s3_is_enabled"))
    monkeypatch.setattr(ai_consent, "dispatch", recorder("ai_consent_dispatch"))
    monkeypatch.setattr(ai_provider_call, "admit", recorder("ai_provider_admit"))
    return calls


def _complete_raw(client, headers, ref, revision=0, key="complete-key-0001",
                  clock=FIXED_NOW, **request_kwargs):
    with audit_clock(clock):
        return client.post(
            f"{SESSIONS_PATH}/{ref}/complete",
            headers={**headers, "If-Match": str(revision), "Idempotency-Key": key},
            **request_kwargs)


def _started(client, headers, workout_ref):
    response = _start(client, headers, workout_ref)
    assert response.status_code == 201, response.json
    return response.json["session"]["session_ref"]


def _rows(user_id):
    return {
        "claims": [(c.date_key, c.proof) for c in
                   WorkoutCompletionDay.query.filter_by(user_id=user_id).all()],
        "pump_checks": PumpCheck.query.filter_by(user_id=user_id).count(),
        "markers": WorkoutLog.query.filter_by(
            user_id=user_id, exercise_name=WORKOUT_COMPLETION_MARKER).count(),
        "evidence_rows": WorkoutLog.query.filter(
            WorkoutLog.user_id == user_id,
            WorkoutLog.exercise_name != WORKOUT_COMPLETION_MARKER).count(),
        "activities": [a.content for a in Activity.query.filter_by(
            user_id=user_id, activity_type="workout_completed").all()],
        "xp": db.session.get(User, user_id).rank_points or 0,
        "messages": Message.query.filter_by(sender_id=user_id).count(),
        "feed_items": FeedItem.query.count(),
        "sessions": [s.status for s in WorkoutSession.query.filter_by(user_id=user_id).all()],
    }


NOTHING = {"claims": [], "pump_checks": 0, "markers": 0, "evidence_rows": 0,
           "activities": [], "xp": 0, "messages": 0, "feed_items": 0,
           "sessions": ["active"]}


def _completed_photo_free(xp=10):
    return {"claims": [(FIXED_DAY, "none")], "pump_checks": 0, "markers": 1,
            "evidence_rows": 0, "activities": ["Bugünkü antrenmanını tamamladı"],
            "xp": xp, "messages": 0, "feed_items": 0, "sessions": ["completed"]}


# -- K6-A / K6-B: AI declined, no image, zero external work -------------------

@pytest.mark.parametrize("body", [
    {}, {"json": {}}, {"json": {"note": "felt strong"}},
    {"data": b"", "content_type": "application/octet-stream"},
], ids=["no_body", "empty_json", "json_without_photo_fields", "empty_bytes"])
def test_k6a_ai_declined_user_completes_without_a_photo(
        client, owner, as_mobile, workout_ref, forbidden, body):
    from app.services import ai_consent

    # The REAL D1 authority, untouched: no policy, no disclosure recipient.
    assert dict(ai_consent.POLICIES) == {}
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    response = _complete_raw(client, headers, ref, **body)

    assert response.status_code == 200, response.json
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json["completion"] == {
        "outcome": "created", "proof": "none", "xp_awarded": 10,
        "new_total": 10, "level": response.json["completion"]["level"],
        "title": response.json["completion"]["title"]}
    assert response.json["session"]["status"] == "completed"
    assert forbidden == []
    assert _rows(owner.id) == _completed_photo_free()


def test_k6b_no_image_means_no_challenge_photo_event_and_no_gallery_item(
        client, owner, as_mobile, workout_ref, forbidden, monkeypatch):
    from app.services import challenges

    events = []
    monkeypatch.setattr(challenges, "record_event",
                        lambda user_id, metric, *a, **kw: events.append(metric))
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    assert _complete_raw(client, headers, ref).status_code == 200

    # First-party quest funnel still runs; the Pump Check challenge does not.
    assert "workout_logged" in events
    assert "pump_check_created" not in events
    gallery = client.get("/api/v1/pump-checks", headers=headers)
    assert gallery.status_code == 200
    assert gallery.json["pump_checks"] == []
    assert forbidden == []


def test_k6a_photo_free_completion_preserves_first_party_quest_progress(
        client, owner, as_mobile, workout_ref, forbidden):
    db.session.add(DailyQuest(title="Antrenman Kaydet", description="d",
                              points_reward=50, quest_type="workout_logged"))
    db.session.commit()
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    response = _complete_raw(client, headers, ref)

    assert response.json["completion"]["xp_awarded"] == 60  # base 10 + quest 50
    assert UserQuestProgress.query.filter_by(user_id=owner.id).count() == 1
    assert _rows(owner.id)["xp"] == 60


# -- checkpoint preservation + the empty-checkpoint product rule ---------------

def test_photo_free_completion_preserves_the_durable_checkpoint(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    saved = _checkpoint(client, headers, ref, 0, "checkpoint-key-0001")
    assert saved.status_code == 200, saved.json
    before = _session_snapshot(owner.id)

    response = _complete_raw(client, headers, ref, revision=1)

    assert response.status_code == 200, response.json
    after = _session_snapshot(owner.id)
    assert after["checkpoint"] == before["checkpoint"]
    assert after["revision"] == before["revision"] == 1
    assert response.json["session"]["checkpoint"] == saved.json["session"]["checkpoint"]
    # Performed sets stay in the checkpoint; nothing is re-materialized as logs.
    assert _rows(owner.id)["evidence_rows"] == 0


def test_empty_checkpoint_completion_is_the_pre_existing_rule_and_invents_nothing(
        client, owner, as_mobile, workout_ref, forbidden):
    """Qualified, not changed: revision 0 (no checkpoint) was already
    completable on the photo path. Photo-free keeps that rule and fabricates
    no performed set, exercise row or checkpoint."""
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    response = _complete_raw(client, headers, ref, revision=0)

    assert response.status_code == 200
    assert response.json["session"]["checkpoint"] is None
    assert _session_snapshot(owner.id)["checkpoint"] is None
    assert _rows(owner.id)["evidence_rows"] == 0


def _session_snapshot(user_id):
    row = WorkoutSession.query.filter_by(user_id=user_id).one()
    return {"checkpoint": row.checkpoint_data, "revision": row.checkpoint_revision}


# -- K6-C: photo evidence never bypasses D1 through the photo-free path --------

PHOTO_REQUESTS = {
    "multipart_image": {"data": {"image": (io.BytesIO(b"\xff\xd8\xff-jpeg"), "a.jpg")},
                        "content_type": "multipart/form-data"},
    "multipart_misnamed_file": {"data": {"photo": (io.BytesIO(b"\xff\xd8\xff"), "a.jpg")},
                                "content_type": "multipart/form-data"},
    "multipart_empty_file": {"data": {"image": (io.BytesIO(b""), "")},
                             "content_type": "multipart/form-data"},
    "form_metadata_only": {"data": {"location_type": "gym"}},
    "form_unrelated_field": {"data": {"anything": "x"}},
    "json_image": {"json": {"image": "data:image/jpeg;base64,/9j/"}},
    "json_description": {"json": {"description": "leg day"}},
    "raw_image_bytes": {"data": b"\xff\xd8\xff\xe0-raw-jpeg", "content_type": "image/jpeg"},
    "json_list": {"json": ["image"]},
}


@pytest.mark.parametrize("case", sorted(PHOTO_REQUESTS))
def test_k6c_submitted_photo_meets_the_real_d1_gate_and_is_never_discarded(
        client, owner, as_mobile, workout_ref, monkeypatch, case):
    from app.blueprints import mobile_workout_sessions as routes

    vision = []
    monkeypatch.setattr(routes, "validate_pump_check",
                        lambda *a, **kw: vision.append(1) or {"valid": True})
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    request_kwargs = dict(PHOTO_REQUESTS[case])
    if "data" in request_kwargs and isinstance(request_kwargs["data"], dict):
        request_kwargs["data"] = {
            k: ((io.BytesIO(v[0].getvalue()), v[1]) if isinstance(v, tuple) else v)
            for k, v in request_kwargs["data"].items()}
    response = _complete_raw(client, headers, ref, **request_kwargs)

    assert response.status_code == 403, (case, response.status_code, response.json)
    assert response.json["error"]["code"] == "AI_CONSENT_DISCLOSURE_UNAVAILABLE"
    assert vision == []
    assert _rows(owner.id) == NOTHING


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_k6c_an_unusable_photo_is_refused_not_completed_photo_free(
        client, owner, as_mobile, workout_ref, monkeypatch):
    from app.blueprints import mobile_workout_sessions as routes

    vision = []
    monkeypatch.setattr(routes, "validate_pump_check",
                        lambda *a, **kw: vision.append(1) or {"valid": True})
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    response = _complete_raw(
        client, headers, ref, content_type="multipart/form-data",
        data={"image": (io.BytesIO(b"not an image at all"), "a.jpg")})

    assert response.status_code == 400
    assert response.json["error"]["code"] == "TRAINING_SESSION_INVALID_REQUEST"
    assert vision == []
    assert _rows(owner.id) == NOTHING


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_k6c_genuine_photo_completion_is_unchanged(
        client, owner, as_mobile, workout_ref, completion_proof):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    response = _complete_raw(client, headers, ref,
                             data={"location_type": "gym", "description": "leg day"})

    assert response.status_code == 200, response.json
    assert response.json["completion"]["proof"] == "photo"
    assert response.json["completion"]["xp_awarded"] == 35
    assert completion_proof["validate"] == 1
    rows = _rows(owner.id)
    assert rows["claims"] == [(FIXED_DAY, "photo")]
    assert rows["pump_checks"] == 1 and rows["xp"] == 35
    assert rows["activities"] == ["Bugünkü antrenmanını tamamladı (foto eklendi)"]


# -- K6-D: ownership -----------------------------------------------------------

def test_k6d_another_user_cannot_complete_or_reuse_the_session_reference(
        client, owner, stranger, as_mobile, workout_ref, forbidden):
    ref = _started(client, as_mobile(owner), workout_ref)

    response = _complete_raw(client, as_mobile(stranger), ref)

    assert response.status_code == 404
    assert response.json["error"]["code"] == "TRAINING_SESSION_NOT_FOUND"
    assert _rows(owner.id) == NOTHING
    assert WorkoutCompletionDay.query.filter_by(user_id=stranger.id).count() == 0
    assert forbidden == []


# -- K6-E: stale revision ------------------------------------------------------

def test_k6e_a_stale_declared_revision_is_a_conflict_with_no_claim(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    assert _checkpoint(client, headers, ref, 0, "checkpoint-key-0001").status_code == 200

    response = _complete_raw(client, headers, ref, revision=0)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_REVISION_CONFLICT"
    assert response.headers["Session-Resolution"] == "reread"
    assert _rows(owner.id) == NOTHING


def test_k6e_checkpoint_landing_between_preflight_and_transaction_is_refused(
        client, owner, as_mobile, workout_ref, forbidden, monkeypatch):
    from app.services import mobile_workout_sessions as sessions

    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    real_prepare = sessions.prepare_complete

    def _prepare_then_race(user_id, session_ref, revision):
        row = real_prepare(user_id, session_ref, revision)
        # A checkpoint commits after the cheap preflight accepted revision 0.
        db.session.execute(sa.text(
            "UPDATE workout_session SET checkpoint_revision = checkpoint_revision + 1 "
            "WHERE public_id = :ref"), {"ref": session_ref})
        db.session.commit()
        return row

    monkeypatch.setattr(sessions, "prepare_complete", _prepare_then_race)

    response = _complete_raw(client, headers, ref, revision=0)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_REVISION_CONFLICT"
    assert _rows(owner.id) == NOTHING


# -- K6-F: duplicate and lost-response retries ---------------------------------

def test_k6f_retries_replay_one_completion_one_reward_one_terminalization(
        client, owner, as_mobile, workout_ref, forbidden, lifecycle_events):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    first = _complete_raw(client, headers, ref)
    same_key = _complete_raw(client, headers, ref)
    lost_response = _complete_raw(client, headers, ref, key="complete-key-0002")

    assert first.json["completion"]["outcome"] == "created"
    for replay in (same_key, lost_response):
        assert replay.status_code == 200
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json["completion"] == {
            "outcome": "already_completed", "proof": None, "xp_awarded": 0,
            "new_total": None, "level": None, "title": None}
    assert _rows(owner.id) == _completed_photo_free()
    completed = [c for c in lifecycle_events
                 if c[0] == "WorkoutSessionLifecycle" and c[1] == {"Event": "completed"}]
    assert len(completed) == 1


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_k6f_photo_retry_after_photo_free_completion_processes_no_photo(
        client, owner, as_mobile, workout_ref, completion_proof):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    assert _complete_raw(client, headers, ref).json["completion"]["proof"] == "none"

    retry = _complete_raw(client, headers, ref, key="complete-key-0002",
                          data={"location_type": "gym", "description": "late photo"})

    assert retry.json["completion"]["outcome"] == "already_completed"
    assert completion_proof["validate"] == 0
    assert _rows(owner.id) == _completed_photo_free()


# -- mixed mode, sequential (the PG module races them) -------------------------

def test_photo_completion_after_photo_free_completion_is_already_completed(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    assert _complete_raw(client, headers, ref).status_code == 200

    result = complete_workout(CompleteWorkoutCommand(
        user_id=owner.id, today=DAY, location_type="gym", base_xp=10,
        photo_bonus=25, entry_path="route"))

    assert result.outcome is CompletionOutcome.ALREADY_COMPLETED
    assert _rows(owner.id) == _completed_photo_free()


@pytest.mark.usefixtures("synthetic_ai_layer")
def test_photo_free_after_photo_completion_reconciles_without_rewards(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    complete_workout(CompleteWorkoutCommand(
        user_id=owner.id, today=DAY, location_type="gym", base_xp=10,
        photo_bonus=25, entry_path="route"))

    response = _complete_raw(client, headers, ref)

    assert response.json["completion"]["outcome"] == "already_completed"
    rows = _rows(owner.id)
    assert rows["claims"] == [(FIXED_DAY, "photo")]
    assert rows["xp"] == 35 and rows["markers"] == 1
    assert rows["sessions"] == ["completed"]


# -- K6-I: historical compatibility -------------------------------------------

def _historical_pump_check(user_id, date_key):
    db.session.add(PumpCheck(
        user_id=user_id, image_key=None, location_type="gym", description="legacy",
        valid=True, fallback=False, date_key=date_key, visibility="private",
        shared_friend_ids=[], created_at=datetime(2026, 7, 23, 6, 0)))
    db.session.commit()


def test_k6i_historical_daily_pump_check_still_counts_as_completed(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    _historical_pump_check(owner.id, FIXED_DAY)

    response = _complete_raw(client, headers, ref)

    assert response.json["completion"]["outcome"] == "already_completed"
    rows = _rows(owner.id)
    assert rows["claims"] == [] and rows["xp"] == 0 and rows["markers"] == 0
    assert rows["sessions"] == ["completed"]  # reconciled, never left ACTIVE


def test_k6i_standalone_pump_check_same_day_is_not_a_completion(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    _historical_pump_check(owner.id, None)
    standalone = PumpCheck.query.filter_by(user_id=owner.id).one()

    response = _complete_raw(client, headers, ref)

    assert response.json["completion"]["outcome"] == "created"
    rows = _rows(owner.id)
    assert rows["claims"] == [(FIXED_DAY, "none")] and rows["xp"] == 10
    # The historical photo row is never mutated or re-dated.
    assert db.session.get(PumpCheck, standalone.id).date_key is None


def test_completed_days_reads_both_claims_in_one_statement(app, owner):
    db.session.add(WorkoutCompletionDay(user_id=owner.id, date_key="2026-07-21", proof="none"))
    _historical_pump_check(owner.id, "2026-07-22")
    _historical_pump_check(owner.id, None)
    statements = []

    def _count(conn, cursor, statement, *args):
        statements.append(statement)

    sa.event.listen(db.engine, "before_cursor_execute", _count)
    try:
        days = completion_queries.completed_days(
            owner.id, (date(2026, 7, 21), date(2026, 7, 22), DAY))
    finally:
        sa.event.remove(db.engine, "before_cursor_execute", _count)
    assert days == {date(2026, 7, 21), date(2026, 7, 22)}
    assert len(statements) == 1 and "UNION" in statements[0].upper()


def test_deleting_the_photo_of_a_photo_completion_does_not_uncomplete_the_day(
        client, owner, as_mobile, workout_ref):
    """Behavior change (intentional): before K6 the PumpCheck WAS the claim, so
    a gallery delete reopened the day for a second full reward."""
    headers = as_mobile(owner)
    complete_workout(CompleteWorkoutCommand(
        user_id=owner.id, today=DAY, location_type="gym", base_xp=10,
        photo_bonus=25, entry_path="route"))
    PumpCheck.query.filter_by(user_id=owner.id).delete()
    db.session.commit()

    assert completion_queries.already_completed_today(owner.id, DAY) is True
    again = complete_workout(CompleteWorkoutCommand(
        user_id=owner.id, today=DAY, location_type="gym", base_xp=10,
        photo_bonus=25, entry_path="route"))
    assert again.outcome is CompletionOutcome.ALREADY_COMPLETED
    assert _rows(owner.id)["xp"] == 35


# -- K6-J: forced failure → full rollback --------------------------------------

@pytest.mark.parametrize("seam", ["award_xp", "log_activity", "_claim_quest"])
def test_k6j_a_failed_required_write_rolls_everything_back(
        client, owner, as_mobile, workout_ref, forbidden, monkeypatch, seam):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)

    def _boom(*args, **kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr(completion_service, seam, _boom)
    response = _complete_raw(client, headers, ref)

    assert response.status_code == 503
    assert response.json["error"]["code"] == "TRAINING_SESSION_UNAVAILABLE"
    assert response.json["error"]["retryable"] is True
    assert _rows(owner.id) == NOTHING


# -- K6-K: no fabricated PumpCheck ---------------------------------------------

def test_k6k_service_writes_no_pump_check_and_reports_no_photo_identifier(app, owner):
    session = WorkoutSession(user_id=owner.id, workout_date=FIXED_DAY, source="scheduled")
    db.session.add(session)
    db.session.commit()

    result = complete_workout(CompleteWorkoutCommand(
        user_id=owner.id, today=DAY, session_id=session.id, proof=PROOF_NONE,
        base_xp=10, photo_bonus=0, entry_path="session_service"))

    assert result.outcome is CompletionOutcome.CREATED
    assert result.proof == PROOF_NONE and result.pump_check_id is None
    assert result.xp_awarded == 10
    assert PumpCheck.query.count() == 0
    assert _rows(owner.id)["activities"] == ["Bugünkü antrenmanını tamamladı"]


def test_k6k_photo_free_command_without_a_lockable_owned_session_fails_closed(app, owner, stranger):
    from app.services.workout_completion import SessionCompletionConflict

    foreign = WorkoutSession(user_id=stranger.id, workout_date=FIXED_DAY, source="scheduled")
    db.session.add(foreign)
    db.session.commit()

    for session_id in (foreign.id, foreign.id + 999):
        with pytest.raises(SessionCompletionConflict):
            complete_workout(CompleteWorkoutCommand(
                user_id=owner.id, today=DAY, session_id=session_id, proof=PROOF_NONE,
                base_xp=10, photo_bonus=0, entry_path="session_service"))
    assert WorkoutCompletionDay.query.count() == 0
    assert db.session.get(WorkoutSession, foreign.id).status == "active"


# -- K6-L: day boundary --------------------------------------------------------

def test_k6l_completing_after_the_istanbul_day_boundary_is_stale_not_a_claim(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    midnight = datetime(2026, 7, 24, 0, 0, 30, tzinfo=APP_TZ)

    response = _complete_raw(client, headers, ref, clock=midnight)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_STALE"
    assert _rows(owner.id) == NOTHING


def test_k6l_a_late_evening_completion_claims_its_own_istanbul_day(
        client, owner, as_mobile, workout_ref, forbidden):
    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    # 23:59 Istanbul is 20:59 UTC on the same day.
    late = datetime(2026, 7, 23, 23, 59, tzinfo=APP_TZ)

    response = _complete_raw(client, headers, ref, clock=late)

    assert response.status_code == 200
    assert _rows(owner.id)["claims"] == [(FIXED_DAY, "none")]


# -- readers: every completed-day consumer sees the photo-free completion ------

def test_every_completed_day_reader_sees_the_photo_free_completion(
        app, client, owner, as_mobile, workout_ref, forbidden):
    from app.services.training_generation.time_series_model import build_performance_history
    from app.services.workout_state import resolve_workout_state

    headers = as_mobile(owner)
    ref = _started(client, headers, workout_ref)
    assert _complete_raw(client, headers, ref).status_code == 200
    # Harness artifact only: WorkoutLog.created_at defaults to the REAL utcnow,
    # which audit_clock cannot pin; place the marker on the pinned day it was
    # written for, so the marker/claim agreement check is meaningful.
    WorkoutLog.query.filter_by(user_id=owner.id).update(
        {"created_at": _CompletionClock.utcnow()})
    db.session.commit()

    with audit_clock(FIXED_NOW):
        state = resolve_workout_state(owner.id, today=DAY, sessions_enabled=True)
        assert state.completed_today is True
        assert state.primary_state == "completed"
        assert state.to_dict()["anomaly"] is None  # marker and claim agree
        today = client.get("/api/v1/today", headers=headers)
        current = client.get(f"{SESSIONS_PATH}/current", headers=headers)
        restart = client.post(SESSIONS_PATH, headers=headers,
                              json={"workout_ref": workout_ref})
        history = build_performance_history(owner.id)
    assert today.status_code == 200 and today.json["today"]["status"] == "completed"
    assert current.status_code == 200 and current.json.get("session") is None
    assert restart.status_code == 409
    assert restart.json["error"]["code"] == "TRAINING_WORKOUT_NOT_STARTABLE"
    assert WorkoutSession.query.filter_by(user_id=owner.id).count() == 1
    assert history.weekly_training_sessions[-1] >= 1
    # Adherence counts the CLAIM, not merely the marker row: without the marker
    # the photo-free day is still a trained day.
    WorkoutLog.query.filter_by(user_id=owner.id).delete()
    db.session.commit()
    with audit_clock(FIXED_NOW):
        assert build_performance_history(owner.id).weekly_training_sessions[-1] == 1
    # Session history keeps the completed session, readable by reference.
    read = client.get(f"{SESSIONS_PATH}/{ref}", headers=headers)
    assert read.json["session"]["status"] == "completed"


# -- command contract ----------------------------------------------------------

def test_proof_vocabulary_matches_the_persisted_check_constraint():
    assert completion_models.COMPLETION_PROOFS == WORKOUT_COMPLETION_PROOFS
    assert (PROOF_PHOTO, PROOF_NONE) == WORKOUT_COMPLETION_PROOFS


@pytest.mark.parametrize("field,value", [
    ("session_id", None), ("image_key", "pump-checks/1/x.jpg"),
    ("shared_friend_ids", (2,)), ("visibility", "friends"), ("photo_bonus", 25),
    ("location_type", "gym"), ("description", "leg day"), ("workout_score", 7.5),
    ("activity_text", "Bugünkü antrenmanını tamamladı (foto eklendi)"),
])
def test_a_photo_free_command_cannot_carry_photo_evidence(field, value):
    fields = {"user_id": 1, "today": DAY, "session_id": 1, "proof": PROOF_NONE,
              "photo_bonus": 0}
    fields[field] = value
    with pytest.raises(ValueError):
        CompleteWorkoutCommand(**fields)


def test_an_unknown_proof_is_refused():
    with pytest.raises(ValueError):
        CompleteWorkoutCommand(user_id=1, today=DAY, proof="selfie")


def test_existing_photo_commands_keep_their_defaults():
    command = CompleteWorkoutCommand(user_id=1, today=DAY)
    assert command.proof == PROOF_PHOTO
    assert command.resolved_activity_text == "Bugünkü antrenmanını tamamladı (foto eklendi)"


# -- C7: violation classification ---------------------------------------------

class _Diag:
    def __init__(self, name):
        self.constraint_name = name


class _Orig(Exception):
    def __init__(self, name=None, text=""):
        super().__init__(text)
        self.diag = _Diag(name)


@pytest.mark.parametrize("name,expected", [
    ("uq_workout_completion_day", True), ("uq_pump_check_day", True),
    ("ck_workout_completion_day_proof", False), ("workout_completion_day_user_id_fkey", False),
    ("uq_pump_check_user_idempotency", False), ("uq_workout_session_active_owner", False),
])
def test_only_named_day_claims_classify_as_replay_on_postgres(name, expected):
    exc = sa.exc.IntegrityError("stmt", {}, _Orig(name))
    assert completion_queries.is_completion_day_violation(exc) is expected


@pytest.mark.parametrize("text,expected", [
    ("UNIQUE constraint failed: workout_completion_day.user_id, "
     "workout_completion_day.date_key", True),
    ("UNIQUE constraint failed: pump_check.user_id, pump_check.date_key", True),
    ("CHECK constraint failed: ck_workout_completion_day_proof", False),
    ("NOT NULL constraint failed: workout_completion_day.date_key", False),
    ("FOREIGN KEY constraint failed", False),
])
def test_only_day_claim_messages_classify_as_replay_on_sqlite(text, expected):
    exc = sa.exc.IntegrityError("stmt", {}, _Orig(None, text))
    assert completion_queries.is_completion_day_violation(exc) is expected


def test_a_real_sqlite_check_violation_is_not_swallowed_as_replay(app, owner):
    db.session.add(WorkoutCompletionDay(user_id=owner.id, date_key=FIXED_DAY, proof="bogus"))
    with pytest.raises(sa.exc.IntegrityError) as caught:
        db.session.flush()
    db.session.rollback()
    assert completion_queries.is_completion_day_violation(caught.value) is False


def test_a_real_sqlite_duplicate_day_is_a_replay(app, owner):
    db.session.add(WorkoutCompletionDay(user_id=owner.id, date_key=FIXED_DAY, proof="none"))
    db.session.commit()
    db.session.add(WorkoutCompletionDay(user_id=owner.id, date_key=FIXED_DAY, proof="photo"))
    with pytest.raises(sa.exc.IntegrityError) as caught:
        db.session.flush()
    db.session.rollback()
    assert completion_queries.is_completion_day_violation(caught.value) is True


# -- structure: the photo-free branch cannot reach the photo path --------------

def _function(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_photo_free_kwargs_builder_touches_no_photo_boundary():
    source = ast.unparse(_function(ROUTE_PATH, "_no_photo_completion"))
    for forbidden in ("require_legacy_ai", "validate_", "s3_helper", "request.files",
                      "latest_training_plan_score", "image_key"):
        assert forbidden not in source, forbidden
    assert "PROOF_NONE" in source and "'photo_bonus': 0" in source


def test_the_photo_path_still_checks_consent_before_reading_the_image():
    source = ast.unparse(_function(ROUTE_PATH, "_completion_proof"))
    assert source.index("require_legacy_ai()") < source.index("validate_uploaded_pump_check_image(")
    assert source.index("validate_uploaded_pump_check_image(") < source.index("validate_pump_check(")


def test_the_route_chooses_photo_free_only_on_replay_or_affirmative_absence():
    source = ast.unparse(_function(ROUTE_PATH, "complete_workout_session"))
    assert "if replaying or not _photo_evidence_submitted():" in source
    assert source.index("already_completed_today(") < source.index("_photo_evidence_submitted()")


def test_only_the_completion_service_constructs_a_day_claim():
    constructors = []
    for path in list(Path("app").rglob("*.py")) + list(Path("fitx_mcp").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "WorkoutCompletionDay"):
                constructors.append(str(path))
    assert constructors == [str(SERVICE_PATH)]


def test_the_locked_recheck_sits_between_the_day_lock_and_the_claim_write():
    source = ast.unparse(_function(SERVICE_PATH, "complete_workout"))
    lock_at = source.index("lock_completion_day(")
    recheck_at = source.index("already_completed_today(", lock_at)
    claim_at = source.index("db.session.add(claim)")
    assert lock_at < recheck_at < claim_at
    assert "command.base_xp + (command.photo_bonus if is_photo else 0)" in source


# -- migration (SQLite; PostgreSQL negative controls in the _pg module) ---------

def _migration_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(MIGRATION.stem, MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, fn):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    with Operations.context(MigrationContext.configure(connection)):
        fn()


def test_sqlite_migration_accepts_create_all_shape_and_refuses_drift():
    module = _migration_module()
    assert module.expand_contract == "expand"
    assert module.down_revision == "e7a1c3d5f9b2"

    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
        WorkoutCompletionDay.__table__.create(conn)
        _run(conn, module.upgrade)
        _run(conn, module.upgrade)

    drifted = sa.create_engine("sqlite://")
    with drifted.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql(
            "CREATE TABLE workout_completion_day (id INTEGER NOT NULL, "
            'user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE, '
            "date_key VARCHAR(10) NOT NULL, proof VARCHAR(8) NOT NULL, "
            "created_at DATETIME NOT NULL, PRIMARY KEY (id), "
            "CONSTRAINT ck_workout_completion_day_proof CHECK (proof IN ('photo', 'none')))")
        with pytest.raises(RuntimeError, match="day uniqueness"):
            _run(conn, module.upgrade)


def test_sqlite_downgrade_refuses_populated_claims():
    module = _migration_module()
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
        conn.exec_driver_sql('INSERT INTO "user" (id) VALUES (1)')
        _run(conn, module.upgrade)
        conn.exec_driver_sql(
            "INSERT INTO workout_completion_day (user_id, date_key, proof, created_at) "
            "VALUES (1, '2026-07-23', 'none', '2026-07-23 12:00:00')")
        with pytest.raises(RuntimeError, match="must be retained"):
            _run(conn, module.downgrade)
        assert conn.exec_driver_sql(
            "SELECT count(*) FROM workout_completion_day").scalar() == 1
