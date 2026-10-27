"""Non-AI general first Training plan (LP21). See docs/LP21_NONAI_GENERAL_PLAN.md.

A deterministic, catalog-backed template persisted as a real canonical
``TrainingPlan``. No provider, consent, allowance or quota module is involved,
and nothing about the person is read except the account language.
"""
from .contract import GeneralCardio, GeneralPlanRequest, parse_general_request
from .errors import GeneralPlanSuperseded, GeneralPlanUnavailable
from .service import GeneralPlanResult, create_general_plan, is_general_receipt
from .template import CARDIO_TEMPLATE_ID, TEMPLATE_ID

__all__ = [
    "CARDIO_TEMPLATE_ID",
    "GeneralCardio",
    "GeneralPlanRequest",
    "GeneralPlanResult",
    "GeneralPlanSuperseded",
    "GeneralPlanUnavailable",
    "TEMPLATE_ID",
    "create_general_plan",
    "is_general_receipt",
    "parse_general_request",
]
