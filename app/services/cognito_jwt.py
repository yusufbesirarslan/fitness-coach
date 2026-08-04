"""Canonical Cognito JWT validation with matching-kid JWKS caching."""

import base64
import json
import logging
import os
import threading
import time
import urllib.request

from joserfc import jwt
from joserfc.errors import ExpiredTokenError, JoseError
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from app.config import COGNITO_APP_CLIENT_ID, COGNITO_REGION, COGNITO_USER_POOL_ID


_logger = logging.getLogger(__name__)
_ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
_JWKS_URL = f"{_ISSUER}/.well-known/jwks.json"
_jwks_cache = None

# Hardening PR4 — zorunlu JWKS tazelemesi TEK-UÇUŞ + soğuma penceresi altındadır.
#
# Önceden: önbellekte olmayan her `kid` KOŞULSUZ bir `force=True` tazeleme
# tetikliyordu; yani 5 sn'lik BLOKLAYICI bir ağ çağrısı. Bu çağrı hiçbir
# eşzamanlılık kapısının arkasında DEĞİLDİR ve thread rezervi aritmetiğinde
# SAYILMAZ. Uydurma `kid` taşıyan bir Bearer token'ı üretmek bedava olduğundan,
# kimliği doğrulanmamış bir istek akışı her istek başına bir web thread'ini 5 sn
# park ettirebiliyordu → 8 thread tükenir, /health kuyruğa girer, deploy health
# gate'i sahte rollback tetikler. Tam olarak A1/I1 rezervinin engellemek için var
# olduğu senaryo, ama auth sınırından.
#
# Şimdi: pencere başına EN FAZLA bir tazeleme, ve tazelemeyi bekleyen thread'ler
# kilidi aldıklarında ağ'a çıkmadan önce yeniden bakar. Bilinen sınır: gerçek bir
# anahtar rotasyonu bir tazelemeden hemen SONRA olursa, yeni `kid` soğuma
# penceresi kadar reddedilebilir. Cognito rotasyon sırasında eski anahtarla
# imzalamayı sürdürür ve pencere sınırlıdır; sınırsız thread park etmeye kıyasla
# bilinçli takas (docs/CAPACITY.md).
JWKS_FORCED_REFRESH_COOLDOWN_SECONDS = max(
    0.0, float(os.getenv("JWKS_FORCED_REFRESH_COOLDOWN_SECONDS", "60")))
_refresh_lock = threading.Lock()
_last_forced_refresh = None  # monotonic; None = bu süreçte hiç zorlanmadı


class TokenValidationError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _reset_cache():
    global _jwks_cache, _last_forced_refresh
    _jwks_cache = None
    _last_forced_refresh = None


def _load_jwks(force=False):
    """Return cached keys or atomically replace them after a complete fetch."""
    global _jwks_cache
    if _jwks_cache is not None and not force:
        return _jwks_cache
    try:
        with urllib.request.urlopen(_JWKS_URL, timeout=5) as response:
            data = json.loads(response.read().decode())
        fresh = KeySet.import_key_set(data)
    except Exception as exc:
        _logger.error("[COGNITO-JWT] JWKS unavailable: %s", type(exc).__name__)
        raise TokenValidationError("jwks_unavailable") from exc
    _jwks_cache = fresh
    return fresh


def _unverified_kid(token):
    """Read only the untrusted protected-header kid used for key selection."""
    try:
        protected = token.split(".", 1)[0]
        padding = "=" * (-len(protected) % 4)
        header = json.loads(base64.urlsafe_b64decode(protected + padding).decode())
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ValueError("missing kid")
        return kid
    except Exception as exc:
        raise TokenValidationError("malformed") from exc


def _key_by_kid(key_set, kid):
    if key_set is None:
        return None
    try:
        return key_set.get_by_kid(kid)
    except JoseError:
        return None


def _forced_refresh_for(kid):
    """Bilinmeyen bir `kid` için EN FAZLA bir tazeleme uçur, sonucu döndür.

    Kilit ağ çağrısını kapsar (tek-uçuş): aynı anda gelen N bilinmeyen-kid
    isteği N ayrı 5 sn'lik fetch yerine TEK fetch'i paylaşır. Kilidi bekleyen
    thread'ler önce önbelleğe yeniden bakar — kazanan thread anahtarı zaten
    getirdiyse ağ'a hiç çıkılmaz. Soğuma penceresi içindeyken tazeleme
    ATLANIR ve `None` dönülür (çağıran bunu kesin `invalid_key` reddine çevirir).
    """
    global _last_forced_refresh
    with _refresh_lock:
        # Zorlamasız `_load_jwks()` önbelleği döndürür; kilidi bekleyen thread'ler
        # böylece kazanan thread'in getirdiği TAZE seti görür ve ağ'a çıkmaz.
        key = _key_by_kid(_load_jwks(), kid)
        if key is not None:
            return key
        now = time.monotonic()
        if (_last_forced_refresh is not None
                and now - _last_forced_refresh
                < JWKS_FORCED_REFRESH_COOLDOWN_SECONDS):
            return None
        # Zaman damgası fetch'ten ÖNCE yazılır: JWKS ucu düşmüşse de soğuma
        # işler, yoksa her istek düşen bir uca 5 sn'lik yeni bir çağrı açardı.
        _last_forced_refresh = now
        return _key_by_kid(_load_jwks(force=True), kid)


def _select_key(token):
    kid = _unverified_kid(token)
    key = _key_by_kid(_load_jwks(), kid)
    if key is not None:
        return key
    key = _forced_refresh_for(kid)
    if key is None:
        raise TokenValidationError("invalid_key")
    return key


def _decode(token, key, leeway_seconds=0):
    decoded = jwt.decode(token, key, algorithms=["RS256"])
    JWTClaimsRegistry(
        leeway=int(leeway_seconds), exp={"essential": True}).validate(
            decoded.claims)
    return decoded.claims


def validate_token(token, expected_use, leeway_seconds=0):
    """Validate signature, expiry, issuer, audience/client, and token use."""
    try:
        claims = _decode(token, _select_key(token), leeway_seconds)
    except TokenValidationError:
        raise
    except ExpiredTokenError:
        raise TokenValidationError("expired")
    except JoseError:
        # A matching cached kid with an invalid signature is definitive. Only a
        # kid miss refreshes JWKS, inside _select_key.
        raise TokenValidationError("invalid_signature")
    except Exception:
        raise TokenValidationError("malformed")

    if claims.get("iss") != _ISSUER:
        raise TokenValidationError("wrong_issuer")
    if claims.get("token_use") != expected_use:
        raise TokenValidationError("wrong_use")
    audience = claims.get("aud") if expected_use == "id" else claims.get("client_id")
    if audience != COGNITO_APP_CLIENT_ID:
        raise TokenValidationError("wrong_audience")
    return dict(claims)
