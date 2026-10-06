"""TI-03 Training Insight read API over real persistence.

Exact projection shape, flag readiness, ownership, read-only behaviour, query
bounds, deterministic ordering, legacy/Pump Check separation and one real
native lifecycle (start → V2 checkpoint → complete) feeding the insight.
"""
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import (
    WORKOUT_COMPLETION_MARKER, PumpCheck, TrainingPlan, WorkoutLog, WorkoutSession,
)
from app.services import training_intelligence
from app.services.training_intelligence import queries
from app.services.training_intelligence.models import MAX_HISTORY_ROWS
from app.timeutil import APP_TZ, audit_clock
from tests.test_mobile_workout_sessions_api import as_mobile, completion_proof  # noqa: F401
from tests.ti03_support import (
    ANCHOR, BENCH, MONDAY, SQUAT, context_entry, day, stored, straight, utc_noon,
)

INSIGHT = "/api/v1/training/workout-sessions/{}/training-insight"
CAPABILITIES = "/api/v1/training/workout-execution-capabilities"
INSIGHT_KEYS = {"contract_version", "ruleset_version", "session_ref", "checkpoint_revision",
                "state", "kind", "exercise_id", "title_key", "evidence", "missing_data",
                "recommended_action"}
EVIDENCE_KEYS = {"metric", "unit", "previous", "current", "paired_sets", "previous_session_ref"}


@pytest.fixture
def flags(app):
    def _set(sessions=True, p0=True, p1=True):
        app.config.update(FITX_WORKOUT_SESSIONS_ENABLED=sessions,
                          FITX_TRAINING_EXECUTION_CONTEXT_ENABLED=p0,
                          FITX_TRAINING_INSIGHTS_ENABLED=p1)
    _set()
    return _set


@pytest.fixture
def owner(make_user):
    return make_user("ti03-owner")


@pytest.fixture
def stranger(make_user):
    return make_user("ti03-stranger")


def persist(user_id, row):
    db.session.add(WorkoutSession(
        public_id=row.ref, user_id=user_id, status=row.status, workout_date=row.workout_date,
        weekday_slot=row.weekday_slot, source="scheduled",
        started_at=(row.completed_at or utc_noon(ANCHOR)) - timedelta(minutes=50),
        last_activity_at=row.completed_at or utc_noon(ANCHOR),
        completed_at=row.completed_at if row.status == "completed" else None,
        checkpoint_revision=row.checkpoint_revision, checkpoint_data=row.checkpoint_data,
        execution_context_data=row.execution_context_data,
        prescription_data=row.prescription_data,
    ))
    db.session.commit()


def improving(user_id, *, current_ref="cur-ref", previous_ref="prev-ref"):
    persist(user_id, stored(previous_ref, day(-7), [(SQUAT, straight(60.0, 8, 8, 8))],
                            context=[context_entry(SQUAT, i, rir="2") for i in range(3)]))
    persist(user_id, stored(current_ref, ANCHOR, [(SQUAT, straight(60.0, 9, 8, 8))],
                            context=[context_entry(SQUAT, i, rir="2") for i in range(3)]))
    return current_ref


def get_insight(client, user, ref, as_mobile):
    return client.get(INSIGHT.format(ref), headers=as_mobile(user))


