"""Canonical workout-state contract (Sprint 7 PR1).

Two layers, matching repo convention (see tests/test_training_planning.py):
- Pure resolver matrix — no fixtures, deterministic. Covers the required §10
  state/invariant matrix by constructing ``WorkoutStateInputs`` directly, so the
  full classification is proven without a DB.
- Service + API tests — ``make_user``/``auth_user`` fixtures with a fixed
  ``today``; prove the read-only query layer, no-write guarantee, timezone
  behavior, back-compat and cross-user isolation on ``GET /workout/status``.

    python -m pytest tests/test_workout_state.py -v

Architecture notes captured as executable evidence (spec §10 "document scenarios
that cannot exist"):
- There is NO persisted "started"/active workout session (the client session in
  static/training.js is ephemeral, in-memory only), and non-marker WorkoutLog rows
  can be written outside any interactive flow (AI-coach ``commit_workout_log``).
  Such rows are therefore surfaced as ``execution_recorded`` — RECORDED EXECUTION
  EVIDENCE — and NEVER as a resumable session: the contract exposes no ``resume``
  action. (Sprint 7 PR1 review Finding 2.)
- WorkoutLog carries no planned-workout identifier, so scenario 15 (completed but
  identifier mismatch) is not derivable — association is by local-date + tip only.
"""
import json
from datetime import date, datetime, timedelta

import pytest

from app.extensions import db
from app.models import PumpCheck, TrainingPlan, WORKOUT_COMPLETION_MARKER, WorkoutLog
from app.services.training_generation.response_validator import WEEKDAYS
from app.services import workout_state as ws
from app.services.workout_state import models as m
from app.services.workout_state import resolve_workout_state
from app.services.workout_state.resolver import resolve
from app.timeutil import app_today

# A fixed Thursday ("Perşembe"); noon UTC → 15:00 Istanbul, safely inside the day.
TODAY = date(2026, 7, 23)
YESTERDAY = TODAY - timedelta(days=1)
NOON_TODAY = datetime(2026, 7, 23, 12, 0, 0)
NOON_YESTERDAY = datetime(2026, 7, 22, 12, 0, 0)

# The complete, closed vocabularies of the contract — used to prove no value ever
# escapes them and (Finding 2) that ``resume`` is not part of the action alphabet.
VALID_ACTIONS = {m.ACTION_START, m.ACTION_NONE, m.ACTION_BLOCKED}
VALID_PRIMARY = {
    m.PRIMARY_REST_DAY, m.PRIMARY_SCHEDULED_NOT_STARTED, m.PRIMARY_EXECUTION_RECORDED,
    m.PRIMARY_COMPLETED, m.PRIMARY_UNSCHEDULED_EXECUTION,
    m.PRIMARY_UNSCHEDULED_COMPLETED, m.PRIMARY_NO_PLAN, m.PRIMARY_NEEDS_ATTENTION,
}
VALID_SCHEDULE = {m.SCHEDULE_SCHEDULED, m.SCHEDULE_REST_DAY, m.SCHEDULE_NO_PLAN,
                  m.SCHEDULE_UNAVAILABLE}
VALID_EXECUTION = {m.EXEC_NONE, m.EXEC_RECORDED, m.EXEC_COMPLETED}
VALID_RELATIONSHIP = {m.REL_MATCHES_SCHEDULED, m.REL_UNSCHEDULED,
                      m.REL_UNRELATED_DATE, m.REL_INDETERMINATE}
STATE_KEYS = {
    "contract_version", "today", "schedule_state", "execution_state",
    "plan_relationship", "action", "primary_state", "completed_today",
    "is_rest_day", "stale_previous_workout", "anomaly"}


