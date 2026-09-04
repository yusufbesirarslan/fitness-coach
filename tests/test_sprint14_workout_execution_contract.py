"""Sprint 14 PR2 — one canonical workout execution contract, two server transports.

`#276` gave the NATIVE surface durable, revision-gated workout execution. The
browser half of the same feature flag did not move: its checkpoint route wrote
`last_activity_at` and nothing else, and its completion declared no revision.
One table, one identity space, two contracts that disagreed — so a browser
completion could silently discard progress a phone had committed, and a browser
workout still lost every set on reload.

This module proves the convergence at the level it actually has to hold: the
persisted row. Almost every assertion below reads the database AFTER the
request, because "the response said it refused" and "nothing was written" are
different claims and only the second one matters.

Scope note: PR2 owns the BACKEND contract. `static/training.js` still does not
send it — PR3 owns the browser client — so nothing here asserts UI behaviour.
"""
import json
from datetime import datetime, timedelta

import pytest

from app.extensions import db
from app.models import (
    WORKOUT_SESSION_ABANDONED,
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
    Activity,
    PumpCheck,
    TrainingPlan,
    UserQuestProgress,
    WorkoutLog,
    WorkoutSession,
)
from app.services.training_generation.response_validator import WEEKDAYS
from app.services.workout_session import (
    MAX_EXERCISES,
    MAX_SETS_PER_EXERCISE,
    MAX_SNAPSHOT_BYTES,
    SessionView,
    build_session_view,
    get_current_session,
    get_owned_session,
    read_session_for_state,
    record_checkpoint,
)
from app.timeutil import app_today
from app.blueprints import training as training_bp


# Canonical catalog identities — a checkpoint may only name exercises that are
# actually in the session's workout, so the plan has to carry real ones.
SQUAT = "ex_barbell_back_squat"
BENCH = "ex_barbell_bench_press"
ROW = "ex_barbell_row"


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sessions_on(app):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    yield app
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False


@pytest.fixture
def proof_accepted(monkeypatch):
    """Make the completion proof pass without a provider or an object store.

    Completion's expensive tail is deliberately OUTSIDE the mutation; these
    stubs stand in for it so the tests below can be about revision authority.
    ``calls`` records whether the expensive work ran at all, which is itself an
    assertion in the stale-completion tests.
    """
    calls = {"image": 0, "vision": 0}

    def _image(*args, **kwargs):
        calls["image"] += 1
        return b"jpeg", "image/jpeg", None

    def _vision(*args, **kwargs):
        calls["vision"] += 1
        return {"valid": True, "fallback": False}

    monkeypatch.setattr(training_bp, "validate_pump_check_image", _image)
    monkeypatch.setattr(training_bp, "validate_pump_check", _vision)
    return calls


def save_workout_plan(user_id, exercise_ids=(SQUAT, BENCH), names=None):
    """Persist a plan whose TODAY (Istanbul) weekday is a real workout."""
    today_weekday = WEEKDAYS[app_today().weekday()]
    labels = names or ["Squat", "Bench", "Row", "Press", "Curl"]
    program = []
    for name in WEEKDAYS:
        if name == today_weekday:
            program.append({"gun": name, "tip": "antrenman", "egzersizler": [
                {"isim": labels[index % len(labels)], "exercise_id": exercise_id}
                for index, exercise_id in enumerate(exercise_ids)
            ]})
        else:
            program.append({"gun": name, "tip": "dinlenme", "egzersizler": []})
    plan = TrainingPlan(user_id=user_id, score=5, plan_data=json.dumps(
        {"program": program, "haftalik_ozet": {}}, ensure_ascii=False))
    db.session.add(plan)
    db.session.commit()
    return plan


def snapshot(exercise_id=SQUAT, reps=8, sets=1, elapsed=120, index=0):
    return {
        "current_exercise_index": index,
        "elapsed_seconds": elapsed,
        "exercises": [{"exercise_id": exercise_id, "sets": [
            {"index": i, "completed": True, "reps": reps, "weight_kg": 60.0}
            for i in range(sets)
        ]}],
    }


def headers(revision, key="browser-key-000001"):
    return {"If-Match": str(revision), "Idempotency-Key": key}


def start_session_over_http(client):
    started = client.post("/workout/session/start", json={})
    assert started.status_code == 201, started.get_json()
    return started.get_json()["session"]["public_id"]


def checkpoint_over_http(client, public_id, revision, body=None, key="browser-key-000001"):
    return client.post(
        f"/workout/session/{public_id}/checkpoint",
        headers=headers(revision, key),
        json={"checkpoint": body if body is not None else snapshot()})


def row_for(user_id):
    return WorkoutSession.query.filter_by(user_id=user_id).one()


def durable_state(row):
    """Every column the checkpoint authority is allowed to move, as one tuple."""
    return (
        row.checkpoint_revision, row.checkpoint_data, row.checkpoint_fingerprint,
        row.checkpoint_idempotency_key, row.checkpoint_at, row.status,
    )


# ═════════════════════════════════════════════════════════════════════════════
# §47 — characterization: what #276 already guaranteed, and what PR2 changed
# ═════════════════════════════════════════════════════════════════════════════

