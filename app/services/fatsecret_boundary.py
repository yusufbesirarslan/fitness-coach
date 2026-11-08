"""Approved advice fallback. This grants no provider retention permission.

The current ledger has no server-attested nutrition input lineage. Nonempty
intake is therefore unavailable for advice, regardless of its discovery label.
New independent estimates use signed source envelopes, never the shared cache.
"""
import hashlib
import hmac
import json
import time

from flask import current_app

from app.models import MealLog
from app.services import foodcache
from app.timeutil import day_key

PROOF = "_independent_nutrition_v1"

# D-B2 (founder, 2026-11-08): new provider-backed diary / saved-meal
# persistence is HELD until written FatSecret retention permission is verified.
# Fail-closed code constant, deliberately not an environment flag: enabling
# requires a reviewed change that records the written permission reference.
PROVIDER_PERSISTENCE_PERMISSION_REF = None


def provider_persistence_allowed():
    ref = PROVIDER_PERSISTENCE_PERMISSION_REF
    return isinstance(ref, str) and bool(ref.strip())
CACHE_PREFIX = "food:independent-menu:v1:per_serving:"
CACHE_TTL = 3 * 24 * 3600


def intake_unproven(user_id):
    # Do not read/sum restricted facts, or interpret an incomplete safe subset
    # as the user's complete consumption. Empty is genuinely measured empty.
    return MealLog.query.with_entities(MealLog.id).filter_by(
        user_id=user_id, tarih=day_key()).first() is not None


def intake_unproven_between(user_id, first_day_key, last_day_key):
    """Same rule over an inclusive Istanbul day-key range (R-5 weekly nudges)."""
    return MealLog.query.with_entities(MealLog.id).filter(
        MealLog.user_id == user_id, MealLog.tarih >= first_day_key,
        MealLog.tarih <= last_day_key).first() is not None


def unavailable_macros():
    return dict.fromkeys(("calories", "protein", "carbs", "fat"))


NOTICE_KEY = "coach.nutrition_advice_unavailable_notice"


def notice(language="en"):
    # Model-facing; the single copy lives in the locale catalog so the
    # AI-free coach_handoff module can render it through app.i18n alone.
    from app.i18n import t
    return t(NOTICE_KEY, locale="en" if language == "en" else "tr")


def _sign(domain, owner, payload):
    secret = current_app.config["SECRET_KEY"]
    if isinstance(secret, str):
        secret = secret.encode()
    if not secret:
        raise RuntimeError("nutrition boundary requires signing key")
    data = json.dumps(["axisai/independent-nutrition/v1", domain, owner, payload],
                      sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode()
    return hmac.new(secret, data, hashlib.sha256).hexdigest()


def attest(domain, owner, payload):
    result = dict(payload)
    result[PROOF] = _sign(domain, owner, payload)
    return result


def verified(domain, owner, payload):
    if not isinstance(payload, dict) or owner is None:
        return False
    clean = {k: v for k, v in payload.items() if k != PROOF}
    signature = payload.get(PROOF)
    try:
        return (isinstance(signature, str) and signature.isascii()
                and hmac.compare_digest(signature, _sign(domain, owner, clean)))
    except (ValueError, TypeError):
        return False


def independent_food_search(query):
    # The existing fresh AI estimate path; neither FatSecret, the unverified
    # static table nor any mixed macro cache supplies its numeric inputs.
    from app.services.ai_nutrition import _food_search_llm
    return _food_search_llm(query) or []


def _key(name):
    return CACHE_PREFIX + hashlib.sha256(name.encode()).hexdigest()


def cached_estimates(names):
    hits = {}
    redis = foodcache._get_redis()
    keys = [_key(name) for name in names]
    try:
        values = redis.mget(keys) if redis is not None else []
    except Exception:
        values = []
    for name, key, raw in zip(names, keys, values or []):
        try:
            envelope = json.loads(raw) if raw else None
            if (verified("menu-cache", key, envelope)
                    and type(envelope.get("expires_at")) in (int, float)
                    and envelope["expires_at"] > time.time()
                    and isinstance(envelope.get("macros"), dict)):
                hits[name] = dict(envelope["macros"])
        except (ValueError, TypeError):
            pass
    return hits, [name for name in names if name not in hits]


def cache_estimates(values):
    # Only estimates produced from independent menu input this turn. Never
    # renew a generic macro_map/cache hit with a newly asserted source label.
    redis = foodcache._get_redis()
    if redis is None:
        return
    try:
        pipe = redis.pipeline(transaction=False)
        for name, macros in values.items():
            if macros.get("calories", 0) <= 0:
                continue
            key = _key(name)
            envelope = attest("menu-cache", key, {
                "source": "independent_menu_estimate", "macros": dict(macros),
                "expires_at": time.time() + CACHE_TTL})
            pipe.setex(key, CACHE_TTL, json.dumps(envelope))
        pipe.execute()
    except Exception:
        pass  # a cache failure never authorizes another source
