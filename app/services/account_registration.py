"""Canonical account registration and email verification (LP-01).

One authority, two transports. The browser routes in `app/blueprints/auth.py`
and the native `/api/v1/auth/{register,verify,verify/resend}` routes in
`app/blueprints/mobile_registration.py` both call THIS module; neither calls
`cognito_service.sign_up` / `confirm_sign_up` / `resend_code` itself
(`tests/test_account_registration_architecture.py`).

The module owns everything that is not presentation:

  - input normalization and validation (the existing `validators` rules),
  - the local username/e-mail collision pre-check,
  - the provider call, bounded by the shared `blocking_concurrency_slot`,
  - classification of provider failures into the closed `Outcome` vocabulary,
  - the local `User` row written at registration (unusable local password,
    `cognito_sub` bound, pending referral marker),
  - the post-confirmation effects (pending referral, welcome e-mail).

It never returns a redirect, a flash message, HTML or a JSON response, never
reads `request`/`session`/cookies, and never creates an authenticated session
of any kind: registration and confirmation end with an unauthenticated caller.
Signing in stays with the existing login authorities.

Failures raise `RegistrationFailure`. `outcome` is the transport-neutral
classification a native client is mapped from. `detail` (a localized validator
sentence) and `provider_message` (the existing Turkish Cognito sentence) exist
ONLY so the browser route can keep rendering exactly what it rendered before
the extraction; the native transport never reads them.

Logging: one line per operation outcome, `account_registration event=...
outcome=... request_id=...`. Never a username, e-mail, password or code.
"""

from dataclasses import dataclass

from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.i18n import AVAILABLE_LOCALES
from app.models import User
from app.observability import current_request_id
from app.services import cognito_service, email_service, email_templates
from app.services.ai_gate import (
    BlockingConcurrencyLimit, blocking_concurrency_slot,
)
from app.services.referral import consume_referral, ensure_referral_code
from app.services.validators import (
    validate_email, validate_password, validate_username,
)


PENDING_REFERRAL_KEY = "pending_referral_code"
DEFAULT_LANGUAGE = "tr"


class Outcome:
    """Closed failure vocabulary. Transport adapters map these, nothing else."""

    FIELDS_REQUIRED = "fields_required"
    USERNAME_INVALID = "username_invalid"
    EMAIL_INVALID = "email_invalid"
    PASSWORD_INVALID = "password_invalid"
    IDENTITY_UNAVAILABLE = "identity_unavailable"
    INPUT_REJECTED = "input_rejected"
    CODE_INVALID = "code_invalid"
    CODE_EXPIRED = "code_expired"
    NOT_PENDING = "not_pending"
    THROTTLED = "throttled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    STORAGE_FAILED = "storage_failed"


class Phase:
    """Where a failure happened; the browser adapter's parity depends on it."""

    VALIDATION = "validation"
    PROVIDER = "provider"
    PERSISTENCE = "persistence"


class RegistrationFailure(Exception):
    def __init__(self, outcome, phase, detail=None, provider_message=None):
        super().__init__(outcome)
        self.outcome = outcome
        self.phase = phase
        self.detail = detail
        self.provider_message = provider_message


@dataclass(frozen=True)
class RegisteredAccount:
    username: str
    referred: bool = False


@dataclass(frozen=True)
class ConfirmedAccount:
    referred: bool