# ── Shape and contract ──────────────────────────────────────────────────────
def test_available_insight_has_the_exact_amended_shape(client, owner, flags, as_mobile):
    ref = improving(owner.id)
    response = get_insight(client, owner, ref, as_mobile)
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    body = response.get_json()
    assert set(body) == {"training_insight"}
    insight = body["training_insight"]
    assert set(insight) == INSIGHT_KEYS
    assert insight == {
        "contract_version": 1, "ruleset_version": "ti_rules_v1", "session_ref": ref,
        "checkpoint_revision": 4, "state": "available", "kind": "performance_improved",
        "exercise_id": SQUAT, "title_key": "training_insight.performance_improved",
        "evidence": [
            {"metric": "reps", "unit": "int", "previous": 24, "current": 25,
             "paired_sets": 3, "previous_session_ref": "prev-ref"},
            {"metric": "weight_kg", "unit": "number", "previous": 60.0, "current": 60.0,
             "paired_sets": 3, "previous_session_ref": "prev-ref"},
            {"metric": "rir", "unit": "token", "previous": "2", "current": "2",
             "paired_sets": 3, "previous_session_ref": "prev-ref"},
            # The previous session (09-24) lies in the recent complete week and
            # nothing in the prior one: described, but a zero baseline yields
            # no exposure context.
            {"metric": "set_count", "unit": "int", "previous": 0, "current": 3,
             "paired_sets": 0, "previous_session_ref": None},
            {"metric": "frequency_days", "unit": "int", "previous": 0, "current": 1,
             "paired_sets": 0, "previous_session_ref": None},
        ],
        "missing_data": ["missing_tempo"],
        "recommended_action": None,
    }
    assert all(set(item) == EVIDENCE_KEYS for item in insight["evidence"])


def test_insufficient_history_is_200_not_an_error(client, owner, flags, as_mobile):
    persist(owner.id, stored("only", ANCHOR, [(SQUAT, straight(60.0, 8, 8))]))
    insight = get_insight(client, owner, "only", as_mobile).get_json()["training_insight"]
    assert (insight["state"], insight["kind"], insight["exercise_id"], insight["title_key"]) == (
        "insufficient_data", "insufficient_comparable_history", SQUAT,
        "training_insight.insufficient_comparable_history")
    assert insight["evidence"] == [] and insight["recommended_action"] is None
    assert insight["missing_data"] == ["insufficient_pairs"]


def test_not_comparable_uses_null_kind_and_fixed_title(client, owner, flags, as_mobile):
    persist(owner.id, stored("prev", day(-7), [(SQUAT, straight(60.0, 8, 8))]))
    persist(owner.id, stored("cur", ANCHOR, [(SQUAT, straight(65.0, 6, 6))]))
    insight = get_insight(client, owner, "cur", as_mobile).get_json()["training_insight"]
    assert (insight["state"], insight["kind"], insight["title_key"]) == (
        "not_comparable", None, "training_insight.not_comparable")
    assert "mixed_performance" in insight["missing_data"]


@pytest.mark.parametrize("status", ["active", "abandoned"])
def test_unfinished_target_session_reports_session_not_completed(client, owner, flags, as_mobile, status):
    persist(owner.id, replace(stored("live", ANCHOR, [(SQUAT, straight(60.0, 8, 8))]), status=status))
    insight = get_insight(client, owner, "live", as_mobile).get_json()["training_insight"]
    assert insight["state"] == "insufficient_data"
    assert insight["missing_data"] == ["session_not_completed"]
    assert insight["exercise_id"] is None


@pytest.mark.parametrize("raw,code", [(None, "missing_execution"), ("{", "missing_execution"),
                                      ("EMPTY", "no_completed_sets")])
def test_target_without_usable_execution(client, owner, flags, as_mobile, raw, code):
    row = stored("cur", ANCHOR, [(SQUAT, straight(60.0, 8, 8))])
    if raw == "EMPTY":
        row = stored("cur", ANCHOR, [(SQUAT, [(0, False, 8, 60.0)])])
    else:
        row = replace(row, checkpoint_data=raw)
    persist(owner.id, row)
    insight = get_insight(client, owner, "cur", as_mobile).get_json()["training_insight"]
    assert insight["state"] == "insufficient_data" and insight["missing_data"] == [code]


# ── Eligibility over real rows ──────────────────────────────────────────────
@pytest.mark.parametrize("status", ["active", "abandoned"])
def test_unfinished_history_is_never_executed_evidence(client, owner, flags, as_mobile, status):
    persist(owner.id, replace(stored("prev", day(-7), [(SQUAT, straight(60.0, 8, 8))]), status=status))
    persist(owner.id, stored("cur", ANCHOR, [(SQUAT, straight(60.0, 9, 9))]))
    insight = get_insight(client, owner, "cur", as_mobile).get_json()["training_insight"]
    assert insight["state"] == "insufficient_data"
    # Not even read as an excluded historical row: the query never selects it.
    assert insight["missing_data"] == ["insufficient_pairs"]


