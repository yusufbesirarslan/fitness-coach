"""Türkiye V1 Food Search exclusion (founder decision, issue #520).

Source-controlled, closed for every account. Environment variables, cached
credentials, consent, subscription and request parameters cannot reopen it.
Manual logging, existing diary reads/deletes and independent QR estimates keep
their own existing permissions and retention boundaries.
"""
from flask import jsonify, request

FOOD_SEARCH_AVAILABLE = False
ERROR_CODE = "FOOD_SEARCH_UNAVAILABLE"
DISCOVERY_ENDPOINTS = frozenset({
    "mobile_api.nutrition_food_search",
    "mobile_api.nutrition_food_servings",
    "mobile_api.nutrition_food_barcode",
})


class FoodSearchExcluded(RuntimeError):
    pass


def available():
    return FOOD_SEARCH_AVAILABLE is True


def require_available():
    if not available():
        raise FoodSearchExcluded("Food Search is excluded from V1")


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
        return {"food_search_available": available()}