def test_the_legacy_heartbeat_is_no_longer_any_transport_progress_authority():
    """The pre-PR2 browser contract, pinned as the thing that was replaced.

    ``checkpoint_session`` still exists as a liveness heartbeat and still writes
    only ``last_activity_at`` — that part is characterization. What PR2 changed
    is that NO transport routes durable progress through it any more.
    """
    import inspect

    from app.services.workout_session import service as session_service

    source = inspect.getsource(session_service.checkpoint_session)
    # Still only a heartbeat: it cannot write a snapshot or move a revision.
    assert "heartbeat(" in source
    for forbidden in ("checkpoint_data", "checkpoint_revision", "advance_checkpoint"):
        assert forbidden not in source

    web = inspect.getsource(training_bp.workout_session_checkpoint)
    assert "record_checkpoint(" in web
    assert "checkpoint_session(" not in web


def test_both_server_transports_reach_one_checkpoint_authority():
    """Neither transport may own an execution state machine (brief §5).

    Structural, and deliberately so: a behaviour test proves today's code is
    right, this proves the next change cannot quietly give one surface its own
    durable write back.
    """
    import ast
    from pathlib import Path

    callers = []
    for path in Path("app").rglob("*.py"):
        if path.name in {"queries.py", "execution.py"} and "workout_session" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", "") == "advance_checkpoint"):
                callers.append(f"{path}:{node.lineno}")
    assert callers == [], callers


# ═════════════════════════════════════════════════════════════════════════════
# §48 / S14-2 — the shared session projection
# ═════════════════════════════════════════════════════════════════════════════

def test_the_canonical_projection_publishes_durable_progress(app, make_user):
    user = make_user("projection-owner")
    save_workout_plan(user.id)
    row = WorkoutSession(
        public_id="seeded-public-id", user_id=user.id,
        status=WORKOUT_SESSION_ACTIVE, workout_date=app_today().isoformat(),
        weekday_slot=WEEKDAYS[app_today().weekday()], source="scheduled",
        checkpoint_revision=3, checkpoint_data=json.dumps(snapshot(reps=11)),
        checkpoint_fingerprint="a" * 64, checkpoint_idempotency_key="seed-key-0001",
        checkpoint_at=datetime.utcnow(), version=1)
    db.session.add(row)
    db.session.commit()

    view = build_session_view(row, app_today())
    published = view.to_dict()

    assert published["public_id"] == "seeded-public-id"
    assert published["checkpoint_revision"] == 3
    assert published["checkpoint"]["exercises"][0]["sets"][0]["reps"] == 11
    # Durable progress, never persistence internals: the replay authority and
    # the row's identity stay private (brief §7).
    for private in ("id", "user_id", "checkpoint_fingerprint",
                    "checkpoint_idempotency_key", "plan_fingerprint",
                    "planned_training_plan_id", "workout_ref"):
        assert private not in published
    assert row.id not in published.values()


def test_a_session_with_no_checkpoint_publishes_zero_and_none(app, make_user):
    """0 means "started, nothing recorded yet" -- never "unknown"."""
    user = make_user("fresh-session-owner")
    save_workout_plan(user.id)
    row = WorkoutSession(
        public_id="fresh-public-id", user_id=user.id,
        status=WORKOUT_SESSION_ACTIVE, workout_date=app_today().isoformat(),
        weekday_slot=WEEKDAYS[app_today().weekday()], source="scheduled",
        checkpoint_revision=0, version=1)
    db.session.add(row)
    db.session.commit()

    published = build_session_view(row, app_today()).to_dict()

    assert published["checkpoint_revision"] == 0
    assert published["checkpoint"] is None


def test_a_corrupt_stored_snapshot_never_makes_an_owned_session_unreadable(
    app, make_user
):
    user = make_user("corrupt-snapshot-owner")
    save_workout_plan(user.id)
    row = WorkoutSession(
        public_id="corrupt-public-id", user_id=user.id,
        status=WORKOUT_SESSION_ACTIVE, workout_date=app_today().isoformat(),
        weekday_slot=WEEKDAYS[app_today().weekday()], source="scheduled",
        checkpoint_revision=2, checkpoint_data="{not json", version=1)
    db.session.add(row)
    db.session.commit()

    published = build_session_view(row, app_today()).to_dict()

    assert published["checkpoint"] is None
    assert published["checkpoint_revision"] == 2


def test_every_server_projection_of_a_session_carries_the_same_revision(
    client, auth_user, sessions_on
):
    """S14-2: the browser envelope, the canonical read model and the native
    projection are three renderings of ONE value, not three computations."""
    from app.services.mobile_workout_sessions.projection import project_session

    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    assert checkpoint_over_http(client, public_id, 0).status_code == 200

    browser = client.get("/workout/session/current").get_json()["session"]
    canonical = get_current_session(auth_user.id).session
    state_view = read_session_for_state(auth_user.id)
    row = get_owned_session(auth_user.id, public_id)
    native = project_session(row, build_session_view(row, app_today()))

    assert browser["checkpoint_revision"] == 1
    assert canonical.checkpoint_revision == 1
    assert state_view.checkpoint_revision == 1
    # The native envelope names it `revision`; two transports may name one
    # canonical value differently, they may not compute it differently.
    assert native["revision"] == 1
    assert native["checkpoint"] == browser["checkpoint"] == canonical.checkpoint


def test_the_projection_stays_additive_for_existing_consumers():
    """S14-2 must not have changed the meaning of a single existing key."""
    fields = {field for field in SessionView.__dataclass_fields__}
    assert {"checkpoint_revision", "checkpoint"} <= fields
    # Everything the pre-PR2 contract published is still published, unchanged.
    assert {
        "public_id", "status", "workout_date", "weekday_slot", "source",
        "started_at", "last_activity_at", "completed_at", "abandoned_at",
        "terminal_reason", "relationship", "stale_reason", "resumable",
    } <= fields


