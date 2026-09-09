"""AxisAI UIUX Sprint 1 PR3 — Plan canonical-facts read layer.

The read-only source of truth Plan V2 renders from. It shares the *exact* active-
plan selector the ``/training-plan/active`` endpoint uses (``get_active_plan`` in
``app.services.today_facts``) so the page and that endpoint can never disagree
about which plan is active, then parses the plan's ``plan_data`` JSON into a
presentation-only, canonically-ordered day→exercise structure.

Read-only: no writes, no AI, no HTTP. This module owns the presentation-facing
*composition and safe parse*; the presenter (``app/plan_presenter.py``) owns the
presentation and stays pure.

Fallback semantics are deliberately strict (answer.txt §8):
  * a DB/read failure yields ``read_ok=False`` — an honest error state, never a
    fabricated populated one and never "no plan";
  * an active plan row whose ``plan_data`` cannot be parsed into a usable ordered
    day list yields ``parse_ok=False`` (→ ``partial_active_plan``) — malformed data
    is NOT "no plan";
  * canonical day order is preserved exactly; days are never reordered;
  * an unknown day label is carried through verbatim (stays neutral);
  * an empty exercise list is NOT turned into a rest day — only the plan's own
    explicit ``tip == "dinlenme"`` marks a rest day.

None of legacy ``static/training.js``'s client-side authority lives here: no
clock-based "today" selection, no rest-day inference, no completion-from-storage.
"""
from __future__ import annotations

import json

from app.extensions import db
from app.models import Supplement, UserSession
from app.plan_presenter import PlanDay, PlanExercise, PlanFacts, REST_DAY_KIND
from app.services.today_facts import get_active_plan
from app.services.workout_state import resolve_workout_state
from app.services.workout_state.serialization import (
    serialize_today_plan,
    workout_state_payload,
)
from app.timeutil import app_today, display_dt


def _text(value) -> str:
    """A neutral display string for an opaque canonical value (never ``None``)."""
    return "" if value is None else str(value)


