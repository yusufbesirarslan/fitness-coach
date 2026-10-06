"""Canonical registration service (LP-01): typed, transport-agnostic outcomes.

Exercises `app/services/account_registration.py` directly, without either
transport, so the outcome vocabulary both adapters map from is pinned in one
place.

    python -m pytest tests/test_account_registration_service.py -v
"""
import pytest

from app.extensions import db
from app.models import User
from app.services import account_registration, cognito_service
from app.services.account_registration import (
    ConfirmedAccount, Outcome, Phase, RegisteredAccount, RegistrationFailure,
)
from app.services.cognito_service import CognitoServiceError


@pytest.fixture
def provider(monkeypatch):
    calls = []
    failures = {}

    def record(name):
        def call(**kwargs):
            args = tuple(kwargs.values())
            calls.append((name, args))
            if name in failures:
                raise failures[name]
            return f"sub-{args[0]}" if name == "sign_up" else None
        return call

    monkeypatch.setattr(cognito_service, "sign_up", record("sign_up"))
    monkeypatch.setattr(cognito_service, "confirm_sign_up", record("confirm"))
    monkeypatch.setattr(cognito_service, "resend_code", record("resend"))
    return {"calls": calls, "failures": failures}


def _failure(callable_, *args, **kwargs):
    with pytest.raises(RegistrationFailure) as caught:
        callable_(*args, **kwargs)
    return caught.value


def test_register_creates_pending_local_profile(app, provider):
    account = account_registration.register_account(
        "svcuser", " SVC@Example.com ", "Sifre123", language="en",
        referral_code=" REF1 ")
    assert account == RegisteredAccount(username="svcuser", referred=False)
    assert provider["calls"] == [
        ("sign_up", ("svcuser", "Sifre123", "svc@example.com", "svcuser", "en"))]
    user = User.query.filter_by(username="svcuser").one()
    assert (user.email, user.cognito_sub, user.language, user.full_name) == (
        "svc@example.com", "sub-svcuser", "en", "svcuser")
    assert user.password_hash is None
    assert user.user_metadata == {"pending_referral_code": "REF1"}
    assert user.referral_code


def test_register_unknown_language_defaults_to_tr(app, provider):
    account_registration.register_account(
        "svcuser", "svc@example.com", "Sifre123", language="xx")
    assert User.query.filter_by(username="svcuser").one().language == "tr"


@pytest.mark.parametrize(("args", "outcome"), [
    (("", "svc@example.com", "Sifre123"), Outcome.FIELDS_REQUIRED),
    ((None, "svc@example.com", "Sifre123"), Outcome.FIELDS_REQUIRED),
    ((5, "svc@example.com", "Sifre123"), Outcome.FIELDS_REQUIRED),
    (("svcuser", ["a"], "Sifre123"), Outcome.FIELDS_REQUIRED),
    (("svcuser", "svc@example.com", b"Sifre123"), Outcome.FIELDS_REQUIRED),
    (("svcuser", "svc@example.com", "kisa1"), Outcome.PASSWORD_INVALID),
    (("ab", "svc@example.com", "Sifre123"), Outcome.USERNAME_INVALID),
    (("svcuser", "nope", "Sifre123"), Outcome.EMAIL_INVALID),
])
def test_register_validation_outcomes(app, provider, args, outcome):
    failure = _failure(account_registration.register_account, *args)
    assert (failure.outcome, failure.phase) == (outcome, Phase.VALIDATION)
    assert failure.provider_message is None
    assert provider["calls"] == []
    assert User.query.count() == 0


def test_register_validation_failures_carry_the_validator_sentence(
        app, provider):
    failure = _failure(account_registration.register_account,
                       "svcuser", "svc@example.com", "12345678")
    assert failure.detail  # the web route renders exactly this sentence


def test_register_local_collision_is_one_outcome_without_provider_call(
        app, provider, make_user):
    make_user("taken", email="taken@example.com")
    by_name = _failure(account_registration.register_account,
                       "taken", "fresh@example.com", "Sifre123")
    by_mail = _failure(account_registration.register_account,
                       "fresh", "TAKEN@example.com", "Sifre123")
    for failure in (by_name, by_mail):
        assert (failure.outcome, failure.phase) == (
            Outcome.IDENTITY_UNAVAILABLE, Phase.VALIDATION)
    assert provider["calls"] == []


