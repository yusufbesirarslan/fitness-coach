"""Read/write ORM adapters for the persisted workout-session lifecycle (PR3).

Impure (needs an app/DB context). All *classification* stays pure in
``models.py``; all transaction ownership stays in ``service.py``. Plan parsing
reuses the canonical ``WEEKDAYS``/``VALID_TIPS`` vocabulary (the same the plan
generator validates against — no second definition), and completion identity
reuses the PR2 canonical preflight (no second "completed today" definition).
Istanbul-day semantics come from ``app.timeutil`` only.
"""
import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional, Tuple

from sqlalchemy import update

from app.extensions import db
from app.models import (
    WORKOUT_SESSION_ABANDONED,
    WORKOUT_SESSION_ACTIVE,
    TrainingPlan,
    WorkoutSession,
)
from app.services.training_generation.response_validator import VALID_TIPS, WEEKDAYS
from app.services.workout_completion import already_completed_today

from .models import SOURCE_SCHEDULED, SOURCE_UNSCHEDULED, compute_fingerprint

# The active-session partial unique index (app/models.py WorkoutSession).
ACTIVE_OWNER_CONSTRAINT = "uq_workout_session_active_owner"
_PUBLIC_ID_CONSTRAINT = "uq_workout_session_public_id"

_REST_TIP = "dinlenme"
_WORKOUT_TIPS = frozenset(VALID_TIPS) - {_REST_TIP}


@dataclass(frozen=True)
class NativeWorkoutIdentity:
    """The canonical native workout identity captured at start (PR5).

    Server-resolved from an opaque ``workout_ref`` against the owner's CURRENT
    plan before the session row exists; never client-authored content. ``None``
    for a browser-started session, which has no native reference.
    """
    workout_ref: str
    plan_lineage_id: str
    plan_mutation_version: int


@dataclass(frozen=True)
class PlanSnapshot:
    """The trusted, server-derived plan reference captured at session start."""
    weekday_slot: str
    source: str
    planned_training_plan_id: Optional[int]
    plan_fingerprint: Optional[str]


@dataclass(frozen=True)
class CurrentPlanFacts:
    """The current newest-plan facts used to classify an existing session."""
    has_current_plan: bool
    current_plan_id: Optional[int]
    current_weekday_is_workout: bool
    current_fingerprint: Optional[str]


def _weekday_name(d: date) -> str:
    # date.weekday(): Monday=0 lines up with WEEKDAYS[0] ("Pazartesi").
    return WEEKDAYS[d.weekday()]


def _newest_plan(user_id: int) -> Optional[TrainingPlan]:
    return (
        TrainingPlan.query.filter_by(user_id=user_id)
        .order_by(TrainingPlan.created_at.desc())
        .first()
    )


def _parse_program(plan: TrainingPlan) -> Optional[list]:
    try:
        data = json.loads(plan.plan_data)
    except (ValueError, TypeError):
        return None
    program = data if isinstance(data, list) else (
        data.get("program") if isinstance(data, dict) else None)
    if not isinstance(program, list) or len(program) != 7:
        return None
    return program


def _weekday_workout(program: list, weekday_name: str) -> Tuple[bool, List[str]]:
    """Return ``(is_workout, ordered_exercise_names)`` for ``weekday_name``.

    ``is_workout`` is True only when that weekday's ``tip`` is a recognized
    non-rest workout kind. Exercise names are the ordered ``isim`` values — the
    canonical, order-preserving input to the versioned fingerprint.
    """
    for day in program:
        if not isinstance(day, dict) or day.get("gun") != weekday_name:
            continue
        if day.get("tip") not in _WORKOUT_TIPS:
            return False, []
        exercises = day.get("egzersizler")
        if not isinstance(exercises, list):
            return False, []
        names = [
            str(ex.get("isim") or "").strip()
            for ex in exercises
            if isinstance(ex, dict)
        ]
        return True, names
    return False, []


def compute_plan_snapshot(user_id: int, today: date) -> PlanSnapshot:
    """Derive the minimal soft plan reference for a session started ``today``.

    Scheduled when today's weekday is a workout in the newest plan (records the
    plan id + versioned fingerprint); otherwise unscheduled (a workout on a rest
    day / with no plan) — still records the plan id as context when a plan exists,
    but no fingerprint (there is no scheduled workout to fingerprint).
    """
    weekday = _weekday_name(today)
    plan = _newest_plan(user_id)
    if plan is None:
        return PlanSnapshot(weekday, SOURCE_UNSCHEDULED, None, None)
    program = _parse_program(plan)
    if program is None:
        return PlanSnapshot(weekday, SOURCE_UNSCHEDULED, plan.id, None)
    is_workout, names = _weekday_workout(program, weekday)
    if is_workout:
        return PlanSnapshot(weekday, SOURCE_SCHEDULED, plan.id, compute_fingerprint(names))
    return PlanSnapshot(weekday, SOURCE_UNSCHEDULED, plan.id, None)


