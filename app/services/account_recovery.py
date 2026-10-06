"""Canonical password recovery (LP-02).

One authority, two transports. The browser routes `/forgot-password` and
`/reset-password` in `app/blueprints/auth.py` and the native
`/api/v1/auth/password/{forgot,reset}` routes in
`app/blueprints/mobile_password_recovery.py` both call THIS module; neither
calls `cognito_service.forgot_password` / `confirm_forgot_password` itself
(`tests/test_account_recovery_architecture.py`).

The module owns everything that is not presentation:

  - identifier normalization, the e-mail → username resolution and the
    local owner of the identity, all through the one case-insensitive rule in
    `cognito_identity` (the provider pool is case-insensitive with an e-mail
    alias; a reset for `alice` must revoke `Alice`),
  - the canonical password policy (`validators.validate_password`, the same
    rule registration uses),
  - the provider call, bounded by the shared `blocking_concurrency_slot`,
  - classification of provider failures into the closed `Outcome` vocabulary,
  - the non-enumeration rule for reset requests: every provider answer to a
    reset request is swallowed, so no transport can turn one into an oracle,
  - what a successful reset does to existing sessions: every web and mobile
    session issued under the old credential is revoked (local, authoritative,
    fenced against in-flight logins) before any provider-side revocation, and
    the password-changed notice is sent.

It never returns a redirect, a flash message, HTML or a JSON response, never
reads `request`/`session`/cookies, and never creates an authenticated session
of any kind: a successful reset ends with an unauthenticated caller who signs
in through the existing login authorities.

Failures raise `RecoveryFailure`. `outcome` is the transport-neutral
classification the native client is mapped from; `phase` and `detail` (the
localized validator sentence) exist so the browser route keeps rendering
exactly what it rendered before the extraction. The native transport never
reads `detail`.

Logging: one line per operation outcome, `account_recovery event=...
outcome=... request_id=...`. Never an identifier, password or code.
"""

from dataclasses import dataclass

from flask import current_app

from app.extensions import db
from app.i18n import AVAILABLE_LOCALES, DEFAULT_LOCALE
from app.models import User
from app.observability import current_request_id
from app.services import (
    cognito_identity, cognito_service, email_service, email_templates,
    mobile_auth, session_store,
)
from app.services.ai_gate import (
    BlockingConcurrencyLimit, blocking_concurrency_slot,
)
from app.services.validators import validate_password


# Most provider refresh tokens one credential change will try to revoke. Local
# revocation is unbounded and authoritative; this only bounds the network work.
PROVIDER_REVOKE_LIMIT = 20


class Outcome:
    """Closed failure vocabulary. Transport adapters map these, nothing else."""

    FIELDS_REQUIRED = "fields_required"
    PASSWORD_INVALID = "password_invalid"
    CODE_INVALID = "code_invalid"
    CODE_EXPIRED = "code_expired"
    # The provider refused further attempts on this reset (repeated wrong
    # codes). Distinct from THROTTLED only so the browser keeps its sentence.
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    THROTTLED = "throttled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    # The password DID change, but the sessions issued under the old one could
    # not be revoked. Never reported as success.
    SESSIONS_NOT_REVOKED = "sessions_not_revoked"


class Phase:
    """Where a failure happened; the browser adapter's parity depends on it."""

    VALIDATION = "validation"
    PROVIDER = "provider"
    REVOCATION = "revocation"


class RecoveryFailure(Exception):
    def __init__(self, outcome, phase, detail=None):
        super().__init__(outcome)
        self.outcome = outcome
        self.phase = phase
        self.detail = detail


@dataclass(frozen=True)
class ResetRequested:
    """A reset request was handed to the provider (or deliberately absorbed).

    `identity` is the provider username the reset targets; the browser keeps it
    in its reset context. It is the submitted identifier when no local account
    matches, so it says nothing about whether an account exists.
    """

    identity: str


@dataclass(frozen=True)
class PasswordReset:
    identity: str


