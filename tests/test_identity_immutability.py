"""`User.username` is the immutable provider identity (LP-02 closure).

The Cognito username cannot change, and recovery maps a submitted username —
or an e-mail, through its owner's username — back to the local row by it. A
local rename therefore made a reset miss the account whose password it
changed (sessions stayed valid) or land on another account. Chosen closure
(docs/MOBILE_PASSWORD_RECOVERY.md §11): the local username IS the provider
username, written once at account creation and never again; the display name
is `full_name`.

Every test below drives the real routes, recovery, revocation and fence code
against the case-insensitive provider stub of test_recovery_identity_case.

    python -m pytest tests/test_identity_immutability.py -v
"""
import ast
import pathlib

import pytest

from app.blueprints import auth as auth_bp
from app.extensions import db
from app.models import CognitoSession, MobileAuthSession, User
from app.services import (
    account_recovery, account_registration, cognito_jwt, cognito_service,
    email_service, mobile_auth,
)
from app.services.cognito_service import CognitoServiceError
from test_recovery_identity_case import (  # noqa: F401  (fixtures)
    CODE, PASSWORD, _bearer, _epoch, _live_families, _native_reset, _now,
    _web_rows, accounts, cognito,
)


APP_ROOT = pathlib.Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def provider(cognito, monkeypatch):
    """The provider stub, plus what a real Cognito ID token also carries:
    `cognito:username` (overridable per sub via `claim`), and a mailbox."""
    inner = cognito_jwt.validate_token
    cognito["claim"] = {}

    def validate(token, expected_use, leeway_seconds=0):
        claims = inner(token, expected_use, leeway_seconds)
        if expected_use == "id":
            sub = claims["sub"]
            value = cognito["claim"].get(sub, cognito["users"][sub][0])
            if value is not None:
                claims["cognito:username"] = value
        return claims

    cognito["mail"] = []
    monkeypatch.setattr(cognito_jwt, "validate_token", validate)
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    monkeypatch.setattr(email_service, "send_html_email",
                        lambda to, *a, **k: cognito["mail"].append(to))
    return cognito


def _web_login(client, identifier):
    response = client.post("/login", json={
        "username": identifier, "password": "Oldpass123"})
    assert response.status_code == 200, response.get_json()
    return response


def _user(user_id):
    db.session.expire_all()
    return db.session.get(User, user_id)


def _assert_only_alice_revoked(raw_client, provider, alice_id, bob_id,
                               sessions):
    """The account the provider changed lost every session; nobody else did."""
    assert _epoch(alice_id) == 1
    assert _live_families(alice_id) == 0
    assert _web_rows(alice_id) == 0
    assert raw_client.get(
        "/api/v1/account/me", headers=_bearer(sessions["alice"])).status_code == 401
    refreshed = raw_client.post("/api/v1/auth/refresh", json={
        "refresh_credential": sessions["alice"].refresh_credential})
    assert refreshed.get_json()["error"]["code"] == "AUTH_REFRESH_FAILED"
    assert provider["revoked"]
    assert all("alice" in token for token in provider["revoked"])
    assert provider["mail"] == ["alice@example.com"]

    assert _epoch(bob_id) == 0
    assert _live_families(bob_id) == 1
    assert _web_rows(bob_id) == 1
    assert raw_client.get(
        "/api/v1/account/me", headers=_bearer(sessions["bob"])).status_code == 200
    assert _user(bob_id).username == "Bob"


# ---------------------------------------------------------------------------
# Normal account
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("submitted", ["Alice", "alice", "alice@example.com"])
def test_normal_recovery_revokes_exactly_the_account_it_changed(
        raw_client, provider, accounts, submitted):
    alice_id, bob_id, sessions = accounts("Alice")

    assert _native_reset(raw_client, submitted).status_code == 200

    assert [sub for _, sub in provider["confirmed"]] == ["sub-alice"]
    _assert_only_alice_revoked(raw_client, provider, alice_id, bob_id, sessions)