def fingerprint_for_program(program: Optional[list], weekday_name: str) -> Optional[str]:
    """Canonical exercise-name fingerprint for ``weekday_name`` in ``program``.

    Reuses ``_weekday_workout`` + ``compute_fingerprint`` so impact policy never
    invents a second digest algorithm. ``None`` when the slot is not a workout.
    """
    if not program or not weekday_name:
        return None
    is_workout, names = _weekday_workout(program, weekday_name)
    return compute_fingerprint(names) if is_workout else None


def current_plan_facts(user_id: int, weekday_slot: Optional[str]) -> CurrentPlanFacts:
    """Load the CURRENT newest-plan facts for classifying an existing session."""
    plan = _newest_plan(user_id)
    if plan is None:
        return CurrentPlanFacts(False, None, False, None)
    program = _parse_program(plan)
    if program is None or not weekday_slot:
        return CurrentPlanFacts(True, plan.id, False, None)
    is_workout, names = _weekday_workout(program, weekday_slot)
    return CurrentPlanFacts(
        True, plan.id, is_workout,
        compute_fingerprint(names) if is_workout else None,
    )


def get_active_session(user_id: int) -> Optional[WorkoutSession]:
    return (
        WorkoutSession.query.filter_by(
            user_id=user_id, status=WORKOUT_SESSION_ACTIVE
        )
        .order_by(WorkoutSession.started_at.desc())
        .first()
    )


def get_latest_session_for_day(user_id: int, workout_date: str) -> Optional[WorkoutSession]:
    """The most recently started session whose START date is ``workout_date``
    (any status). Read-only — used by the PR1 resolver to report a terminal
    (completed/abandoned) session for today when no ACTIVE session exists."""
    if not workout_date:
        return None
    return (
        WorkoutSession.query.filter_by(user_id=user_id, workout_date=workout_date)
        .order_by(WorkoutSession.started_at.desc())
        .first()
    )


def get_owned_session(user_id: int, public_id: str) -> Optional[WorkoutSession]:
    """Ownership-scoped lookup by opaque public id (never cross-user)."""
    if not public_id:
        return None
    return WorkoutSession.query.filter_by(
        user_id=user_id, public_id=public_id
    ).first()


def lock_owned_session(user_id: int, session_id: int) -> Optional[WorkoutSession]:
    """SELECT ... FOR UPDATE the owned session by internal id (no-op lock on SQLite)."""
    return (
        db.session.query(WorkoutSession)
        .filter_by(id=session_id, user_id=user_id)
        .with_for_update()
        .first()
    )


def insert_active_session(
    user_id: int,
    today: date,
    snapshot: PlanSnapshot,
    now: datetime,
    native: Optional[NativeWorkoutIdentity] = None,
) -> WorkoutSession:
    """Insert a new ACTIVE session and flush so a partial-index conflict surfaces.

    ``native`` records the PR5 native workout identity when the session was
    started through the native contract. Progress always starts at revision 0
    with no snapshot: durable progress is only ever created by a checkpoint.
    """
    session = WorkoutSession(
        public_id=_gen_public_id(),
        user_id=user_id,
        status=WORKOUT_SESSION_ACTIVE,
        workout_date=today.isoformat(),
        weekday_slot=snapshot.weekday_slot,
        source=snapshot.source,
        planned_training_plan_id=snapshot.planned_training_plan_id,
        plan_fingerprint=snapshot.plan_fingerprint,
        workout_ref=native.workout_ref if native is not None else None,
        plan_lineage_id=native.plan_lineage_id if native is not None else None,
        plan_mutation_version=(
            native.plan_mutation_version if native is not None else None),
        checkpoint_revision=0,
        started_at=now,
        last_activity_at=now,
        version=1,
    )
    db.session.add(session)
    db.session.flush()
    return session


def _gen_public_id() -> str:
    import secrets

    return secrets.token_urlsafe(32)