def test_foreign_owner_history_and_sessions_are_invisible(client, owner, stranger, flags, as_mobile):
    persist(stranger.id, stored("theirs-prev", day(-7), [(SQUAT, straight(60.0, 8, 8))]))
    persist(stranger.id, stored("theirs", ANCHOR, [(SQUAT, straight(60.0, 9, 9))]))
    persist(owner.id, stored("mine", ANCHOR, [(SQUAT, straight(60.0, 9, 9))], minute=1))
    mine = get_insight(client, owner, "mine", as_mobile)
    assert mine.get_json()["training_insight"]["state"] == "insufficient_data"
    foreign = get_insight(client, owner, "theirs", as_mobile)
    absent = get_insight(client, owner, "never-existed", as_mobile)
    assert foreign.status_code == absent.status_code == 404
    assert foreign.get_json()["error"]["code"] == absent.get_json()["error"]["code"] == \
        "TRAINING_SESSION_NOT_FOUND"
    assert get_insight(client, owner, "x" * 65, as_mobile).status_code == 404


def test_corrupt_history_is_reported_not_silently_skipped(client, owner, flags, as_mobile):
    persist(owner.id, replace(stored("bad", day(-7), [(SQUAT, straight(60.0, 8, 8))]),
                              checkpoint_data="{"))
    persist(owner.id, stored("cur", ANCHOR, [(SQUAT, straight(60.0, 9, 9))]))
    insight = get_insight(client, owner, "cur", as_mobile).get_json()["training_insight"]
    assert insight["state"] == "insufficient_data"
    assert "history_unavailable" in insight["missing_data"]


def test_legacy_workout_logs_and_markers_are_never_merged(client, owner, flags, as_mobile):
    persist(owner.id, stored("cur", ANCHOR, [(SQUAT, straight(60.0, 9, 9))]))
    before = get_insight(client, owner, "cur", as_mobile).get_json()
    for offset in (-7, -10, -17):
        at = utc_noon(day(offset))
        db.session.add(WorkoutLog(user_id=owner.id, exercise_name="Back Squat", sets=3, reps=8,
                                  weight_kg=60.0, volume=1440.0, created_at=at))
        db.session.add(WorkoutLog(user_id=owner.id, exercise_name="ex_barbell_back_squat", sets=3,
                                  reps=8, weight_kg=60.0, volume=1440.0, created_at=at))
        db.session.add(WorkoutLog(user_id=owner.id, exercise_name=WORKOUT_COMPLETION_MARKER,
                                  sets=0, reps=0, weight_kg=0, volume=0, created_at=at))
    db.session.commit()
    after = get_insight(client, owner, "cur", as_mobile).get_json()
    assert after == before
    assert after["training_insight"]["state"] == "insufficient_data"


def test_pump_check_data_is_ignored(client, owner, flags, as_mobile):
    ref = improving(owner.id)
    before = get_insight(client, owner, ref, as_mobile).get_json()
    for offset in (0, -7):
        db.session.add(PumpCheck(user_id=owner.id, workout_score=1.0,
                                 description="tired, rested too little", valid=True,
                                 date_key=day(offset).isoformat(), created_at=utc_noon(day(offset))))
    db.session.commit()
    assert get_insight(client, owner, ref, as_mobile).get_json() == before


def test_current_plan_changes_never_reinterpret_history(client, owner, flags, as_mobile):
    ref = improving(owner.id)
    before = get_insight(client, owner, ref, as_mobile).get_json()
    db.session.add(TrainingPlan(user_id=owner.id, plan_data=json.dumps({"program": []}), score=1.0,
                                lineage_id="replacement-lineage", mutation_version=0))
    db.session.commit()
    assert get_insight(client, owner, ref, as_mobile).get_json() == before


