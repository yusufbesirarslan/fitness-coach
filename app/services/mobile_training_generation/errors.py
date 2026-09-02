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


class GenerationInProgress(PlanGenerationCommandError):
    public_code = "TRAINING_PLAN_GENERATION_IN_PROGRESS"
    http_status = 409
    retryable = True


class ExistingPlanRefused(PlanGenerationCommandError):
    public_code = "TRAINING_PLAN_REPLACEMENT_REFUSED"
    http_status = 409
    retryable = False


class GenerationPrerequisiteMissing(PlanGenerationCommandError):
    public_code = "TRAINING_PLAN_PREREQUISITE_MISSING"
    http_status = 422
    retryable = False


class GenerationQuotaExceeded(PlanGenerationCommandError):
    public_code = "PREMIUM_REQUIRED"
    http_status = 402
    retryable = False


class GenerationPersistenceUnavailable(PlanGenerationCommandError):
    public_code = "TRAINING_PLAN_PERSISTENCE_UNAVAILABLE"
    http_status = 503
    retryable = True


class StoredGenerationFailure(PlanGenerationCommandError):
    """A bounded terminal result reconstructed from the durable ledger."""

    def __init__(self, public_code, http_status, retryable):
        super().__init__("stored training plan generation failure")
        self.public_code = public_code
        self.http_status = http_status
        self.retryable = retryable