# Reset REQUEST answers, classified for the log line only. Every one of them is
# absorbed: with PreventUserExistenceErrors the provider only reaches account
# state (unconfirmed, no verified e-mail, disabled, delivery failure, the
# per-account send limit, even a Lambda sender failure) for accounts that
# exist, so any of these surfacing would be an existence oracle.
_REQUEST_LOG_OUTCOMES = {
    "UserNotFoundException": "not_eligible",
    "InvalidParameterException": "not_eligible",
    "NotAuthorizedException": "not_eligible",
    "UserNotConfirmedException": "not_eligible",
    "CodeDeliveryFailureException": "not_eligible",
    "LimitExceededException": Outcome.THROTTLED,
    "TooManyRequestsException": Outcome.THROTTLED,
}

# Reset CONFIRM answers. Anything not listed is PROVIDER_UNAVAILABLE: an
# unrecognized provider answer is never turned into a more specific (and
# possibly false) claim. The empty code is a network or client failure that
# never reached a Cognito answer.
_CONFIRM_OUTCOMES = {
    "CodeMismatchException": Outcome.CODE_INVALID,
    "ExpiredCodeException": Outcome.CODE_EXPIRED,
    # Unknown user, unconfirmed or disabled account, no verified e-mail: the
    # same answer as a wrong code. None of them may become an oracle.
    "UserNotFoundException": Outcome.CODE_INVALID,
    "NotAuthorizedException": Outcome.CODE_INVALID,
    "UserNotConfirmedException": Outcome.CODE_INVALID,
    "InvalidParameterException": Outcome.CODE_INVALID,
    "InvalidPasswordException": Outcome.PASSWORD_INVALID,
    "PasswordHistoryPolicyViolationException": Outcome.PASSWORD_INVALID,
    "TooManyFailedAttemptsException": Outcome.ATTEMPTS_EXHAUSTED,
    "LimitExceededException": Outcome.THROTTLED,
    "TooManyRequestsException": Outcome.THROTTLED,
}


def _event(event, outcome, level="info"):
    getattr(current_app.logger, level)(
        "account_recovery event=%s outcome=%s request_id=%s",
        event, outcome, current_request_id())


def _text(value):
    """Strip a submitted string; anything that is not a string is absent."""
    return value.strip() if isinstance(value, str) else ""


def _resolve(identifier):
    """The provider username a submitted identifier names, and its local owner.

    Returns `(identity, owner)`. `owner` is the one local account the provider
    identity belongs to (id, username, email, language), or None when there is
    none; it is resolved by `cognito_identity.resolve_local_user` — username or
    e-mail, compared case-insensitively, because the provider pool is — so the
    account a reset revokes is the account the provider changed, whatever the
    casing. `language` rides on the same statement (LP-14 code e-mails).

    `identity` is what the provider is asked about. A username is used as
    submitted (trimmed; the provider folds case itself). An e-mail is replaced
    by its owner's username when a local account carries it, otherwise it is
    lower-cased and used as-is, so a known and an unknown account take the same
    path to the provider.

    Raises `AmbiguousLocalIdentity` when more than one local row matches. Leaves
    no transaction open: a provider round-trip follows.
    """
    try:
        owner = cognito_identity.resolve_local_user(
            identifier, User.id, User.username, User.email, User.language)
    finally:
        db.session.rollback()
    if "@" not in identifier:
        return identifier, owner
    return (owner.username if owner is not None else identifier.lower()), owner


def _call_provider(event, operation, *args, **kwargs):
    """Run one provider round-trip inside the shared blocking-capacity slot.

    Only the network call is inside the slot — no DB read or write happens
    while a permit is held (house rule, docs/CAPACITY.md §3).
    """
    try:
        with blocking_concurrency_slot():
            return operation(*args, **kwargs)
    except BlockingConcurrencyLimit as exc:
        _event(event, Outcome.CAPACITY_EXHAUSTED, "warning")
        raise RecoveryFailure(
            Outcome.CAPACITY_EXHAUSTED, Phase.PROVIDER) from exc


