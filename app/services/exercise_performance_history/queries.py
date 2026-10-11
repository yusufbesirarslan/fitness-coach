"""The only impure layer of TD-01: exactly ONE owner-scoped, read-only SELECT.

The owner's COMPLETED ``WorkoutSession`` rows whose Istanbul ``workout_date`` is
one of the HISTORY_DAYS + 1 day keys ending at the anchor, newest first by
``completed_at`` (NULLs last), ties broken by ``public_id`` in byte order, at
most MAX_SCAN_ROWS rows.

* Day keys are matched by exact ``IN`` equality on server-derived ISO strings,
  so no database collation or timezone can move a window boundary.
* The exercise ID never enters SQL: exercise membership is decided only by the
  canonical checkpoint parser, after selection, so a corrupt row is never
  hidden by a prefilter.
* Only five columns are selected. Execution context and prescription columns
  are never read (TD-01 T7).
* Nothing here adds, flushes, commits or locks.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import nullslast

from app.extensions import db
from app.models import WORKOUT_SESSION_COMPLETED, WorkoutSession

from .models import HISTORY_DAYS, MAX_SCAN_ROWS, CandidateRow

_COLUMNS = (
    WorkoutSession.public_id,
    WorkoutSession.workout_date,
    WorkoutSession.completed_at,
    WorkoutSession.checkpoint_revision,
    WorkoutSession.checkpoint_data,
)


def day_keys(anchor: date) -> tuple:
    """Istanbul ISO day keys ``anchor - HISTORY_DAYS`` .. ``anchor``, inclusive."""
    return tuple((anchor - timedelta(days=offset)).isoformat()
                 for offset in range(HISTORY_DAYS, -1, -1))


def _tie_key():
    """One comparator on every dialect: ``public_id`` in byte order.

    PostgreSQL's default collation is locale-aware (``en_US.utf8`` in CI), so the
    tie-break names ``COLLATE "C"`` explicitly. SQLite has no "C" collation; its
    default ``BINARY`` collation already compares bytes. Reading the bind's
    dialect issues no SQL.
    """
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        return WorkoutSession.public_id.collate("C")
    if dialect == "sqlite":
        return WorkoutSession.public_id
    raise RuntimeError("unsupported database for exercise performance history")


def load_candidates(user_id: int, anchor: date):
    """``(rows, scan_limit_reached)`` for one owner and one anchor day.

    ``scan_limit_reached`` is True whenever the row bound was reached, even if
    exactly MAX_SCAN_ROWS rows existed: the error is always toward incomplete
    coverage, never toward ``no_history``.
    """
    if type(user_id) is not int:
        raise TypeError("user_id must be the authenticated owner's integer id")
    rows = (
        db.session.query(*_COLUMNS)
        .filter(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == WORKOUT_SESSION_COMPLETED,
            WorkoutSession.workout_date.in_(day_keys(anchor)),
        )
        .order_by(nullslast(WorkoutSession.completed_at.desc()), _tie_key().desc())
        .limit(MAX_SCAN_ROWS)
        .all()
    )
    candidates = tuple(CandidateRow(
        public_id=row.public_id, workout_date=row.workout_date, completed_at=row.completed_at,
        checkpoint_revision=row.checkpoint_revision, checkpoint_data=row.checkpoint_data,
    ) for row in rows)
    return candidates, len(rows) >= MAX_SCAN_ROWS
