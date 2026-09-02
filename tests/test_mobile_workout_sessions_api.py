"""Native workout-session write contracts (Mobile Training PR5).

These tests exercise the real HTTP surface against real persistence. They
assert literal contracts (status codes, payload keys, header values, canonical
side-effect rows) rather than mirroring the implementation, so a refactor that
changes behaviour fails here instead of silently shipping.
"""
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (
    WORKOUT_COMPLETION_MARKER,
    PumpCheck,
    TrainingPlan,
    User,
    WorkoutLog,
    WorkoutSession,
)
from app.services import mobile_auth, mobile_training
from app.timeutil import APP_TZ, app_today, audit_clock


SESSIONS_PATH = "/api/v1/training/workout-sessions"
CURRENT_PATH = SESSIONS_PATH + "/current"
# A Thursday: the plan fixture below puts the trainable day on that slot, so a
# fixed clock keeps "today's workout" deterministic across the whole file.
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
FIXED_DAY = "2026-07-23"
WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]
LINEAGE = "session-lineage-a"
VERSION = 4
EXERCISE_A = "ex_barbell_back_squat"
EXERCISE_B = "ex_barbell_deadlift"


# -- fixtures -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def sessions_enabled(app):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    return app


# The fixtures hand back a bare identity, not a live ORM instance: a request
# tears down the scoped session, so a User captured before one would be detached
# (and its expired columns unreadable) by the time a later step touches it.
@pytest.fixture
def owner(make_user):
    return SimpleNamespace(id=make_user("session-owner").id)


@pytest.fixture
def stranger(make_user):
    return SimpleNamespace(id=make_user("session-stranger").id)


@pytest.fixture
def as_mobile(monkeypatch):
    def _headers(user, **extra):
        # Reload by id inside the stub: a request tears down its own session, so
        # an object captured here would be detached by the NEXT request.
        owner_id = user.id

        def _authenticate(raw):
            row = db.session.get(User, owner_id)
            return mobile_auth.MobilePrincipal(
                row, SimpleNamespace(id=1), {"sub": row.cognito_sub})

        monkeypatch.setattr(mobile_auth, "authenticate_access", _authenticate)
        headers = {"Authorization": "Bearer opaque-access-credential"}
        headers.update(extra)
        return headers

    return _headers


def _exercise(exercise_id, name):
    return {
        "exercise_id": exercise_id,
        "isim": name,
        "set": 3,
        "tekrar": "8-10",
        "dinlenme": "90 sn",
        "not": "Controlled tempo",
    }


def _day(weekday, kind="dinlenme"):
    if kind == "dinlenme":
        return {
            "gun": weekday, "tip": kind, "odak": "Recovery",
            "sure_dk": 0, "tahmini_kalori": 0, "egzersizler": [],
        }
    return {
        "gun": weekday, "tip": kind, "odak": "Full body",
        "sure_dk": 45, "tahmini_kalori": 320,
        "egzersizler": [
            _exercise(EXERCISE_A, "Squat"),
            _exercise(EXERCISE_B, "Deadlift"),
        ],
    }


def _plan_document():
    days = [_day(name) for name in WEEKDAYS]
    days[3] = _day("Perşembe", "antrenman")
    return {"program": days}


@pytest.fixture
def plan(owner):
    row = TrainingPlan(
        user_id=owner.id,
        plan_data=json.dumps(_plan_document(), ensure_ascii=False),
        score=8.5,
        created_at=datetime(2026, 7, 1, 8, 30),
        lineage_id=LINEAGE,
        mutation_version=VERSION,
    )
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture
def workout_ref(app, plan):
    """Thursday's canonical workout reference for the owner."""
    return mobile_training.workout_ref(
        app.config["SECRET_KEY"], plan.user_id, LINEAGE, VERSION, 3)


def _post(client, headers, path=SESSIONS_PATH, **kwargs):
    with audit_clock(FIXED_NOW):
        return client.post(path, headers=headers, **kwargs)


def _start(client, headers, reference):
    return _post(client, headers, json={"workout_ref": reference})


def _snapshot(*, index=0, elapsed=60, reps=8, completed=True, exercise=EXERCISE_A):
    return {
        "current_exercise_index": index,
        "elapsed_seconds": elapsed,
        "exercises": [{
            "exercise_id": exercise,
            "sets": [{
                "index": 0, "completed": completed,
                "reps": reps, "weight_kg": 60.0,
            }],
        }],
    }


_DEFAULT = object()


def _checkpoint(client, headers, ref, revision, key, snapshot=_DEFAULT):
    with audit_clock(FIXED_NOW):
        return client.put(
            f"{SESSIONS_PATH}/{ref}/checkpoint",
            headers={**headers, "If-Match": str(revision), "Idempotency-Key": key},
            json={"checkpoint": _snapshot() if snapshot is _DEFAULT else snapshot},
        )


def _abandon(client, headers, ref, **body):
    return _post(client, headers, f"{SESSIONS_PATH}/{ref}/abandon", json=body)


