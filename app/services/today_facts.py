"""AxisAI Today canonical-facts read layer.

The SINGLE read-only source of truth that web Today and the existing
``/training-plan/active`` and ``/workout/status`` endpoints share, so the page and
the endpoints can never disagree about whether a plan is active or today's workout
is done. This module owns the presentation-facing *composition*; the presenter
(``app/today_presenter.py``) owns the presentation and stays pure.

Read-only: no writes, no AI, no HTTP.

Canonical sources (never re-implemented here):
  * active plan — the exact selector ``/training-plan/active`` uses (the most
    recently created ``TrainingPlan``); reused verbatim so an archived/superseded
    older plan is never treated as active.
  * today's date — ``app.timeutil.app_today()`` (Istanbul), the one clock
    authority. No client date, request parameter or naive local date call reaches
    this module, and the day is captured ONCE so every read in one render
    describes the same day.
  * today's workout state — delegated to the canonical workout-state owner,
    ``app.services.workout_state.resolve_workout_state`` (Sprint 7 PR1). That
    resolver *is* the authority behind ``/workout/status``; Today reads
    ``primary_state`` / ``action`` / ``completed_today`` from it rather than
    re-deriving any of them. It is itself read-only and fail-safe (an
    unexpected read failure yields a safe snapshot with a set ``anomaly`` rather
    than raising), which ``gather_today_facts`` surfaces as an honest error state
    — never a fabricated populated one.
  * today's plan content — ``workout_state.serialization.serialize_today_plan``,
    the bounded projection ``/training/bootstrap`` already publishes, narrowed to
    a summary exactly as ``app/services/mobile_today`` narrows it.
  * the observation about the user's current state —
    ``app.services.progress_insights.build_progress_insights``, the same
    deterministic, LLM-free read model ``GET /api/progress/axis-insights``
    publishes. Today re-publishes AT MOST ONE of its three slots; it does not
    select, rank, threshold or phrase a signal of its own. There is no second
    insight authority here and no provider call on this page.

This composition deliberately mirrors ``app/services/mobile_today._project`` so
the web and mobile Today surfaces read one contract. It differs in exactly two
ways, both required by the surface:

  * it does NOT open a ``coherent_read_snapshot()`` — that helper calls
    ``db.session.remove()``, which would detach the request-scoped
    ``current_user`` the surrounding page still renders from;
  * a canonical read failure degrades to an honest ``read_ok=False`` view instead
    of raising, because Today is the app's home page: it must still render its
    shell, navigation and non-training content when training state is unreadable.
"""
from __future__ import annotations

import json

from flask import current_app

from app.models import TrainingPlan
from app.services.progress_insights import (SLOT_AVAILABLE,
                                            build_progress_insights)
from app.services.workout_state import resolve_workout_state
from app.services.workout_state.models import ANOMALY_RESOLUTION_ERROR
from app.services.workout_state.serialization import (PlanSerializationError,
                                                      serialize_today_plan)
from app.timeutil import app_today
from app.today_presenter import (INSIGHT_WATCH, INSIGHT_WORKING, TodayFacts,
                                 TodayPlanSummary)