def assert_valid_state_contract(state):
    """Strict shape + closed-vocabulary check for a serialized snapshot.

    Enforces the full public key set (no field removed/added), that every
    enum-valued field stays inside its closed vocabulary, and — per review
    Finding 2 — that ``action`` can never be ``resume``.
    """
    assert set(state) == STATE_KEYS
    assert state["contract_version"] == m.CONTRACT_VERSION
    assert state["schedule_state"] in VALID_SCHEDULE
    assert state["execution_state"] in VALID_EXECUTION
    assert state["plan_relationship"] in VALID_RELATIONSHIP
    assert state["action"] in VALID_ACTIONS
    assert state["action"] != "resume"
    assert state["primary_state"] in VALID_PRIMARY
    assert isinstance(state["completed_today"], bool)
    assert isinstance(state["is_rest_day"], bool)
    assert isinstance(state["stale_previous_workout"], bool)


def _inp(**over):
    """Build ``WorkoutStateInputs`` with sane 'active plan, nothing done' defaults."""
    base = dict(
        today=TODAY, has_plan=True, schedule_valid=True,
        today_schedule_kind=m.KIND_WORKOUT, completed_today=False,
        has_completion_marker_today=False, real_entry_count_today=0,
        stale_previous_workout=False,
    )
    base.update(over)
    return m.WorkoutStateInputs(**base)


# ── §10 required state & invariant matrix (pure resolver) ────────────────────
# Each row: (id, input overrides, expected (schedule, execution, relationship,
# action, primary)).
MATRIX = [
    ("01_scheduled_not_started",
     dict(),
     (m.SCHEDULE_SCHEDULED, m.EXEC_NONE, m.REL_INDETERMINATE,
      m.ACTION_START, m.PRIMARY_SCHEDULED_NOT_STARTED)),
    ("02_scheduled_evidence",  # non-marker rows today ≠ resumable session (Finding 2)
     dict(real_entry_count_today=2),
     (m.SCHEDULE_SCHEDULED, m.EXEC_RECORDED, m.REL_MATCHES_SCHEDULED,
      m.ACTION_NONE, m.PRIMARY_EXECUTION_RECORDED)),
    ("03_scheduled_evidence_single",
     dict(real_entry_count_today=1),
     (m.SCHEDULE_SCHEDULED, m.EXEC_RECORDED, m.REL_MATCHES_SCHEDULED,
      m.ACTION_NONE, m.PRIMARY_EXECUTION_RECORDED)),
    ("04_scheduled_completed",
     dict(completed_today=True, has_completion_marker_today=True),
     (m.SCHEDULE_SCHEDULED, m.EXEC_COMPLETED, m.REL_MATCHES_SCHEDULED,
      m.ACTION_NONE, m.PRIMARY_COMPLETED)),
    ("05_rest_no_workout",
     dict(today_schedule_kind=m.KIND_REST),
     (m.SCHEDULE_REST_DAY, m.EXEC_NONE, m.REL_INDETERMINATE,
      m.ACTION_NONE, m.PRIMARY_REST_DAY)),
    ("06_rest_unscheduled_completed",
     dict(today_schedule_kind=m.KIND_REST, completed_today=True,
          has_completion_marker_today=True),
     (m.SCHEDULE_REST_DAY, m.EXEC_COMPLETED, m.REL_UNSCHEDULED,
      m.ACTION_NONE, m.PRIMARY_UNSCHEDULED_COMPLETED)),
    ("07_rest_unscheduled_evidence",
     dict(today_schedule_kind=m.KIND_REST, real_entry_count_today=2),
     (m.SCHEDULE_REST_DAY, m.EXEC_RECORDED, m.REL_UNSCHEDULED,
      m.ACTION_NONE, m.PRIMARY_UNSCHEDULED_EXECUTION)),
    ("08_no_plan",
     dict(has_plan=False, schedule_valid=False, today_schedule_kind=None),
     (m.SCHEDULE_NO_PLAN, m.EXEC_NONE, m.REL_INDETERMINATE,
      m.ACTION_NONE, m.PRIMARY_NO_PLAN)),
    ("09_plan_malformed_schedule",
     dict(schedule_valid=False, today_schedule_kind=None),
     (m.SCHEDULE_UNAVAILABLE, m.EXEC_NONE, m.REL_INDETERMINATE,
      m.ACTION_BLOCKED, m.PRIMARY_NEEDS_ATTENTION)),
    ("10_latest_workout_yesterday",  # yesterday completed → not stale; today clean
     dict(completed_today=False, stale_previous_workout=False),
     (m.SCHEDULE_SCHEDULED, m.EXEC_NONE, m.REL_INDETERMINATE,
      m.ACTION_START, m.PRIMARY_SCHEDULED_NOT_STARTED)),
    ("11_previous_day_incomplete",
     dict(stale_previous_workout=True),
     (m.SCHEDULE_SCHEDULED, m.EXEC_NONE, m.REL_UNRELATED_DATE,
      m.ACTION_START, m.PRIMARY_SCHEDULED_NOT_STARTED)),
    ("12_multiple_records_same_day",  # multiplicity = presence, never "newest wins"
     dict(real_entry_count_today=4),
     (m.SCHEDULE_SCHEDULED, m.EXEC_RECORDED, m.REL_MATCHES_SCHEDULED,
      m.ACTION_NONE, m.PRIMARY_EXECUTION_RECORDED)),
    ("13_completed_no_plan_relationship",
     dict(has_plan=False, schedule_valid=False, today_schedule_kind=None,
          completed_today=True, has_completion_marker_today=True),
     (m.SCHEDULE_NO_PLAN, m.EXEC_COMPLETED, m.REL_UNSCHEDULED,
      m.ACTION_NONE, m.PRIMARY_UNSCHEDULED_COMPLETED)),
    ("14_marker_without_execution",  # marker row but no PumpCheck, no real rows
     dict(has_completion_marker_today=True),
     (m.SCHEDULE_SCHEDULED, m.EXEC_NONE, m.REL_INDETERMINATE,
      m.ACTION_START, m.PRIMARY_SCHEDULED_NOT_STARTED)),
    ("16_local_date_differs_from_utc",  # resolved purely; DB timezone test below
     dict(completed_today=True, has_completion_marker_today=True),
     (m.SCHEDULE_SCHEDULED, m.EXEC_COMPLETED, m.REL_MATCHES_SCHEDULED,
      m.ACTION_NONE, m.PRIMARY_COMPLETED)),
    ("20_history_but_no_today",
     dict(stale_previous_workout=False),
     (m.SCHEDULE_SCHEDULED, m.EXEC_NONE, m.REL_INDETERMINATE,
      m.ACTION_START, m.PRIMARY_SCHEDULED_NOT_STARTED)),
]


