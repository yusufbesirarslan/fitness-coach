"""Canonical cross-client authentication contract.

Both clients already share one validator (``app/services/cognito_jwt.py``).
What they did not share was a single statement of *how* they call it: the web
path hardcoded its expiry leeway while the mobile path read a configurable
knob, and each middleware re-derived for itself which validation failures are
transient and which are definitive. Neither was wrong, but nothing stopped the
two from drifting apart either — a change to one path had no mechanical reason
to reach the other.

This module is that statement. It owns:

  * the token use each request-authentication path requires,
  * the expiry leeway each path passes to the validator,
  * the transient/definitive split applied to a ``TokenValidationError``, and
  * the fixed outcome vocabulary both paths report as metrics.

It deliberately does NOT own the response shape. The web path answers a browser
with a redirect or an i18n message; the mobile path answers an API client with
a machine-readable code (ADR 0001). That difference is intentional, is relied
on by both clients, and stays where it is.

Import direction is one-way: this module is imported by ``app/config.py`` at
boot, by ``app/auth_middleware.py`` and by ``app/services/mobile_auth.py``, so
it must not import any of them back. Flask plus stdlib only.

Contract table and the intentional/accidental classification: docs/AUTH_CONTRACT.md
"""

from flask import current_app


# --- Paths -----------------------------------------------------------------
# Server-side facts, not client-supplied labels. A request is "mobile" because
# the mobile blueprint served it, never because a header said so.
WEB = "web"
MOBILE = "mobile"
PATHS = (WEB, MOBILE)


# --- Token use -------------------------------------------------------------
# Request authentication validates an ACCESS token on both paths. An ID token
# is an identity assertion, not an authorization credential; accepting one here
# would let a token minted for a different purpose authorize API calls.
REQUEST_TOKEN_USE = "access"
# Login-time identity establishment validates an ID token on both paths.
IDENTITY_TOKEN_USE = "id"


# --- Expiry leeway ---------------------------------------------------------
# The web path is PINNED to zero: it has never had an operator knob, and adding
# one silently would widen the window in which an expired token is still
# accepted. The mobile path keeps its existing configurable knob (default 0,
# maximum 300 — app/services/mobile_credentials.py).
#
# This asymmetry is real and is the one accidental difference PR3 found. It is
# kept here rather than fixed here so the two values are decided in ONE place
# and cannot silently diverge further; converging them is a reviewer decision
# recorded in docs/AUTH_CONTRACT.md, not a side effect of this refactor.
WEB_CLOCK_SKEW_SECONDS = 0
MOBILE_CLOCK_SKEW_CONFIG_KEY = "MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS"


def _mobile_skew(config):
    try:
        return int(config.get(MOBILE_CLOCK_SKEW_CONFIG_KEY, 0) or 0)
    except (TypeError, ValueError):
        return 0


def validation_clock_skew_seconds(path):
    """Return the expiry leeway ``path`` passes to ``cognito_jwt``."""
    if path == MOBILE:
        return _mobile_skew(current_app.config)
    return WEB_CLOCK_SKEW_SECONDS


def validate_provider_token(path, token, expected_use=REQUEST_TOKEN_USE):
    """The single call every authentication path makes into the validator.

    Raises ``cognito_jwt.TokenValidationError`` unchanged — classification is
    the caller's job, through :func:`is_transient_validation_reason`, because
    the two paths turn the same classification into different responses.
    """
    from app.services import cognito_jwt
    return cognito_jwt.validate_token(
        token, expected_use,
        leeway_seconds=validation_clock_skew_seconds(path))


# --- Declared validator call sites -----------------------------------------
# Every module allowed to call cognito_jwt.validate_token directly, and the
# reason it is allowed. The drift test in tests/test_auth_contract.py DERIVES
# its allow-list from this mapping rather than keeping a second copy, so a new
# call site cannot appear without a declared reason — the same data-driven rule
# the flag registry uses for `parsed_by`.
#
# Only this module may vary the expiry leeway. The other two entries validate a
# login-time ID token with the pinned default of 0 and are asserted to keep it.
VALIDATOR_CALL_SITES = {
    "app/services/auth_contract.py":
        "canonical request-authentication call for both clients",
    "app/blueprints/auth.py":
        "web login identity assertion (ID token, pinned leeway 0)",
    "app/services/cognito_service.py":
        "shared provider login decode used by both clients "
        "(ID token, pinned leeway 0)",
}


