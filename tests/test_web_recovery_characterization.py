"""Web password-recovery characterization (LP-02).

Pins the externally visible browser contract of `POST /forgot-password` and
`POST /reset-password` BEFORE the canonical recovery logic moves out of
`app/blueprints/auth.py`. Written and run against the pre-extraction code
first; after the extraction the same assertions must hold unchanged. Exact
status codes, JSON keys, message texts and the browser reset context are
pinned, not the internal call graph.

Session revocation after a reset, the credential fence and the
password-changed e-mail are pinned separately by
`tests/test_password_recovery.py` and `tests/test_password_reset.py`, which
also stay unchanged in behaviour.

Deliberate LP-02 differences live in `tests/test_web_recovery_lp02_deltas.py`.

    python -m pytest tests/test_web_recovery_characterization.py -v
"""
import time

import pytest

from app.extensions import limiter
from app.i18n import t
from app.models import User
from app.services import cognito_service
from app.services.cognito_service import CognitoServiceError, _ERROR_MESSAGES


GENERIC_PROVIDER_MESSAGE = "İşlem başarısız. Lütfen tekrar dene."
RESET_KEYS = ("password_reset_username", "password_reset_started_at")


def _tr(key):
    return t(key, locale="tr")


def _provider_error(code):
    return CognitoServiceError(
        _ERROR_MESSAGES.get(code, GENERIC_PROVIDER_MESSAGE), code)


@pytest.fixture
def provider(monkeypatch):
    """Both recovery primitives recorded and individually breakable."""
    calls = {"forgot": [], "confirm": []}
    failures = {}

    def forgot_password(username):
        calls["forgot"].append(username)
        if "forgot" in failures:
            raise failures["forgot"]

    def confirm_forgot_password(username, code, new_password):
        calls["confirm"].append(
            {"username": username, "code": code, "password": new_password})
        if "confirm" in failures:
            raise failures["confirm"]

    monkeypatch.setattr(cognito_service, "forgot_password", forgot_password)
    monkeypatch.setattr(
        cognito_service, "confirm_forgot_password", confirm_forgot_password)
    return {"calls": calls, "failures": failures}


def _forgot(client, identifier="alice", **kwargs):
    return client.post("/forgot-password", json={"identifier": identifier},
                       **kwargs)


def _context(client, username="alice", *, age_seconds=0):
    with client.session_transaction() as state:
        state["password_reset_username"] = username
        state["password_reset_started_at"] = time.time() - age_seconds


def _reset(client, **overrides):
    body = {"code": "123456", "password": "Newpass123",
            "confirm_password": "Newpass123"}
    body.update(overrides)
    return client.post("/reset-password", json=body)


def _reset_state(client):
    with client.session_transaction() as state:
        return {key: state.get(key) for key in RESET_KEYS}


def _forgot_ok():
    return {"message": _tr("auth.forgot_generic"), "next": "/reset-password"}


# ---------------------------------------------------------------------------
# /forgot-password
# ---------------------------------------------------------------------------

def test_forgot_get_renders_the_page(client):
    response = client.get("/forgot-password")
    assert response.status_code == 200
    assert b"forgot-submit" in response.data


def test_forgot_known_email_resolves_to_username_exact_contract(
        client, provider, make_user):
    make_user("alice", email="alice@example.com")
    response = _forgot(client, " Alice@Example.COM ")
    assert response.status_code == 200
    assert response.get_json() == _forgot_ok()
    assert provider["calls"]["forgot"] == ["alice"]
    state = _reset_state(client)
    assert state["password_reset_username"] == "alice"
    assert abs(state["password_reset_started_at"] - time.time()) < 5


def test_forgot_known_username_is_sent_as_submitted(client, provider,
                                                    make_user):
    make_user("Alice", email="alice@example.com")
    response = _forgot(client, "  Alice ")
    assert response.status_code == 200
    assert response.get_json() == _forgot_ok()
    # Trimmed, never lower-cased: a username is not an e-mail address.
    assert provider["calls"]["forgot"] == ["Alice"]
    assert _reset_state(client)["password_reset_username"] == "Alice"


def test_forgot_unknown_email_is_sent_lowercased(client, provider):
    response = _forgot(client, "Nobody@Example.com")
    assert response.status_code == 200
    assert response.get_json() == _forgot_ok()
    assert provider["calls"]["forgot"] == ["nobody@example.com"]
    assert _reset_state(client)["password_reset_username"] == (
        "nobody@example.com")


