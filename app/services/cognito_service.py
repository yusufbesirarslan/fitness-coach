"""Amazon Cognito User Pools — native (Hosted UI'siz) kayıt / doğrulama / giriş.

Hosted UI redirect akışının (app/services/cognito.py + /login/cognito) aksine,
buradaki çağrılar uygulamanın KENDİ formlarından doğrudan Cognito'nun cognito-idp
API'sine gider:
  - sign_up               → kullanıcı oluşturur, e-postaya DOĞRULAMA KODU gönderir
  - confirm_sign_up       → kullanıcının girdiği kodu doğrular
  - resend_code           → kodu yeniden gönderir
  - authenticate          → USER_PASSWORD_AUTH ile giriş; ham token'lar + claim'ler
  - refresh_tokens        → REFRESH_TOKEN_AUTH ile access token'ı yeniler
  - global_sign_out       → kullanıcının TÜM refresh token'larını iptal eder (logout)
  - initiate_auth         → geriye dönük uyum shim'i (yalnızca claim'leri döner)

Bu uç noktalar İMZASIZ (public app client) çağrılır — AWS IAM kimliği GEREKMEZ;
app client'ın bir secret'i varsa SECRET_HASH ile kimliklenir. Bu yüzden boto3
istemcisi signature_version=UNSIGNED ile kurulur (aksi halde boto3 AWS kimliği
arar ve kimlik yokken gereksiz yere patlar).
"""
import base64
import hashlib
import hmac
import logging
import threading
import time

import requests
from joserfc import jwt
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from app.config import (COGNITO_APP_CLIENT_ID, COGNITO_CLIENT_SECRET,
                        COGNITO_REGION, COGNITO_USER_POOL_ID)

_logger = logging.getLogger(__name__)
_client = None
_JWKS_TTL_SECONDS = 6 * 60 * 60
_jwks_cache = {"value": None, "expires_at": 0.0}
_jwks_lock = threading.Lock()

# Ham Cognito hata adı → kullanıcıya gösterilebilir Türkçe mesaj. Listede olmayan
# hatalar generic mesaja düşer (ham hata sızdırılmaz).
_ERROR_MESSAGES = {
    "UsernameExistsException": "Bu kullanıcı adı veya e-posta zaten kayıtlı.",
    "InvalidPasswordException": "Şifre politikası karşılanmıyor (en az 8 karakter, harf + rakam).",
    "InvalidParameterException": "Geçersiz bilgi. Lütfen alanları kontrol et.",
    "CodeMismatchException": "Doğrulama kodu hatalı.",
    "ExpiredCodeException": "Doğrulama kodunun süresi doldu. Yeni kod iste.",
    "UserNotFoundException": "Kullanıcı adı veya şifre hatalı.",
    "NotAuthorizedException": "Kullanıcı adı veya şifre hatalı.",
    "UserNotConfirmedException": "E-postan henüz doğrulanmadı.",
    "LimitExceededException": "Çok fazla deneme. Lütfen biraz sonra tekrar dene.",
    "TooManyRequestsException": "Çok fazla deneme. Lütfen biraz sonra tekrar dene.",
    "PasswordResetRequiredException": "Şifreni sıfırlaman gerekiyor. Lütfen şifre sıfırlama akışını kullan.",
    "InternalErrorException": "Sunucu hatası. Lütfen biraz sonra tekrar dene.",
}


class CognitoServiceError(Exception):
    """cognito-idp çağrısı kullanıcı hatasıyla döndüğünde yükseltilir. `message`
    Türkçe ve kullanıcıya gösterilebilir; `code` ham Cognito hata adıdır
    (örn. 'CodeMismatchException', 'UserNotConfirmedException') — route bazı
    kodlara (doğrulanmamış e-posta) özel davranır."""

    def __init__(self, message, code=""):
        super().__init__(message)
        self.message = message
        self.code = code


def _get_client():
    global _client
    if _client is None:
        import boto3
        from botocore import UNSIGNED
        from botocore.config import Config
        # N5: açık connect/read timeout. initiate_auth LOGIN yolunda oturur
        # (LOGIN_FAIL_CLOSED ile birleşince bir asılı çağrı thread baskısını
        # büyütür); botocore varsayılanları (~60s + retry → dakikalar) yerine sınırla.
        _client = boto3.client(
            "cognito-idp",
            region_name=COGNITO_REGION,
            config=Config(signature_version=UNSIGNED,
                          connect_timeout=5, read_timeout=10,
                          retries={"max_attempts": 2}),
        )
    return _client


