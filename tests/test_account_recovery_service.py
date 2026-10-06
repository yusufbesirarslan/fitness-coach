"""Canonical password-recovery service (LP-02): typed, transport-agnostic outcomes.

Exercises `app/services/account_recovery.py` directly, without either
transport, so the outcome vocabulary both adapters map from — and the
non-enumeration rule for reset requests — is pinned in one place.

    python -m pytest tests/test_account_recovery_service.py -v
"""
import threading

import pytest

from app.extensions import db
from app.models import CognitoSession, MobileAuthSession, User
from app.services import account_recovery, ai_gate, cognito_service, mobile_auth
from app.services.account_recovery import (
    Outcome, PasswordReset, Phase, RecoveryFailure, ResetRequested,
)
from app.services.cognito_service import CognitoServiceError
from app.services.validators import validate_password


PASSWORD = "Newpass123"
CODE = "123456"


@pytest.fixture
def provider(monkeypatch):
    calls = []
    failures = {}

    def forgot_password(username, language=None):
        calls.append(("forgot", (username,)))
        if "forgot" in failures:
            raise failures["forgot"]

    def confirm_forgot_password(username, code, new_password):
        calls.append(("confirm", (username, code, new_password)))
        if "confirm" in failures:
            raise failures["confirm"]

    monkeypatch.setattr(cognito_service, "forgot_password", forgot_password)
    monkeypatch.setattr(
        cognito_service, "confirm_forgot_password", confirm_forgot_password)
    monkeypatch.setattr(cognito_service, "revoke_token", lambda token: None)
    return {"calls": calls, "failures": failures}


def _failure(callable_, *args):
    with pytest.raises(RecoveryFailure) as caught:
        callable_(*args)
    return caught.value


# ---------------------------------------------------------------------------
# request_password_reset
# ---------------------------------------------------------------------------

def test_request_known_email_targets_the_local_username(app, provider,
                                                        make_user):
    make_user("alice", email="alice@example.com")
    result = account_recovery.request_password_reset(" ALICE@example.com ")
    assert result == ResetRequested(identity="alice")
    assert provider["calls"] == [("forgot", ("alice",))]


def test_request_unknown_email_targets_the_lowercased_email(app, provider):
    result = account_recovery.request_password_reset("Ghost@Example.com")
    assert result == ResetRequested(identity="ghost@example.com")
    assert provider["calls"] == [("forgot", ("ghost@example.com",))]


def test_request_username_is_used_as_submitted(app, provider, make_user):
    make_user("Alice", email="alice@example.com")
    assert account_recovery.request_password_reset("  Alice  ") == (
        ResetRequested(identity="Alice"))
    assert account_recovery.request_password_reset("nobody") == (
        ResetRequested(identity="nobody"))


@pytest.mark.parametrize("identifier", [None, "", "   ", 42, ["a"], True])
def test_request_blank_or_non_string_identifier_is_fields_required(
        app, provider, identifier):
    failure = _failure(account_recovery.request_password_reset, identifier)
    assert (failure.outcome, failure.phase) == (
        Outcome.FIELDS_REQUIRED, Phase.VALIDATION)
    assert provider["calls"] == []


@pytest.mark.parametrize("provider_code", [
    "UserNotFoundException", "InvalidParameterException",
    "NotAuthorizedException", "UserNotConfirmedException",
    "CodeDeliveryFailureException", "LimitExceededException",
    "TooManyRequestsException", "InternalErrorException",
    "UserLambdaValidationException", "SomethingNewException", "",
])
def test_request_absorbs_every_provider_answer(app, provider, provider_code):
    """Only an existing account reaches account state at the provider
    (PreventUserExistenceErrors), so no provider answer may escape."""
    provider["failures"]["forgot"] = CognitoServiceError("x", provider_code)
    assert account_recovery.request_password_reset("alice") == (
        ResetRequested(identity="alice"))


def test_request_known_and_unknown_return_structurally_equal_results(
        app, provider, make_user):
    make_user("alice", email="alice@example.com")
    known = account_recovery.request_password_reset("alice")
    provider["failures"]["forgot"] = CognitoServiceError(
        "x", "UserNotFoundException")
    unknown = account_recovery.request_password_reset("nobody")
    assert type(known) is type(unknown) is ResetRequested


