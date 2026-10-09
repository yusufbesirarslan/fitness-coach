"""Bounded native check-in history + current week (LP16-B). Read-only.

Rows: ``progress_history.fetch_qualifying_checkins`` — the existing
qualifying read (``yogunluk IS NOT NULL``, ``created_at DESC, id DESC``), so
weight-only ``/update-weight`` rows never appear and same-timestamp rows have
the canonical order. The id tie-breaker orders; it is never published.

Weight delta: the Progress History rule, reused, not re-derived —
``previous_daily_row`` (latest valid weight on an OLDER Istanbul day; same-day
check-ins are never each other's prior point) + ``historical_body`` (the
``summarize_body`` two-point subtraction/rounding). History applies that rule
inside its bounded read; here the visible window is the last 12 rows, so when
the read was cut and the prior point lies past it, ONE bounded lookup finds
the same point (latest qualifying row with a positive weight created before
the start of that Istanbul day). Not derivable → ``null``.

Legacy values outside the native contract are ``null``, never 0 or 3.

``current_week`` is display semantics only — Monday–Sunday in
Europe/Istanbul; ``submitted`` is true when at least one FULL check-in exists
in it. It is not a uniqueness rule and never says "can't submit".
"""
from __future__ import annotations

from datetime import timedelta

from app.extensions import db
from app.models import WeeklyCheckIn
from app.services.progress_history import (fetch_qualifying_checkins,
                                           historical_body,
                                           previous_daily_row)
from app.services.weekly_checkin import FULL_CHECKIN
from app.timeutil import APP_TZ, app_date_of, app_today, utc_day_bounds

from .contract import (CONTRACT_VERSION, OVERLOAD_STORED_TO_WIRE, RATING_MAX,
                       RATING_MIN, analysis_day, iso_checked_in_at)

# Visible bound of the launch contract: the last 12 full check-ins.
HISTORY_LIMIT = 12
TIMEZONE = APP_TZ.key


def _rating(value):
    if isinstance(value, int) and not isinstance(value, bool) and \
            RATING_MIN <= value <= RATING_MAX:
        return value
    return None


def _prior_day_row(user_id, day):
    """Latest qualifying row with a positive weight before Istanbul ``day``.

    The same point ``previous_daily_row`` would reach if the bounded read had
    not been cut: rows are ``created_at``-ordered, so "an older Istanbul day"
    is exactly "created before that day's UTC start".
    """
    day_start_utc, _day_end_utc = utc_day_bounds(day)
    with db.session.no_autoflush:
        return (WeeklyCheckIn.query
                .filter_by(user_id=user_id)
                .filter(FULL_CHECKIN,
                        WeeklyCheckIn.created_at < day_start_utc,
                        WeeklyCheckIn.weight > 0)
                .order_by(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc())
                .first())


def _item(row, weight_kg, weight_delta_kg):
    """One history item. Hand-written field list: no id/owner/key/feedback."""
    return {
        "checked_in_at": iso_checked_in_at(row.created_at),
        "analysis_day": analysis_day(row.created_at),
        "weight_kg": weight_kg,
        "weight_delta_kg": weight_delta_kg,
        "training_intensity": _rating(row.yogunluk),
        "fatigue": _rating(row.fatigue),
        "sleep_quality": _rating(row.uyku_kalitesi),
        "nutrition_adherence": _rating(row.beslenme_uyumu),
        "progressive_overload": OVERLOAD_STORED_TO_WIRE.get(
            row.progressive_overload),
    }


def _items(user_id):
    rows = fetch_qualifying_checkins(user_id, HISTORY_LIMIT + 1)
    cut = len(rows) > HISTORY_LIMIT
    beyond = {}
    items = []
    for index, row in enumerate(rows[:HISTORY_LIMIT]):
        previous = previous_daily_row(rows, index)
        if previous is None and cut:
            day = app_date_of(row.created_at)
            if day is not None:
                if day not in beyond:
                    beyond[day] = _prior_day_row(user_id, day)
                previous = beyond[day]
        weight_kg, weight_delta_kg = historical_body(row, previous)
        items.append(_item(row, weight_kg, weight_delta_kg))
    return items


def current_week(user_id, today):
    """Monday–Sunday Istanbul week of ``today`` and whether it has a FULL check-in."""
    start_day = today - timedelta(days=today.weekday())
    end_day = start_day + timedelta(days=6)
    week_start_utc, _ = utc_day_bounds(start_day)
    _, week_end_utc = utc_day_bounds(end_day)
    with db.session.no_autoflush:
        submitted = (db.session.query(WeeklyCheckIn.id)
                     .filter(WeeklyCheckIn.user_id == user_id,
                             FULL_CHECKIN,
                             WeeklyCheckIn.created_at >= week_start_utc,
                             WeeklyCheckIn.created_at < week_end_utc)
                     .first()) is not None
    return {
        "timezone": TIMEZONE,
        "start_day": start_day.isoformat(),
        "end_day": end_day.isoformat(),
        "submitted": submitted,
    }


def build_history(user_id):
    """The closed GET body for ``user_id`` (the Bearer owner; nothing else)."""
    today = app_today()
    return {
        "contract_version": CONTRACT_VERSION,
        "current_week": current_week(user_id, today),
        "check_ins": _items(user_id),
    }