# ═════════════════════════════════════════════════════════════════════════════
# §49 / S14-1 — browser checkpoint happy path
# ═════════════════════════════════════════════════════════════════════════════

def test_a_valid_browser_checkpoint_persists_the_snapshot_and_advances_by_one(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)

    response = checkpoint_over_http(client, public_id, 0, snapshot(reps=12, sets=3))

    assert response.status_code == 200
    body = response.get_json()
    assert body["outcome"] == "checkpointed"
    assert body["replayed"] is False
    assert body["session"]["checkpoint_revision"] == 1

    row = row_for(auth_user.id)
    assert row.checkpoint_revision == 1
    stored = json.loads(row.checkpoint_data)
    assert stored == snapshot(reps=12, sets=3)
    assert row.checkpoint_at is not None
    assert row.checkpoint_fingerprint
    assert row.status == WORKOUT_SESSION_ACTIVE
    # Nothing else moved: a checkpoint is progress, not completion.
    assert PumpCheck.query.count() == 0
    assert WorkoutLog.query.count() == 0
    assert row.completed_at is None


def test_consecutive_checkpoints_advance_one_revision_each(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)

    for revision in range(3):
        response = checkpoint_over_http(
            client, public_id, revision, snapshot(reps=8 + revision),
            key=f"browser-key-{revision:06d}")
        assert response.status_code == 200
        assert response.get_json()["session"]["checkpoint_revision"] == revision + 1

    row = row_for(auth_user.id)
    assert row.checkpoint_revision == 3
    assert json.loads(row.checkpoint_data)["exercises"][0]["sets"][0]["reps"] == 10


def test_a_checkpoint_may_name_every_exercise_of_the_canonical_workout(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id, exercise_ids=(SQUAT, BENCH, ROW))
    public_id = start_session_over_http(client)

    body = {
        "current_exercise_index": 2,
        "elapsed_seconds": 900,
        "exercises": [
            {"exercise_id": ROW, "sets": [
                {"index": 0, "completed": True, "reps": 10, "weight_kg": 40.0}]},
            {"exercise_id": SQUAT, "sets": [
                {"index": 0, "completed": True, "reps": 5, "weight_kg": 100.0}]},
        ],
    }
    response = checkpoint_over_http(client, public_id, 0, body)

    assert response.status_code == 200
    stored = json.loads(row_for(auth_user.id).checkpoint_data)
    # Canonical ordering: stored in the WORKOUT's order, not the request's, so
    # the same progress sent in a different order fingerprints identically.
    assert [entry["exercise_id"] for entry in stored["exercises"]] == [SQUAT, ROW]


# ═════════════════════════════════════════════════════════════════════════════
# §50 / S14-1 — stale checkpoint writes nothing
# ═════════════════════════════════════════════════════════════════════════════

def test_a_stale_browser_checkpoint_is_refused_and_mutates_no_column(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    assert checkpoint_over_http(
        client, public_id, 0, snapshot(reps=9), key="browser-key-000001"
    ).status_code == 200
    before = durable_state(row_for(auth_user.id))

    # A late request built on R0 while the stored revision is already R1.
    stale = checkpoint_over_http(
        client, public_id, 0, snapshot(reps=99), key="browser-key-000002")

    assert stale.status_code == 409
    body = stale.get_json()
    assert body["code"] == "revision_conflict"
    assert stale.headers["Session-Resolution"] == "reread"
    assert body["session"] is None

    db.session.expire_all()
    after = durable_state(row_for(auth_user.id))
    assert after == before
    assert json.loads(after[1])["exercises"][0]["sets"][0]["reps"] == 9


def test_a_checkpoint_without_a_declared_revision_is_refused(
    client, auth_user, sessions_on
):
    """Progress with no declared base revision cannot be ordered, so there is no
    default and no fallback channel — it is simply refused."""
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)

    response = client.post(
        f"/workout/session/{public_id}/checkpoint",
        headers={"Idempotency-Key": "browser-key-000001"},
        json={"checkpoint": snapshot()})

    assert response.status_code == 428
    assert response.get_json()["code"] == "revision_required"
    assert row_for(auth_user.id).checkpoint_revision == 0


@pytest.mark.parametrize("bad", ["", "abc", "-1", "1.0", "9999999999", '"1"x'])
def test_a_malformed_revision_is_refused_rather_than_coerced(
    client, auth_user, sessions_on, bad
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)

    response = client.post(
        f"/workout/session/{public_id}/checkpoint",
        headers={"If-Match": bad, "Idempotency-Key": "browser-key-000001"},
        json={"checkpoint": snapshot()})

    assert response.status_code == 428
    assert durable_state(row_for(auth_user.id))[0] == 0


def test_a_checkpoint_without_an_idempotency_key_is_refused(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)

    response = client.post(
        f"/workout/session/{public_id}/checkpoint",
        headers={"If-Match": "0"}, json={"checkpoint": snapshot()})

    assert response.status_code == 400
    assert response.get_json()["code"] == "idempotency_key_invalid"
    assert row_for(auth_user.id).checkpoint_data is None


# ═════════════════════════════════════════════════════════════════════════════
# §51 — replay identity
# ═════════════════════════════════════════════════════════════════════════════

