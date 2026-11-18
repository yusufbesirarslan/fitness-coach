"""Targeted Nutrition Plan edits — Adaptive Coach C1 (no runtime surface).

The one server-authoritative place a saved Nutrition Plan may be changed in a
targeted way: typed meal-item edits, whole-meal server re-estimation (F-3),
daily totals recomputed from canonical meals, a monotonic version, a durable
journal with exact before/after bytes and provenance, keyed replay and a
byte-exact undo. No route, no AI tool and no flag ship with it; C2 adds the
Coach tools and the confirmation flow over this API. Whole-plan replacement
stays ``nutrition_plan_store.replace_nutrition_plan``.
"""
from .commands import (
    NUTRITION_EDIT_COMMANDS,
    AddMealItemCommand,
    RemoveMealItemCommand,
    ReplaceMealItemCommand,
)
from .errors import (
    AmbiguousMealItem,
    EstimateUnavailable,
    InvalidNutritionEdit,
    MealItemAlreadyPresent,
    MealItemNotFound,
    MealNotFound,
    NutritionIdempotencyConflict,
    NutritionPlanMutationError,
    NutritionPlanNotEditable,
    NutritionPlanNotFound,
    NutritionPlanStateConflict,
    NutritionUndoConflict,
    NutritionUndoUnavailable,
)
from .estimate import ESTIMATOR_ID, server_estimator
from .service import (
    OUTCOME_APPLIED,
    OUTCOME_NO_OP,
    NutritionEditResult,
    PreparedMealEdit,
    apply_prepared_edit,
    prepare_meal_edit,
    undo_last_nutrition_edit,
)

__all__ = [
    "ESTIMATOR_ID", "NUTRITION_EDIT_COMMANDS", "OUTCOME_APPLIED", "OUTCOME_NO_OP",
    "AddMealItemCommand", "AmbiguousMealItem", "EstimateUnavailable",
    "InvalidNutritionEdit", "MealItemAlreadyPresent", "MealItemNotFound",
    "MealNotFound", "NutritionEditResult", "NutritionIdempotencyConflict",
    "NutritionPlanMutationError", "NutritionPlanNotEditable",
    "NutritionPlanNotFound", "NutritionPlanStateConflict", "NutritionUndoConflict",
    "NutritionUndoUnavailable", "PreparedMealEdit", "RemoveMealItemCommand",
    "ReplaceMealItemCommand", "apply_prepared_edit", "prepare_meal_edit",
    "server_estimator", "undo_last_nutrition_edit",
]
