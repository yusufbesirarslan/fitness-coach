"""Sprint 7 PR3 — flag-conditional workout-state contract (both modes).

The persisted-session lifecycle is exposed through the SAME canonical resolver as
PR1. The contract is flag-conditional (correction #1):

* ``FITX_WORKOUT_SESSIONS_ENABLED`` OFF  ⇒ the resolver returns the EXACT PR1
  ``contract_version=1`` snapshot — identical key set, identical enum vocabulary,
  no ``session`` keys, ``resume``/``in_progress`` never emitted.
* ``FITX_WORKOUT_SESSIONS_ENABLED`` ON   ⇒ ``contract_version=2`` with the additive
  session dimension; ``resume``/``in_progress`` are producible ONLY from a
  persisted ELIGIBLE ACTIVE session, never from evidence-only WorkoutLog rows.

Two layers, matching repo convention:
* the pure v2 projection (``enrich_with_session``) — no DB, deterministic matrix;
* the service (``resolve_workout_state``) with the real flag on/off — proving the
  OFF path stays byte-identical to PR1 and the ON path yields the v2 contract.

    python -m pytest tests/test_workout_state_sessions.py -v
"""
import pytest

from app import models as appm
from app.extensions import db
from app.models import PumpCheck
from app.services.workout_state import models as m
from app.services.workout_state import resolve_workout_state
from app.services.workout_state.resolver import enrich_with_session, resolve
from app.services.workout_session import models as sm
from app.timeutil import app_today

from tests.test_workout_state import STATE_KEYS, _inp, assert_valid_state_contract
from tests.test_workout_session import _save_plan
from app.services.workout_session import start_session


# ── Drift guard: the pure resolver mirrors the ORM status vocabulary BY VALUE ──
def test_session_status_constants_match_orm_by_value():
    """The pure resolver layer imports no ORM (so the state matrix stays DB-free);
    it mirrors the persisted status strings by value. If app.models.WORKOUT_SESSION_*
    ever changes, this guard fails loudly instead of drifting silently."""
    assert m.SESSION_STATUS_ACTIVE == appm.WORKOUT_SESSION_ACTIVE
    assert m.SESSION_STATUS_COMPLETED == appm.WORKOUT_SESSION_COMPLETED
    assert m.SESSION_STATUS_ABANDONED == appm.WORKOUT_SESSION_ABANDONED


# ── Pure v2 projection matrix (enrich_with_session; no DB) ────────────────────
V2_KEYS = STATE_KEYS | {"session_state", "session"}


def _facts(**over):
    base = dict(
        present=True, status=m.SESSION_STATUS_ACTIVE, resumable=True,
        public_id="pub_abc", workout_date=app_today().isoformat(),
        relationship=None, stale_reason=None,
    )
    base.update(over)
    return m.ActiveSessionFacts(**base)


def _base(**over):
    return resolve(_inp(**over))


def test_enrich_advances_contract_and_key_set():
    v2 = enrich_with_session(_base(), m.ACTIVE_SESSION_FACTS_NONE)
    d = v2.to_dict()
    assert d["contract_version"] == m.CONTRACT_VERSION_SESSIONS == 2
    assert set(d) == V2_KEYS


def test_enrich_never_mutates_the_v1_base():
    base = _base()
    before = base.to_dict()
    enrich_with_session(base, _facts())
    # The exact PR1 base object is untouched — the OFF path that skips enrich stays
    # byte-identical.
    assert base.to_dict() == before
    assert base.contract_version == m.CONTRACT_VERSION


def test_active_resumable_yields_resume_in_progress():
    v2 = enrich_with_session(_base(), _facts(resumable=True, relationship=sm.REL_MATCHES_CURRENT_PLAN))
    assert v2.session_state == m.SESSION_STATE_ACTIVE_RESUMABLE
    assert v2.action == m.ACTION_RESUME
    assert v2.execution_state == m.EXEC_IN_PROGRESS
    assert v2.primary_state == m.PRIMARY_IN_PROGRESS
    assert v2.session["public_id"] == "pub_abc"


