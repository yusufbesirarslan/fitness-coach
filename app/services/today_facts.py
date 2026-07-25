"""AxisAI UIUX Sprint 1 PR2 — Today canonical-facts read layer.

The SINGLE read-only source of truth that Today V2 and the existing
``/training-plan/active`` and ``/workout/status`` endpoints share, so the page and
the endpoints can never disagree about whether a plan is active or today's workout
is done. This module owns the presentation-facing *composition*; the presenter
(``app/today_presenter.py``) owns the presentation and stays pure.

Read-only: no writes, no AI, no HTTP.

Canonical sources (never re-implemented here):
  * active plan — the exact selector ``/training-plan/active`` uses (the most
    recently created ``TrainingPlan``); reused verbatim so an archived/superseded
    older plan is never treated as active.
  * today's completion — delegated to the canonical workout-state owner,
    ``app.services.workout_state.resolve_workout_state`` (Sprint 7 PR1). After that
    contract landed, the resolver *is* the completion helper behind
    ``/workout/status``; Today reads ``snapshot.completed_today`` from it instead of
    duplicating the day-bounded ``PumpCheck`` query. The resolver is itself
    read-only and fail-safe (an unexpected read failure yields a safe snapshot with
    a set ``anomaly`` rather than raising), which ``gather_today_facts`` surfaces as
    an honest error state — never a fabricated populated one.
"""
from __future__ import annotations

from app.models import TrainingPlan
from app.services.workout_state import resolve_workout_state
from app.services.workout_state.models import ANOMALY_RESOLUTION_ERROR
from app.today_presenter import TodayFacts


def get_active_plan(user_id):
    """The canonical active ``TrainingPlan`` for a user, or ``None``.

    This is the exact selector ``/training-plan/active`` has always used: the
    most recently created plan. Reused here so Today cannot diverge from the
    endpoint (an archived/superseded older plan is never treated as active
    because a newer row would sort ahead of it).
    """
    return (
        TrainingPlan.query.filter_by(user_id=user_id)
        .order_by(TrainingPlan.created_at.desc())
        .first()
    )


def has_active_plan(user_id) -> bool:
    """Whether a canonical active plan exists (existence of ``get_active_plan``)."""
    return get_active_plan(user_id) is not None


def workout_completed_today(user_id) -> bool:
    """Whether today's workout is completed, from the canonical owner.

    Delegates to ``resolve_workout_state`` (Sprint 7 PR1) — the single owner of
    current workout-state and the same signal ``/workout/status`` now returns.
    Today's server-rendered primary action and that endpoint therefore always
    share one completion signal, with the day-bounded ``PumpCheck`` query living
    in exactly one place (``app/services/workout_state``), never duplicated here.
    """
    return resolve_workout_state(user_id).completed_today


def gather_today_facts(user_id) -> TodayFacts:
    """Gather the canonical facts Today V2 needs, tolerating read failure.

    A DB/read error yields ``read_ok=False`` so the presenter surfaces an honest
    error state instead of a fabricated populated one. The canonical workout-state
    resolver fails safe on an unexpected read failure — a ``resolution_error``
    anomaly with ``completed_today=False`` — which we treat as a read failure so
    Today does not report a possibly-wrong "not completed". *Domain* anomalies
    (an unparseable schedule, a completion-marker mismatch) are honest
    classifications where ``completed_today`` is still trustworthy, so they do NOT
    force the error state. The booleans are only consulted when ``read_ok`` is True.
    """
    try:
        plan_exists = has_active_plan(user_id)
        snapshot = resolve_workout_state(user_id)
    except Exception:
        return TodayFacts(
            read_ok=False, has_active_plan=False, workout_completed_today=False
        )
    if snapshot.anomaly == ANOMALY_RESOLUTION_ERROR:
        # The canonical resolver hit an unexpected read failure and failed safe;
        # surface an honest error state rather than a fabricated "not completed".
        return TodayFacts(
            read_ok=False, has_active_plan=False, workout_completed_today=False
        )
    return TodayFacts(
        read_ok=True,
        has_active_plan=plan_exists,
        workout_completed_today=snapshot.completed_today,
    )
