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

Completion identity is the **canonical completion claim** itself (PRIV-K6-B1):

* a ``WorkoutCompletionDay`` row — written by :func:`service.complete_workout`
  for EVERY new completion, photo or photo-free; ``uq_workout_completion_day``
  is the durable cross-path claim; or
* a historical ``PumpCheck`` whose ``date_key`` is the Istanbul ISO day
  (``app.timeutil``) — the only claim that existed before K6. ``date_key`` is
  still written ONLY by :func:`service.complete_workout`, for photo completions,
  and ``uq_pump_check_day`` still guards it.

:func:`completed_days` reads both in ONE statement, so the preflight, the
read-model (``workout_state``) and the durable claims can never disagree about
what "completed today" means.

A ``PumpCheck`` with ``date_key IS NULL`` is **not** completion evidence. The
same table also holds standalone Pump Checks (``POST /api/v1/pump-checks``,
``mobile_pump_checks.service`` writes ``date_key=None``) and pre-2026-06-22
legacy rows (the ``a7b8c9d0e1f2`` migration added the column without a
backfill). Bucketing by ``created_at`` alone let a standalone Pump Check taken
earlier the same day make the day look completed and suppress the real
completion's marker/XP (Native Progress D1).
"""
import hashlib
from datetime import date, datetime
from typing import Iterable, Optional, Set

from sqlalchemy import select, text, union

from app.extensions import db
from app.models import (
    WORKOUT_SESSION_ACTIVE,
    WORKOUT_SESSION_COMPLETED,
    PumpCheck,
    WorkoutCompletionDay,
    WorkoutSession,
)

# The historical daily-completion unique constraint (PumpCheck.__table_args__).
PUMP_CHECK_DAY_CONSTRAINT = "uq_pump_check_day"
# The canonical K6 claim (WorkoutCompletionDay.__table_args__).
COMPLETION_DAY_CONSTRAINT = "uq_workout_completion_day"

# Namespace for the (owner, Istanbul day) advisory lock below, so its 64-bit key
# can never coincide with another advisory-lock user in the same database
# (``mobile_training_generation.locking`` hashes its own domain the same way).
_COMPLETION_DAY_LOCK_DOMAIN = b"fitx:workout-completion-day-lock:v1\0"


def completion_proof_clause():
    """The ONE predicate that makes a ``PumpCheck`` (historical) completion
    evidence.

    Every completion-state reader must go through :func:`completed_days` (or the
    helpers below) rather than re-deriving "completed" from PumpCheck row
    existence or ``created_at``.
    """
    return PumpCheck.date_key.isnot(None)


def completed_days(user_id: int, days: Iterable[date]) -> Set[date]:
    """The subset of Istanbul ``days`` on which ``user_id`` holds a canonical
    completion claim — a ``WorkoutCompletionDay`` row or a historical
    ``PumpCheck.date_key``. One bounded, read-only statement (a UNION, so read
    paths with pinned statement budgets keep them); no flush/commit."""
    keys = {day.isoformat(): day for day in days}
    if not keys:
        return set()
    claimed = select(WorkoutCompletionDay.date_key).where(
        WorkoutCompletionDay.user_id == user_id,
        WorkoutCompletionDay.date_key.in_(tuple(keys)),
    )
    historical = select(PumpCheck.date_key).where(
        PumpCheck.user_id == user_id,
        completion_proof_clause(),
        PumpCheck.date_key.in_(tuple(keys)),
    )
    rows = db.session.execute(union(claimed, historical)).all()
    return {keys[date_key] for (date_key,) in rows}


def already_completed_today(user_id: int, today: date) -> bool:
    """True if ``user_id`` already holds the canonical completion claim for
    Istanbul day ``today``. Read-only; no flush/commit.

    Shared by every completion entry path (browser route, AI-coach tool, native
    session completion) and by ``workout_state``'s ``completed_today`` via
    :func:`completed_days` — one definition of "completed". A standalone Pump
    Check (``date_key IS NULL``) never satisfies it.
    """
    return today in completed_days(user_id, (today,))


def completion_day_lock_key(user_id: int, day: date) -> int:
    """The signed 64-bit advisory-lock key for ``(user_id, day)``."""
    material = f"{int(user_id)}:{day.isoformat()}".encode("ascii")
    digest = hashlib.sha256(_COMPLETION_DAY_LOCK_DOMAIN + material).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def lock_completion_day(user_id: int, day: date) -> None:
    """Serialize on ``(user_id, Istanbul day)`` until the transaction ends (LP-13).

    The ONE boundary shared by the two writers whose interleaving could leave a
    new ACTIVE session on an already-completed day:

    * :func:`service.complete_workout` takes it before it writes the day's
      canonical claim (the ``WorkoutCompletionDay`` row, plus the ``PumpCheck``
      with ``date_key`` for a photo completion);
    * ``workout_session.start_session`` takes it before it re-checks that claim
      and inserts a session.

    So a start's re-check and INSERT and a completion's claim write can never
    interleave: whichever transaction holds the lock commits (or rolls back)
    before the other proceeds, and the waiter's next statement runs on a fresh
    READ COMMITTED snapshot that already contains the holder's commit.

    ``pg_advisory_xact_lock`` is cluster-wide, so it serializes every web
    worker, process and host on the same database (a Python lock would not),
    and PostgreSQL releases it on COMMIT, ROLLBACK or a dropped connection —
    there is no unlock to forget. Different owners and different days hash to
    different keys and never wait on each other. The caller owns the
    transaction and must not do network I/O while holding it.

    On SQLite this is a no-op, as everywhere else in the repository: that
    backend admits one writer at a time, so the critical section is already
    exclusive.
    """
    if db.session.get_bind().dialect.name != "postgresql":
        return
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": completion_day_lock_key(user_id, day)},
    )


def _constraint_identity(exc):
    """``(constraint_name, message_text)`` of an ``IntegrityError``.

    PostgreSQL (psycopg2/psycopg) names the constraint authoritatively in
    ``exc.orig.diag.constraint_name``; SQLite only names the offending columns in
    its message (``UNIQUE constraint failed: pump_check.user_id,
    pump_check.date_key``)."""
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    return getattr(diag, "constraint_name", None), str(orig if orig is not None else exc)


def is_pump_check_day_violation(exc) -> bool:
    """Return True only for a verified ``uq_pump_check_day`` unique violation.

    Correction #1: never classify an *arbitrary* ``IntegrityError`` as an
    already-completed replay. A foreign-key, NOT NULL, or unrelated-unique
    failure is a real internal error and must be re-raised. Only
    ``uq_pump_check_day`` involves ``pump_check.date_key``.

    Anything we cannot positively identify returns ``False`` (fail closed — treat
    as a real error rather than silently swallowing it as "already done").
    """
    constraint_name, message = _constraint_identity(exc)
    if constraint_name:
        return constraint_name == PUMP_CHECK_DAY_CONSTRAINT
    return PUMP_CHECK_DAY_CONSTRAINT in message or "pump_check.date_key" in message


def is_completion_day_violation(exc) -> bool:
    """True only for a verified daily-completion claim violation (PRIV-K6-B1):
    ``uq_workout_completion_day`` or the historical ``uq_pump_check_day``.

    The SQLite token ``workout_completion_day.date_key`` belongs only to
    ``uq_workout_completion_day`` (its other columns are the PK, ``user_id``,
    ``proof`` and ``created_at``; the ``proof`` CHECK names the constraint, not
    the column). Same fail-closed rule as :func:`is_pump_check_day_violation`:
    a CHECK, NOT NULL, FK or unrelated-unique failure is never a replay.
    """
    if is_pump_check_day_violation(exc):
        return True
    constraint_name, message = _constraint_identity(exc)
    if constraint_name:
        return constraint_name == COMPLETION_DAY_CONSTRAINT
    return (
        COMPLETION_DAY_CONSTRAINT in message
        or "UNIQUE constraint failed: workout_completion_day.user_id, "
           "workout_completion_day.date_key" in message
    )


# ── Session terminalization (Sprint 7 PR3) ───────────────────────────────────
# The completion authority owns the ACTIVE→COMPLETED transition of a linked
# session, inside its single transaction, so a session is never left ACTIVE after
# its day is completed and COMPLETED is never written without a completion claim.
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
        # A route-level preflight may already have loaded this identity.  The
        # row lock must refresh it from PostgreSQL; otherwise SQLAlchemy can
        # return the stale identity-map state and defeat the locked revision
        # guard after a concurrent checkpoint commits.
        .populate_existing()
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
