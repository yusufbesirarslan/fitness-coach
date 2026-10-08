"""Native menu-analysis contract (LP15-C): intake, error vocabulary, projection.

An ADAPTER over `app.services.menu_analysis`, never a second menu authority.
The route (`app/blueprints/mobile_menu.py`) owns transport, gates and limits;
this module owns three pure things:

* `parse_request` — the closed body `{"url": "https://..."}`. Native intake is
  HTTPS-only: `http://` is refused here, before the shared HTTP/HTTPS fetcher
  is ever called (LP15-B1 contract). Every other key — menu text, macros,
  account identity — is refused, not ignored.
* `acquisition_failure` / `analysis_failure` — the closed native error table:
  canonical failure reason → (code, HTTP status, retryable). Codes come from
  the reason, never from an exception message.
* `project` — the bounded DTO. Remote menu body, headings, framework state,
  crawl diagnostics and fetched URLs never reach it; the menu's own dish and
  category names are passed through untranslated (length-bounded only), and
  unknown nutrition is `null`, never 0.

Read-only. Nothing here (or in the analysis it projects) writes `MealLog`,
`NutritionPlan`, `CustomMeal` or `CustomMealItem`. `item_id` is an opaque,
owner-bound reference for a FUTURE confirmation flow (LP15-D) — it is not a
write authority and nothing resolves it in LP15-C.

Pure: stdlib + `menu_remote.validate_url` (itself pure). No Flask, no ORM.
"""
import base64
import hashlib
import hmac
from urllib.parse import urlsplit

from app.services import menu_remote

CONTRACT_VERSION = 1
MAX_URL_CHARS = 2048
MAX_NAME_CHARS = 200
MAX_CATEGORY_CHARS = 120
MAX_TITLE_CHARS = 256

_REQUEST_KEYS = frozenset({"url"})


class InvalidMenuRequest(ValueError):
    """Body is not exactly `{"url": <string>}`."""


class InvalidMenuUrl(ValueError):
    """The URL is malformed, carries userinfo or fails canonical admission."""


class MenuHttpsRequired(ValueError):
    """The URL is plain `http://`; native intake admits HTTPS only."""


def parse_request(data):
    """Return the admitted HTTPS URL, or raise one of the three intake errors."""
    if not isinstance(data, dict) or set(data) != _REQUEST_KEYS:
        raise InvalidMenuRequest()
    url = data["url"]
    if not isinstance(url, str):
        raise InvalidMenuRequest()
    url = url.strip()
    if not url or len(url) > MAX_URL_CHARS:
        raise InvalidMenuUrl()
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        has_userinfo = (parts.username is not None or parts.password is not None
                        or "@" in parts.netloc)
    except ValueError:
        raise InvalidMenuUrl() from None
    if scheme == "http":
        raise MenuHttpsRequired()
    if scheme != "https" or has_userinfo:
        raise InvalidMenuUrl()
    try:
        # The same canonical admission the fetch boundary re-applies per hop.
        menu_remote.validate_url(url)
    except ValueError:
        raise InvalidMenuUrl() from None
    return url


# -- Errors --------------------------------------------------------------------

class NativeMenuError:
    __slots__ = ("code", "status", "retryable", "message")

    def __init__(self, code, status, retryable, message):
        self.code = code
        self.status = status
        self.retryable = retryable
        self.message = message


INVALID_MENU_REQUEST = NativeMenuError(
    "INVALID_MENU_REQUEST", 400, False, "Invalid menu analysis request.")
INVALID_MENU_URL = NativeMenuError(
    "INVALID_MENU_URL", 400, False, "The menu link is not valid.")
MENU_HTTPS_REQUIRED = NativeMenuError(
    "MENU_HTTPS_REQUIRED", 400, False, "The menu link must use HTTPS.")
MENU_HTTPS_DOWNGRADE = NativeMenuError(
    "MENU_HTTPS_REQUIRED", 422, False, "The menu link redirected away from HTTPS.")
MENU_DESTINATION_BLOCKED = NativeMenuError(
    "MENU_DESTINATION_BLOCKED", 422, False, "The menu link cannot be opened.")
MENU_FETCH_TIMEOUT = NativeMenuError(
    "MENU_FETCH_TIMEOUT", 504, True, "The menu site did not respond in time.")
MENU_FETCH_FAILED = NativeMenuError(
    "MENU_FETCH_FAILED", 502, True, "The menu could not be retrieved.")