# ── Flag readiness (TI-00 §17) ──────────────────────────────────────────────
@pytest.mark.parametrize("sessions,p0,p1,visible", [
    (True, True, True, True),
    (True, True, False, False),
    (True, False, True, False),     # invalid P1-without-P0: absent, never advertised
    (True, False, False, False),
    (False, True, True, False),
])
def test_flag_matrix(client, owner, flags, as_mobile, sessions, p0, p1, visible):
    ref = improving(owner.id)
    flags(sessions=sessions, p0=p0, p1=p1)
    response = get_insight(client, owner, ref, as_mobile)
    assert response.status_code == (200 if visible else 404)
    if not visible:
        assert response.get_json()["error"]["code"] == "TRAINING_SESSION_NOT_FOUND"
    capabilities = client.get(CAPABILITIES, headers=as_mobile(owner))
    if sessions:
        assert capabilities.get_json()["insights_enabled"] is visible
    # Disabling P1 never touches canonical execution storage.
    row = WorkoutSession.query.filter_by(public_id=ref).one()
    assert row.execution_context_data and row.prescription_data and row.checkpoint_data


# ── Read-only, bounded, deterministic ───────────────────────────────────────
@pytest.fixture
def statements(app):
    captured = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement.strip().split()[0].upper())

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", _before)
    yield captured
    event.remove(engine, "before_cursor_execute", _before)


def test_projection_issues_exactly_two_reads_and_no_writes(app, owner, flags, statements):
    improving(owner.id)
    for n in range(10):
        persist(owner.id, stored(f"extra{n}", day(-(n + 8)), [(SQUAT, straight(60.0, 8, 8)),
                                                               (BENCH, straight(40.0, 8, 8))],
                                 slot=MONDAY))
    owner_id = owner.id
    db.session.expire_all()
    statements.clear()
    with app.test_request_context():
        training_intelligence.build_training_insight(owner_id, "cur-ref")
    assert statements == ["SELECT", "SELECT"]


def test_reads_do_not_mutate_any_column(client, owner, flags, as_mobile):
    ref = improving(owner.id)

    def snapshot():
        db.session.expire_all()
        return [tuple(getattr(row, column.name) for column in WorkoutSession.__table__.columns)
                for row in WorkoutSession.query.order_by(WorkoutSession.id).all()]

    before = snapshot()
    for _ in range(3):
        get_insight(client, owner, ref, as_mobile)
    assert snapshot() == before


def test_insertion_order_never_changes_the_output(client, app, owner, flags, as_mobile):
    rows = [stored("prev", day(-7), [(SQUAT, straight(60.0, 8, 8)), (BENCH, straight(40.0, 8, 8))]),
            stored("cur", ANCHOR, [(SQUAT, straight(60.0, 7, 8)), (BENCH, straight(40.0, 9, 8))]),
            stored("tie-b", day(-10), [(SQUAT, straight(60.0, 8, 8, 8))], slot=MONDAY),
            stored("tie-a", day(-10), [(SQUAT, straight(60.0, 8, 8, 8))], slot=MONDAY),
            stored("w2", day(-16), [(SQUAT, straight(60.0, 8))], slot=MONDAY)]
    outputs = []
    for ordering in (rows, list(reversed(rows)), rows[2:] + rows[:2]):
        WorkoutSession.query.delete()
        db.session.commit()
        for row in ordering:
            persist(owner.id, row)
        outputs.append(get_insight(client, owner, "cur", as_mobile).get_data())
    assert outputs[0] == outputs[1] == outputs[2]
    insight = json.loads(outputs[0])["training_insight"]
    assert (insight["kind"], insight["exercise_id"]) == ("performance_declined", SQUAT)


def _slot_for(on):
    return "Perşembe" if on.weekday() == 3 else MONDAY


def test_worst_case_input_stays_bounded(app, owner, flags, statements):
    """32 exercises x 20 sets in the target and in every one of 56 prior days."""
    from app.services.exercise_catalog import load_exercise_catalog
    pool = [e.exercise_id for e in load_exercise_catalog().exercises if e.active][:32]
    assert len(pool) == 32
    full = [(exercise, straight(50.0, *([8] * 20))) for exercise in pool]
    prescribed = {exercise: 20 for exercise in pool}
    for n in range(56, 0, -1):
        persist(owner.id, stored(f"h{n:02d}", day(-n), full, prescribed=prescribed,
                                 slot=_slot_for(day(-n))))
    persist(owner.id, stored("cur", ANCHOR, full, prescribed=prescribed))
    owner_id = owner.id
    statements.clear()
    with app.test_request_context():
        payload = training_intelligence.build_training_insight(owner_id, "cur")
    insight = payload["training_insight"]
    assert statements == ["SELECT", "SELECT"]
    assert len(insight["evidence"]) <= 8 and len(insight["missing_data"]) <= 12
    assert len(json.dumps(payload)) < 4096


