"""Amazon Cognito native (cognito-idp) sarmalayıcı testleri.

app/services/cognito_service.py boto3 cognito-idp çağrılarını sarar. boto3 istemcisi
tamamen sahte ile değiştirilir (ağ/AWS kimliği YOK): sign_up/confirm/resend/
initiate_auth doğru argümanlarla çağrılır, ClientError → Türkçe CognitoServiceError'a
eşlenir, ID token claim'leri çözülür ve SECRET_HASH (gizli app client) üretimi
sabitlenir.

    python -m pytest tests/test_cognito_idp.py -v
"""
import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from joserfc import jwt
from joserfc.jwk import RSAKey

from app.services import cognito_service
from app.services.cognito_service import CognitoServiceError


# ---------------------------------------------------------------------------
# Yardımcılar — sahte boto3 istemcisi ve ClientError.
# ---------------------------------------------------------------------------

class _FakeClientError(Exception):
    """boto3 ClientError'ı taklit eder: .response["Error"]["Code"] taşır."""
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeIdpClient:
    def __init__(self, *, raises=None, result=None):
        self.calls = []
        self.raises = raises
        self.result = result or {}

    def _do(self, name, kwargs):
        self.calls.append((name, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.result

    def sign_up(self, **kw):
        return self._do("sign_up", kw)

    def confirm_sign_up(self, **kw):
        return self._do("confirm_sign_up", kw)

    def resend_confirmation_code(self, **kw):
        return self._do("resend_confirmation_code", kw)

    def forgot_password(self, **kw):
        return self._do("forgot_password", kw)

    def confirm_forgot_password(self, **kw):
        return self._do("confirm_forgot_password", kw)

    def initiate_auth(self, **kw):
        return self._do("initiate_auth", kw)


_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_SIGNING_KEY = RSAKey.import_key(
    _PRIVATE_KEY, parameters={"kid": "test-kid", "use": "sig", "alg": "RS256"})
_PUBLIC_JWKS = {"keys": [_SIGNING_KEY.as_dict(private=False)]}


def _use_fake(monkeypatch, fake):
    monkeypatch.setattr(cognito_service, "_get_client", lambda: fake)
    monkeypatch.setattr(cognito_service, "COGNITO_APP_CLIENT_ID", "client-123")
    monkeypatch.setattr(cognito_service, "COGNITO_REGION", "eu-central-1")
    monkeypatch.setattr(cognito_service, "COGNITO_USER_POOL_ID", "eu-central-1_test")
    monkeypatch.setattr(
        cognito_service, "_get_jwks", lambda force_refresh=False: _PUBLIC_JWKS,
        raising=False)
    # Varsayılan: public client (secret yok) → SECRET_HASH üretilmez.
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "")


def _id_token(claims, key=_SIGNING_KEY, kid="test-kid"):
    payload = {
        "iss": "https://cognito-idp.eu-central-1.amazonaws.com/eu-central-1_test",
        "aud": "client-123",
        "token_use": "id",
        "exp": int(time.time()) + 300,
        **claims,
    }
    return jwt.encode(
        {"alg": "RS256", "kid": kid}, payload, key, algorithms=["RS256"])


# ---------------------------------------------------------------------------
# _secret_hash / _maybe_secret — public vs gizli app client.
# ---------------------------------------------------------------------------

def test_secret_hash_none_for_public_client(monkeypatch):
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "")
    assert cognito_service._secret_hash("ali") is None


def test_secret_hash_computed_when_secret_present(monkeypatch):
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "s3cr3t")
    monkeypatch.setattr(cognito_service, "COGNITO_APP_CLIENT_ID", "client-123")
    sh = cognito_service._secret_hash("ali")
    assert isinstance(sh, str) and sh  # base64(HMAC-SHA256) → boş değil
    # Deterministik: aynı girdi aynı hash.
    assert sh == cognito_service._secret_hash("ali")


def test_sign_up_adds_secret_hash_when_secret_set(monkeypatch):
    fake = _FakeIdpClient(result={"UserSub": "sub-1"})
    _use_fake(monkeypatch, fake)
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "s3cr3t")
    cognito_service.sign_up("ali", "Sifre123", "ali@example.com", "Ali")
    _, kwargs = fake.calls[0]
    assert "SecretHash" in kwargs


# ---------------------------------------------------------------------------
# sign_up
# ---------------------------------------------------------------------------

def test_sign_up_returns_user_sub_and_passes_attributes(monkeypatch):
    fake = _FakeIdpClient(result={"UserSub": "sub-xyz"})
    _use_fake(monkeypatch, fake)
    sub = cognito_service.sign_up("ali", "Sifre123", "ali@example.com", "Ali Veli")
    assert sub == "sub-xyz"
    name, kwargs = fake.calls[0]
    assert name == "sign_up"
    assert kwargs["ClientId"] == "client-123"
    assert kwargs["Username"] == "ali"
    attrs = {a["Name"]: a["Value"] for a in kwargs["UserAttributes"]}
    assert attrs == {"email": "ali@example.com", "name": "Ali Veli"}
    assert "SecretHash" not in kwargs  # public client