@pytest.mark.parametrize(("operation", "code", "outcome"), [
    ("sign_up", "UsernameExistsException", Outcome.IDENTITY_UNAVAILABLE),
    ("sign_up", "AliasExistsException", Outcome.IDENTITY_UNAVAILABLE),
    ("sign_up", "InvalidPasswordException", Outcome.PASSWORD_INVALID),
    ("sign_up", "InvalidParameterException", Outcome.INPUT_REJECTED),
    ("sign_up", "LimitExceededException", Outcome.THROTTLED),
    ("sign_up", "TooManyRequestsException", Outcome.PROVIDER_UNAVAILABLE),
    ("sign_up", "SomethingNewException", Outcome.PROVIDER_UNAVAILABLE),
    ("sign_up", "", Outcome.PROVIDER_UNAVAILABLE),
    ("confirm", "CodeMismatchException", Outcome.CODE_INVALID),
    ("confirm", "UserNotFoundException", Outcome.CODE_INVALID),
    ("confirm", "NotAuthorizedException", Outcome.CODE_INVALID),
    ("confirm", "ExpiredCodeException", Outcome.CODE_EXPIRED),
    ("confirm", "LimitExceededException", Outcome.THROTTLED),
    ("confirm", "TooManyFailedAttemptsException", Outcome.THROTTLED),
    ("confirm", "InternalErrorException", Outcome.PROVIDER_UNAVAILABLE),
    ("confirm", "", Outcome.PROVIDER_UNAVAILABLE),
    ("resend", "UserNotFoundException", Outcome.NOT_PENDING),
    ("resend", "InvalidParameterException", Outcome.NOT_PENDING),
    ("resend", "LimitExceededException", Outcome.THROTTLED),
    ("resend", "CodeDeliveryFailureException", Outcome.PROVIDER_UNAVAILABLE),
    ("resend", "", Outcome.PROVIDER_UNAVAILABLE),
])
def test_provider_failures_are_classified(app, provider, operation, code,
                                          outcome):
    provider["failures"][operation] = CognitoServiceError("sentence", code)
    call = {
        "sign_up": lambda: account_registration.register_account(
            "svcuser", "svc@example.com", "Sifre123"),
        "confirm": lambda: account_registration.confirm_account(
            "svcuser", "123456"),
        "resend": lambda: account_registration.resend_confirmation("svcuser"),
    }[operation]
    failure = _failure(call)
    assert (failure.outcome, failure.phase) == (outcome, Phase.PROVIDER)
    assert failure.provider_message == "sentence"
    assert User.query.filter_by(username="svcuser").first() is None


def test_register_commit_race_is_identity_unavailable_in_persistence(
        app, provider, monkeypatch):
    def racing(username, password, email, name, language=None):
        db.session.add(User(username="racer", email=email, password_hash="x"))
        db.session.commit()
        return "sub-x"

    monkeypatch.setattr(cognito_service, "sign_up", racing)
    failure = _failure(account_registration.register_account,
                       "svcuser", "svc@example.com", "Sifre123")
    assert (failure.outcome, failure.phase) == (
        Outcome.IDENTITY_UNAVAILABLE, Phase.PERSISTENCE)


def test_register_releases_the_read_transaction_before_the_provider_call(
        app, provider, monkeypatch):
    observed = []

    def sign_up(username, password, email, name, language=None):
        observed.append(db.session().in_transaction())
        return "sub-x"

    monkeypatch.setattr(cognito_service, "sign_up", sign_up)
    account_registration.register_account(
        "svcuser", "svc@example.com", "Sifre123")
    assert observed == [False]


def test_confirm_and_resend_strip_and_require_fields(app, provider):
    assert account_registration.confirm_account(" svcuser ", " 123456 ") == (
        ConfirmedAccount(referred=False))
    account_registration.resend_confirmation(" svcuser ")
    assert provider["calls"] == [("confirm", ("svcuser", "123456")),
                                 ("resend", ("svcuser",))]
    for call in (lambda: account_registration.confirm_account("svcuser", " "),
                 lambda: account_registration.confirm_account(None, "1"),
                 lambda: account_registration.resend_confirmation(["x"])):
        assert _failure(call).outcome == Outcome.FIELDS_REQUIRED


def test_service_never_creates_a_session(app, provider, monkeypatch):
    from app.models import MobileAuthSession
    from app.services import mobile_auth, session_store

    forbidden = lambda *a, **k: (_ for _ in ()).throw(  # noqa: E731
        AssertionError("no session authority may be reached"))
    monkeypatch.setattr(cognito_service, "authenticate", forbidden)
    monkeypatch.setattr(mobile_auth, "login", forbidden)
    monkeypatch.setattr(session_store, "create", forbidden)
    account_registration.register_account(
        "svcuser", "svc@example.com", "Sifre123")
    account_registration.confirm_account("svcuser", "123456")
    account_registration.resend_confirmation("svcuser")
    assert MobileAuthSession.query.count() == 0