def request_password_reset(identifier):
    """Ask the provider to e-mail a reset code to the account `identifier` names.

    Returns the same `ResetRequested` for every account-specific provider
    answer — known, unknown, unconfirmed, disabled, throttled per account,
    undeliverable, or a provider failure that only an existing account could
    reach. Raises only for a blank identifier and for our own capacity
    refusal, neither of which depends on the account.
    """
    identifier = _text(identifier)
    if not identifier:
        raise RecoveryFailure(Outcome.FIELDS_REQUIRED, Phase.VALIDATION)
    language = None
    try:
        identity, owner = _resolve(identifier)
        language = _owner_language(owner)
    except cognito_identity.AmbiguousLocalIdentity:
        # Local data cannot say which account this is; the provider can still
        # send the code to the one user it knows. The confirm step refuses to
        # change a password it could not revoke for (`reset_password`), and the
        # answer here stays the same as for any other account.
        _event("request", "identity_ambiguous", "error")
        identity = identifier.lower() if "@" in identifier else identifier
    try:
        _call_provider("request", cognito_service.forgot_password, identity,
                       language=language)
    except cognito_service.CognitoServiceError as exc:
        _event("request", _REQUEST_LOG_OUTCOMES.get(
            exc.code, Outcome.PROVIDER_UNAVAILABLE))
    else:
        _event("request", "requested")
    return ResetRequested(identity=identity)


def _owner_language(owner):
    """The code-email language for a reset request (LP-14).

    Only a resolved local owner has one: its CURRENT `User.language`, read by
    the same single `_resolve` statement (no second query), with the app's one
    rule for an unusable stored value (→ `DEFAULT_LOCALE`, as every other
    account e-mail renders it). Without an owner — unknown, ambiguous or
    provider-only identifier — nothing is synthesized: `None` sends no
    ClientMetadata and the public answer is the same either way.
    """
    if owner is None:
        return None
    return owner.language if owner.language in AVAILABLE_LOCALES else DEFAULT_LOCALE


def reset_password(identifier, code, new_password):
    """Confirm a reset code and install `new_password` at the provider.

    On success every session issued under the old credential — browser rows
    and native families alike — is revoked, and the password-changed notice is
    sent. It is not a sign-in: nothing here creates a browser or native
    session, and the caller signs in with the new password afterwards.
    """
    identifier = _text(identifier)
    code = _text(code)
    new_password = new_password if isinstance(new_password, str) else ""
    if not identifier or not code or not new_password:
        raise RecoveryFailure(Outcome.FIELDS_REQUIRED, Phase.VALIDATION)
    policy_error = validate_password(new_password)
    if policy_error:
        raise RecoveryFailure(
            Outcome.PASSWORD_INVALID, Phase.VALIDATION, detail=policy_error)

    # The local owner is resolved BEFORE the provider call. If it cannot be
    # resolved unambiguously the password must not change at all: a changed
    # password whose sessions nobody revoked is exactly the failure this
    # resolution exists to prevent. Answered like a wrong code — non-retryable,
    # and no oracle for which identifiers collide locally.
    try:
        identity, owner = _resolve(identifier)
    except cognito_identity.AmbiguousLocalIdentity as exc:
        _event("reset", "identity_ambiguous", "error")
        raise RecoveryFailure(Outcome.CODE_INVALID, Phase.PROVIDER) from exc
    try:
        _call_provider(
            "reset", cognito_service.confirm_forgot_password,
            identity, code, new_password)
    except cognito_service.CognitoServiceError as exc:
        outcome = _CONFIRM_OUTCOMES.get(exc.code, Outcome.PROVIDER_UNAVAILABLE)
        _event("reset", outcome)
        raise RecoveryFailure(outcome, Phase.PROVIDER) from exc

    if owner is not None:
        try:
            revoke_all_sessions_after_credential_change(owner.id)
        except mobile_auth.MobileAuthFailure as exc:
            # The password ALREADY changed at the provider, but open sessions
            # could not be closed. Success would be the lie "you are signed out
            # everywhere"; storage is transiently unreachable, so say that.
            current_app.logger.error(
                "[AUTH] şifre değişti ama oturumlar kapatılamadı (user=%s)",
                owner.id)
            _event("reset", Outcome.SESSIONS_NOT_REVOKED, "error")
            raise RecoveryFailure(
                Outcome.SESSIONS_NOT_REVOKED, Phase.REVOCATION) from exc
        _send_password_changed_email(owner.id)
    _event("reset", "password_changed")
    return PasswordReset(identity=identity)


