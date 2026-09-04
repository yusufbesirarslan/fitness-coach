"""AxisAI UX-2 PR4 — Today presentation contract (PURE).

Maps already-gathered canonical facts (``TodayFacts``) to a presentation-only
``TodayView``: one stable, non-localized state identifier + AT MOST ONE dominant
primary action + subordinate secondary actions, each pointing at an EXISTING
canonical route via a localization KEY (never translated copy).

This module owns NO business rules and performs NO I/O — no SQLAlchemy session,
no model query, no write, no AI/HTTP call, no timezone-bound calculation. It is a
deterministic function of its frozen input. It does not decide *which* workout the
user should do, does not rank actions with new heuristics, and never claims an
action is "recommended": the repository has not made that determination. When the
canonical read failed it surfaces an honest error state — it does not fabricate a
populated one, and does not convert an error into "no plan".

**State vocabulary (UX-2 PR4).** The four PR2-era identifiers
(``plan_ready`` / ``workout_done``) were a *fourth* Today vocabulary layered on top
of the canonical workout-state contract. PR4 retires them: the state ids below are
re-exported VERBATIM from ``app.services.workout_state.models`` (the same
``primary_state`` ``GET /workout/status`` and ``GET /api/v1/today`` publish), so
web Today, mobile Today and the status endpoint can never describe the same user
in three different words. ``docs/WORKOUT_STATE.md`` owns the enum; this module
owns only how it is *presented*. ``error`` is the one identifier that is NOT a
canonical product state — it is the honest "we could not read" state and is
deliberately outside the canonical enum.

An unrecognized canonical ``primary_state`` fails to ``error`` rather than to a
guessed presentation: contract drift must surface as unavailability, never as a
confident-looking screen built on a state this module does not understand.

State identifiers are canonical, not copy: tests assert on them and they must
never be derived from, or leak, the translated label.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.workout_state.models import (
    PRIMARY_COMPLETED, PRIMARY_EXECUTION_RECORDED,
    PRIMARY_IN_PROGRESS, PRIMARY_NEEDS_ATTENTION, PRIMARY_NO_PLAN,
    PRIMARY_REST_DAY, PRIMARY_SCHEDULED_NOT_STARTED,
    PRIMARY_UNSCHEDULED_COMPLETED, PRIMARY_UNSCHEDULED_EXECUTION)
from app.today_guidance import (
    CANDIDATE_CREATE_PLAN,
    CANDIDATE_RESUME_WORKOUT,
    CANDIDATE_START_WORKOUT,
    STATE_ERROR,
    decide_today_guidance,
)

# ── Existing canonical routes (no route is invented here; these mirror the
#    paths already owned by app/nav.py). ──
_ROUTE_PLAN = "/training"
_ROUTE_PROGRESS = "/progress-page"

# ── Presentation-state identifiers, re-exported from the canonical contract. ──
STATE_NO_PLAN = PRIMARY_NO_PLAN
STATE_REST_DAY = PRIMARY_REST_DAY
STATE_SCHEDULED = PRIMARY_SCHEDULED_NOT_STARTED
STATE_IN_PROGRESS = PRIMARY_IN_PROGRESS
STATE_EXECUTION_RECORDED = PRIMARY_EXECUTION_RECORDED
STATE_UNSCHEDULED_EXECUTION = PRIMARY_UNSCHEDULED_EXECUTION
STATE_COMPLETED = PRIMARY_COMPLETED
STATE_UNSCHEDULED_COMPLETED = PRIMARY_UNSCHEDULED_COMPLETED
STATE_NEEDS_ATTENTION = PRIMARY_NEEDS_ATTENTION

#: Every state Today can render. A ``primary_state`` outside this set is contract
#: drift and is presented as ``STATE_ERROR``.
TODAY_STATES = (
    STATE_NO_PLAN,
    STATE_REST_DAY,
    STATE_SCHEDULED,
    STATE_IN_PROGRESS,
    STATE_EXECUTION_RECORDED,
    STATE_UNSCHEDULED_EXECUTION,
    STATE_COMPLETED,
    STATE_UNSCHEDULED_COMPLETED,
    STATE_NEEDS_ATTENTION,
    STATE_ERROR,
)

#: Canonical state → the short label the compact status strip shows for TRAINING.
#: Several canonical states legitimately share one short label (a scheduled
#: workout completed and an unscheduled one both read "Done"): the compact strip
#: answers "where do I stand today", and the full distinction is already carried
#: by the brief above it and by the canonical `data-today-state` attribute. This
#: table is TOTAL over ``TODAY_STATES`` — a state without an entry would render a
#: raw key, so ``tests/test_today_v2.py`` pins the coverage.
_TRAINING_STAT_KEYS = {
    STATE_NO_PLAN: "today.stat_training.no_plan",
    STATE_REST_DAY: "today.stat_training.rest",
    STATE_SCHEDULED: "today.stat_training.scheduled",
    STATE_IN_PROGRESS: "today.stat_training.in_progress",
    STATE_EXECUTION_RECORDED: "today.stat_training.recorded",
    STATE_UNSCHEDULED_EXECUTION: "today.stat_training.recorded",
    STATE_COMPLETED: "today.stat_training.done",
    STATE_UNSCHEDULED_COMPLETED: "today.stat_training.done",
    STATE_NEEDS_ATTENTION: "today.stat_training.unavailable",
    STATE_ERROR: "today.stat_training.unavailable",
}

@dataclass(frozen=True)
class TodayPlanSummary:
    """A bounded summary of today's canonical plan day.

    Narrowed from ``workout_state.serialization.serialize_today_plan`` — the same
    bounded projection ``/training/bootstrap`` and ``/api/v1/today`` publish — so
    Today shows the day's shape without duplicating the exercise payload onto a
    third surface. Every field is plan *content* an author wrote; none of it is
    computed, estimated or inferred here.
    """

    focus: str
    duration_min: int
    exercise_count: int


@dataclass(frozen=True)
class TodayFacts:
    """Canonical facts gathered by the read layer (``app/services/today_facts``).

    ``read_ok`` is False when a canonical read failed; the presenter must then
    surface an honest error/unavailable state and never fall through to a
    populated one. Every other field is only meaningful when ``read_ok`` is True.

    ``primary_state`` and ``action`` are carried VERBATIM from the canonical
    workout-state snapshot — this layer re-labels nothing, and it deliberately
    carries no second rest-day boolean: ``primary_state`` already answers that,
    and a duplicate could disagree with it. ``plan`` is
    ``None`` whenever the canonical projection has no publishable day for today
    (no plan, an unreadable schedule, or content outside the public bounds); it is
    never the answer to "is today a rest day?" — that is ``primary_state``'s job.

    ``insight_kind`` / ``insight_code`` are the canonical Axis Insights slot the
    read layer selected (``watch`` or ``working``) and the canonical code that
    slot published, both verbatim. Both are ``None`` when no slot names anything
    or the insight read failed — an absent insight is rendered as absence, never
    as reassurance.
    """

    read_ok: bool
    has_active_plan: bool
    workout_completed_today: bool
    primary_state: str = STATE_ERROR
    action: str = ""
    plan: "TodayPlanSummary | None" = None
    insight_kind: "str | None" = None
    insight_code: "str | None" = None


@dataclass(frozen=True)
class Action:
    """A presentation action pointing at an EXISTING canonical route.

    ``label_key`` is a localization key resolved by the template — never the
    translated string. ``primary`` marks the single dominant CTA (at most one per
    view). ``href`` is an existing route; no route is invented here.
    """

    label_key: str
    href: str
    primary: bool = False


# ── Canonical Axis Insights slot ids (what this surface may show). ──
# The full three-slot Axis Insights surface belongs to Progress
# (docs/PRODUCT_IA.md ownership matrix). Today shows AT MOST ONE of them, and
# NEXT MOVE is deliberately excluded: it is a training instruction for the week
# and would compete with Today's single primary action.
INSIGHT_WATCH = "watch"
INSIGHT_WORKING = "working"

#: ``(slot, canonical code)`` → the localization key Progress already uses for
#: that exact claim. This is a POINTER TABLE, not a second copy of the copy: the
#: sentence lives in ``locales/*.json`` once and both surfaces render the same
#: words, so Today can never phrase a canonical signal differently from Progress.
#: ``tests/test_today_v2.py`` pins the key set against the canonical
#: ``WATCH_CODES`` / ``WORKING_CODES`` tuples, so an upstream code added without
#: a Today mapping fails the suite instead of rendering nothing.
_INSIGHT_LABEL_KEYS = {
    (INSIGHT_WATCH, "build_consistency"): "progress.axis_watch_build_consistency",
    (INSIGHT_WATCH, "deload_due"): "progress.axis_watch_deload_due",
    (INSIGHT_WATCH, "plateau_detected"): "progress.axis_watch_plateau_detected",
    (INSIGHT_WATCH, "volume_trend_down"): "progress.axis_watch_volume_trend_down",
    (INSIGHT_WATCH, "strength_trend_down"): (
        "progress.axis_watch_strength_trend_down"),
    (INSIGHT_WORKING, "training_progressing"): (
        "progress.axis_working_training_progressing"),
    (INSIGHT_WORKING, "training_steady"): (
        "progress.axis_working_training_steady"),
    (INSIGHT_WORKING, "training_consistent"): (
        "progress.axis_working_training_consistent"),
}


@dataclass(frozen=True)
class TodayInsight:
    """One canonical Axis Insight, projected onto Today.

    ``kind`` is the canonical slot (``watch`` / ``working``) so the template can
    label it truthfully — a concern must never be presented as an achievement.
    ``label_key`` resolves to the SAME sentence Progress renders for that code.
    ``href`` points at Progress, which owns the full three-slot surface.
    """

    kind: str
    label_key: str
    href: str = _ROUTE_PROGRESS


@dataclass(frozen=True)
class TodayView:
    """Presentation-only view of Today: a canonical state id, at most one
    dominant primary action (``None`` is valid), subordinate secondary actions,
    today's bounded plan summary when the canonical projection published one, and
    at most one canonical Axis Insight.
    """

    state: str
    brief_key: str
    primary: "Action | None"
    secondary: tuple = ()
    plan: "TodayPlanSummary | None" = None
    insight: "TodayInsight | None" = None

    @property
    def training_stat_key(self) -> str:
        """The compact status strip's TRAINING label key for this state.

        A lookup, not a rule: every canonical state has an entry, so this can
        never fall through to a guess or to a raw key.
        """
        return _TRAINING_STAT_KEYS[self.state]


# ── Secondary (subordinate) actions. ──
# Two rules, both structural rather than per-state bookkeeping:
#   * a view with a dominant CTA carries NO secondary links, so the hierarchy is
#     never "one primary action plus a competing menu";
#   * a view without one ALWAYS carries at least the neutral fallback, so no
#     state can be a dead end — including the ones only a flag-on v2 snapshot can
#     produce (a blocked scheduled day, a blocked in-progress session).
# No entry is a recommendation: each is a neutral doorway to a canonical
# destination the user already owns.
_SECONDARY_FALLBACK = ("today.action.open_plan",)

_SECONDARY: dict = {
    STATE_COMPLETED: ("today.action.view_progress", "today.action.open_plan"),
    STATE_UNSCHEDULED_COMPLETED: (
        "today.action.view_progress",
        "today.action.open_plan",
    ),
}

_HREF_BY_LABEL = {
    "today.action.open_plan": _ROUTE_PLAN,
    "today.action.view_progress": _ROUTE_PROGRESS,
}


_PRIMARY_PRESENTATION = {
    CANDIDATE_RESUME_WORKOUT: (
        "today.action.resume_workout", _ROUTE_PLAN),
    CANDIDATE_START_WORKOUT: (
        "today.action.start_workout", _ROUTE_PLAN),
    CANDIDATE_CREATE_PLAN: (
        "today.action.create_plan", _ROUTE_PLAN),
}


def _primary_for_kind(kind: "str | None") -> "Action | None":
    """Present the decision layer's winning semantic action, if any."""
    presentation = _PRIMARY_PRESENTATION.get(kind)
    if presentation is None:
        return None
    label_key, href = presentation
    return Action(label_key, href, primary=True)


