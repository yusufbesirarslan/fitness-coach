"""cognito_service token operasyonları — authenticate/refresh/global_sign_out.
boto3 istemcisi sahte; ağ yok.

    python -m pytest tests/test_cognito_service_tokens.py -v
"""
import base64
import json
import pytest

from app.services import cognito_service
from app.services.cognito_service import CognitoServiceError


class _FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeIdp:
    def __init__(self, *, raises=None, result=None):
        self.calls = []
        self.raises = raises
        self.result = result or {}

    def initiate_auth(self, **kw):
        self.calls.append(("initiate_auth", kw))
        if self.raises:
            raise self.raises
        return self.result

    def global_sign_out(self, **kw):
        self.calls.append(("global_sign_out", kw))
        if self.raises:
            raise self.raises
        return {}

    def revoke_token(self, **kw):
        self.calls.append(("revoke_token", kw))
        if self.raises:
            raise self.raises
        return {}

    def forgot_password(self, **kw):
        self.calls.append(("forgot_password", kw))
        if self.raises:
            raise self.raises
        return {}

    def confirm_forgot_password(self, **kw):
        self.calls.append(("confirm_forgot_password", kw))
        if self.raises:
            raise self.raises
        return {}


def _use_fake(monkeypatch, fake):
    monkeypatch.setattr(cognito_service, "_get_client", lambda: fake)
    monkeypatch.setattr(cognito_service, "COGNITO_APP_CLIENT_ID", "client-123")
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "")


def _id_token(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"hdr.{payload}.sig"


def test_authenticate_returns_tokens_and_claims(monkeypatch):
    fake = _FakeIdp(result={"AuthenticationResult": {
        "AccessToken": "acc", "IdToken": _id_token({"sub": "s1", "email": "a@b.co"}),
        "RefreshToken": "ref", "ExpiresIn": 3600}})
    _use_fake(monkeypatch, fake)
    monkeypatch.setattr(
        cognito_service,
        "_decode_claims",
        lambda token: {"sub": "s1", "email": "a@b.co"},
    )
    out = cognito_service.authenticate("ali", "Sifre123")
    assert out["tokens"] == {"access_token": "acc", "id_token": _id_token({"sub": "s1", "email": "a@b.co"}),
                             "refresh_token": "ref", "expires_in": 3600}
    assert out["claims"]["sub"] == "s1"


def test_initiate_auth_still_returns_claims(monkeypatch):
    fake = _FakeIdp(result={"AuthenticationResult": {
        "AccessToken": "acc", "IdToken": _id_token({"sub": "s2"}), "RefreshToken": "r"}})
    _use_fake(monkeypatch, fake)
    monkeypatch.setattr(cognito_service, "_decode_claims", lambda token: {"sub": "s2"})
    claims = cognito_service.initiate_auth("ali", "Sifre123")
    assert claims["sub"] == "s2"


def test_authenticate_challenge_rejected(monkeypatch):
    _use_fake(monkeypatch, _FakeIdp(result={"ChallengeName": "SMS_MFA"}))
    with pytest.raises(CognitoServiceError):
        cognito_service.authenticate("ali", "Sifre123")


def test_refresh_tokens_returns_new_access(monkeypatch):
    fake = _FakeIdp(result={"AuthenticationResult": {"AccessToken": "newacc", "ExpiresIn": 3600}})
    _use_fake(monkeypatch, fake)
    out = cognito_service.refresh_tokens("refresh-tok", "ali")
    assert out["access_token"] == "newacc"
    name, kw = fake.calls[-1]
    assert kw["AuthFlow"] == "REFRESH_TOKEN_AUTH"
    assert kw["AuthParameters"]["REFRESH_TOKEN"] == "refresh-tok"


def test_refresh_tokens_preserves_or_replaces_provider_refresh(monkeypatch):
    unchanged = _FakeIdp(result={"AuthenticationResult": {
        "AccessToken": "newacc", "IdToken": "newid", "ExpiresIn": 1200}})
    _use_fake(monkeypatch, unchanged)
    assert cognito_service.refresh_tokens("old-refresh", "ali") == {
        "access_token": "newacc", "id_token": "newid",
        "refresh_token": "old-refresh", "expires_in": 1200,
    }

    rotated = _FakeIdp(result={"AuthenticationResult": {
        "AccessToken": "nextacc", "RefreshToken": "rotated-refresh"}})
    _use_fake(monkeypatch, rotated)
    assert cognito_service.refresh_tokens("old-refresh", "ali")["refresh_token"] == (
        "rotated-refresh")


def test_revoke_token_uses_refresh_token(monkeypatch):
    fake = _FakeIdp()
    _use_fake(monkeypatch, fake)
    cognito_service.revoke_token("provider-refresh")
    assert fake.calls == [("revoke_token", {
        "Token": "provider-refresh", "ClientId": "client-123"})]


def test_refresh_tokens_failure_maps_error(monkeypatch):
    _use_fake(monkeypatch, _FakeIdp(raises=_FakeClientError("NotAuthorizedException")))
    with pytest.raises(CognitoServiceError):
        cognito_service.refresh_tokens("refresh-tok", "ali")


def test_global_sign_out_calls_client(monkeypatch):
    fake = _FakeIdp()
    _use_fake(monkeypatch, fake)
    cognito_service.global_sign_out("acc-token")
    assert fake.calls[-1][0] == "global_sign_out"
    assert fake.calls[-1][1]["AccessToken"] == "acc-token"


def test_forgot_password_calls_cognito(monkeypatch):
    fake = _FakeIdp()
    _use_fake(monkeypatch, fake)
    cognito_service.forgot_password("alice")
    assert fake.calls[-1] == ("forgot_password", {
        "ClientId": "client-123", "Username": "alice",
    })


def test_confirm_forgot_password_calls_cognito(monkeypatch):
    fake = _FakeIdp()
    _use_fake(monkeypatch, fake)
    cognito_service.confirm_forgot_password("alice", "123456", "Newpass123")
    assert fake.calls[-1] == ("confirm_forgot_password", {
        "ClientId": "client-123", "Username": "alice",
        "ConfirmationCode": "123456", "Password": "Newpass123",
    })


@pytest.mark.parametrize("code", [
    "ExpiredCodeException", "CodeMismatchException", "LimitExceededException",
    "NotAuthorizedException", "TooManyRequestsException", "UserNotFoundException",
    "InvalidPasswordException",
])
def test_recovery_errors_are_wrapped(monkeypatch, code):
    fake = _FakeIdp(raises=_FakeClientError(code))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError) as exc:
        cognito_service.forgot_password("alice")
    assert exc.value.code == code
    assert code not in exc.value.message


def test_password_reset_and_internal_error_mapped():
    assert "PasswordResetRequiredException" in cognito_service._ERROR_MESSAGES
    assert "InternalErrorException" in cognito_service._ERROR_MESSAGES


def test_unexpected_provider_error_does_not_log_raw_exception(caplog):
    sensitive = "alice@example.com Password1 reset-code-123456"
    wrapped = cognito_service._wrap(RuntimeError(sensitive))
    assert wrapped.code == ""
    assert sensitive not in caplog.text
    assert "RuntimeError" in caplog.text
