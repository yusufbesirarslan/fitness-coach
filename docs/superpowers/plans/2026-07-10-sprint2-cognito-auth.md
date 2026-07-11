# Sprint 2 — Cognito Login, JWT Validation & Session Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Authenticate login against AWS Cognito, validate the returned JWTs against Cognito JWKS, manage sessions with server-side per-session token storage, support refresh + GlobalSignOut, and put every protected endpoint behind a unified `@require_auth` middleware — without changing the frontend.

**Architecture:** Cognito is the credential authority; Flask-Login stays the session mechanism. Login validates the Cognito ID token via JWKS, then establishes the existing Flask-Login session and stores the access+refresh tokens (Fernet-encrypted) in a new `CognitoSession` DB row keyed by a random `cognito_sid` carried in the signed session cookie. A `@require_auth` decorator replaces `@login_required` on protected endpoints: it validates/refreshes the session-bound access token and invalidates the session on failure. Legacy (non-Cognito) users fall through to plain session auth until Sprint 3.

**Tech Stack:** Flask, Flask-Login, SQLAlchemy/Alembic, `authlib.jose` (JWKS/JWT validation), `cryptography` (Fernet), `boto3` (cognito-idp). No new dependencies — Authlib 1.7.2 and cryptography 48 are already in `requirements.txt`.

**Design spec:** `docs/superpowers/specs/2026-07-10-sprint2-cognito-auth-design.md`

## Global Constraints

- Do NOT change any frontend template, layout, or form. Do NOT introduce Hosted UI or redirect to Cognito. Backend API calls only; continue using `boto3`.
- English code, Turkish UI strings/comments (repo convention).
- Authentication identity is `cognito_sub`. `password_hash` must not be used for authentication; keep it functional for legacy users and mark obsolete auth code `# TODO(Sprint 3)`.
- Never log tokens, passwords, or JWTs — only exception types / error codes.
- Migrations are expand-only (additive). New migration `down_revision = 'd6e7f8a9b0c1'` (current committed head). If the untracked barcode migration `e7f8a9b0c1d2` is committed to the mainline separately, rebase this `down_revision` onto the then-current head to keep one linear chain.
- Cookies are already `HttpOnly` + `Secure` (prod) + `SameSite=Lax` (`app/config.py`); do not weaken them.
- Tests are hermetic (in-memory SQLite, no network). `COGNITO_ENABLED` defaults False in tests; enable per-test by monkeypatching. Cognito/JWKS/boto3 must always be stubbed — never hit real AWS.
- **Testability import rule:** modules that call `validate_token`, `session_store.*`, or `cognito_service.*` must import the *module* and call the attribute (e.g. `from app.services import cognito_jwt` then `cognito_jwt.validate_token(...)`), so a single `monkeypatch.setattr(cognito_jwt, "validate_token", ...)` patches all call sites — mirroring how existing tests patch `cognito_service.initiate_auth`.
- Run tests with: `python -m pytest <path> -v` (repo standard; see conftest hermetic env).

---

### Task 1: JWT validator (`app/services/cognito_jwt.py`)

Self-contained reusable JWKS-based validator. No app wiring yet.

**Files:**
- Create: `app/services/cognito_jwt.py`
- Test: `tests/test_cognito_jwt.py`

**Interfaces:**
- Consumes: `COGNITO_USER_POOL_ID`, `COGNITO_REGION`, `COGNITO_APP_CLIENT_ID` from `app.config`.
- Produces:
  - `validate_token(token: str, expected_use: str) -> dict` — returns validated claims; raises `TokenValidationError`.
  - `class TokenValidationError(Exception)` with `.reason: str` in {malformed, invalid_signature, expired, wrong_use, wrong_audience, wrong_issuer, jwks_unavailable}.
  - `_ISSUER: str`, `_JWKS_URL: str` (module constants).
  - `_reset_cache()` — test hook to clear the cached keyset.

- [ ] **Step 1: Write the failing tests**

Uses a locally-generated RSA keypair + a fake JWKS built from its public numbers. No network, no real Cognito.

```python
# tests/test_cognito_jwt.py
"""cognito_jwt JWKS doğrulayıcı testleri — yerel RSA anahtar çifti + sahte JWKS.
Ağ yok, gerçek Cognito yok: imza/issuer/audience/exp/token_use dallarını sabitler.

    python -m pytest tests/test_cognito_jwt.py -v
"""
import time
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from authlib.jose import jwt as jose_jwt, JsonWebKey

from app.services import cognito_jwt
from app.services.cognito_jwt import TokenValidationError


@pytest.fixture
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def signer(rsa_key, monkeypatch):
    """Yerel anahtarın public tarafını sahte JWKS olarak enjekte et; token üretici döndür."""
    pub = JsonWebKey.import_key(
        rsa_key.public_key(),
        {"kty": "RSA", "use": "sig", "kid": "test-kid", "alg": "RS256"},
    )
    keyset = JsonWebKey.import_key_set({"keys": [pub.as_dict()]})
    monkeypatch.setattr(cognito_jwt, "_load_jwks", lambda force=False: keyset)
    priv = JsonWebKey.import_key(rsa_key, {"kty": "RSA", "kid": "test-kid", "alg": "RS256"})

    def _make(claims):
        header = {"alg": "RS256", "kid": "test-kid"}
        return jose_jwt.encode(header, claims, priv).decode()

    return _make


def _claims(use="access", **over):
    now = int(time.time())
    base = {
        "iss": cognito_jwt._ISSUER,
        "sub": "sub-123",
        "token_use": use,
        "exp": now + 3600,
        "iat": now,
    }
    if use == "id":
        base["aud"] = "client-123"
    else:
        base["client_id"] = "client-123"
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _client_id(monkeypatch):
    monkeypatch.setattr(cognito_jwt, "COGNITO_APP_CLIENT_ID", "client-123")


def test_valid_access_token(signer):
    claims = cognito_jwt.validate_token(signer(_claims("access")), "access")
    assert claims["sub"] == "sub-123"


def test_valid_id_token(signer):
    claims = cognito_jwt.validate_token(signer(_claims("id")), "id")
    assert claims["aud"] == "client-123"


def test_expired_token_rejected(signer):
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(signer(_claims("access", exp=int(time.time()) - 10)), "access")
    assert e.value.reason == "expired"


def test_modified_signature_rejected(signer):
    tok = signer(_claims("access"))
    tampered = tok[:-3] + ("aaa" if tok[-3:] != "aaa" else "bbb")
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(tampered, "access")
    assert e.value.reason == "invalid_signature"


def test_wrong_token_use_rejected(signer):
    # access token doğrulanırken "id" beklenirse reddet.
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(signer(_claims("access")), "id")
    assert e.value.reason in ("wrong_use", "wrong_audience")


def test_wrong_audience_rejected(signer):
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(signer(_claims("id", aud="someone-else")), "id")
    assert e.value.reason == "wrong_audience"


def test_wrong_issuer_rejected(signer):
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token(signer(_claims("access", iss="https://evil.example")), "access")
    assert e.value.reason == "wrong_issuer"


def test_malformed_token_rejected(signer):
    with pytest.raises(TokenValidationError) as e:
        cognito_jwt.validate_token("not-a-jwt", "access")
    assert e.value.reason in ("malformed", "invalid_signature")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cognito_jwt.py -v`
