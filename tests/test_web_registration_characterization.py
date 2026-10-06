"""Web registration / verification characterization (LP-01).

Pins the externally visible browser contract of `POST /register`,
`POST /verify` and `POST /verify/resend` BEFORE the canonical registration
logic moves out of `app/blueprints/auth.py`. Written and run against the
pre-extraction code first; after the extraction the same assertions must hold
unchanged. Exact status codes, JSON keys and message texts are pinned, not the
internal call graph.

    python -m pytest tests/test_web_registration_characterization.py -v
"""
import pytest

from app.blueprints import auth as auth_bp
from app.extensions import db, limiter
from app.i18n import t
from app.models import User
from app.services import cognito_service
from app.services.cognito_service import CognitoServiceError, _ERROR_MESSAGES


GENERIC_PROVIDER_MESSAGE = "İşlem başarısız. Lütfen tekrar dene."


def _tr(key):
    return t(key, locale="tr")


def _provider_error(code):
    return CognitoServiceError(
        _ERROR_MESSAGES.get(code, GENERIC_PROVIDER_MESSAGE), code)


@pytest.fixture
def provider(monkeypatch):
    """Cognito on, every provider primitive recorded and individually breakable."""
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)
    calls = {"sign_up": [], "confirm": [], "resend": []}
    failures = {}

    def sign_up(username, password, email, name, language=None):
        calls["sign_up"].append(
            {"username": username, "email": email, "name": name})
        if "sign_up" in failures:
            raise failures["sign_up"]
        return f"sub-{username}"

    def confirm_sign_up(username, code):
        calls["confirm"].append({"username": username, "code": code})
        if "confirm" in failures:
            raise failures["confirm"]

    def resend_code(username):
        calls["resend"].append({"username": username})
        if "resend" in failures:
            raise failures["resend"]

    monkeypatch.setattr(cognito_service, "sign_up", sign_up)
    monkeypatch.setattr(cognito_service, "confirm_sign_up", confirm_sign_up)
    monkeypatch.setattr(cognito_service, "resend_code", resend_code)
    return {"calls": calls, "failures": failures}


def _register(client, **overrides):
    body = {"username": "charuser", "email": "charuser@example.com",
            "password": "Sifre123"}
    body.update(overrides)
    return client.post("/register", json=body)


# ---------------------------------------------------------------------------
# /register
# ---------------------------------------------------------------------------

def test_register_success_exact_contract(client, provider):
    response = _register(client)
    assert response.status_code == 200
    assert response.get_json() == {
        "message": _tr("auth.register_verify_sent"),
        "needs_verification": True,
        "username": "charuser",
        "referred": False,
    }
    assert provider["calls"]["sign_up"] == [{
        "username": "charuser", "email": "charuser@example.com",
        "name": "charuser"}]
    user = User.query.filter_by(username="charuser").one()
    assert user.cognito_sub == "sub-charuser"
    assert user.full_name == "charuser"
    assert user.language == "tr"
    assert user.password_hash is None
    assert user.referral_code
    with client.session_transaction() as session:
        assert session["lang"] == "tr"


def test_register_normalizes_email_before_provider_and_storage(client, provider):
    response = _register(client, email="  Char.User@EXAMPLE.com ")
    assert response.status_code == 200
    assert provider["calls"]["sign_up"][0]["email"] == "char.user@example.com"
    assert User.query.filter_by(
        username="charuser").one().email == "char.user@example.com"


def test_register_language_body_then_session_then_tr(app, provider):
    # Body value wins; an invalid body value falls back to the browser
    # session's language (which a previous registration set), else to "tr".
    client = app.test_client()
    assert _register(client, language="en").status_code == 200
    assert User.query.filter_by(username="charuser").one().language == "en"
    assert _register(
        client, username="otheruser", email="other@example.com",
        language="xx").status_code == 200
    assert User.query.filter_by(username="otheruser").one().language == "en"

    fresh = app.test_client()
    assert _register(
        fresh, username="thirduser", email="third@example.com",
        language="xx").status_code == 200
    assert User.query.filter_by(username="thirduser").one().language == "tr"