# The flag that selects the workout-state contract version. Read here for the
# same reason ``/training/bootstrap`` and ``/api/v1/today`` read it: the value must
# be captured ONCE and passed in, so every read in one response describes one
# contract.
_SESSIONS_FLAG = "FITX_WORKOUT_SESSIONS_ENABLED"


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
    current workout-state and the same signal ``/workout/status`` returns. Today's
    server-rendered primary action and that endpoint therefore always share one
    completion signal, with the day-bounded ``PumpCheck`` query living in exactly
    one place (``app/services/workout_state``), never duplicated here.
    """
    return resolve_workout_state(user_id).completed_today


def gather_today_facts(user_id) -> TodayFacts:
    """Gather the canonical facts Today needs, tolerating read failure.

    A DB/read error yields ``read_ok=False`` so the presenter surfaces an honest
    error state instead of a fabricated populated one. The canonical workout-state
    resolver fails safe on an unexpected read failure — a ``resolution_error``
    anomaly — which we treat as a read failure so Today does not report a
    possibly-wrong state. *Domain* anomalies (an unparseable schedule, a
    completion-marker mismatch) are honest classifications the resolver already
    expresses through ``primary_state`` (``needs_attention``), so they do NOT force
    the error state — surfacing "your plan needs attention" is more useful, and
    more truthful, than "unavailable".
    """
    try:
        day = app_today()
        plan = get_active_plan(user_id)
        snapshot = resolve_workout_state(
            user_id,
            today=day,
            plan=plan,
            sessions_enabled=_sessions_enabled(),
        )
    except Exception:  # noqa: BLE001 - any canonical read fault => honest error
        return TodayFacts(
            read_ok=False, has_active_plan=False, workout_completed_today=False
        )
    if snapshot.anomaly == ANOMALY_RESOLUTION_ERROR:
        # The canonical resolver hit an unexpected read failure and failed safe;
        # surface an honest error state rather than a fabricated product state.
        return TodayFacts(
            read_ok=False, has_active_plan=False, workout_completed_today=False
        )
    insight_kind, insight_code = _gather_insight(user_id, day)
    return TodayFacts(
        read_ok=True,
        has_active_plan=plan is not None,
        workout_completed_today=snapshot.completed_today,
        primary_state=snapshot.primary_state,
        action=snapshot.action,
        plan=_plan_summary(plan, day),
        insight_kind=insight_kind,
        insight_code=insight_code,
    )


def _sessions_enabled() -> bool:
    try:
        return bool(current_app.config.get(_SESSIONS_FLAG, False))
    except Exception:  # noqa: BLE001 - no app context / missing key => OFF
        return False


def _plan_summary(plan, day) -> "TodayPlanSummary | None":
    """A bounded summary of today's canonical plan day, or ``None``.

    ``None`` means the canonical projection has no publishable day for today (no
    plan, an unreadable schedule, or content outside the public bounds). It never
    means "rest day" — a rest day still has a publishable row, and whether today
    rests is ``primary_state``'s answer, never this summary's.

    An unparseable plan is a *domain* condition, not an infrastructure fault — the
    canonical resolver already classifies it as ``needs_attention`` — so the caught
    set here is exactly the one ``workout_state.queries._load_schedule`` catches,
    and the summary can never disagree with the canonical schedule state about
    whether the plan was readable.
    """
    if plan is None:
        return None
    try:
        plan_data = json.loads(plan.plan_data)
    except (ValueError, TypeError):
        return None
    try:
        today_plan = serialize_today_plan(plan_data, day)
    except PlanSerializationError:
        return None
    if today_plan is None:
        return None
    return TodayPlanSummary(
        focus=today_plan["odak"],
        duration_min=today_plan["sure_dk"],
        exercise_count=len(today_plan["egzersizler"]),
    )


def _gather_insight(user_id, day):
    """The one canonical Axis Insights slot Today may re-publish.

    Returns ``(kind, code)`` — ``("watch", "deload_due")``, ``("working",
    "training_consistent")`` — or ``(None, None)``.

    The selection rule is a PRESENTATION choice, not a signal: WATCH THIS wins
    over WHAT'S WORKING because a concern the user has not seen matters more on a
    daily surface than a compliment they have. Both slots were already decided by
    ``progress_insights``; nothing is re-ranked, re-thresholded or re-worded here,
    and NEXT MOVE is deliberately never taken (it is a week-level training
    instruction that would compete with Today's single primary action).

    Fails SOFT and SILENT. This is a secondary observation on the app's home
    page: a broken or drifted insight read must degrade to no insight, never to a
    reassuring one, and never to a broken Today. The canonical training state
    above is unaffected — the two reads are independent by construction.
    """
    try:
        insights = build_progress_insights(user_id, end_day=day)
    except Exception:  # noqa: BLE001 - a secondary observation never breaks Home
        return (None, None)
    for kind, slot in ((INSIGHT_WATCH, insights.watch),
                       (INSIGHT_WORKING, insights.working)):
        if slot.status == SLOT_AVAILABLE and slot.code:
            return (kind, slot.code)
    return (None, None)
