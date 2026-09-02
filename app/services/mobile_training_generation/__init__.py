"""Public boundary for native Training plan generation."""
from .contract import NativePlanRequest, parse_idempotency_key, parse_native_request
from .errors import (
    ExistingPlanRefused,
    GenerationInProgress,
    GenerationPersistenceUnavailable,
    GenerationPrerequisiteMissing,
    GenerationQuotaExceeded,
    IdempotencyConflict,
    InvalidIdempotencyKey,
    InvalidPlanRequest,
    PlanGenerationCommandError,
    StoredGenerationFailure,
)
from .service import GenerationCommandResult, generate_and_persist

__all__ = [
    "ExistingPlanRefused",
    "GenerationCommandResult",
    "GenerationInProgress",
    "GenerationPersistenceUnavailable",
    "GenerationPrerequisiteMissing",
    "GenerationQuotaExceeded",
    "IdempotencyConflict",
    "InvalidIdempotencyKey",
    "InvalidPlanRequest",
    "NativePlanRequest",
    "PlanGenerationCommandError",
    "StoredGenerationFailure",
    "generate_and_persist",
    "parse_idempotency_key",
    "parse_native_request",
]