def test_forgot_malformed_identifier_is_forwarded_not_validated(
        client, provider):
    response = _forgot(client, "@@not an email@@")
    assert response.status_code == 200
    assert response.get_json() == _forgot_ok()
    assert provider["calls"]["forgot"] == ["@@not an email@@"]


def test_forgot_known_and_unknown_answer_byte_identically(client, provider,
                                                          make_user):
    make_user("alice", email="alice@example.com")
    known = _forgot(client, "alice@example.com")
    unknown = _forgot(client, "ghost@example.com")
    assert known.status_code == unknown.status_code == 200
    assert known.data == unknown.data


@pytest.mark.parametrize("body", [
    {}, {"identifier": ""}, {"identifier": "   "}, {"identifier": None},
])
def test_forgot_missing_identifier(client, provider, body):
    response = client.post("/forgot-password", json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.identifier_required")}
    assert provider["calls"]["forgot"] == []
    assert _reset_state(client)["password_reset_username"] is None


def test_forgot_non_json_body_is_missing_identifier(client, provider):
    response = client.post("/forgot-password", data="identifier=alice",
                           content_type="application/x-www-form-urlencoded")
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.identifier_required")}
    assert provider["calls"]["forgot"] == []


@pytest.mark.parametrize("provider_code", [
    "UserNotFoundException", "InvalidParameterException",
    "NotAuthorizedException", "LimitExceededException",
    "TooManyRequestsException", "CodeDeliveryFailureException",
    "InternalErrorException", "",
])
def test_forgot_every_provider_failure_answers_the_generic_success(
        client, provider, provider_code):
    provider["failures"]["forgot"] = _provider_error(provider_code)
    response = _forgot(client, "alice")
    assert response.status_code == 200
    assert response.get_json() == _forgot_ok()
    # The reset context is still armed: the browser cannot tell either.
    assert _reset_state(client)["password_reset_username"] == "alice"


def test_forgot_repeat_requests_are_identical_and_rearm_context(
        client, provider):
    first = _forgot(client, "alice")
    second = _forgot(client, "alice")
    assert first.data == second.data
    assert provider["calls"]["forgot"] == ["alice", "alice"]
    assert _reset_state(client)["password_reset_username"] == "alice"


def test_forgot_per_ip_budget_is_five_per_fifteen_minutes(client, provider):
    limiter.enabled = True
    limiter.reset()
    try:
        statuses = [_forgot(client, f"user{i}").status_code for i in range(6)]
    finally:
        limiter.enabled = False
        limiter.reset()
    assert statuses == [200] * 5 + [429]
    assert len(provider["calls"]["forgot"]) == 5


def test_forgot_issues_no_login(client, provider, make_user):
    make_user("alice", email="alice@example.com")
    _forgot(client, "alice")
    with client.session_transaction() as state:
        assert "_user_id" not in state
        assert "cognito_sid" not in state


# ---------------------------------------------------------------------------
# /reset-password
# ---------------------------------------------------------------------------

def test_reset_get_without_context_redirects_to_forgot(client):
    response = client.get("/reset-password")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/forgot-password")


def test_reset_get_with_context_renders_the_page(client):
    _context(client)
    response = client.get("/reset-password")
    assert response.status_code == 200


def test_reset_success_exact_contract(client, provider, make_user):
    make_user("alice", email="alice@example.com")
    _context(client)
    response = _reset(client, code="  123456 ")
    assert response.status_code == 200
    assert response.get_json() == {
        "message": _tr("auth.reset_success"), "next": "/login"}
    # Code trimmed, password passed through exactly.
    assert provider["calls"]["confirm"] == [
        {"username": "alice", "code": "123456", "password": "Newpass123"}]
    assert _reset_state(client) == dict.fromkeys(RESET_KEYS)


def test_reset_success_without_a_local_row_still_succeeds(client, provider):
    _context(client, "nobody@example.com")
    response = _reset(client)
    assert response.status_code == 200
    assert provider["calls"]["confirm"][0]["username"] == "nobody@example.com"


def test_reset_success_issues_no_session(client, provider, make_user):
    user = make_user("alice", email="alice@example.com")
    _context(client)
    with client.session_transaction() as state:
        state["_user_id"] = str(user.id)
    assert _reset(client).status_code == 200
    with client.session_transaction() as state:
        assert "_user_id" not in state
        assert "cognito_sid" not in state


def test_reset_without_context_is_409(client, provider):
    response = _reset(client)
    assert response.status_code == 409
    assert response.get_json() == {"error": _tr("auth.reset_context_expired")}
    assert provider["calls"]["confirm"] == []


def test_reset_with_expired_context_is_409_and_clears_it(client, provider):
    _context(client, age_seconds=16 * 60)
    response = _reset(client)
    assert response.status_code == 409
    assert response.get_json() == {"error": _tr("auth.reset_context_expired")}
    assert _reset_state(client) == dict.fromkeys(RESET_KEYS)


@pytest.mark.parametrize("overrides", [
    {"code": ""}, {"code": "   "}, {"password": ""}, {"confirm_password": ""},
    {"code": None}, {"password": None},
])
def test_reset_missing_fields(client, provider, overrides):
    _context(client)
    response = _reset(client, **overrides)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.reset_fields_required")}
    assert provider["calls"]["confirm"] == []


def test_reset_non_json_body_is_missing_fields(client, provider):
    _context(client)
    response = client.post("/reset-password", data="code=1",
                           content_type="application/x-www-form-urlencoded")
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.reset_fields_required")}


def test_reset_password_mismatch(client, provider):
    _context(client)
    response = _reset(client, confirm_password="Different123")
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.password_mismatch")}
    assert provider["calls"]["confirm"] == []


@pytest.mark.parametrize(("password", "key"), [
    ("short1", "validate.password_min"),
    ("x" * 120 + "12345678a", "validate.password_max"),
    ("12345678", "validate.password_letter"),
    ("abcdefgh", "validate.password_digit"),
])
def test_reset_weak_password_renders_the_canonical_validator_sentence(
        client, provider, password, key):
    _context(client)
    response = _reset(client, password=password, confirm_password=password)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr(key)}
    assert provider["calls"]["confirm"] == []