def test_sign_up_maps_known_error_to_turkish(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("UsernameExistsException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError) as ei:
        cognito_service.sign_up("ali", "Sifre123", "ali@example.com", "Ali")
    assert ei.value.code == "UsernameExistsException"
    assert "zaten kayıtlı" in ei.value.message


def test_sign_up_maps_unknown_error_to_generic(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("SomeWeirdException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError) as ei:
        cognito_service.sign_up("ali", "Sifre123", "ali@example.com", "Ali")
    assert ei.value.code == "SomeWeirdException"
    assert ei.value.message == "İşlem başarısız. Lütfen tekrar dene."


def test_wrap_non_client_error_has_no_code(monkeypatch):
    # response taşımayan bir hata → code boş, generic mesaj.
    fake = _FakeIdpClient(raises=RuntimeError("network blip"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError) as ei:
        cognito_service.sign_up("ali", "Sifre123", "ali@example.com", "Ali")
    assert ei.value.code == ""
    assert "başarısız" in ei.value.message


# ---------------------------------------------------------------------------
# confirm_sign_up / resend_code
# ---------------------------------------------------------------------------

def test_confirm_sign_up_calls_client_with_code(monkeypatch):
    fake = _FakeIdpClient()
    _use_fake(monkeypatch, fake)
    cognito_service.confirm_sign_up("ali", "123456")
    name, kwargs = fake.calls[0]
    assert name == "confirm_sign_up"
    assert kwargs["ConfirmationCode"] == "123456"
    assert kwargs["Username"] == "ali"


def test_confirm_sign_up_maps_code_mismatch(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("CodeMismatchException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError, match="kodu hatalı"):
        cognito_service.confirm_sign_up("ali", "000000")


def test_resend_code_calls_client(monkeypatch):
    fake = _FakeIdpClient()
    _use_fake(monkeypatch, fake)
    cognito_service.resend_code("ali")
    name, kwargs = fake.calls[0]
    assert name == "resend_confirmation_code"
    assert kwargs["Username"] == "ali"


# ---------------------------------------------------------------------------
# forgot_password / confirm_forgot_password — şifre sıfırlama sarmalayıcıları.
# ---------------------------------------------------------------------------

def test_forgot_password_calls_client(monkeypatch):
    fake = _FakeIdpClient()
    _use_fake(monkeypatch, fake)
    cognito_service.forgot_password("ali")
    name, kwargs = fake.calls[0]
    assert name == "forgot_password"
    assert kwargs == {"ClientId": "client-123", "Username": "ali"}


def test_forgot_password_adds_secret_hash_when_secret_set(monkeypatch):
    fake = _FakeIdpClient()
    _use_fake(monkeypatch, fake)
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "s3cr3t")
    cognito_service.forgot_password("ali")
    _, kwargs = fake.calls[0]
    assert "SecretHash" in kwargs


def test_forgot_password_maps_user_not_found(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("UserNotFoundException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError) as ei:
        cognito_service.forgot_password("yok")
    assert ei.value.code == "UserNotFoundException"


def test_forgot_password_maps_limit_exceeded(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("LimitExceededException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError, match="Çok fazla deneme"):
        cognito_service.forgot_password("ali")


def test_confirm_forgot_password_calls_client(monkeypatch):
    fake = _FakeIdpClient()
    _use_fake(monkeypatch, fake)
    cognito_service.confirm_forgot_password("ali", "123456", "YeniSifre1")
    name, kwargs = fake.calls[0]
    assert name == "confirm_forgot_password"
    assert kwargs == {"ClientId": "client-123", "Username": "ali",
                      "ConfirmationCode": "123456", "Password": "YeniSifre1"}


def test_confirm_forgot_password_adds_secret_hash_when_secret_set(monkeypatch):
    fake = _FakeIdpClient()
    _use_fake(monkeypatch, fake)
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "s3cr3t")
    cognito_service.confirm_forgot_password("ali", "123456", "YeniSifre1")
    _, kwargs = fake.calls[0]
    assert "SecretHash" in kwargs


def test_confirm_forgot_password_maps_code_mismatch(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("CodeMismatchException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError, match="kodu hatalı"):
        cognito_service.confirm_forgot_password("ali", "000000", "YeniSifre1")


def test_confirm_forgot_password_maps_expired_code(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("ExpiredCodeException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError, match="süresi doldu"):
        cognito_service.confirm_forgot_password("ali", "123456", "YeniSifre1")


def test_confirm_forgot_password_maps_invalid_password(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("InvalidPasswordException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError, match="Şifre politikası"):
        cognito_service.confirm_forgot_password("ali", "123456", "zayif")


# ---------------------------------------------------------------------------
# initiate_auth — claim çözümü + SECRET_HASH yer değişimi.
# ---------------------------------------------------------------------------

def test_initiate_auth_decodes_id_token_claims(monkeypatch):
    token = _id_token({"sub": "sub-9", "email": "Ali@Example.com",
                       "email_verified": True, "name": "Ali"})
    fake = _FakeIdpClient(result={"AuthenticationResult": {"IdToken": token}})
    _use_fake(monkeypatch, fake)
    claims = cognito_service.initiate_auth("ali", "Sifre123")
    assert claims == {"sub": "sub-9", "email": "ali@example.com",
                      "email_verified": True, "name": "Ali"}


def test_initiate_auth_moves_secret_hash_into_auth_params(monkeypatch):
    token = _id_token({"sub": "s", "email": "a@b.co"})
    fake = _FakeIdpClient(result={"AuthenticationResult": {"IdToken": token}})
    _use_fake(monkeypatch, fake)
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "s3cr3t")
    cognito_service.initiate_auth("ali", "Sifre123")
    _, kwargs = fake.calls[0]
    params = kwargs["AuthParameters"]
    assert "SECRET_HASH" in params       # AuthParameters SECRET_HASH bekler
    assert "SecretHash" not in params     # boto3 üst-düzey anahtarı taşınmadı
    assert params["USERNAME"] == "ali"


def test_initiate_auth_maps_not_authorized(monkeypatch):
    fake = _FakeIdpClient(raises=_FakeClientError("NotAuthorizedException"))
    _use_fake(monkeypatch, fake)
    with pytest.raises(CognitoServiceError) as ei:
        cognito_service.initiate_auth("ali", "yanlis")
    assert ei.value.code == "NotAuthorizedException"


# ---------------------------------------------------------------------------
# _decode_claims — bozuk token sessizce {} döner.
# ---------------------------------------------------------------------------

def test_decode_claims_garbage_returns_empty():
    assert cognito_service._decode_claims("not-a-jwt") == {}
    assert cognito_service._decode_claims("") == {}


def test_decode_claims_verifies_valid_signature(monkeypatch):
    _use_fake(monkeypatch, _FakeIdpClient())
    token = _id_token({"sub": "sub-1", "email": "A@EXAMPLE.COM"})
    assert cognito_service._decode_claims(token) == {
        "sub": "sub-1", "email": "a@example.com",
        "email_verified": False, "name": "",
    }


def test_decode_claims_rejects_forged_signature(monkeypatch):
    _use_fake(monkeypatch, _FakeIdpClient())
    other = RSAKey.import_key(
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        parameters={"kid": "test-kid", "use": "sig", "alg": "RS256"})
    assert cognito_service._decode_claims(_id_token({"sub": "forged"}, key=other)) == {}


@pytest.mark.parametrize("overrides", [
    {"iss": "https://attacker.invalid/pool"},
    {"aud": "wrong-client"},
    {"token_use": "access"},
    {"exp": 1},
    {"sub": ""},
])
def test_decode_claims_rejects_invalid_cognito_claims(monkeypatch, overrides):
    _use_fake(monkeypatch, _FakeIdpClient())
    claims = {"sub": "sub-1", **overrides}
    assert cognito_service._decode_claims(_id_token(claims)) == {}


def test_initiate_auth_missing_id_token_rejected(monkeypatch):
    # AuthenticationResult yok → token yok → giriş POZİTİF doğrulama olduğundan
    # boş claim döndürmek yerine reddedilmeli (auth bypass koruması).
    fake = _FakeIdpClient(result={})
    _use_fake(monkeypatch, fake)
    with pytest.raises(cognito_service.CognitoServiceError):
        cognito_service.initiate_auth("ali", "Sifre123")


def test_initiate_auth_challenge_rejected(monkeypatch):
    # Cognito parolayı doğrulayıp MFA/challenge dönerse giriş TAMAMLANMAMIŞTIR;
    # boş claim yerine açıkça reddedilmeli.
    fake = _FakeIdpClient(result={"ChallengeName": "SOFTWARE_TOKEN_MFA",
                                  "Session": "sess"})
    _use_fake(monkeypatch, fake)
    with pytest.raises(cognito_service.CognitoServiceError):
        cognito_service.initiate_auth("ali", "Sifre123")


# ---------------------------------------------------------------------------
# _get_client — boto3 istemcisini lazy + UNSIGNED kurar (AWS kimliği gerekmez).
# ---------------------------------------------------------------------------

def test_get_client_lazy_constructs_unsigned(monkeypatch):
    import boto3
    monkeypatch.setattr(cognito_service, "_client", None)
    captured = {}

    def fake_boto_client(service, region_name=None, config=None):
        captured.update(service=service, region_name=region_name, config=config)
        return "SENTINEL"

    monkeypatch.setattr(boto3, "client", fake_boto_client)
    client = cognito_service._get_client()
    assert client == "SENTINEL"
    assert captured["service"] == "cognito-idp"
    assert captured["config"] is not None  # UNSIGNED config geçti
