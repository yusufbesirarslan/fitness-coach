"""Failures specific to the general plan command; the rest are the AI command's.

Reusing ``mobile_training_generation.errors`` for every shared outcome
(invalid request, key conflict, existing plan, persistence) keeps one public
vocabulary for "first Training plan" whichever command produced it.
"""
from app.services.mobile_training_generation.errors import PlanGenerationCommandError


class GeneralPlanSuperseded(PlanGenerationCommandError):
    """The key's plan was replaced or removed; it is not replayed or recreated."""

    public_code = "TRAINING_PLAN_GENERAL_SUPERSEDED"
    http_status = 409
    retryable = False


class GeneralPlanUnavailable(PlanGenerationCommandError):
    """The reviewed template no longer validates against the catalog."""

    public_code = "TRAINING_PLAN_GENERAL_UNAVAILABLE"
    http_status = 503
    retryable = False
