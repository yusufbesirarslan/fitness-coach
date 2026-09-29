"""Case-insensitive provider identity → one local account (LP-02 closure).

The production Cognito pool is `UsernameConfiguration.CaseSensitive = false`
with `AliasAttributes = [email]`: `Alice`, `alice`, `ALICE` and the account's
e-mail in any casing are ONE provider user. A reset submitted under any of
them changes that user's password, so local revocation and the mobile
credential fence must land on the same local row, and never on another one.

The provider stub below models exactly that folding, so every test drives the
real service, routes, fence and revocation code against it.

    python -m pytest tests/test_recovery_identity_case.py -v
"""
import calendar
from datetime import datetime, timedelta

import pytest

from app.blueprints import mobile_password_recovery
from app.extensions import db
from app.models import CognitoSession, MobileAuthSession, User
from app.services import (
    account_recovery, account_registration, cognito_identity, cognito_jwt,
    cognito_service, mobile_auth, session_store,
)
from app.services.account_recovery import Outcome, RecoveryFailure
from app.services.cognito_identity import AmbiguousLocalIdentity
from app.services.cognito_service import CognitoServiceError


PASSWORD = "Newpass123"
CODE = "482913"

ALICE_USERNAME_VARIANTS = ["Alice", "alice", "ALICE", "aLiCe"]
ALICE_EMAIL_VARIANTS = [
    "alice@example.com", "ALICE@EXAMPLE.COM", "Alice@Example.Com"]


def _now():
    return datetime.utcnow().replace(microsecond=0)


@pytest.fixture
def cognito(monkeypatch):
    """A case-insensitive provider with an e-mail alias, like production.

    `users` maps sub → (username, email). An identifier names a provider user
    when it equals that user's username or e-mail after case folding — the
    documented CaseSensitive=false + email-alias behaviour.
    """
    monkeypatch.setattr(mobile_password_recovery, "COGNITO_ENABLED", True)
    state = {"users": {}, "confirmed": [], "requested": [], "revoked": []}

    def sub_for(identifier):
        folded = identifier.lower()
        for sub, (username, email) in state["users"].items():
            if folded in (username.lower(), email.lower()):
                return sub
        raise CognitoServiceError("x", "UserNotFoundException")

    def forgot_password(identifier):
        state["requested"].append((identifier, sub_for(identifier)))

    def confirm_forgot_password(identifier, code, new_password):
        state["confirmed"].append((identifier, sub_for(identifier)))

    def authenticate(identifier, password):
        sub = sub_for(identifier)
        return {"tokens": {
            "access_token": f"access|{sub}", "id_token": f"id|{sub}",
            "refresh_token": f"refresh|{sub}", "expires_in": 3600},
            "claims": {"sub": sub}}

    def validate(token, expected_use, leeway_seconds=0):
        sub = token.split("|", 1)[1]
        if expected_use == "id":
            return {"sub": sub, "email": state["users"][sub][1],
                    "email_verified": True}
        return {"sub": sub, "exp": calendar.timegm(
            (_now() + timedelta(hours=1)).timetuple())}

    monkeypatch.setattr(cognito_service, "forgot_password", forgot_password)
    monkeypatch.setattr(
        cognito_service, "confirm_forgot_password", confirm_forgot_password)
    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    monkeypatch.setattr(cognito_service, "revoke_token", state["revoked"].append)
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    return state


@pytest.fixture
def accounts(make_user, cognito):
    """Alice and a bystander, each with a live native family and a web row."""

    def build(alice_username="Alice"):
        alice = make_user(alice_username, email="alice@example.com",
                          cognito_sub="sub-alice")
        bob = make_user("Bob", email="bob@example.com", cognito_sub="sub-bob")
        cognito["users"].update({
            "sub-alice": (alice_username, "alice@example.com"),
            "sub-bob": ("Bob", "bob@example.com")})
        sessions = {}
        for user, name in ((alice, "alice"), (bob, "bob")):
            sessions[name] = mobile_auth.login(name, "Oldpass123", now=_now())
            db.session.add(CognitoSession(
                session_id=f"web-{name}", user_id=user.id,
                cognito_username=user.username,
                access_token=session_store.encrypt_token(f"web-access-{name}"),
                refresh_token=session_store.encrypt_token(f"web-refresh-{name}"),
                access_token_exp=datetime.utcnow() + timedelta(hours=1)))
            db.session.commit()
        return alice.id, bob.id, sessions

    return build


def _bearer(issued):
    return {"Authorization": f"Bearer {issued.access_credential}"}


def _epoch(user_id):
    return db.session.query(User.credential_epoch).filter_by(
        id=user_id).scalar()