MENU_FETCH_REFUSED = NativeMenuError(
    "MENU_FETCH_FAILED", 502, False, "The menu could not be retrieved.")
MENU_UNSUPPORTED_MEDIA = NativeMenuError(
    "MENU_UNSUPPORTED_MEDIA", 422, False, "This menu format is not supported.")
MENU_CONTENT_TOO_LARGE = NativeMenuError(
    "MENU_CONTENT_TOO_LARGE", 422, False, "The menu page is too large to analyze.")
MENU_PARSE_FAILED = NativeMenuError(
    "MENU_PARSE_FAILED", 422, False, "No menu items could be read from this page.")
MENU_PROFILE_INCOMPLETE = NativeMenuError(
    "MENU_PROFILE_INCOMPLETE", 409, False,
    "A daily calorie target is required for menu analysis.")
MENU_ANALYSIS_FAILED = NativeMenuError(
    "MENU_ANALYSIS_FAILED", 503, True, "Menu analysis is temporarily unavailable.")
MENU_ANALYSIS_BUSY = NativeMenuError(
    "MENU_ANALYSIS_BUSY", 503, True, "Menu analysis is busy. Try again shortly.")
MENU_ANALYSIS_RATE_LIMITED = NativeMenuError(
    "MENU_ANALYSIS_RATE_LIMITED", 429, True, "Too many menu analysis requests.")

# LP15-B1 boundary codes (menu_remote / menu_fetch / menu_parse) → native.
_BOUNDARY_CODES = {
    "MENU_URL_INVALID": MENU_DESTINATION_BLOCKED,  # a redirect target, not the input
    "MENU_DESTINATION_BLOCKED": MENU_DESTINATION_BLOCKED,
    "MENU_PEER_BLOCKED": MENU_DESTINATION_BLOCKED,
    "MENU_HTTPS_DOWNGRADE": MENU_HTTPS_DOWNGRADE,
    "MENU_DNS_FAILED": MENU_FETCH_FAILED,
    "MENU_REDIRECT_LIMIT": MENU_FETCH_REFUSED,
    "MENU_COOKIES_FORBIDDEN": MENU_FETCH_REFUSED,
    "MENU_FETCH_FAILED": MENU_FETCH_FAILED,
    "MENU_FETCH_TIMEOUT": MENU_FETCH_TIMEOUT,
    "MENU_DEADLINE": MENU_FETCH_TIMEOUT,
    "MENU_MEDIA_UNSUPPORTED": MENU_UNSUPPORTED_MEDIA,
    "MENU_ENCODING_UNSUPPORTED": MENU_UNSUPPORTED_MEDIA,
    "MENU_DRIVE_CONFIRMATION_UNSUPPORTED": MENU_UNSUPPORTED_MEDIA,
    "MENU_BODY_LIMIT": MENU_CONTENT_TOO_LARGE,
    "MENU_WORK_LIMIT": MENU_CONTENT_TOO_LARGE,
    "MENU_PARSE_LIMIT": MENU_CONTENT_TOO_LARGE,
}


def acquisition_failure(error):
    """Native error for a `menu_analysis.MenuAcquisitionError`."""
    reason, detail = error.reason, error.detail
    if reason == "URL_INVALID":
        return INVALID_MENU_URL
    if reason in ("FETCH_REJECTED", "PARSE_LIMIT"):
        return _BOUNDARY_CODES.get(detail, MENU_FETCH_REFUSED)
    if reason == "DRIVE":
        # Drive's own failures are historical web strings/JSON; only the
        # boundary codes among them are classifiable. Everything else (private
        # link, missing file, upstream status) is a non-retryable fetch failure.
        return _BOUNDARY_CODES.get(detail, MENU_FETCH_REFUSED)
    if reason == "TIMEOUT":
        return MENU_FETCH_TIMEOUT
    if reason == "CONNECTION":
        upstream = error.upstream_status
        if isinstance(upstream, int) and 400 <= upstream < 500:
            return MENU_FETCH_REFUSED
        return MENU_FETCH_FAILED
    if reason == "UNSUPPORTED_TYPE":
        return MENU_UNSUPPORTED_MEDIA
    if reason == "UNREADABLE":
        return MENU_PARSE_FAILED
    return MENU_FETCH_REFUSED


def analysis_failure(error):
    """Native error for a `menu_analysis.MenuAnalysisError`."""
    if error.reason == "PROFILE_DATA_MISSING":
        return MENU_PROFILE_INCOMPLETE
    return MENU_PARSE_FAILED