def test_request_capacity_exhausted_is_typed_and_skips_the_provider(
        app, provider, monkeypatch):
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    assert semaphore.acquire(blocking=False)
    try:
        failure = _failure(account_recovery.request_password_reset, "alice")
    finally:
        semaphore.release()
    assert (failure.outcome, failure.phase) == (
        Outcome.CAPACITY_EXHAUSTED, Phase.PROVIDER)
    assert provider["calls"] == []


def test_request_writes_nothing_locally(app, provider, make_user):
    user = make_user("alice", email="alice@example.com")
    before = (user.credential_epoch, User.query.count())
    account_recovery.request_password_reset("alice")
    db.session.refresh(user)
    assert (user.credential_epoch, User.query.count()) == before


# ---------------------------------------------------------------------------
# reset_password
# ---------------------------------------------------------------------------

def test_reset_success(app, provider, make_user):
    make_user("alice", email="alice@example.com")
    result = account_recovery.reset_password("alice", f" {CODE} ", PASSWORD)
    assert result == PasswordReset(identity="alice")
    assert provider["calls"] == [("confirm", ("alice", CODE, PASSWORD))]


def test_reset_resolves_an_email_identifier_like_the_request(
        app, provider, make_user):
    make_user("alice", email="alice@example.com")
    account_recovery.reset_password("Alice@Example.com", CODE, PASSWORD)
    assert provider["calls"] == [("confirm", ("alice", CODE, PASSWORD))]


def test_reset_password_is_passed_through_exactly(app, provider):
    account_recovery.reset_password("alice", CODE, " Spaced pass1 ")
    assert provider["calls"][0][1][2] == " Spaced pass1 "


@pytest.mark.parametrize("args", [
    ("", CODE, PASSWORD), ("alice", "", PASSWORD), ("alice", CODE, ""),
    ("  ", CODE, PASSWORD), ("alice", "   ", PASSWORD),
    (None, CODE, PASSWORD), ("alice", 123456, PASSWORD),
    ("alice", CODE, ["Newpass123"]), (True, CODE, PASSWORD),
])
def test_reset_missing_or_non_string_fields(app, provider, args):
    failure = _failure(account_recovery.reset_password, *args)
    assert (failure.outcome, failure.phase) == (
        Outcome.FIELDS_REQUIRED, Phase.VALIDATION)
    assert provider["calls"] == []


@pytest.mark.parametrize("password", [
    "short1", "x" * 129 + "1", "12345678", "abcdefgh",
])
def test_reset_uses_the_canonical_password_policy(app, provider, password):
    failure = _failure(account_recovery.reset_password, "alice", CODE, password)
    assert (failure.outcome, failure.phase) == (
        Outcome.PASSWORD_INVALID, Phase.VALIDATION)
    # Parity: the exact sentence registration's validator produces.
    assert failure.detail == validate_password(password)
    assert provider["calls"] == []


@pytest.mark.parametrize(("provider_code", "outcome"), [
    ("CodeMismatchException", Outcome.CODE_INVALID),
    ("ExpiredCodeException", Outcome.CODE_EXPIRED),
    ("UserNotFoundException", Outcome.CODE_INVALID),
    ("NotAuthorizedException", Outcome.CODE_INVALID),
    ("UserNotConfirmedException", Outcome.CODE_INVALID),
    ("InvalidParameterException", Outcome.CODE_INVALID),
    ("InvalidPasswordException", Outcome.PASSWORD_INVALID),
    ("PasswordHistoryPolicyViolationException", Outcome.PASSWORD_INVALID),
    ("TooManyFailedAttemptsException", Outcome.ATTEMPTS_EXHAUSTED),
    ("LimitExceededException", Outcome.THROTTLED),
    ("TooManyRequestsException", Outcome.THROTTLED),
    ("InternalErrorException", Outcome.PROVIDER_UNAVAILABLE),
    ("SomethingNewException", Outcome.PROVIDER_UNAVAILABLE),
    ("", Outcome.PROVIDER_UNAVAILABLE),
])
def test_reset_provider_failures_are_typed(app, provider, provider_code,
                                           outcome):
    provider["failures"]["confirm"] = CognitoServiceError("x", provider_code)
    failure = _failure(account_recovery.reset_password, "alice", CODE, PASSWORD)
    assert (failure.outcome, failure.phase) == (outcome, Phase.PROVIDER)
    assert failure.detail is None


