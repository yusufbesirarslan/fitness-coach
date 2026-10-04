"""NUTR-PR6 — the bounded, server-owned Nutrition day view.

A READ PROJECTION over four existing canonical authorities. It is not a
ledger, a cache, a table or a new source of truth: nothing here writes,
nothing here is persisted, and nothing here calls a provider or a model.

    target     latest ``UserSession.target_calories`` through the one target
               authority, ``nutrition_targets.derive_daily_macro_targets``
               (the same projection ``/meal-log/today`` publishes)
    intake     today's ``MealLog`` rows (the consumption ledger), aggregated
    hydration  today's ``WaterLog`` row (the same row ``GET /water`` reads)
    plan       the newest ``NutritionPlan`` (the row ``/nutrition-plan/active``
               reads); a generated proposal is never a row, so it can never
               appear here

"Today" is computed ONCE per build with ``app_today()`` — the server's
Europe/Istanbul day that ``MealLog.tarih`` (``day_key()``) and
``WaterLog.date_key`` are both written with — and the same value is used for
the intake and the hydration read, so the two can never straddle midnight.

Every section is read in its own savepoint and fails on its own. Each carries
one state from :data:`SECTION_STATES`:

    available    a successful read with something recorded
    empty        a successful read that proves "nothing": no configured target,
                 zero meals (measured zero totals), zero water (measured 0), no
                 saved plan
    unavailable  the read failed — the value is UNKNOWN, never 0 and never empty
    invalid      the read succeeded but the persisted value cannot be
                 represented truthfully (non-finite or negative numbers, a plan
                 document that cannot be parsed or has no meal)

``loading`` is part of the shared vocabulary but belongs to clients only: the
server always answers with a settled state. There is deliberately NO aggregate
"partial" field: a consumer sees which sections failed from the sections
themselves, and no presentation needs a summary state.

``next_action`` is derived by :func:`derive_next_action`, a pure decision
table over the intake and target states ONLY (see its docstring). It names
one of three safe kinds — never an advisory one. There is no adherence,
score, "on track", "behind" or gap concept anywhere in this module, and no
user-facing sentence: consumers localise ``label_key``.

Transport-neutral: no Flask request/response, no ``current_user``. The web
session route (``GET /nutrition-day-view``) and the Coach handoff are thin
callers; a future native route wraps :func:`build_nutrition_day_view` +
:func:`nutrition_day_view_payload` without duplicating a rule.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func

from app.extensions import db
from app.models import MealLog, NutritionPlan, UserSession, WaterLog
from app.services.nutrition_plan_schema import MEAL_KEYS, NAME_KEY, NAME_MAX
from app.services.nutrition_targets import derive_daily_macro_targets
from app.timeutil import app_today

CONTRACT_VERSION = 1

AVAILABLE = "available"
EMPTY = "empty"
UNAVAILABLE = "unavailable"
INVALID = "invalid"
LOADING = "loading"  # client-side only; the server never answers it
SECTION_STATES = (LOADING, AVAILABLE, EMPTY, UNAVAILABLE, INVALID)
SERVER_SECTION_STATES = (AVAILABLE, EMPTY, UNAVAILABLE, INVALID)
READABLE = (AVAILABLE, EMPTY)

TARGET_UNIT = "kcal"
HYDRATION_UNIT = "glass"
INTAKE_KEYS = ("calories", "protein", "carbs", "fat")

# next_action — the ONLY kinds PR6 may emit. Each maps to an existing canonical
# user action (docs/NUTRITION_VNEXT_PR6.md §7). Advisory kinds (eat_more,
# drink_more, hit_protein, follow_plan, ...) need a separately reviewed rule.
LOG_FOOD = "log_food"
SET_TARGET = "set_target"
RETRY = "retry"
NEXT_ACTION_KINDS = (LOG_FOOD, SET_TARGET, RETRY)
NEXT_ACTION_LABEL_KEYS = {
    LOG_FOOD: "nutrition.next.log_food",
    SET_TARGET: "nutrition.next.set_target",
    RETRY: "nutrition.next.retry",
}
NO_ACTION_LABEL_KEY = "nutrition.next.none"


@dataclass(frozen=True)
class TargetSection:
    state: str
    value: float | None = None
    unit: str = TARGET_UNIT


@dataclass(frozen=True)
class IntakeSection:
    state: str
    totals: dict | None = None
    meal_count: int | None = None


@dataclass(frozen=True)
class HydrationSection:
    state: str
    amount: int | None = None
    unit: str = HYDRATION_UNIT


@dataclass(frozen=True)
class PlanSummary:
    name: str | None
    planned_meal_count: int


@dataclass(frozen=True)
class PlanSection:
    state: str
    summary: PlanSummary | None = None


@dataclass(frozen=True)
class NextAction:
    state: str            # "available" (one safe action) | "empty" (none)
    kind: str | None
    label_key: str


@dataclass(frozen=True)
class NutritionDayView:
    day: date
    target: TargetSection
    intake: IntakeSection
    hydration: HydrationSection
    plan: PlanSection
    next_action: NextAction


# ── pure decision table ─────────────────────────────────────────────────


def derive_next_action(intake_state, target_state) -> NextAction:
    """The one deterministic next step, from intake and target states ONLY.

    | intake              | target                        | next_action        |
    | ------------------- | ----------------------------- | ------------------ |
    | unavailable         | (any)                         | retry              |
    | invalid             | (any)                         | none               |
    | available / empty   | empty (genuinely not set)     | set_target         |
    | available / empty   | available/unavailable/invalid | log_food           |
    | anything else       | anything else                 | none               |

    * A failed ledger read is the only failure a retry can repair, so it is the
      only one that asks for one. Invalid persisted meals are not repaired by
      re-reading them; no action is safer than a futile or invented one.
    * ``set_target`` only when a SUCCESSFUL read proved there is no configured
      target. A target that failed to read, or is stored unusably, is not
      "missing" — logging food is still correct and nothing is guessed.
    * Hydration and plan are deliberately NOT inputs: neither can produce a
      stronger action, and their failure must not invent one.
    """
    if intake_state == UNAVAILABLE:
        return _action(RETRY)
    if intake_state in READABLE:
        if target_state == EMPTY:
            return _action(SET_TARGET)
        if target_state in (AVAILABLE, UNAVAILABLE, INVALID):
            return _action(LOG_FOOD)
    return NextAction(state=EMPTY, kind=None, label_key=NO_ACTION_LABEL_KEY)


def _action(kind):
    return NextAction(state=AVAILABLE, kind=kind, label_key=NEXT_ACTION_LABEL_KEYS[kind])


# ── section reads (each bounded, each fails alone) ──────────────────────


def _finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _read_target(user_id) -> TargetSection:
    with db.session.begin_nested():
        row = (UserSession.query.filter_by(user_id=user_id)
               .with_entities(UserSession.target_calories, UserSession.goal)
               .order_by(UserSession.created_at.desc(), UserSession.id.desc()).first())
    if row is None or row[0] is None:
        return TargetSection(EMPTY)
    raw = row[0]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return TargetSection(INVALID)
    if not math.isfinite(raw):
        return TargetSection(INVALID)
    macros = derive_daily_macro_targets(raw, row[1])
    if macros is None:
        # Zero / negative is "no configured target" under the existing
        # contract (/meal-log/today publishes `targets: null` for it).
        return TargetSection(EMPTY)
    return TargetSection(AVAILABLE, value=float(macros.calories))


def _read_intake(user_id, day_iso) -> IntakeSection:
    with db.session.begin_nested():
        row = db.session.query(
            func.count(MealLog.id),
            func.coalesce(func.sum(MealLog.kalori), 0),
            func.coalesce(func.sum(MealLog.protein), 0),
            func.coalesce(func.sum(MealLog.karb), 0),
            func.coalesce(func.sum(MealLog.yag), 0),
        ).filter(MealLog.user_id == user_id, MealLog.tarih == day_iso).one()
    count = row[0]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return IntakeSection(INVALID)
    totals = {}
    for key, raw in zip(INTAKE_KEYS, row[1:]):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return IntakeSection(INVALID)
        if not math.isfinite(value) or value < 0:
            return IntakeSection(INVALID)
        totals[key] = value
    return IntakeSection(AVAILABLE if count else EMPTY, totals=totals, meal_count=count)


def _read_hydration(user_id, day_iso) -> HydrationSection:
    with db.session.begin_nested():
        row = (WaterLog.query.filter_by(user_id=user_id, date_key=day_iso)
               .with_entities(WaterLog.count).first())
    if row is None:
        return HydrationSection(EMPTY, amount=0)
    count = row[0]
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return HydrationSection(INVALID)
    return HydrationSection(AVAILABLE if count else EMPTY, amount=count)


def _read_plan(user_id) -> PlanSection:
    with db.session.begin_nested():
        row = (NutritionPlan.query.filter_by(user_id=user_id)
               .with_entities(NutritionPlan.plan_data)
               .order_by(NutritionPlan.created_at.desc(), NutritionPlan.id.desc()).first())
    if row is None:
        return PlanSection(EMPTY)
    try:
        document = json.loads(row[0])
    except (TypeError, ValueError):
        return PlanSection(INVALID)
    if not isinstance(document, dict):
        return PlanSection(INVALID)
    # Same "drawable" rule as the Plan tab: at least one meal object.
    meals = sum(1 for key in MEAL_KEYS if isinstance(document.get(key), dict))
    if meals == 0:
        return PlanSection(INVALID)
    name = document.get(NAME_KEY)
    name = name.strip()[:NAME_MAX] if isinstance(name, str) and name.strip() else None
    return PlanSection(AVAILABLE, PlanSummary(name=name, planned_meal_count=meals))


def _guard(read, failed):
    try:
        return read()
    except Exception:
        return failed


def build_nutrition_day_view(user_id) -> NutritionDayView:
    """Owner-scoped day view. Never raises for a section read; never writes."""
    today = app_today()
    day_iso = today.isoformat()
    target = _guard(lambda: _read_target(user_id), TargetSection(UNAVAILABLE))
    intake = _guard(lambda: _read_intake(user_id, day_iso), IntakeSection(UNAVAILABLE))
    hydration = _guard(lambda: _read_hydration(user_id, day_iso),
                       HydrationSection(UNAVAILABLE))
    plan = _guard(lambda: _read_plan(user_id), PlanSection(UNAVAILABLE))
    return NutritionDayView(
        day=today, target=target, intake=intake, hydration=hydration, plan=plan,
        next_action=derive_next_action(intake.state, target.state))


def nutrition_day_view_payload(view: NutritionDayView) -> dict:
    """The stable wire shape (hand-written; no DOM concepts, no sentences)."""
    plan_summary = None
    if view.plan.summary is not None:
        plan_summary = {"name": view.plan.summary.name,
                        "planned_meal_count": view.plan.summary.planned_meal_count}
    return {
        "contract_version": CONTRACT_VERSION,
        "day": view.day.isoformat(),
        "target": {"state": view.target.state, "value": view.target.value,
                   "unit": view.target.unit},
        "intake": {"state": view.intake.state,
                   "totals": dict(view.intake.totals) if view.intake.totals is not None else None,
                   "meal_count": view.intake.meal_count},
        "hydration": {"state": view.hydration.state, "amount": view.hydration.amount,
                      "unit": view.hydration.unit},
        "plan": {"state": view.plan.state, "summary": plan_summary},
        "next_action": {"state": view.next_action.state, "kind": view.next_action.kind,
                        "label_key": view.next_action.label_key},
    }