def _live_families(user_id):
    return MobileAuthSession.query.filter_by(
        user_id=user_id, revoked_at=None).count()


def _web_rows(user_id):
    return CognitoSession.query.filter_by(user_id=user_id).count()


def _native_reset(raw_client, identifier):
    return raw_client.post("/api/v1/auth/password/reset", json={
        "identifier": identifier, "code": CODE, "new_password": PASSWORD})


def _assert_only_alice_revoked(raw_client, cognito, alice_id, bob_id,
                               sessions):
    # Alice: every session issued under the old password is dead.
    assert raw_client.get(
        "/api/v1/account/me", headers=_bearer(sessions["alice"])).status_code == 401
    refreshed = raw_client.post("/api/v1/auth/refresh", json={
        "refresh_credential": sessions["alice"].refresh_credential})
    assert refreshed.get_json()["error"]["code"] == "AUTH_REFRESH_FAILED"
    assert _live_families(alice_id) == 0
    assert _web_rows(alice_id) == 0
    assert _epoch(alice_id) == 1
    assert sorted(cognito["revoked"]) == ["refresh|sub-alice", "web-refresh-alice"]
    # Bob: untouched.
    assert raw_client.get(
        "/api/v1/account/me", headers=_bearer(sessions["bob"])).status_code == 200
    assert _live_families(bob_id) == 1
    assert _web_rows(bob_id) == 1
    assert _epoch(bob_id) == 0
    # No session was issued by the reset.
    assert MobileAuthSession.query.count() == 2


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("identifier", ALICE_USERNAME_VARIANTS + ALICE_EMAIL_VARIANTS
                         + [" alice ", " ALICE@example.com "])
def test_every_provider_spelling_resolves_to_the_one_local_account(
        app, make_user, identifier):
    alice = make_user("Alice", email="alice@example.com")
    make_user("Bob", email="bob@example.com")
    assert cognito_identity.resolve_local_user(identifier).id == alice.id


@pytest.mark.parametrize("identifier", [
    "Alic", "lice", "Alice2", "Al_ce", "Al%", "%", "_____", "Alice.",
    "alice@example", "lice@example.com", "alice@example.co", "@example.com",
    "Bo", "example.com",
])
def test_resolution_is_exact_after_folding_never_partial(
        app, make_user, identifier):
    make_user("Alice", email="alice@example.com")
    make_user("Bob", email="bob@example.com")
    assert cognito_identity.resolve_local_user(identifier) is None


def test_an_identifier_matches_one_column_only(app, make_user):
    """A username is never compared with e-mails, nor an e-mail with usernames."""
    make_user("Alice", email="carol@example.com")
    make_user("carol", email="dave@example.com")
    assert cognito_identity.resolve_local_user("CAROL").username == "carol"
    assert cognito_identity.resolve_local_user(
        "Carol@Example.com").username == "Alice"


@pytest.mark.parametrize("identifier", ["bob", "BOB", "Bob@Example.com"])
def test_a_bystander_identifier_never_resolves_alice(app, make_user, identifier):
    make_user("Alice", email="alice@example.com")
    bob = make_user("Bob", email="bob@example.com")
    assert cognito_identity.resolve_local_user(identifier).id == bob.id


def test_case_ambiguous_usernames_fail_closed(app, make_user):
    """The schema's uniqueness is case-sensitive, so this state is storable."""
    make_user("Alice", email="alice@example.com")
    make_user("alice", email="other@example.com")
    with pytest.raises(AmbiguousLocalIdentity):
        cognito_identity.resolve_local_user("ALICE")
    assert len(cognito_identity.local_users_for_identifier("ALICE")) == 2
    # Each e-mail is still unique, so each account stays reachable by it.
    assert cognito_identity.resolve_local_user(
        "OTHER@example.com").username == "alice"


def test_case_ambiguous_emails_fail_closed(app, make_user):
    make_user("Alice", email="Alice@Example.com")
    make_user("Alicia", email="alice@example.com")
    with pytest.raises(AmbiguousLocalIdentity):
        cognito_identity.resolve_local_user("alice@example.com")


# ---------------------------------------------------------------------------
# Reset — end to end, native transport
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("stored", "submitted"), [
    ("Alice", "Alice"), ("Alice", "alice"), ("Alice", "ALICE"),
    ("Alice", "aLiCe"),
    ("alice", "Alice"), ("alice", "ALICE"),
    ("Alice", "alice@example.com"), ("Alice", "ALICE@EXAMPLE.COM"),
    ("Alice", "Alice@Example.Com"),
])
def test_native_reset_under_any_casing_revokes_exactly_the_canonical_account(
        raw_client, cognito, accounts, stored, submitted):
    alice_id, bob_id, sessions = accounts(stored)

    response = _native_reset(raw_client, submitted)

    assert response.status_code == 200
    assert "Set-Cookie" not in response.headers
    assert [sub for _, sub in cognito["confirmed"]] == ["sub-alice"]
    _assert_only_alice_revoked(raw_client, cognito, alice_id, bob_id, sessions)


