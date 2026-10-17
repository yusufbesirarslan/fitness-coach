"""Bounded public failures for the native Training generation command."""
from app.services.training_generation.preference_contract import (
    CODE_GENERATION_EXERCISE_AMBIGUOUS,
    CODE_GENERATION_EXERCISE_IDENTITY_INVALID,
    CODE_GENERATION_EXERCISE_INCOMPATIBLE,
    CODE_GENERATION_EXERCISE_UNRESOLVED,
    CODE_GENERATION_SCHEMA_INVALID,
    CODE_GENERATION_SEMANTICALLY_INVALID,
)

# A provider answer that failed canonical validation (stored as 422).
INVALID_CANDIDATE_CODES = frozenset({
    CODE_GENERATION_SCHEMA_INVALID,
    CODE_GENERATION_SEMANTICALLY_INVALID,
    CODE_GENERATION_EXERCISE_UNRESOLVED,
    CODE_GENERATION_EXERCISE_AMBIGUOUS,
    CODE_GENERATION_EXERCISE_IDENTITY_INVALID,
    CODE_GENERATION_EXERCISE_INCOMPATIBLE,
})


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
    """A bounded terminal result reconstructed from the durable ledger.

    Never retryable: a FAILED operation consumes its key, so sending the same
    command with the same key can only replay this answer. Whether a NEW
    logical attempt (a new key) may succeed is said by `public_code`
    (`TRAINING_PLAN_GENERATION_UNAVAILABLE` = yes), never by this flag.
    """

    retryable = False

    def __init__(self, public_code, http_status):
        super().__init__("stored training plan generation failure")
        self.public_code = public_code
        self.http_status = http_status


class TrainingAllowanceRefused(PlanGenerationCommandError):
    """An AI allowance refusal before dispatch; no unit was held or used.

    Carries the closed ``ai_allowances`` catalogue code, HTTP status and
    retryability unchanged, never an internal ledger detail.
    """

    def __init__(self, public_code, http_status, retryable):
        super().__init__("training allowance refused")
        self.public_code = public_code
        self.http_status = http_status
        self.retryable = retryable