@pytest.mark.parametrize("case_id,over,expected", MATRIX, ids=[c[0] for c in MATRIX])
def test_state_matrix(case_id, over, expected):
    snap = resolve(_inp(**over))
    got = (snap.schedule_state, snap.execution_state, snap.plan_relationship,
           snap.action, snap.primary_state)
    assert got == expected


@pytest.mark.parametrize("case_id,over,expected", MATRIX, ids=[c[0] for c in MATRIX])
def test_matrix_invariants(case_id, over, expected):
    snap = resolve(_inp(**over))
    # Full closed-vocabulary + key-set contract holds for every scenario, and the
    # `resume` action is never emitted (Finding 2).
    assert_valid_state_contract(snap.to_dict())
    # Execution is COMPLETED only when completion is proven (PumpCheck).
    assert (snap.execution_state == m.EXEC_COMPLETED) == snap.completed_today
    # Recorded evidence (no completion) is never a resumable/active session:
    # it grants no `resume` and is never reported as completed.
    if snap.execution_state == m.EXEC_RECORDED:
        assert snap.completed_today is False
        assert snap.action == m.ACTION_NONE
    # Malformed schedule never grants an action (Finding 4d).
    if snap.schedule_state == m.SCHEDULE_UNAVAILABLE:
        assert snap.action == m.ACTION_BLOCKED
        assert snap.primary_state == m.PRIMARY_NEEDS_ATTENTION
    # is_rest_day mirrors the schedule dimension coherently.
    assert snap.is_rest_day == (snap.schedule_state == m.SCHEDULE_REST_DAY)


