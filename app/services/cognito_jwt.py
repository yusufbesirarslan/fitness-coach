"""Amazon Cognito JWT doğrulama — JWKS ile üretim-hazır imza/claim kontrolü.

initiate_auth'tan dönen token'lar TLS ile Cognito'dan gelse de, spec gereği
manuel güvenmeyiz: imza (RS256, JWKS), issuer, audience (id → aud / access →
client_id), exp ve token_use tam doğrulanır. JWKS bir kez çekilip süreç-boyu
önbelleklenir; bilinmeyen kid tek sefer yeniden çekmeyi tetikler.

L4: Uygulamadaki TEK JWT doğrulayıcı burasıdır. Eskiden cognito_service kendi
joserfc doğrulayıcısını ve AYRI bir JWKS önbelleğini taşıyordu; iki uygulama bir
güvenlik düzeltmesinin yalnızca birine inmesi riskini doğuruyordu. Artık
cognito_service._decode_claims buraya delege eder. Kütüphane joserfc'dir
(cognito_service'in zaten kullandığı); Authlib JOSE yolu, 2.0 uyumsuzluğu ve
deprecation uyarıları nedeniyle kaldırıldı.
"""
import json
import logging
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

# exp/nbf/iat zaman kontrolleri. exp ZORUNLU: süresiz token kabul edilmemeli.
_TIME_CLAIMS = JWTClaimsRegistry(exp={"essential": True})


class TokenValidationError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _reset_cache():
    global _jwks_cache
    _jwks_cache = None


def _load_jwks(force=False):
    """JWKS'i çek ve önbellekle. Ağ hatasında önbellek varsa onu kullan."""
    global _jwks_cache
    if _jwks_cache is not None and not force:
        return _jwks_cache
    try:
        with urllib.request.urlopen(_JWKS_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        _jwks_cache = KeySet.import_key_set(data)
    except Exception as e:  # ağ/parse hatası
        if _jwks_cache is not None:
            _logger.warning("[COGNITO-JWT] JWKS yenileme başarısız, önbellek kullanılıyor: %s", type(e).__name__)
            return _jwks_cache
        _logger.error("[COGNITO-JWT] JWKS çekilemedi: %s", type(e).__name__)
        # jwks_unavailable, "imza GEÇERSİZ" değil "imza DOĞRULANAMADI" demektir.
        # Çağıran (require_auth) bu ayrımı 503'e çevirir ve oturumu SİLMEZ (H1).
        raise TokenValidationError("jwks_unavailable")
    return _jwks_cache


def _decode(token, keyset):
    """İmzayı doğrula + exp/nbf/iat kontrolü. joserfc hataları yukarı çıkar."""
    decoded = jwt.decode(token, keyset, algorithms=["RS256"])
    _TIME_CLAIMS.validate(decoded.claims)
    return decoded.claims


def validate_token(token, expected_use):
    """Cognito JWT'yi tam doğrula. Başarılıysa claim dict döner; aksi halde
    TokenValidationError(reason) yükseltir. Token/JWT değerleri LOGLANMAZ."""
    try:
        claims = _decode(token, _load_jwks())
    except TokenValidationError:
        raise
    except ExpiredTokenError:
        raise TokenValidationError("expired")
    except JoseError:
        # bilinmeyen kid (anahtar rotasyonu) veya imza uyuşmazlığı olabilir
        # → JWKS'i bir kez yenile ve tekrar dene
        try:
            claims = _decode(token, _load_jwks(force=True))
        except TokenValidationError:
            raise
        except ExpiredTokenError:
            raise TokenValidationError("expired")
        except Exception:
            raise TokenValidationError("invalid_signature")
    except Exception:
        raise TokenValidationError("malformed")

    if claims.get("iss") != _ISSUER:
        raise TokenValidationError("wrong_issuer")
    if claims.get("token_use") != expected_use:
        raise TokenValidationError("wrong_use")
    aud = claims.get("aud") if expected_use == "id" else claims.get("client_id")
    if aud != COGNITO_APP_CLIENT_ID:
        raise TokenValidationError("wrong_audience")
    return dict(claims)
