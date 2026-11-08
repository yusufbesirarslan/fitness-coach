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
CACHE_PREFIX = "food:independent-menu:v1:per_serving:"
CACHE_TTL = 3 * 24 * 3600


def intake_unproven(user_id):
    # Do not read/sum restricted facts, or interpret an incomplete safe subset
    # as the user's complete consumption. Empty is genuinely measured empty.
    return MealLog.query.with_entities(MealLog.id).filter_by(
        user_id=user_id, tarih=day_key()).first() is not None


def unavailable_macros():
    return dict.fromkeys(("calories", "protein", "carbs", "fat"))


def notice(language="en"):
    return ("Logged intake is unavailable for advice because its nutrition source "
            "is unverified. Do not infer consumed intake, remaining budgets or "
            "daily adherence, and do not substitute zero or a partial total. "
            "Independent training, hydration and nutrition questions remain available."
            if language == "en" else
            "Kayıtlı beslenme verilerinin kaynağı doğrulanamadığından bu verilerle "
            "kişisel değerlendirme yapılamıyor. Tüketim, kalan bütçe veya günlük "
            "uyum hesaplama; bilinmeyen değer yerine sıfır ya da kısmi toplam kullanma. "
            "Bağımsız antrenman, su takibi ve beslenme soruları kullanılabilir.")


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
