"""LP-13 P2: no new workout session after the day is already completed.

Physical qualification found that once the Istanbul day was completed the
terminal session vacated the active slot, so ``start_session`` saw "no active
session" and inserted a NEW ACTIVE row on the completed day (the server itself
later classified it ``lifecycle_inconsistent``). The fix is one service-layer
guard in ``workout_session.start_session``:

    active lookup (replay / conflict, unchanged)
    -> canonical completed-today claim (refuse as INVALID_TRANSITION)
    -> insert

These tests pin that order at the service, the native API and the browser API,
prove the guard reads the ONE canonical completion authority
(``PumpCheck.date_key`` via ``already_completed_today``) rather than session
status, and prove a refusal mutates nothing. The real multi-connection races
live in ``test_mobile_workout_sessions_pg.py`` (CI ``PostgreSQL concurrency``).
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import (
    WORKOUT_COMPLETION_MARKER,
    WORKOUT_SESSION_ABANDONED,
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
    Activity,
    PumpCheck,
    TrainingPlan,
    User,
    WorkoutLog,
    WorkoutSession,
)
from app.services import mobile_auth, mobile_training
from app.services.workout_session import (
    SessionOutcome,
    abandon_session,
    complete_session,
    start_session,
)
from app.services.workout_session import models as sm
from app.timeutil import APP_TZ, audit_clock

SESSIONS_PATH = "/api/v1/training/workout-sessions"
# A Thursday; the plan below trains on Thursday only.
FIXED_NOW = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
FIXED_DAY = "2026-07-23"
WEEKDAYS = [
    "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar",
]
LINEAGE = "lp13-no-start-lineage"
VERSION = 2
OWNER = "lp13-owner"


# -- fixtures -----------------------------------------------------------------

@pytest.fixture(autouse=True)
def sessions_enabled(app):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    return app


@pytest.fixture
def owner(make_user):
    return SimpleNamespace(id=make_user(OWNER).id)


def _plan_document():
    days = [
        {"gun": name, "tip": "dinlenme", "odak": "Recovery", "sure_dk": 0,
         "tahmini_kalori": 0, "egzersizler": []}
        for name in WEEKDAYS
    ]
    days[3] = {
        "gun": "Perşembe", "tip": "antrenman", "odak": "Full body",
        "sure_dk": 45, "tahmini_kalori": 320,
        "egzersizler": [{
            "exercise_id": "ex_barbell_back_squat", "isim": "Squat", "set": 3,
            "tekrar": "8-10", "dinlenme": "90 sn", "not": "",
        }],
    }
    return {"program": days}


@pytest.fixture
def plan(owner):
    row = TrainingPlan(
        user_id=owner.id,
        plan_data=json.dumps(_plan_document(), ensure_ascii=False),
        score=8.0, created_at=datetime(2026, 7, 1, 8, 30),
        lineage_id=LINEAGE, mutation_version=VERSION)
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture
def workout_ref(app, plan):
    return mobile_training.workout_ref(
        app.config["SECRET_KEY"], plan.user_id, LINEAGE, VERSION, 3)


@pytest.fixture
def as_mobile(monkeypatch):
    def _headers(user):
        owner_id = user.id

        def _authenticate(raw):
            row = db.session.get(User, owner_id)
            return mobile_auth.MobilePrincipal(
                row, SimpleNamespace(id=1), {"sub": row.cognito_sub})

        monkeypatch.setattr(mobile_auth, "authenticate_access", _authenticate)
        return {"Authorization": "Bearer opaque-access-credential"}

    return _headers


@pytest.fixture
def native_proof(monkeypatch):
    """Stub only the native completion route's remote work (vision + S3)."""
    from app.blueprints import mobile_workout_sessions as routes

    monkeypatch.setattr(
        routes, "validate_uploaded_pump_check_image",
        lambda upload: (b"\xff\xd8jpeg", "image/jpeg", None))
    monkeypatch.setattr(
        routes, "validate_pump_check",
        lambda *a, **k: {"valid": True, "fallback": False})
    monkeypatch.setattr(routes.s3_helper, "is_enabled", lambda: False)


