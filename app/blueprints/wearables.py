from datetime import date

from flask import Blueprint, current_app, jsonify, redirect, request, session, url_for
from flask_login import current_user
from app.auth_middleware import require_auth

from app.config import SCRAPE_RATELIMIT
from app.extensions import _user_or_ip_key, limiter
from app.models import UserWearableConnection
from app.services.ai_gate import (BlockingConcurrencyLimit,
                                  blocking_concurrency_slot)
from app.services.wearables.adapters import WearableError, get_adapter
from app.services.wearables.sync import sync_provider_day
from app.services.wearables.tokens import save_wearable_tokens
from app.timeutil import app_today


bp = Blueprint("wearables", __name__)

_WHOOP_QUERY_PARAMS = {
    "profile": frozenset(),
    "body": frozenset(),
    "recovery": frozenset({"start", "end", "limit"}),
    "sleep": frozenset({"start", "end", "limit"}),
    "workout": frozenset({"start", "end", "limit"}),
}


def _json_error(message, status=400):
    return jsonify({"error": message}), status


# Hardening PR4 — giyilebilir sağlayıcı çağrıları PAYLAŞILAN bloklayıcı kapasiteyi
# tüketir.
#
# Bu üç route senkron dış HTTP atar ve `_authed_json` 401'de token yenileyip
# yeniden dener: çağrı başına 10 sn × en fazla 3 ardışık istek ≈ 30 sn boyunca
# bir web thread'i park eder. Hiçbir eşzamanlılık kapısı YOKTU ve bu yüzey
# thread-rezervi aritmetiğinde SAYILMIYORDU — WHOOP/Google gecikme artışında
# yeterli eşzamanlı senkronizasyon 8 thread'i doldurup /health'i kuyruğa
# sokabilir, deploy health gate'ini düşürüp sahte rollback tetikleyebilirdi.
# Bu, PR #199'un mobil Cognito uçları için kapattığı boşluğun AYNISIDIR; burada
# da AYNI mekanizmayı kullanırız (yeni bir kapı/semafor İCAT ETMEYİZ).
#
# Aşırı yük yanıtı kapı dekoratörünün sözleşmesiyle aynıdır: 503 + Retry-After.
def _overload_response():
    current_app.logger.warning(
        "wearables event=blocking_capacity_exhausted")
    resp = jsonify({"error": "Servis yoğun, lütfen birazdan tekrar deneyin."})
    resp.status_code = 503
    resp.headers["Retry-After"] = "15"
    return resp


def _whoop_query_params(resource):
    """Return validated, resource-scoped WHOOP query parameters."""
    params = {
        key: request.args.get(key)
        for key in _WHOOP_QUERY_PARAMS.get(resource, ())
        if key in request.args
    }
    for key in ("start", "end"):
        if key in params and len(params[key]) > 64:
            raise ValueError(f"{key} parametresi çok uzun.")
    if "limit" in params:
        try:
            limit = int(params["limit"])
        except (TypeError, ValueError):
            raise ValueError("limit 1 ile 25 arasında bir tam sayı olmalı.") from None
        if not 1 <= limit <= 25:
            raise ValueError("limit 1 ile 25 arasında bir tam sayı olmalı.")
    return params


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
        # Kapı YALNIZCA ağ alışverişini sarar; token yazımı (kısa, yerel DB işi)
        # izni tutmaz — hiçbir DB transaction'ı sağlayıcı I/O'sunu KAPSAMAZ.
        with blocking_concurrency_slot():
            token_data = adapter.exchange_code(code)
    except BlockingConcurrencyLimit:
        return _overload_response()
    except Exception:
        current_app.logger.warning("[WEARABLE] %s bağlantısı kurulamadı",
                                   adapter.provider, exc_info=True)
        return _json_error(f"{adapter.provider} bağlantısı kurulamadı.", 502)
    try:
        save_wearable_tokens(current_user.id, adapter.provider, token_data)
    except Exception:
        # S3: ham exception metni (iç URL/kütüphane detayı) istemciye sızmasın —
        # sunucuda logla, istemciye jenerik mesaj dön. (cognito_service._wrap deseni)
        current_app.logger.warning("[WEARABLE] %s bağlantısı kurulamadı",
                                   adapter.provider, exc_info=True)
        return _json_error(f"{adapter.provider} bağlantısı kurulamadı.", 502)
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
            "status": row.status,
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
        with blocking_concurrency_slot():
            return jsonify(sync_provider_day(current_user.id, provider, target))
    except BlockingConcurrencyLimit:
        return _overload_response()
    except WearableError as exc:
        return _json_error(str(exc), 400)
    except Exception:
        current_app.logger.warning("[WEARABLE] %s senkronizasyonu başarısız",
                                   provider, exc_info=True)
        return _json_error("Senkronizasyon başarısız.", 502)


@bp.route("/api/wearables/whoop/<resource>")
@require_auth
@limiter.limit(SCRAPE_RATELIMIT, key_func=_user_or_ip_key)  # dış WHOOP çağrısı (S4)
def whoop_resource(resource):
    adapter = get_adapter("whoop")
    endpoint = adapter.resource_endpoints.get(resource)
    if endpoint is None:
        return _json_error("Desteklenmeyen WHOOP kaynağı.", 404)
    try:
        params = _whoop_query_params(resource)
    except ValueError as exc:
        return _json_error(str(exc), 400)  # uygulama-yazımı mesaj, sızıntı yok
    try:
        with blocking_concurrency_slot():
            return jsonify(
                adapter.request(endpoint, current_user.id, params=params))
    except BlockingConcurrencyLimit:
        return _overload_response()
    except Exception:
        current_app.logger.warning("[WEARABLE] WHOOP %s isteği başarısız",
                                   resource, exc_info=True)
        return _json_error("WHOOP isteği başarısız.", 502)