Expected: FAIL (module `app.services.cognito_jwt` does not exist).

- [ ] **Step 3: Write the implementation**

```python
# app/services/cognito_jwt.py
"""Amazon Cognito JWT doğrulama — JWKS ile üretim-hazır imza/claim kontrolü.

initiate_auth'tan dönen token'lar TLS ile Cognito'dan gelse de, spec gereği
manuel güvenmeyiz: imza (RS256, JWKS), issuer, audience (id → aud / access →
client_id), exp ve token_use tam doğrulanır. JWKS bir kez çekilip süreç-boyu
önbelleklenir; bilinmeyen kid tek sefer yeniden çekmeyi tetikler.
"""
import json
import logging
import urllib.request

from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import ExpiredTokenError, JoseError

from app.config import COGNITO_APP_CLIENT_ID, COGNITO_REGION, COGNITO_USER_POOL_ID

_logger = logging.getLogger(__name__)

_ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"
_JWKS_URL = f"{_ISSUER}/.well-known/jwks.json"
_JWT = JsonWebToken(["RS256"])
_jwks_cache = None


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
        _jwks_cache = JsonWebKey.import_key_set(data)
    except Exception as e:  # ağ/parse hatası
        if _jwks_cache is not None:
            _logger.warning("[COGNITO-JWT] JWKS yenileme başarısız, önbellek kullanılıyor: %s", type(e).__name__)
            return _jwks_cache
        _logger.error("[COGNITO-JWT] JWKS çekilemedi: %s", type(e).__name__)
        raise TokenValidationError("jwks_unavailable")
    return _jwks_cache


def validate_token(token, expected_use):
    """Cognito JWT'yi tam doğrula. Başarılıysa claim dict döner; aksi halde
    TokenValidationError(reason) yükseltir. Token/JWT değerleri LOGLANMAZ."""
    try:
        claims = _JWT.decode(token, _load_jwks())
        claims.validate()  # exp/nbf/iat
    except TokenValidationError:
        raise
    except ExpiredTokenError:
        raise TokenValidationError("expired")
    except JoseError:
        # bilinmeyen kid olabilir → JWKS'i bir kez yenile ve tekrar dene
        try:
            claims = _JWT.decode(token, _load_jwks(force=True))
            claims.validate()
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cognito_jwt.py -v`
Expected: PASS (8 tests). If Authlib raises a different error class for a tampered signature (e.g. `BadSignatureError`), it still subclasses `JoseError` → caught. If `test_malformed_token_rejected` yields `invalid_signature` instead of `malformed`, that is allowed by the assertion.

- [ ] **Step 5: Commit**

```bash
git add app/services/cognito_jwt.py tests/test_cognito_jwt.py
git commit -m "Add Cognito JWKS JWT validator"
```

---

### Task 2: Cognito service token operations (`app/services/cognito_service.py`)

Add `authenticate`, `refresh_tokens`, `global_sign_out`; refactor `initiate_auth` to delegate; extend the error map. Must keep all existing `tests/test_cognito_idp.py` and `tests/test_auth.py` cognito tests green.

**Files:**
- Modify: `app/services/cognito_service.py`
- Test: `tests/test_cognito_service_tokens.py`