def test_the_same_key_and_the_same_snapshot_replay_without_advancing(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    first = checkpoint_over_http(client, public_id, 0, snapshot(reps=7))
    assert first.status_code == 200
    assert first.get_json()["session"]["checkpoint_revision"] == 1
    committed = durable_state(row_for(auth_user.id))

    # The identical request again — a retry after a lost response.
    replay = checkpoint_over_http(client, public_id, 0, snapshot(reps=7))

    assert replay.status_code == 200
    body = replay.get_json()
    assert body["replayed"] is True
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert body["session"]["checkpoint_revision"] == 1
    db.session.expire_all()
    assert durable_state(row_for(auth_user.id)) == committed


def test_the_same_key_with_a_different_snapshot_is_a_deterministic_conflict(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    assert checkpoint_over_http(client, public_id, 0, snapshot(reps=7)).status_code == 200
    committed = durable_state(row_for(auth_user.id))

    conflict = checkpoint_over_http(client, public_id, 1, snapshot(reps=15))

    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "idempotency_conflict"
    db.session.expire_all()
    assert durable_state(row_for(auth_user.id)) == committed


def test_a_reordered_but_semantically_identical_retry_replays(
    client, auth_user, sessions_on
):
    """Canonical ordering exists so a retry that serialized its exercises in a
    different order is recognised as the SAME command, not a conflict."""
    save_workout_plan(auth_user.id, exercise_ids=(SQUAT, BENCH))
    public_id = start_session_over_http(client)

    def body(order):
        return {
            "current_exercise_index": 0, "elapsed_seconds": 60,
            "exercises": [
                {"exercise_id": exercise_id, "sets": [
                    {"index": 0, "completed": True, "reps": 6, "weight_kg": 80.0}]}
                for exercise_id in order
            ],
        }

    assert checkpoint_over_http(client, public_id, 0, body((SQUAT, BENCH))).status_code == 200
    replay = checkpoint_over_http(client, public_id, 0, body((BENCH, SQUAT)))

    assert replay.status_code == 200
    assert replay.get_json()["replayed"] is True
    assert row_for(auth_user.id).checkpoint_revision == 1


# ═════════════════════════════════════════════════════════════════════════════
# §52 / S14-6 — validation bounds: refuse completely, never truncate
# ═════════════════════════════════════════════════════════════════════════════

def _oversized_snapshot():
    """Valid on every per-dimension bound but pathological in bytes."""
    return {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": [
            {"index": index, "completed": True, "reps": 10,
             "weight_kg": 100.0 + index}
            for index in range(MAX_SETS_PER_EXERCISE)
        ]}],
    }


@pytest.mark.parametrize("label,body", [
    ("unknown exercise", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": "ex_not_in_this_workout", "sets": []}]}),
    ("exercise outside the canonical workout", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": ROW, "sets": []}]}),
    ("duplicate exercise identity", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": []},
                      {"exercise_id": SQUAT, "sets": []}]}),
    ("too many exercises", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": []}] * (MAX_EXERCISES + 1)}),
    ("too many sets", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": [
            {"index": i, "completed": True, "reps": 1, "weight_kg": 1.0}
            for i in range(MAX_SETS_PER_EXERCISE + 1)]}]}),
    ("out-of-range set index", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": [
            {"index": MAX_SETS_PER_EXERCISE, "completed": True,
             "reps": 1, "weight_kg": 1.0}]}]}),
    ("duplicate set index", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": [
            {"index": 0, "completed": True, "reps": 1, "weight_kg": 1.0},
            {"index": 0, "completed": True, "reps": 2, "weight_kg": 2.0}]}]}),
    ("current_exercise_index past the workout", {
        "current_exercise_index": 5, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": []}]}),
    ("unknown top-level field", {
        "current_exercise_index": 0, "elapsed_seconds": 60, "exercises": [],
        "client_note": "hello"}),
    ("missing field", {"current_exercise_index": 0, "exercises": []}),
    ("not an object", ["not", "an", "object"]),
    ("boolean smuggled as a completion flag", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": [
            {"index": 0, "completed": "yes", "reps": 1, "weight_kg": 1.0}]}]}),
    ("negative reps", {
        "current_exercise_index": 0, "elapsed_seconds": 60,
        "exercises": [{"exercise_id": SQUAT, "sets": [
            {"index": 0, "completed": True, "reps": -1, "weight_kg": 1.0}]}]}),
])
def test_an_invalid_checkpoint_is_refused_whole_and_stores_nothing(
    client, auth_user, sessions_on, label, body
):
    save_workout_plan(auth_user.id, exercise_ids=(SQUAT, BENCH))
    public_id = start_session_over_http(client)
    before = durable_state(row_for(auth_user.id))

    response = checkpoint_over_http(client, public_id, 0, body)

    assert response.status_code == 400, label
    assert response.get_json()["code"] == "invalid_checkpoint", label
    db.session.expire_all()
    # Never truncated, never partially saved, never coerced.
    assert durable_state(row_for(auth_user.id)) == before, label


def test_an_oversized_body_is_refused_and_stores_nothing(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id, exercise_ids=(SQUAT,))
    public_id = start_session_over_http(client)
    body = _oversized_snapshot()
    # Inflate past the byte backstop without breaking any per-dimension bound,
    # by using the longest identity the column can hold.
    body["exercises"] = [
        {"exercise_id": "e" * 64, "sets": body["exercises"][0]["sets"]}
    ]

    response = checkpoint_over_http(client, public_id, 0, body)

    assert response.status_code == 400
    assert row_for(auth_user.id).checkpoint_data is None
    assert MAX_SNAPSHOT_BYTES > 0