# --- Transient vs. definitive ----------------------------------------------
# "The signature could not be verified" is not "the signature is invalid". A
# cold JWKS cache is real on every fresh container, i.e. after every deploy;
# treating it as a rejection produces a correlated mass logout. Both paths must
# answer 503 + Retry-After here and must leave the session intact.
TRANSIENT_VALIDATION_REASONS = frozenset({"jwks_unavailable"})


def is_transient_validation_reason(reason):
    """True when a ``TokenValidationError`` means "try again", not "denied"."""
    return reason in TRANSIENT_VALIDATION_REASONS


# --- Outcome vocabulary ----------------------------------------------------
# Fixed, small, and shared: these become a CloudWatch dimension, so the set has
# to stay bounded. Never a user id, a token, a header or a raw path.
OUTCOME_OK = "ok"
OUTCOME_NO_IDENTITY = "no_identity"
OUTCOME_SESSION_INVALID = "session_invalid"
OUTCOME_TOKEN_REJECTED = "token_rejected"
OUTCOME_PROVIDER_UNAVAILABLE = "provider_unavailable"
OUTCOMES = frozenset({
    OUTCOME_OK,
    OUTCOME_NO_IDENTITY,
    OUTCOME_SESSION_INVALID,
    OUTCOME_TOKEN_REJECTED,
    OUTCOME_PROVIDER_UNAVAILABLE,
})


# The mobile boundary speaks machine codes (ADR 0001) and the web boundary does
# not; mapping them onto one vocabulary is what makes a single dashboard
# possible. Throttling is deliberately absent: a 429 is not an authentication
# outcome and PR1 already counts it as HttpThrottled.
MOBILE_CODE_OUTCOMES = {
    "AUTH_INVALID_REQUEST": OUTCOME_NO_IDENTITY,
    "AUTH_INVALID_CREDENTIALS": OUTCOME_TOKEN_REJECTED,
    "AUTH_VERIFICATION_REQUIRED": OUTCOME_SESSION_INVALID,
    "AUTH_SESSION_EXPIRED": OUTCOME_SESSION_INVALID,
    "AUTH_REFRESH_FAILED": OUTCOME_SESSION_INVALID,
    "AUTH_TEMPORARILY_UNAVAILABLE": OUTCOME_PROVIDER_UNAVAILABLE,
}


def mobile_outcome_for(code):
    """Map a mobile error code onto the shared outcome vocabulary."""
    return MOBILE_CODE_OUTCOMES.get(code, OUTCOME_SESSION_INVALID)


def record_outcome(path, outcome):
    """Buffer one authentication outcome. Never raises, never blocks a request.

    ``AuthOutcomes`` answers questions ``HttpRequests`` cannot: a 503 on the
    auth path could be an overloaded gate or an unreachable provider, and a
    401 could be an expired session or a rejected token. Cardinality is
    2 paths x 5 outcomes.
    """
    try:
        from app.services import runtime_metrics
        if not runtime_metrics.is_enabled():
            return
        runtime_metrics.increment(
            "AuthOutcomes", dimensions={"Path": path, "Outcome": outcome})
    except Exception:
        pass


# --- Boot-time visibility --------------------------------------------------

def contract_state(config=None):
    """Names and integers only — safe for logs and /health?deep=1.

    ``config`` is passed explicitly at boot, where there is no app context yet;
    inside a request it defaults to the running application's config.
    """
    mobile_skew = (_mobile_skew(config) if config is not None
                   else validation_clock_skew_seconds(MOBILE))
    return {
        "request_token_use": REQUEST_TOKEN_USE,
        "web_clock_skew_seconds": WEB_CLOCK_SKEW_SECONDS,
        "mobile_clock_skew_seconds": mobile_skew,
        "clock_skew_aligned": mobile_skew == WEB_CLOCK_SKEW_SECONDS,
    }


def log_contract_state(app):
    """Emit the contract on one boot line, loudly if the paths disagree.

    The point is that a divergence is never silent again. An operator who sets
    MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS is widening the expired-token
    window for mobile only, and that decision should be visible in the deploy
    log rather than discovered during an incident.
    """
    state = contract_state(app.config)
    app.logger.info(
        "[AUTH_CONTRACT] token_use=%s web_skew=%ss mobile_skew=%ss aligned=%s",
        state["request_token_use"], state["web_clock_skew_seconds"],
        state["mobile_clock_skew_seconds"],
        "yes" if state["clock_skew_aligned"] else "NO")
    if not state["clock_skew_aligned"]:
        app.logger.warning(
            "[AUTH_CONTRACT] mobile accepts tokens up to %ss past expiry while "
            "web accepts none. Intentional? See docs/AUTH_CONTRACT.md.",
            state["mobile_clock_skew_seconds"])