@pytest.fixture
def browser_proof(monkeypatch):
    """Stub only the browser completion route's remote work."""
    from app.blueprints import training as routes

    monkeypatch.setattr(
        routes, "validate_pump_check_image",
        lambda *a, **k: (b"jpeg", "image/jpeg", None))
    monkeypatch.setattr(
        routes, "validate_pump_check",
        lambda *a, **k: {"valid": True, "fallback": False})


def _native_start(client, headers, reference):
    with audit_clock(FIXED_NOW):
        return client.post(
            SESSIONS_PATH, headers=headers, json={"workout_ref": reference})


def _native_complete(client, headers, ref, revision=0):
    with audit_clock(FIXED_NOW):
        return client.post(
            f"{SESSIONS_PATH}/{ref}/complete",
            headers={**headers, "If-Match": str(revision),
                     "Idempotency-Key": "lp13-complete-0001"},
            data={"location_type": "gym", "description": "leg day"})


def _browser_start(client):
    with audit_clock(FIXED_NOW):
        return client.post("/workout/session/start", json={})


def _service_start(user_id):
    with audit_clock(FIXED_NOW):
        return start_session(user_id)


def _service_complete(user_id, public_id):
    with audit_clock(FIXED_NOW):
        return complete_session(
            user_id, public_id, location_type="salon", description="done",
            base_xp=10, photo_bonus=25)


def _claim_today(user_id):
    """The canonical completion claim with no session at all (session-less
    completion: the AI-coach gym photo, or the legacy browser route)."""
    db.session.add(PumpCheck(user_id=user_id, valid=True, date_key=FIXED_DAY))
    db.session.commit()


def _sessions(user_id):
    return WorkoutSession.query.filter_by(user_id=user_id).order_by(
        WorkoutSession.id).all()


def _world(user_id):
    """Every persisted fact a refused start must leave untouched."""
    db.session.expire_all()
    user = db.session.get(User, user_id)
    return {
        "sessions": [
            (s.public_id, s.status, s.workout_date, s.version,
             s.completed_at, s.abandoned_at, s.checkpoint_revision,
             s.checkpoint_data, s.last_activity_at)
            for s in _sessions(user_id)
        ],
        "pump_checks": sorted(
            (p.id, p.date_key, p.valid)
            for p in PumpCheck.query.filter_by(user_id=user_id)),
        "workout_logs": sorted(
            (w.id, w.exercise_name)
            for w in WorkoutLog.query.filter_by(user_id=user_id)),
        "activities": sorted(
            (a.id, a.activity_type)
            for a in Activity.query.filter_by(user_id=user_id)),
        "xp": (user.rank_points, user.weekly_xp),
        "streak": user.streak_count,
        "plans": sorted(
            (p.id, p.plan_data, p.lineage_id, p.mutation_version)
            for p in TrainingPlan.query.filter_by(user_id=user_id)),
    }


# -- 1 / 7 / 11. service: start after completion ------------------------------

def test_service_start_after_session_completion_is_refused_and_writes_nothing(
    app, owner, plan
):
    first = _service_start(owner.id)
    assert first.outcome is SessionOutcome.CREATED
    assert _service_complete(
        owner.id, first.session.public_id).outcome is SessionOutcome.COMPLETED
    before = _world(owner.id)

    refused = _service_start(owner.id)

    assert refused.outcome is SessionOutcome.INVALID_TRANSITION
    assert refused.session is None
    assert _world(owner.id) == before
    rows = _sessions(owner.id)
    assert [r.status for r in rows] == [WORKOUT_SESSION_COMPLETED]
    assert WorkoutSession.query.filter_by(
        user_id=owner.id, status=WORKOUT_SESSION_ACTIVE).count() == 0


