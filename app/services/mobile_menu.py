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
* `issue_item_proof` / `read_item_proof` — the signed confirmation proof of
  one LOGGABLE candidate (domain `axisai/mobile-menu-item-proof/v1`, the
  shared `nutrition_native.tokens` signed-payload construction): a 30-minute,
  owner-bound (through the MAC only), self-contained snapshot of exactly what
  LP15-D may later persist with `menu_estimated` provenance — no refetch, no
  Redis state, no model or provider call needed. Only candidates with a
  complete nutrition snapshot inside the canonical LogFood bounds get one;
  unknown stays unknown and unloggable.

Read-only and stateless. Nothing here (or in the analysis it projects) writes
`MealLog`, `NutritionPlan`, `CustomMeal` or `CustomMealItem`, stores a proof,
a selection or an idempotency key. LP15-D owns verification ordering, replay
protection and the write.

Pure: stdlib + `menu_remote.validate_url` + the pure token and LogFood value
modules. No Flask, no ORM.
"""
import secrets
import time
from decimal import Decimal
from urllib.parse import urlsplit

from app.services import menu_remote
from app.services.mobile_log_food.commands import ManualNutritionSnapshot
from app.services.nutrition_native import tokens

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

# -- Confirmation proof ----------------------------------------------------------

PROOF_DOMAIN = tokens.MENU_ITEM_PROOF
PROOF_VERSION = 1
PROOF_TTL_SECONDS = 30 * 60
PROOF_CLOCK_SKEW_SECONDS = 60
# Hard ceiling on one token. A normal proof is a few hundred characters; the
# ceiling is only reached by a pathological (escaped non-BMP) name, and such a
# candidate fails closed to "not loggable" instead of minting a huge token.
MAX_PROOF_CHARS = 2048
PERSISTED_SOURCE = "menu_estimated"
MAX_STATED_GRAMS = 5000
_ID_BYTES = 12  # 96 random bits → 16 base64url characters
_PROOF_KEYS = frozenset({"v", "aid", "cid", "iat", "exp", "name", "portion",
                         "nutrition", "estimated", "source", "confidence",
                         "loggable", "persist_as"})
_NUTRIENTS = ("energy_kcal", "protein_g", "carbohydrate_g", "fat_g")


class ProofTooLarge(ValueError):
    """The signed snapshot would exceed `MAX_PROOF_CHARS`."""


class InvalidItemProof(ValueError):
    """Forged, foreign, malformed, wrong-domain or wrong-version proof."""


class ItemProofExpired(InvalidItemProof):
    """A genuine proof past its lifetime."""


def new_identity():
    return secrets.token_urlsafe(_ID_BYTES)


def _portion(item):
    grams = item.get("stated_grams")
    if (isinstance(grams, (int, float)) and not isinstance(grams, bool)
            and 0 < grams <= MAX_STATED_GRAMS):
        grams = int(round(grams))
    else:
        grams = None
    # The canonical analysis estimates ONE restaurant serving of the dish.
    return {"basis": "serving", "quantity": 1, "stated_grams": grams}


def _macros(values):
    return {
        "energy_kcal": values["calories"],
        "protein_g": values["protein"],
        "carbohydrate_g": values["carbs"],
        "fat_g": values["fat"],
    }


def loggable_nutrition(item):
    """The complete, bounded nutrition snapshot of a candidate, or None.

    Complete = every nutrient is a known non-negative integer inside the
    canonical LogFood bounds (`ManualNutritionSnapshot`, the one manual bounds
    policy), with positive energy, from a canonical estimate source. Anything
    else is not truthfully persistable and stays unloggable — never zero-filled.
    """
    if not item.get("has_macros") or item.get("macro_source") not in ESTIMATE_SOURCES:
        return None
    raw = item.get("macros")
    if not isinstance(raw, dict):
        return None
    nutrition = {}
    for key, value in _macros({k: raw.get(k) for k in ("calories", "protein", "carbs", "fat")}).items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        nutrition[key] = value
    if nutrition["energy_kcal"] <= 0:
        return None
    try:
        ManualNutritionSnapshot(**{k: Decimal(v) for k, v in nutrition.items()})
    except ValueError:
        return None
    return nutrition


def issue_item_proof(secret, user_id, *, analysis_id, candidate_id, name,
                     portion, nutrition, source, confidence, now=None):
    """Sign one candidate's confirmation snapshot for its owner (stateless)."""
    issued = int(time.time() if now is None else now)
    token = tokens.sign_payload(secret, PROOF_DOMAIN, user_id, {
        "v": PROOF_VERSION,
        "aid": analysis_id,
        "cid": candidate_id,
        "iat": issued,
        "exp": issued + PROOF_TTL_SECONDS,
        "name": name,
        "portion": portion,
        "nutrition": nutrition,
        "estimated": True,
        "source": source,
        "confidence": confidence,
        "loggable": True,
        "persist_as": PERSISTED_SOURCE,
    })
    if len(token) > MAX_PROOF_CHARS:
        raise ProofTooLarge()
    return token


