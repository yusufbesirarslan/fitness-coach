from datetime import date

from flask import Blueprint, jsonify, redirect, request, session, url_for
from flask_login import current_user
from app.auth_middleware import require_auth

from app.config import SCRAPE_RATELIMIT
from app.extensions import _user_or_ip_key, limiter
from app.models import UserWearableConnection
from app.services.wearables.adapters import WearableError, get_adapter
from app.services.wearables.sync import sync_provider_day
from app.services.wearables.tokens import save_wearable_tokens
from app.timeutil import app_today


bp = Blueprint("wearables", __name__)


def _json_error(message, status=400):
    return jsonify({"error": message}), status


@bp.route("/api/auth/wearable/<provider>")
@require_auth
# OAuth başlatma da (authorization_url dış sağlayıcıya yönlendirir) döngüye
# sokulabilir; callback ise her çağrıda token exchange için dışarı HTTP atar.
# İkisini de diğer dış-fetch route'larıyla aynı kovaya al (SEC-2).
@limiter.limit(SCRAPE_RATELIMIT, key_func=_user_or_ip_key)
def wearable_login(provider):
    try:
        adapter = get_adapter(provider)
        url, state = adapter.authorization_url()
    except WearableError as exc:
        return _json_error(str(exc), 503)
    session[f"_wearable_oauth_state_{adapter.provider}"] = state
    return redirect(url)


@bp.route("/api/auth/callback/<provider>")
@require_auth
@limiter.limit(SCRAPE_RATELIMIT, key_func=_user_or_ip_key)  # exchange_code dış HTTP (SEC-2)
def wearable_callback(provider):
    try:
        adapter = get_adapter(provider)
    except WearableError as exc:
        return _json_error(str(exc), 404)
    expected = session.get(f"_wearable_oauth_state_{adapter.provider}")
    returned = request.args.get("state", "")
    if not expected or returned != expected:
        return _json_error("OAuth state doğrulaması başarısız.", 400)
    code = request.args.get("code", "")
    if not code:
        return _json_error("OAuth code eksik.", 400)
    try:
        token_data = adapter.exchange_code(code)
        save_wearable_tokens(current_user.id, adapter.provider, token_data)
    except Exception as exc:
        return _json_error(f"{adapter.provider} bağlantısı kurulamadı: {exc}", 502)
    session.pop(f"_wearable_oauth_state_{adapter.provider}", None)
    return redirect(url_for("profile.edit_profile"))


@bp.route("/api/wearables/status")
@require_auth
def wearable_status():
    rows = UserWearableConnection.query.filter_by(user_id=current_user.id)\
        .order_by(UserWearableConnection.provider.asc()).all()
    return jsonify({
        "connections": [{
            "provider": row.provider,
            "connected": row.status == "connected",
            "token_expiry": row.token_expiry.isoformat() if row.token_expiry else None,
            "last_sync_at": row.last_sync_at.isoformat() if row.last_sync_at else None,
        } for row in rows]
    })


@bp.route("/api/wearables/<provider>/sync", methods=["POST"])
@require_auth
# Her çağrı WHOOP/Google'a dışarı HTTP atar; limitsizken kullanıcı üçüncü-taraf
# rate-limit tüketimi / maliyet amplifikasyonu için döngüye sokabilirdi (S4).
# Diğer dış-fetch route'larıyla aynı kova (SCRAPE_RATELIMIT).
@limiter.limit(SCRAPE_RATELIMIT, key_func=_user_or_ip_key)
def wearable_sync(provider):
    raw_date = request.args.get("date")
    try:
        target = date.fromisoformat(raw_date) if raw_date else app_today()
    except ValueError:
        return _json_error("date YYYY-MM-DD formatında olmalı.", 400)
    try:
        return jsonify(sync_provider_day(current_user.id, provider, target))
    except WearableError as exc:
        return _json_error(str(exc), 400)
    except Exception as exc:
        return _json_error(f"Senkronizasyon başarısız: {exc}", 502)


@bp.route("/api/wearables/whoop/<resource>")
@require_auth
@limiter.limit(SCRAPE_RATELIMIT, key_func=_user_or_ip_key)  # dış WHOOP çağrısı (S4)
def whoop_resource(resource):
    adapter = get_adapter("whoop")
    endpoint = adapter.resource_endpoints.get(resource)
    if endpoint is None:
        return _json_error("Desteklenmeyen WHOOP kaynağı.", 404)
    try:
        return jsonify(adapter.request(endpoint, current_user.id, params=request.args.to_dict()))
    except Exception as exc:
        return _json_error(f"WHOOP isteği başarısız: {exc}", 502)
