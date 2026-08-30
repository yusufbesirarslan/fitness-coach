"""Canonical daily macro-target authority (Sprint 13 PR2, decision C2).

One question, answered once: *given a user's configured daily calorie target and
their configured goal, how many grams of protein / carbohydrate / fat does the
day allow, and how much of that is left after what they have eaten?*

Before this module that question was answered four times — `ai_coach`,
`menu.analyze_menu`, `barcode._target_macros` and `fitx_mcp` — and the barcode
copy disagreed with the other three for every non-muscle-gain user (F2) while
also inventing a ``2000 kcal`` goal for users who had configured none (F3a).
The coach/menu formula is the surviving definition; see
``docs/superpowers/specs/2026-08-30-sprint13-pr1-nutrition-closure-discovery.md``.

**This module is pure.** No Flask, no ``request``/``g``/``current_user``, no
SQLAlchemy, no HTTP serializer, no LLM or provider call, nothing persisted. It
*consumes* a configured calorie target and a goal string; it never looks a user
up. Fetching the latest ``UserSession`` and today's ``MealLog`` totals stays with
the adapters that already own those queries — this layer only does arithmetic.

**Absence is not a number.** A user who has never completed the calculation that
sets ``UserSession.target_calories`` has *no* configured target, and
:func:`derive_daily_macro_targets` returns ``None`` for them. It does not return
``2000``, zeros, an estimate, or a synthetic target. That matches the mobile
boundary's existing rule (``mobile_nutrition.serialization.nutrition_goal``),
which is the reference behaviour for this decision. Callers decide how to
*present* absence — the coach omits the budget, the menu route answers its
existing ``profile_data_missing`` error, barcode publishes ``null`` — but no
caller substitutes a number.

What this module deliberately is **not**: a recommendation model, a goal
classifier, or a nutrition-intelligence domain. ``barcode._goal_key`` still
classifies a goal into ``bulk``/``cut``/``maintain`` for recommendation
*messaging*; that is a different question and stays where it is.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# The one stored goal value that shifts the split. `profile.py` permits exactly
# {"kilo verme", "kas kazanma", ""}; every value other than this one takes the
# default ratios, so an unset goal and a weight-loss goal agree — as they did in
# the coach and menu formulas this module replaces.
GOAL_MUSCLE_GAIN = "kas kazanma"

# The four canonical macro keys, in the order every consumer publishes them.
MACRO_KEYS = ("calories", "protein", "carbs", "fat")

# Atwater factors. Named rather than inlined so the guard in
# tests/test_nutrition_targets.py has one structural place to point at.
KCAL_PER_GRAM_PROTEIN = 4.0
KCAL_PER_GRAM_CARB = 4.0
KCAL_PER_GRAM_FAT = 9.0


@dataclass(frozen=True)
class MacroCalorieRatios:
    """How one day's calorie target is split across the three macros.

    Fractions of *calories*, not grams, and they sum to 1.0.
    """
    protein: float
    carbs: float
    fat: float


MUSCLE_GAIN_RATIOS = MacroCalorieRatios(protein=0.30, carbs=0.45, fat=0.25)
DEFAULT_RATIOS = MacroCalorieRatios(protein=0.25, carbs=0.50, fat=0.25)


@dataclass(frozen=True)
class DailyMacros:
    """A calorie figure and its three macro *gram* figures for one day.

    Used for both the derived daily target and the remaining budget, because
    they are the same shape and the same units; which one an instance holds is
    the caller's context, not a different type.

    Values are unrounded. Rounding is presentation and belongs to the surface
    that publishes them — barcode publishes one decimal, menu publishes whole
    numbers, and neither precision is a domain fact.
    """
    calories: float
    protein: float
    carbs: float
    fat: float

    def as_dict(self) -> dict[str, float]:
        """The canonical mapping shape every server consumer publishes."""
        return {
            "calories": self.calories,
            "protein": self.protein,
            "carbs": self.carbs,
            "fat": self.fat,
        }


def macro_calorie_ratios(goal) -> MacroCalorieRatios:
    """The canonical calorie split for a stored goal value.

    Exposed because ``analytics_engine`` asks a genuinely different question —
    a *weekly* protein goal — from the same ratio (C2). It consumes the ratio;
    it does not derive a daily target. Deriving one from this is
    :func:`derive_daily_macro_targets`'s job and nobody else's.
    """
    return MUSCLE_GAIN_RATIOS if (goal or "") == GOAL_MUSCLE_GAIN else DEFAULT_RATIOS


def derive_daily_macro_targets(target_calories, goal) -> DailyMacros | None:
    """The configured daily calorie target's macro split, or ``None``.

    ``None`` means *no configured target*, and it is returned whenever
    ``target_calories`` is missing, non-numeric, or not a positive number of
    kilocalories. Zero is not a target anyone configured and neither is a
    negative one, so neither is dressed up as one — the same normalisation the
    mobile goal boundary already applies.
    """
    try:
        calories = float(target_calories)
    except (TypeError, ValueError):
        return None
    if calories <= 0:
        return None

    ratios = macro_calorie_ratios(goal)
    return DailyMacros(
        calories=calories,
        protein=calories * ratios.protein / KCAL_PER_GRAM_PROTEIN,
        carbs=calories * ratios.carbs / KCAL_PER_GRAM_CARB,
        fat=calories * ratios.fat / KCAL_PER_GRAM_FAT,
    )


def remaining_macro_budget(
        targets: DailyMacros | None,
        consumed: Mapping[str, float] | None) -> DailyMacros | None:
    """What is left of ``targets`` after ``consumed``, floored at zero.

    ``None`` in, ``None`` out: there is no remaining budget without a configured
    target, and inventing one would re-create exactly the fabrication F3a is
    about. ``consumed`` is a mapping keyed by :data:`MACRO_KEYS` — canonically
    today's ``MealLog`` totals, produced by whichever adapter already owns that
    query. This function does not re-derive the split; it only subtracts.
    """
    if targets is None:
        return None
    eaten = consumed or {}
    return DailyMacros(**{
        key: max(getattr(targets, key) - _to_number(eaten.get(key)), 0.0)
        for key in MACRO_KEYS
    })


def _to_number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
