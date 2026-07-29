"""Amazon Cognito User Pools — native (Hosted UI'siz) kayıt / doğrulama / giriş.

Hosted UI redirect akışının (app/services/cognito.py + /login/cognito) aksine,
buradaki çağrılar uygulamanın KENDİ formlarından doğrudan Cognito'nun cognito-idp
API'sine gider:
  - sign_up               → kullanıcı oluşturur, e-postaya DOĞRULAMA KODU gönderir
  - confirm_sign_up       → kullanıcının girdiği kodu doğrular
  - resend_code           → kodu yeniden gönderir
  - forgot_password       → e-postaya ŞİFRE SIFIRLAMA KODU gönderir
  - confirm_forgot_password → sıfırlama kodunu doğrular, yeni şifreyi ayarlar
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

from app.config import (COGNITO_APP_CLIENT_ID, COGNITO_CLIENT_SECRET,
                        COGNITO_REGION, COGNITO_USER_POOL_ID)

_logger = logging.getLogger(__name__)
_client = None

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
    Cognito formülü: base64(HMAC_SHA256(secret, username + client_id)).

    L3: username None gelirse (username + ...) TypeError yükseltirdi — bu bir
    ClientError DEĞİL, dolayısıyla _wrap onu kullanıcı hatasına çeviremez ve
    çağıran temiz 401 yerine 500 alırdı. Boş dizeye normalize et; kimlik
    doğrulaması Cognito tarafında zaten başarısız olur.
    """
    if not COGNITO_CLIENT_SECRET:
        return None
    msg = ((username or "") + COGNITO_APP_CLIENT_ID).encode("utf-8")
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
    """Şifre sıfırlama kodunu kullanıcının e-postasına gönder (ForgotPassword).

    Kodu Cognito üretir ve iletir (CustomEmailSender trigger'ı bağlıysa markalı
    e-posta Lambda→Resend üzerinden gider); uygulama kodu hiç görmez. Kullanıcı
    yoksa/doğrulanmamışsa Cognito hata döner — route katmanı bunları hesap
    numaralandırmasına (enumeration) karşı JENERİK yanıtla yutar."""
    kwargs = _maybe_secret({
        "ClientId": COGNITO_APP_CLIENT_ID,
        "Username": username,
    }, username)
    try:
        _get_client().forgot_password(**kwargs)
    except Exception as e:
        raise _wrap(e)


def confirm_forgot_password(username, code, new_password):
    """Sıfırlama kodunu doğrula ve yeni şifreyi ayarla (ConfirmForgotPassword).

    Kod süresi/deneme limiti Cognito'nun kendi kurallarıdır; burada yalnızca
    iletilir. Başarı, kullanıcının şifresinin DEĞİŞTİĞİ anlamına gelir."""
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
    return {
        "access_token": new_access,
        "id_token": auth.get("IdToken", ""),
        "refresh_token": auth.get("RefreshToken") or refresh_token,
        "expires_in": auth.get("ExpiresIn", 3600),
    }


def revoke_token(refresh_token):
    """Revoke one provider refresh token without widening to global sign-out."""
    kwargs = {"Token": refresh_token, "ClientId": COGNITO_APP_CLIENT_ID}
    if COGNITO_CLIENT_SECRET:
        kwargs["ClientSecret"] = COGNITO_CLIENT_SECRET
    try:
        _get_client().revoke_token(**kwargs)
    except Exception as exc:
        raise _wrap(exc)


def global_sign_out(access_token):
    """Cognito GlobalSignOut — kullanıcının TÜM refresh token'larını iptal eder."""
    try:
        _get_client().global_sign_out(AccessToken=access_token)
    except Exception as e:
        raise _wrap(e)


CognitoIdpError = CognitoServiceError


def _decode_claims(id_token):
    """Verify a Cognito ID token and return the identity claims used by auth.

    L4: doğrulama cognito_jwt'ye DELEGE edilir. Burada ayrı bir joserfc
    doğrulayıcısı + ayrı bir JWKS önbelleği vardı; iki uygulama, bir güvenlik
    düzeltmesinin yalnızca birine inmesi riskini taşıyordu. Tek doğrulayıcı,
    tek önbellek, tek anahtar-rotasyonu yolu. Sözleşme değişmedi: doğrulama
    başarısızsa {} döner (yükseltmez) — authenticate() boş sub'ı zaten
    "NoIdentity" ile reddeder.
    """
    if not id_token or not COGNITO_USER_POOL_ID or not COGNITO_APP_CLIENT_ID:
        return {}
    from app.services import cognito_jwt
    try:
        claims = cognito_jwt.validate_token(id_token, "id")
    except cognito_jwt.TokenValidationError as exc:
        _logger.warning("[COGNITO-IDP] ID token doğrulaması başarısız: %s", exc.reason)
        return {}
    sub = (claims.get("sub") or "").strip()
    if not sub:
        return {}
    return {
        "sub": sub,
        "email": (claims.get("email") or "").strip().lower(),
        "email_verified": claims.get("email_verified", False),
        "name": claims.get("name") or "",
    }
