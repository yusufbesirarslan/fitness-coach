"""Same-day check-ins are events in History and days in body trends."""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import WeeklyCheckIn
from app.services.progress_history import build_progress_history
from app.services.progress_summary import WEIGHT_SERIES_POINTS, build_progress_summary


def _checkin(user_id, stamp, weight, *, qualifying=True):
    row = WeeklyCheckIn(user_id=user_id, created_at=stamp, weight=weight,
                        yogunluk=3 if qualifying else None)
    db.session.add(row)
    return row


@pytest.mark.parametrize("day_two", [(77.7,), (77.7, 77.7), (77.9, 77.7)])
def test_distinct_days_drive_delta_and_series_while_history_keeps_events(make_user, day_two):
    user = make_user("canonical" + str(len(day_two)) + str(day_two[0]), weight=77.7)
    _checkin(user.id, datetime(2026, 9, 18, 12), 78.0)
    for minute, weight in enumerate(day_two):
        _checkin(user.id, datetime(2026, 9, 26, 18, minute), weight)
    db.session.commit()

    body = build_progress_summary(user.id, end_day=date(2026, 9, 26)).body
    assert [(p.day.isoformat(), p.weight_kg) for p in body.weight_series] == [
        ("2026-09-18", 78.0), ("2026-09-26", 77.7)]
    assert body.current_weight_kg == 77.7
    assert body.weight_delta_kg == -0.3

    history = build_progress_history(user.id)
    assert len([e for e in history.entries if e.analysis_day == date(2026, 9, 26)]) == len(day_two)
    assert history.entries[0].weight_delta_kg == -0.3
    assert WeeklyCheckIn.query.filter_by(user_id=user.id).count() == 1 + len(day_two)


def test_many_same_day_rows_do_not_consume_the_daily_series_cap(make_user):
    user = make_user("canonicalcap", weight=77.7)
    for i in range(WEIGHT_SERIES_POINTS):
        day = date(2026, 9, 26) - timedelta(days=i)
        _checkin(user.id, datetime(day.year, day.month, day.day, 12), 77.7 + i)
    for minute in range(WEIGHT_SERIES_POINTS + 4):
        _checkin(user.id, datetime(2026, 9, 26, 18, minute), 77.7)
    db.session.commit()

    body = build_progress_summary(user.id, end_day=date(2026, 9, 26)).body
    assert len(body.weight_series) == WEIGHT_SERIES_POINTS
    assert len({p.day for p in body.weight_series}) == WEIGHT_SERIES_POINTS
    assert body.weight_series[0].day == date(2026, 9, 19)
    assert body.weight_delta_kg == -1.0


def test_invalid_and_sparse_rows_cannot_replace_valid_daily_observation(make_user):
    user = make_user("canonicalvalid", weight=77.7)
    _checkin(user.id, datetime(2026, 9, 18, 12), 78.0)
    _checkin(user.id, datetime(2026, 9, 26, 12), 77.7)
    _checkin(user.id, datetime(2026, 9, 26, 13), 0.0)
    _checkin(user.id, datetime(2026, 9, 26, 14), 70.0, qualifying=False)
    db.session.commit()

    body = build_progress_summary(user.id, end_day=date(2026, 9, 26)).body
    assert [p.weight_kg for p in body.weight_series] == [78.0, 77.7]
    assert body.weight_delta_kg == -0.3


def test_one_distinct_day_has_no_delta_even_with_multiple_checkins(make_user):
    user = make_user("canonicalone", weight=77.7)
    _checkin(user.id, datetime(2026, 9, 26, 12), 77.9)
    _checkin(user.id, datetime(2026, 9, 26, 13), 77.7)
    db.session.commit()

    body = build_progress_summary(user.id, end_day=date(2026, 9, 26)).body
    assert len(body.weight_series) == 1
    assert body.weight_delta_kg is None


def test_three_distinct_days_remain_three_observations(make_user):
    user = make_user("canonicalthree", weight=77.7)
    for day, weight in ((18, 78.0), (22, 77.9), (26, 77.7)):
        _checkin(user.id, datetime(2026, 9, day, 12), weight)
    db.session.commit()

    body = build_progress_summary(user.id, end_day=date(2026, 9, 26)).body
    assert [p.day.day for p in body.weight_series] == [18, 22, 26]
    assert body.weight_delta_kg == -0.2


def test_utc_midnight_boundary_uses_istanbul_analysis_day_and_id_tiebreak(make_user):
    user = make_user("canonicaltz", weight=77.7)
    _checkin(user.id, datetime(2026, 9, 25, 20, 30), 78.0)  # Sep 25 Istanbul
    _checkin(user.id, datetime(2026, 9, 25, 22, 30), 77.9)  # Sep 26 Istanbul
    stamp = datetime(2026, 9, 26, 12)
    _checkin(user.id, stamp, 77.8)
    _checkin(user.id, stamp, 77.7)  # same timestamp: higher ID wins
    db.session.commit()

    body = build_progress_summary(user.id, end_day=date(2026, 9, 26)).body
    assert [(p.day.isoformat(), p.weight_kg) for p in body.weight_series] == [
        ("2026-09-25", 78.0), ("2026-09-26", 77.7)]
    assert body.weight_delta_kg == -0.3


def test_daily_projection_uses_one_bounded_ledger_query(make_user):
    user = make_user("canonicalquery", weight=77.7)
    for minute in range(20):
        _checkin(user.id, datetime(2026, 9, 26, 12, minute), 77.7)
    _checkin(user.id, datetime(2026, 9, 18, 12), 78.0)
    db.session.commit()
    statements = []

    def capture(_conn, _cursor, sql, _params, _context, _many):
        if "weekly_check_in" in sql.lower() and sql.lstrip().lower().startswith("select"):
            statements.append(sql.lower())

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        body = build_progress_summary(user.id, end_day=date(2026, 9, 26)).body
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "row_number() over" in statements[0]
    assert "limit" in statements[0]
    assert len(body.weight_series) == 2