**Interfaces:**
- Consumes: existing `_get_client`, `_maybe_secret`, `_secret_hash`, `_decode_claims`, `_wrap`, `CognitoServiceError`.
- Produces:
  - `authenticate(username, password) -> {"tokens": {"access_token","id_token","refresh_token","expires_in"}, "claims": {...}}`
  - `refresh_tokens(refresh_token, cognito_username) -> {"access_token","id_token","expires_in"}`
  - `global_sign_out(access_token) -> None`
  - `initiate_auth(username, password) -> claims` (unchanged return; now delegates to `authenticate`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cognito_service_tokens.py
"""cognito_service token operasyonları — authenticate/refresh/global_sign_out.
boto3 istemcisi sahte; ağ yok.

    python -m pytest tests/test_cognito_service_tokens.py -v
"""
import base64
import json
import pytest

from app.services import cognito_service
from app.services.cognito_service import CognitoServiceError


class _FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeIdp:
    def __init__(self, *, raises=None, result=None):
        self.calls = []
        self.raises = raises
        self.result = result or {}

    def initiate_auth(self, **kw):
        self.calls.append(("initiate_auth", kw))
        if self.raises:
            raise self.raises
        return self.result

    def global_sign_out(self, **kw):
        self.calls.append(("global_sign_out", kw))
        if self.raises:
            raise self.raises
        return {}


def _use_fake(monkeypatch, fake):
    monkeypatch.setattr(cognito_service, "_get_client", lambda: fake)
    monkeypatch.setattr(cognito_service, "COGNITO_APP_CLIENT_ID", "client-123")
    monkeypatch.setattr(cognito_service, "COGNITO_CLIENT_SECRET", "")


def _id_token(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"hdr.{payload}.sig"


def test_authenticate_returns_tokens_and_claims(monkeypatch):
    fake = _FakeIdp(result={"AuthenticationResult": {
        "AccessToken": "acc", "IdToken": _id_token({"sub": "s1", "email": "a@b.co"}),
        "RefreshToken": "ref", "ExpiresIn": 3600}})
    _use_fake(monkeypatch, fake)
    out = cognito_service.authenticate("ali", "Sifre123")
    assert out["tokens"] == {"access_token": "acc", "id_token": _id_token({"sub": "s1", "email": "a@b.co"}),
                             "refresh_token": "ref", "expires_in": 3600}
    assert out["claims"]["sub"] == "s1"


def test_initiate_auth_still_returns_claims(monkeypatch):
    fake = _FakeIdp(result={"AuthenticationResult": {
        "AccessToken": "acc", "IdToken": _id_token({"sub": "s2"}), "RefreshToken": "r"}})
    _use_fake(monkeypatch, fake)
    claims = cognito_service.initiate_auth("ali", "Sifre123")
    assert claims["sub"] == "s2"


def test_authenticate_challenge_rejected(monkeypatch):
    _use_fake(monkeypatch, _FakeIdp(result={"ChallengeName": "SMS_MFA"}))
    with pytest.raises(CognitoServiceError):
        cognito_service.authenticate("ali", "Sifre123")


def test_refresh_tokens_returns_new_access(monkeypatch):
    fake = _FakeIdp(result={"AuthenticationResult": {"AccessToken": "newacc", "ExpiresIn": 3600}})
    _use_fake(monkeypatch, fake)
    out = cognito_service.refresh_tokens("refresh-tok", "ali")
    assert out["access_token"] == "newacc"
    name, kw = fake.calls[-1]
    assert kw["AuthFlow"] == "REFRESH_TOKEN_AUTH"
    assert kw["AuthParameters"]["REFRESH_TOKEN"] == "refresh-tok"


def test_refresh_tokens_failure_maps_error(monkeypatch):
    _use_fake(monkeypatch, _FakeIdp(raises=_FakeClientError("NotAuthorizedException")))
    with pytest.raises(CognitoServiceError):
        cognito_service.refresh_tokens("refresh-tok", "ali")


def test_global_sign_out_calls_client(monkeypatch):
    fake = _FakeIdp()
    _use_fake(monkeypatch, fake)
    cognito_service.global_sign_out("acc-token")
    assert fake.calls[-1][0] == "global_sign_out"
    assert fake.calls[-1][1]["AccessToken"] == "acc-token"


def test_password_reset_and_internal_error_mapped():
    assert "PasswordResetRequiredException" in cognito_service._ERROR_MESSAGES
    assert "InternalErrorException" in cognito_service._ERROR_MESSAGES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cognito_service_tokens.py -v`
Expected: FAIL (`authenticate` / `refresh_tokens` / `global_sign_out` not defined).

- [ ] **Step 3: Implement in `app/services/cognito_service.py`**

Add to `_ERROR_MESSAGES` (after the existing `TooManyRequestsException` entry):

```python
    "PasswordResetRequiredException": "Şifreni sıfırlaman gerekiyor. Lütfen şifre sıfırlama akışını kullan.",
    "InternalErrorException": "Sunucu hatası. Lütfen biraz sonra tekrar dene.",
```

Replace the existing `initiate_auth` function body with a delegation, and add the three new functions:

```python
def authenticate(username, password):
    """USER_PASSWORD_AUTH ile giriş. Başarılıysa ham token'ları (access/id/refresh/
    expires_in) VE çözülmüş id-token claim'lerini döndürür. Challenge/boş kimlik
    reddedilir (auth bypass koruması)."""
    params = _maybe_secret({"USERNAME": username, "PASSWORD": password}, username)
    if "SecretHash" in params:
        params["SECRET_HASH"] = params.pop("SecretHash")
    try:
        resp = _get_client().initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters=params,
        )
    except Exception as e:
        raise _wrap(e)
    if resp.get("ChallengeName"):
        raise CognitoServiceError("Ek doğrulama gerekiyor; bu akış desteklenmiyor.", "ChallengeRequired")
    auth = resp.get("AuthenticationResult") or {}
    id_token = auth.get("IdToken", "")
    claims = _decode_claims(id_token)
    if not claims.get("sub"):
        raise CognitoServiceError("Kimlik doğrulanamadı.", "NoIdentity")
    return {
        "tokens": {
            "access_token": auth.get("AccessToken", ""),
            "id_token": id_token,
            "refresh_token": auth.get("RefreshToken", ""),
            "expires_in": auth.get("ExpiresIn", 3600),
        },
        "claims": claims,
    }


def initiate_auth(username, password):
    """Geriye dönük uyum: yalnızca id-token claim'lerini döndürür (Sprint 1
    çağıranları/testleri için). Yeni giriş yolu authenticate() kullanır."""
    return authenticate(username, password)["claims"]


def refresh_tokens(refresh_token, cognito_username):
    """REFRESH_TOKEN_AUTH ile yeni access token al. SECRET_HASH (gizli client)
    kullanıcı ADIYLA üretilir. Başarısızsa CognitoServiceError."""
    params = {"REFRESH_TOKEN": refresh_token}
    sh = _secret_hash(cognito_username)
    if sh:
        params["SECRET_HASH"] = sh
    try:
        resp = _get_client().initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters=params,
        )
    except Exception as e:
        raise _wrap(e)
    auth = resp.get("AuthenticationResult") or {}
    new_access = auth.get("AccessToken", "")
    if not new_access:
        raise CognitoServiceError("Oturum yenilenemedi.", "RefreshFailed")
    return {"access_token": new_access, "id_token": auth.get("IdToken", ""),
            "expires_in": auth.get("ExpiresIn", 3600)}


def global_sign_out(access_token):
    """Cognito GlobalSignOut — kullanıcının TÜM refresh token'larını iptal eder."""
    try:
        _get_client().global_sign_out(AccessToken=access_token)
    except Exception as e:
        raise _wrap(e)
```

Delete the now-duplicated original `initiate_auth` body (the old one that did the boto3 call inline) — there must be exactly one `initiate_auth` (the delegating wrapper above).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_cognito_service_tokens.py tests/test_cognito_idp.py -v`
Expected: PASS (new file + all existing `test_cognito_idp.py` tests still green — `initiate_auth` behavior unchanged via delegation).

- [ ] **Step 5: Commit**

```bash
git add app/services/cognito_service.py tests/test_cognito_service_tokens.py
git commit -m "Add Cognito authenticate/refresh/global_sign_out"
```

---

### Task 3: `CognitoSession` model + token store (`app/services/session_store.py`)

**Files:**
- Modify: `app/models.py` (add `CognitoSession`)
- Create: `app/services/session_store.py`
- Modify: `app/config.py` (add `COGNITO_TOKEN_ENC_KEY`, `COGNITO_REFRESH_SKEW_SECONDS`)
- Test: `tests/test_session_store.py`