# Provider error code → outcome, per operation. Anything not listed is
# PROVIDER_UNAVAILABLE: an unrecognized provider answer is never turned into a
# more specific (and possibly false) claim. The empty code is a network or
# client failure that never reached a Cognito answer.
_SIGN_UP_OUTCOMES = {
    "UsernameExistsException": Outcome.IDENTITY_UNAVAILABLE,
    "AliasExistsException": Outcome.IDENTITY_UNAVAILABLE,
    "InvalidPasswordException": Outcome.PASSWORD_INVALID,
    "InvalidParameterException": Outcome.INPUT_REJECTED,
    "LimitExceededException": Outcome.THROTTLED,
}
_CONFIRM_OUTCOMES = {
    "CodeMismatchException": Outcome.CODE_INVALID,
    # Unknown user and "cannot be confirmed" (already confirmed, disabled) are
    # the same answer as a wrong code: none of them may become an oracle.
    "UserNotFoundException": Outcome.CODE_INVALID,
    "NotAuthorizedException": Outcome.CODE_INVALID,
    "ExpiredCodeException": Outcome.CODE_EXPIRED,
    "InvalidParameterException": Outcome.INPUT_REJECTED,
    "LimitExceededException": Outcome.THROTTLED,
    "TooManyFailedAttemptsException": Outcome.THROTTLED,
}
_RESEND_OUTCOMES = {
    "UserNotFoundException": Outcome.NOT_PENDING,
    # Cognito answers an already-confirmed account with InvalidParameter.
    "InvalidParameterException": Outcome.NOT_PENDING,
    "NotAuthorizedException": Outcome.NOT_PENDING,
    "LimitExceededException": Outcome.THROTTLED,
}


def _event(event, outcome, level="info"):
    getattr(current_app.logger, level)(
        "account_registration event=%s outcome=%s request_id=%s",
        event, outcome, current_request_id())


def _text(value):
    """Strip a submitted string; anything that is not a string is absent."""
    return value.strip() if isinstance(value, str) else ""


def _call_provider(event, table, operation, **kwargs):
    """Run one provider round-trip inside the shared blocking-capacity slot.

    Only the network call is inside the slot — no DB read or write happens
    while a permit is held (house rule, docs/CAPACITY.md §3).
    """
    try:
        with blocking_concurrency_slot():
            return operation(**kwargs)
    except BlockingConcurrencyLimit as exc:
        _event(event, Outcome.CAPACITY_EXHAUSTED, "warning")
        raise RegistrationFailure(
            Outcome.CAPACITY_EXHAUSTED, Phase.PROVIDER) from exc
    except cognito_service.CognitoServiceError as exc:
        outcome = table.get(exc.code, Outcome.PROVIDER_UNAVAILABLE)
        _event(event, outcome)
        raise RegistrationFailure(
            outcome, Phase.PROVIDER, provider_message=exc.message) from exc


def register_account(username, email, password, language=None,
                     referral_code=None):
    """Create a pending account at the provider and its local profile row.

    `username` is used exactly as submitted (the validator rejects whitespace);
    `email` is trimmed and lower-cased before validation, the provider and
    storage. `language` outside `AVAILABLE_LOCALES` becomes the default; the
    caller decides any other fallback before calling. The account cannot sign
    in until `confirm_account` succeeds — the provider refuses login for an
    unconfirmed user and this function issues no session.
    """
    username = username if isinstance(username, str) else ""
    email = _text(email).lower()
    password = password if isinstance(password, str) else ""
    if language not in AVAILABLE_LOCALES:
        language = DEFAULT_LANGUAGE

    if not username or not email or not password:
        raise RegistrationFailure(Outcome.FIELDS_REQUIRED, Phase.VALIDATION)
    for outcome, message in (
            (Outcome.PASSWORD_INVALID, validate_password(password)),
            (Outcome.USERNAME_INVALID, validate_username(username)),
            (Outcome.EMAIL_INVALID, validate_email(email))):
        if message:
            raise RegistrationFailure(outcome, Phase.VALIDATION, detail=message)

    # One outcome for a taken username and a taken e-mail: the answer must not
    # say which one exists (enumeration hardening, pinned by tests).
    taken = (User.query.filter_by(username=username).first()
             or User.query.filter(db.func.lower(User.email) == email).first())
    # Close the read transaction before the provider round-trip so no pooled
    # connection idles inside a transaction for its length.
    db.session.rollback()
    if taken:
        _event("register", Outcome.IDENTITY_UNAVAILABLE)
        raise RegistrationFailure(
            Outcome.IDENTITY_UNAVAILABLE, Phase.VALIDATION)

    sub = _call_provider(
        "register", _SIGN_UP_OUTCOMES, cognito_service.sign_up,
        username=username, password=password, email=email, name=username)

    # The local row makes the profile/referral data exist immediately. Its
    # password is unusable: sign-in goes through the provider only.
    user = User(username=username, email=email, cognito_sub=sub or None,
                full_name=username, language=language)
    referral_code = _text(referral_code)
    if referral_code:
        user.user_metadata = {PENDING_REFERRAL_KEY: referral_code}
    ensure_referral_code(user)
    db.session.add(user)
    try:
        db.session.commit()
    except Exception as exc:
        # The provider account exists but the local row does not (a Cognito
        # orphan). An unsigned public client cannot delete it; login-time
        # reconciliation (auth._reconcile_local_user / mobile_auth._resolve_user)
        # binds the verified identity later, so the user is not locked out.
        db.session.rollback()
        current_app.logger.error(
            "[REGISTER] Cognito sign_up başarılı ama yerel commit başarısız "
            "— Cognito orphan olası, manuel temizlik/retry gerekir: %s",
            type(exc).__name__)
        if isinstance(exc, IntegrityError):
            _event("register", Outcome.IDENTITY_UNAVAILABLE, "warning")
            raise RegistrationFailure(
                Outcome.IDENTITY_UNAVAILABLE, Phase.PERSISTENCE) from exc
        _event("register", Outcome.STORAGE_FAILED, "error")
        raise RegistrationFailure(
            Outcome.STORAGE_FAILED, Phase.PERSISTENCE) from exc
    _event("register", "pending_verification")
    return RegisteredAccount(username=username)


