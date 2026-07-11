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


def test_password_reset_and_internal_error_mapped():
    assert "PasswordResetRequiredException" in cognito_service._ERROR_MESSAGES
    assert "InternalErrorException" in cognito_service._ERROR_MESSAGES