def test_execution_evidence_never_completed_or_resumable():
    # Non-marker rows with no PumpCheck = recorded evidence, not a done or
    # resumable session (Finding 2).
    snap = resolve(_inp(real_entry_count_today=5, completed_today=False))
    assert snap.execution_state == m.EXEC_RECORDED
    assert snap.primary_state == m.PRIMARY_EXECUTION_RECORDED
    assert snap.completed_today is False
    assert snap.action == m.ACTION_NONE          # never `resume`
    assert snap.action != "resume"


def test_contract_has_no_resume_action():
    # `resume` is not part of the action alphabet at all — the server persists no
    # resumable session, so it can never be offered (Finding 2).
    assert not hasattr(m, "ACTION_RESUME")
    assert "resume" not in VALID_ACTIONS


def test_no_scenario_ever_emits_resume():
    # Exhaustive sweep over the whole input space: no combination yields `resume`.
    kinds = (m.KIND_WORKOUT, m.KIND_REST, None)
    for has_plan in (True, False):
        for valid in (True, False):
            for kind in kinds:
                for completed in (True, False):
                    for marker in (True, False):
                        for real in (0, 1, 3):
                            for stale in (True, False):
                                snap = resolve(_inp(
                                    has_plan=has_plan, schedule_valid=valid,
                                    today_schedule_kind=kind, completed_today=completed,
                                    has_completion_marker_today=marker,
                                    real_entry_count_today=real,
                                    stale_previous_workout=stale))
                                assert snap.action in VALID_ACTIONS
                                assert snap.action != "resume"


def test_completion_outranks_real_rows():
    # Completed session with logged exercises stays COMPLETED, not in_progress.
    snap = resolve(_inp(real_entry_count_today=5, completed_today=True,
                        has_completion_marker_today=True))
    assert snap.execution_state == m.EXEC_COMPLETED


def test_rest_day_does_not_erase_unscheduled_workout():
    snap = resolve(_inp(today_schedule_kind=m.KIND_REST, completed_today=True,
                        has_completion_marker_today=True))
    assert snap.is_rest_day is True
    assert snap.plan_relationship == m.REL_UNSCHEDULED
    assert snap.primary_state == m.PRIMARY_UNSCHEDULED_COMPLETED


def test_historical_record_is_not_todays_workout():
    # Stale previous-day work + nothing today → today is not completed/in-progress.
    snap = resolve(_inp(stale_previous_workout=True))
    assert snap.execution_state == m.EXEC_NONE
    assert snap.completed_today is False
    assert snap.plan_relationship == m.REL_UNRELATED_DATE


def test_marker_without_pumpcheck_is_anomaly_not_completed():
    snap = resolve(_inp(has_completion_marker_today=True, completed_today=False))
    assert snap.execution_state == m.EXEC_NONE
    assert snap.anomaly == m.ANOMALY_COMPLETION_MARKER_MISMATCH


def test_malformed_schedule_is_anomaly_and_blocked():
    snap = resolve(_inp(schedule_valid=False, today_schedule_kind=None))
    assert snap.anomaly == m.ANOMALY_SCHEDULE_UNPARSEABLE
    assert snap.action == m.ACTION_BLOCKED


def test_no_plan_is_not_an_anomaly():
    snap = resolve(_inp(has_plan=False, schedule_valid=False, today_schedule_kind=None))
    assert snap.anomaly is None
    assert snap.primary_state == m.PRIMARY_NO_PLAN


def test_repeated_resolution_is_deterministic():
    i = _inp(real_entry_count_today=2)
    assert resolve(i) == resolve(i)
    assert resolve(i).to_dict() == resolve(i).to_dict()


def test_to_dict_is_stable_and_serializable():
    d = resolve(_inp()).to_dict()
    assert d["contract_version"] == m.CONTRACT_VERSION
    assert d["today"] == TODAY.isoformat()
    # JSON-serializable (no dataclasses/dates leak through).
    assert json.loads(json.dumps(d))["primary_state"] == m.PRIMARY_SCHEDULED_NOT_STARTED


