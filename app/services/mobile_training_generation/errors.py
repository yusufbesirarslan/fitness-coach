"""Bounded public failures for the native Training generation command."""


class PlanGenerationCommandError(RuntimeError):
    """A command failure safe to translate into the mobile error envelope."""

    public_code = "TRAINING_PLAN_GENERATION_COMMAND_FAILED"
    http_status = 500
    retryable = False

    def __init__(self, reason: str = "training plan generation command failed"):
        super().__init__(reason)


class InvalidPlanRequest(PlanGenerationCommandError):
    public_code = "TRAINING_PLAN_INVALID_REQUEST"
    http_status = 422
    retryable = False


class InvalidIdempotencyKey(PlanGenerationCommandError):
    public_code = "TRAINING_PLAN_INVALID_IDEMPOTENCY_KEY"
    http_status = 400
    retryable = False


class IdempotencyConflict(PlanGenerationCommandError):
    public_code = "TRAINING_PLAN_IDEMPOTENCY_CONFLICT"
    http_status = 409
    retryable = False
