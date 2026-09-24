"""Native Progress PR2 — a standalone Pump Check is not workout-completion proof.

Defect D1 (docs/mobile/progress-vertical-slice.md): ``pump_check`` holds two kinds
of row. The canonical completion transaction (``workout_completion.service``)
writes ``date_key`` — the exact column ``uq_pump_check_day`` claims. A
standalone ``POST /api/v1/pump-checks`` writes ``date_key=NULL``. Every
completion reader used to count *any* row created that Istanbul day, so a
standalone photo taken before training made the real completion answer
``already_completed`` with no marker, no XP and no proof, and made Today read
completed before the user had trained.

The rule proved here: only a row carrying the canonical claim is completion
evidence — in the preflight, in ``workout_state`` (Today, ``/workout/status``,
native Today) and in the training-generation adherence input. The matrix runs
through the real native HTTP routes; the PostgreSQL races live in
``tests/test_progress_pump_check_completion_pg.py``.
"""
from datetime import datetime, timedelta

from app.extensions import db
from app.models import (
    WORKOUT_COMPLETION_MARKER,
    WORKOUT_SESSION_COMPLETED,
    PumpCheck,
    User,
    WorkoutLog,
    WorkoutSession,
)
from app.services.training_generation.time_series_model import (
    build_performance_history,
)
from app.services.workout_completion import already_completed_today
from app.services.workout_completion.queries import completed_days
from app.services.workout_state import models as ws_models
from app.services.workout_state import resolve_workout_state
from app.timeutil import audit_clock
from tests.test_mobile_progress_pr1_convergence_guard import (
    _create_standalone_on_fixed_day,
    standalone_dependencies,  # noqa: F401 - fixture
)
from tests.test_mobile_workout_sessions_api import (  # noqa: F401 - fixtures
    FIXED_DAY,
    FIXED_NOW,
    _complete,
    _start,
    as_mobile,
    completion_proof,
    owner,
    plan,
    sessions_enabled,
    stranger,
    workout_ref,
)

TODAY_PATH = "/api/v1/today"
PUMP_CHECKS_PATH = "/api/v1/pump-checks"
DAY = FIXED_NOW.date()
FIXED_DAY_UTC_MORNING = datetime(2026, 7, 23, 9, 0)
# The native route's completion grant: base 10 + photo bonus 25, no quest seeded.
COMPLETION_XP = 35


def _today(client, headers):
    with audit_clock(FIXED_NOW):
        response = client.get(TODAY_PATH, headers=headers)
    assert response.status_code == 200
    return response.json["today"]


def _counts(user_id):
    rows = PumpCheck.query.filter_by(user_id=user_id).all()
    return {
        "standalone": sum(1 for row in rows if row.date_key is None),
        "proofs": sum(1 for row in rows if row.date_key is not None),
        "markers": WorkoutLog.query.filter_by(
            user_id=user_id, exercise_name=WORKOUT_COMPLETION_MARKER).count(),
        "xp": db.session.get(User, user_id).rank_points or 0,
    }


def _not_completed(client, headers, user_id):
    assert already_completed_today(user_id, DAY) is False
    assert resolve_workout_state(user_id, today=DAY).completed_today is False
    today = _today(client, headers)
    assert today["workout"]["completed"] is False
    assert today["status"] != ws_models.PRIMARY_COMPLETED


def _completed(client, headers, user_id):
    assert already_completed_today(user_id, DAY) is True
    assert resolve_workout_state(user_id, today=DAY).completed_today is True
    today = _today(client, headers)
    assert today["workout"]["completed"] is True
    assert today["status"] == ws_models.PRIMARY_COMPLETED


def _add_standalone_row(user_id, created_at, key):
    """A standalone row exactly as ``mobile_pump_checks.service`` persists it."""
    db.session.add(PumpCheck(
        user_id=user_id, captured_at=created_at, body_region="upper_body",
        visibility="private", valid=True, fallback=False,
        analysis_status="completed", idempotency_key=key,
        public_id=key[:24], date_key=None, created_at=created_at))
    db.session.commit()


