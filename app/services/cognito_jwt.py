"""Canonical Cognito JWT validation with matching-kid JWKS caching."""

import base64
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


class TokenValidationError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _reset_cache():
    global _jwks_cache
    _jwks_cache = None


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


def _select_key(token):
    kid = _unverified_kid(token)
    cached = _load_jwks()
    try:
        key = cached.get_by_kid(kid)
    except JoseError:
        key = None
    if key is not None:
        return key
    fresh = _load_jwks(force=True)
    try:
        key = fresh.get_by_kid(kid)
    except JoseError:
        key = None
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
