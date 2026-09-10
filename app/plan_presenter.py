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

from app.today_guidance import (
    CANDIDATE_RESUME_WORKOUT,
    CANDIDATE_START_WORKOUT,
    decide_today_guidance,
)

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

# ── UX-3 PR3: Training-management placement state (stable, non-localized) ──
# ONE Training-management workflow lives inside Plan, and this is the identifier
# that says which half of it applies. "No plan" creation and "replace the plan I
# already follow" are deliberately DIFFERENT operations (brief §10): the first is
# additive and safe, the second is destructive and must be explicitly confirmed.
# Collapsing them into one "save" identifier is exactly the failure this PR
# exists to prevent, so they never share a state id.
MANAGE_CREATE = "create"           # no active plan → safe entry into generation
MANAGE_REGENERATE = "regenerate"   # active/partial plan → destructive replacement
MANAGE_UNAVAILABLE = "unavailable"  # canonical read failed → offer nothing

# The one canonical rest-day marker the plan data itself carries. A rest day is a
# rest day ONLY because the plan explicitly labels it so — never because an
# exercise list happens to be empty (answer.txt §8).
REST_DAY_KIND = "dinlenme"

# Persisted-session states (mirrored BY VALUE from
# app.services.workout_state.models) in which an ACTIVE session exists that a
# plan replacement would invalidate. Used ONLY to decide whether the destructive
# confirmation must tell the truth about the running workout — never to block,
# re-classify, or repair the session (that authority stays server-side).
_SESSION_CONFLICT_STATES = ("active_resumable", "active_blocked")


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
    workout_read_ok: bool = False
    workout_snapshot: object = None
    execution_bootstrap: object = None
    nutrition_state: str = "unknown"
    nutrition_target_state: str = "unknown"
    nutrition_target_calories: object = None
    nutrition_intake_state: str = "unknown"
    nutrition_consumed_calories: object = None
    nutrition_meal_count: object = None
    nutrition_plan_state: str = "unknown"
    has_nutrition_plan: object = None
    supplements_state: str = "unknown"
    supplements_count: object = None
    # UX-3 PR3 — canonical plan-freshness identity, carried VERBATIM from the
    # persisted row (``lineage_id`` + ``mutation_version``) or ``None`` when
    # there is no active plan / the read failed. The presenter never derives,
    # compares, or advances it; it only hands the server's reading to the
    # template so a later management action can be checked against a FRESH
    # server reading of the same fact.
    plan_revision: object = None
    # Bounded, truthful projection of the persisted-session facts the Training
    # placement must not misrepresent after a plan change (contract v2 only).
    workout_session_state: str = ""
    workout_session_stale_reason: str = ""


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
    workout_state: str = "error"
    workout_action: str = "none"
    workout_action_label_key: str = ""
    execution_bootstrap: object = None
    nutrition_state: str = "unknown"
    nutrition_target_state: str = "unknown"
    nutrition_target_calories: object = None
    nutrition_intake_state: str = "unknown"
    nutrition_consumed_calories: object = None
    nutrition_meal_count: object = None
    nutrition_plan_state: str = "unknown"
    has_nutrition_plan: object = None
    supplements_state: str = "unknown"
    supplements_count: object = None
    # ── UX-3 PR3 Training-management placement ──
    management_state: str = MANAGE_UNAVAILABLE
    # The complete server statement about the plan this render was made against:
    # whether one was present and, when it was, its canonical identity. The
    # browser may only hand this back for comparison against a FRESH server
    # reading; it is not a client-side version and nothing derives one from it.
    management_baseline: object = None
    session_conflict: bool = False
    workout_session_stale_reason: str = ""


# Secondary NAVIGATION actions available whenever a plan surface is shown.
# Deliberately does NOT include Plan itself (no self-link). Regeneration is NOT
# one of these: it is not navigation, it is a Training-management operation on
# this page, so UX-3 PR3 models it as ``management_state`` and the template
# renders it inside the Training placement — subordinate to Start/Resume whenever
# execution is actionable (brief §23), never as a peer link.
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
    decision = decide_today_guidance(
        read_ok=facts.workout_read_ok,
        primary_state=(facts.workout_snapshot.primary_state
                       if facts.workout_snapshot is not None else "unknown"),
        action=(facts.workout_snapshot.action
                if facts.workout_snapshot is not None else "none"),
    )
    workout_action = "none"
    workout_label_key = ""
    if decision.primary_kind == CANDIDATE_START_WORKOUT:
        workout_action = "start"
        workout_label_key = "plan.action.start_workout"
    elif decision.primary_kind == CANDIDATE_RESUME_WORKOUT:
        workout_action = "resume"
        workout_label_key = "plan.action.resume_workout"

    # An ACTIVE persisted session is the one fact a destructive replacement is
    # allowed to change the meaning of, so the confirmation has to know about it.
    # It is read from the canonical snapshot, never inferred from the plan.
    session_conflict = facts.workout_session_state in _SESSION_CONFLICT_STATES

    # ``present`` is the read layer's answer, not a guess from the revision:
    # a plan whose identity could not be read is still a plan that exists, and
    # collapsing that to "no plan" is what would let it be silently replaced.
    baseline = {"present": bool(facts.read_ok and facts.has_active_plan)}
    if isinstance(facts.plan_revision, dict):
        baseline.update(facts.plan_revision)

    shared = {
        "workout_state": decision.state,
        "workout_action": workout_action,
        "workout_action_label_key": workout_label_key,
        "execution_bootstrap": facts.execution_bootstrap,
        "nutrition_state": facts.nutrition_state,
        "nutrition_target_state": facts.nutrition_target_state,
        "nutrition_target_calories": facts.nutrition_target_calories,
        "nutrition_intake_state": facts.nutrition_intake_state,
        "nutrition_consumed_calories": facts.nutrition_consumed_calories,
        "nutrition_meal_count": facts.nutrition_meal_count,
        "nutrition_plan_state": facts.nutrition_plan_state,
        "has_nutrition_plan": facts.has_nutrition_plan,
        "supplements_state": facts.supplements_state,
        "supplements_count": facts.supplements_count,
        "management_baseline": baseline,
        "session_conflict": session_conflict,
        "workout_session_stale_reason": facts.workout_session_stale_reason,
    }

    # Honest failure: a canonical read failed. Do NOT present a plan, do NOT
    # convert to no_active_plan. Offer no dominant CTA and no "Open Plan" (the user
    # is already on Plan); the template renders a safe retry (answer.txt §2).
    if not facts.read_ok:
        # No Training management is offered on top of an unknown plan: neither
        # "create" (there may be one) nor "replace" (we cannot say what it is).
        return PlanView(
            state=STATE_READ_ERROR,
            primary=None,
            secondary=_PLAN_SECONDARY,
            weekly_section_state=_weekly_state(STATE_READ_ERROR, weekly_enabled),
            management_state=MANAGE_UNAVAILABLE,
            **shared,
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
            management_state=MANAGE_CREATE,
            **shared,
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
            # A plan row that cannot be displayed is still a plan the user is
            # following, so replacing it stays the DESTRUCTIVE operation. Offering
            # "create" here would let an unreadable plan be silently overwritten
            # without the confirmation an active plan is entitled to.
            management_state=MANAGE_REGENERATE,
            **shared,
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
        management_state=MANAGE_REGENERATE,
        **shared,
    )
