"""Sprint 4 WS5 — AI sonuç önbelleği (jenerik Redis yardımcısı).

Deterministik (veya deterministik-e yakın) LLM sonuçlarını Redis'te önbelleğe
alıp tekrar-hesabı azaltır: besin adı→EN normalizasyonu, makro tahmini, statik
öneriler. `foodcache.py` L2 desenini izler:
  • Redis yoksa/erişilemezse SESSİZCE no-op (lokal/test birebir eski davranış).
  • TÜM Redis hataları yutulur+loglanır — önbellek arızası hiçbir zaman asıl
    isteği düşürmez.
Redis prod'da 200mb `allkeys-lru`: bu girdiler tahliye edilebilir; burası
YALNIZCA önbellek, asla kalıcı durum değildir. Anahtar sözleşmesi projedeki
namespace desenini izler: `ai:cache:<feature>:v1:<sha256(payload)>`.
"""
import hashlib
import json
import logging

from app.config import (
    AI_CACHE_ENABLED,
    AI_CACHE_TTL_FOOD,
    AI_CACHE_TTL_MACRO,
    AI_CACHE_TTL_PROMPT,
    AI_CACHE_TTL_RECS,
)

_log = logging.getLogger(__name__)


# Özellik → TTL (saniye) eşlemesi. Bilinmeyen özellik _DEFAULT_TTL'e düşer.
_FEATURE_TTL = {
    "food_normalize": AI_CACHE_TTL_FOOD,
    "food_normalize_batch": AI_CACHE_TTL_FOOD,
    "macro": AI_CACHE_TTL_MACRO,
    "prompt": AI_CACHE_TTL_PROMPT,
    "recs": AI_CACHE_TTL_RECS,
}
_DEFAULT_TTL = AI_CACHE_TTL_PROMPT


def _get_redis():
    """app.extensions.redis_client'ı çağrı anında tembel çöz (import döngüsü ve
    test monkeypatch yüzeyi için — foodcache._get_redis ile aynı desen)."""
    try:
        from app.extensions import redis_client
        return redis_client
    except Exception:
        return None


def _key(feature, payload):
    """`ai:cache:<feature>:v1:<sha256>` — payload kararlı JSON'a serileştirilir
    (sort_keys) ki anahtar sözlük sırasından bağımsız olsun."""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"ai:cache:{feature}:v1:{digest}"


def cache_get(feature, payload):
    """Önbellek okuması. Miss / kapalı / Redis yok / hata → None (çağıran hesaplar).
    Değer JSON-çözülür; str/dict/list gibi JSON-uyumlu her tür döner."""
    if not AI_CACHE_ENABLED:
        return None
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.get(_key(feature, payload))
    except Exception as e:
        _log.warning("[AI CACHE] get failed (%s): %s: %s", feature, type(e).__name__, e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        # Bozuk/eski biçim girdi — miss gibi davran (çağıran yeniden hesaplar).
        return None


def cache_set(feature, payload, value, ttl=None):
    """Önbellek yazımı (SETEX). Kapalı / Redis yok / hata → sessiz no-op.
    `ttl` verilmezse özellik-başı varsayılan (_FEATURE_TTL) kullanılır."""
    if not AI_CACHE_ENABLED:
        return
    r = _get_redis()
    if r is None:
        return
    if ttl is None:
        ttl = _FEATURE_TTL.get(feature, _DEFAULT_TTL)
    try:
        r.setex(_key(feature, payload), ttl,
                json.dumps(value, ensure_ascii=False))
    except Exception as e:
        _log.warning("[AI CACHE] set failed (%s): %s: %s", feature, type(e).__name__, e)