def test_a_checkpoint_against_a_regenerated_plan_is_stale_not_silently_rebound(
    client, auth_user, sessions_on
):
    """The browser has no opaque workout reference, so drift is decided by the
    session's own versioned plan fingerprint — the same signal the lifecycle
    classifier uses, never a second staleness rule."""
    save_workout_plan(auth_user.id, exercise_ids=(SQUAT, BENCH))
    public_id = start_session_over_http(client)
    # Regenerate the plan with a different workout for the same weekday.
    save_workout_plan(auth_user.id, exercise_ids=(ROW,), names=["Row"])

    response = checkpoint_over_http(client, public_id, 0, snapshot(exercise_id=ROW))

    assert response.status_code == 409
    assert response.get_json()["code"] == "stale_session_requires_resolution"
    assert row_for(auth_user.id).checkpoint_data is None


def test_a_session_with_no_planned_workout_cannot_checkpoint(
    client, auth_user, sessions_on
):
    """An unscheduled session has no canonical workout to be a member of — the
    same reason a native session without a workout reference is refused."""
    public_id = start_session_over_http(client)  # no plan at all → unscheduled
    assert row_for(auth_user.id).source == "unscheduled"

    response = checkpoint_over_http(client, public_id, 0)

    assert response.status_code == 409
    assert response.get_json()["code"] == "stale_session_requires_resolution"
    assert row_for(auth_user.id).checkpoint_data is None


# ═════════════════════════════════════════════════════════════════════════════
# §53 — ownership: no oracle
# ═════════════════════════════════════════════════════════════════════════════

def test_user_b_cannot_checkpoint_user_a_and_learns_nothing(
    client, auth_user, make_user, sessions_on
):
    victim = make_user("victim-owner")
    save_workout_plan(victim.id)
    save_workout_plan(auth_user.id)
    foreign = WorkoutSession(
        public_id="victim-public-id", user_id=victim.id,
        status=WORKOUT_SESSION_ACTIVE, workout_date=app_today().isoformat(),
        weekday_slot=WEEKDAYS[app_today().weekday()], source="scheduled",
        checkpoint_revision=0, version=1)
    db.session.add(foreign)
    db.session.commit()

    attacked = checkpoint_over_http(client, "victim-public-id", 0)
    absent = checkpoint_over_http(client, "no-such-session-id", 0)

    # Indistinguishable: another owner's session and one that never existed.
    assert attacked.status_code == absent.status_code == 404
    assert attacked.get_json() == absent.get_json()
    db.session.expire_all()
    untouched = db.session.get(WorkoutSession, foreign.id)
    assert untouched.status == WORKOUT_SESSION_ACTIVE
    assert untouched.checkpoint_revision == 0
    assert untouched.checkpoint_data is None


# ═════════════════════════════════════════════════════════════════════════════
# §54 — terminal sessions accept nothing
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("terminal_status", [
    WORKOUT_SESSION_COMPLETED, WORKOUT_SESSION_ABANDONED,
])
def test_a_terminal_session_cannot_be_checkpointed(
    client, auth_user, sessions_on, terminal_status
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    row = row_for(auth_user.id)
    row.status = terminal_status
    row.checkpoint_revision = 4
    db.session.commit()
    before = durable_state(row_for(auth_user.id))

    response = checkpoint_over_http(client, public_id, 4)

    assert response.status_code == 409
    assert response.get_json()["code"] == "session_terminal"
    db.session.expire_all()
    # No resurrection, no durable progress after a terminal transition.
    assert durable_state(row_for(auth_user.id)) == before


# ═════════════════════════════════════════════════════════════════════════════
# §55 / S14-3 — browser completion declares its revision
# ═════════════════════════════════════════════════════════════════════════════

def test_a_session_linked_web_completion_at_the_current_revision_succeeds(
    client, auth_user, sessions_on, proof_accepted
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    assert checkpoint_over_http(client, public_id, 0).status_code == 200

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon", "session_id": public_id,
        "expected_checkpoint_revision": 1,
    })

    assert response.status_code == 200
    body = response.get_json()
    assert body["session_completed"] is True
    assert body["points_awarded"] > 0

    row = row_for(auth_user.id)
    assert row.status == WORKOUT_SESSION_COMPLETED
    assert row.completed_at is not None
    # The accepted checkpoint is preserved, not discarded by completion.
    assert row.checkpoint_revision == 1
    assert json.loads(row.checkpoint_data)["exercises"][0]["sets"][0]["reps"] == 8
    # Exactly one of each completion artifact.
    assert PumpCheck.query.filter_by(user_id=auth_user.id).count() == 1
    assert WorkoutLog.query.filter_by(user_id=auth_user.id).count() == 1
    assert Activity.query.filter_by(
        user_id=auth_user.id, activity_type="workout_completed").count() == 1


def test_a_session_linked_web_completion_without_a_revision_is_refused(
    client, auth_user, sessions_on, proof_accepted
):
    """The declaration is REQUIRED, so no caller can opt out of the precondition
    by simply omitting the field."""
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon", "session_id": public_id,
    })

    assert response.status_code == 428
    assert response.get_json()["code"] == "revision_required"
    assert PumpCheck.query.count() == 0
    assert row_for(auth_user.id).status == WORKOUT_SESSION_ACTIVE