def revoke_all_sessions_after_credential_change(user_id):
    """End every session issued under the OLD credential, then tell the provider.

    Web and mobile fail differently, so both are handled here explicitly:

    * Web sessions die with their server-side row — `require_auth` resolves
      `cognito_sid` against CognitoSession on every request — so deleting the
      rows is sufficient locally.
    * Mobile sessions do NOT. `mobile_auth.authenticate_access` validates the
      STORED provider access token offline, so an opaque credential stays
      accepted for its whole TTL — and its family keeps minting new ones until
      the absolute expiry — unless the family row itself is revoked.
      `revoke_all_for_user` also publishes the credential fence that refuses a
      login which authenticated with the old password while this ran.

    Local revocation is what actually ends the sessions, so it runs first and is
    allowed to fail the request: "nothing is live" and "I could not check" must
    not look the same to the caller.

    The provider calls come last and are best-effort. `ConfirmForgotPassword`
    changes the password but does not revoke refresh tokens, and the reset runs
    unauthenticated: `GlobalSignOut` needs the user's own access token and
    `AdminUserGlobalSignOut` needs AWS credentials this UNSIGNED public client
    does not have. Revoking each stored refresh token reaches the same set —
    every session this app issued — and a provider outage must never make an
    already-changed password look unchanged.
    """
    mobile_results = mobile_auth.revoke_all_for_user(user_id)
    web_provider_refresh = session_store.provider_refresh_tokens_for_user(user_id)
    session_store.delete_for_user(user_id)
    _best_effort_provider_revoke_all(user_id, mobile_results, web_provider_refresh)


def _best_effort_provider_revoke_all(user_id, mobile_results, web_refresh_tokens):
    """Tell Cognito about revocations already committed locally. Never raises.

    Every call here is a blocking provider round-trip, so it takes the shared
    `blocking_concurrency_slot` the same way every other Cognito/FatSecret/model
    caller does: one gunicorn worker with 8 threads cannot afford an ungated
    sequence of network calls (each up to the client's 5s connect + 10s read) or
    /health queues behind it and the deploy gate rolls a healthy build back. One
    slot covers the whole sequence because the sequence is nothing BUT network —
    no cache read, DB write or lock happens while it is held.

    Bounded on purpose: sessions accumulate for the family's whole absolute
    lifetime, so a busy account can hold dozens, and "revoke them all" must not
    become "park a thread for minutes". Anything past the cap stays locally
    revoked — which is what actually ends the session — and is logged.

    Capacity rejection is not an error either: local revocation already ran, so
    skipping the advisory provider call is strictly better than failing a
    password reset that has already changed the password.
    """
    tokens = [result.provider_refresh_token for result in mobile_results
              if result.provider_refresh_token]
    tokens.extend(web_refresh_tokens)
    if not tokens:
        return
    skipped = max(0, len(tokens) - PROVIDER_REVOKE_LIMIT)
    if skipped:
        current_app.logger.warning(
            "[AUTH] sağlayıcı iptali üst sınırda kesildi (user=%s skipped=%d)",
            user_id, skipped)
    try:
        with blocking_concurrency_slot():
            for refresh_token in tokens[:PROVIDER_REVOKE_LIMIT]:
                try:
                    cognito_service.revoke_token(refresh_token)
                except Exception:
                    current_app.logger.warning(
                        "[AUTH] refresh token iptal edilemedi (user=%s)", user_id)
    except BlockingConcurrencyLimit:
        current_app.logger.warning(
            "[AUTH] sağlayıcı iptali kapasite nedeniyle atlandı (user=%s)", user_id)


def _send_password_changed_email(user_id):
    """Password-changed notice — best-effort, NEVER raises.

    The reset already succeeded at the provider; an e-mail failure cannot undo
    or fail it. Addressed by id: the account notified is the account revoked."""
    try:
        user = db.session.get(User, user_id)
        if user is None or not user.email:
            return
        subject, html, text = email_templates.password_changed_email(
            user.username, language=user.language)
        email_service.send_html_email(user.email, subject, html, text=text)
        current_app.logger.info("[AUTH-EMAIL] password-changed kuyruklandı: user=%s to=%s",
                                user_id, email_service.mask_email(user.email))
    except Exception:
        current_app.logger.warning("[AUTH-EMAIL] password-changed gönderilemedi (user=%s)",
                                   user_id, exc_info=True)
