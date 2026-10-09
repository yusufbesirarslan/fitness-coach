"""User-scoped, read-only body/profile reads for the Progress summary.

The only impure module in the package besides the orchestrator, and deliberately
narrow: training data is never touched here — it reaches the summary exclusively
through ``training_progression``. What this layer reads is the part of the body
picture that has no service in front of it yet (``User`` profile columns and the
``WeeklyCheckIn`` ledger), and it reads it using the *existing* canonical
semantics rather than inventing new ones.

Read-only is enforced structurally, not by convention: every statement runs
inside ``db.session.no_autoflush`` so the summary can never be the thing that
flushes somebody else's pending work, and nothing in the package adds, deletes,
flushes or commits (``tests/test_progress_summary.py`` pins this).
"""
from datetime import datetime

from sqlalchemy import Date, cast, func

from app.extensions import db
from app.models import User, WeeklyCheckIn
from app.timeutil import app_date_of

from .models import WEIGHT_SERIES_POINTS, BodyFacts, WeightPoint

# Latest canonical day plus the previous canonical day.
_DELTA_OBSERVATIONS = 2


def _positive(value):
    """A weight/target only exists when it is a positive number.

    Guards the missing-vs-zero rule at the boundary where it actually bites: a
    stored ``0`` in ``User.target_weight`` is an unset target, not a goal of zero
    kilograms, and publishing ``0.0`` would let the client render "0.0 kg to your
    target". Same precedent as the mobile nutrition boundary, which folds a
    non-positive stored calorie goal to ``null`` (``docs/MOBILE_NUTRITION.md``).
    """
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric > 0 else None


def _analysis_day_expression():
    """SQL equivalent of ``app_date_of`` for supported database engines.

    PostgreSQL has IANA timezone support. SQLite uses the same Python helper as
    History through a deterministic scalar function on this session connection.
    Registration adds no SELECT and respects historical timezone changes.
    """
    connection = db.session.connection()
    if connection.dialect.name == "sqlite":
        raw = connection.connection.driver_connection
        raw.create_function(
            "app_analysis_day", 1,
            lambda stamp: app_date_of(datetime.fromisoformat(stamp)).isoformat()
            if stamp is not None else None,
            deterministic=True,
        )
        return func.app_analysis_day(WeeklyCheckIn.created_at)
    if connection.dialect.name == "postgresql":
        utc_instant = func.timezone("UTC", WeeklyCheckIn.created_at)
        return cast(func.timezone("Europe/Istanbul", utc_instant), Date)
    raise RuntimeError("unsupported database for Progress analysis day")


def _canonical_daily_weights(user_id):
    """Latest valid full check-in per Istanbul day, newest days first.

    Rank before the eight-day cap so same-day rows cannot crowd older days
    out. Timestamp then stable ID descending matches History event ordering.
    One database statement returns at most eight rows; there is no day loop.
    """
    ranked = (
        db.session.query(
            WeeklyCheckIn.id.label("checkin_id"),
            func.row_number().over(
                partition_by=_analysis_day_expression(),
                order_by=(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc()),
            ).label("day_rank"),
        )
        .filter(WeeklyCheckIn.user_id == user_id,
                WeeklyCheckIn.yogunluk.isnot(None),
                WeeklyCheckIn.created_at.isnot(None),
                WeeklyCheckIn.weight > 0,
                WeeklyCheckIn.weight < 1e308)
        .subquery()
    )
    return (
        db.session.query(WeeklyCheckIn)
        .join(ranked, WeeklyCheckIn.id == ranked.c.checkin_id)
        .filter(ranked.c.day_rank == 1)
        .order_by(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc())
        .limit(WEIGHT_SERIES_POINTS)
        .all()
    )


def fetch_body_facts(user_id: int) -> BodyFacts:
    """Canonical body observations for ``user_id`` — no interpretation.

    ``current_weight_kg`` follows the fallback ``/progress-page`` already
    established: the canonical profile weight (``User.weight``, kept current by
    ``weekly_checkin.apply_body_weight`` from both ``/checkin`` and
    ``/update-weight``), falling back to the newest check-in row when the profile
    has none yet. That fallback intentionally accepts *any* check-in row, because
    its job is "what does this user weigh", which a sparse ``/update-weight`` row
    answers perfectly well.

    ``recent_qualifying_weights`` is the stricter set and does **not** merge the
    two concepts: only full weekly check-ins qualify, identified by
    ``yogunluk IS NOT NULL`` — the exact filter ``/checkin-history`` and
    ``/api/progress/insights`` already use to keep sparse weight-only rows out of
    Progress history (BUG-5). Blending them would make a delta appear between two
    rows the rest of Progress does not consider comparable observations.
    """
    with db.session.no_autoflush:
        user = db.session.get(User, user_id)
        if user is None:
            return BodyFacts()

        current = _positive(user.weight)
        if current is None:
            latest_any = (
                WeeklyCheckIn.query
                .filter_by(user_id=user_id)
                .order_by(WeeklyCheckIn.created_at.desc(), WeeklyCheckIn.id.desc())
                .first()
            )
            current = _positive(latest_any.weight) if latest_any else None

        qualifying_rows = _canonical_daily_weights(user_id)

    # Delta and series use the same valid, canonical daily sequence. The query
    # rejects invalid weights before ranking, so a later bad row cannot hide a
    # real observation or manufacture a zero.
    weights = tuple(
        w for w in (_positive(row.weight) for row in qualifying_rows[:_DELTA_OBSERVATIONS])
        if w is not None
    )
    series = []
    for row in qualifying_rows:
        weight = _positive(row.weight)
        if weight is not None and row.created_at is not None:
            series.append(WeightPoint(day=app_date_of(row.created_at), weight_kg=weight))
    series.reverse()  # newest-first query → chronological series

    return BodyFacts(
        current_weight_kg=current,
        target_weight_kg=_positive(user.target_weight),
        recent_qualifying_weights=weights,
        weight_series=tuple(series),
    )
