"""Sprint 7 PR3 — persisted workout-session lifecycle.

Deterministic, hermetic (in-memory SQLite) coverage of the server-owned session
lifecycle: the pure relationship/stale/fingerprint classification (no DB), the
DB-level active-owner invariant, and the transaction-owning service (start /
idempotent replay / conflict / current / resume eligibility / replay-idempotent
heartbeat / abandon / completion delegation), plus ownership isolation, the
required acceptance matrix, fault-injection rollback and the fail-closed
verify-or-create migration.

The opt-in real-concurrency proof lives in ``test_workout_session_pg.py``
(Postgres, marker + env gated). The flag-conditional v2 read contract lives in
``test_workout_state_sessions.py``; the completion↔session reconciliation lives in
``test_workout_completion.py``.

    python -m pytest tests/test_workout_session.py -v
"""
import importlib.util
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Activity,
    PumpCheck,
    TrainingPlan,
    User,
    WORKOUT_COMPLETION_MARKER,
    WORKOUT_SESSION_ABANDONED,
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
    WorkoutLog,
    WorkoutSession,
)
from app.services.training_generation.response_validator import WEEKDAYS
from app.services.workout_session import (
    FINGERPRINT_VERSION,
    SessionOutcome,
    abandon_session,
    checkpoint_session,
    complete_session,
    compute_fingerprint,
    get_current_session,
    read_session_for_state,
    resolve_for_completion,
    resume_session,
    start_session,
)
from app.services.workout_session import models as sm
from app.services.workout_session import queries as wq
from app.services.workout_session import service as wservice
from app.timeutil import app_today


# ── Pure classification (no DB, no Flask) ────────────────────────────────────
class TestFingerprint:
    def test_versioned_and_order_preserving(self):
        fp = compute_fingerprint(["Squat", "Bench"])
        assert fp.startswith(f"{FINGERPRINT_VERSION}:")
        # Order matters — a reordered list is a different intended workout.
        assert compute_fingerprint(["Squat", "Bench"]) != compute_fingerprint(["Bench", "Squat"])

    def test_case_insensitive_but_content_sensitive(self):
        assert compute_fingerprint(["Squat"]) == compute_fingerprint(["squat"])
        assert compute_fingerprint(["Squat"]) != compute_fingerprint(["Deadlift"])

    def test_deterministic(self):
        assert compute_fingerprint(["A", "B", "C"]) == compute_fingerprint(["A", "B", "C"])

    def test_match_true_only_same_version(self):
        a = compute_fingerprint(["Squat"])
        assert sm.fingerprints_match(a, a) is True
        assert sm.fingerprints_match(a, compute_fingerprint(["Bench"])) is False

    def test_version_mismatch_is_undecidable_never_silent_match(self):
        # A future v2 digest can never be silently treated as matching a stored v1.
        stored = "v1:" + ("a" * 64)
        current = "v2:" + ("a" * 64)  # same digest bytes, different algorithm version
        assert sm.fingerprints_match(stored, current) is None
        # And a missing/malformed fingerprint is undecidable, not a match.
        assert sm.fingerprints_match(None, current) is None
        assert sm.fingerprints_match(stored, "v1:") is None


class TestClassifyRelationship:
    def _rel(self, **over):
        base = dict(
            source=sm.SOURCE_SCHEDULED, has_current_plan=True,
            current_weekday_is_workout=True,
            stored_fingerprint="v1:x", current_fingerprint="v1:x",
        )
        base.update(over)
        return sm.classify_relationship(**base)

    def test_matches_current_plan(self):
        assert self._rel() == sm.REL_MATCHES_CURRENT_PLAN

    def test_unscheduled_short_circuits(self):
        assert self._rel(source=sm.SOURCE_UNSCHEDULED) == sm.REL_UNSCHEDULED

    def test_plan_missing(self):
        assert self._rel(has_current_plan=False) == sm.REL_PLAN_MISSING

    def test_schedule_slot_changed(self):
        assert self._rel(current_weekday_is_workout=False) == sm.REL_SCHEDULE_SLOT_CHANGED

    def test_regenerated_on_different_fingerprint(self):
        assert self._rel(current_fingerprint="v1:y") == sm.REL_PLAN_REGENERATED

    def test_version_mismatch_is_indeterminate_not_regenerated(self):
        # The whole point of the versioned fingerprint: an algorithm/version
        # mismatch is classified safely (indeterminate), NEVER a silent match and
        # NEVER a confident "regenerated".
        assert self._rel(stored_fingerprint="v2:x") == sm.REL_INDETERMINATE

    def test_malformed_fingerprint_is_indeterminate(self):
        assert self._rel(stored_fingerprint=None) == sm.REL_INDETERMINATE