def _secondary_for(state: str, primary: "Action | None") -> tuple:
    if primary is not None:
        return ()
    keys = _SECONDARY.get(state, _SECONDARY_FALLBACK)
    return tuple(Action(key, _HREF_BY_LABEL[key]) for key in keys)


def _insight_for(kind, code) -> "TodayInsight | None":
    """The one canonical Axis Insight Today may show, or ``None``.

    Absence is a first-class answer here. A slot the canonical read model
    reported as ``empty`` or ``insufficient_data`` carries no code, and a code
    this build has no mapping for is treated the same way — Today renders
    nothing rather than a plausible-looking default, exactly as
    ``static/progress_insights.js`` does with the same vocabulary.
    """
    if not kind or not code:
        return None
    label_key = _INSIGHT_LABEL_KEYS.get((kind, code))
    if label_key is None:
        return None
    return TodayInsight(kind=kind, label_key=label_key)


def build_today_view(facts: TodayFacts) -> TodayView:
    """Pure mapping: canonical facts → presentation view. No I/O, no rules."""
    decision = decide_today_guidance(
        read_ok=facts.read_ok,
        primary_state=facts.primary_state,
        action=facts.action,
    )
    state = decision.state
    brief_key = f"today.brief.{decision.emphasis}"
    if state == STATE_ERROR:
        # Read failure, contract drift, and incompatible state/action dimensions
        # all fail closed. Never publish plan or insight content beside a state
        # that could not be validated as one coherent canonical snapshot.
        return TodayView(
            state=STATE_ERROR, brief_key=brief_key, primary=None,
            secondary=_secondary_for(STATE_ERROR, None),
        )

    primary = _primary_for_kind(decision.primary_kind)
    return TodayView(
        state=state,
        brief_key=brief_key,
        primary=primary,
        secondary=_secondary_for(state, primary),
        plan=facts.plan,
        insight=_insight_for(facts.insight_kind, facts.insight_code),
    )