def test_a_revision_declared_without_a_session_is_refused_not_ignored(
    client, auth_user, sessions_on, proof_accepted
):
    save_workout_plan(auth_user.id)

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon",
        "expected_checkpoint_revision": 0,
    })

    assert response.status_code == 400
    assert response.get_json()["code"] == "invalid_checkpoint"
    assert PumpCheck.query.count() == 0


# ═════════════════════════════════════════════════════════════════════════════
# §56 / §24 — stale completion: typed refusal, ZERO artifacts
# ═════════════════════════════════════════════════════════════════════════════

def test_a_stale_web_completion_writes_no_artifact_at_all(
    client, auth_user, sessions_on, proof_accepted
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    # Progress moved on to R2 after the caller last read R1.
    assert checkpoint_over_http(
        client, public_id, 0, snapshot(reps=8), key="browser-key-000001"
    ).status_code == 200
    assert checkpoint_over_http(
        client, public_id, 1, snapshot(reps=10), key="browser-key-000002"
    ).status_code == 200
    committed = durable_state(row_for(auth_user.id))

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon", "session_id": public_id,
        "expected_checkpoint_revision": 1,
    })

    assert response.status_code == 409
    assert response.get_json()["code"] == "revision_conflict"

    db.session.expire_all()
    # Asserted from PERSISTED data, not from the response.
    assert PumpCheck.query.count() == 0
    assert WorkoutLog.query.count() == 0
    assert Activity.query.filter_by(activity_type="workout_completed").count() == 0
    assert UserQuestProgress.query.count() == 0
    row = row_for(auth_user.id)
    assert row.status == WORKOUT_SESSION_ACTIVE
    assert row.completed_at is None
    # The accepted checkpoint the caller did not know about is intact.
    assert durable_state(row) == committed
    assert json.loads(row.checkpoint_data)["exercises"][0]["sets"][0]["reps"] == 10


# ═════════════════════════════════════════════════════════════════════════════
# §58 — the expensive tail is skipped when the refusal is already decidable
# ═════════════════════════════════════════════════════════════════════════════

def test_a_stale_completion_pays_for_no_vision_call_and_no_upload(
    client, auth_user, sessions_on, proof_accepted, monkeypatch
):
    uploads = []
    monkeypatch.setattr(training_bp.s3_helper, "is_enabled", lambda: True)
    monkeypatch.setattr(
        training_bp.s3_helper, "upload_image",
        lambda *a, **k: uploads.append(1) or "key")

    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    assert checkpoint_over_http(client, public_id, 0).status_code == 200

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon", "session_id": public_id,
        "expected_checkpoint_revision": 0,
    })

    assert response.status_code == 409
    # The preflight is a COST optimisation; this asserts it actually saves cost.
    assert proof_accepted["vision"] == 0
    assert proof_accepted["image"] == 0
    assert uploads == []


# ═════════════════════════════════════════════════════════════════════════════
# §57 — the canonical guard is not vacuous
# ═════════════════════════════════════════════════════════════════════════════

def test_the_locked_completion_service_refuses_a_stale_revision_by_itself(
    app, make_user
):
    """Reaches the canonical authority DIRECTLY, bypassing the route entirely.

    If the only thing making the stale-completion tests pass were the route's
    cheap preflight, this test would fail — which is exactly its job. Deleting
    the route preflight leaves this green; deleting the in-transaction revision
    comparison in ``workout_completion.service`` turns it red.
    """
    from app.services.workout_completion import (
        CompleteWorkoutCommand,
        SessionCompletionConflict,
        complete_workout,
    )

    user = make_user("canonical-guard-owner")
    save_workout_plan(user.id)
    row = WorkoutSession(
        public_id="guard-public-id", user_id=user.id,
        status=WORKOUT_SESSION_ACTIVE, workout_date=app_today().isoformat(),
        weekday_slot=WEEKDAYS[app_today().weekday()], source="scheduled",
        checkpoint_revision=2, checkpoint_data=json.dumps(snapshot()),
        version=1)
    db.session.add(row)
    db.session.commit()
    session_id = row.id

    with pytest.raises(SessionCompletionConflict) as raised:
        complete_workout(CompleteWorkoutCommand(
            user_id=user.id, today=app_today(), session_id=session_id,
            expected_checkpoint_revision=1, image_key="k",
            entry_path="sprint14_guard"))

    assert raised.value.reason == "revision"
    db.session.expire_all()
    assert PumpCheck.query.count() == 0
    assert WorkoutLog.query.count() == 0
    refreshed = db.session.get(WorkoutSession, session_id)
    assert refreshed.status == WORKOUT_SESSION_ACTIVE
    assert refreshed.checkpoint_revision == 2


def test_the_route_hands_the_declared_revision_to_the_canonical_command(
    client, auth_user, sessions_on, proof_accepted, monkeypatch
):
    """The route may not merely CHECK the revision — it must DECLARE it, or the
    in-transaction guard has nothing to enforce."""
    from app.services import workout_completion

    seen = []
    original = workout_completion.complete_workout

    def _spy(command):
        seen.append(command.expected_checkpoint_revision)
        return original(command)

    monkeypatch.setattr(training_bp, "run_completion", _spy)

    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    assert checkpoint_over_http(client, public_id, 0).status_code == 200

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon", "session_id": public_id,
        "expected_checkpoint_revision": 1,
    })

    assert response.status_code == 200
    assert seen == [1]


# ═════════════════════════════════════════════════════════════════════════════
# §22 / §59 / S14-10 — the legacy path and the dark path are untouched
# ═════════════════════════════════════════════════════════════════════════════