def _complete(client, headers, ref, revision, key="complete-key-0001", **stub):
    with audit_clock(FIXED_NOW):
        return client.post(
            f"{SESSIONS_PATH}/{ref}/complete",
            headers={**headers, "If-Match": str(revision), "Idempotency-Key": key},
            data={"location_type": "gym", "description": "leg day"},
        )


@pytest.fixture
def completion_proof(monkeypatch):
    """Stub the completion gate's remote work (vision + object store).

    The route is the ONLY layer that performs network I/O for a completion, so
    stubbing exactly these two boundaries proves the rest of the path — the
    canonical transaction and all of its side effects — is exercised for real.
    """
    from app.blueprints import mobile_workout_sessions as routes

    calls = {"validate": 0, "upload": 0}

    def _validate_image(upload):
        return b"\x89PNG-bytes", "image/png", None

    def _validate_pump(image_bytes, location_type, description):
        calls["validate"] += 1
        return {"valid": True, "fallback": False}

    monkeypatch.setattr(
        routes, "validate_uploaded_pump_check_image", _validate_image)
    monkeypatch.setattr(routes, "validate_pump_check", _validate_pump)
    monkeypatch.setattr(routes.s3_helper, "is_enabled", lambda: False)
    return calls


def _session_row(user):
    return WorkoutSession.query.filter_by(user_id=user.id).one()


# -- 57. start ----------------------------------------------------------------

def test_start_publishes_the_exact_native_session_contract(
    client, owner, as_mobile, workout_ref
):
    response = _start(client, as_mobile(owner), workout_ref)

    assert response.status_code == 201, response.json
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.headers["Cache-Control"] == "no-store"
    session = response.json["session"]
    assert set(response.json) == {"session"}
    assert set(session) == {
        "session_ref", "workout_ref", "plan_lineage", "mutation_version",
        "status", "workout_date", "source", "relationship", "stale_reason",
        "resumable", "revision", "started_at", "last_activity_at",
        "checkpoint_at", "completed_at", "abandoned_at", "terminal_reason",
        "checkpoint",
    }
    assert session["workout_ref"] == workout_ref
    assert session["plan_lineage"] == LINEAGE
    assert session["mutation_version"] == VERSION
    assert session["status"] == "active"
    assert session["revision"] == 0
    assert session["checkpoint"] is None
    assert session["checkpoint_at"] is None
    assert session["completed_at"] is None
    assert session["abandoned_at"] is None
    assert session["resumable"] is True
    assert session["started_at"].endswith("Z")


def test_start_never_exposes_a_raw_database_identifier(
    client, owner, as_mobile, workout_ref
):
    response = _start(client, as_mobile(owner), workout_ref)

    row = _session_row(owner)
    body = response.get_data(as_text=True)
    assert response.json["session"]["session_ref"] == row.public_id
    assert f'"{row.id}"' not in body
    assert '"id"' not in body
    assert str(row.id) not in {
        str(value) for value in response.json["session"].values()
    }


def test_start_requires_bearer_and_rejects_browser_cookies(
    raw_client, client, owner, workout_ref
):
    anonymous = raw_client.post(
        SESSIONS_PATH, json={"workout_ref": workout_ref},
        headers={"Origin": "http://localhost"})
    assert anonymous.status_code == 401
    assert anonymous.json["error"]["code"] == "AUTH_SESSION_EXPIRED"
    assert "Location" not in anonymous.headers

    with client.session_transaction() as session:
        session["_user_id"] = str(owner.id)
        session["_fresh"] = True
    cookie_authorized = client.post(SESSIONS_PATH, json={"workout_ref": workout_ref})
    assert cookie_authorized.status_code == 401
    assert WorkoutSession.query.count() == 0


def test_start_is_absent_while_the_rollout_flag_is_off(
    app, client, owner, as_mobile, workout_ref
):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False

    response = _start(client, as_mobile(owner), workout_ref)

    assert response.status_code == 404
    assert response.json["error"]["code"] == "TRAINING_SESSION_NOT_FOUND"
    assert WorkoutSession.query.count() == 0


@pytest.mark.parametrize("reference", [
    None, "", 123, "short", "!" * 24, "A" * 25, {"workout_ref": "x"},
])
def test_start_rejects_a_malformed_workout_reference(
    client, owner, as_mobile, plan, reference
):
    response = _start(client, as_mobile(owner), reference)

    assert response.status_code in (400, 404)
    assert WorkoutSession.query.count() == 0


def test_start_rejects_a_reference_minted_for_a_superseded_plan_version(
    app, client, owner, as_mobile, plan
):
    stale = mobile_training.workout_ref(
        app.config["SECRET_KEY"], owner.id, LINEAGE, VERSION - 1, 3)

    response = _start(client, as_mobile(owner), stale)

    # Well-formed but no longer mintable from the current plan: a conflict the
    # client resolves by re-reading, NOT a malformed request.
    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_WORKOUT_NOT_STARTABLE"
    assert WorkoutSession.query.count() == 0


