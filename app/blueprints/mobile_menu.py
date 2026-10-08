"""Bearer-authenticated native restaurant-menu analysis (LP15-C).

    POST /api/v1/nutrition/menu/analyze   {"url": "https://..."}

Attached to the existing `mobile_api` blueprint, so the `/api/v1` surface, the
`Cache-Control: no-store` policy, the 413/429 handlers, the error envelope and
the approved-route gate in tests/test_mobile_auth_feature_gate.py all stay
single-sourced.

A transport over the web menu flow's own authorities, never a second menu
analyzer: `menu_analysis.acquire_menu` (the LP15-B1 credential-free fetch
boundary, unchanged) then `menu_analysis.analyze_menu_text` (extraction, macro
resolution, canonical targets and score), both shared with
`/api/proxy/scan-menu` + `/api/menu/analyze`. What this route adds:

  * the owner is `g.mobile_user`, nothing else; the body is the closed key set
    {"url"} and the URL must be HTTPS (`mobile_menu.parse_request`);
  * scan and analysis run server-side in ONE request, so the client never
    relays remote menu text (and so can never substitute it);
  * two sequential capacity phases — the scrape permit is released before the
    AI permit is taken, never both at once (INF-5 / thread reserve);
  * the web per-route ceilings (SCRAPE + AI + BEDROCK), keyed on the Bearer
    owner and checked up front, so a rejected request does no network work;
  * typed native errors (never an auth-shaped answer for a menu fault) and a
    bounded DTO without the fetched body.

Read-only: no MealLog / NutritionPlan / CustomMeal write. See
docs/LP15_C_NATIVE_MENU_ANALYSIS.md.
"""
from flask import current_app, g, jsonify, request
from flask_limiter.errors import RateLimitExceeded

from app.blueprints.mobile_api import bp, mobile_error
from app.config import AI_RATELIMIT, BEDROCK_RATELIMIT, SCRAPE_RATELIMIT
from app.extensions import db, limiter
from app.mobile_auth_middleware import require_mobile_auth
from app.observability import current_request_id
from app.services import menu_analysis, mobile_menu
from app.services.ai_gate import (
    BlockingConcurrencyLimit, blocking_concurrency_slot, blocking_scrape_slot,
)


def _owner_key():
    return str(g.mobile_user.id)


def _check_rate_limits():
    """The web scan + analyze ceilings, keyed on the Bearer owner.

    Checked in the view so a rejection is a typed menu 429, not the
    blueprint's auth-flavoured `AUTH_RATE_LIMITED`.
    """
    with limiter.limit(SCRAPE_RATELIMIT, key_func=_owner_key):
        with limiter.limit(AI_RATELIMIT, key_func=_owner_key):
            with limiter.limit(BEDROCK_RATELIMIT, key_func=_owner_key):
                return


def _fail(error, event):
    # Fixed event + error code/type + request id + counts. Never the URL, the
    # remote body, a dish/category name, model output or account identity.
    log = current_app.logger.warning if error.status >= 500 else current_app.logger.info
    log("mobile_menu event=%s code=%s request_id=%s", event, error.code,
        current_request_id())
    retry_after = 15 if error is mobile_menu.MENU_ANALYSIS_BUSY else None
    return mobile_error(error.code, error.message, error.status,
                        error.retryable, retry_after=retry_after)


def _unexpected(error, phase):
    try:
        db.session.rollback()
    except Exception:
        pass
    current_app.logger.error(
        "mobile_menu event=%s_failed error_type=%s request_id=%s",
        phase, type(error).__name__, current_request_id())
    native = mobile_menu.MENU_ANALYSIS_FAILED
    return mobile_error(native.code, native.message, native.status, native.retryable)


@bp.post("/nutrition/menu/analyze")
@require_mobile_auth
def nutrition_menu_analyze():
    try:
        url = mobile_menu.parse_request(request.get_json(silent=True))
    except mobile_menu.MenuHttpsRequired:
        return _fail(mobile_menu.MENU_HTTPS_REQUIRED, "request_rejected")
    except mobile_menu.InvalidMenuUrl:
        return _fail(mobile_menu.INVALID_MENU_URL, "request_rejected")
    except mobile_menu.InvalidMenuRequest:
        return _fail(mobile_menu.INVALID_MENU_REQUEST, "request_rejected")

    try:
        _check_rate_limits()
    except RateLimitExceeded as error:
        native = mobile_menu.MENU_ANALYSIS_RATE_LIMITED
        return mobile_error(native.code, native.message, native.status,
                            native.retryable,
                            retry_after=error.limit.limit.get_expiry())

    owner = g.mobile_user

    # Phase 1 — network acquisition, under the scrape permit only.
    try:
        with blocking_scrape_slot():
            scan = menu_analysis.acquire_menu(url, https_only=True)
    except BlockingConcurrencyLimit:
        return _fail(mobile_menu.MENU_ANALYSIS_BUSY, "scrape_busy")
    except menu_analysis.MenuAcquisitionError as error:
        return _fail(mobile_menu.acquisition_failure(error), "acquisition_rejected")
    except Exception as error:
        return _unexpected(error, "acquisition")

    # Phase 2 — extraction/estimation/scoring, under the AI permit only.
    try:
        with blocking_concurrency_slot():
            analysis = menu_analysis.analyze_menu_text(
                owner.id, **menu_analysis.analysis_request_from_scan(scan))
        payload = mobile_menu.project(
            scan, analysis, secret=current_app.config["SECRET_KEY"],
            user_id=owner.id)
    except BlockingConcurrencyLimit:
        return _fail(mobile_menu.MENU_ANALYSIS_BUSY, "analysis_busy")
    except menu_analysis.MenuAnalysisError as error:
        return _fail(mobile_menu.analysis_failure(error), "analysis_rejected")
    except Exception as error:
        return _unexpected(error, "analysis")

    body = payload["menu_analysis"]
    current_app.logger.info(
        "mobile_menu event=analyzed request_id=%s categories=%d items=%d",
        current_request_id(), len(body["categories"]), body["item_count"])
    return jsonify(payload)