def test_service_refusal_is_deterministic_across_repeats(app, owner, plan):
    started = _service_start(owner.id)
    _service_complete(owner.id, started.session.public_id)

    outcomes = {_service_start(owner.id).outcome for _ in range(3)}

    assert outcomes == {SessionOutcome.INVALID_TRANSITION}
    assert len(_sessions(owner.id)) == 1


# -- 10. completion authority is the canonical claim, not session status ------

def test_session_less_completion_claim_blocks_start(app, owner, plan):
    """No session row exists at all; the day is completed only by the canonical
    claim. A session-status-only guard would let this start through."""
    _claim_today(owner.id)
    before = _world(owner.id)

    refused = _service_start(owner.id)

    assert refused.outcome is SessionOutcome.INVALID_TRANSITION
    assert _sessions(owner.id) == []
    assert _world(owner.id) == before


def test_standalone_pump_check_is_not_completion_and_does_not_block_start(
    app, owner, plan
):
    """A standalone Pump Check (``date_key IS NULL``) is not completion evidence
    under the canonical definition, so it must not block a start."""
    db.session.add(PumpCheck(user_id=owner.id, valid=True, date_key=None))
    db.session.commit()

    assert _service_start(owner.id).outcome is SessionOutcome.CREATED


def test_previous_day_claim_does_not_block_today(app, owner, plan):
    db.session.add(PumpCheck(user_id=owner.id, valid=True, date_key="2026-07-22"))
    db.session.commit()

    assert _service_start(owner.id).outcome is SessionOutcome.CREATED


def test_guard_defers_to_the_canonical_completion_helper(
    app, owner, plan, monkeypatch
):
    """The guard asks the ONE shared definition (``already_completed_today`` via
    ``queries.completed_today``) with the start's own Istanbul day."""
    from app.services.workout_session import service as wservice

    asked = []

    def _spy(user_id, day):
        asked.append((user_id, day.isoformat()))
        return True

    monkeypatch.setattr(wservice, "completed_today", _spy)

    assert _service_start(owner.id).outcome is SessionOutcome.INVALID_TRANSITION
    assert asked == [(owner.id, FIXED_DAY)]
    assert _sessions(owner.id) == []


# -- 6. abandon -> restart stays valid on a not-completed day -----------------

def test_abandon_then_restart_still_creates_one_new_session(app, owner, plan):
    first = _service_start(owner.id)
    with audit_clock(FIXED_NOW):
        abandon_session(owner.id, first.session.public_id)

    again = _service_start(owner.id)

    assert again.outcome is SessionOutcome.CREATED
    assert again.session.public_id != first.session.public_id
    statuses = [r.status for r in _sessions(owner.id)]
    assert statuses == [WORKOUT_SESSION_ABANDONED, WORKOUT_SESSION_ACTIVE]


def test_abandon_then_completed_day_still_refuses_restart(app, owner, plan):
    """Abandoning is not completing; but once the day is canonically completed
    (here session-less), an abandoned history does not reopen the day."""
    first = _service_start(owner.id)
    with audit_clock(FIXED_NOW):
        abandon_session(owner.id, first.session.public_id)
    _claim_today(owner.id)

    assert _service_start(owner.id).outcome is SessionOutcome.INVALID_TRANSITION
    assert [r.status for r in _sessions(owner.id)] == [WORKOUT_SESSION_ABANDONED]


# -- 4 / 5. active replay and conflict are evaluated BEFORE the guard ---------

def test_active_replay_is_unchanged_on_a_not_completed_day(app, owner, plan):
    first = _service_start(owner.id)
    second = _service_start(owner.id)

    assert second.outcome is SessionOutcome.EXISTING_ACTIVE
    assert second.session.public_id == first.session.public_id
    assert len(_sessions(owner.id)) == 1


