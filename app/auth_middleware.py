# app/auth_middleware.py
"""Birleşik kimlik doğrulama ara katmanı — @require_auth.

Flask-Login oturumunu KORUR (current_user, login-redirect UX değişmez) ve
üstüne Cognito access token doğrulaması/yenilemesi ekler. Legacy (cognito_sub'ı
olmayan) kullanıcılar yalnızca oturum kimliğiyle geçer (Sprint 3'e kadar geriye
uyum). Her korumalı endpoint bu dekoratörü kullanır.
"""
from functools import wraps

from flask import g, session
from flask_login import current_user, logout_user

from app.extensions import login_manager
from app.services import cognito_jwt, session_store


def _invalidate():
    sid = session.pop("cognito_sid", None)
    if sid:
        session_store.delete(sid)
    logout_user()
    return login_manager.unauthorized()


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        # Legacy kullanıcı: Cognito token yok → yalnızca oturumla geç.
        if not getattr(current_user, "cognito_sub", None):
            return view(*args, **kwargs)
        sid = session.get("cognito_sid")
        if not sid:
            return _invalidate()
        try:
            access = session_store.get_valid_access_token(sid)
            claims = cognito_jwt.validate_token(access, "access")
        except (session_store.SessionInvalid, cognito_jwt.TokenValidationError):
            return _invalidate()
        g.cognito_claims = claims
        session_store.touch(sid)
        return view(*args, **kwargs)
    return wrapped