# ── Structural guards: read-only, no AI/provider dependency ───────────────────
def test_package_has_no_write_or_provider_imports():
    """AST-level proof the package imports no provider/AI/write module and issues
    no ``db.session`` write call.

    Parses the AST (not a raw substring scan) so it verifies real *dependencies*
    and *calls*, never false-flagging a documentation comment that merely names
    ``ai_coach.py`` — while still failing hard on any genuine ``import openai`` /
    ``db.session.add(...)`` etc.
    """
    import ast
    import pathlib

    pkg = pathlib.Path(ws.__file__).parent
    forbidden_import = ("openai", "boto3", "bedrock", "botocore",
                        "ai_coach", "ai_stream", "requests", "httpx")
    forbidden_write_attrs = {"add", "commit", "flush", "delete", "merge",
                             "bulk_save_objects", "execute"}

    for src in pkg.glob("*.py"):
        tree = ast.parse(src.read_text(encoding="utf-8"), filename=str(src))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(f in alias.name for f in forbidden_import), \
                        f"{src.name} imports forbidden module {alias.name!r}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not any(f in mod for f in forbidden_import), \
                    f"{src.name} imports from forbidden module {mod!r}"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                fn = node.func
                # Catch `db.session.<write>(...)` and `session.<write>(...)`.
                if fn.attr in forbidden_write_attrs:
                    val = fn.value
                    is_session = (
                        (isinstance(val, ast.Attribute) and val.attr == "session")
                        or (isinstance(val, ast.Name) and val.id == "session")
                    )
                    assert not is_session, \
                        f"{src.name} performs a write: session.{fn.attr}("


# ── Service layer (DB-backed, fixed `today`) ─────────────────────────────────
def _program(today, today_tip):
    """A valid 7-day program: all rest except today's weekday gets ``today_tip``."""
    today_name = WEEKDAYS[today.weekday()]
    days = [{"gun": name, "tip": (today_tip if name == today_name else "dinlenme"),
             "egzersizler": []} for name in WEEKDAYS]
    return {"program": days, "haftalik_ozet": {}}


def _save_plan(user_id, today_tip="antrenman", today=TODAY, raw=None):
    plan_data = raw if raw is not None else json.dumps(_program(today, today_tip))
    db.session.add(TrainingPlan(user_id=user_id, plan_data=plan_data, score=5))
    db.session.commit()


def _add_workout(user_id, when=NOON_TODAY, marker=False, name="Squat"):
    db.session.add(WorkoutLog(
        user_id=user_id,
        exercise_name=(WORKOUT_COMPLETION_MARKER if marker else name),
        sets=3, reps=10, weight_kg=50, volume=(0 if marker else 1500),
        created_at=when))
    db.session.commit()


def _add_pump(user_id, when=NOON_TODAY, day_key=None):
    db.session.add(PumpCheck(user_id=user_id, valid=True,
                             date_key=(day_key or when.date().isoformat()),
                             created_at=when))
    db.session.commit()


def test_service_scheduled_not_started(app, make_user):
    u = make_user("svc_sched")
    _save_plan(u.id, "antrenman")
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.primary_state == m.PRIMARY_SCHEDULED_NOT_STARTED
    assert snap.action == m.ACTION_START


def test_service_rest_day(app, make_user):
    u = make_user("svc_rest")
    _save_plan(u.id, "dinlenme")
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.primary_state == m.PRIMARY_REST_DAY
    assert snap.is_rest_day is True


def test_service_execution_evidence_from_real_rows(app, make_user):
    u = make_user("svc_prog")
    _save_plan(u.id, "antrenman")
    _add_workout(u.id, NOON_TODAY, marker=False)
    _add_workout(u.id, NOON_TODAY, marker=False, name="Bench")
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.execution_state == m.EXEC_RECORDED
    assert snap.primary_state == m.PRIMARY_EXECUTION_RECORDED
    assert snap.action == m.ACTION_NONE          # never `resume`
    assert snap.completed_today is False