def _secret_hash(username):
    """App client'ın secret'i varsa SECRET_HASH üret; yoksa None (public client).
    Cognito formülü: base64(HMAC_SHA256(secret, username + client_id))."""
    if not COGNITO_CLIENT_SECRET:
        return None
    msg = (username + COGNITO_APP_CLIENT_ID).encode("utf-8")
    digest = hmac.new(COGNITO_CLIENT_SECRET.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _wrap(exc):
    """boto3 ClientError'ı (varsa) Türkçe CognitoServiceError'a çevir."""
    code = ""
    try:
        code = exc.response["Error"]["Code"]
    except Exception:
        pass
    if code:
        _logger.info("[COGNITO-IDP] %s", code)
        return CognitoServiceError(_ERROR_MESSAGES.get(code, "İşlem başarısız. Lütfen tekrar dene."), code)
    _logger.warning("[COGNITO-IDP] beklenmeyen hata: %s", type(exc).__name__)
    return CognitoServiceError("İşlem başarısız. Lütfen tekrar dene.")


def _maybe_secret(kwargs, username):
    sh = _secret_hash(username)
    if sh:
        kwargs["SecretHash"] = sh
    return kwargs


def sign_up(username, password, email, name):
    """Cognito'da yeni kullanıcı oluştur; e-postaya doğrulama kodu gönderilir.

    `name` Cognito 'name' attribute'una geçer — kullanıcı havuzu bunu ZORUNLU
    attribute yapmış olabilir; eksikse Cognito InvalidParameterException döner.
    Döndürür: Cognito 'sub' (UserSub) — yerel hesabı buna bağlarız.
    """
    kwargs = _maybe_secret({
        "ClientId": COGNITO_APP_CLIENT_ID,
        "Username": username,
        "Password": password,
        "UserAttributes": [
            {"Name": "email", "Value": email},
            {"Name": "name", "Value": name},
        ],
    }, username)
    try:
        resp = _get_client().sign_up(**kwargs)
    except Exception as e:
        raise _wrap(e)
    return resp.get("UserSub")


def confirm_sign_up(username, code):
    """Kullanıcının e-postasına gelen doğrulama kodunu onayla."""
    kwargs = _maybe_secret({
        "ClientId": COGNITO_APP_CLIENT_ID,
        "Username": username,
        "ConfirmationCode": code,
    }, username)
    try:
        _get_client().confirm_sign_up(**kwargs)
    except Exception as e:
        raise _wrap(e)


def resend_code(username):
    """Doğrulama kodunu yeniden gönder (kod kaybolduysa/süresi dolduysa)."""
    kwargs = _maybe_secret({
        "ClientId": COGNITO_APP_CLIENT_ID,
        "Username": username,
    }, username)
    try:
        _get_client().resend_confirmation_code(**kwargs)
    except Exception as e:
        raise _wrap(e)


def forgot_password(username):
    """Cognito forgot-password akışını başlat ve e-postaya kod gönder."""
    kwargs = _maybe_secret({
        "ClientId": COGNITO_APP_CLIENT_ID,
        "Username": username,
    }, username)
    try:
        _get_client().forgot_password(**kwargs)
    except Exception as e:
        raise _wrap(e)


def confirm_forgot_password(username, code, new_password):
    """Doğrulama koduyla Cognito parolasını değiştir."""
    kwargs = _maybe_secret({
        "ClientId": COGNITO_APP_CLIENT_ID,
        "Username": username,
        "ConfirmationCode": code,
        "Password": new_password,
    }, username)
    try:
        _get_client().confirm_forgot_password(**kwargs)
    except Exception as e:
        raise _wrap(e)


def authenticate(username, password):
    """USER_PASSWORD_AUTH ile giriş. Başarılıysa ham token'ları (access/id/refresh/
    expires_in) VE çözülmüş id-token claim'lerini döndürür. Challenge/boş kimlik
    reddedilir (auth bypass koruması)."""
    params = _maybe_secret({"USERNAME": username, "PASSWORD": password}, username)
    # _maybe_secret SecretHash anahtarını yazar; AuthParameters SECRET_HASH ister.
    if "SecretHash" in params:
        params["SECRET_HASH"] = params.pop("SecretHash")
    try:
        resp = _get_client().initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters=params,
        )
    except Exception as e:
        raise _wrap(e)
    # Cognito parolayı doğrulayıp token yerine bir CHALLENGE (SMS_MFA,
    # SOFTWARE_TOKEN_MFA, NEW_PASSWORD_REQUIRED, ...) dönebilir. Bu durumda
    # AuthenticationResult boştur; giriş TAMAMLANMAMIŞTIR. Boş claim döndürüp
    # çağıranın bunu başarı sanmasına izin verme — açıkça reddet (auth bypass).
    if resp.get("ChallengeName"):
        raise CognitoServiceError(
            "Ek doğrulama gerekiyor; bu akış desteklenmiyor.",
            "ChallengeRequired",
        )
    auth = resp.get("AuthenticationResult") or {}
    id_token = auth.get("IdToken", "")
    claims = _decode_claims(id_token)
    # Token çözülemediyse (boş/bozuk) giriş başarılı sayılmamalı.
    if not claims.get("sub"):
        raise CognitoServiceError("Kimlik doğrulanamadı.", "NoIdentity")
    return {
        "tokens": {
            "access_token": auth.get("AccessToken", ""),
            "id_token": id_token,
            "refresh_token": auth.get("RefreshToken", ""),
            "expires_in": auth.get("ExpiresIn", 3600),
        },
        "claims": claims,
    }