# ---------------------------------------------------------------------------
# Renamed account — the rename is refused, so recovery keeps finding it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("new_name", ["Alicia", "alice", "ALICE", "Bob", "bob"])
def test_rename_attempt_is_refused_and_recovery_still_hits_the_account(
        client, raw_client, provider, accounts, new_name):
    alice_id, bob_id, sessions = accounts("Alice")
    _web_login(client, "Alice")

    response = client.post("/edit-profile", json={
        "username": new_name, "full_name": "Changed", "goal": ""})

    assert response.status_code == 400
    alice = _user(alice_id)
    assert (alice.username, alice.full_name) == ("Alice", None)

    # The provider still knows only `Alice`; the local row still answers to it.
    web_rows_before_reset = _web_rows(alice_id)
    assert web_rows_before_reset == 2
    assert _native_reset(raw_client, "Alice").status_code == 200
    _assert_only_alice_revoked(raw_client, provider, alice_id, bob_id, sessions)


def test_a_reset_by_the_refused_new_name_changes_nobody(
        client, raw_client, provider, accounts):
    alice_id, bob_id, _ = accounts("Alice")
    _web_login(client, "Alice")
    client.post("/edit-profile", json={"username": "Alicia", "goal": ""})

    response = _native_reset(raw_client, "Alicia")

    # Answered like any account (no oracle); the provider knows no `Alicia`.
    assert response.status_code == 400
    assert provider["confirmed"] == []
    assert (_epoch(alice_id), _epoch(bob_id)) == (0, 0)
    assert provider["mail"] == []


# ---------------------------------------------------------------------------
# Old-name reuse — another account can never come to answer to `Alice`
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("taken", ["Alice", "alice", "ALICE"])
def test_another_account_cannot_take_the_name_by_edit_profile(
        client, raw_client, provider, accounts, taken):
    alice_id, bob_id, sessions = accounts("Alice")
    _web_login(client, "Bob")

    refused = client.post("/edit-profile", json={"username": taken, "goal": ""})
    # The display name is free text and may read "Alice" — it is not identity.
    allowed = client.post("/edit-profile", json={
        "username": "Bob", "full_name": "Alice", "goal": ""})

    assert refused.status_code == 400
    assert allowed.status_code == 200
    assert (_user(bob_id).username, _user(bob_id).full_name) == ("Bob", "Alice")

    assert _native_reset(raw_client, taken).status_code == 200
    assert [sub for _, sub in provider["confirmed"]] == ["sub-alice"]
    # Bob's own browser session from the edit is his second web row.
    assert _epoch(bob_id) == 0
    assert _live_families(bob_id) == 1
    assert _web_rows(bob_id) == 2
    assert _epoch(alice_id) == 1 and _live_families(alice_id) == 0
    assert _web_rows(alice_id) == 0
    assert provider["mail"] == ["alice@example.com"]
    assert all("alice" in token for token in provider["revoked"])


@pytest.mark.parametrize("taken", ["Alice", "alice", "ALICE"])
def test_registration_cannot_take_the_name_in_any_casing(
        app, make_user, cognito, monkeypatch, taken):
    make_user("Alice", email="alice@example.com", cognito_sub="sub-alice")
    signed_up = []
    monkeypatch.setattr(cognito_service, "sign_up",
                        lambda **kw: signed_up.append(kw) or "sub-new")

    with pytest.raises(account_registration.RegistrationFailure) as caught:
        account_registration.register_account(taken, "new@example.com", "Sifre123")

    assert caught.value.outcome == account_registration.Outcome.IDENTITY_UNAVAILABLE
    # Refused locally, before the provider: no provider user, no local twin.
    assert signed_up == []
    assert User.query.filter(db.func.lower(User.username) == "alice").count() == 1


def test_a_local_row_without_a_provider_user_still_blocks_its_case_twin(
        app, make_user, monkeypatch):
    """The provider would accept `Alice` here (it has no such user); the local
    check must not, or `alice`/`Alice` both become unrecoverable by username."""
    make_user("alice", email="legacy@example.com", cognito_sub=None)
    monkeypatch.setattr(cognito_service, "sign_up", lambda **kw: "sub-new")

    with pytest.raises(account_registration.RegistrationFailure):
        account_registration.register_account("Alice", "new@example.com", "Sifre123")
    assert User.query.count() == 1


# ---------------------------------------------------------------------------
# E-mail recovery after the edits that ARE allowed
# ---------------------------------------------------------------------------