def test_active_blocked_never_startable_or_resumable():
    v2 = enrich_with_session(_base(), _facts(resumable=False, relationship=sm.REL_PLAN_MISSING,
                                             stale_reason=sm.STALE_PLAN_MISSING))
    assert v2.session_state == m.SESSION_STATE_ACTIVE_BLOCKED
    assert v2.action == m.ACTION_BLOCKED           # never start, never resume
    assert v2.primary_state == m.PRIMARY_NEEDS_ATTENTION


def test_start_never_emitted_when_active_session_exists():
    # Base would offer START (scheduled, nothing done); a resumable ACTIVE session
    # overrides it with RESUME — a conflicting active session blocks a fresh start.
    base = _base()  # scheduled_not_started → action start
    assert base.action == m.ACTION_START
    v2 = enrich_with_session(base, _facts(resumable=True))
    assert v2.action == m.ACTION_RESUME
    assert v2.action != m.ACTION_START


def test_completed_session_status():
    v2 = enrich_with_session(_base(), _facts(status=m.SESSION_STATUS_COMPLETED, resumable=False))
    assert v2.session_state == m.SESSION_STATE_COMPLETED
    assert v2.action != m.ACTION_RESUME


def test_abandoned_session_status():
    v2 = enrich_with_session(_base(), _facts(status=m.SESSION_STATUS_ABANDONED, resumable=False))
    assert v2.session_state == m.SESSION_STATE_ABANDONED


def test_lifecycle_inconsistent_when_completed_today_but_active():
    base = _base(completed_today=True, has_completion_marker_today=True)
    v2 = enrich_with_session(base, _facts(status=m.SESSION_STATUS_ACTIVE, resumable=False))
    assert v2.session_state == m.SESSION_STATE_INCONSISTENT
    assert v2.anomaly == m.ANOMALY_SESSION_INCONSISTENT
    # The day IS done — the completed base action/execution are kept, not "resume".
    assert v2.execution_state == m.EXEC_COMPLETED
    assert v2.action != m.ACTION_RESUME


def test_evidence_without_session_is_labeled_but_never_resumable():
    # Non-marker WorkoutLog rows (evidence only), NO persisted session → labeled
    # execution_without_session, but never resume/in_progress.
    base = _base(real_entry_count_today=3)
    assert base.execution_state == m.EXEC_RECORDED
    v2 = enrich_with_session(base, m.ACTIVE_SESSION_FACTS_NONE)
    assert v2.session_state == m.SESSION_STATE_EXECUTION_WITHOUT_SESSION
    assert v2.session is None
    assert v2.action != m.ACTION_RESUME
    assert v2.execution_state == m.EXEC_RECORDED    # NOT promoted to in_progress


def test_no_session_none_state():
    v2 = enrich_with_session(_base(), m.ACTIVE_SESSION_FACTS_NONE)
    assert v2.session_state == m.SESSION_STATE_NONE
    assert v2.session is None


# ── Service: flag OFF ⇒ exact PR1 v1 contract (byte-identical) ────────────────
def _seed_scheduled(user):
    _save_plan(user.id)  # today is an antrenman


def test_flag_off_is_exact_pr1_contract(app, make_user):
    u = make_user("off1")
    _seed_scheduled(u)
    # Default: flag OFF (config default False).
    snap = resolve_workout_state(u.id, today=app_today())
    d = snap.to_dict()
    assert d["contract_version"] == m.CONTRACT_VERSION == 1
    assert set(d) == STATE_KEYS                   # NO session keys
    assert_valid_state_contract(d)                # PR1 vocab + no `resume`
    assert "session_state" not in d
    assert d["action"] == m.ACTION_START


def test_flag_off_ignores_persisted_sessions(app, make_user):
    # Disabling after sessions already exist is safe: the read contract ignores
    # persisted rows entirely — never deletes them, never emits v2.
    u = make_user("off2")
    _seed_scheduled(u)
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    start_session(u.id)                            # a real ACTIVE session exists
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
    d = resolve_workout_state(u.id, today=app_today()).to_dict()
    assert d["contract_version"] == 1
    assert set(d) == STATE_KEYS
    assert d["action"] == m.ACTION_START           # never resume, session ignored


