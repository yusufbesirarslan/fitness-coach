"""Read-only ORM adapters that gather the trusted facts the resolver classifies.

Impure (needs an app/DB context); every classification decision stays in
``resolver.py``. This layer performs **no** writes, no flush, no commit and no
external/AI calls — it only reads existing canonical sources:

* schedule   → newest ``TrainingPlan.plan_data.program`` matched to today's
               Istanbul weekday (the same ``WEEKDAYS``/``VALID_TIPS`` vocabulary
               the plan generator validates against — no second definition).
* completion → today's ``PumpCheck`` (canonical, matching /workout/status and the
               completion idempotency guard).
* execution  → non-marker ``WorkoutLog`` rows via the training-history reader.

Istanbul-day windows come from ``app.timeutil`` (``WorkoutLog``/``PumpCheck``
``created_at`` are naive UTC — never compared raw).
"""
import json
from datetime import date, timedelta
from typing import Optional, Tuple

from app.extensions import db
from app.models import PumpCheck, TrainingPlan
from app.services.training_generation.response_validator import VALID_TIPS, WEEKDAYS
from app.services.training_history import fetch_workout_entries
from app.timeutil import app_date_of, utc_day_bounds

from .models import (
    ACTIVE_SESSION_FACTS_NONE,
    KIND_REST,
    KIND_WORKOUT,
    ActiveSessionFacts,
    WorkoutStateInputs,
)

# The one non-rest, recognized workout tips (antrenman/kardiyo). Derived from the
# canonical ``VALID_TIPS`` so it can never drift from the plan validator.
_REST_TIP = "dinlenme"
_WORKOUT_TIPS = frozenset(VALID_TIPS) - {_REST_TIP}


def _weekday_name_tr(d: date) -> str:
    """Istanbul weekday → the Turkish ``gun`` value used in ``plan_data``.

    ``date.weekday()`` is Monday=0, which lines up with ``WEEKDAYS[0]``
    ("Pazartesi") — the plan generator's own ordering.
    """
    return WEEKDAYS[d.weekday()]


def _load_schedule(user_id: int, today: date) -> Tuple[bool, bool, Optional[str]]:
    """Return ``(has_plan, schedule_valid, today_schedule_kind)``.

    ``schedule_valid`` means ``plan_data`` parsed to a proper 7-day program;
    ``today_schedule_kind`` is ``KIND_WORKOUT``/``KIND_REST`` or ``None`` when
    today's weekday is absent or its ``tip`` is unrecognized. Malformed data is
    reported as ``schedule_valid=False`` — the resolver decides the safe state.
    """
    plan = (TrainingPlan.query
            .filter_by(user_id=user_id)
            .order_by(TrainingPlan.created_at.desc())
            .first())
    if plan is None:
        return False, False, None
    try:
        data = json.loads(plan.plan_data)
    except (ValueError, TypeError):
        return True, False, None
    if not isinstance(data, dict):
        return True, False, None
    program = data.get("program")
    if not isinstance(program, list) or len(program) != 7:
        return True, False, None

    today_name = _weekday_name_tr(today)
    tip = None
    for day in program:
        if isinstance(day, dict) and day.get("gun") == today_name:
            tip = day.get("tip")
            break
    if tip == _REST_TIP:
        return True, True, KIND_REST
    if tip in _WORKOUT_TIPS:
        return True, True, KIND_WORKOUT
    # Structure is valid but today's day/tip is unresolvable — kind None; the
    # resolver maps this to ``schedule_unavailable`` (never a silent rest day).
    return True, True, None


def _load_execution(user_id: int, today: date) -> Tuple[bool, bool, int, bool]:
    """Return ``(completed_today, has_marker_today, real_count_today, stale_prev)``.

    One windowed WorkoutLog read (today + yesterday) plus one PumpCheck read —
    bounded, no N+1. ``stale_prev`` = yesterday had real exercise rows but no
    completion (marker or PumpCheck) that day.
    """
    yesterday = today - timedelta(days=1)

    # Single windowed read for both days (reuse the training-history foundation).
    entries = fetch_workout_entries(user_id, yesterday, today, include_markers=True)
    real_today = marker_today = real_yest = marker_yest = 0
    for e in entries:
        if e.performed_on == today:
            if e.is_marker:
                marker_today += 1
            else:
                real_today += 1
        elif e.performed_on == yesterday:
            if e.is_marker:
                marker_yest += 1
            else:
                real_yest += 1

    # PumpCheck completion for the same 2-day window, bucketed by Istanbul day.
    start_utc = utc_day_bounds(yesterday)[0]
    end_utc = utc_day_bounds(today)[1]
    pump_days = {
        app_date_of(created_at)
        for (created_at,) in db.session.query(PumpCheck.created_at).filter(
            PumpCheck.user_id == user_id,
            PumpCheck.created_at >= start_utc,
            PumpCheck.created_at < end_utc,
        ).all()
    }
    completed_today = today in pump_days
    completed_yest = yesterday in pump_days

    stale_prev = (real_yest > 0) and not (marker_yest > 0 or completed_yest)
    return completed_today, marker_today > 0, real_today, stale_prev


def load_session_facts(user_id: int, today: date) -> ActiveSessionFacts:
    """Load the persisted-session facts for the v2 (flag-ON) enrichment.

    Delegates to the ``workout_session`` read model (the single owner of session
    truth) — read-only, never mutating. Imported lazily so the PR1 read model
    carries no hard dependency on the PR3 session package when the flag is OFF and
    this is never called. Returns :data:`ACTIVE_SESSION_FACTS_NONE` when nothing
    is persisted for the user/day.
    """
    from app.services.workout_session import read_session_for_state

    view = read_session_for_state(user_id, today=today)
    if view is None:
        return ACTIVE_SESSION_FACTS_NONE
    return ActiveSessionFacts(
        present=True,
        status=view.status,
        resumable=view.resumable,
        public_id=view.public_id,
        workout_date=view.workout_date,
        relationship=view.relationship,
        stale_reason=view.stale_reason,
    )


def load_inputs(user_id: int, today: date) -> WorkoutStateInputs:
    """Gather all trusted facts for ``user_id`` on Istanbul day ``today``."""
    has_plan, schedule_valid, today_kind = _load_schedule(user_id, today)
    completed_today, has_marker_today, real_count_today, stale_prev = \
        _load_execution(user_id, today)
    return WorkoutStateInputs(
        today=today,
        has_plan=has_plan,
        schedule_valid=schedule_valid,
        today_schedule_kind=today_kind,
        completed_today=completed_today,
        has_completion_marker_today=has_marker_today,
        real_entry_count_today=real_count_today,
        stale_previous_workout=stale_prev,
    )
