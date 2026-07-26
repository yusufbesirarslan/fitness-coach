"""Read-only persistence helpers for the canonical completion mutation.

Two responsibilities, both deliberately tiny and side-effect free:

* :func:`already_completed_today` — the completion **preflight**. It lets entry
  paths short-circuit an obvious replay *before* doing expensive provider work
  (Bedrock vision, S3 upload). It is a cost/latency optimization only — it is
  **not** the concurrency-safe claim. The ``uq_pump_check_day`` unique
  constraint remains the sole atomic authority (see ``service.complete_workout``).

* :func:`is_pump_check_day_violation` — safely decides whether an
  ``IntegrityError`` is specifically the daily-completion unique violation, so
  and only so it is mapped to the deterministic ``ALREADY_COMPLETED`` replay
  outcome. Any other integrity failure must surface as an internal error.

Completion identity uses the repository's canonical Istanbul-day semantics
(``app.timeutil``), matching the existing completion guards *and* the Sprint 7
PR1 resolver's ``completed_today`` (``workout_state/queries.py``) — today's
``PumpCheck`` bucketed by ``created_at`` into the Istanbul day. In production
this is equivalent to the ``date_key`` the row is written with (both derive from
the same instant); the unique constraint on ``date_key`` is the durable claim.
"""
from datetime import date, datetime
from typing import Optional

from app.extensions import db
from app.models import (
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
    PumpCheck,
    WorkoutSession,
)
from app.timeutil import utc_day_bounds

# The daily-completion unique constraint (app/models.py PumpCheck.__table_args__).
PUMP_CHECK_DAY_CONSTRAINT = "uq_pump_check_day"


def already_completed_today(user_id: int, today: date) -> bool:
    """True if ``user_id`` already has a completion ``PumpCheck`` for Istanbul
    day ``today``. Read-only; no flush/commit.

    Byte-identical in intent to the pre-PR2 inline guards (``training.py`` and
    the AI-coach tool) and to PR1's ``completed_today`` — same Istanbul-day
    ``created_at`` window, so the mutation preflight and the read-model never
    disagree about what "completed today" means.
    """
    start_utc, end_utc = utc_day_bounds(today)
    return (
        PumpCheck.query.filter(
            PumpCheck.user_id == user_id,
            PumpCheck.created_at >= start_utc,
            PumpCheck.created_at < end_utc,
        ).first()
        is not None
    )


def is_pump_check_day_violation(exc) -> bool:
    """Return True only for a verified ``uq_pump_check_day`` unique violation.

    Correction #1: never classify an *arbitrary* ``IntegrityError`` as an
    already-completed replay. A foreign-key, NOT NULL, or unrelated-unique
    failure is a real internal error and must be re-raised. We inspect the
    driver's constraint identity safely:

    * PostgreSQL (psycopg2/psycopg): ``exc.orig.diag.constraint_name``.
    * SQLite: the message names the offending columns
      (``UNIQUE constraint failed: pump_check.user_id, pump_check.date_key``);
      only ``uq_pump_check_day`` involves ``pump_check.date_key``.

    Anything we cannot positively identify returns ``False`` (fail closed — treat
    as a real error rather than silently swallowing it as "already done").
    """
    orig = getattr(exc, "orig", None)

    # PostgreSQL: authoritative constraint name.
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    if constraint_name:
        return constraint_name == PUMP_CHECK_DAY_CONSTRAINT

    # SQLite / generic: match the constraint name or the date_key column token.
    text = str(orig if orig is not None else exc)
    return PUMP_CHECK_DAY_CONSTRAINT in text or "pump_check.date_key" in text


# ── Session terminalization (Sprint 7 PR3) ───────────────────────────────────
# The completion authority owns the ACTIVE→COMPLETED transition of a linked
# session, inside its single transaction, so a session is never left ACTIVE after
# its day is completed and COMPLETED is never written without PumpCheck authority.
# The completion authority imports ONLY the WorkoutSession ORM model here — never
# the ``workout_session`` service package — keeping the dependency arrow one-way.

def lock_session_for_completion(user_id: int, session_id: int) -> Optional[WorkoutSession]:
    """``SELECT … FOR UPDATE`` the owned session by internal id — the FIXED lock
    order: the session row is locked BEFORE any PumpCheck/completion artifact, on
    both the create and the reconciliation path, so the two paths can never
    deadlock against each other. On SQLite ``with_for_update`` is a harmless no-op
    (the enclosing write transaction already serializes)."""
    return (
        db.session.query(WorkoutSession)
        .filter_by(id=session_id, user_id=user_id)
        .with_for_update()
        .first()
    )


def mark_session_completed(session: Optional[WorkoutSession], now: datetime) -> bool:
    """Terminalize an already-locked ACTIVE session as COMPLETED (mutates the ORM
    object; the caller owns the commit). Conditional on ``status == 'active'`` so
    it is idempotent and a concurrent abandon/complete leaves exactly one winner.
    Returns True iff this call performed the transition."""
    if session is None or session.status != WORKOUT_SESSION_ACTIVE:
        return False
    session.status = WORKOUT_SESSION_COMPLETED
    session.completed_at = now
    session.version = (session.version or 1) + 1
    return True