class TestClassifyActiveSession:
    """The (relationship, stale_reason, resumable) matrix over an ACTIVE session.
    Elapsed inactivity is NEVER an input — stale is evidence-only (correction #6)."""

    def _inp(self, **over):
        base = dict(
            status=WORKOUT_SESSION_ACTIVE, workout_date=app_today().isoformat(),
            source=sm.SOURCE_SCHEDULED, stored_fingerprint="v1:x", today=app_today(),
            completed_today=False, has_current_plan=True,
            current_weekday_is_workout=True, current_fingerprint="v1:x",
        )
        base.update(over)
        return sm.RelationshipInputs(**base)

    def test_matches_current_plan_is_resumable(self):
        c = sm.classify(self._inp())
        assert (c.relationship, c.stale_reason, c.resumable) == (
            sm.REL_MATCHES_CURRENT_PLAN, sm.STALE_NONE, True)

    def test_unscheduled_is_resumable(self):
        c = sm.classify(self._inp(source=sm.SOURCE_UNSCHEDULED))
        assert c.resumable is True
        assert c.stale_reason == sm.STALE_NONE

    def test_completion_today_is_lifecycle_inconsistent(self):
        # A confirmed same-day completion with a still-ACTIVE session blocks resume
        # BEFORE plan-relationship is even consulted.
        c = sm.classify(self._inp(completed_today=True))
        assert c.stale_reason == sm.STALE_LIFECYCLE_INCONSISTENT
        assert c.resumable is False

    def test_previous_day_blocks_resume(self):
        c = sm.classify(self._inp(workout_date=(app_today() - timedelta(days=1)).isoformat()))
        assert c.stale_reason == sm.STALE_PREVIOUS_DAY
        assert c.resumable is False

    def test_plan_missing_blocks_resume(self):
        c = sm.classify(self._inp(has_current_plan=False))
        assert (c.relationship, c.stale_reason, c.resumable) == (
            sm.REL_PLAN_MISSING, sm.STALE_PLAN_MISSING, False)

    def test_regenerated_blocks_resume(self):
        c = sm.classify(self._inp(current_fingerprint="v1:other"))
        assert (c.relationship, c.stale_reason, c.resumable) == (
            sm.REL_PLAN_REGENERATED, sm.STALE_PLAN_REGENERATED, False)

    def test_schedule_slot_changed_blocks_resume(self):
        c = sm.classify(self._inp(current_weekday_is_workout=False))
        assert (c.relationship, c.stale_reason, c.resumable) == (
            sm.REL_SCHEDULE_SLOT_CHANGED, sm.STALE_SCHEDULE_SLOT_CHANGED, False)

    def test_version_mismatch_blocks_resume_as_indeterminate(self):
        c = sm.classify(self._inp(stored_fingerprint="v2:x"))
        assert (c.relationship, c.stale_reason, c.resumable) == (
            sm.REL_INDETERMINATE, sm.STALE_INDETERMINATE, False)

    def test_no_inactivity_input_exists(self):
        # There is deliberately no last_activity/elapsed field on the classifier —
        # a same-day matching session is resumable regardless of how long it idled.
        assert "last_activity" not in sm.RelationshipInputs.__annotations__
        assert "elapsed" not in sm.RelationshipInputs.__annotations__


# ── DB helpers ───────────────────────────────────────────────────────────────
def _today_weekday():
    return WEEKDAYS[app_today().weekday()]


def _program(workout_weekday, exercises):
    days = []
    for name in WEEKDAYS:
        if name == workout_weekday:
            days.append({"gun": name, "tip": "antrenman",
                         "egzersizler": [{"isim": e} for e in exercises]})
        else:
            days.append({"gun": name, "tip": "dinlenme", "egzersizler": []})
    return {"program": days, "haftalik_ozet": {}}