**Interfaces:**
- Consumes: `cognito_service.refresh_tokens` (from Task 2), `db` from `app.extensions`.
- Produces:
  - `class CognitoSession(db.Model)` — columns per spec.
  - `session_store.create(user, tokens: dict, cognito_username: str) -> str` (returns `session_id`).
  - `session_store.get(session_id) -> CognitoSession | None`
  - `session_store.get_valid_access_token(session_id) -> str` (refreshes on/near expiry; raises `SessionInvalid`)
  - `session_store.current_access_token(session_id) -> str | None` (decrypted stored token, no refresh; for logout)
  - `session_store.touch(session_id) -> None`
  - `session_store.delete(session_id) -> None`
  - `class SessionInvalid(Exception)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_store.py
"""CognitoSession token deposu — şifreleme round-trip, süre-dolumunda yenileme,
yenileme başarısızlığında geçersiz kılma. boto3 yok; refresh monkeypatch'lenir.

    python -m pytest tests/test_session_store.py -v
"""
from datetime import datetime, timedelta
import pytest

from app.extensions import db
from app.models import User, CognitoSession
from app.services import session_store, cognito_service
from app.services.session_store import SessionInvalid


@pytest.fixture
def cog_user(app):
    u = User(username="cg", email="cg@example.com", cognito_sub="sub-cg")
    db.session.add(u)
    db.session.commit()
    return u


def _tokens(exp=3600):
    return {"access_token": "acc-1", "id_token": "id-1", "refresh_token": "ref-1", "expires_in": exp}


def test_create_persists_encrypted_row(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    row = session_store.get(sid)
    assert row.user_id == cog_user.id
    assert row.cognito_username == "cg"
    # ham token DB'de düz metin OLMAMALI (şifreli saklanır).
    assert row.access_token != "acc-1"
    assert session_store.current_access_token(sid) == "acc-1"


def test_valid_token_returned_without_refresh(app, cog_user, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(cognito_service, "refresh_tokens", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    assert session_store.get_valid_access_token(sid) == "acc-1"
    assert called["n"] == 0  # yenileme yok


def test_expired_token_triggers_refresh(app, cog_user, monkeypatch):
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda ref, uname: {"access_token": "acc-2", "id_token": "", "expires_in": 3600})
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    # süreyi geçmişe çek
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    assert session_store.get_valid_access_token(sid) == "acc-2"


def test_refresh_failure_invalidates(app, cog_user, monkeypatch):
    def boom(ref, uname):
        raise cognito_service.CognitoServiceError("Oturum yenilenemedi.", "NotAuthorizedException")
    monkeypatch.setattr(cognito_service, "refresh_tokens", boom)
    sid = session_store.create(cog_user, _tokens(3600), "cg")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    with pytest.raises(SessionInvalid):
        session_store.get_valid_access_token(sid)
    assert session_store.get(sid) is None  # satır silindi


def test_delete_removes_row(app, cog_user):
    sid = session_store.create(cog_user, _tokens(), "cg")
    session_store.delete(sid)
    assert session_store.get(sid) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_session_store.py -v`
Expected: FAIL (`CognitoSession` / `session_store` not defined).

- [ ] **Step 3a: Add the model to `app/models.py`**

Append after the `UserSession` class (keep it near other session-ish models):

```python
class CognitoSession(db.Model):
    """Bir Flask-Login oturumuna bağlı Cognito token'ları (sunucu tarafı, şifreli).
    session_id çerezde taşınır; access/refresh token'lar Fernet ile şifreli saklanır.
    Bir giriş = bir satır → eşzamanlı oturumlar bağımsız yaşar."""
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(64), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    cognito_username = db.Column(db.String(80), nullable=False)
    access_token = db.Column(db.Text, nullable=False)    # Fernet ile şifreli
    refresh_token = db.Column(db.Text, nullable=False)   # Fernet ile şifreli
    access_token_exp = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index("uq_cognito_session_session_id", "session_id", unique=True),
    )
```

- [ ] **Step 3b: Add config to `app/config.py`**

After the Cognito block (`COGNITO_ENABLED = ...`):

```python
# Sprint 2: oturuma bağlı Cognito token'ları sunucu tarafında Fernet ile şifreli
# saklanır. Anahtar açıkça verilmezse SECRET_KEY'den türetilir (SECRET_KEY dönerse
# eski token'lar çözülemez → o kullanıcılar yeniden giriş yapar; kabul edilen maliyet).
COGNITO_TOKEN_ENC_KEY = os.getenv("COGNITO_TOKEN_ENC_KEY", "").strip()
# Access token bitmeden bu kadar saniye önce proaktif yenile (edge yarışları için).
COGNITO_REFRESH_SKEW_SECONDS = int(os.getenv("COGNITO_REFRESH_SKEW_SECONDS", "60"))
```

- [ ] **Step 3c: Implement `app/services/session_store.py`**

```python
# app/services/session_store.py
"""CognitoSession token deposu — Fernet şifreleme + süre-dolumunda yenileme.

Ham access/refresh token'lar ASLA düz metin saklanmaz/loglanmaz. Anahtar
COGNITO_TOKEN_ENC_KEY (geçerli Fernet anahtarı) verilmişse ondan, yoksa
SECRET_KEY'den deterministik türetilir.
"""
import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta

from cryptography.fernet import Fernet

from app.config import COGNITO_REFRESH_SKEW_SECONDS, COGNITO_TOKEN_ENC_KEY
from app.extensions import db
from app.models import CognitoSession
from app.services import cognito_service

_logger = logging.getLogger(__name__)
_fernet = None


class SessionInvalid(Exception):
    pass


def _get_fernet():
    global _fernet
    if _fernet is None:
        if COGNITO_TOKEN_ENC_KEY:
            key = COGNITO_TOKEN_ENC_KEY.encode()
        else:
            from flask import current_app
            secret = current_app.config["SECRET_KEY"].encode()
            key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
        _fernet = Fernet(key)
    return _fernet


def _enc(value):
    return _get_fernet().encrypt((value or "").encode()).decode()


def _dec(value):
    return _get_fernet().decrypt(value.encode()).decode()


def create(user, tokens, cognito_username):
    sid = secrets.token_urlsafe(32)
    exp = datetime.utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    row = CognitoSession(
        session_id=sid, user_id=user.id, cognito_username=cognito_username,
        access_token=_enc(tokens["access_token"]),
        refresh_token=_enc(tokens["refresh_token"]),
        access_token_exp=exp,
    )
    db.session.add(row)
    db.session.commit()
    return sid


def get(session_id):
    if not session_id:
        return None
    return CognitoSession.query.filter_by(session_id=session_id).first()


def current_access_token(session_id):
    row = get(session_id)
    return _dec(row.access_token) if row else None


def get_valid_access_token(session_id):
    row = get(session_id)
    if not row:
        raise SessionInvalid("no_session")
    skew = timedelta(seconds=COGNITO_REFRESH_SKEW_SECONDS)
    if row.access_token_exp and (row.access_token_exp - datetime.utcnow()) > skew:
        return _dec(row.access_token)
    # süresi dolmuş / dolmak üzere → yenile
    try:
        refreshed = cognito_service.refresh_tokens(_dec(row.refresh_token), row.cognito_username)
    except cognito_service.CognitoServiceError:
        delete(session_id)
        raise SessionInvalid("refresh_failed")
    row.access_token = _enc(refreshed["access_token"])
    row.access_token_exp = datetime.utcnow() + timedelta(seconds=int(refreshed.get("expires_in", 3600)))
    db.session.commit()
    return refreshed["access_token"]


def touch(session_id):
    row = get(session_id)
    if row:
        row.last_used_at = datetime.utcnow()
        db.session.commit()


def delete(session_id):
    row = get(session_id)
    if row:
        db.session.delete(row)
        db.session.commit()
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_session_store.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/models.py app/config.py app/services/session_store.py tests/test_session_store.py
git commit -m "Add CognitoSession model and encrypted token store"
```