def test_lifecycle_inconsistent_active_session_is_still_replayed(
    app, owner, plan
):
    """An ACTIVE session on a day completed elsewhere (session-less completion,
    or a row left by the pre-fix bug) is still REPLAYED, so the client can see
    and resolve it. A guard placed before the active lookup would hide it."""
    first = _service_start(owner.id)
    _claim_today(owner.id)

    replay = _service_start(owner.id)

    assert replay.outcome is SessionOutcome.EXISTING_ACTIVE
    assert replay.session.public_id == first.session.public_id
    assert replay.session.stale_reason == sm.STALE_LIFECYCLE_INCONSISTENT
    assert replay.session.resumable is False
    assert len(_sessions(owner.id)) == 1


def test_different_active_session_is_still_a_conflict_on_completed_day(
    app, owner, plan
):
    first = _service_start(owner.id)
    row = _sessions(owner.id)[0]
    row.weekday_slot = "Pazartesi"
    db.session.commit()
    _claim_today(owner.id)

    conflict = _service_start(owner.id)

    assert conflict.outcome is SessionOutcome.CONFLICT
    assert conflict.session.public_id == first.session.public_id
    assert len(_sessions(owner.id)) == 1


# -- 2. native API ------------------------------------------------------------

def test_native_start_after_completion_is_409_not_startable(
    client, owner, as_mobile, workout_ref, native_proof
):
    headers = as_mobile(owner)
    started = _native_start(client, headers, workout_ref)
    assert started.status_code == 201
    ref = started.json["session"]["session_ref"]
    assert _native_complete(client, headers, ref).status_code == 200
    before = _world(owner.id)

    response = _native_start(client, headers, workout_ref)

    assert response.status_code == 409, response.json
    assert response.json["error"]["code"] == "TRAINING_WORKOUT_NOT_STARTABLE"
    assert response.json["error"]["retryable"] is False
    assert response.headers["Session-Resolution"] == "reread"
    assert _world(owner.id) == before
    assert WorkoutSession.query.filter_by(
        user_id=owner.id, status=WORKOUT_SESSION_ACTIVE).count() == 0
    assert len(_sessions(owner.id)) == 1


def test_native_start_after_session_less_completion_is_409(
    client, owner, as_mobile, workout_ref
):
    _claim_today(owner.id)

    response = _native_start(client, as_mobile(owner), workout_ref)

    assert response.status_code == 409
    assert response.json["error"]["code"] == "TRAINING_WORKOUT_NOT_STARTABLE"
    assert response.headers["Session-Resolution"] == "reread"
    assert _sessions(owner.id) == []


