"""MON-02-BE-03A AI allowance ledger (dark; ``AI_ALLOWANCE_LEDGER_ENABLED`` OFF).

No existing route imports this package in BE-03A; enforcement wiring is BE-03B.
Contract: docs/mobile/ai-allowances-v1.md. Design: docs/AI_ALLOWANCE_LEDGER.md.
"""
from app.services.ai_allowances.errors import AllowanceError, CATALOG
from app.services.ai_allowances.policy import (
    AllowancePolicy, ScopePolicy, SUPPORTED_FEATURES, EXTENSIONS, canonical_feature,
)
from app.services.ai_allowances.service import (
    BILLING_CONFIRMED_BILLED, BILLING_CONFIRMED_NOT_BILLED, BILLING_UNKNOWN,
    FAILURE_OUTCOMES, OUTCOME_SUCCESS, RESERVATION_TTL, Reservation, Settlement,
    allowance_view, expire_abandoned, finalize, grant_starter, purge_retained, reserve,
    reserve_within,
)

__all__ = [
    "AllowanceError", "CATALOG", "AllowancePolicy", "ScopePolicy", "SUPPORTED_FEATURES",
    "EXTENSIONS", "canonical_feature", "FAILURE_OUTCOMES", "OUTCOME_SUCCESS",
    "RESERVATION_TTL", "Reservation", "Settlement", "allowance_view", "expire_abandoned",
    "finalize", "grant_starter", "purge_retained", "reserve", "reserve_within",
    "BILLING_CONFIRMED_BILLED", "BILLING_CONFIRMED_NOT_BILLED", "BILLING_UNKNOWN",
    "compensate_training_expiry",
]

from app.services.ai_allowances.training_compensation import compensate as compensate_training_expiry