def test_start_refuses_a_rest_day_workout(app, client, owner, as_mobile, plan):
    rest_reference = mobile_training.workout_ref(
        app.config["SECRET_KEY"], owner.id, LINEAGE, VERSION, 0)

    response = _start(client, as_mobile(owner), rest_reference)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_WORKOUT_NOT_STARTABLE"
    assert response.headers["Session-Resolution"] == "reread"
    assert WorkoutSession.query.count() == 0


def test_a_reference_owned_by_somebody_else_cannot_start_a_session(
    client, stranger, as_mobile, workout_ref
):
    response = _start(client, as_mobile(stranger), workout_ref)

    # The reference is bound to its owner by HMAC, so it simply does not name a
    # workout of THIS caller's plan - and says nothing about the other owner.
    assert response.status_code in (404, 409)
    assert WorkoutSession.query.count() == 0


def test_duplicate_start_replays_the_same_session_instead_of_creating_a_second(
    client, owner, as_mobile, workout_ref
):
    first = _start(client, as_mobile(owner), workout_ref)
    second = _start(client, as_mobile(owner), workout_ref)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.headers["Idempotency-Replayed"] == "true"
    assert (second.json["session"]["session_ref"]
            == first.json["session"]["session_ref"])
    assert WorkoutSession.query.count() == 1


def test_starting_a_different_workout_while_one_is_active_is_a_conflict(
    app, client, owner, as_mobile, plan, workout_ref
):
    _start(client, as_mobile(owner), workout_ref)
    # A different slot's reference, resolved on a day it IS startable.
    friday = mobile_training.workout_ref(
        app.config["SECRET_KEY"], owner.id, LINEAGE, VERSION, 3)
    row = _session_row(owner)
    row.workout_ref = "other-workout-reference"
    db.session.commit()

    response = _start(client, as_mobile(owner), friday)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_ALREADY_ACTIVE"
    assert WorkoutSession.query.count() == 1


def test_a_browser_started_session_is_adopted_but_cannot_be_checkpointed(
    client, owner, as_mobile, workout_ref
):
    """The documented cross-surface edge, with a working way out.

    A session started through the BROWSER contract has no native workout
    reference, because that contract never had one. The native client adopts it
    (same day, slot and source — it is the same intended workout) and can read,
    resume and abandon it, but it cannot checkpoint against a canonical workout
    the session never recorded. Rather than guess an identity, the server says
    so and the client recovers by abandoning and starting natively.
    """
    from app.services.workout_session import start_session

    headers = as_mobile(owner)
    with audit_clock(FIXED_NOW):
        start_session(owner.id)
    assert _session_row(owner).workout_ref is None

    adopted = _start(client, headers, workout_ref)
    assert adopted.status_code == 200
    assert adopted.headers["Idempotency-Replayed"] == "true"
    assert adopted.json["session"]["workout_ref"] is None
    reference = adopted.json["session"]["session_ref"]
    assert WorkoutSession.query.count() == 1

    blocked = _checkpoint(client, headers, reference, 0, "adopt-key-000001")
    assert blocked.status_code == 409
    assert blocked.json["error"]["code"] == "TRAINING_SESSION_STALE"
    assert blocked.headers["Session-Resolution"] == "reread"

    # The documented recovery: abandon, then start natively.
    assert _abandon(client, headers, reference).status_code == 200
    restarted = _start(client, headers, workout_ref)
    assert restarted.status_code == 201
    assert restarted.json["session"]["workout_ref"] == workout_ref
    assert _checkpoint(
        client, headers, restarted.json["session"]["session_ref"], 0,
        "adopt-key-000002").status_code == 200


# -- 58. current / resume -----------------------------------------------------

def test_current_reports_a_deliberate_no_session_state(client, owner, as_mobile):
    with audit_clock(FIXED_NOW):
        response = client.get(CURRENT_PATH, headers=as_mobile(owner))

    assert response.status_code == 200
    assert response.json == {"session": None}


def test_current_recovers_the_durable_checkpoint_after_an_app_restart(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "restart-key-0001")

    with audit_clock(FIXED_NOW):
        response = client.get(CURRENT_PATH, headers=headers)

    session = response.json["session"]
    assert session["session_ref"] == reference
    assert session["revision"] == 1
    assert session["checkpoint"] == _snapshot()
    assert session["checkpoint_at"].endswith("Z")