def _save_plan(user_id, exercises=("Squat", "Bench"), *, weekday=None,
               created_at=None, raw=None):
    """Persist a plan whose ``weekday`` (default today) is an antrenman."""
    weekday = weekday or _today_weekday()
    data = raw if raw is not None else json.dumps(_program(weekday, exercises))
    plan = TrainingPlan(user_id=user_id, plan_data=data, score=5)
    if created_at is not None:
        plan.created_at = created_at
    db.session.add(plan)
    db.session.commit()
    return plan


def _mk_session(user_id, *, status=WORKOUT_SESSION_ACTIVE, workout_date=None,
                weekday_slot="__default__", source=sm.SOURCE_SCHEDULED,
                fingerprint=None, started_at=None, last_activity_at=None):
    now = datetime.utcnow()
    s = WorkoutSession(
        public_id=secrets.token_urlsafe(16),
        user_id=user_id,
        status=status,
        workout_date=workout_date or app_today().isoformat(),
        weekday_slot=(_today_weekday() if weekday_slot == "__default__" else weekday_slot),
        source=source,
        plan_fingerprint=fingerprint,
        started_at=started_at or now,
        last_activity_at=last_activity_at or now,
        version=1,
    )
    db.session.add(s)
    db.session.commit()
    return s


# ── DB-level active-owner invariant ──────────────────────────────────────────
def test_partial_index_forbids_two_active_sessions(app, make_user):
    u = make_user("inv")
    _mk_session(u.id)
    with pytest.raises(IntegrityError):
        _mk_session(u.id)
    db.session.rollback()


def test_terminal_row_vacates_the_active_slot(app, make_user):
    u = make_user("inv2")
    _mk_session(u.id, status=WORKOUT_SESSION_COMPLETED)
    _mk_session(u.id, status=WORKOUT_SESSION_ABANDONED)
    # A terminal row does not occupy the active slot → a new ACTIVE row is allowed.
    _mk_session(u.id, status=WORKOUT_SESSION_ACTIVE)
    assert WorkoutSession.query.filter_by(
        user_id=u.id, status=WORKOUT_SESSION_ACTIVE).count() == 1


def test_two_users_may_each_have_an_active_session(app, make_user):
    a, b = make_user("a"), make_user("b")
    _mk_session(a.id)
    _mk_session(b.id)  # different owner → no conflict
    assert WorkoutSession.query.filter_by(status=WORKOUT_SESSION_ACTIVE).count() == 2


# ── start_session ────────────────────────────────────────────────────────────
def test_start_scheduled_creates_active_session(app, make_user):
    u = make_user("s1")
    _save_plan(u.id)
    r = start_session(u.id)
    assert r.outcome is SessionOutcome.CREATED
    assert r.session.status == WORKOUT_SESSION_ACTIVE
    assert r.session.source == sm.SOURCE_SCHEDULED
    assert r.session.public_id
    row = WorkoutSession.query.filter_by(user_id=u.id).one()
    assert row.status == WORKOUT_SESSION_ACTIVE
    assert row.plan_fingerprint.startswith("v1:")


def test_start_unscheduled_on_rest_day(app, make_user):
    u = make_user("s2")  # no plan at all → unscheduled workout
    r = start_session(u.id)
    assert r.outcome is SessionOutcome.CREATED
    assert r.session.source == sm.SOURCE_UNSCHEDULED
    assert r.session.relationship == sm.REL_UNSCHEDULED
    assert r.session.resumable is True


def test_repeated_identical_start_is_idempotent(app, make_user):
    u = make_user("s3")
    _save_plan(u.id)
    first = start_session(u.id)
    second = start_session(u.id)
    assert second.outcome is SessionOutcome.EXISTING_ACTIVE
    assert second.session.public_id == first.session.public_id
    assert WorkoutSession.query.filter_by(user_id=u.id).count() == 1


def test_different_workout_while_active_is_conflict(app, make_user):
    u = make_user("s4")
    # A pre-existing ACTIVE session for a DIFFERENT weekday slot than today's.
    other = "Pazartesi" if _today_weekday() != "Pazartesi" else "Salı"
    _mk_session(u.id, weekday_slot=other, source=sm.SOURCE_SCHEDULED)
    r = start_session(u.id)  # today's snapshot ≠ the active session's slot
    assert r.outcome is SessionOutcome.CONFLICT
    assert WorkoutSession.query.filter_by(
        user_id=u.id, status=WORKOUT_SESSION_ACTIVE).count() == 1


