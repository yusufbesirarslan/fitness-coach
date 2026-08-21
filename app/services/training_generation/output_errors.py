"""Typed training-generation output failures.

Preference-contract errors (PR2) are not generation-output errors: they happen
before any provider call. These types are raised only after a supported request
has been sent to a provider, or when a client tries to persist a plan.
"""
from __future__ import annotations

from app.services.training_generation.preference_contract import (
    CODE_GENERATION_PARSE_FAILED,
    CODE_GENERATION_SCHEMA_INVALID,
    CODE_GENERATION_SEMANTICALLY_INVALID,
    CODE_GENERATION_TRUNCATED,
    CODE_GENERATION_UNAVAILABLE,
    CODE_SAVE_INVALID,
    I18N_GENERATION_PARSE_FAILED,
    I18N_GENERATION_SCHEMA_INVALID,
    I18N_GENERATION_SEMANTICALLY_INVALID,
    I18N_GENERATION_TRUNCATED,
    I18N_GENERATION_UNAVAILABLE,
    I18N_SAVE_INVALID,
)


class GenerationOutputError(ValueError):
    """Untrusted provider/client plan data that AxisAI will not accept."""

    public_code = CODE_GENERATION_UNAVAILABLE
    i18n_key = I18N_GENERATION_UNAVAILABLE
    http_status = 500
    retryable = True
    repairable = False

    def to_body(self, translate) -> dict:
        return {
            "error": translate(self.i18n_key),
            "code": self.public_code,
            "retryable": self.retryable,
        }


class ParseFailedError(GenerationOutputError):
    public_code = CODE_GENERATION_PARSE_FAILED
    i18n_key = I18N_GENERATION_PARSE_FAILED
    repairable = True


class TruncatedError(GenerationOutputError):
    public_code = CODE_GENERATION_TRUNCATED
    i18n_key = I18N_GENERATION_TRUNCATED
    repairable = True


class PlanValidationError(GenerationOutputError):
    """Validated JSON that is not an acceptable plan. Not parse-repairable."""

    public_code = CODE_GENERATION_SCHEMA_INVALID
    i18n_key = I18N_GENERATION_SCHEMA_INVALID
    repairable = False


class SchemaInvalidError(PlanValidationError):
    """Structurally invalid JSON object. Not eligible for parse repair."""


class SemanticInvalidError(PlanValidationError):
    """Structurally valid object that violates the accepted generation command."""

    public_code = CODE_GENERATION_SEMANTICALLY_INVALID
    i18n_key = I18N_GENERATION_SEMANTICALLY_INVALID


class GenerationUnavailableError(GenerationOutputError):
    public_code = CODE_GENERATION_UNAVAILABLE
    i18n_key = I18N_GENERATION_UNAVAILABLE
    repairable = False


class SaveInvalidError(GenerationOutputError):
    """Client persistence payload failed canonical validation."""

    public_code = CODE_SAVE_INVALID
    i18n_key = I18N_SAVE_INVALID
    http_status = 422
    retryable = False
    repairable = False