def test_history_row_bound_reports_incomplete_coverage(app, owner, flags):
    for n in range(MAX_HISTORY_ROWS + 3):
        persist(owner.id, stored(f"r{n:03d}", day(-(n % 50) - 1), [(SQUAT, straight(60.0, 8, 8))],
                                 slot=MONDAY, minute=n % 60))
    persist(owner.id, stored("cur", ANCHOR, [(SQUAT, straight(60.0, 8, 8))]))
    rows, truncated = queries.load_history(owner.id, ANCHOR)
    assert len(rows) == MAX_HISTORY_ROWS and truncated
    with app.test_request_context():
        insight = training_intelligence.build_training_insight(owner.id, "cur")["training_insight"]
    assert "incomplete_coverage" in insight["missing_data"]


def test_history_window_is_57_istanbul_day_keys():
    keys = queries.history_day_keys(ANCHOR)
    assert len(keys) == 57 and keys[0] == "2026-08-06" and keys[-1] == "2026-10-01"


def test_read_failure_is_retryable_503_never_empty_history(client, owner, flags, as_mobile, monkeypatch):
    ref = improving(owner.id)

    def explode(*args, **kwargs):
        raise RuntimeError("SELECT secret FROM workout_session")

    monkeypatch.setattr(queries, "load_history", explode)
    response = get_insight(client, owner, ref, as_mobile)
    assert response.status_code == 503
    error = response.get_json()["error"]
    assert (error["code"], error["retryable"]) == ("TRAINING_SESSION_UNAVAILABLE", True)
    assert response.headers["Session-Resolution"] == "retry"
    assert b"secret" not in response.get_data()


def test_privacy_no_internal_identity_or_free_text(client, owner, flags, as_mobile):
    ref = improving(owner.id)
    raw = get_insight(client, owner, ref, as_mobile).get_data(as_text=True)
    row = WorkoutSession.query.filter_by(public_id=ref).one()
    for forbidden in (f'"id": {row.id}', "user_id", "checkpoint_data", "execution_context",
                      "prescription", "base_anchor", "bound_revision", "note", "pump", "feedback",
                      "Back Squat", "isim"):
        assert forbidden not in raw


def test_metrics_use_only_fixed_dimensions(client, owner, flags, as_mobile, monkeypatch):
    from app.services import runtime_metrics
    calls = []
    monkeypatch.setattr(runtime_metrics, "increment",
                        lambda name, dimensions=None, value=1: calls.append((name, dimensions)))
    ref = improving(owner.id)
    get_insight(client, owner, ref, as_mobile)
    assert ("TrainingInsight", {"Event": "insight_generated"}) in calls
    for name, dimensions in calls:
        if name == "TrainingInsight":
            assert set(dimensions) == {"Event"}


# ── Real native lifecycle feeding the insight ───────────────────────────────
PLAN_LINEAGE, PLAN_VERSION = "ti03-e2e-lineage", 2
THURSDAY_ONE = datetime(2026, 7, 23, 15, 0, tzinfo=APP_TZ)
THURSDAY_TWO = THURSDAY_ONE + timedelta(days=7)


def _plan(user_id):
    def entry(exercise_id):
        return {"exercise_id": exercise_id, "isim": exercise_id, "set": 3, "tekrar": "8-10",
                "dinlenme": "90 sn", "not": "Controlled tempo"}
    days = [{"gun": name, "tip": "dinlenme", "odak": "Recovery", "sure_dk": 0,
             "tahmini_kalori": 0, "egzersizler": []}
            for name in ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]]
    days[3] = {"gun": "Perşembe", "tip": "antrenman", "odak": "Full body", "sure_dk": 45,
               "tahmini_kalori": 300, "egzersizler": [entry(SQUAT), entry(BENCH)]}
    db.session.add(TrainingPlan(user_id=user_id, plan_data=json.dumps({"program": days}),
                                score=8.0, created_at=datetime(2026, 7, 1, 8, 0),
                                lineage_id=PLAN_LINEAGE, mutation_version=PLAN_VERSION))
    db.session.commit()