def test_native_reset_for_the_bystander_never_touches_alice(
        raw_client, cognito, accounts):
    alice_id, bob_id, sessions = accounts("Alice")
    assert _native_reset(raw_client, "BOB").status_code == 200
    assert _epoch(bob_id) == 1 and _live_families(bob_id) == 0
    assert _epoch(alice_id) == 0 and _live_families(alice_id) == 1
    assert _web_rows(alice_id) == 1
    assert raw_client.get(
        "/api/v1/account/me", headers=_bearer(sessions["alice"])).status_code == 200


# ---------------------------------------------------------------------------
# Reset — web transport (same authority, reset context carries the identity)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("submitted", ["ALICE", "alice", "Alice@EXAMPLE.com"])
def test_web_reset_under_any_casing_revokes_exactly_the_canonical_account(
        client, raw_client, cognito, accounts, submitted):
    alice_id, bob_id, sessions = accounts("Alice")

    assert client.post(
        "/forgot-password", json={"identifier": submitted}).status_code == 200
    response = client.post("/reset-password", json={
        "code": CODE, "password": PASSWORD, "confirm_password": PASSWORD})

    assert response.status_code == 200
    assert [sub for _, sub in cognito["confirmed"]] == ["sub-alice"]
    _assert_only_alice_revoked(raw_client, cognito, alice_id, bob_id, sessions)


# ---------------------------------------------------------------------------
# Local ambiguity — the password must not change
# ---------------------------------------------------------------------------

@pytest.fixture
def ambiguous(make_user, cognito):
    """`Alice` is the provider user; `alice` is a local row the provider does
    not know (a legacy row, or a profile rename). Both are storable."""
    alice = make_user("Alice", email="alice@example.com", cognito_sub="sub-alice")
    shadow = make_user("alice", email="shadow@example.com",
                       cognito_sub="sub-shadow")
    cognito["users"]["sub-alice"] = ("Alice", "alice@example.com")
    return alice.id, shadow.id


@pytest.mark.parametrize("submitted", ["alice", "ALICE", "Alice"])
def test_ambiguous_reset_fails_closed_before_the_provider(
        app, cognito, ambiguous, submitted):
    alice_id, shadow_id = ambiguous
    with pytest.raises(RecoveryFailure) as caught:
        account_recovery.reset_password(submitted, CODE, PASSWORD)
    assert caught.value.outcome == Outcome.CODE_INVALID
    # The password did not change, so nothing is left unrevoked.
    assert cognito["confirmed"] == []
    assert (_epoch(alice_id), _epoch(shadow_id)) == (0, 0)


def test_ambiguous_reset_answers_exactly_like_a_wrong_code(
        raw_client, cognito, ambiguous, monkeypatch):
    ambiguous_response = _native_reset(raw_client, "ALICE")
    monkeypatch.setattr(
        cognito_service, "confirm_forgot_password",
        lambda *a: (_ for _ in ()).throw(
            CognitoServiceError("x", "CodeMismatchException")))
    wrong_code = _native_reset(raw_client, "Bob")

    def wire(response):
        body = response.get_json()
        body["error"]["request_id"] = "-"
        return response.status_code, body

    assert wire(ambiguous_response) == wire(wrong_code)
    assert ambiguous_response.get_json()["error"]["retryable"] is False


def test_ambiguous_web_reset_fails_closed_with_the_invalid_code_sentence(
        client, cognito, ambiguous):
    from app.i18n import t

    alice_id, shadow_id = ambiguous
    assert client.post(
        "/forgot-password", json={"identifier": "ALICE"}).status_code == 200
    response = client.post("/reset-password", json={
        "code": CODE, "password": PASSWORD, "confirm_password": PASSWORD})
    assert response.status_code == 400
    with client.application.test_request_context():
        assert response.get_json() == {"error": t("auth.reset_invalid_or_expired")}
    assert cognito["confirmed"] == []
    assert (_epoch(alice_id), _epoch(shadow_id)) == (0, 0)


def test_ambiguous_request_still_answers_like_any_other_account(
        raw_client, cognito, ambiguous):
    response = raw_client.post(
        "/api/v1/auth/password/forgot", json={"identifier": "ALICE"})
    assert response.status_code == 202
    assert response.get_json() == {"password_reset": {"status": "accepted"}}


