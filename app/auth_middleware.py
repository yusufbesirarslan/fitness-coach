# app/auth_middleware.py
"""Birleşik kimlik doğrulama ara katmanı — @require_auth.

Flask-Login oturumunu KORUR (current_user, login-redirect UX değişmez) ve
üstüne Cognito access token doğrulaması/yenilemesi ekler. Her korumalı endpoint
bu dekoratörü kullanır ve Flask kullanıcısı, sunucu oturumu ve doğrulanmış
Cognito `sub` aynı yerel kullanıcıya bağlanır.
"""
from functools import wraps

from flask import g, session
from flask_login import current_user, logout_user

from app.extensions import login_manager
from app.models import User
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
        sid = session.get("cognito_sid")
        if not sid:
            return _invalidate()
        try:
            access = session_store.get_valid_access_token(sid, current_user.id)
            claims = cognito_jwt.validate_token(access, "access")
        except (session_store.SessionInvalid, cognito_jwt.TokenValidationError):
            return _invalidate()
        resolved = User.query.filter_by(cognito_sub=claims.get("sub")).first()
        if resolved is None or resolved.id != current_user.id:
            return _invalidate()
        g.cognito_claims = claims
        session_store.touch(sid)
        return view(*args, **kwargs)
    wrapped._require_auth = True
    return wrapped
