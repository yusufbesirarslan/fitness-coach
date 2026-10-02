"""The closed NUTR-PR7 native error vocabulary.

Every refusal is a class carrying its own (code, HTTP status, retryable) triple,
so the transport renders a class — never a message — and no exception text,
SQL or provider body can reach a client. ``retryable`` means "the SAME request
may succeed later"; a stale precondition is ``False`` because the client must
re-read first, and a semantic conflict is ``False`` because it never will.
"""


class NativeNutritionError(Exception):
    code = "NUTRITION_TEMPORARILY_UNAVAILABLE"
    status = 503
    retryable = True
    message = "Nutrition data is temporarily unavailable."


def _error(name, code, status, retryable, message):
    return type(name, (NativeNutritionError,), {
        "code": code, "status": status, "retryable": retryable,
        "message": message})


# ── preconditions (shared) ──────────────────────────────────────────────────
PreconditionRequired = _error(
    "PreconditionRequired", "NUTRITION_PRECONDITION_REQUIRED", 428, False,
    "A precondition header is required.")
InvalidPrecondition = _error(
    "InvalidPrecondition", "INVALID_NUTRITION_PRECONDITION", 400, False,
    "The precondition header is invalid.")
IdempotencyKeyRequired = _error(
    "IdempotencyKeyRequired", "INVALID_IDEMPOTENCY_KEY", 400, False,
    "A valid Idempotency-Key is required.")
IdempotencyConflict = _error(
    "IdempotencyConflict", "IDEMPOTENCY_CONFLICT", 409, False,
    "The Idempotency-Key belongs to a different command.")

# ── day view ───────────────────────────────────────────────────────────────
DayViewUnavailable = _error(
    "DayViewUnavailable", "NUTRITION_DAY_VIEW_UNAVAILABLE", 503, True,
    "The nutrition day view is temporarily unavailable.")

# ── nutrition plan ─────────────────────────────────────────────────────────
PlanUnavailable = _error(
    "PlanUnavailable", "NUTRITION_PLAN_UNAVAILABLE", 503, True,
    "The nutrition plan is temporarily unavailable.")
StalePlan = _error(
    "StalePlan", "STALE_NUTRITION_PLAN", 412, False,
    "The nutrition plan has changed. Read it again.")
InvalidPlan = _error(
    "InvalidPlan", "INVALID_NUTRITION_PLAN", 400, False,
    "The nutrition plan is invalid.")
InvalidProposal = _error(
    "InvalidProposal", "INVALID_PLAN_PROPOSAL", 400, False,
    "The plan proposal is not valid for this plan.")
ProposalExpired = _error(
    "ProposalExpired", "PLAN_PROPOSAL_EXPIRED", 400, False,
    "The plan proposal has expired. Generate a new one.")
InvalidGenerationRequest = _error(
    "InvalidGenerationRequest", "INVALID_PLAN_GENERATION_REQUEST", 400, False,
    "Invalid plan generation request.")
TargetRequired = _error(
    "TargetRequired", "NUTRITION_TARGET_REQUIRED", 409, False,
    "A daily calorie target is required before generating a plan.")
GenerationRateLimited = _error(
    "GenerationRateLimited", "NUTRITION_PLAN_RATE_LIMITED", 429, True,
    "Too many plan generation requests.")
GenerationQuotaExceeded = _error(
    "GenerationQuotaExceeded", "NUTRITION_PLAN_QUOTA_EXCEEDED", 402, False,
    "The weekly plan generation allowance is used up.")
GenerationFailed = _error(
    "GenerationFailed", "NUTRITION_PLAN_GENERATION_FAILED", 503, True,
    "Plan generation is temporarily unavailable.")

# ── planned meal ───────────────────────────────────────────────────────────
PlannedMealNotFound = _error(
    "PlannedMealNotFound", "PLANNED_MEAL_NOT_FOUND", 404, False,
    "Planned meal was not found.")
PlannedMealNotLoggable = _error(
    "PlannedMealNotLoggable", "PLANNED_MEAL_NOT_LOGGABLE", 422, False,
    "This planned meal cannot be logged.")
InvalidPlannedMealCommand = _error(
    "InvalidPlannedMealCommand", "INVALID_PLANNED_MEAL_COMMAND", 400, False,
    "Invalid planned meal command.")

# ── hydration ──────────────────────────────────────────────────────────────
HydrationUnavailable = _error(
    "HydrationUnavailable", "HYDRATION_UNAVAILABLE", 503, True,
    "Hydration is temporarily unavailable.")
StaleHydration = _error(
    "StaleHydration", "STALE_HYDRATION", 412, False,
    "Hydration has changed. Read it again.")
InvalidHydration = _error(
    "InvalidHydration", "INVALID_HYDRATION_COMMAND", 400, False,
    "Invalid hydration command.")

# ── history ────────────────────────────────────────────────────────────────
HistoryUnavailable = _error(
    "HistoryUnavailable", "NUTRITION_HISTORY_UNAVAILABLE", 503, True,
    "Nutrition history is temporarily unavailable.")
InvalidHistoryCursor = _error(
    "InvalidHistoryCursor", "INVALID_HISTORY_CURSOR", 400, False,
    "The history cursor is invalid.")
InvalidHistoryLimit = _error(
    "InvalidHistoryLimit", "INVALID_HISTORY_LIMIT", 400, False,
    "The history page size is invalid.")

# ── supplements ────────────────────────────────────────────────────────────
SupplementsUnavailable = _error(
    "SupplementsUnavailable", "SUPPLEMENTS_UNAVAILABLE", 503, True,
    "Supplements are temporarily unavailable.")
SupplementNotFound = _error(
    "SupplementNotFound", "SUPPLEMENT_NOT_FOUND", 404, False,
    "Supplement was not found.")
StaleSupplement = _error(
    "StaleSupplement", "STALE_SUPPLEMENT", 412, False,
    "The supplement has changed. Read it again.")
StaleCabinet = _error(
    "StaleCabinet", "STALE_SUPPLEMENT_CABINET", 412, False,
    "The supplement cabinet has changed. Read it again.")
InvalidSupplement = _error(
    "InvalidSupplement", "INVALID_SUPPLEMENT_COMMAND", 400, False,
    "Invalid supplement command.")
CabinetFull = _error(
    "CabinetFull", "SUPPLEMENT_CABINET_FULL", 409, False,
    "The supplement cabinet is full.")


def all_error_classes():
    return [value for value in globals().values()
            if isinstance(value, type) and issubclass(value, NativeNutritionError)]
