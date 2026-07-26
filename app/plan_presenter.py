"""AxisAI UIUX Sprint 1 PR3 — Plan presentation contract (PURE).

Maps already-gathered canonical facts (``PlanFacts``) to a presentation-only
``PlanView``: a stable, non-localized *page* state identifier, AT MOST ONE dominant
primary action, subordinate secondary actions (each pointing at an EXISTING
canonical route via a localization KEY, never translated copy), and the parsed —
but not re-authored — plan content the template renders.

Like ``app/today_presenter.py`` this module owns NO business rules and performs NO
I/O: no SQLAlchemy session, no model query, no write, no AI/HTTP call, no
timezone-bound calculation. It is a deterministic function of its frozen input. In
particular it makes NONE of legacy ``static/training.js``'s forbidden client-side
authority decisions — it does not pick "today's" workout, does not infer a rest day
from an empty exercise list, and does not derive completion from browser storage.

Two INDEPENDENT state models live here (answer.txt §3):

  * the **Plan page state** (``read_error`` / ``no_active_plan`` / ``active_plan`` /
    ``partial_active_plan``) — whole-page identity; and
  * the **weekly-program section state** — a subordinate, independent surface. The
    server only ever emits ``loading`` (mount handed to ``weekly_program.js``) or
    ``disabled`` (flag off / no plan context); ``populated`` / ``missing_baseline`` /
    ``insufficient_data`` / ``partial`` / ``error`` are CLIENT runtime states that
    ``weekly_program.js`` owns after mount. A weekly-section failure must never
    collapse an otherwise-valid active plan.

State identifiers are canonical, not copy: tests assert on them and they must never
be derived from, or leak, the translated label.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Existing canonical routes (no route is invented here; these mirror paths
#    already owned by app/nav.py). The Plan page itself is /training, so it is
#    deliberately NOT offered as an action from within Plan (no self-link). ──
_ROUTE_COACH = "/coach"
_ROUTE_PROGRESS = "/progress-page"
# The canonical prerequisite for AI plan generation (creates the UserSession the
# generator needs). A ROUTE, not a self-link to /training (answer.txt §2).
_ROUTE_SETUP = "/setup"

# ── Stable, non-localized Plan *page* state identifiers (answer.txt §3). ──
STATE_READ_ERROR = "read_error"
STATE_NO_ACTIVE_PLAN = "no_active_plan"
STATE_ACTIVE_PLAN = "active_plan"
STATE_PARTIAL_ACTIVE_PLAN = "partial_active_plan"

# ── Weekly-program *section* state identifiers (answer.txt §3). Only LOADING and
#    DISABLED are emitted at server-render time; the remaining values are the
#    runtime states weekly_program.js transitions through after it owns the mount,
#    declared here so the contract is complete and testable. ──
WEEKLY_LOADING = "loading"
WEEKLY_POPULATED = "populated"
WEEKLY_MISSING_BASELINE = "missing_baseline"
WEEKLY_INSUFFICIENT_DATA = "insufficient_data"
WEEKLY_PARTIAL = "partial"
WEEKLY_ERROR = "error"
WEEKLY_DISABLED = "disabled"

# The one canonical rest-day marker the plan data itself carries. A rest day is a
# rest day ONLY because the plan explicitly labels it so — never because an
# exercise list happens to be empty (answer.txt §8).
REST_DAY_KIND = "dinlenme"


@dataclass(frozen=True)
class PlanExercise:
    """One exercise line, presentation-only. Every field is neutral text carried
    verbatim from canonical plan data (escaped by the template); nothing here is
    interpreted, ranked, or relabelled."""

    name: str = ""
    sets: str = ""
    reps: str = ""
    rest: str = ""
    note: str = ""


@dataclass(frozen=True)
class PlanDay:
    """One day of the plan in canonical order.

    ``is_rest`` is True ONLY when the plan explicitly marks the day as a rest day
    (``kind == "dinlenme"``). An empty ``exercises`` tuple does NOT make a day a
    rest day (answer.txt §8) — it stays a non-rest day with no listed exercises,
    which the template surfaces honestly rather than inventing "rest".
    """

    label: str = ""          # gun — day label; unknown labels stay neutral (verbatim)
    kind: str = ""           # tip — canonical kind token, carried verbatim
    is_rest: bool = False
    focus: str = ""          # odak — neutral focus text
    duration_min: int = 0    # sure_dk
    est_calories: int = 0    # tahmini_kalori
    exercises: tuple = ()     # tuple[PlanExercise], canonical order preserved


@dataclass(frozen=True)
class PlanFacts:
    """Canonical facts gathered by the read layer (``app/services/plan_facts``).

    ``read_ok`` is False when a canonical read failed → the presenter surfaces an
    honest error state and never a fabricated populated one. ``parse_ok`` is False
    when an active plan row exists but its ``plan_data`` could not be parsed into a
    usable ordered day structure → the presenter surfaces ``partial_active_plan``,
    never ``no_active_plan`` (malformed data is NOT "no plan", answer.txt §8). The
    remaining fields are only meaningful when ``read_ok`` is True.
    """

    read_ok: bool
    has_active_plan: bool
    parse_ok: bool
    days: tuple = ()          # tuple[PlanDay], canonical order preserved
    score: object = None       # opaque canonical score, carried verbatim or None
    created_at: object = None  # display string or None


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
class PlanView:
    """Presentation-only view of Plan: a canonical page-state id, at most one
    dominant primary action (``None`` is valid and is the norm for a populated
    plan — the plan content itself is the destination), subordinate secondary
    actions, the subordinate weekly-section state, and the parsed plan content."""

    state: str
    primary: "Action | None"
    secondary: tuple
    weekly_section_state: str
    days: tuple = ()
    score: object = None
    created_at: object = None
    partial: bool = False


# Secondary actions available whenever a plan surface is shown. Deliberately does
# NOT include Plan itself (no self-link) and does NOT invent "edit"/"regenerate":
# the canonical regenerate path is the in-page generator, only reachable from the
# no_active_plan state; re-entering it from a populated plan is out of scope for
# this PR and is documented as omitted rather than fabricated (answer.txt §2, §8).
_PLAN_SECONDARY = (
    Action("plan.action.open_coach", _ROUTE_COACH),
    Action("plan.action.view_progress", _ROUTE_PROGRESS),
)


def _weekly_state(page_state: str, weekly_enabled: bool) -> str:
    """The weekly-section state to render.

    The section is only mounted when it has a plan context (an active or partial
    plan) AND its own flag is on; otherwise it is ``disabled`` and the template
    omits it entirely — no indefinite loading mount, no error (answer.txt §6).
    """
    has_plan_context = page_state in (STATE_ACTIVE_PLAN, STATE_PARTIAL_ACTIVE_PLAN)
    if weekly_enabled and has_plan_context:
        return WEEKLY_LOADING
    return WEEKLY_DISABLED


def build_plan_view(facts: PlanFacts, weekly_enabled: bool = False) -> PlanView:
    """Pure mapping: canonical facts → presentation view. No I/O, no rules.

    ``weekly_enabled`` is the server-owned ``WEEKLY_PROGRAM_UI_ENABLED`` value,
    passed in by the route; the presenter never reads config itself.
    """
    # Honest failure: a canonical read failed. Do NOT present a plan, do NOT
    # convert to no_active_plan. Offer no dominant CTA and no "Open Plan" (the user
    # is already on Plan); the template renders a safe retry (answer.txt §2).
    if not facts.read_ok:
        return PlanView(
            state=STATE_READ_ERROR,
            primary=None,
            secondary=_PLAN_SECONDARY,
            weekly_section_state=_weekly_state(STATE_READ_ERROR, weekly_enabled),
        )

    # No canonical active plan → the one honest next step is to create one via the
    # existing in-page generator (rendered by the template). No dominant navigation
    # CTA and no self-link to /training; the generator's own submit is the action.
    if not facts.has_active_plan:
        return PlanView(
            state=STATE_NO_ACTIVE_PLAN,
            primary=None,
            secondary=(),
            weekly_section_state=_weekly_state(STATE_NO_ACTIVE_PLAN, weekly_enabled),
        )

    # An active plan row exists but its data could not be parsed into a usable
    # ordered structure → honest partial, NEVER "no plan" (answer.txt §8). Whatever
    # parsed cleanly (possibly nothing) is still shown; the plan stays visible and
    # the weekly section may still mount (it reads workout history, not plan_data).
    if not facts.parse_ok or not facts.days:
        return PlanView(
            state=STATE_PARTIAL_ACTIVE_PLAN,
            primary=None,
            secondary=_PLAN_SECONDARY,
            weekly_section_state=_weekly_state(
                STATE_PARTIAL_ACTIVE_PLAN, weekly_enabled
            ),
            days=facts.days,
            score=facts.score,
            created_at=facts.created_at,
            partial=True,
        )

    # Populated active plan: the plan content itself IS the destination → NO
    # dominant CTA (answer.txt §2). Coach/Progress remain subordinate secondaries.
    return PlanView(
        state=STATE_ACTIVE_PLAN,
        primary=None,
        secondary=_PLAN_SECONDARY,
        weekly_section_state=_weekly_state(STATE_ACTIVE_PLAN, weekly_enabled),
        days=facts.days,
        score=facts.score,
        created_at=facts.created_at,
    )
