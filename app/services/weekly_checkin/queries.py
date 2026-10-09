"""The canonical WeeklyCheckIn reads the persistence authority needs (LP16-A).

Moved verbatim out of ``app/blueprints/tracking.py``; no read contract is
published from here. Every query is owner-scoped by an explicit ``user_id``.
"""
from __future__ import annotations

from app.extensions import db
from app.models import User, WeeklyCheckIn

# A full check-in is a row with ``yogunluk`` set. Weight-only rows written by
# ``/update-weight`` leave every metric NULL (BUG-5) and are never "full".
FULL_CHECKIN = WeeklyCheckIn.yogunluk.isnot(None)


def lock_owner(user_id):
    """``SELECT user.id … FOR UPDATE``: serialize this owner's keyed submissions.

    Repository lock order position 2 (the ``user`` row). A column query, so the
    identity-mapped user is not reloaded. The unique constraint
    ``uq_weekly_checkin_user_key`` stays the durable backstop.
    """
    db.session.query(User.id).filter_by(id=user_id).with_for_update().one()


def find_by_idempotency_key(user_id, idempotency_key):
    return WeeklyCheckIn.query.filter_by(
        user_id=user_id, idempotency_key=idempotency_key).first()


def latest_full_checkin(user_id):
    """Newest full check-in: ``created_at DESC, id DESC`` (canonical tiebreak)."""
    return (WeeklyCheckIn.query.filter_by(user_id=user_id)
            .filter(FULL_CHECKIN)
            .order_by(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc())
            .first())


def same_app_day_checkin(user_id, day_start_utc, day_end_utc):
    """Any row (full OR weight-only) created inside one Istanbul day.

    Legacy ``/update-weight`` selector, preserved as is: it has NO ORDER BY, so
    with several rows that day the database picks one. Not a canonical
    "today's check-in" read and not for new callers.
    """
    return WeeklyCheckIn.query.filter(
        WeeklyCheckIn.user_id == user_id,
        WeeklyCheckIn.created_at >= day_start_utc,
        WeeklyCheckIn.created_at < day_end_utc,
    ).first()