def confirm_account(username, code):
    """Confirm a pending account with the code the provider e-mailed.

    Success means the provider marked the account confirmed. It is not a
    sign-in: nothing here creates a browser or native session.
    """
    username = _text(username)
    code = _text(code)
    if not username or not code:
        raise RegistrationFailure(Outcome.FIELDS_REQUIRED, Phase.VALIDATION)

    _call_provider(
        "confirm", _CONFIRM_OUTCOMES, cognito_service.confirm_sign_up,
        username=username, code=code)

    user = User.query.filter_by(username=username).first()
    referred = _consume_pending_referral(user) if user else False
    _send_welcome_email(user)
    _event("confirm", "confirmed")
    return ConfirmedAccount(referred=referred)


def resend_confirmation(username):
    """Ask the provider to e-mail a fresh confirmation code."""
    username = _text(username)
    if not username:
        raise RegistrationFailure(Outcome.FIELDS_REQUIRED, Phase.VALIDATION)
    _call_provider(
        "resend", _RESEND_OUTCOMES, cognito_service.resend_code,
        username=username)
    _event("resend", "requested")


def _consume_pending_referral(user):
    """Consume and clear a referral saved during unverified signup."""
    metadata = dict(user.user_metadata or {})
    code = metadata.get(PENDING_REFERRAL_KEY)
    if not code:
        return False

    referred = bool(consume_referral(user, code))

    # consume_referral may commit or roll back its atomic claim. Reload the row,
    # then clear the one-time marker for valid and invalid referral codes alike.
    user = db.session.get(User, user.id)
    db.session.refresh(user)
    metadata = dict(user.user_metadata or {})
    metadata.pop(PENDING_REFERRAL_KEY, None)
    user.user_metadata = metadata
    flag_modified(user, "user_metadata")
    db.session.commit()
    return referred


def _send_welcome_email(user):
    """Best-effort welcome e-mail after confirmation; never raises.

    A template, database or delivery failure is only logged — the confirmation
    answer is never affected by it.
    """
    try:
        if user is None or not user.email:
            return
        subject, html, text = email_templates.welcome_email(user.username)
        email_service.send_html_email(user.email, subject, html, text=text)
        current_app.logger.info("[AUTH-EMAIL] welcome kuyruklandı: user=%s to=%s",
                                user.username, email_service.mask_email(user.email))
    except Exception:
        current_app.logger.warning("[AUTH-EMAIL] welcome gönderilemedi (user=%s)",
                                   getattr(user, "username", "?"), exc_info=True)
