"""Food Search release gate (founder decision 2026-11-18, supersedes #520).

Default closed. Reopening is limited to the native text-search journey:
``/api/v1`` search → servings → provider-backed LogFood, and only when the
``FITX_FOOD_SEARCH_ENABLED`` rollout flag is ``1`` on the host. The gate is
scoped by request endpoint, so turning the flag on does NOT reopen:

* barcode lookup (native or browser),
* the browser ``food`` blueprint and the browser diary builder,
* any other FatSecret caller (Coach, QR Menu, social suggestions, workers).

Those keep refusing at the HTTP/token layer exactly as under the V1
exclusion. Manual logging, diary reads/deletes and independent QR estimates
keep their own permissions and retention boundaries.
"""
from flask import current_app, has_app_context, has_request_context, jsonify, request

FLAG_KEY = "FITX_FOOD_SEARCH_ENABLED"

# TEST ONLY: the characterization fixture (tests/conftest.py
# ``food_search_reopened``) flips this to exercise dormant provider code on every
# surface. Production never sets it; release availability is FLAG_KEY.
FOOD_SEARCH_AVAILABLE = False

ERROR_CODE = "FOOD_SEARCH_UNAVAILABLE"
DISCOVERY_ENDPOINTS = frozenset({
    "mobile_api.nutrition_food_search",
    "mobile_api.nutrition_food_servings",
    "mobile_api.nutrition_food_barcode",
})
# The only request endpoints the release flag can reopen. Barcode is excluded
# on purpose; it needs its own entitlement/scope decision.
NATIVE_RELEASE_ENDPOINTS = frozenset({
    "mobile_api.nutrition_food_search",
    "mobile_api.nutrition_food_servings",
    "mobile_api.nutrition_log_food",
})


class FoodSearchExcluded(RuntimeError):
    pass


def released():
    """The host rollout flag. Says nothing about the current call's scope."""
    return has_app_context() and current_app.config.get(FLAG_KEY) is True


def _native_release_scope():
    return has_request_context() and request.endpoint in NATIVE_RELEASE_ENDPOINTS


def available():
    """Whether THIS call may use FatSecret search/serving/persistence."""
    if FOOD_SEARCH_AVAILABLE is True:  # test-only characterization override
        return True
    return released() and _native_release_scope()


def require_available():
    if not available():
        raise FoodSearchExcluded("Food Search is not available for this call")


def install(app):
    @app.before_request
    def launch_v1_food_search_boundary():
        # Close browser cache/discovery/compatibility routes as well as native
        # discovery, before auth, query processing, caches or any fallback AI.
        if not available() and (request.blueprint == "food" or
                                request.endpoint in DISCOVERY_ENDPOINTS):
            response = jsonify(error={
                "code": ERROR_CODE,
                "message": "Food Search is unavailable in this version. Log food manually.",
                "retryable": False,
            })
            response.status_code = 404
            response.headers["Cache-Control"] = "no-store"
            return response
        return None

    @app.context_processor
    def food_search_launch_context():
        # Browser surfaces stay closed under the release flag.
        return {"food_search_available": available()}