def read_item_proof(secret, user_id, token, now=None, *, enforce_expiry=True):
    """The signed snapshot of a genuine, unexpired proof of THIS owner.

    ``enforce_expiry=False`` defers ONLY expiry until canonical replay lookup.
    Signature, owner, structure, lifetime and future-issued checks still apply.

    The format half of the contract, so the issuer is tested against its own
    reader. LP15-D owns the route-level ordering around it (verification,
    replay protection, idempotency, the write).
    """
    try:
        payload = tokens.verify_payload(secret, PROOF_DOMAIN, user_id, token,
                                        max_length=MAX_PROOF_CHARS)
    except tokens.InvalidSignedToken:
        raise InvalidItemProof() from None
    if set(payload) != _PROOF_KEYS or type(payload["v"]) is not int or payload["v"] != PROOF_VERSION:
        raise InvalidItemProof()
    if (payload["loggable"] is not True or payload["estimated"] is not True
            or payload["persist_as"] != PERSISTED_SOURCE
            or not isinstance(payload["source"], str)
            or payload["source"] not in ESTIMATE_SOURCES):
        raise InvalidItemProof()
    name, portion, confidence = payload["name"], payload["portion"], payload["confidence"]
    if (not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_CHARS
            or not all(isinstance(payload[k], str) and 0 < len(payload[k]) <= 32
                       for k in ("aid", "cid"))
            or not isinstance(portion, dict)
            or set(portion) != {"basis", "quantity", "stated_grams"}
            or portion["basis"] != "serving"
            or type(portion["quantity"]) not in (int, float)
            or portion["quantity"] != 1
            or not (portion["stated_grams"] is None
                    or (isinstance(portion["stated_grams"], int)
                        and not isinstance(portion["stated_grams"], bool)
                        and 0 < portion["stated_grams"] <= MAX_STATED_GRAMS))
            or isinstance(confidence, bool)
            or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
        raise InvalidItemProof()
    issued, expires = payload["iat"], payload["exp"]
    if (isinstance(issued, bool) or isinstance(expires, bool)
            or not isinstance(issued, int) or not isinstance(expires, int)
            or expires - issued != PROOF_TTL_SECONDS):
        raise InvalidItemProof()
    nutrition = payload["nutrition"]
    if (not isinstance(nutrition, dict) or set(nutrition) != set(_NUTRIENTS)
            or loggable_nutrition({"has_macros": True, "macro_source": payload["source"],
                                   "macros": {"calories": nutrition["energy_kcal"],
                                              "protein": nutrition["protein_g"],
                                              "carbs": nutrition["carbohydrate_g"],
                                              "fat": nutrition["fat_g"]}}) != nutrition):
        raise InvalidItemProof()
    current = time.time() if now is None else now
    if issued > current + PROOF_CLOCK_SKEW_SECONDS:
        raise InvalidItemProof()
    if enforce_expiry:
        enforce_item_proof_expiry(expires, now=current)
    return payload


def enforce_item_proof_expiry(expires, now=None):
    """Expiry gate for an already authenticated snapshot, after replay lookup."""
    if (time.time() if now is None else now) >= expires:
        raise ItemProofExpired()


def _candidate(secret, user_id, analysis_id, item, now):
    candidate_id = new_identity()
    name = item["name"]
    portion = _portion(item)
    nutrition = loggable_nutrition(item)
    raw = item.get("macros") if isinstance(item.get("macros"), dict) else {}
    known = (item.get("has_macros") and item.get("macro_source") in ESTIMATE_SOURCES
             and all(isinstance(raw.get(k), (int, float)) and not isinstance(raw.get(k), bool)
                     for k in ("calories", "protein", "carbs", "fat")))
    token = None
    if nutrition is not None and len(name) <= MAX_NAME_CHARS:
        try:
            token = issue_item_proof(
                secret, user_id, analysis_id=analysis_id,
                candidate_id=candidate_id, name=name, portion=portion,
                nutrition=nutrition, source=item["macro_source"],
                confidence=item["confidence"], now=now)
        except ProofTooLarge:
            token = None
    if known:
        shown = dict(_macros(item["macros"]), status="estimated")
        estimate = {"source": item["macro_source"], "confidence": item["confidence"]}
        fit = {
            "score": item["score"],
            "flags": [f for f in item.get("fit_flags", []) if f in FIT_FLAGS],
            "warnings": [FIT_WARNINGS[w] for w in item.get("fit_warnings", [])
                         if w in FIT_WARNINGS],
        }
    else:
        # Unknown stays unknown: no zero macros, no zero score, no proof.
        shown = {"status": "unknown", "energy_kcal": None, "protein_g": None,
                 "carbohydrate_g": None, "fat_g": None}
        estimate = {"source": None, "confidence": None}
        fit = {"score": None, "flags": [], "warnings": []}
    return {
        "candidate_id": candidate_id,
        "name": name[:MAX_NAME_CHARS],
        "description": None,
        "portion": portion,
        "nutrition": shown,
        "estimated": True,
        "estimate": estimate,
        "fit": fit,
        "loggable": token is not None,
        "confirmation_token": token,
    }


def _host(url):
    try:
        return urlsplit(url).hostname if isinstance(url, str) else None
    except ValueError:
        return None


def project(scan, analysis, *, secret, user_id, now=None):
    """The bounded native DTO for one (scan, analysis) pair.

    Pure and stateless: identities are fresh random values, proofs are signed
    snapshots — nothing is stored, fetched or re-estimated.
    """
    now = int(time.time() if now is None else now)
    analysis_id = new_identity()
    categories = []
    by_key = {}
    for category, items in analysis["categories"].items():
        projected = []
        for item in items:
            key = (item["category"], item["name"])
            if key in by_key:  # the same dish listed twice in a category
                continue
            by_key[key] = candidate = _candidate(secret, user_id, analysis_id, item, now)
            projected.append(candidate)
        categories.append({"name": str(category)[:MAX_CATEGORY_CHARS],
                           "candidates": projected})
    picks = [by_key[(item["category"], item["name"])]["candidate_id"]
             for item in analysis["coach_picks"]
             if (item["category"], item["name"]) in by_key]
    kind = SOURCE_KINDS.get(scan.get("menu_source"), "unknown")
    # Only a web page has a title of its own; Drive's is a server label.
    title = ((scan.get("title") or "").strip()[:MAX_TITLE_CHARS] or None
             if kind == "web_page" else None)
    return {"menu_analysis": {
        "contract_version": CONTRACT_VERSION,
        "analysis_id": analysis_id,
        "issued_at": now,
        "expires_at": now + PROOF_TTL_SECONDS,
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
        "candidate_count": sum(len(c["candidates"]) for c in categories),
        "loggable_count": sum(1 for c in categories for i in c["candidates"]
                              if i["loggable"]),
    }}
