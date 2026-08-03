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
  * the expiry leeway both paths pass to the validator — one value, pinned to
    zero, with the per-client knob retired and its old key rejected at boot,
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
# ONE canonical value for request authentication, and it is zero on every path.
# Leeway is the window in which an already-expired token is still accepted, so
# a non-zero value is a security decision, not a tuning knob. Web never had a
# knob; mobile's was retired here rather than generalised, because generalising
# it would have let a mobile-named setting widen the browser's expired-token
# window too.
#
# If production evidence ever shows non-zero leeway is genuinely required, that
# is a separate security-reviewed change applying to BOTH paths at once — not a
# per-client setting. docs/AUTH_CONTRACT.md §3 records the reasoning.
REQUEST_CLOCK_SKEW_SECONDS = 0

# Retired 2026-08-03. Kept as a named constant because a retired setting must
# be REJECTED rather than ignored: a host still carrying a non-zero value
# believes mobile tolerates expired tokens, and silently dropping it would make
# the documentation and the running system disagree without anyone noticing.
RETIRED_CLOCK_SKEW_KEY = "MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS"


class AuthContractConfigurationError(RuntimeError):
    """A retired authentication setting is still configured on this host."""


def validation_clock_skew_seconds(path):
    """Return the expiry leeway ``path`` passes to ``cognito_jwt``.

    ``path`` is still required, and the answer is deliberately independent of
    it: a caller has to name which boundary it is, and gets told that both get
    the same number. Config is not consulted — there is nothing to consult.
    """
    if path not in PATHS:
        raise ValueError(f"unknown authentication path: {path!r}")
    return REQUEST_CLOCK_SKEW_SECONDS


def enforce_retired_settings(environ):
    """Refuse to boot while a retired authentication setting is still set.

    Checked UNCONDITIONALLY — not behind ``MOBILE_AUTH_ENABLED``. A host with
    the mobile flag off can still be carrying a stale non-zero value, and that
    is precisely the value that would take effect at the worst possible moment:
    the day mobile auth is switched on. Failing here, with the deploy health
    gate able to roll back, is much cheaper than discovering it during a
    rollout.

    Accepted: absent, or an explicit ``0`` (which agrees with the pinned
    contract). Everything else — non-zero, empty, non-numeric — is rejected,
    matching the strictness the setting already had before retirement.
    """
    raw = environ.get(RETIRED_CLOCK_SKEW_KEY)
    if raw is None:
        return
    value = raw.strip()
    try:
        parsed = int(value)
    except ValueError:
        raise AuthContractConfigurationError(
            f"{RETIRED_CLOCK_SKEW_KEY} is retired and its value "
            f"{raw!r} is not a valid integer. Remove the line from the host "
            f".env — see docs/AUTH_CONTRACT.md §3.") from None
    if parsed != REQUEST_CLOCK_SKEW_SECONDS:
        raise AuthContractConfigurationError(
            f"{RETIRED_CLOCK_SKEW_KEY}={parsed} is retired and would have made "
            f"mobile accept tokens up to {parsed}s past expiry while web "
            f"accepts none. Request authentication is now pinned to "
            f"{REQUEST_CLOCK_SKEW_SECONDS}s on both paths. Remove the line "
            f"from the host .env — see docs/AUTH_CONTRACT.md §3.")


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

def contract_state():
    """Names and integers only — safe for logs and /health?deep=1.

    Every value is a property of the deployed build rather than of the host
    `.env`, which is exactly what makes it worth publishing: an operator can
    confirm the running process holds the pinned contract instead of an older
    one that still honoured a per-client knob.
    """
    return {
        "request_token_use": REQUEST_TOKEN_USE,
        "request_clock_skew_seconds": REQUEST_CLOCK_SKEW_SECONDS,
        "clock_skew_configurable": False,
        "paths": list(PATHS),
    }


def log_contract_state(app):
    """Emit the contract on one boot line.

    There is no divergence branch any more: a host that still configures the
    retired knob does not reach this line, because enforce_retired_settings
    stops the boot first.
    """
    state = contract_state()
    app.logger.info(
        "[AUTH_CONTRACT] token_use=%s skew=%ss paths=%s configurable=no",
        state["request_token_use"], state["request_clock_skew_seconds"],
        ",".join(state["paths"]))