def test_an_unambiguous_email_still_resets_an_account_with_a_case_twin(
        raw_client, cognito, ambiguous):
    alice_id, shadow_id = ambiguous
    assert _native_reset(raw_client, "Alice@Example.com").status_code == 200
    assert (_epoch(alice_id), _epoch(shadow_id)) == (1, 0)


# ---------------------------------------------------------------------------
# Credential fence — same rule as the reset
# ---------------------------------------------------------------------------

def _reset_lands_during_provider_call(monkeypatch, reset_identifier):
    """Drive a full reset from inside the login's provider round-trip."""
    real_authenticate = cognito_service.authenticate

    def authenticate(identifier, password):
        result = real_authenticate(identifier, password)
        account_recovery.reset_password(reset_identifier, CODE, PASSWORD)
        return result

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)


@pytest.mark.parametrize(("login_as", "reset_as"), [
    ("alice", "ALICE"), ("ALICE", "alice"), ("Alice", "alice@example.com"),
    ("alice@example.com", "Alice"), ("ALICE@example.com", "aLiCe"),
])
def test_a_login_racing_a_reset_under_another_casing_is_refused(
        app, make_user, cognito, monkeypatch, login_as, reset_as):
    alice = make_user("Alice", email="alice@example.com", cognito_sub="sub-alice")
    cognito["users"]["sub-alice"] = ("Alice", "alice@example.com")
    _reset_lands_during_provider_call(monkeypatch, reset_as)

    with pytest.raises(mobile_auth.MobileAuthFailure) as caught:
        mobile_auth.login(login_as, "Oldpass123", now=_now())

    assert caught.value.reason == "credential_changed_during_login"
    assert MobileAuthSession.query.count() == 0
    assert _epoch(alice.id) == 1


@pytest.mark.parametrize("login_as", [
    "alice", "ALICE", "alice@example.com", "Alice@Example.com"])
def test_a_login_after_a_completed_reset_is_issued_under_any_casing(
        raw_client, make_user, cognito, login_as):
    """The fence must follow the account, not the spelling: an exact-match
    fence read `{}` for `alice@example.com` and refused every later login."""
    alice = make_user("Alice", email="alice@example.com", cognito_sub="sub-alice")
    cognito["users"]["sub-alice"] = ("Alice", "alice@example.com")
    account_recovery.reset_password("ALICE", CODE, PASSWORD)
    assert _epoch(alice.id) == 1

    issued = mobile_auth.login(login_as, PASSWORD, now=_now())

    assert raw_client.get(
        "/api/v1/account/me", headers=_bearer(issued)).status_code == 200


def test_fence_over_ambiguous_rows_compares_the_row_the_provider_verified(
        app, cognito, ambiguous, monkeypatch):
    alice_id, shadow_id = ambiguous
    # A reset of the case twin must not refuse Alice's login ...
    real_authenticate = cognito_service.authenticate

    def authenticate(identifier, password):
        result = real_authenticate(identifier, password)
        mobile_auth.revoke_all_for_user(shadow_id)
        return result

    monkeypatch.setattr(cognito_service, "authenticate", authenticate)
    mobile_auth.login("alice", "Oldpass123", now=_now())
    assert _live_families(alice_id) == 1

    # ... and a reset of Alice herself must.
    _reset_lands_during_provider_call(monkeypatch, "alice@example.com")
    with pytest.raises(mobile_auth.MobileAuthFailure) as caught:
        mobile_auth.login("ALICE", "Oldpass123", now=_now())
    assert caught.value.reason == "credential_changed_during_login"


# ---------------------------------------------------------------------------
# Registration — the provider refuses a case twin of a provider account
# ---------------------------------------------------------------------------

def test_registration_cannot_create_a_case_twin_of_a_provider_account(
        app, make_user, cognito, monkeypatch):
    make_user("Alice", email="alice@example.com", cognito_sub="sub-alice")
    cognito["users"]["sub-alice"] = ("Alice", "alice@example.com")

    def sign_up(username, password, email, name):
        for existing, _ in cognito["users"].values():
            if existing.lower() == username.lower():
                raise CognitoServiceError("x", "UsernameExistsException")
        return f"sub-{username}"

    monkeypatch.setattr(cognito_service, "sign_up", sign_up)
    with pytest.raises(account_registration.RegistrationFailure) as caught:
        account_registration.register_account(
            "alice", "new@example.com", "Sifre123")
    assert caught.value.outcome == account_registration.Outcome.IDENTITY_UNAVAILABLE
    assert User.query.filter(
        db.func.lower(User.username) == "alice").count() == 1
