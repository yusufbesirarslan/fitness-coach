"""AxisAI UIUX Sprint 1 PR2 — Today presentation contract (PURE).

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

State identifiers are canonical, not copy: tests assert on them and they must
never be derived from, or leak, the translated label.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Existing canonical routes (no route is invented here; these mirror the
#    paths already owned by app/nav.py). ──
_ROUTE_PLAN = "/training"
_ROUTE_PROGRESS = "/progress-page"

# ── Stable, non-localized presentation-state identifiers. ──
STATE_NO_PLAN = "no_plan"
STATE_PLAN_READY = "plan_ready"
STATE_WORKOUT_DONE = "workout_done"
STATE_ERROR = "error"


@dataclass(frozen=True)
class TodayFacts:
    """Canonical facts gathered by the read layer (``app/services/today_facts``).

    ``read_ok`` is False when a canonical read failed; the presenter must then
    surface an honest error/unavailable state and never fall through to a
    populated one. The two booleans are only meaningful when ``read_ok`` is True.
    """

    read_ok: bool
    has_active_plan: bool
    workout_completed_today: bool


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


@dataclass(frozen=True)
class TodayView:
    """Presentation-only view of Today: a canonical state id, at most one
    dominant primary action (``None`` is valid), and subordinate secondary
    actions."""

    state: str
    primary: "Action | None"
    secondary: tuple


def build_today_view(facts: TodayFacts) -> TodayView:
    """Pure mapping: canonical facts → presentation view. No I/O, no rules."""
    # Honest failure: a canonical read failed. Do NOT present plan_ready, do NOT
    # convert to no_plan, do NOT claim the state is current. Offer only a neutral
    # secondary "Open Plan" (safe existing route); render no dominant CTA.
    if not facts.read_ok:
        return TodayView(
            state=STATE_ERROR,
            primary=None,
            secondary=(Action("today.action.open_plan", _ROUTE_PLAN),),
        )

    # No canonical active plan → the one honest next step is to create one.
    if not facts.has_active_plan:
        return TodayView(
            state=STATE_NO_PLAN,
            primary=Action("today.action.create_plan", _ROUTE_PLAN, primary=True),
            secondary=(),
        )

    # Active plan + today's workout already completed → explicit completion. It
    # is acceptable to render no dominant CTA; keep review/plan as secondary and
    # never a stale "Start".
    if facts.workout_completed_today:
        return TodayView(
            state=STATE_WORKOUT_DONE,
            primary=None,
            secondary=(
                Action("today.action.view_progress", _ROUTE_PROGRESS),
                Action("today.action.open_plan", _ROUTE_PLAN),
            ),
        )

    # Active plan exists and the completion signal is false. This means ONLY
    # that — PR2 has NOT selected today's workout. Neutral canonical label →
    # Plan (never "today's plan / today's workout / recommended").
    return TodayView(
        state=STATE_PLAN_READY,
        primary=Action("today.action.view_plan", _ROUTE_PLAN, primary=True),
        secondary=(Action("today.action.view_progress", _ROUTE_PROGRESS),),
    )