# -- A: standalone first, canonical completion later ------------------------
def test_a_standalone_then_completion_creates_exactly_one_completion(
        client, owner, as_mobile, workout_ref, completion_proof,
        standalone_dependencies):
    headers = as_mobile(owner)
    _create_standalone_on_fixed_day(client, headers)
    _not_completed(client, headers, owner.id)

    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    done = _complete(client, headers, reference, 0)

    assert done.status_code == 200
    assert done.json["completion"]["outcome"] == "created"
    assert done.json["completion"]["xp_awarded"] == COMPLETION_XP
    assert completion_proof["validate"] == 1
    assert _counts(owner.id) == {
        "standalone": 1, "proofs": 1, "markers": 1, "xp": COMPLETION_XP}
    proof = PumpCheck.query.filter(
        PumpCheck.user_id == owner.id, PumpCheck.date_key.isnot(None)).one()
    assert proof.date_key == FIXED_DAY
    assert WorkoutSession.query.filter_by(
        user_id=owner.id).one().status == WORKOUT_SESSION_COMPLETED
    _completed(client, headers, owner.id)


# -- B: standalone only ------------------------------------------------------
def test_b_standalone_only_is_never_completion(
        client, owner, as_mobile, plan, standalone_dependencies):
    headers = as_mobile(owner)
    _create_standalone_on_fixed_day(client, headers)

    _not_completed(client, headers, owner.id)
    assert _counts(owner.id) == {
        "standalone": 1, "proofs": 0, "markers": 0, "xp": 0}
    assert _today(client, headers)["status"] == (
        ws_models.PRIMARY_SCHEDULED_NOT_STARTED)


# -- C: canonical completion only (unchanged behaviour) ---------------------
def test_c_canonical_completion_alone_is_unchanged(
        client, owner, as_mobile, workout_ref, completion_proof):
    headers = as_mobile(owner)
    _not_completed(client, headers, owner.id)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    done = _complete(client, headers, reference, 0)

    assert done.json["completion"]["outcome"] == "created"
    assert _counts(owner.id) == {
        "standalone": 0, "proofs": 1, "markers": 1, "xp": COMPLETION_XP}
    _completed(client, headers, owner.id)


# -- D: canonical replay stays idempotent (with a standalone row present) ---
def test_d_completion_replay_is_idempotent(
        client, owner, as_mobile, workout_ref, completion_proof,
        standalone_dependencies):
    headers = as_mobile(owner)
    _create_standalone_on_fixed_day(client, headers)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    first = _complete(client, headers, reference, 0)
    replay = _complete(client, headers, reference, 0)

    assert first.json["completion"]["outcome"] == "created"
    assert replay.status_code == 200
    assert replay.json["completion"]["outcome"] == "already_completed"
    assert replay.json["completion"]["xp_awarded"] == 0
    assert completion_proof["validate"] == 1  # replay skips the proof work
    assert _counts(owner.id) == {
        "standalone": 1, "proofs": 1, "markers": 1, "xp": COMPLETION_XP}
    _completed(client, headers, owner.id)


# -- E: standalone after canonical completion -------------------------------
def test_e_standalone_after_completion_does_not_disturb_it(
        client, owner, as_mobile, workout_ref, completion_proof,
        standalone_dependencies):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    assert _complete(client, headers, reference, 0).json["completion"][
        "outcome"] == "created"
    before = _counts(owner.id)

    _create_standalone_after_completion(client, headers, owner.id)

    after = _counts(owner.id)
    assert after == {**before, "standalone": before["standalone"] + 1}
    assert after["proofs"] == 1 and after["markers"] == 1
    _completed(client, headers, owner.id)
    history = client.get(PUMP_CHECKS_PATH, headers=headers)
    assert history.status_code == 200
    assert len(history.json["pump_checks"]) == 1  # only the standalone row


def _create_standalone_after_completion(client, headers, user_id):
    from tests.test_mobile_pump_check_api import _image

    response = client.post(
        PUMP_CHECKS_PATH,
        headers={**headers, "Idempotency-Key": "standalone-after-0001"},
        data={
            "image": (_image(), "pump.jpg", "image/jpeg"),
            "body_region": "upper_body",
            "environment": "gym",
            "description": "",
            "captured_at": datetime.utcnow().replace(microsecond=0).isoformat()
            + "Z",
        },
    )
    assert response.status_code == 201
    row = PumpCheck.query.filter_by(
        user_id=user_id, idempotency_key="standalone-after-0001").one()
    assert row.date_key is None
    row.created_at = FIXED_DAY_UTC_MORNING + timedelta(hours=3)
    db.session.commit()