def test_service_ai_coach_logged_exercise_is_evidence_not_resumable(app, make_user):
    # ADVERSARIAL (Finding 2): the AI-coach `commit_workout_log` tool writes a
    # single non-marker WorkoutLog row with NO PumpCheck and NO marker — created
    # entirely outside the interactive workout flow. The contract must treat it as
    # recorded execution evidence, never as a completed or resumable session.
    u = make_user("svc_ai_log")
    _save_plan(u.id, "antrenman")
    _add_workout(u.id, NOON_TODAY, marker=False, name="Deadlift")  # AI-coach style row
    assert PumpCheck.query.filter_by(user_id=u.id).count() == 0    # no completion proof
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.execution_state == m.EXEC_RECORDED
    assert snap.completed_today is False
    assert snap.action == m.ACTION_NONE
    assert snap.action != "resume"
    assert snap.primary_state == m.PRIMARY_EXECUTION_RECORDED


def test_service_marker_without_pumpcheck_is_not_completion(app, make_user):
    # ADVERSARIAL (Finding 3): a completion MARKER row alone, with no PumpCheck,
    # must not be read as completion (PumpCheck is the sole completion authority);
    # the discrepancy is surfaced as an anomaly, not silently trusted.
    u = make_user("svc_marker_only")
    _save_plan(u.id, "antrenman")
    _add_workout(u.id, NOON_TODAY, marker=True)   # marker but NO PumpCheck
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.completed_today is False
    assert snap.execution_state == m.EXEC_NONE    # marker is excluded from evidence
    assert snap.anomaly == m.ANOMALY_COMPLETION_MARKER_MISMATCH


def test_service_completed_via_pumpcheck(app, make_user):
    u = make_user("svc_done")
    _save_plan(u.id, "antrenman")
    _add_pump(u.id, NOON_TODAY)
    _add_workout(u.id, NOON_TODAY, marker=True)  # paired marker (real writers do this)
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.completed_today is True
    assert snap.execution_state == m.EXEC_COMPLETED
    assert snap.primary_state == m.PRIMARY_COMPLETED


def test_service_no_plan(app, make_user):
    u = make_user("svc_noplan")
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.primary_state == m.PRIMARY_NO_PLAN


def test_service_yesterday_completion_not_counted_today(app, make_user):
    u = make_user("svc_yest")
    _save_plan(u.id, "antrenman")
    _add_pump(u.id, NOON_YESTERDAY)
    _add_workout(u.id, NOON_YESTERDAY, marker=True)
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.completed_today is False
    assert snap.execution_state == m.EXEC_NONE
    assert snap.stale_previous_workout is False  # yesterday completed → not stale


def test_service_stale_previous_workout(app, make_user):
    u = make_user("svc_stale")
    _save_plan(u.id, "antrenman")
    _add_workout(u.id, NOON_YESTERDAY, marker=False)  # real rows, no completion
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.stale_previous_workout is True
    assert snap.plan_relationship == m.REL_UNRELATED_DATE
    assert snap.execution_state == m.EXEC_NONE  # not today's workout


def test_service_timezone_boundary_counts_as_today(app, make_user):
    # 23:00 UTC on YESTERDAY = 02:00 Istanbul TODAY → completed_today must be True.
    u = make_user("svc_tz")
    _save_plan(u.id, "antrenman")
    _add_pump(u.id, datetime(2026, 7, 22, 23, 0, 0))
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.completed_today is True


def test_service_malformed_plan_is_safe(app, make_user):
    u = make_user("svc_bad")
    _save_plan(u.id, raw="this-is-not-json{")
    snap = resolve_workout_state(u.id, today=TODAY)
    assert snap.schedule_state == m.SCHEDULE_UNAVAILABLE
    assert snap.action == m.ACTION_BLOCKED
    assert snap.primary_state == m.PRIMARY_NEEDS_ATTENTION


def test_service_performs_no_writes(app, make_user):
    u = make_user("svc_nowrite")
    _save_plan(u.id, "antrenman")
    _add_workout(u.id, NOON_TODAY, marker=False)
    before = (WorkoutLog.query.count(), PumpCheck.query.count(),
              TrainingPlan.query.count())
    resolve_workout_state(u.id, today=TODAY)
    after = (WorkoutLog.query.count(), PumpCheck.query.count(),
             TrainingPlan.query.count())
    assert before == after
    # No pending mutations queued on the session either.
    assert not db.session.new and not db.session.dirty and not db.session.deleted