---

### Task 4: Migration for `CognitoSession`

Expand-only additive migration so prod boot auto-applies it. Tests use `create_all` and don't need it, so this task is verified by inspection + a fresh-DB upgrade check.

**Files:**
- Create: `migrations/versions/aa11bb22cc33_add_cognito_session.py`

**Interfaces:**
- Consumes: current committed Alembic head `d6e7f8a9b0c1`.
- Produces: table `cognito_session` matching the model in Task 3 exactly (schema-drift guard compares model vs migration chain).

- [ ] **Step 1: Write the migration file**

```python
# migrations/versions/aa11bb22cc33_add_cognito_session.py
"""add cognito_session

Revision ID: aa11bb22cc33
Revises: d6e7f8a9b0c1
Create Date: 2026-07-10

Sprint 2: oturuma bağlı Cognito token'ları için sunucu tarafı tablo. Additive
(expand-only) — rollback kodu geri alsa da tablo kalır, eski kod onsuz çalışır.
"""
from alembic import op
import sqlalchemy as sa

revision = "aa11bb22cc33"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cognito_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("cognito_username", sa.String(length=80), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("access_token_exp", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_cognito_session_user_id", "cognito_session", ["user_id"])
    op.create_index("uq_cognito_session_session_id", "cognito_session", ["session_id"], unique=True)


def downgrade():
    op.drop_index("uq_cognito_session_session_id", table_name="cognito_session")
    op.drop_index("ix_cognito_session_user_id", table_name="cognito_session")
    op.drop_table("cognito_session")
```

- [ ] **Step 2: Verify the head is still `d6e7f8a9b0c1`**

Run: `git ls-files migrations/versions/ | xargs grep -l "down_revision = None\|^revision"` is not needed; instead confirm no *committed* migration has `down_revision = "d6e7f8a9b0c1"` other than this one:

Run: `grep -rl "d6e7f8a9b0c1" migrations/versions/`
Expected: only `d6e7f8a9b0c1_make_user_password_hash_nullable.py` (as its `revision`) and this new file (as its `down_revision`). If the untracked barcode migration `e7f8a9b0c1d2` is present on disk it also lists `d6e7f8a9b0c1` — that is unrelated uncommitted work; leave this migration chained to `d6e7f8a9b0c1` and note the coordination item in the handoff.

- [ ] **Step 3: Verify model/migration agreement on a fresh SQLite DB**

Run:
```bash
FITX_SKIP_DB_INIT=1 FLASK_DEBUG=1 DATABASE_URL="sqlite:///$(pwd)/_mig_check.db" \
  flask --app starter db upgrade aa11bb22cc33 && rm -f _mig_check.db
```
Expected: upgrade runs without error (table created). (If `flask db upgrade` errors on multiple heads because the untracked barcode file is on disk, temporarily move it aside for this check, then restore it.)

- [ ] **Step 4: Commit**

```bash
git add migrations/versions/aa11bb22cc33_add_cognito_session.py
git commit -m "Add cognito_session migration"
```

---

### Task 5: `@require_auth` middleware (`app/auth_middleware.py`)

**Files:**
- Create: `app/auth_middleware.py`
- Test: `tests/test_require_auth.py`

**Interfaces:**
- Consumes: `login_manager` (`app.extensions`), `cognito_jwt.validate_token`, `session_store.*`, Flask-Login `current_user`/`logout_user`.
- Produces: `require_auth(view)` decorator.

- [ ] **Step 1: Write the failing tests**

Tests mount a throwaway route decorated with `require_auth` on the real app, then drive it through the test client. `cognito_jwt.validate_token` and `session_store` refresh are stubbed.

```python
# tests/test_require_auth.py
"""@require_auth davranış testleri: anonim reddi, legacy geçişi, cognito geçerli,
süre-dolumunda yenileme, yenileme başarısızlığında geçersiz kılma.

    python -m pytest tests/test_require_auth.py -v
"""
from datetime import datetime, timedelta
import pytest

from app.extensions import db
from app.models import User
from app.services import session_store, cognito_jwt
from app.auth_middleware import require_auth


@pytest.fixture
def probe_route(app):
    """require_auth ile korunan geçici bir route ekle."""
    @app.route("/__probe")
    @require_auth
    def _probe():
        return "ok", 200
    app.url_map.update()  # route kaydını uygula
    return "/__probe"


@pytest.fixture
def legacy_user(app):
    u = User(username="leg", email="leg@example.com")
    u.set_password("Sifre123")
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture
def cognito_user(app):
    u = User(username="cog", email="cog@example.com", cognito_sub="sub-cog")
    db.session.add(u)
    db.session.commit()
    return u


def _login_session(client, user, sid=None):
    """Flask-Login oturumunu ve (varsa) cognito_sid'i doğrudan kur."""
    with client.session_transaction() as s:
        s["_user_id"] = str(user.id)
        if sid:
            s["cognito_sid"] = sid


def test_anonymous_redirected(client, probe_route):
    resp = client.get(probe_route)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_legacy_user_passes_through(client, probe_route, legacy_user):
    _login_session(client, legacy_user)
    assert client.get(probe_route).status_code == 200


def test_cognito_valid_token_allowed(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use: {"sub": "sub-cog"})
    sid = session_store.create(cognito_user, {"access_token": "a", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    _login_session(client, cognito_user, sid)
    assert client.get(probe_route).status_code == 200


def test_cognito_missing_session_row_invalidated(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use: {"sub": "sub-cog"})
    _login_session(client, cognito_user, "no-such-sid")
    resp = client.get(probe_route)
    assert resp.status_code == 302  # geçersiz → login'e


def test_cognito_expired_access_refreshed(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use: {"sub": "sub-cog"})
    from app.services import cognito_service
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda ref, uname: {"access_token": "fresh", "id_token": "", "expires_in": 3600})
    sid = session_store.create(cognito_user, {"access_token": "old", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    _login_session(client, cognito_user, sid)
    assert client.get(probe_route).status_code == 200


def test_cognito_dead_refresh_invalidated(client, probe_route, cognito_user, monkeypatch):
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use: {"sub": "sub-cog"})
    from app.services import cognito_service
    def boom(ref, uname):
        raise cognito_service.CognitoServiceError("x", "NotAuthorizedException")
    monkeypatch.setattr(cognito_service, "refresh_tokens", boom)
    sid = session_store.create(cognito_user, {"access_token": "old", "refresh_token": "r",
                                              "id_token": "i", "expires_in": 3600}, "cog")
    row = session_store.get(sid)
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    _login_session(client, cognito_user, sid)
    resp = client.get(probe_route)
    assert resp.status_code == 302
    assert session_store.get(sid) is None  # satır silindi
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_require_auth.py -v`
Expected: FAIL (`app.auth_middleware` does not exist).