def test_start_race_rolls_back_and_returns_existing(app, make_user, monkeypatch):
    """The insert IS the atomic claim: a partial-index conflict rolls back and the
    existing session is returned — never a duplicate ACTIVE row, session not
    poisoned (correction: replay-safe after client timeout / concurrent start)."""
    u = make_user("s5")
    _save_plan(u.id)
    first = start_session(u.id)
    assert first.outcome is SessionOutcome.CREATED

    real = wq.get_active_session
    calls = {"n": 0}

    def _hide_once(user_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(user_id)

    # Hide the winner from the pre-check so the insert runs and trips the index.
    monkeypatch.setattr(wservice, "get_active_session", _hide_once)
    second = start_session(u.id)
    assert second.outcome is SessionOutcome.EXISTING_ACTIVE
    assert WorkoutSession.query.filter_by(
        user_id=u.id, status=WORKOUT_SESSION_ACTIVE).count() == 1
    # Session usable afterwards (not poisoned): a normal read still works.
    assert get_current_session(u.id).outcome is SessionOutcome.EXISTING_ACTIVE


def test_new_session_allowed_after_abandonment(app, make_user):
    u = make_user("s6")
    _save_plan(u.id)
    started = start_session(u.id)
    abandon_session(u.id, started.session.public_id)
    again = start_session(u.id)
    assert again.outcome is SessionOutcome.CREATED
    assert again.session.public_id != started.session.public_id


# ── get_current_session ──────────────────────────────────────────────────────
def test_current_none_when_no_active(app, make_user):
    u = make_user("c1")
    assert get_current_session(u.id).outcome is SessionOutcome.NOT_FOUND


def test_current_returns_active_with_classification(app, make_user):
    u = make_user("c2")
    _save_plan(u.id)
    start_session(u.id)
    r = get_current_session(u.id)
    assert r.outcome is SessionOutcome.EXISTING_ACTIVE
    assert r.session.relationship == sm.REL_MATCHES_CURRENT_PLAN
    assert r.session.resumable is True


def test_current_terminal_session_not_returned_as_active(app, make_user):
    u = make_user("c3")
    _mk_session(u.id, status=WORKOUT_SESSION_COMPLETED)
    assert get_current_session(u.id).outcome is SessionOutcome.NOT_FOUND


# ── resume_session ───────────────────────────────────────────────────────────
def test_resume_eligible_matching_session(app, make_user):
    u = make_user("r1")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    r = resume_session(u.id, pid)
    assert r.outcome is SessionOutcome.RESUMED
    assert r.session.resumable is True


def test_resume_unscheduled_session(app, make_user):
    u = make_user("r2")
    pid = start_session(u.id).session.public_id  # no plan → unscheduled
    assert resume_session(u.id, pid).outcome is SessionOutcome.RESUMED


def test_resume_completed_is_terminal_outcome(app, make_user):
    u = make_user("r3")
    s = _mk_session(u.id, status=WORKOUT_SESSION_COMPLETED)
    assert resume_session(u.id, s.public_id).outcome is SessionOutcome.ALREADY_COMPLETED


def test_resume_abandoned_is_terminal_outcome(app, make_user):
    u = make_user("r4")
    s = _mk_session(u.id, status=WORKOUT_SESSION_ABANDONED)
    assert resume_session(u.id, s.public_id).outcome is SessionOutcome.ALREADY_ABANDONED


def test_resume_unknown_public_id_not_found(app, make_user):
    u = make_user("r5")
    assert resume_session(u.id, "does-not-exist").outcome is SessionOutcome.NOT_FOUND


def test_resume_stale_previous_day_requires_resolution(app, make_user):
    u = make_user("r6")
    _save_plan(u.id)
    yesterday = app_today() - timedelta(days=1)
    started = start_session(u.id, today=yesterday)
    r = resume_session(u.id, started.session.public_id)  # read on today's date
    assert r.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION
    assert r.session.stale_reason == sm.STALE_PREVIOUS_DAY
    # Preserved — never silently re-attached or auto-terminalized.
    assert db.session.get(WorkoutSession, _row_id(u.id)).status == WORKOUT_SESSION_ACTIVE


def test_resume_stale_plan_missing(app, make_user):
    u = make_user("r7")
    plan = _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    db.session.delete(plan)  # plan gone → soft ref now dangles
    db.session.commit()
    r = resume_session(u.id, pid)
    assert r.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION
    assert r.session.stale_reason == sm.STALE_PLAN_MISSING


def test_resume_stale_plan_regenerated(app, make_user):
    u = make_user("r8")
    _save_plan(u.id, exercises=("Squat", "Bench"))
    pid = start_session(u.id).session.public_id
    # A NEWER plan, same weekday, DIFFERENT contents → different fingerprint.
    _save_plan(u.id, exercises=("Deadlift", "Row"),
               created_at=datetime.utcnow() + timedelta(hours=1))
    r = resume_session(u.id, pid)
    assert r.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION
    assert r.session.stale_reason == sm.STALE_PLAN_REGENERATED


def test_resume_stale_schedule_slot_changed(app, make_user):
    u = make_user("r9")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    # A newer plan where TODAY is now a rest day → the workout slot changed.
    _save_plan(u.id, raw=json.dumps(_program("__none__", [])),
               created_at=datetime.utcnow() + timedelta(hours=1))
    r = resume_session(u.id, pid)
    assert r.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION
    assert r.session.stale_reason == sm.STALE_SCHEDULE_SLOT_CHANGED


def test_resume_fingerprint_version_mismatch_is_indeterminate(app, make_user):
    u = make_user("r10")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    # Corrupt the stored fingerprint's ALGORITHM VERSION (as a future v2 would look).
    row = WorkoutSession.query.filter_by(user_id=u.id).one()
    row.plan_fingerprint = "v2:" + ("a" * 64)
    db.session.commit()
    r = resume_session(u.id, pid)
    assert r.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION
    assert r.session.relationship == sm.REL_INDETERMINATE
    assert r.session.stale_reason == sm.STALE_INDETERMINATE


def test_resume_lifecycle_inconsistent_when_already_completed_today(app, make_user):
    u = make_user("r11")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    db.session.add(PumpCheck(user_id=u.id, valid=True, date_key=app_today().isoformat()))
    db.session.commit()
    r = resume_session(u.id, pid)
    assert r.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION
    assert r.session.stale_reason == sm.STALE_LIFECYCLE_INCONSISTENT


def _row_id(user_id):
    return WorkoutSession.query.filter_by(user_id=user_id).one().id


# ── checkpoint_session (replay-idempotent heartbeat) ─────────────────────────
def test_checkpoint_moves_only_last_activity(app, make_user):
    u = make_user("h1")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    row = WorkoutSession.query.filter_by(user_id=u.id).one()
    # Age the heartbeat past the coalesce window so a real write happens.
    old = datetime.utcnow() - timedelta(seconds=sm.HEARTBEAT_COALESCE_SECONDS + 60)
    started_at, status, version, workout_date = (
        row.started_at, row.status, row.version, row.workout_date)
    row.last_activity_at = old
    db.session.commit()

    r = checkpoint_session(u.id, pid)
    assert r.outcome is SessionOutcome.CHECKPOINTED
    db.session.expire_all()
    row = WorkoutSession.query.filter_by(user_id=u.id).one()
    assert row.last_activity_at > old               # moved
    assert row.started_at == started_at             # identity/start untouched
    assert row.status == status
    assert row.version == version                   # heartbeat never bumps version
    assert row.workout_date == workout_date


def test_checkpoint_replay_is_idempotent_no_false_conflict(app, make_user):
    u = make_user("h2")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    row = WorkoutSession.query.filter_by(user_id=u.id).one()
    row.last_activity_at = datetime.utcnow() - timedelta(seconds=120)
    db.session.commit()

    first = checkpoint_session(u.id, pid)
    db.session.expire_all()
    moved_to = WorkoutSession.query.filter_by(user_id=u.id).one().last_activity_at
    # A retried heartbeat inside the coalesce window is a SUCCESS no-op — never a
    # false conflict — and does not move last_activity again (write coalesced).
    second = checkpoint_session(u.id, pid)
    third = checkpoint_session(u.id, pid)
    assert first.outcome is second.outcome is third.outcome is SessionOutcome.CHECKPOINTED
    db.session.expire_all()
    assert WorkoutSession.query.filter_by(user_id=u.id).one().last_activity_at == moved_to


def test_checkpoint_terminal_session_reports_terminal(app, make_user):
    u = make_user("h3")
    c = _mk_session(u.id, status=WORKOUT_SESSION_COMPLETED)
    assert checkpoint_session(u.id, c.public_id).outcome is SessionOutcome.ALREADY_COMPLETED


def test_checkpoint_unknown_is_not_found(app, make_user):
    u = make_user("h4")
    assert checkpoint_session(u.id, "nope").outcome is SessionOutcome.NOT_FOUND


# ── abandon_session ──────────────────────────────────────────────────────────
def test_abandon_active_session(app, make_user):
    u = make_user("ab1")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    r = abandon_session(u.id, pid, reason="changed my mind")
    assert r.outcome is SessionOutcome.ABANDONED
    row = WorkoutSession.query.filter_by(user_id=u.id).one()
    assert row.status == WORKOUT_SESSION_ABANDONED
    assert row.abandoned_at is not None
    assert row.terminal_reason == "changed my mind"
    assert row.version == 2  # terminal transitions bump the version


def test_abandon_is_idempotent(app, make_user):
    u = make_user("ab2")
    pid = start_session(u.id).session.public_id
    first = abandon_session(u.id, pid)
    second = abandon_session(u.id, pid)
    assert first.outcome is SessionOutcome.ABANDONED
    assert second.outcome is SessionOutcome.ALREADY_ABANDONED
    # No physical delete — evidence is preserved.
    assert WorkoutSession.query.filter_by(user_id=u.id).count() == 1


def test_abandon_reason_is_bounded(app, make_user):
    u = make_user("ab3")
    pid = start_session(u.id).session.public_id
    abandon_session(u.id, pid, reason="x" * 200)
    assert len(WorkoutSession.query.filter_by(user_id=u.id).one().terminal_reason) <= 40


def test_abandon_completed_session_reports_completed(app, make_user):
    u = make_user("ab4")
    c = _mk_session(u.id, status=WORKOUT_SESSION_COMPLETED)
    assert abandon_session(u.id, c.public_id).outcome is SessionOutcome.ALREADY_COMPLETED


# ── completion delegation ────────────────────────────────────────────────────
def test_complete_session_terminalizes_via_pr2(app, make_user):
    u = make_user("cp1")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    r = complete_session(u.id, pid, location_type="salon", description="done",
                         base_xp=10, photo_bonus=25)
    assert r.outcome is SessionOutcome.COMPLETED
    assert r.session.status == WORKOUT_SESSION_COMPLETED
    # PR2 authority created exactly one PumpCheck; the session never creates its own.
    assert PumpCheck.query.filter_by(user_id=u.id).count() == 1
    assert db.session.get(User, u.id).rank_points == 35


def test_duplicate_session_completion_no_duplicate_side_effects(app, make_user):
    u = make_user("cp2")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    complete_session(u.id, pid)
    second = complete_session(u.id, pid)
    assert second.outcome is SessionOutcome.ALREADY_COMPLETED
    assert PumpCheck.query.filter_by(user_id=u.id).count() == 1   # not doubled
    assert db.session.get(User, u.id).rank_points == 35           # XP not doubled


def test_complete_abandoned_session_is_terminal_conflict(app, make_user):
    u = make_user("cp3")
    _save_plan(u.id)
    pid = start_session(u.id).session.public_id
    abandon_session(u.id, pid)
    r = complete_session(u.id, pid)
    assert r.outcome is SessionOutcome.ALREADY_ABANDONED
    assert PumpCheck.query.filter_by(user_id=u.id).count() == 0   # nothing written


def test_previous_day_completion_link_is_rejected_read_only(app, make_user):
    """A caller-supplied completion day may not attach today's artifacts to a
    previous-day ACTIVE session or resolve that stale lifecycle implicitly."""
    u = make_user("cp_stale")
    _save_plan(u.id)
    today = app_today()
    pid = start_session(u.id, today=today - timedelta(days=1)).session.public_id
    row = WorkoutSession.query.filter_by(user_id=u.id).one()
    before_row = (
        row.user_id, row.public_id, row.status, row.workout_date,
        row.started_at, row.last_activity_at, row.version,
        row.completed_at, row.abandoned_at, row.terminal_reason,
    )
    before_counts = (
        WorkoutSession.query.filter_by(user_id=u.id).count(),
        PumpCheck.query.filter_by(user_id=u.id).count(),
        WorkoutLog.query.filter_by(user_id=u.id).count(),
        Activity.query.filter_by(user_id=u.id).count(),
        db.session.get(User, u.id).rank_points or 0,
    )

    result = complete_session(u.id, pid, today=today)

    db.session.expire_all()
    row = WorkoutSession.query.filter_by(user_id=u.id).one()
    assert result.outcome is SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION
    assert (
        row.user_id, row.public_id, row.status, row.workout_date,
        row.started_at, row.last_activity_at, row.version,
        row.completed_at, row.abandoned_at, row.terminal_reason,
    ) == before_row
    assert row.status == WORKOUT_SESSION_ACTIVE
    assert WorkoutSession.query.filter_by(
        user_id=u.id, status=WORKOUT_SESSION_ACTIVE).count() == 1
    assert WorkoutSession.query.filter_by(
        user_id=u.id, status=WORKOUT_SESSION_COMPLETED).count() == 0
    assert WorkoutSession.query.filter_by(
        user_id=u.id, status=WORKOUT_SESSION_ABANDONED).count() == 0
    assert (
        WorkoutSession.query.filter_by(user_id=u.id).count(),
        PumpCheck.query.filter_by(user_id=u.id).count(),
        WorkoutLog.query.filter_by(user_id=u.id).count(),
        Activity.query.filter_by(user_id=u.id).count(),
        db.session.get(User, u.id).rank_points or 0,
    ) == before_counts
    assert WorkoutLog.query.filter_by(
        user_id=u.id, exercise_name=WORKOUT_COMPLETION_MARKER).count() == 0


def test_resolve_for_completion_returns_internal_id(app, make_user):
    u = make_user("cp4")
    pid = start_session(u.id).session.public_id
    session_id, error = resolve_for_completion(u.id, pid, app_today())
    assert error is None
    assert session_id == _row_id(u.id)


# ── ownership isolation (A can never touch B's session) ──────────────────────
def test_ownership_isolation_across_all_operations(app, make_user):
    a, b = make_user("own_a"), make_user("own_b")
    _save_plan(a.id)
    a_pid = start_session(a.id).session.public_id

    # B sees none of A's session through any operation.
    assert get_current_session(b.id).outcome is SessionOutcome.NOT_FOUND
    assert resume_session(b.id, a_pid).outcome is SessionOutcome.NOT_FOUND
    assert checkpoint_session(b.id, a_pid).outcome is SessionOutcome.NOT_FOUND
    assert abandon_session(b.id, a_pid).outcome is SessionOutcome.NOT_FOUND
    assert complete_session(b.id, a_pid).outcome is SessionOutcome.NOT_FOUND
    _sid, err = resolve_for_completion(b.id, a_pid, app_today())
    assert err.outcome is SessionOutcome.NOT_FOUND
    # A's session is untouched by any of B's attempts.
    assert WorkoutSession.query.filter_by(user_id=a.id).one().status == WORKOUT_SESSION_ACTIVE
    assert PumpCheck.query.count() == 0


# ── reads never mutate ───────────────────────────────────────────────────────
def test_reads_do_not_mutate_or_auto_abandon(app, make_user):
    u = make_user("ro")
    _save_plan(u.id)
    yesterday = app_today() - timedelta(days=1)
    start_session(u.id, today=yesterday)  # a stale previous-day ACTIVE session
    before = _snapshot_row(u.id)
    for _ in range(3):
        get_current_session(u.id)
        read_session_for_state(u.id)
    assert _snapshot_row(u.id) == before
    # Still ACTIVE — a read never auto-abandons a stale session.
    assert WorkoutSession.query.filter_by(user_id=u.id).one().status == WORKOUT_SESSION_ACTIVE
    assert not db.session.new and not db.session.dirty and not db.session.deleted


def _snapshot_row(user_id):
    r = WorkoutSession.query.filter_by(user_id=user_id).one()
    return (r.status, r.workout_date, r.started_at, r.last_activity_at,
            r.version, r.completed_at, r.abandoned_at)


def test_repeated_resolution_is_deterministic(app, make_user):
    u = make_user("det")
    _save_plan(u.id)
    start_session(u.id)
    a = get_current_session(u.id).session.to_dict()
    b = get_current_session(u.id).session.to_dict()
    assert a == b


# ── timezone / local midnight ────────────────────────────────────────────────
def test_workout_date_is_start_local_date(app, make_user):
    u = make_user("tz1")
    _save_plan(u.id)
    r = start_session(u.id)
    assert r.session.workout_date == app_today().isoformat()


def test_session_crossing_local_midnight_becomes_previous_day(app, make_user):
    # Started "yesterday", read "today" (a local-midnight crossing) → previous_day,
    # never silently rewritten to today.
    u = make_user("tz2")
    _save_plan(u.id)
    yesterday = app_today() - timedelta(days=1)
    started = start_session(u.id, today=yesterday)
    assert started.session.workout_date == yesterday.isoformat()
    cur = get_current_session(u.id)  # defaults to app_today()
    assert cur.session.stale_reason == sm.STALE_PREVIOUS_DAY
    assert cur.session.resumable is False


# ── fault-injection rollback ─────────────────────────────────────────────────
def test_abandon_race_loser_is_deterministic(app, make_user, monkeypatch):
    """A conditional terminal UPDATE that affects 0 rows (a concurrent terminal
    transition already won) yields a deterministic terminal outcome, never a false
    success — exactly one terminal transition wins."""
    u = make_user("fi1")
    pid = start_session(u.id).session.public_id
    # Force the atomic UPDATE to report "no row changed" (as if another txn won).
    monkeypatch.setattr(wservice, "terminalize_abandon", lambda *a, **k: 0)
    r = abandon_session(u.id, pid)
    assert r.outcome is SessionOutcome.ALREADY_ABANDONED


def test_read_session_for_state_prefers_active_then_terminal(app, make_user):
    u = make_user("rs1")
    # No session → None.
    assert read_session_for_state(u.id) is None
    # A terminal session that STARTED today is reported when no active exists.
    _mk_session(u.id, status=WORKOUT_SESSION_COMPLETED)
    view = read_session_for_state(u.id)
    assert view is not None and view.status == WORKOUT_SESSION_COMPLETED


# ── fail-closed verify-or-create migration ───────────────────────────────────
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "migrations" / "versions"
    / "a994f9bed783_add_workout_session_sprint_7_pr3.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("ws_pr3_migration", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(engine, fn):
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE IF NOT EXISTS user (id INTEGER PRIMARY KEY)"))
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            fn()


def test_migration_creates_full_table_on_fresh_db(tmp_path):
    mig = _load_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    _run(engine, mig.upgrade)
    insp = sa.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("workout_session")}
    assert mig._REQUIRED_COLUMNS <= cols
    idx = {i["name"] for i in insp.get_indexes("workout_session")}
    assert "uq_workout_session_active_owner" in idx
    assert "uq_workout_session_public_id" in idx
    engine.dispose()


def test_migration_verify_or_create_is_idempotent(tmp_path):
    mig = _load_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
    _run(engine, mig.upgrade)
    # Running again over the create_all-style existing table must NOT blanket-skip
    # nor fail — it verifies and creates nothing new.
    _run(engine, mig.upgrade)
    insp = sa.inspect(engine)
    assert insp.has_table("workout_session")
    engine.dispose()


def test_migration_fails_closed_on_incompatible_table(tmp_path):
    mig = _load_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'bad.db'}")
    with engine.begin() as conn:
        # An existing table missing required columns → must NOT report success.
        conn.execute(sa.text(
            "CREATE TABLE workout_session (id INTEGER PRIMARY KEY, user_id INTEGER)"))
    with pytest.raises(RuntimeError, match="incompatible"):
        _run(engine, mig.upgrade)
    engine.dispose()


def test_migration_downgrade_drops_table(tmp_path):
    mig = _load_migration()
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'down.db'}")
    _run(engine, mig.upgrade)
    assert sa.inspect(engine).has_table("workout_session")
    _run(engine, mig.downgrade)
    assert not sa.inspect(engine).has_table("workout_session")
    engine.dispose()