# -- F: previous-day standalone ---------------------------------------------
def test_f_previous_day_standalone_does_not_affect_today(
        client, owner, as_mobile, workout_ref, completion_proof):
    headers = as_mobile(owner)
    _add_standalone_row(
        owner.id, FIXED_DAY_UTC_MORNING - timedelta(days=1), "prevday-standalone-01")

    _not_completed(client, headers, owner.id)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    assert _complete(client, headers, reference, 0).json["completion"][
        "outcome"] == "created"
    assert _counts(owner.id) == {
        "standalone": 1, "proofs": 1, "markers": 1, "xp": COMPLETION_XP}


# -- G: account isolation ----------------------------------------------------
def test_g_other_accounts_rows_have_no_effect(
        client, owner, stranger, as_mobile, workout_ref, completion_proof):
    headers = as_mobile(owner)
    _add_standalone_row(stranger.id, FIXED_DAY_UTC_MORNING, "stranger-standalone-1")
    db.session.add(PumpCheck(user_id=stranger.id, valid=True, date_key=FIXED_DAY,
                             created_at=FIXED_DAY_UTC_MORNING))
    db.session.commit()

    _not_completed(client, headers, owner.id)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    assert _complete(client, headers, reference, 0).json["completion"][
        "outcome"] == "created"
    assert _counts(owner.id) == {
        "standalone": 0, "proofs": 1, "markers": 1, "xp": COMPLETION_XP}
    assert PumpCheck.query.filter_by(user_id=stranger.id).count() == 2


# -- The one definition every resolver shares --------------------------------
def test_completed_days_counts_only_the_canonical_claim(app, owner):
    _add_standalone_row(owner.id, FIXED_DAY_UTC_MORNING, "standalone-days-00001")
    yesterday = DAY - timedelta(days=1)
    db.session.add(PumpCheck(user_id=owner.id, valid=True,
                             date_key=yesterday.isoformat(),
                             created_at=FIXED_DAY_UTC_MORNING - timedelta(days=1)))
    db.session.commit()

    assert completed_days(owner.id, (yesterday, DAY)) == {yesterday}
    assert completed_days(owner.id, ()) == set()


def test_claim_is_read_from_date_key_not_created_at(app, owner):
    """The preflight reads the SAME column the unique constraint claims. A
    completion whose insert lands a moment after Istanbul midnight still
    belongs to the day it claimed, and cannot mark the next day completed."""
    just_after_midnight = datetime(2026, 7, 23, 21, 0, 1)  # 00:00:01 on 07-24 IST
    db.session.add(PumpCheck(user_id=owner.id, valid=True, date_key=FIXED_DAY,
                             created_at=just_after_midnight))
    db.session.commit()

    assert already_completed_today(owner.id, DAY) is True
    assert already_completed_today(owner.id, DAY + timedelta(days=1)) is False


def test_standalone_checks_are_not_training_sessions_for_generation(app, owner):
    with audit_clock(FIXED_NOW):
        _add_standalone_row(
            owner.id, FIXED_DAY_UTC_MORNING - timedelta(days=10), "standalone-gen-0001")
        history = build_performance_history(owner.id)
    assert history.weekly_training_sessions == [0, 0, 0, 0]
    assert history.adherence_score == 0.0

    with audit_clock(FIXED_NOW):
        db.session.add(PumpCheck(
            user_id=owner.id, valid=True,
            date_key=(DAY - timedelta(days=10)).isoformat(),
            created_at=FIXED_DAY_UTC_MORNING - timedelta(days=10)))
        db.session.commit()
        history = build_performance_history(owner.id)
    assert history.weekly_training_sessions == [0, 1, 0, 0]


def test_web_workout_status_ignores_standalone_rows(client, auth_user):
    """The browser's completion read shares the same resolver: a standalone row
    alone reports not-completed; the canonical claim reports completed."""
    _add_standalone_row(auth_user.id, FIXED_DAY_UTC_MORNING, "web-status-standalone1")
    with audit_clock(FIXED_NOW):
        response = client.get("/workout/status")
    assert response.status_code == 200
    assert response.json["completed"] is False

    db.session.add(PumpCheck(user_id=auth_user.id, valid=True, date_key=FIXED_DAY,
                             created_at=FIXED_DAY_UTC_MORNING))
    db.session.commit()
    with audit_clock(FIXED_NOW):
        response = client.get("/workout/status")
    assert response.json["completed"] is True