- [ ] **Step 3: Implement `app/auth_middleware.py`**

```python
# app/auth_middleware.py
"""Birleşik kimlik doğrulama ara katmanı — @require_auth.

Flask-Login oturumunu KORUR (current_user, login-redirect UX değişmez) ve
üstüne Cognito access token doğrulaması/yenilemesi ekler. Legacy (cognito_sub'ı
olmayan) kullanıcılar yalnızca oturum kimliğiyle geçer (Sprint 3'e kadar geriye
uyum). Her korumalı endpoint bu dekoratörü kullanır.
"""
from functools import wraps

from flask import g, session
from flask_login import current_user, logout_user

from app.extensions import login_manager
from app.services import cognito_jwt, session_store


def _invalidate():
    sid = session.pop("cognito_sid", None)
    if sid:
        session_store.delete(sid)
    logout_user()
    return login_manager.unauthorized()


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        # Legacy kullanıcı: Cognito token yok → yalnızca oturumla geç.
        if not getattr(current_user, "cognito_sub", None):
            return view(*args, **kwargs)
        sid = session.get("cognito_sid")
        if not sid:
            return _invalidate()
        try:
            access = session_store.get_valid_access_token(sid)
            claims = cognito_jwt.validate_token(access, "access")
        except (session_store.SessionInvalid, cognito_jwt.TokenValidationError):
            return _invalidate()
        g.cognito_claims = claims
        session_store.touch(sid)
        return view(*args, **kwargs)
    return wrapped
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_require_auth.py -v`
Expected: PASS (6 tests). If the `probe_route` fixture's `app.url_map.update()` is insufficient for late route registration, register the probe view via `app.add_url_rule("/__probe", "_probe", require_auth(lambda: ("ok", 200)))` inside the fixture instead.

- [ ] **Step 5: Commit**

```bash
git add app/auth_middleware.py tests/test_require_auth.py
git commit -m "Add unified require_auth middleware"
```

---

### Task 6: Wire login + logout to Cognito tokens (`app/blueprints/auth.py`)

Login uses `authenticate`, validates the ID token via JWKS, creates a `CognitoSession`, stores `cognito_sid`. Logout does best-effort `global_sign_out` and deletes the row. Update the `cognito_native` fixture and inline monkeypatches in `tests/test_auth.py` to the new shape.

**Files:**
- Modify: `app/blueprints/auth.py`
- Modify: `tests/test_auth.py` (fixture + 2 inline monkeypatches)

**Interfaces:**
- Consumes: `cognito_service.authenticate/global_sign_out`, `cognito_jwt.validate_token`, `session_store.create/get/current_access_token/delete`.
- Produces: `session["cognito_sid"]` on successful Cognito login; cleared on logout.

- [ ] **Step 1: Update the Cognito test fixture (make existing tests express the new contract)**

In `tests/test_auth.py`, add imports near the top of the cognito section (after `from app.services import cognito_service`):

```python
from app.services import cognito_jwt, session_store  # noqa: E402
```

Replace the `cognito_native` fixture's `fake_initiate` + its `monkeypatch.setattr(cognito_service, "initiate_auth", fake_initiate)` line with an `authenticate` stub plus permissive JWKS validation:

```python
    def fake_authenticate(username, password):
        if username not in captured["confirmed"]:
            raise CognitoServiceError("E-postan henüz doğrulanmadı.", "UserNotConfirmedException")
        return {
            "tokens": {"access_token": f"acc-{username}", "id_token": f"id-{username}",
                       "refresh_token": f"ref-{username}", "expires_in": 3600},
            "claims": {"sub": f"sub-{username}", "email": f"{username}@example.com",
                       "email_verified": True, "name": username},
        }

    monkeypatch.setattr(cognito_service, "sign_up", fake_sign_up)
    monkeypatch.setattr(cognito_service, "confirm_sign_up", fake_confirm)
    monkeypatch.setattr(cognito_service, "authenticate", fake_authenticate)
    # JWKS doğrulaması testte sahte token'ları geçsin (kripto testi test_cognito_jwt'de).
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use: {"sub": tok.replace("id-", "sub-").replace("acc-", "sub-")})
    return captured
```

Update the two inline monkeypatches later in the file:
- `test_cognito_login_sub_mismatch_rejected`: change to patch `authenticate` returning the mismatched sub:
```python
    monkeypatch.setattr(cognito_service, "authenticate",
                        lambda u, p: {"tokens": {"access_token": "a", "id_token": "i",
                                                 "refresh_token": "r", "expires_in": 3600},
                                      "claims": {"sub": "BASKA-SUB", "email": f"{u}@example.com"}})
```
- `test_cognito_login_not_authorized_returns_401`: change `initiate_auth` → `authenticate` in the `monkeypatch.setattr` target (the thrown `CognitoServiceError` is unchanged).

- [ ] **Step 2: Run the cognito auth tests to verify they now fail against the old route**