def test_current_never_reports_a_terminal_session_as_current(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _abandon(client, headers, reference)

    with audit_clock(FIXED_NOW):
        response = client.get(CURRENT_PATH, headers=headers)

    assert response.json == {"session": None}


def test_current_is_scoped_to_its_owner(
    client, owner, stranger, as_mobile, workout_ref
):
    _start(client, as_mobile(owner), workout_ref)

    with audit_clock(FIXED_NOW):
        response = client.get(CURRENT_PATH, headers=as_mobile(stranger))

    assert response.json == {"session": None}


def test_resume_reattaches_to_the_same_session_without_creating_another(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "resume-key-0001")

    response = _post(client, headers, f"{SESSIONS_PATH}/{reference}/resume")

    assert response.status_code == 200
    assert response.json["session"]["session_ref"] == reference
    assert response.json["session"]["revision"] == 1
    assert response.json["session"]["checkpoint"] == _snapshot()
    assert WorkoutSession.query.count() == 1


def test_resume_honours_an_optional_if_match_guard(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    response = _post(
        client, {**headers, "If-Match": "7"}, f"{SESSIONS_PATH}/{reference}/resume")

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_REVISION_CONFLICT"


def test_another_owners_session_reference_resolves_as_not_found(
    client, owner, stranger, as_mobile, workout_ref
):
    reference = _start(
        client, as_mobile(owner), workout_ref).json["session"]["session_ref"]

    response = _post(
        client, as_mobile(stranger), f"{SESSIONS_PATH}/{reference}/resume")

    assert response.status_code == 404
    assert response.json["error"]["code"] == "TRAINING_SESSION_NOT_FOUND"


# -- 59. checkpoint -----------------------------------------------------------

def test_the_first_checkpoint_durably_advances_the_revision(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    response = _checkpoint(client, headers, reference, 0, "first-key-000001")

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json["session"]["revision"] == 1
    row = _session_row(owner)
    assert json.loads(row.checkpoint_data) == _snapshot()
    assert row.checkpoint_revision == 1


def test_a_subsequent_checkpoint_advances_from_the_declared_revision(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "step-key-00000001")

    second = _checkpoint(
        client, headers, reference, 1, "step-key-00000002",
        _snapshot(index=1, elapsed=180, reps=10, exercise=EXERCISE_B))

    assert second.status_code == 200
    assert second.json["session"]["revision"] == 2
    assert second.json["session"]["checkpoint"]["elapsed_seconds"] == 180


def test_checkpoint_requires_a_usable_if_match_revision(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    with audit_clock(FIXED_NOW):
        response = client.put(
            f"{SESSIONS_PATH}/{reference}/checkpoint",
            headers={**headers, "Idempotency-Key": "no-revision-0001"},
            json={"checkpoint": _snapshot()})

    assert response.status_code == 428
    assert response.json["error"]["code"] == "TRAINING_SESSION_INVALID_REVISION"
    assert response.headers["Session-Resolution"] == "reread"
    assert _session_row(owner).checkpoint_revision == 0


def test_checkpoint_requires_an_idempotency_key(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    with audit_clock(FIXED_NOW):
        response = client.put(
            f"{SESSIONS_PATH}/{reference}/checkpoint",
            headers={**headers, "If-Match": "0"},
            json={"checkpoint": _snapshot()})

    assert response.status_code == 400
    assert (response.json["error"]["code"]
            == "TRAINING_SESSION_INVALID_IDEMPOTENCY_KEY")
    assert _session_row(owner).checkpoint_revision == 0


def test_a_stale_revision_cannot_overwrite_newer_progress(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "stale-key-000001")

    response = _checkpoint(
        client, headers, reference, 0, "stale-key-000002", _snapshot(elapsed=999))

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_REVISION_CONFLICT"
    row = _session_row(owner)
    assert row.checkpoint_revision == 1
    assert json.loads(row.checkpoint_data)["elapsed_seconds"] == 60


def test_the_same_key_and_snapshot_replays_without_advancing_the_revision(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    first = _checkpoint(client, headers, reference, 0, "replay-key-00001")

    replay = _checkpoint(client, headers, reference, 0, "replay-key-00001")

    assert first.json["session"]["revision"] == 1
    assert replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json["session"]["revision"] == 1
    assert _session_row(owner).checkpoint_revision == 1


def test_a_reordered_but_equivalent_snapshot_replays_rather_than_conflicting(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    both = {
        "current_exercise_index": 0,
        "elapsed_seconds": 60,
        "exercises": [
            {"exercise_id": EXERCISE_A, "sets": [
                {"index": 0, "completed": True, "reps": 8, "weight_kg": 60.0},
                {"index": 1, "completed": False, "reps": None, "weight_kg": None},
            ]},
            {"exercise_id": EXERCISE_B, "sets": []},
        ],
    }
    reordered = {
        "current_exercise_index": 0,
        "elapsed_seconds": 60,
        "exercises": [
            {"exercise_id": EXERCISE_B, "sets": []},
            {"exercise_id": EXERCISE_A, "sets": [
                {"index": 1, "completed": False, "reps": None, "weight_kg": None},
                {"index": 0, "completed": True, "reps": 8, "weight_kg": 60.0},
            ]},
        ],
    }
    _checkpoint(client, headers, reference, 0, "order-key-000001", both)

    replay = _checkpoint(client, headers, reference, 0, "order-key-000001", reordered)

    assert replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert _session_row(owner).checkpoint_revision == 1


def test_the_same_key_with_a_different_snapshot_is_an_idempotency_conflict(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "conflict-key-0001")

    response = _checkpoint(
        client, headers, reference, 0, "conflict-key-0001", _snapshot(elapsed=61))

    assert response.status_code == 409
    assert (response.json["error"]["code"]
            == "TRAINING_SESSION_IDEMPOTENCY_CONFLICT")
    assert _session_row(owner).checkpoint_revision == 1


@pytest.mark.parametrize("snapshot", [
    None,
    "not-an-object",
    {},
    {"current_exercise_index": 0, "elapsed_seconds": 0},
    {"current_exercise_index": 0, "elapsed_seconds": 0, "exercises": [], "x": 1},
    {"current_exercise_index": -1, "elapsed_seconds": 0, "exercises": []},
    {"current_exercise_index": 9, "elapsed_seconds": 0, "exercises": []},
    {"current_exercise_index": 0, "elapsed_seconds": -1, "exercises": []},
    {"current_exercise_index": 0, "elapsed_seconds": 86_401, "exercises": []},
    {"current_exercise_index": 0, "elapsed_seconds": True, "exercises": []},
    {"current_exercise_index": 0, "elapsed_seconds": 0, "exercises": {}},
])
def test_checkpoint_rejects_a_snapshot_that_breaks_the_contract(
    client, owner, as_mobile, workout_ref, snapshot
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    response = _checkpoint(client, headers, reference, 0, "bounds-key-00001", snapshot)

    assert response.status_code == 400
    assert response.json["error"]["code"] == "TRAINING_SESSION_INVALID_REQUEST"
    assert _session_row(owner).checkpoint_revision == 0


def test_checkpoint_rejects_an_exercise_outside_the_canonical_workout(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    response = _checkpoint(
        client, headers, reference, 0, "member-key-000001",
        _snapshot(exercise="ex_not_in_this_workout"))

    assert response.status_code == 400
    assert _session_row(owner).checkpoint_revision == 0


def test_checkpoint_rejects_a_duplicated_exercise_or_set_identity(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    duplicate_exercise = {
        "current_exercise_index": 0, "elapsed_seconds": 10,
        "exercises": [
            {"exercise_id": EXERCISE_A, "sets": []},
            {"exercise_id": EXERCISE_A, "sets": []},
        ],
    }
    duplicate_set = {
        "current_exercise_index": 0, "elapsed_seconds": 10,
        "exercises": [{"exercise_id": EXERCISE_A, "sets": [
            {"index": 0, "completed": True, "reps": 8, "weight_kg": 60.0},
            {"index": 0, "completed": False, "reps": None, "weight_kg": None},
        ]}],
    }

    for snapshot in (duplicate_exercise, duplicate_set):
        response = _checkpoint(
            client, headers, reference, 0, "dupe-key-00000001", snapshot)
        assert response.status_code == 400
    assert _session_row(owner).checkpoint_revision == 0


def test_checkpoint_enforces_its_per_dimension_bounds(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    too_many_sets = {
        "current_exercise_index": 0, "elapsed_seconds": 10,
        "exercises": [{"exercise_id": EXERCISE_A, "sets": [
            {"index": i, "completed": False, "reps": None, "weight_kg": None}
            for i in range(21)
        ]}],
    }
    over_weight = _snapshot()
    over_weight["exercises"][0]["sets"][0]["weight_kg"] = 1000.1
    over_reps = _snapshot(reps=1001)

    for snapshot in (too_many_sets, over_weight, over_reps):
        response = _checkpoint(
            client, headers, reference, 0, "bound-key-00000001", snapshot)
        assert response.status_code == 400
    assert _session_row(owner).checkpoint_revision == 0


def test_a_terminal_session_accepts_no_further_checkpoint(
    client, owner, as_mobile, workout_ref, completion_proof
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _abandon(client, headers, reference)

    abandoned = _checkpoint(client, headers, reference, 0, "terminal-key-001")
    assert abandoned.status_code == 409
    assert abandoned.json["error"]["code"] == "TRAINING_SESSION_TERMINAL"

    second = _start(client, headers, workout_ref)
    second_ref = second.json["session"]["session_ref"]
    _complete(client, headers, second_ref, 0)

    completed = _checkpoint(client, headers, second_ref, 0, "terminal-key-002")
    assert completed.status_code == 409
    assert completed.json["error"]["code"] == "TRAINING_SESSION_TERMINAL"


def test_every_non_completion_command_works_with_providers_detonated(
    client, owner, as_mobile, workout_ref, monkeypatch
):
    """Running a workout is a deterministic database operation.

    Start, current, resume, checkpoint and abandon must never reach a Training
    or vision provider. Replacing both clients with objects that explode on ANY
    attribute access proves it against the real request path, not against an
    import list.
    """
    from app import extensions

    class _Detonator:
        def __getattr__(self, name):
            raise AssertionError(f"a session write reached provider method {name}")

    monkeypatch.setattr(extensions, "openai_client", _Detonator())
    monkeypatch.setattr(extensions, "bedrock_client", _Detonator())
    headers = as_mobile(owner)

    started = _start(client, headers, workout_ref)
    reference = started.json["session"]["session_ref"]
    with audit_clock(FIXED_NOW):
        current = client.get(CURRENT_PATH, headers=headers)
    resumed = _post(client, headers, f"{SESSIONS_PATH}/{reference}/resume")
    saved = _checkpoint(client, headers, reference, 0, "detonate-key-0001")
    ended = _abandon(client, headers, reference, reason="user_cancelled")

    assert started.status_code == 201
    assert current.status_code == 200
    assert resumed.status_code == 200
    assert saved.status_code == 200
    assert ended.status_code == 200


# -- 60. abandon --------------------------------------------------------------

def test_abandon_terminalizes_without_any_completion_side_effect(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "abandon-key-0001")
    before_xp = db.session.get(User, owner.id).rank_points or 0

    response = _abandon(client, headers, reference, reason="user_cancelled")

    assert response.status_code == 200
    session = response.json["session"]
    assert session["status"] == "abandoned"
    assert session["abandoned_at"].endswith("Z")
    assert session["completed_at"] is None
    # Progress is preserved, not deleted: the row stays as evidence.
    assert session["checkpoint"] == _snapshot()
    assert PumpCheck.query.count() == 0
    assert WorkoutLog.query.count() == 0
    assert (db.session.get(User, owner.id).rank_points or 0) == before_xp


def test_abandon_retry_replays_the_same_terminal_state(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    first = _abandon(client, headers, reference)

    replay = _abandon(client, headers, reference)

    assert first.headers["Idempotency-Replayed"] == "false"
    assert replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json["session"]["abandoned_at"] == first.json["session"]["abandoned_at"]


def test_abandon_rejects_a_bounded_reason_that_is_not_a_reason_code(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    response = _abandon(client, headers, reference, reason="I hurt my LEFT knee!")

    assert response.status_code == 400
    assert _session_row(owner).status == "active"


def test_a_completed_session_cannot_be_abandoned_afterwards(
    client, owner, as_mobile, workout_ref, completion_proof
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _complete(client, headers, reference, 0)

    response = _abandon(client, headers, reference)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_TERMINAL"
    assert _session_row(owner).status == "completed"
    assert PumpCheck.query.count() == 1


# -- 61. complete -------------------------------------------------------------

def test_complete_produces_exactly_one_set_of_canonical_side_effects(
    client, owner, as_mobile, workout_ref, completion_proof
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "complete-key-0001")

    response = _complete(client, headers, reference, 1)

    assert response.status_code == 200
    session = response.json["session"]
    assert session["status"] == "completed"
    assert session["completed_at"].endswith("Z")
    assert session["abandoned_at"] is None
    assert session["revision"] == 1
    completion = response.json["completion"]
    assert completion["outcome"] == "created"
    assert completion["xp_awarded"] > 0
    # Canonical evidence, owned by workout_completion — exactly once.
    assert PumpCheck.query.filter_by(user_id=owner.id).count() == 1
    assert PumpCheck.query.one().date_key == FIXED_DAY
    markers = WorkoutLog.query.filter_by(
        user_id=owner.id, exercise_name=WORKOUT_COMPLETION_MARKER).all()
    assert len(markers) == 1
    assert completion_proof["validate"] == 1


def test_complete_never_creates_a_pump_check_from_the_mobile_route_itself(
    client, owner, as_mobile, workout_ref, completion_proof
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    _complete(client, headers, reference, 0)

    check = PumpCheck.query.one()
    # The canonical service stamps the day key; a route-authored row would not.
    assert check.date_key == FIXED_DAY
    assert check.user_id == owner.id


def test_a_lost_completion_response_replays_with_no_duplicate_side_effects(
    client, owner, as_mobile, workout_ref, completion_proof
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    first = _complete(client, headers, reference, 0)
    xp_after_first = db.session.get(User, owner.id).rank_points or 0

    replay = _complete(client, headers, reference, 0)

    assert first.json["completion"]["outcome"] == "created"
    assert replay.status_code == 200
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json["completion"]["outcome"] == "already_completed"
    assert replay.json["completion"]["xp_awarded"] == 0
    assert PumpCheck.query.count() == 1
    assert WorkoutLog.query.filter_by(
        exercise_name=WORKOUT_COMPLETION_MARKER).count() == 1
    assert (db.session.get(User, owner.id).rank_points or 0) == xp_after_first
    # The replay skips the expensive proof entirely.
    assert completion_proof["validate"] == 1


def test_complete_refuses_a_revision_that_no_longer_holds(
    client, owner, as_mobile, workout_ref, completion_proof
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "revconf-key-00001")

    response = _complete(client, headers, reference, 0)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_REVISION_CONFLICT"
    assert response.headers["Session-Resolution"] == "reread"
    assert PumpCheck.query.count() == 0
    assert WorkoutLog.query.count() == 0
    assert _session_row(owner).status == "active"
    assert completion_proof["validate"] == 0


def test_complete_requires_an_if_match_revision(
    client, owner, as_mobile, workout_ref, completion_proof
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    with audit_clock(FIXED_NOW):
        response = client.post(
            f"{SESSIONS_PATH}/{reference}/complete",
            headers={**headers, "Idempotency-Key": "no-if-match-0001"},
            data={"location_type": "gym"})

    assert response.status_code == 428
    assert PumpCheck.query.count() == 0


def test_an_abandoned_session_can_never_be_completed(
    client, owner, as_mobile, workout_ref, completion_proof
):
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _abandon(client, headers, reference)

    response = _complete(client, headers, reference, 0)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_SESSION_TERMINAL"
    assert PumpCheck.query.count() == 0
    assert WorkoutLog.query.count() == 0
    assert completion_proof["validate"] == 0


def test_a_rejected_completion_proof_writes_nothing(
    client, owner, as_mobile, workout_ref, completion_proof, monkeypatch
):
    from app.blueprints import mobile_workout_sessions as routes

    monkeypatch.setattr(
        routes, "validate_pump_check",
        lambda *args: {"valid": False, "reason": "not a gym"})
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    response = _complete(client, headers, reference, 0)

    assert response.status_code == 422
    assert (response.json["error"]["code"]
            == "TRAINING_SESSION_COMPLETION_REJECTED")
    assert PumpCheck.query.count() == 0
    assert _session_row(owner).status == "active"


def test_an_unusable_completion_image_writes_nothing(
    client, owner, as_mobile, workout_ref, completion_proof, monkeypatch
):
    from app.blueprints import mobile_workout_sessions as routes

    monkeypatch.setattr(
        routes, "validate_uploaded_pump_check_image",
        lambda upload: (None, None, "invalid_image"))
    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    response = _complete(client, headers, reference, 0)

    assert response.status_code == 400
    assert PumpCheck.query.count() == 0
    assert _session_row(owner).status == "active"


def test_completion_never_leaks_an_internal_failure_to_the_client(
    client, owner, as_mobile, workout_ref, completion_proof, monkeypatch
):
    from app.services import mobile_workout_sessions as service

    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    def _explode(*args, **kwargs):
        raise RuntimeError("psycopg2.OperationalError: secret connection string")

    monkeypatch.setattr(service, "complete", _explode)
    response = _complete(client, headers, reference, 0)

    assert response.status_code == 503
    assert response.json["error"]["code"] == "TRAINING_SESSION_UNAVAILABLE"
    assert response.json["error"]["retryable"] is True
    assert response.headers["Retry-After"] == "15"
    assert "psycopg2" not in response.get_data(as_text=True)


def test_the_in_transaction_revision_precondition_refuses_on_its_own(
    client, owner, as_mobile, workout_ref, completion_proof
):
    """The route's cheap preflight is NOT the guard that matters.

    ``prepare_complete`` rejects a stale revision before the expensive proof
    work, which is a latency/cost win but is inherently racy: a checkpoint can
    land between that check and the transaction. The guard that actually holds is
    inside ``complete_workout``, evaluated while the session row is locked. This
    test bypasses the route entirely so ONLY that guard can produce the refusal.
    """
    from app.services.workout_completion import (
        CompleteWorkoutCommand,
        SessionCompletionConflict,
        complete_workout,
    )

    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _checkpoint(client, headers, reference, 0, "intx-key-0000001")
    row = _session_row(owner)
    assert row.checkpoint_revision == 1

    with audit_clock(FIXED_NOW):
        with pytest.raises(SessionCompletionConflict) as raised:
            complete_workout(CompleteWorkoutCommand(
                user_id=owner.id,
                today=date.fromisoformat(FIXED_DAY),
                session_id=row.id,
                expected_checkpoint_revision=0,
                valid=True,
                entry_path="test",
            ))

    assert raised.value.reason == "revision"
    assert PumpCheck.query.count() == 0
    assert WorkoutLog.query.count() == 0
    assert _session_row(owner).status == "active"


def test_a_completion_replay_does_not_even_attempt_a_second_insert(
    client, owner, as_mobile, workout_ref, completion_proof
):
    """Exact-once must not be bought with a failed INSERT round-trip.

    ``uq_pump_check_day`` is the authority and would classify a duplicate even
    without the read-only preflight, so a replay that "works" is not enough
    evidence: it must also be CHEAP. A replay that reaches the INSERT and relies
    on catching the IntegrityError burns a write and a rollback on every retry.
    """
    from sqlalchemy import event

    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]
    _complete(client, headers, reference, 0)
    # Fixture repair, not production behaviour: the canonical preflight windows on
    # ``created_at``, and this file freezes the CANONICAL day without freezing the
    # database default, so the row lands months outside its own Istanbul day. In
    # production those always agree; align them so the test measures the replay
    # path rather than the frozen clock.
    proof = PumpCheck.query.one()
    proof.created_at = FIXED_NOW.astimezone(timezone.utc).replace(tzinfo=None)
    db.session.commit()

    statements = []
    engine = db.engine

    def _record(conn, cursor, statement, params, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        replay = _complete(client, headers, reference, 0)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert replay.status_code == 200
    assert replay.json["completion"]["outcome"] == "already_completed"
    inserts = [s for s in statements if s.lstrip().upper().startswith("INSERT")]
    assert inserts == [], inserts


# -- Today convergence (section 51) -------------------------------------------

@pytest.fixture
def live_workout_ref(app, owner):
    """A plan whose training day is the REAL current weekday, on a live clock.

    The rest of this file freezes the canonical day, which is fine for the write
    contracts but makes a Today proof dishonest: the completion authority windows
    on ``PumpCheck.created_at``, so a frozen canonical day and a live column
    default land in different Istanbul days and the completion would look
    unregistered for a reason that cannot happen in production.
    """
    slot = app_today().weekday()
    document = _plan_document()
    document["program"] = [_day(name) for name in WEEKDAYS]
    document["program"][slot] = _day(WEEKDAYS[slot], "antrenman")
    db.session.add(TrainingPlan(
        user_id=owner.id,
        plan_data=json.dumps(document, ensure_ascii=False),
        score=8.5, created_at=datetime(2026, 7, 1, 8, 30),
        lineage_id="live-session-lineage", mutation_version=2))
    db.session.commit()
    return mobile_training.workout_ref(
        app.config["SECRET_KEY"], owner.id, "live-session-lineage", 2, slot)


def test_today_observes_the_session_lifecycle_through_the_canonical_authority(
    client, owner, as_mobile, live_workout_ref, completion_proof
):
    """The write surface must never need a second reader to be believed.

    /api/v1/today is a PROJECTION over the canonical workout-state resolver, and
    PR5 touches neither. Driving the lifecycle through the native writes and
    reading Today back is what proves the effects land in canonical state rather
    than in a private native store.
    """
    headers = as_mobile(owner)
    workout_ref = live_workout_ref

    def _today():
        response = client.get("/api/v1/today", headers=headers)
        assert response.status_code == 200
        return response.json["today"]

    before = _today()
    assert before["status"] == "scheduled_not_started"
    assert before["state"]["completed_today"] is False
    assert before["state"]["session"] is None
    assert before["workout"]["completed"] is False

    reference = client.post(
        SESSIONS_PATH, headers=headers,
        json={"workout_ref": workout_ref}).json["session"]["session_ref"]
    started = _today()
    # The session Today reports is THE session the native write created.
    assert started["state"]["session"]["public_id"] == reference
    assert started["state"]["session_state"] == "active_resumable"
    assert started["state"]["session"]["resumable"] is True
    assert started["state"]["action"] == "resume"

    client.put(
        f"{SESSIONS_PATH}/{reference}/checkpoint",
        headers={**headers, "If-Match": "0", "Idempotency-Key": "today-key-01"},
        json={"checkpoint": _snapshot()})
    saved = _today()
    # Durable progress is session state, not completion: Today must NOT move.
    assert saved["status"] == started["status"]
    assert saved["state"]["completed_today"] is False
    assert saved["state"]["session"]["public_id"] == reference

    client.post(
        f"{SESSIONS_PATH}/{reference}/complete",
        headers={**headers, "If-Match": "1", "Idempotency-Key": "today-key-02"},
        data={"location_type": "gym"})
    done = _today()
    assert done["status"] == "completed"
    assert done["state"]["completed_today"] is True
    assert done["state"]["execution_state"] == "completed"
    assert done["workout"]["completed"] is True


def test_today_reports_an_abandoned_session_as_not_completed(
    client, owner, as_mobile, live_workout_ref
):
    headers = as_mobile(owner)
    reference = client.post(
        SESSIONS_PATH, headers=headers,
        json={"workout_ref": live_workout_ref}).json["session"]["session_ref"]
    client.put(
        f"{SESSIONS_PATH}/{reference}/checkpoint",
        headers={**headers, "If-Match": "0", "Idempotency-Key": "today-key-03"},
        json={"checkpoint": _snapshot()})

    client.post(f"{SESSIONS_PATH}/{reference}/abandon", headers=headers,
                json={"reason": "user_cancelled"})

    today = client.get("/api/v1/today", headers=headers).json["today"]
    # No completion was earned, and the terminal session is reported honestly:
    # visible, but never resumable and never a completion.
    assert today["state"]["completed_today"] is False
    assert today["state"]["session_state"] == "abandoned"
    assert today["state"]["session"]["status"] == "abandoned"
    assert today["state"]["session"]["resumable"] is False
    assert today["state"]["action"] == "start"
    assert today["workout"]["completed"] is False


def test_the_native_completion_never_fans_a_pump_check_out_to_friends(
    client, owner, as_mobile, workout_ref, completion_proof
):
    from app.models import Message

    headers = as_mobile(owner)
    reference = _start(client, headers, workout_ref).json["session"]["session_ref"]

    _complete(client, headers, reference, 0)

    assert PumpCheck.query.one().visibility == "private"
    assert Message.query.count() == 0