def test_service_scoped_to_user(app, make_user):
    a = make_user("iso_a")
    b = make_user("iso_b")
    _save_plan(a.id, "dinlenme")           # A: rest, nothing done
    _save_plan(b.id, "antrenman")
    _add_pump(b.id, NOON_TODAY)            # B: completed
    _add_workout(b.id, NOON_TODAY, marker=True)
    snap_a = resolve_workout_state(a.id, today=TODAY)
    assert snap_a.completed_today is False
    assert snap_a.primary_state == m.PRIMARY_REST_DAY


# ── API contract (GET /workout/status) ───────────────────────────────────────
# The route resolves for the real app_today(); seed against it (created_at=now)
# so these tests are date-independent — app_date_of(utcnow()) == app_today().
def test_api_status_backcompat_and_additive(client, auth_user):
    # Strict contract (Finding 4): exact top-level shape (legacy `completed`
    # preserved, no unintended field), full state key set, closed enum vocabularies.
    _save_plan(auth_user.id, "antrenman", today=app_today())
    resp = client.get("/workout/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) == {"completed", "state"}                  # no field removed/added
    assert body["completed"] is False                           # preserved legacy field
    state = body["state"]
    assert_valid_state_contract(state)                          # keys + enum vocab + no `resume`
    assert state["today"] == app_today().isoformat()
    assert state["completed_today"] is body["completed"]        # legacy mirrors snapshot
    # Fresh scheduled plan, nothing done → deterministic dominant state/action.
    assert state["schedule_state"] == m.SCHEDULE_SCHEDULED
    assert state["execution_state"] == m.EXEC_NONE
    assert state["primary_state"] == m.PRIMARY_SCHEDULED_NOT_STARTED
    assert state["action"] == m.ACTION_START


def test_api_status_completed_contract_is_strict(client, auth_user):
    # Strict contract on the completed transition (Finding 4): exact shape holds and
    # `completed`/state stay consistent after a real completion (PumpCheck + marker).
    _save_plan(auth_user.id, "antrenman", today=app_today())
    _add_pump(auth_user.id, datetime.utcnow())
    _add_workout(auth_user.id, datetime.utcnow(), marker=True)
    body = client.get("/workout/status").get_json()
    assert set(body) == {"completed", "state"}
    assert body["completed"] is True
    state = body["state"]
    assert_valid_state_contract(state)
    assert state["completed_today"] is True
    assert state["execution_state"] == m.EXEC_COMPLETED
    assert state["primary_state"] == m.PRIMARY_COMPLETED
    assert state["action"] == m.ACTION_NONE


def test_api_status_requires_auth(client):
    # No login → require_auth must not serve the state (401/redirect, never 200).
    assert client.get("/workout/status").status_code != 200


def test_api_status_malformed_plan_not_500(client, auth_user):
    _save_plan(auth_user.id, raw="{bad json")
    resp = client.get("/workout/status")
    assert resp.status_code == 200
    assert resp.get_json()["state"]["primary_state"] == m.PRIMARY_NEEDS_ATTENTION


@pytest.mark.parametrize("weekly_ui,adaptive", [(True, "1"), (False, "0"), (True, "0")])
def test_api_status_stable_across_feature_flags(client, auth_user, monkeypatch, weekly_ui, adaptive):
    # Flags for unrelated features must not alter execution truth (spec §10 17-19).
    from flask import current_app
    now = datetime.utcnow()
    current_app.config["WEEKLY_PROGRAM_UI_ENABLED"] = weekly_ui
    monkeypatch.setenv("AI_ADAPTIVE_PLAN_CONTEXT", adaptive)
    _save_plan(auth_user.id, "antrenman", today=app_today())
    _add_pump(auth_user.id, now)
    _add_workout(auth_user.id, now, marker=True)
    body = client.get("/workout/status").get_json()
    assert body["completed"] is True
    assert body["state"]["primary_state"] == m.PRIMARY_COMPLETED