def _int(value) -> int:
    """A non-negative-ish integer projection of a numeric canonical field.

    Best-effort only: a missing/garbage value becomes ``0`` (a neutral display),
    never an exception and never an inferred quantity.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_exercise(raw) -> PlanExercise:
    """Project one raw exercise mapping into a neutral ``PlanExercise``.

    Field names mirror the canonical plan contract consumed by legacy
    ``training.js`` (``isim``/``set``/``tekrar``/``dinlenme``/``not``). A non-mapping
    entry degrades to an empty exercise rather than raising.
    """
    if not isinstance(raw, dict):
        return PlanExercise()
    return PlanExercise(
        name=_text(raw.get("isim")),
        sets=_text(raw.get("set")),
        reps=_text(raw.get("tekrar")),
        rest=_text(raw.get("dinlenme")),
        note=_text(raw.get("not")),
    )


def _parse_day(raw) -> "PlanDay | None":
    """Project one raw day mapping into a neutral ``PlanDay`` in canonical order.

    Returns ``None`` for a non-mapping entry so the caller can skip it without
    downgrading the whole plan. ``is_rest`` is set ONLY from the explicit canonical
    rest marker (``tip == "dinlenme"``) — never inferred from an empty exercise
    list (answer.txt §8).
    """
    if not isinstance(raw, dict):
        return None
    kind = _text(raw.get("tip"))
    raw_exercises = raw.get("egzersizler")
    exercises = tuple(
        _parse_exercise(ex) for ex in raw_exercises
    ) if isinstance(raw_exercises, list) else ()
    return PlanDay(
        label=_text(raw.get("gun")),
        kind=kind,
        is_rest=(kind == REST_DAY_KIND),
        focus=_text(raw.get("odak")),
        duration_min=_int(raw.get("sure_dk")),
        est_calories=_int(raw.get("tahmini_kalori")),
        exercises=exercises,
    )


def _parse_plan_days(plan_data):
    """Safely parse a plan row's ``plan_data`` JSON text into ordered days.

    Returns ``(parse_ok, days)``. Accepted JSON shapes are a bare list or an
    object whose ``program`` field is a list. ``parse_ok`` is False for invalid
    JSON, any other shape, or zero usable day entries — every one of which is an
    honest ``partial_active_plan`` signal, not "no plan". Canonical order is
    preserved; malformed individual entries are skipped, not reordered.
    """
    try:
        parsed = json.loads(plan_data)
    except (TypeError, ValueError):
        return False, ()
    if isinstance(parsed, dict):
        parsed = parsed.get("program")
    if not isinstance(parsed, list):
        return False, ()
    days = tuple(day for day in (_parse_day(raw) for raw in parsed) if day is not None)
    if not days:
        return False, ()
    return True, days


def _plan_revision(plan):
    """The canonical plan-freshness identity of an already-loaded plan row.

    UX-3 PR3. Both fields are read straight off the row the caller already
    holds — no extra query, no derivation, no client-side version. The pair is
    the SAME identity the native contract publishes as ``plan_lineage`` /
    ``mutation_version``, named identically so the two surfaces cannot drift.
    ``lineage_id`` changes only when the plan is REPLACED (the save path deletes
    and re-inserts); ``mutation_version`` counts up on every targeted mutation,
    including a Coach one. Either moving means "this is no longer the plan the
    page was rendered against".
    """
    if plan is None:
        return None
    lineage = getattr(plan, "lineage_id", None)
    version = getattr(plan, "mutation_version", None)
    if not isinstance(lineage, str) or not lineage or not isinstance(version, int):
        # An unidentifiable plan must not present a comparable revision: a
        # freshness check against ``None`` would silently succeed forever.
        return None
    return {"plan_lineage": lineage, "mutation_version": version}


def _session_facts(snapshot):
    """Bounded truthful projection of the persisted session (contract v2 only).

    Returns ``(session_state, stale_reason)``. Never the ``public_id``, never a
    raw row: the Plan placement only needs to know that an ACTIVE session exists
    and why the server considers it stale, so it can tell the truth before and
    after a plan replacement. With sessions OFF the snapshot carries no session
    at all and both values are empty.
    """
    if snapshot is None:
        return "", ""
    state = getattr(snapshot, "session_state", None)
    session = getattr(snapshot, "session", None)
    reason = session.get("stale_reason") if isinstance(session, dict) else None
    return (state if isinstance(state, str) else "",
            reason if isinstance(reason, str) else "")


def _child_domain_facts(user_id):
    nutrition_state = "unknown"
    nutrition_target = None
    supplements_state = "unknown"
    supplements_count = None
    try:
        with db.session.begin_nested():
            session = (UserSession.query.filter_by(user_id=user_id)
                       .order_by(UserSession.created_at.desc()).first())
        target = getattr(session, "target_calories", None) if session else None
        if isinstance(target, (int, float)) and target > 0:
            nutrition_state = "available"
            nutrition_target = int(round(target))
        else:
            nutrition_state = "empty"
    except Exception:
        nutrition_state = "unavailable"

    try:
        with db.session.begin_nested():
            supplements_count = Supplement.query.filter_by(user_id=user_id).count()
        supplements_state = "available" if supplements_count else "empty"
    except Exception:
        supplements_state = "unavailable"
        supplements_count = None
    return {
        "nutrition_state": nutrition_state,
        "nutrition_target_calories": nutrition_target,
        "supplements_state": supplements_state,
        "supplements_count": supplements_count,
    }


def gather_plan_facts(user_id, *, sessions_enabled=False) -> PlanFacts:
    """Gather the canonical facts Plan V2 needs, tolerating read failure.

    A DB/read error yields ``read_ok=False`` so the presenter surfaces an honest
    error state instead of a fabricated populated one. When the row exists but its
    ``plan_data`` cannot be parsed, ``parse_ok=False`` drives the honest
    ``partial_active_plan`` state — malformed data is never treated as "no plan".
    """
    children = _child_domain_facts(user_id)
    try:
        plan = get_active_plan(user_id)
    except Exception:
        return PlanFacts(
            read_ok=False, has_active_plan=False, parse_ok=False, **children)

    today = app_today()
    workout_snapshot = None
    execution_bootstrap = None
    workout_read_ok = False
    try:
        workout_snapshot = resolve_workout_state(
            user_id, today=today, plan=plan,
            sessions_enabled=sessions_enabled, strict_reads=True,
        )
        plan_data = json.loads(plan.plan_data) if plan is not None else None
        execution_bootstrap = {
            "workout": workout_state_payload(workout_snapshot),
            "today_plan": serialize_today_plan(plan_data, today),
        }
        workout_read_ok = True
    except Exception:
        workout_snapshot = None
        execution_bootstrap = None

    session_state, session_stale_reason = _session_facts(workout_snapshot)

    if plan is None:
        return PlanFacts(
            read_ok=True, has_active_plan=False, parse_ok=False,
            workout_read_ok=workout_read_ok,
            workout_snapshot=workout_snapshot,
            execution_bootstrap=execution_bootstrap,
            plan_revision=None,
            workout_session_state=session_state,
            workout_session_stale_reason=session_stale_reason,
            **children,
        )

    parse_ok, days = _parse_plan_days(plan.plan_data)
    created_at = None
    try:
        created_at = display_dt(plan.created_at, "%d.%m.%Y")
    except Exception:
        created_at = None
    return PlanFacts(
        read_ok=True,
        has_active_plan=True,
        parse_ok=parse_ok,
        days=days,
        score=getattr(plan, "score", None),
        created_at=created_at,
        workout_read_ok=workout_read_ok,
        workout_snapshot=workout_snapshot,
        execution_bootstrap=execution_bootstrap,
        plan_revision=_plan_revision(plan),
        workout_session_state=session_state,
        workout_session_stale_reason=session_stale_reason,
        **children,
    )
