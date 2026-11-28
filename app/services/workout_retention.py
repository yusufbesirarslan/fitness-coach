"""Prospective schedule facts and owner-scoped workout progress."""
from datetime import timedelta

from sqlalchemy import select

from app.extensions import db
from app.models import TrainingSessionOutcome, WorkoutScheduleCoverage
from app.services.workout_completion.queries import completed_days
from app.services.workout_state.queries import schedule_tips
from app.timeutil import app_today

LOOKBACK_DAYS = 366
ONE_DAY = timedelta(days=1)


def stage_schedule(owner, plan, *, starts_plan=False):
    """Write tomorrow's expectation in the canonical plan transaction."""
    today = app_today()
    effective = today + ONE_DAY
    dates = (today, effective) if starts_plan else (effective,)
    existing = {row.effective_date: row for row in
                WorkoutScheduleCoverage.query.filter_by(user_id=owner)
                .filter(WorkoutScheduleCoverage.effective_date.in_(dates)).all()}
    if starts_plan and today not in existing:
        db.session.add(WorkoutScheduleCoverage(user_id=owner, effective_date=today))
    row = existing.get(effective)
    if row is None:
        row = WorkoutScheduleCoverage(user_id=owner, effective_date=effective)
        db.session.add(row)
    row.lineage_id = plan.lineage_id if plan is not None else None
    row.schedule = (
        schedule_tips(plan.plan_data) or []
        if plan is not None else None
    )


def _streak(today, first, coverage, canonical, has_plan):
    if not has_plan:
        return "no_plan", None
    index = len(coverage) - 1
    active = coverage[index] if index >= 0 else None
    if active is None:
        return "unavailable", None
    if active.schedule is None:
        return "unavailable", None
    count = 0
    day = today
    while day >= first:
        while index >= 0 and coverage[index].effective_date > day:
            index -= 1
        if index < 0:
            return "bounded", count
        row = coverage[index]
        if row.schedule is None:
            return "verified", count
        tip = (row.schedule[day.weekday()]
               if isinstance(row.schedule, list) and len(row.schedule) == 7 else None)
        if tip not in ("dinlenme", "antrenman", "kardiyo"):
            return ("unavailable", None) if day == today else ("bounded", count)
        if tip != "dinlenme":
            completed = day in canonical
            if completed:
                count += 1
            elif day != today:
                return "verified", count
        day -= ONE_DAY

    return "bounded", count


def project(owner, today, *, has_plan):
    week = today - timedelta(days=today.weekday())
    first = today - timedelta(days=LOOKBACK_DAYS - 1)
    prior = (select(WorkoutScheduleCoverage.effective_date)
             .filter_by(user_id=owner)
             .where(WorkoutScheduleCoverage.effective_date < first)
             .order_by(WorkoutScheduleCoverage.effective_date.desc()).limit(1)
             .scalar_subquery())
    coverage = WorkoutScheduleCoverage.query.filter_by(user_id=owner).filter(
        WorkoutScheduleCoverage.effective_date <= today,
        (WorkoutScheduleCoverage.effective_date >= first)
        | (WorkoutScheduleCoverage.effective_date == prior),
    ).order_by(WorkoutScheduleCoverage.effective_date).all()
    earliest = min(week, max(first, coverage[0].effective_date)) if coverage else week
    canonical = completed_days(owner, (
        earliest + timedelta(days=offset) for offset in range((today - earliest).days + 1)
    ))
    cardio_days = set(db.session.scalars(select(
        TrainingSessionOutcome.date_key,
    ).filter_by(user_id=owner, session_kind="cardio", outcome="completed").where(
        TrainingSessionOutcome.date_key.between(earliest.isoformat(), today.isoformat())
    )))
    status, streak = _streak(today, first, coverage, canonical, has_plan)
    days = [week + timedelta(days=offset) for offset in range(7)]
    indicators = [
        day <= today and (day in canonical or day.isoformat() in cardio_days)
        for day in days
    ]
    return {"streak_status": status, "streak_days": streak,
            "week_start": week.isoformat(), "completed_days": sum(indicators),
            "week_days": indicators}