def test_register_records_pending_referral_and_clears_ref_cookie(
        client, provider):
    client.set_cookie("fitx_ref", "COOKIECODE")
    response = _register(client)
    assert response.status_code == 200
    assert response.get_json()["referred"] is False
    user = User.query.filter_by(username="charuser").one()
    assert user.user_metadata == {"pending_referral_code": "COOKIECODE"}
    assert "fitx_ref=;" in response.headers.get("Set-Cookie", "")


@pytest.mark.parametrize("body", [
    {},
    {"username": "charuser"},
    {"username": "charuser", "email": "charuser@example.com"},
    {"username": "", "email": "charuser@example.com", "password": "Sifre123"},
    {"username": "charuser", "email": "   ", "password": "Sifre123"},
])
def test_register_missing_fields(client, provider, body):
    response = client.post("/register", json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.all_fields_required")}
    assert provider["calls"]["sign_up"] == []


def test_register_non_json_body_is_missing_fields(client, provider):
    response = client.post("/register", data="username=x",
                           content_type="application/x-www-form-urlencoded")
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.all_fields_required")}


@pytest.mark.parametrize(("password", "key"), [
    ("kisa1", "validate.password_min"),
    ("a1" * 65, "validate.password_max"),
    ("12345678", "validate.password_letter"),
    ("abcdefgh", "validate.password_digit"),
])
def test_register_password_policy_messages(client, provider, password, key):
    response = _register(client, password=password)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr(key)}
    assert provider["calls"]["sign_up"] == []


@pytest.mark.parametrize(("username", "key"), [
    ("ab", "validate.username_min"),
    ("a" * 81, "validate.username_max"),
    ("bad name", "validate.username_charset"),
])
def test_register_username_messages(client, provider, username, key):
    response = _register(client, username=username)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr(key)}


@pytest.mark.parametrize("email", [
    "not-an-email", "a@b", "x" * 120 + "@example.com"])
def test_register_invalid_email_message(client, provider, email):
    response = _register(client, email=email)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("validate.email_invalid")}


def test_register_password_is_validated_before_username_and_email(
        client, provider):
    response = _register(client, username="ab", email="bad", password="kisa1")
    assert response.get_json() == {"error": _tr("validate.password_min")}


def test_register_local_collisions_share_one_message(
        client, provider, make_user):
    make_user("existing", email="existing@example.com")
    by_username = _register(client, username="existing")
    by_email = _register(client, email="EXISTING@example.com")
    for response in (by_username, by_email):
        assert response.status_code == 400
        assert response.get_json() == {"error": _tr("auth.user_or_email_taken")}
    assert provider["calls"]["sign_up"] == []


@pytest.mark.parametrize("code", [
    "UsernameExistsException", "InvalidPasswordException",
    "InvalidParameterException", "LimitExceededException",
    "TooManyRequestsException", "InternalErrorException",
    "CodeDeliveryFailureException", ""])
def test_register_provider_rejection_surfaces_mapped_message(
        client, provider, code):
    provider["failures"]["sign_up"] = _provider_error(code)
    response = _register(client)
    assert response.status_code == 400
    assert response.get_json() == {
        "error": _ERROR_MESSAGES.get(code, GENERIC_PROVIDER_MESSAGE)}
    assert User.query.filter_by(username="charuser").first() is None


def test_register_local_commit_race_is_409(client, provider, monkeypatch):
    def racing(username, password, email, name, language=None):
        db.session.add(User(username="racer", email=email, password_hash="x"))
        db.session.commit()
        return f"sub-{username}"

    monkeypatch.setattr(cognito_service, "sign_up", racing)
    response = _register(client)
    assert response.status_code == 409
    assert response.get_json() == {"error": _tr("auth.user_or_email_taken")}
    assert User.query.filter_by(username="charuser").first() is None


def test_register_local_commit_failure_is_503(client, provider, monkeypatch):
    real_commit = db.session.commit
    state = {"armed": False}

    def sign_up(username, password, email, name, language=None):
        state["armed"] = True
        return f"sub-{username}"

    def commit():
        if state["armed"]:
            state["armed"] = False
            raise RuntimeError("storage down")
        return real_commit()

    monkeypatch.setattr(cognito_service, "sign_up", sign_up)
    monkeypatch.setattr(db.session, "commit", commit)
    response = _register(client)
    assert response.status_code == 503
    assert response.get_json() == {"error": _tr("auth.register_failed")}