def test_reset_failure_does_not_revoke_or_notify(app, provider, make_user,
                                                 monkeypatch):
    user = make_user("alice", email="alice@example.com")
    effects = []
    monkeypatch.setattr(mobile_auth, "revoke_all_for_user",
                        lambda *a, **k: effects.append("revoke") or [])
    monkeypatch.setattr(account_recovery, "_send_password_changed_email",
                        lambda username: effects.append("email"))
    provider["failures"]["confirm"] = CognitoServiceError(
        "x", "CodeMismatchException")
    _failure(account_recovery.reset_password, "alice", CODE, PASSWORD)
    assert effects == []
    db.session.refresh(user)
    assert user.credential_epoch == 0


def test_reset_capacity_exhausted_is_typed_and_skips_the_provider(
        app, provider, monkeypatch):
    semaphore = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_ai_slots", semaphore)
    assert semaphore.acquire(blocking=False)
    try:
        failure = _failure(
            account_recovery.reset_password, "alice", CODE, PASSWORD)
    finally:
        semaphore.release()
    assert failure.outcome == Outcome.CAPACITY_EXHAUSTED
    assert provider["calls"] == []


def test_reset_success_revokes_every_session_and_fences_logins(
        app, provider, make_user):
    user = make_user("alice", email="alice@example.com")
    db.session.add(CognitoSession(
        session_id="web-one", user_id=user.id, cognito_username="alice",
        access_token="encrypted", refresh_token="encrypted",
        access_token_exp=db.func.now()))
    db.session.commit()
    account_recovery.reset_password("alice", CODE, PASSWORD)
    db.session.refresh(user)
    assert CognitoSession.query.filter_by(user_id=user.id).count() == 0
    assert user.credential_epoch == 1


def test_reset_success_for_an_unknown_local_account_revokes_nothing(
        app, provider, monkeypatch):
    monkeypatch.setattr(
        mobile_auth, "revoke_all_for_user",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no account")))
    assert account_recovery.reset_password(
        "ghost@example.com", CODE, PASSWORD) == PasswordReset(
            identity="ghost@example.com")


def test_reset_revocation_storage_failure_is_never_success(
        app, provider, make_user, monkeypatch):
    make_user("alice", email="alice@example.com")
    sent = []
    monkeypatch.setattr(
        mobile_auth, "revoke_all_for_user",
        lambda *a, **k: (_ for _ in ()).throw(mobile_auth.MobileAuthFailure(
            "AUTH_TEMPORARILY_UNAVAILABLE", 503, True, "storage_unavailable")))
    monkeypatch.setattr(account_recovery, "_send_password_changed_email",
                        sent.append)
    failure = _failure(account_recovery.reset_password, "alice", CODE, PASSWORD)
    assert (failure.outcome, failure.phase) == (
        Outcome.SESSIONS_NOT_REVOKED, Phase.REVOCATION)
    assert sent == []


def test_reset_issues_no_session(app, provider, make_user, monkeypatch):
    make_user("alice", email="alice@example.com")

    def forbidden(*args, **kwargs):
        raise AssertionError("recovery must never sign anyone in")

    monkeypatch.setattr(cognito_service, "authenticate", forbidden)
    monkeypatch.setattr(mobile_auth, "login", forbidden)
    account_recovery.reset_password("alice", CODE, PASSWORD)
    assert MobileAuthSession.query.count() == 0
    assert CognitoSession.query.count() == 0


def test_reset_repeat_after_success_follows_the_provider(
        app, provider, make_user):
    """No fabricated idempotent success: a spent code is whatever the provider
    says it is, and local state is not touched a second time."""
    user = make_user("alice", email="alice@example.com")
    account_recovery.reset_password("alice", CODE, PASSWORD)
    provider["failures"]["confirm"] = CognitoServiceError(
        "x", "CodeMismatchException")
    failure = _failure(account_recovery.reset_password, "alice", CODE, PASSWORD)
    assert failure.outcome == Outcome.CODE_INVALID
    db.session.refresh(user)
    assert user.credential_epoch == 1