# ── Service: flag ON ⇒ v2 session-aware contract ─────────────────────────────
@pytest.fixture
def sessions_on(app):
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    yield app
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False


def test_flag_on_no_session_is_v2_with_none(sessions_on, make_user):
    u = make_user("on1")
    _seed_scheduled(u)
    d = resolve_workout_state(u.id, today=app_today()).to_dict()
    assert d["contract_version"] == 2
    assert set(d) == V2_KEYS
    assert d["session_state"] == m.SESSION_STATE_NONE
    assert d["session"] is None
    # No persisted session ⇒ still the PR1 dominant action.
    assert d["action"] == m.ACTION_START


def test_flag_on_active_resumable_emits_resume(sessions_on, make_user):
    u = make_user("on2")
    _seed_scheduled(u)
    started = start_session(u.id)
    d = resolve_workout_state(u.id, today=app_today()).to_dict()
    assert d["contract_version"] == 2
    assert d["session_state"] == m.SESSION_STATE_ACTIVE_RESUMABLE
    assert d["action"] == m.ACTION_RESUME
    assert d["execution_state"] == m.EXEC_IN_PROGRESS
    assert d["primary_state"] == m.PRIMARY_IN_PROGRESS
    assert d["session"]["public_id"] == started.session.public_id
    assert d["session"]["resumable"] is True


def test_flag_on_completed_session_is_reported(sessions_on, make_user):
    u = make_user("on3")
    _seed_scheduled(u)
    from app.services.workout_session import complete_session
    started = start_session(u.id)
    complete_session(u.id, started.session.public_id)
    d = resolve_workout_state(u.id, today=app_today()).to_dict()
    assert d["session_state"] == m.SESSION_STATE_COMPLETED
    assert d["completed_today"] is True
    assert d["action"] != m.ACTION_RESUME


def test_flag_on_evidence_only_never_resumable(sessions_on, make_user):
    # ADVERSARIAL: a non-marker WorkoutLog row (AI-coach style) with NO persisted
    # session must be execution_without_session, never resume — the whole point of
    # PR3 is that a resumable session is a server-owned FACT, not an inference.
    u = make_user("on4")
    _seed_scheduled(u)
    from app.models import WorkoutLog
    from datetime import datetime
    db.session.add(WorkoutLog(user_id=u.id, exercise_name="Deadlift",
                              sets=3, reps=8, weight_kg=100, volume=2400,
                              created_at=datetime.utcnow()))
    db.session.commit()
    d = resolve_workout_state(u.id, today=app_today()).to_dict()
    assert d["session_state"] == m.SESSION_STATE_EXECUTION_WITHOUT_SESSION
    assert d["session"] is None
    assert d["action"] != m.ACTION_RESUME
    assert d["execution_state"] == m.EXEC_RECORDED


def test_flag_on_session_read_failure_fails_safe(sessions_on, make_user, monkeypatch):
    # A session-read error while enriching must not break the read: the base state
    # stays valid, the session dimension is reported empty, and it is logged.
    u = make_user("on5")
    _seed_scheduled(u)
    import app.services.workout_state as ws
    monkeypatch.setattr(
        ws, "load_session_facts",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    d = resolve_workout_state(u.id, today=app_today()).to_dict()
    assert d["contract_version"] == 2              # mode stays consistent with flag
    assert d["session_state"] == m.SESSION_STATE_NONE
    assert d["session"] is None


def test_flag_does_not_alter_completion_truth(app, make_user):
    # Feature flags must not change lifecycle/execution TRUTH — only its exposure.
    # completed_today is identical OFF vs ON for the same seeded state.
    u = make_user("truth")
    _seed_scheduled(u)
    db.session.add(PumpCheck(user_id=u.id, valid=True, date_key=app_today().isoformat()))
    db.session.commit()
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
    off = resolve_workout_state(u.id, today=app_today()).to_dict()
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = True
    on = resolve_workout_state(u.id, today=app_today()).to_dict()
    app.config["FITX_WORKOUT_SESSIONS_ENABLED"] = False
    assert off["completed_today"] is on["completed_today"] is True
    assert off["primary_state"] == on["primary_state"] == m.PRIMARY_COMPLETED