Run: `python -m pytest tests/test_auth.py -k cognito -v`
Expected: FAIL (route still calls `initiate_auth`; fixture now stubs `authenticate`, and `/supplements` isn't yet token-guarded).

- [ ] **Step 3: Rewrite the Cognito branch of `login()` in `app/blueprints/auth.py`**

Add imports at the top of the file (with the other `from app.services import ...`):

```python
from app.services import cognito_jwt, session_store
from app.services.cognito_jwt import TokenValidationError
```

Replace the Cognito block inside `login()` (the `if user and user.cognito_sub and COGNITO_ENABLED:` branch) with:

```python
    if user and user.cognito_sub and COGNITO_ENABLED:
        try:
            result = cognito_service.authenticate(username, password or "")
        except CognitoServiceError as e:
            if e.code == "UserNotConfirmedException":
                return jsonify({"error": e.message, "needs_verification": True,
                                "username": username}), 403
            return jsonify({"error": e.message}), 401
        claims = result["claims"]
        tokens = result["tokens"]
        # Kimlik token'ını KRİPTOGRAFİK olarak doğrula (JWKS) — manuel güvenme.
        try:
            cognito_jwt.validate_token(tokens["id_token"], "id")
        except TokenValidationError:
            return jsonify({"error": t("auth.bad_credentials")}), 401
        # Kimlik bütünlüğü: dönen sub dolu VE yerel kayıtla eşleşmeli.
        if not claims.get("sub") or claims["sub"] != user.cognito_sub:
            return jsonify({"error": t("auth.bad_credentials")}), 401
        _login_fresh(user)
        # _login_fresh session.clear() yapar → sid'i SONRA yaz.
        session["cognito_sid"] = session_store.create(user, tokens, username)
        quest_result = complete_quest_for_user(user.id, "login")
        response = {"message": t("auth.welcome", username=user.username)}
        if quest_result:
            response["quest_awarded"] = quest_result
        return jsonify(response)
```

- [ ] **Step 4: Update `logout()` in `app/blueprints/auth.py`**

After the same-site guard block, before `session.pop("via_cognito", None)`, insert:

```python
    # Cognito oturumu: GlobalSignOut (best-effort — süresi dolmuş token hata verebilir,
    # yut) + sunucu tarafı token satırını sil.
    sid = session.get("cognito_sid")
    if sid:
        access = session_store.current_access_token(sid)
        if access:
            try:
                cognito_service.global_sign_out(access)
            except CognitoServiceError:
                pass
        session_store.delete(sid)
    session.pop("cognito_sid", None)
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_auth.py -v`
Expected: PASS (all, including `test_cognito_verify_then_login_succeeds` which now hits token-guarded `/supplements` — but Task 7 has not swapped `/supplements` yet, so it uses `@login_required` and passes regardless). If any cognito test that calls `/supplements` expects token validation, it still passes because the fixture stubs `validate_token`.

- [ ] **Step 6: Commit**

```bash
git add app/blueprints/auth.py tests/test_auth.py
git commit -m "Wire login/logout to Cognito tokens + JWT validation"
```

---

### Task 7: Swap `@login_required` → `@require_auth` across protected blueprints

Mechanical swap on every protected endpoint. **Exception:** `/logout` keeps `@login_required` (auth teardown must not require a valid token). `current_user` and templates untouched.

**Files (modify each):**
`app/blueprints/coach.py`, `food.py`, `gamification.py`, `menu.py`, `pages.py`, `profile.py`, `social.py`, `supplements.py`, `tracking.py`, `training.py`, `wearables.py`, `nutrition/diary.py`, `nutrition/meallog.py`, `nutrition/plan.py`, and `auth.py` (all `@login_required` **except** the one on `logout`).

**Interfaces:**
- Consumes: `require_auth` from `app.auth_middleware`.

- [ ] **Step 1: For each file above, replace the import and the decorators**

In each blueprint file, replace the Flask-Login decorator import. Current import looks like:
```python
from flask_login import current_user, login_required, login_user, logout_user
```
Change to keep the other names and drop `login_required`, adding `require_auth`:
```python
from flask_login import current_user, login_user, logout_user
from app.auth_middleware import require_auth
```
(If a file imports only `login_required` from flask_login, replace that line with `from app.auth_middleware import require_auth` and keep any other flask_login names it uses.)

Then replace each decorator usage `@login_required` → `@require_auth` in that file.

**`app/blueprints/auth.py` special-case:** keep `from flask_login import ... login_required ...` (still needed for `logout`), and add `from app.auth_middleware import require_auth`. `auth.py` has 3 `login_required` usages — only `logout` keeps `@login_required`; if the other two are on state-changing account routes, swap them to `@require_auth`. Verify each: leave `logout` as `@login_required`.

- [ ] **Step 2: Verify no stray `login_required` remains except logout**

Run: `grep -rn "login_required" app/blueprints/ --include=*.py`
Expected: exactly one match — the `@login_required` on `logout` in `auth.py` (plus the `login_required` name still in `auth.py`'s import line). No other blueprint should import or use `login_required`.

- [ ] **Step 3: Run the full suite**

Run: `python -m pytest -v`
Expected: PASS. Pay attention to any test that logs in a **Cognito** user then hits a now-`@require_auth` page — those rely on the `cognito_native` fixture stubbing `validate_token` (done in Task 6). Legacy-user tests pass through unaffected. `test_protected_route_redirects_anonymous_to_login` (GET `/supplements`) still returns 302 to `/login` because `@require_auth` calls `login_manager.unauthorized()` for anonymous users.

- [ ] **Step 4: Commit**

```bash
git add app/blueprints/
git commit -m "Route every protected endpoint through require_auth"
```

---

### Task 8: Integration spec tests (`tests/test_cognito_auth.py`)

The spec's end-to-end cases not already unit-covered: logout calls GlobalSignOut, concurrent sessions are independent, session expiration forces re-login, protected-route matrix. Reuses the `cognito_native` fixture from `test_auth.py` by importing it — or redefines a local one.

**Files:**
- Create: `tests/test_cognito_auth.py`

**Interfaces:**
- Consumes: `cognito_native` behavior (stubbed `authenticate` + permissive `validate_token`), `session_store`, `cognito_service`.

- [ ] **Step 1: Write the tests**

```python
# tests/test_cognito_auth.py
"""Sprint 2 uçtan-uca kimlik doğrulama senaryoları: logout GlobalSignOut,
eşzamanlı oturumlar, oturum süre-dolumu, korumalı route matrisi.

    python -m pytest tests/test_cognito_auth.py -v
"""
from datetime import datetime, timedelta
import pytest

from app.extensions import db
from app.models import User, CognitoSession
from app.services import cognito_service, cognito_jwt, session_store
from app.services.cognito_service import CognitoServiceError
from app.blueprints import auth as auth_bp


@pytest.fixture
def cognito_env(monkeypatch):
    """COGNITO_ENABLED aç; authenticate + JWKS doğrulamayı sahtele."""
    monkeypatch.setattr(auth_bp, "COGNITO_ENABLED", True)

    def fake_authenticate(username, password):
        return {
            "tokens": {"access_token": f"acc-{username}", "id_token": f"id-{username}",
                       "refresh_token": f"ref-{username}", "expires_in": 3600},
            "claims": {"sub": f"sub-{username}", "email": f"{username}@example.com",
                       "email_verified": True, "name": username},
        }

    monkeypatch.setattr(cognito_service, "authenticate", fake_authenticate)
    monkeypatch.setattr(cognito_jwt, "validate_token", lambda tok, use: {"sub": "ok"})
    return monkeypatch


@pytest.fixture
def cog_account(app):
    u = User(username="e2e", email="e2e@example.com", cognito_sub="sub-e2e")
    db.session.add(u)
    db.session.commit()
    return u


def test_logout_calls_global_sign_out_and_deletes_row(client, cognito_env, cog_account):
    calls = {}
    cognito_env.setattr(cognito_service, "global_sign_out", lambda tok: calls.setdefault("tok", tok))
    assert client.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    assert CognitoSession.query.count() == 1
    resp = client.get("/logout", headers={"Referer": "http://localhost/"})
    assert resp.status_code in (301, 302)
    assert calls.get("tok") == "acc-e2e"          # GlobalSignOut çağrıldı
    assert CognitoSession.query.count() == 0      # satır silindi


def test_concurrent_sessions_are_independent(app, cognito_env, cog_account):
    c1, c2 = app.test_client(), app.test_client()
    assert c1.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    assert c2.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    assert CognitoSession.query.count() == 2       # iki bağımsız satır
    # c1 çıkış yapınca kendi satırı silinir; c2 satırı kalır (yerel kayıt bağımsız).
    c1.get("/logout", headers={"Referer": "http://localhost/"})
    assert CognitoSession.query.count() == 1


def test_session_expiration_forces_relogin(client, cognito_env, cog_account, monkeypatch):
    # access süresi geçmiş + refresh başarısız → korumalı route login'e yönlendirir.
    assert client.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    row = CognitoSession.query.first()
    row.access_token_exp = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    monkeypatch.setattr(cognito_service, "refresh_tokens",
                        lambda ref, uname: (_ for _ in ()).throw(CognitoServiceError("x", "NotAuthorizedException")))
    resp = client.get("/supplements")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert CognitoSession.query.count() == 0       # geçersiz oturum temizlendi


def test_protected_route_allows_after_login(client, cognito_env, cog_account):
    assert client.post("/login", json={"username": "e2e", "password": "x"}).status_code == 200
    assert client.get("/supplements").status_code == 200
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_cognito_auth.py -v`
Expected: PASS (4 tests). If `/supplements` is not a valid GET page in this app, substitute another `@require_auth` GET route (e.g. `/profile`); confirm the chosen path with `grep -rn "def .*:" app/blueprints/supplements.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cognito_auth.py
git commit -m "Add Sprint 2 end-to-end auth tests"
```

---

### Task 9: Legacy markers, config docs, and documentation

**Files:**
- Modify: `app/models.py` (mark `password_hash` / `check_password`), `app/blueprints/auth.py` (mark legacy local-login branch)
- Modify: `.env.example` (new Cognito env vars)
- Modify: `docs/cognito.md`, `docs/handoff.md`

- [ ] **Step 1: Add legacy TODO markers**

In `app/models.py`, above `password_hash = db.Column(...)`:
```python
    # TODO(Sprint 3): remove legacy local-password auth. Cognito (cognito_sub) is
    # the auth identity; password_hash is retained only for backward compatibility.
```
Above `def check_password`:
```python
    # TODO(Sprint 3): remove — legacy local-password verification path.
```
In `app/blueprints/auth.py`, above the local-password branch (the `# Always run one password hash comparison...` block):
```python
    # TODO(Sprint 3): remove legacy local-password login. Kept for users without a
    # cognito_sub until migration completes; Cognito is the auth identity.
```

- [ ] **Step 2: Update `.env.example`**

Add under the Cognito section:
```
# Sprint 2: server-side Cognito token encryption. If unset, derived from SECRET_KEY.
# COGNITO_TOKEN_ENC_KEY=            # a valid Fernet key (base64, 32 bytes)
# COGNITO_REFRESH_SKEW_SECONDS=60   # refresh access token this many seconds before exp
```

- [ ] **Step 3: Rewrite `docs/cognito.md` Sprint 2 section**

Append a "## Sprint 2 — Login, JWT Validation & Sessions" section documenting: Authentication Flow (authenticate → validate id token via JWKS → Flask-Login session → CognitoSession row), JWT Validation (`cognito_jwt.validate_token`, JWKS, sig/iss/aud/exp/token_use), Session Lifecycle (cognito_sid cookie → encrypted DB row), Refresh Token Lifecycle (`get_valid_access_token` refresh-on-expiry; dead refresh → re-login), Logout Flow (GlobalSignOut + row delete), Protected Route Strategy (`@require_auth` on every protected endpoint; `/logout` exception; legacy passthrough).

- [ ] **Step 4: Update `docs/handoff.md`**

Add a "## Sprint 2 — Cognito Login & Sessions" section with: completed work, modified/created files, remaining technical debt (legacy local-password removal, `cognito_idp.py`/`cognito.py` consolidation, forgot/reset password still absent), Sprint 3 follow-ups (remove `# TODO(Sprint 3)` code once all users are Cognito-backed), and the **coordination note**: the `cognito_session` migration chains onto committed head `d6e7f8a9b0c1`; if the barcode migration `e7f8a9b0c1d2` lands on the mainline first, rebase `down_revision` to keep one linear chain.

- [ ] **Step 5: Run the full suite one final time**

Run: `python -m pytest -v`
Expected: PASS (entire suite green).

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/blueprints/auth.py .env.example docs/cognito.md docs/handoff.md
git commit -m "Mark legacy auth + document Sprint 2 Cognito auth"
```

---

## Self-Review (author checklist — completed)

**Spec coverage:**
- Login via Cognito InitiateAuth USER_PASSWORD_AUTH → Task 2 (`authenticate`) + Task 6 (wiring).
- Receive Access/ID/Refresh tokens → Task 2.
- JWT validation (sig/iss/aud/exp/token_use, JWKS, no manual trust) → Task 1.
- Reusable JWT middleware + auth middleware on all protected endpoints → Task 5 + Task 7.
- Session management (HttpOnly/Secure/SameSite=Lax; refresh tokens never exposed) → existing cookie config + Task 3 (server-side encrypted store).
- Refresh token support (refresh access; expired refresh → re-login; graceful failure) → Task 3 (`get_valid_access_token`) + Task 5.
- Logout GlobalSignOut + destroy session + invalidate refresh → Task 6.
- Protected routes audit → Task 7.
- Legacy isolation with TODO markers, backward compat → Task 9 + `@require_auth` legacy passthrough (Task 5).
- Identity = cognito_sub; password_hash not used for auth → Task 6 (sub integrity) + Task 9 (markers).
- Error handling (6 Cognito exceptions, no AWS leakage) → Task 2 (error map) + existing mapping.
- Security (rate limit, email normalization, constant-time, secure cookies, no sensitive logging) → existing controls preserved + Task 1/3 logging discipline.
- Testing (12 cases) → Tasks 1, 2, 3, 5, 6, 8 (mapping below).
- Documentation (handoff.md, cognito.md, 6 topics) → Task 9.

**Test-case → task map:** Successful Login (T6/T8), Incorrect Password (existing test_auth + T2 error map), Unknown Email/Username (existing test_auth), Unverified User (existing test_auth cognito), Expired Access Token (T3/T5), Expired Refresh Token (T3/T5/T8), Invalid JWT (T1), Modified JWT (T1), Logout (T8), Protected Routes (T5/T7/T8), Concurrent Sessions (T8), Session Expiration (T8).

**Placeholder scan:** No TBD/TODO-as-placeholder in steps; the only `TODO(Sprint 3)` strings are intentional code annotations per spec. All code steps contain full code.

**Type consistency:** `validate_token(token, expected_use)`, `authenticate(...)->{"tokens","claims"}`, `refresh_tokens(refresh_token, cognito_username)->{"access_token","id_token","expires_in"}`, `session_store.create(user, tokens, cognito_username)->sid`, `get_valid_access_token(session_id)->str`, `require_auth(view)`, `session["cognito_sid"]` — names match across Tasks 1–8.