@pytest.mark.parametrize(("provider_code", "status", "key"), [
    ("CodeMismatchException", 400, "auth.reset_invalid_or_expired"),
    ("ExpiredCodeException", 400, "auth.reset_invalid_or_expired"),
    ("UserNotFoundException", 400, "auth.reset_invalid_or_expired"),
    ("NotAuthorizedException", 400, "auth.reset_invalid_or_expired"),
    ("UserNotConfirmedException", 400, "auth.reset_invalid_or_expired"),
    ("InvalidParameterException", 400, "auth.reset_invalid_or_expired"),
    ("InternalErrorException", 400, "auth.reset_invalid_or_expired"),
    ("", 400, "auth.reset_invalid_or_expired"),
    ("InvalidPasswordException", 400, "auth.reset_password_rejected"),
    ("LimitExceededException", 429, "auth.reset_throttled"),
    ("TooManyRequestsException", 429, "auth.reset_throttled"),
    ("TooManyFailedAttemptsException", 400, "auth.reset_invalid_or_expired"),
])
def test_reset_provider_failures_render_fixed_sentences(
        client, provider, provider_code, status, key):
    _context(client)
    provider["failures"]["confirm"] = _provider_error(provider_code)
    response = _reset(client)
    assert response.status_code == status
    assert response.get_json() == {"error": _tr(key)}
    # A failed attempt keeps the context so the user can retry the code.
    assert _reset_state(client)["password_reset_username"] == "alice"


def test_reset_retry_after_success_is_409(client, provider, make_user):
    make_user("alice", email="alice@example.com")
    _context(client)
    assert _reset(client).status_code == 200
    again = _reset(client)
    assert again.status_code == 409
    assert len(provider["calls"]["confirm"]) == 1


def test_reset_per_ip_budget_is_ten_per_fifteen_minutes(client, provider):
    provider["failures"]["confirm"] = _provider_error("CodeMismatchException")
    limiter.enabled = True
    limiter.reset()
    try:
        statuses = []
        for _ in range(11):
            _context(client)
            statuses.append(_reset(client).status_code)
    finally:
        limiter.enabled = False
        limiter.reset()
    assert statuses == [400] * 10 + [429]
    assert len(provider["calls"]["confirm"]) == 10


def test_known_user_row_is_unchanged_by_forgot_and_failed_reset(
        client, provider, make_user):
    user = make_user("alice", email="alice@example.com")
    before = (user.username, user.email, user.credential_epoch)
    _forgot(client, "alice")
    provider["failures"]["confirm"] = _provider_error("CodeMismatchException")
    _reset(client)
    refreshed = User.query.filter_by(username="alice").one()
    assert (refreshed.username, refreshed.email,
            refreshed.credential_epoch) == before