def test_native_replay_and_conflict_semantics_unchanged(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    first = _native_start(client, headers, workout_ref)
    replay = _native_start(client, headers, workout_ref)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert (replay.json["session"]["session_ref"]
            == first.json["session"]["session_ref"])

    row = _sessions(owner.id)[0]
    row.workout_ref = "some-other-workout-reference"
    db.session.commit()
    _claim_today(owner.id)
    conflict = _native_start(client, headers, workout_ref)
    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "TRAINING_SESSION_ALREADY_ACTIVE"
    assert len(_sessions(owner.id)) == 1


def test_native_abandon_then_restart_is_201(
    client, owner, as_mobile, workout_ref
):
    headers = as_mobile(owner)
    ref = _native_start(client, headers, workout_ref).json["session"]["session_ref"]
    with audit_clock(FIXED_NOW):
        abandoned = client.post(
            f"{SESSIONS_PATH}/{ref}/abandon", headers=headers, json={})
    assert abandoned.status_code == 200

    again = _native_start(client, headers, workout_ref)

    assert again.status_code == 201
    assert again.json["session"]["session_ref"] != ref
    assert len(_sessions(owner.id)) == 2


# -- 3. browser API -----------------------------------------------------------

def test_browser_start_after_completion_is_refused(
    client, owner, plan, login, browser_proof
):
    assert login(OWNER).status_code == 200
    started = _browser_start(client)
    assert started.status_code == 201
    public_id = started.get_json()["session"]["public_id"]
    with audit_clock(FIXED_NOW):
        completed = client.post("/workout/complete", json={
            "image": "x", "location_type": "salon", "session_id": public_id,
            "expected_checkpoint_revision": 0,
        })
    assert completed.status_code == 200
    before = _world(owner.id)

    refused = _browser_start(client)

    assert refused.status_code == 409
    body = refused.get_json()
    assert body["outcome"] == "invalid_transition"
    assert body["code"] == "invalid_transition"
    assert body["session"] is None
    assert _world(owner.id) == before
    assert [r.status for r in _sessions(owner.id)] == [WORKOUT_SESSION_COMPLETED]


def test_browser_start_after_legacy_session_less_completion_is_refused(
    client, owner, plan, login, browser_proof
):
    """The legacy browser completion carries no session at all; the day is
    still canonically completed and the browser start must be refused."""
    assert login(OWNER).status_code == 200
    with audit_clock(FIXED_NOW):
        completed = client.post("/workout/complete", json={
            "image": "x", "location_type": "salon"})
    assert completed.status_code == 200
    assert PumpCheck.query.filter_by(
        user_id=owner.id, date_key=FIXED_DAY).count() == 1

    refused = _browser_start(client)

    assert refused.status_code == 409
    assert refused.get_json()["outcome"] == "invalid_transition"
    assert _sessions(owner.id) == []


def test_browser_abandon_then_restart_is_201(client, owner, plan, login):
    assert login(OWNER).status_code == 200
    public_id = _browser_start(client).get_json()["session"]["public_id"]
    with audit_clock(FIXED_NOW):
        client.post(f"/workout/session/{public_id}/abandon", json={})

    again = _browser_start(client)

    assert again.status_code == 201
    assert again.get_json()["session"]["public_id"] != public_id


def test_browser_replay_unchanged(client, owner, plan, login):
    assert login(OWNER).status_code == 200
    first = _browser_start(client)
    second = _browser_start(client)

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.get_json()["outcome"] == "existing_active"
    assert (second.get_json()["session"]["public_id"]
            == first.get_json()["session"]["public_id"])


# -- cross-transport: one guard, both surfaces --------------------------------

def test_native_completion_blocks_browser_start_and_vice_versa(
    client, owner, as_mobile, workout_ref, native_proof, login
):
    headers = as_mobile(owner)
    ref = _native_start(client, headers, workout_ref).json["session"]["session_ref"]
    assert _native_complete(client, headers, ref).status_code == 200

    assert login(OWNER).status_code == 200
    browser = _browser_start(client)
    assert browser.status_code == 409
    assert browser.get_json()["outcome"] == "invalid_transition"

    native = _native_start(client, headers, workout_ref)
    assert native.status_code == 409
    assert native.json["error"]["code"] == "TRAINING_WORKOUT_NOT_STARTABLE"
    assert len(_sessions(owner.id)) == 1
    assert PumpCheck.query.filter_by(user_id=owner.id).count() == 1
    assert WorkoutLog.query.filter_by(
        user_id=owner.id, exercise_name=WORKOUT_COMPLETION_MARKER).count() == 1


# -- 9. the (owner, day) boundary shared with the completion claim ------------
#
# The guard above is read before the insert; a session-less completion could
# commit the claim in between. Both writers therefore take ONE boundary,
# ``workout_completion.lock_completion_day``, and the start re-reads the claim
# under it. These pin the call order hermetically; the real interleavings are
# PostgreSQL-proven in ``test_mobile_workout_sessions_pg.py``.

def test_day_lock_key_is_stable_and_separates_owners_and_days():
    from datetime import date

    from app.services.workout_completion.queries import completion_day_lock_key

    day = date(2026, 7, 23)
    key = completion_day_lock_key(7, day)
    assert key == completion_day_lock_key(7, day)
    assert -(2 ** 63) <= key < 2 ** 63
    assert key != completion_day_lock_key(8, day)
    assert key != completion_day_lock_key(7, date(2026, 7, 24))


def test_day_lock_is_a_no_op_on_sqlite(app, owner):
    from sqlalchemy import event

    from app.services.workout_completion import lock_completion_day

    statements = []

    def _record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", _record)
    try:
        lock_completion_day(owner.id, datetime(2026, 7, 23).date())
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    assert statements == []


def test_start_rechecks_the_claim_under_the_day_lock(
    app, owner, plan, monkeypatch
):
    """A claim that appears between the cheap guard and the insert (what a
    racing session-less completion produces) is seen by the re-check UNDER the
    lock: refused, nothing written."""
    from app.services.workout_session import service as wservice

    calls = []
    answers = iter([False, True])

    def _guard(user_id, day):
        calls.append(("check", day.isoformat()))
        return next(answers)

    def _lock(user_id, day):
        calls.append(("lock", day.isoformat()))

    def _insert(*_args, **_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("inserted on a completed day")

    monkeypatch.setattr(wservice, "completed_today", _guard)
    monkeypatch.setattr(wservice, "lock_completion_day", _lock)
    monkeypatch.setattr(wservice, "insert_active_session", _insert)

    assert _service_start(owner.id).outcome is SessionOutcome.INVALID_TRANSITION
    assert calls == [
        ("check", FIXED_DAY), ("lock", FIXED_DAY), ("check", FIXED_DAY)]
    assert _sessions(owner.id) == []


def test_start_inserts_only_after_taking_the_day_lock(
    app, owner, plan, monkeypatch
):
    from app.services.workout_session import service as wservice

    calls = []
    real_insert = wservice.insert_active_session

    def _lock(user_id, day):
        calls.append(("lock", user_id, day.isoformat()))

    def _insert(*args, **kwargs):
        calls.append(("insert",))
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(wservice, "lock_completion_day", _lock)
    monkeypatch.setattr(wservice, "insert_active_session", _insert)

    assert _service_start(owner.id).outcome is SessionOutcome.CREATED
    assert calls == [("lock", owner.id, FIXED_DAY), ("insert",)]


def test_replay_and_conflict_never_take_the_day_lock(
    app, owner, plan, monkeypatch
):
    from app.services.workout_session import service as wservice

    first = _service_start(owner.id)
    assert first.outcome is SessionOutcome.CREATED
    taken = []
    monkeypatch.setattr(
        wservice, "lock_completion_day", lambda *a: taken.append(a))
    assert _service_start(owner.id).outcome is SessionOutcome.EXISTING_ACTIVE
    assert taken == []


def test_completion_writes_the_claim_only_under_the_same_day_lock(
    app, owner, plan, monkeypatch
):
    """The session-less completion (browser legacy / AI-coach) takes the SAME
    (owner, day) lock before its claim exists, and a replay takes none."""
    from app.services.workout_completion import (
        CompleteWorkoutCommand,
        complete_workout,
    )
    from app.services.workout_completion import service as cservice

    taken = []
    real_lock = cservice.lock_completion_day

    def _lock(user_id, day):
        claims = PumpCheck.query.filter(
            PumpCheck.user_id == user_id, PumpCheck.date_key.isnot(None)).count()
        taken.append((user_id, day.isoformat(), claims))
        real_lock(user_id, day)

    monkeypatch.setattr(cservice, "lock_completion_day", _lock)

    def _complete():
        return complete_workout(CompleteWorkoutCommand(
            user_id=owner.id, today=datetime(2026, 7, 23).date(),
            session_id=None, location_type="gym", description="lp13",
            valid=True, fallback=False, base_xp=10, photo_bonus=25,
            activity_text="lp13", entry_path="test"))

    assert _complete().outcome.value == "created"
    assert taken == [(owner.id, FIXED_DAY, 0)]
    assert _complete().outcome.value == "already_completed"
    assert len(taken) == 1  # the preflight replay is lock-free