def test_email_recovery_after_permitted_profile_edits(
        client, raw_client, provider, accounts):
    alice_id, bob_id, sessions = accounts("Alice")
    _web_login(client, "alice@example.com")

    response = client.post("/edit-profile", json={
        "username": "Alice", "full_name": "Alice Smith",
        "goal": "kas kazanma", "target_weight": 60})
    assert response.status_code == 200
    alice = _user(alice_id)
    assert (alice.username, alice.email, alice.full_name, alice.goal,
            alice.target_weight) == (
        "Alice", "alice@example.com", "Alice Smith", "kas kazanma", 60.0)

    assert _native_reset(raw_client, "ALICE@Example.com").status_code == 200

    # The e-mail maps to the owner's username, which is the provider's.
    assert provider["confirmed"] == [("Alice", "sub-alice")]
    _assert_only_alice_revoked(raw_client, provider, alice_id, bob_id, sessions)


# ---------------------------------------------------------------------------
# Account creation at login writes the provider username, not the input
# ---------------------------------------------------------------------------

def _carol(provider):
    provider["users"]["sub-carol"] = ("Carol", "carol@example.com")


@pytest.mark.parametrize("submitted", [
    "carol@example.com", "CAROL@EXAMPLE.COM", "carol", "CAROL"])
@pytest.mark.parametrize("transport", ["native", "web"])
def test_orphan_login_records_the_provider_username(
        client, raw_client, provider, transport, submitted):
    _carol(provider)

    if transport == "native":
        mobile_auth.login(submitted, "Oldpass123", now=_now())
    else:
        _web_login(client, submitted)

    carol = User.query.filter_by(cognito_sub="sub-carol").one()
    assert (carol.username, carol.email) == ("Carol", "carol@example.com")

    # So recovery by the provider username finds and revokes it.
    assert _native_reset(raw_client, "carol").status_code == 200
    assert _epoch(carol.id) == 1
    assert _live_families(carol.id) == 0 and _web_rows(carol.id) == 0


@pytest.mark.parametrize("claim", [None, "", "   ", "carol@example.com"])
@pytest.mark.parametrize("transport", ["native", "web"])
def test_orphan_login_without_a_usable_provider_username_creates_nothing(
        client, provider, transport, claim):
    _carol(provider)
    provider["claim"]["sub-carol"] = claim

    if transport == "native":
        with pytest.raises(mobile_auth.MobileAuthFailure) as caught:
            mobile_auth.login("carol@example.com", "Oldpass123", now=_now())
        assert caught.value.code == "AUTH_INVALID_CREDENTIALS"
    else:
        response = client.post("/login", json={
            "username": "carol@example.com", "password": "Oldpass123"})
        assert response.status_code == 401

    assert User.query.count() == 0
    assert MobileAuthSession.query.count() == 0
    assert CognitoSession.query.count() == 0


@pytest.mark.parametrize("transport", ["native", "web"])
def test_orphan_login_never_binds_or_twins_a_case_variant_row(
        client, make_user, provider, transport):
    """A local `carol` with another e-mail is not provider `Carol`'s row:
    binding it would hand over that account, and creating `Carol` beside it
    would make both unrecoverable by username."""
    other = make_user("carol", email="other@example.com", cognito_sub=None)
    _carol(provider)

    if transport == "native":
        with pytest.raises(mobile_auth.MobileAuthFailure):
            mobile_auth.login("carol@example.com", "Oldpass123", now=_now())
    else:
        assert client.post("/login", json={
            "username": "carol@example.com",
            "password": "Oldpass123"}).status_code == 401

    assert User.query.count() == 1
    assert _user(other.id).cognito_sub is None


@pytest.mark.parametrize("emails", [
    ("carol@example.com", "CAROL@example.com"),
    # Unrelated e-mails: nothing but the refusal stops a third row `Carol`
    # (the e-mail index would not catch it).
    ("one@example.com", "two@example.com"),
])
@pytest.mark.parametrize("transport", ["native", "web"])
def test_orphan_login_refuses_case_ambiguous_rows_instead_of_picking(
        client, make_user, provider, transport, emails):
    make_user("carol", email=emails[0], cognito_sub=None)
    make_user("CAROL", email=emails[1], cognito_sub=None)
    _carol(provider)

    if transport == "native":
        with pytest.raises(mobile_auth.MobileAuthFailure):
            mobile_auth.login("carol", "Oldpass123", now=_now())
    else:
        assert client.post("/login", json={
            "username": "carol", "password": "Oldpass123"}).status_code == 401

    assert User.query.count() == 2
    assert User.query.filter(User.cognito_sub.isnot(None)).count() == 0


