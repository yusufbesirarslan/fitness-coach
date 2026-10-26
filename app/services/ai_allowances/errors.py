"""Closed allowance error vocabulary: (code, HTTP status, retryable)."""

CATALOG = {
    "AI_ACTION_BUDGET_EXHAUSTED": (422, False),
    "AI_ALLOWANCE_DISABLED": (503, False),
    "AI_ALLOWANCE_POLICY_UNCONFIGURED": (503, False),
    "AI_ALLOWANCE_INVALID_REQUEST": (400, False),
    "AI_ALLOWANCE_FEATURE_UNSUPPORTED": (400, False),
    "AI_ALLOWANCE_ACCOUNT_UNAVAILABLE": (409, False),
    "AI_FEATURE_NOT_IN_PLAN": (402, False),
    "AI_ALLOWANCE_EXHAUSTED": (402, False),
    "AI_ALLOWANCE_IN_PROGRESS": (409, True),
    "AI_ALLOWANCE_IDEMPOTENCY_CONFLICT": (409, False),
    "AI_ALLOWANCE_RESERVATION_NOT_FOUND": (404, False),
    "AI_ALLOWANCE_RESERVATION_CONFLICT": (409, False),
    "AI_ALLOWANCE_TEMPORARILY_UNAVAILABLE": (503, True),
}


class AllowanceError(Exception):
    """``details`` carries only feature/scope/reset tokens, never identifiers."""

    def __init__(self, code, **details):
        if code not in CATALOG:
            raise ValueError(code)
        self.code = code
        self.status, self.retryable = CATALOG[code]
        self.details = details
        super().__init__(code)
