"""The only impure layer of TI-03: two bounded, owner-scoped, read-only reads.

1. the owned target session (absent and foreign references are identical);
2. ONE bounded history query: the owner's COMPLETED sessions whose Istanbul
   ``workout_date`` is one of the HISTORY_DAYS + 1 day keys ending at the
   anchor, newest first, at most MAX_HISTORY_ROWS rows.

Day keys are matched by exact equality (an ``IN`` list derived server-side from
``app.timeutil`` dates), so no database collation or timezone can move a
window boundary. Nothing here adds, flushes or commits, and no other table --
``TrainingPlan``, ``WorkoutLog``, ``PumpCheck``, notes -- is read.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import nullslast

from app.extensions import db
from app.models import WORKOUT_SESSION_COMPLETED, WorkoutSession
from app.services.workout_session.execution import owned_session

from .models import HISTORY_DAYS, MAX_HISTORY_ROWS, StoredSession

_COLUMNS = (
    WorkoutSession.public_id,
    WorkoutSession.status,
    WorkoutSession.workout_date,
    WorkoutSession.completed_at,
    WorkoutSession.weekday_slot,
    WorkoutSession.checkpoint_revision,
    WorkoutSession.checkpoint_data,
    WorkoutSession.execution_context_data,
    WorkoutSession.prescription_data,
)


def _stored(row) -> StoredSession:
    return StoredSession(
        ref=row.public_id, status=row.status, workout_date=row.workout_date,
        completed_at=row.completed_at, weekday_slot=row.weekday_slot,
        checkpoint_revision=row.checkpoint_revision or 0,
        checkpoint_data=row.checkpoint_data,
        execution_context_data=row.execution_context_data,
        prescription_data=row.prescription_data,
    )


def load_target(user_id: int, session_ref) -> StoredSession:
    """Raises ``SessionNotFound`` for an absent, malformed or foreign reference."""
    return _stored(owned_session(user_id, session_ref))


def history_day_keys(anchor: date) -> tuple:
    """Istanbul ISO day keys from ``anchor - HISTORY_DAYS`` to ``anchor``."""
    return tuple((anchor - timedelta(days=offset)).isoformat()
                 for offset in range(HISTORY_DAYS, -1, -1))


def load_history(user_id: int, anchor: date):
    """``(rows, truncated)``: bounded completed history ending at ``anchor``.

    ``truncated`` is True when the row bound was reached; callers report it as
    incomplete coverage instead of presenting partial history as complete.
    """
    rows = (
        db.session.query(*_COLUMNS)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == WORKOUT_SESSION_COMPLETED,
            WorkoutSession.workout_date.in_(history_day_keys(anchor)),
        )
        .order_by(nullslast(WorkoutSession.completed_at.desc()),
                  WorkoutSession.public_id.desc())
        .limit(MAX_HISTORY_ROWS)
        .all()
    )
    return tuple(_stored(row) for row in rows), len(rows) >= MAX_HISTORY_ROWS