def test_register_is_503_when_cognito_disabled(client, provider, monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", False)
    response = _register(client)
    assert response.status_code == 503
    assert response.get_json() == {"error": _tr("auth.login_unavailable")}
    assert provider["calls"]["sign_up"] == []


# ---------------------------------------------------------------------------
# /verify
# ---------------------------------------------------------------------------

def test_verify_success_exact_contract_and_stripping(client, provider):
    response = client.post(
        "/verify", json={"username": "  charuser ", "code": " 123456 "})
    assert response.status_code == 200
    assert response.get_json() == {
        "message": _tr("auth.verify_done"), "referred": False}
    assert provider["calls"]["confirm"] == [
        {"username": "charuser", "code": "123456"}]


@pytest.mark.parametrize("body", [
    {}, {"username": "charuser"}, {"code": "123456"},
    {"username": "  ", "code": "123456"}, {"username": "charuser", "code": " "},
])
def test_verify_missing_fields(client, provider, body):
    response = client.post("/verify", json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.verify_fields_required")}
    assert provider["calls"]["confirm"] == []


@pytest.mark.parametrize("code", [
    "CodeMismatchException", "ExpiredCodeException", "UserNotFoundException",
    "NotAuthorizedException", "LimitExceededException",
    "TooManyRequestsException", "AliasExistsException", ""])
def test_verify_provider_rejection_surfaces_mapped_message(
        client, provider, code):
    provider["failures"]["confirm"] = _provider_error(code)
    response = client.post(
        "/verify", json={"username": "charuser", "code": "000000"})
    assert response.status_code == 400
    assert response.get_json() == {
        "error": _ERROR_MESSAGES.get(code, GENERIC_PROVIDER_MESSAGE)}


def test_verify_consumes_pending_referral_once(client, provider, make_user):
    from app.services.referral import ensure_referral_code

    referrer = make_user("charref")
    ensure_referral_code(referrer)
    db.session.commit()
    code = referrer.referral_code
    assert _register(client, ref=code).status_code == 200

    first = client.post(
        "/verify", json={"username": "charuser", "code": "123456"})
    second = client.post(
        "/verify", json={"username": "charuser", "code": "123456"})
    assert first.get_json()["referred"] is True
    assert second.status_code == 200
    assert second.get_json()["referred"] is False


def test_verify_is_404_when_cognito_disabled(client, provider, monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", False)
    response = client.post(
        "/verify", json={"username": "charuser", "code": "123456"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# /verify/resend
# ---------------------------------------------------------------------------

def test_resend_success_exact_contract_and_repeat(client, provider):
    for _ in range(2):
        response = client.post(
            "/verify/resend", json={"username": " charuser "})
        assert response.status_code == 200
        assert response.get_json() == {"message": _tr("auth.resend_done")}
    assert provider["calls"]["resend"] == [
        {"username": "charuser"}, {"username": "charuser"}]


@pytest.mark.parametrize("body", [{}, {"username": ""}, {"username": "   "}])
def test_resend_missing_username(client, provider, body):
    response = client.post("/verify/resend", json=body)
    assert response.status_code == 400
    assert response.get_json() == {"error": _tr("auth.username_required")}
    assert provider["calls"]["resend"] == []


@pytest.mark.parametrize("code", [
    "UserNotFoundException", "InvalidParameterException",
    "LimitExceededException", "CodeDeliveryFailureException", ""])
def test_resend_provider_rejection_surfaces_mapped_message(
        client, provider, code):
    provider["failures"]["resend"] = _provider_error(code)
    response = client.post("/verify/resend", json={"username": "charuser"})
    assert response.status_code == 400
    assert response.get_json() == {
        "error": _ERROR_MESSAGES.get(code, GENERIC_PROVIDER_MESSAGE)}


def test_resend_is_404_when_cognito_disabled(client, provider, monkeypatch):
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", False)
    response = client.post("/verify/resend", json={"username": "charuser"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Per-IP budgets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("path", "body", "budget"), [
    ("/register", {"username": "x"}, 5),
    ("/verify", {"username": "charuser"}, 10),
    ("/verify/resend", {}, 3),
])
def test_web_per_ip_budgets(client, provider, path, body, budget):
    limiter.reset()
    limiter.enabled = True
    try:
        for _ in range(budget):
            assert client.post(path, json=body).status_code == 400
        assert client.post(path, json=body).status_code == 429
    finally:
        limiter.enabled = False
        limiter.reset()