def test_legacy_completion_needs_no_revision_and_fabricates_no_session(
    client, auth_user, proof_accepted
):
    """Flag OFF: byte-for-byte the pre-PR2 legacy contract."""
    save_workout_plan(auth_user.id)

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon",
        # Both session fields are present and BOTH are ignored while dark.
        "session_id": "does-not-exist",
        "expected_checkpoint_revision": 7,
    })

    assert response.status_code == 200
    body = response.get_json()
    assert "session_completed" not in body
    assert WorkoutSession.query.count() == 0
    assert PumpCheck.query.filter_by(user_id=auth_user.id).count() == 1


def test_a_flag_on_completion_without_a_session_stays_on_the_legacy_contract(
    client, auth_user, sessions_on, proof_accepted
):
    """§22: the session feature must not impose a revision on users who are not
    using it. The AI-coach tool relies on exactly this contract."""
    save_workout_plan(auth_user.id)

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon"})

    assert response.status_code == 200
    assert "session_completed" not in response.get_json()
    assert WorkoutSession.query.count() == 0


def test_the_ai_coach_completion_contract_still_declares_no_revision():
    """`expected_checkpoint_revision=None` must stay a first-class contract."""
    from app.services.workout_completion import CompleteWorkoutCommand

    command = CompleteWorkoutCommand(user_id=1, today=app_today())
    assert command.expected_checkpoint_revision is None
    assert command.session_id is None


@pytest.mark.parametrize("method,path", [
    ("post", "/workout/session/start"),
    ("get", "/workout/session/current"),
    ("post", "/workout/session/abc/resume"),
    ("post", "/workout/session/abc/checkpoint"),
    ("post", "/workout/session/abc/abandon"),
])
def test_the_browser_session_surface_is_absent_while_the_flag_is_off(
    client, auth_user, method, path
):
    """S14-10 (backend half): dark means ABSENT, not merely refused. No session
    state is fabricated and no row is written on the OFF path."""
    response = getattr(client, method)(
        path, headers=headers(0), json={"checkpoint": snapshot()})

    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"
    assert WorkoutSession.query.count() == 0


def test_a_dark_checkpoint_of_an_OWNED_session_enters_no_execution_logic(
    client, auth_user
):
    """The load-bearing OFF-path test: the session EXISTS and the request is
    perfectly well formed.

    Its predecessor addressed a session id that was never minted, so the route
    answered 404 from ``owned_session`` whether or not the flag gate was there --
    it could not tell "the surface is dark" apart from "that session does not
    exist", which is precisely what an OFF-path test has to distinguish. Here the
    caller owns an ACTIVE session and declares its real revision, so the ONLY
    thing that can produce a 404 is the gate, and the persisted row proves no
    execution logic ran behind it.
    """
    save_workout_plan(auth_user.id)
    row = WorkoutSession(
        public_id="dark-owned-session", user_id=auth_user.id,
        status=WORKOUT_SESSION_ACTIVE, workout_date=app_today().isoformat(),
        weekday_slot=WEEKDAYS[app_today().weekday()], source="scheduled",
        checkpoint_revision=0, version=1)
    db.session.add(row)
    db.session.commit()
    before = durable_state(row_for(auth_user.id))

    response = checkpoint_over_http(client, "dark-owned-session", 0)

    assert response.status_code == 404
    assert response.get_json()["code"] == "not_found"
    db.session.expire_all()
    after = row_for(auth_user.id)
    assert durable_state(after) == before
    assert after.checkpoint_revision == 0
    assert after.checkpoint_data is None


def test_a_dark_completion_declares_and_requires_no_revision(
    client, auth_user, proof_accepted
):
    """Flag OFF ⇒ the completion route never reaches the session block at all, so
    a body carrying a revision is neither honoured nor refused — it is ignored,
    exactly as the pre-PR2 legacy contract ignored an unknown field."""
    save_workout_plan(auth_user.id)

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon",
        "session_id": "dark-session", "expected_checkpoint_revision": 7,
    })

    assert response.status_code == 200
    assert WorkoutSession.query.count() == 0


def test_a_dark_checkpoint_answers_absent_rather_than_throttled(app, client, auth_user):
    """The flag gate sits OUTSIDE the throttle: a hammered surface that is
    switched off must look exactly like a surface that does not exist.

    The request count deliberately EXCEEDS ``WORKOUT_CHECKPOINT_RATELIMIT``'s
    per-minute bucket. Staying under it would prove nothing: an ungated route
    would answer 404 for every request too, and the test would pass with the gate
    deleted. Past the bucket, a gate applied inside the throttle answers 429 —
    and a switched-off surface that answers 429 has told the caller it exists.
    """
    from app.config import WORKOUT_CHECKPOINT_RATELIMIT
    from app.extensions import limiter

    per_minute = int(WORKOUT_CHECKPOINT_RATELIMIT.split()[0])
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
    limiter.enabled = True
    try:
        statuses = {
            client.post("/workout/session/abc/checkpoint",
                        headers=headers(0), json={"checkpoint": snapshot()})
            .status_code
            for _ in range(per_minute + 10)
        }
    finally:
        limiter.enabled = False

    assert statuses == {404}


# ═════════════════════════════════════════════════════════════════════════════
# §61 / S14-4 — cross-transport characterization (one row, one authority)
# ═════════════════════════════════════════════════════════════════════════════
#
# NOT the final PostgreSQL race proof — that stays PR4. These are deterministic
# sequential assertions that the two server transports address the same row and
# read back the same canonical state.