def initiate_auth(username, password):
    """Geriye dönük uyum: yalnızca id-token claim'lerini döndürür (Sprint 1
    çağıranları/testleri için). Yeni giriş yolu authenticate() kullanır."""
    return authenticate(username, password)["claims"]


def refresh_tokens(refresh_token, cognito_username):
    """REFRESH_TOKEN_AUTH ile yeni access token al. SECRET_HASH (gizli client)
    kullanıcı ADIYLA üretilir. Başarısızsa CognitoServiceError."""
    params = {"REFRESH_TOKEN": refresh_token}
    sh = _secret_hash(cognito_username)
    if sh:
        params["SECRET_HASH"] = sh
    try:
        resp = _get_client().initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters=params,
        )
    except Exception as e:
        raise _wrap(e)
    auth = resp.get("AuthenticationResult") or {}
    new_access = auth.get("AccessToken", "")
    if not new_access:
        raise CognitoServiceError("Oturum yenilenemedi.", "RefreshFailed")
    return {"access_token": new_access, "id_token": auth.get("IdToken", ""),
            "expires_in": auth.get("ExpiresIn", 3600)}


def global_sign_out(access_token):
    """Cognito GlobalSignOut — kullanıcının TÜM refresh token'larını iptal eder."""
    try:
        _get_client().global_sign_out(AccessToken=access_token)
    except Exception as e:
        raise _wrap(e)


CognitoIdpError = CognitoServiceError


def _get_jwks(force_refresh=False):
    """Return Cognito signing keys with a short in-process rotation cache."""
    now = time.monotonic()
    cached = _jwks_cache["value"]
    if not force_refresh and cached is not None and now < _jwks_cache["expires_at"]:
        return cached
    with _jwks_lock:
        now = time.monotonic()
        cached = _jwks_cache["value"]
        if not force_refresh and cached is not None and now < _jwks_cache["expires_at"]:
            return cached
        url = (
            f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
            f"{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
        )
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise ValueError("Cognito JWKS response is invalid")
        _jwks_cache["value"] = payload
        _jwks_cache["expires_at"] = now + _JWKS_TTL_SECONDS
        return payload


def _decode_claims(id_token):
    """Verify a Cognito ID token and return the identity claims used by auth."""
    if not id_token or not COGNITO_USER_POOL_ID or not COGNITO_APP_CLIENT_ID:
        return {}
    issuer = (
        f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"
        f"{COGNITO_USER_POOL_ID}"
    )
    registry = JWTClaimsRegistry(
        iss={"essential": True, "value": issuer},
        aud={"essential": True, "value": COGNITO_APP_CLIENT_ID},
        exp={"essential": True},
        token_use={"essential": True, "value": "id"},
        sub={"essential": True},
    )
    for force_refresh in (False, True):
        try:
            token = jwt.decode(
                id_token,
                KeySet.import_key_set(_get_jwks(force_refresh=force_refresh)),
                algorithms=["RS256"],
            )
            registry.validate(token.claims)
            claims = token.claims
            return {
                "sub": claims["sub"].strip(),
                "email": (claims.get("email") or "").strip().lower(),
                "email_verified": claims.get("email_verified", False),
                "name": claims.get("name") or "",
            }
        except Exception as exc:
            if force_refresh:
                _logger.warning(
                    "[COGNITO-IDP] ID token doğrulaması başarısız: %s",
                    type(exc).__name__)
    return {}