# -- Projection ----------------------------------------------------------------

# Canonical macro provenance tokens (`menu_analysis._MACRO_CONFIDENCE`).
ESTIMATE_SOURCES = frozenset({
    "fatsecret_serving", "cache", "fatsecret_scaled", "llm_stated_grams", "llm",
    "fatsecret_scaled_fallback",
})
# Canonical `nutrition_pipeline.score_compatibility` vocabulary.
FIT_FLAGS = frozenset({
    "high_protein", "low_fat", "fits_calorie_budget", "low_protein_food"})
FIT_WARNINGS = {
    "Exceeds daily budget limit": "exceeds_daily_budget",
    "Approaching calorie budget": "approaching_calorie_budget",
    "Approaching fat budget": "approaching_fat_budget",
    "High carbohydrate load": "high_carbohydrate_load",
}
SOURCE_KINDS = {"web_scraper": "web_page", "google_drive": "google_drive"}

_ITEM_ID_INFO = b"axisai/mobile-menu/item-id/v1"
_ITEM_ID_BYTES = 18


def item_id(secret, user_id, category, name):
    """Opaque, owner-bound reference to one analyzed item (no persistence)."""
    material = secret.encode("utf-8") if isinstance(secret, str) else bytes(secret)
    key = hmac.new(material, _ITEM_ID_INFO, hashlib.sha256).digest()
    # surrogatepass: model JSON may legally decode to a lone surrogate.
    message = f"{int(user_id)}\x00{category}\x00{name}".encode("utf-8", "surrogatepass")
    digest = hmac.new(key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:_ITEM_ID_BYTES]).decode("ascii")


def _macros(values):
    return {
        "energy_kcal": values["calories"],
        "protein_g": values["protein"],
        "carbohydrate_g": values["carbs"],
        "fat_g": values["fat"],
    }


def _item(secret, user_id, item):
    known = bool(item.get("has_macros"))
    source = item.get("macro_source")
    if known and source in ESTIMATE_SOURCES:
        nutrition = dict(_macros(item["macros"]), status="estimated")
        estimate = {"source": source, "confidence": item["confidence"]}
        fit = {
            "score": item["score"],
            "flags": [f for f in item.get("fit_flags", []) if f in FIT_FLAGS],
            "warnings": [FIT_WARNINGS[w] for w in item.get("fit_warnings", [])
                         if w in FIT_WARNINGS],
        }
    else:
        # Unknown stays unknown: no zero macros, no zero score.
        nutrition = {"status": "unknown", "energy_kcal": None, "protein_g": None,
                     "carbohydrate_g": None, "fat_g": None}
        estimate = {"source": None, "confidence": None}
        fit = {"score": None, "flags": [], "warnings": []}
    return {
        "item_id": item_id(secret, user_id, item["category"], item["name"]),
        "name": item["name"][:MAX_NAME_CHARS],
        "nutrition": nutrition,
        "estimate": estimate,
        "fit": fit,
    }


def _host(url):
    try:
        return urlsplit(url).hostname if isinstance(url, str) else None
    except ValueError:
        return None


def project(scan, analysis, *, secret, user_id):
    """The bounded native DTO for one (scan, analysis) pair."""
    categories = []
    seen = set()
    for category, items in analysis["categories"].items():
        projected = []
        for item in items:
            out = _item(secret, user_id, item)
            if out["item_id"] in seen:  # the same dish listed twice in a category
                continue
            seen.add(out["item_id"])
            projected.append(out)
        categories.append({"name": str(category)[:MAX_CATEGORY_CHARS],
                           "items": projected})
    picks = [item_id(secret, user_id, item["category"], item["name"])
             for item in analysis["coach_picks"]]
    kind = SOURCE_KINDS.get(scan.get("menu_source"), "unknown")
    # Only a web page has a title of its own; Drive's is a server label.
    title = ((scan.get("title") or "").strip()[:MAX_TITLE_CHARS] or None
             if kind == "web_page" else None)
    return {"menu_analysis": {
        "contract_version": CONTRACT_VERSION,
        "source": {
            "kind": kind,
            "host": _host(scan.get("source_url")),
            "title": title,
        },
        "day": {
            "target": _macros(analysis["target"]),
            "consumed": _macros(analysis["consumed"]),
            "remaining": _macros(analysis["remaining"]),
        },
        "categories": categories,
        "coach_pick_ids": list(dict.fromkeys(picks)),
        "item_count": sum(len(c["items"]) for c in categories),
    }}
