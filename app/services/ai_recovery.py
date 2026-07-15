"""Sprint 4 WS9 — AI hata kurtarma + WS7 arıza soğuması.

Kurtarma merdiveni (ağır sağlayıcı çağrıları için, `ai._heavy_chat`'e bağlı):

    geçici hata → sınırlı jitter'lı yeniden deneme → son-iyi (last-good) Redis
    yanıtı → dostça i18n hata (çağıranın mevcut fallback'i)

Yalnızca GEÇİCİ (transient) hatalar yeniden denenir: `TransientAIError` —
rate-limit / timeout / bağlantı. Kalıcı hatalar (kötü istek, şema) anında
yükselir; boşuna beklenmez. Son-iyi yanıt her başarılı çağrıda yazılır ve her
iki sağlayıcı da düştüğünde bayat-ama-gerçek bir yanıt döndürür.

WS7 arıza soğuması: bir kullanıcıda ardışık AI hataları eşiği aşınca (Redis
NX anahtarı, hooks.py rollover-throttle deseni) sonraki /ask* istekleri kısa
süre 429+Retry-After alır — sağlayıcı gerçekten düştüğünde pahalı tekrarları
keser. Redis yoksa (lokal/test) soğuma no-op'tur.

Sağlayıcı istisna metni ASLA kullanıcıya sızmaz; her şey loglanır (+Sentry).
"""
import hashlib
import json
import logging
import random
import time

from app.config import (
    AI_FAILURE_COOLDOWN_SECONDS,
    AI_FAILURE_THRESHOLD,
    AI_LASTGOOD_TTL_SECONDS,
    AI_RECOVERY_ENABLED,
    AI_RETRY_ATTEMPTS,
    AI_RETRY_BASE_DELAY,
    AI_RETRY_MAX_DELAY,
)

_log = logging.getLogger(__name__)


class TransientAIError(RuntimeError):
    """Geçici (yeniden denenebilir) sağlayıcı hatası: rate-limit / timeout /
    bağlantı. RuntimeError'dan türer → mevcut `except RuntimeError` yakalayıcıları
    ve dostça-metin sözleşmesi bozulmaz; kurtarma katmanı bunu retry sinyali sayar."""


_FAILSTREAK_PREFIX = "ai:failstreak:"
_COOLDOWN_PREFIX = "ai:cooldown:"


def _get_redis():
    """redis_client'ı çağrı anında tembel çöz (import döngüsü + test monkeypatch)."""
    try:
        from app.extensions import redis_client
        return redis_client
    except Exception:
        return None


def _sleep(seconds):
    """Yeniden-deneme beklemesi — test dikişi (monkeypatch ile no-op yapılır)."""
    time.sleep(seconds)


def _backoff_delay(attempt):
    """Üstel geri çekilme + tam jitter, tavana clamp'li. attempt 0-tabanlı."""
    base = AI_RETRY_BASE_DELAY * (2 ** attempt)
    capped = min(base, AI_RETRY_MAX_DELAY)
    # Tam jitter: [0.5*capped, capped] — thundering-herd'i dağıtır ama en az
    # yarım gecikmeyi korur.
    return capped * (0.5 + random.random() * 0.5)


def call_with_recovery(fn, *, feature, retryable=(TransientAIError,), attempts=None):
    """`fn`'i çağır; `retryable` sınıfından hata gelirse sınırlı jitter'lı geri
    çekilmeyle yeniden dene. `retryable` DIŞI hata anında yükselir. Denemeler
    tükenirse son hata yükselir (çağıran son-iyi/fallback'e karar verir).

    AI_RECOVERY_ENABLED=0 iken tek deneme (retry devre dışı)."""
    total = attempts if attempts is not None else AI_RETRY_ATTEMPTS
    if not AI_RECOVERY_ENABLED:
        total = 1
    total = max(1, total)
    for i in range(total):
        try:
            return fn()
        except retryable as e:
            if i >= total - 1:
                raise
            delay = _backoff_delay(i)
            _log.warning(
                "[AI RECOVERY] %s: gecici hata (deneme %d/%d), %.2fs sonra yeniden: %s: %s",
                feature, i + 1, total, delay, type(e).__name__, e)
            _sleep(delay)
    # Ulaşılmaz (döngü ya döner ya yükseltir); tamamlık için:
    raise RuntimeError("call_with_recovery: beklenmeyen döngü çıkışı")