def test_orphan_login_binds_the_case_variant_row_of_the_same_account(
        make_user, provider):
    """Same account, stored in another casing (username AND e-mail agree):
    bound, and the stored username — already the provider's after folding —
    is left as it is."""
    carol = make_user("carol", email="Carol@Example.com", cognito_sub=None)
    _carol(provider)

    mobile_auth.login("carol@example.com", "Oldpass123", now=_now())

    assert User.query.count() == 1
    assert (_user(carol.id).cognito_sub, _user(carol.id).username) == (
        "sub-carol", "carol")


# ---------------------------------------------------------------------------
# Rows renamed before this closure are surfaced, never rewritten
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("transport", ["native", "web"])
def test_a_login_reports_a_row_that_diverges_from_the_provider(
        client, make_user, provider, caplog, transport):
    legacy = make_user("Renamed", email="alice@example.com",
                       cognito_sub="sub-alice")
    provider["users"]["sub-alice"] = ("Alice", "alice@example.com")

    if transport == "native":
        mobile_auth.login("alice@example.com", "Oldpass123", now=_now())
        assert "event=identity_divergent" in caplog.text
    else:
        _web_login(client, "alice@example.com")
        assert f"identity_divergent user={legacy.id}" in caplog.text

    # Reported for the operator repair (§11); login never rewrites identity.
    assert _user(legacy.id).username == "Renamed"


@pytest.mark.parametrize("transport", ["native", "web"])
def test_a_login_of_a_consistent_row_reports_nothing(
        client, make_user, provider, caplog, transport):
    make_user("alice", email="alice@example.com", cognito_sub="sub-alice")
    provider["users"]["sub-alice"] = ("Alice", "alice@example.com")

    if transport == "native":
        mobile_auth.login("ALICE", "Oldpass123", now=_now())
    else:
        _web_login(client, "ALICE")

    assert "identity_divergent" not in caplog.text


# ---------------------------------------------------------------------------
# Gate — nothing but account creation writes `User.username`
# ---------------------------------------------------------------------------

# The only modules allowed to construct a User with a username: registration
# and the two login-time orphan reconciliations (which use the verified
# `cognito:username`, pinned above).
USER_CREATORS = {
    "app/services/account_registration.py",
    "app/blueprints/auth.py",
    "app/services/mobile_auth.py",
}


def _username_writes(tree):
    """Every AST node in `tree` that could store a username on a row."""
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            for part in ast.walk(target):
                if isinstance(part, ast.Attribute) and part.attr == "username":
                    yield node, "attribute assignment"
        if not isinstance(node, ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None))
        if (name == "setattr" and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "username"):
            yield node, "setattr"
        if name in ("update", "values") and (
                any(k.arg == "username" for k in node.keywords)
                or any(isinstance(a, ast.Dict) and any(
                    (isinstance(key, ast.Constant) and key.value == "username")
                    or (isinstance(key, ast.Attribute) and key.attr == "username")
                    for key in a.keys) for a in node.args)):
            yield node, f"bulk {name}()"
        if name == "User" and any(k.arg == "username" for k in node.keywords):
            yield node, "User(username=...)"


def test_no_module_writes_a_username_after_account_creation():
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        relative = path.relative_to(APP_ROOT.parent).as_posix()
        for node, kind in _username_writes(ast.parse(path.read_text())):
            if kind == "User(username=...)" and relative in USER_CREATORS:
                continue
            offenders.append(f"{relative}:{node.lineno} {kind}")
    assert offenders == []


@pytest.mark.parametrize("source", [
    "user.username = x",
    "current_user.username = x.strip()",
    "a.username += 's'",
    "setattr(user, 'username', x)",
    "User.query.filter_by(id=1).update({'username': x})",
    "User.query.filter_by(id=1).update({User.username: x})",
    "db.session.execute(update(User).values(username=x))",
    "User(username=x, email=e)",
])
def test_the_gate_sees_every_writer_shape(source):
    assert list(_username_writes(ast.parse(source)))


def test_each_allowed_creator_still_creates_users():
    """Keeps USER_CREATORS honest: a module that stops creating users leaves."""
    for relative in USER_CREATORS:
        tree = ast.parse((APP_ROOT.parent / relative).read_text())
        kinds = [kind for _, kind in _username_writes(tree)]
        assert kinds == ["User(username=...)"], relative