def _native_result(user_id, public_id):
    from app.services.mobile_workout_sessions.projection import project_session

    row = get_owned_session(user_id, public_id)
    return project_session(row, build_session_view(row, app_today()))


def test_a_native_checkpoint_is_visible_to_the_browser_at_the_same_revision(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)

    # Write through the CANONICAL command the native adapter uses, naming the
    # workout the way the browser does (the native ref path needs a signed
    # reference; the authority under test is the same either way).
    from app.services.workout_session import parse_checkpoint, planned_exercise_identities

    result = record_checkpoint(
        auth_user.id, public_id, "native-key-000001", 0,
        planned_exercise_identities,
        lambda allowed: parse_checkpoint(snapshot(reps=13), allowed))
    assert result.view.checkpoint_revision == 1

    browser = client.get("/workout/session/current").get_json()["session"]

    assert browser["checkpoint_revision"] == 1
    assert browser["checkpoint"]["exercises"][0]["sets"][0]["reps"] == 13
    assert browser["public_id"] == public_id
    assert WorkoutSession.query.count() == 1


def test_a_browser_checkpoint_is_visible_to_the_native_projection(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    assert checkpoint_over_http(client, public_id, 0, snapshot(reps=14)).status_code == 200

    native = _native_result(auth_user.id, public_id)

    assert native["session_ref"] == public_id
    assert native["revision"] == 1
    assert native["checkpoint"]["exercises"][0]["sets"][0]["reps"] == 14
    assert WorkoutSession.query.count() == 1


def test_a_browser_checkpoint_built_on_a_revision_native_already_moved_is_stale(
    client, auth_user, sessions_on
):
    """The failure mode F-1 named: one surface silently discarding another's
    committed progress. It is now a typed refusal that writes nothing."""
    from app.services.workout_session import parse_checkpoint, planned_exercise_identities

    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    record_checkpoint(
        auth_user.id, public_id, "native-key-000001", 0,
        planned_exercise_identities,
        lambda allowed: parse_checkpoint(snapshot(reps=20), allowed))
    committed = durable_state(row_for(auth_user.id))

    stale = checkpoint_over_http(
        client, public_id, 0, snapshot(reps=3), key="browser-key-000009")

    assert stale.status_code == 409
    assert stale.get_json()["code"] == "revision_conflict"
    db.session.expire_all()
    assert durable_state(row_for(auth_user.id)) == committed


def test_a_web_completion_built_on_a_revision_native_already_moved_is_refused(
    client, auth_user, sessions_on, proof_accepted
):
    from app.services.workout_session import parse_checkpoint, planned_exercise_identities

    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    record_checkpoint(
        auth_user.id, public_id, "native-key-000001", 0,
        planned_exercise_identities,
        lambda allowed: parse_checkpoint(snapshot(reps=20), allowed))
    record_checkpoint(
        auth_user.id, public_id, "native-key-000002", 1,
        planned_exercise_identities,
        lambda allowed: parse_checkpoint(snapshot(reps=21), allowed))

    response = client.post("/workout/complete", json={
        "image": "x", "location_type": "salon", "session_id": public_id,
        "expected_checkpoint_revision": 1,
    })

    assert response.status_code == 409
    assert response.get_json()["code"] == "revision_conflict"
    db.session.expire_all()
    assert PumpCheck.query.count() == 0
    assert row_for(auth_user.id).status == WORKOUT_SESSION_ACTIVE
    assert row_for(auth_user.id).checkpoint_revision == 2


def test_neither_transport_mints_a_second_session_identity(
    client, auth_user, sessions_on
):
    """S14-9 adjacent: one table, one public id, one active-session claim."""
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    assert checkpoint_over_http(client, public_id, 0).status_code == 200

    replayed_start = client.post("/workout/session/start", json={})

    assert replayed_start.status_code == 200
    assert replayed_start.get_json()["session"]["public_id"] == public_id
    assert WorkoutSession.query.filter_by(
        user_id=auth_user.id, status=WORKOUT_SESSION_ACTIVE).count() == 1


# ═════════════════════════════════════════════════════════════════════════════
# §17 — atomicity: a revision never moves without its snapshot
# ═════════════════════════════════════════════════════════════════════════════

def test_the_revision_and_its_snapshot_always_move_together(
    client, auth_user, sessions_on
):
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    observed = []

    for revision in range(3):
        checkpoint_over_http(
            client, public_id, revision, snapshot(reps=5 + revision),
            key=f"browser-key-{revision:06d}")
        db.session.expire_all()
        row = row_for(auth_user.id)
        observed.append((
            row.checkpoint_revision,
            json.loads(row.checkpoint_data)["exercises"][0]["sets"][0]["reps"],
            row.checkpoint_fingerprint is not None,
        ))

    assert observed == [(1, 5, True), (2, 6, True), (3, 7, True)]


def test_a_refused_checkpoint_leaves_last_activity_untouched_too(
    client, auth_user, sessions_on
):
    """A refusal must not even look like a heartbeat: the whole write is one
    conditional UPDATE, so a stale request touches nothing at all."""
    save_workout_plan(auth_user.id)
    public_id = start_session_over_http(client)
    row = row_for(auth_user.id)
    row.last_activity_at = datetime.utcnow() - timedelta(hours=2)
    db.session.commit()
    before = row_for(auth_user.id).last_activity_at

    assert checkpoint_over_http(client, public_id, 5).status_code == 409

    db.session.expire_all()
    assert row_for(auth_user.id).last_activity_at == before
