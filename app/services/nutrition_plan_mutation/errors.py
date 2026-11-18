"""Closed refusal vocabulary of the Nutrition Plan edit boundary.

Every refusal happens before anything is written. ``code`` is a stable,
locale-free token for a future tool/API layer; messages are internal.
"""


class NutritionPlanMutationError(Exception):
    code = "NUTRITION_PLAN_EDIT_FAILED"


class InvalidNutritionEdit(NutritionPlanMutationError):
    code = "INVALID_EDIT"


class NutritionPlanNotFound(NutritionPlanMutationError):
    code = "NO_NUTRITION_PLAN"


class NutritionPlanNotEditable(NutritionPlanMutationError):
    """The stored plan is not a document this boundary can edit safely."""
    code = "PLAN_NOT_EDITABLE"


class MealNotFound(NutritionPlanMutationError):
    code = "MEAL_NOT_FOUND"


class MealItemNotFound(NutritionPlanMutationError):
    code = "ITEM_NOT_FOUND"


class AmbiguousMealItem(NutritionPlanMutationError):
    """More than one item matches; never resolved by position."""
    code = "ITEM_AMBIGUOUS"


class MealItemAlreadyPresent(NutritionPlanMutationError):
    code = "ITEM_ALREADY_PRESENT"


class EstimateUnavailable(NutritionPlanMutationError):
    """The estimator failed, or returned values that cannot be trusted.

    F-3: a failed or ambiguous estimate writes nothing.
    """
    code = "ESTIMATE_UNAVAILABLE"


class NutritionPlanStateConflict(NutritionPlanMutationError):
    """The plan moved since the change was prepared; nothing was written."""
    code = "PLAN_CHANGED"


class NutritionIdempotencyConflict(NutritionPlanMutationError):
    code = "IDEMPOTENCY_CONFLICT"


class NutritionUndoUnavailable(NutritionPlanMutationError):
    code = "NOTHING_TO_UNDO"


class NutritionUndoConflict(NutritionPlanMutationError):
    code = "UNDO_CONFLICT"
