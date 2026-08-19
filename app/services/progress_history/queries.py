"""Owner-scoped bounded qualifying-check-in reads for Progress History.

This is the only impure module besides the orchestrator. Training data is never
touched here — it reaches history exclusively through ``training_progression``.
Body facts are the stored check-in weights themselves, never the current
profile or target.

Read-only is enforced structurally: every statement runs inside
``db.session.no_autoflush``. Nothing here adds, deletes, flushes or commits.
"""
from app.extensions import db
from app.models import WeeklyCheckIn


def _positive(value):
    """A usable historical weight is a positive number.

    Same missing-vs-zero rule as ``progress_summary.queries``: a stored ``0``
    is not a reported weight. History never substitutes the current profile.
    """
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def fetch_qualifying_checkins(user_id: int, limit: int):
    """Newest-first qualifying WeeklyCheckIns for ``user_id``, bounded.

    Qualifying means ``yogunluk IS NOT NULL`` — the existing canonical divider
    ``/checkin-history`` and Progress Summary already use. Sparse
    ``/update-weight`` rows stay out. Ordering is ``created_at DESC, id DESC``
    so same-timestamp rows have a deterministic event order.

    ``limit`` is the caller's bound (visible rows + one prior-context row).
    The full history is never loaded.
    """
    if limit <= 0:
        return ()
    with db.session.no_autoflush:
        rows = (
            WeeklyCheckIn.query
            .filter_by(user_id=user_id)
            .filter(WeeklyCheckIn.yogunluk.isnot(None))
            .order_by(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc())
            .limit(limit)
            .all()
        )
    return tuple(rows)