# ── Son-iyi (last-good) yanıt önbelleği ────────────────────────────────────

def lastgood_key(feature, *parts):
    """`ai:lastgood:<feature>:v1:<sha256(parts)>` — parts kararlı JSON'a serileşir."""
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return f"ai:lastgood:{feature}:v1:{digest}"


def remember_last_good(key, value):
    """Başarılı yanıtı son-iyi olarak sakla (SETEX). Boş/str-olmayan değer, Redis
    yok veya hata → sessiz no-op."""
    if not key or not isinstance(value, str) or not value.strip():
        return
    r = _get_redis()
    if r is None:
        return
    try:
        r.setex(key, AI_LASTGOOD_TTL_SECONDS, value)
    except Exception as e:
        _log.warning("[AI RECOVERY] son-iyi yazimi basarisiz: %s: %s", type(e).__name__, e)


def recall_last_good(key):
    """Son-iyi yanıtı oku. Yok / Redis yok / hata → None."""
    if not key:
        return None
    r = _get_redis()
    if r is None:
        return None
    try:
        return r.get(key)
    except Exception as e:
        _log.warning("[AI RECOVERY] son-iyi okumasi basarisiz: %s: %s", type(e).__name__, e)
        return None


# ── WS7: kullanıcı-başı arıza soğuması ─────────────────────────────────────

def record_ai_failure(user_id):
    """Kullanıcıda bir AI hatası kaydet. Ardışık hata sayısı eşiği aşınca soğuma
    anahtarını NX ile kur. Redis yok / hata → no-op (soğuma yalnızca Redis'le).
    Sayaç penceresi kendini süreyle siler ki geçmiş hatalar sonsuza dek birikmesin."""
    r = _get_redis()
    if r is None:
        return
    streak_key = f"{_FAILSTREAK_PREFIX}{user_id}"
    try:
        streak = r.incr(streak_key)
        # Pencere: soğumanın en az iki katı — sağlayıcı toparlarsa sayaç doğal söner.
        r.expire(streak_key, max(AI_FAILURE_COOLDOWN_SECONDS * 2, 120))
        if streak >= AI_FAILURE_THRESHOLD:
            r.set(f"{_COOLDOWN_PREFIX}{user_id}", "1", nx=True,
                  ex=AI_FAILURE_COOLDOWN_SECONDS)
    except Exception as e:
        _log.warning("[AI RECOVERY] ariza kaydi basarisiz: %s: %s", type(e).__name__, e)


def clear_ai_failures(user_id):
    """İlk başarıda ardışık-hata sayacını sıfırla (soğuma anahtarına dokunmaz —
    o kendi TTL'iyle söner). Redis yok / hata → no-op."""
    r = _get_redis()
    if r is None:
        return
    try:
        r.delete(f"{_FAILSTREAK_PREFIX}{user_id}")
    except Exception as e:
        _log.warning("[AI RECOVERY] sayac sifirlama basarisiz: %s: %s", type(e).__name__, e)


def ai_cooldown_remaining(user_id):
    """Kullanıcı soğumadaysa kalan saniye (>0), değilse 0. Redis yok / hata → 0
    (fail-open: soğuma altyapısı yoksa istek engellenmez)."""
    r = _get_redis()
    if r is None:
        return 0
    try:
        ttl = r.ttl(f"{_COOLDOWN_PREFIX}{user_id}")
    except Exception as e:
        _log.warning("[AI RECOVERY] soguma sorgusu basarisiz: %s: %s", type(e).__name__, e)
        return 0
    # redis-py ttl: -2 anahtar yok, -1 TTL yok. Yalnızca pozitif kalan anlamlı.
    return ttl if isinstance(ttl, int) and ttl > 0 else 0
