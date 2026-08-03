# app/auth_middleware.py
"""Birleşik kimlik doğrulama ara katmanı — @require_auth.

Flask-Login oturumunu KORUR (current_user, login-redirect UX değişmez) ve
üstüne Cognito access token doğrulaması/yenilemesi ekler. Her korumalı endpoint
bu dekoratörü kullanır ve Flask kullanıcısı, sunucu oturumu ve doğrulanmış
Cognito `sub` aynı yerel kullanıcıya bağlanır.
"""
from functools import wraps

from flask import g, jsonify, session
from flask_login import current_user, logout_user

from app.extensions import login_manager
from app.i18n import t
from app.models import User
from app.services import auth_contract, cognito_jwt, session_store


def _invalidate(outcome=auth_contract.OUTCOME_SESSION_INVALID):
    auth_contract.record_outcome(auth_contract.WEB, outcome)
    sid = session.pop("cognito_sid", None)
    if sid:
        session_store.delete(sid)
    logout_user()
    return login_manager.unauthorized()


def _service_unavailable():
    """H1: Cognito/JWKS GEÇİCİ olarak ulaşılamıyor — oturuma DOKUNMA.

    Kritik ayrım: _invalidate() sunucu tarafı oturum satırını SİLER; bu geri
    dönüşsüzdür. Geçici bir altyapı kesintisi (Cognito throttle, ağ timeout'u,
    soğuk JWKS önbelleğiyle çakışan bir kesinti) "bu kullanıcı yetkisiz" demek
    değildir. Burada satırı ve Flask-Login oturumunu OLDUĞU GİBİ bırakıp
    503 + Retry-After döneriz (ai_gate ile aynı sözleşme): kesinti geçince
    kullanıcı yeniden giriş yapmadan devam eder.
    """
    auth_contract.record_outcome(
        auth_contract.WEB, auth_contract.OUTCOME_PROVIDER_UNAVAILABLE)
    resp = jsonify({"error": t("auth.temporarily_unavailable")})
    resp.status_code = 503
    resp.headers["Retry-After"] = "15"
    return resp


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            auth_contract.record_outcome(
                auth_contract.WEB, auth_contract.OUTCOME_NO_IDENTITY)
            return login_manager.unauthorized()
        sid = session.get("cognito_sid")
        if not sid:
            return _invalidate()
        try:
            access = session_store.get_valid_access_token(sid, current_user.id)
            # Token use ve expiry leeway'i BU dosya seçmez — sözleşme seçer
            # (app/services/auth_contract.py), mobil yol da aynı yerden okur.
            claims = auth_contract.validate_provider_token(
                auth_contract.WEB, access)
        except session_store.SessionTransient:
            return _service_unavailable()
        except cognito_jwt.TokenValidationError as e:
            # jwks_unavailable = imza DOĞRULANAMADI, "imza GEÇERSİZ" değil.
            # cognito_jwt bu nedeni özellikle ayrı tutuyor; sınıflandırma tek
            # yerde (auth_contract) yaşar, iki ara katman kendi kopyasını tutmaz.
            if auth_contract.is_transient_validation_reason(e.reason):
                return _service_unavailable()
            return _invalidate(auth_contract.OUTCOME_TOKEN_REJECTED)
        except session_store.SessionInvalid:
            return _invalidate()
        resolved = User.query.filter_by(cognito_sub=claims.get("sub")).first()
        if resolved is None or resolved.id != current_user.id:
            return _invalidate()
        g.cognito_claims = claims
        session_store.touch(sid)
        auth_contract.record_outcome(
            auth_contract.WEB, auth_contract.OUTCOME_OK)
        return view(*args, **kwargs)
    wrapped._require_auth = True
    return wrapped