class _CompletionClock(datetime):
    """``audit_clock`` pins Istanbul app time; completion stamps ``completed_at``
    from ``datetime.utcnow()``. Pin both to the same instant so the test models
    production, where the two clocks agree."""
    moment = None

    @classmethod
    def utcnow(cls):
        return cls.moment


def _workout(client, app, user, as_mobile, when, squat_reps, key):
    from app.services import mobile_training
    _CompletionClock.moment = when.astimezone(timezone.utc).replace(tzinfo=None)
    reference = mobile_training.workout_ref(app.config["SECRET_KEY"], user.id,
                                            PLAN_LINEAGE, PLAN_VERSION, 3)
    headers = as_mobile(user, **{"AxisAI-Workout-Contract": "2"})
    with audit_clock(when):
        started = client.post("/api/v1/training/workout-sessions", headers=headers,
                              json={"workout_ref": reference})
        assert started.status_code == 201, started.get_json()
        ref = started.get_json()["session"]["session_ref"]
        body = {"checkpoint": {"current_exercise_index": 0, "elapsed_seconds": 900, "exercises": [
            {"exercise_id": SQUAT, "sets": [
                {"index": i, "completed": True, "reps": r, "weight_kg": 60.0}
                for i, r in enumerate(squat_reps)]}]},
            "execution_context": {"schema_version": 1, "sets": [
                {"exercise_id": SQUAT, "index": i, "actual_rir": "2", "tempo_adherence": None,
                 "actual_rest": None if i == 0 else {"seconds": 120, "method": "completion_gap",
                                                     "quality": "foreground_contiguous"}}
                for i in range(len(squat_reps))]}}
        saved = client.put(f"/api/v1/training/workout-sessions/{ref}/checkpoint",
                           headers={**headers, "If-Match": "0", "Idempotency-Key": key}, json=body)
        assert saved.status_code == 200, saved.get_json()
        done = client.post(f"/api/v1/training/workout-sessions/{ref}/complete",
                           headers={**headers, "If-Match": "1", "Idempotency-Key": key + "-c"},
                           data={"location_type": "gym", "description": "x"})
        assert done.status_code == 200, done.get_json()
    return ref


def test_real_native_lifecycle_produces_a_comparable_insight(
        client, app, owner, flags, as_mobile, completion_proof, monkeypatch):
    from app.services.workout_completion import service as completion_service
    monkeypatch.setattr(completion_service, "datetime", _CompletionClock)
    _plan(owner.id)
    first = _workout(client, app, owner, as_mobile, THURSDAY_ONE, [8, 8, 8], "ti03-e2e-key-1")
    second = _workout(client, app, owner, as_mobile, THURSDAY_TWO, [9, 9, 8], "ti03-e2e-key-2")
    with audit_clock(THURSDAY_TWO + timedelta(days=3)):
        insight = get_insight(client, owner, second, as_mobile).get_json()["training_insight"]
    assert (insight["state"], insight["kind"], insight["exercise_id"]) == (
        "available", "performance_improved", SQUAT)
    metrics = {item["metric"]: item for item in insight["evidence"]}
    assert (metrics["reps"]["previous"], metrics["reps"]["current"]) == (24, 26)
    assert metrics["reps"]["previous_session_ref"] == first
    assert (metrics["logging_interval_seconds"]["previous"],
            metrics["logging_interval_seconds"]["current"]) == (120, 120)
    assert "rest_method_unsupported" in insight["missing_data"]
    assert insight["recommended_action"] is None
    # Reading at any later time returns the same projection for the same snapshot.
    with audit_clock(THURSDAY_TWO + timedelta(days=30)):
        assert get_insight(client, owner, second, as_mobile).get_json()["training_insight"] == insight