def heartbeat(user_id: int, public_id: str, now: datetime, coalesce_cutoff: datetime) -> int:
    """Replay-idempotent heartbeat: one atomic conditional UPDATE of ONLY
    ``last_activity_at`` on the owned ACTIVE session, coalescing writes inside the
    short window. Returns the affected row count (0 = coalesced or not-active)."""
    result = db.session.execute(
        update(WorkoutSession)
        .where(
            WorkoutSession.public_id == public_id,
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == WORKOUT_SESSION_ACTIVE,
            WorkoutSession.last_activity_at < coalesce_cutoff,
        )
        .values(last_activity_at=now)
    )
    db.session.commit()
    return result.rowcount


def touch_active(user_id: int, public_id: str, now: datetime) -> int:
    """Unconditionally bump ``last_activity_at`` on the owned ACTIVE session
    (used by an explicit resume). Conditional on status=active so it can never
    resurrect a just-terminalized session. Returns affected row count."""
    result = db.session.execute(
        update(WorkoutSession)
        .where(
            WorkoutSession.public_id == public_id,
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == WORKOUT_SESSION_ACTIVE,
        )
        .values(last_activity_at=now)
    )
    db.session.commit()
    return result.rowcount


def advance_checkpoint(
    user_id: int,
    public_id: str,
    base_revision: int,
    snapshot_json: str,
    fingerprint: str,
    key: str,
    now: datetime,
) -> int:
    """Durably advance progress by exactly one revision (PR5).

    ONE atomic conditional UPDATE is the whole concurrency story: it matches the
    owned session, the ACTIVE status and the caller's declared base revision, so

      * a late request built on an older revision matches 0 rows (it can never
        overwrite newer progress -- revision decides, not arrival order);
      * a terminal session matches 0 rows (no post-completion mutation);
      * two concurrent writers on the same base revision produce exactly one
        winner, because the second one no longer matches the base.

    The snapshot, its fingerprint, the replay key, the new revision and the
    checkpoint timestamp move together in that single statement, so a revision
    can never advance without the snapshot that justified it. Returns the
    affected row count (0 = the caller lost / is stale / is terminal).
    """
    result = db.session.execute(
        update(WorkoutSession)
        .where(
            WorkoutSession.public_id == public_id,
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == WORKOUT_SESSION_ACTIVE,
            WorkoutSession.checkpoint_revision == base_revision,
        )
        .values(
            checkpoint_revision=base_revision + 1,
            checkpoint_data=snapshot_json,
            checkpoint_fingerprint=fingerprint,
            checkpoint_idempotency_key=key,
            checkpoint_at=now,
            last_activity_at=now,
        )
    )
    db.session.commit()
    return result.rowcount


def terminalize_abandon(
    user_id: int, public_id: str, reason: Optional[str], now: datetime
) -> int:
    """Atomically move the owned ACTIVE session to ABANDONED. Conditional on
    status=active so a concurrent completion/abandon leaves exactly one winner.
    Returns affected row count (0 = lost the race / not active)."""
    result = db.session.execute(
        update(WorkoutSession)
        .where(
            WorkoutSession.public_id == public_id,
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == WORKOUT_SESSION_ACTIVE,
        )
        .values(
            status=WORKOUT_SESSION_ABANDONED,
            abandoned_at=now,
            terminal_reason=(reason or "abandoned")[:40],
            version=WorkoutSession.version + 1,
        )
    )
    db.session.commit()
    return result.rowcount


def completed_today(user_id: int, today: date) -> bool:
    """Reuse the PR2 canonical completion preflight — single source of truth for
    what 'completed today' means (today's PumpCheck, Istanbul day)."""
    return already_completed_today(user_id, today)


def is_active_session_owner_violation(exc) -> bool:
    """True only for a verified ``uq_workout_session_active_owner`` violation.

    Mirrors ``workout_completion.is_pump_check_day_violation``: inspect the
    driver's constraint identity safely (Postgres ``diag.constraint_name``, else
    the message text). Any other integrity error is a real error → return False
    (fail closed, re-raise upstream).
    """
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    if constraint_name:
        return constraint_name == ACTIVE_OWNER_CONSTRAINT
    # SQLite names the offending column of the partial unique index, not the index
    # name: "UNIQUE constraint failed: workout_session.user_id". That column is
    # unique to the active-owner index on this table (public_id is the only other
    # unique index and names workout_session.public_id).
    text = str(orig if orig is not None else exc)
    return ACTIVE_OWNER_CONSTRAINT in text or "workout_session.user_id" in text
