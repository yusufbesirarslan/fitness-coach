# Native Mobile Authentication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backend-mediated, opaque AxisAI mobile authentication with Bearer-only API routes, exactly-once refresh rotation, idempotent grace replay, provider-lifetime coverage, per-device revocation, and unchanged web authentication.

**Architecture:** Three additive SQLAlchemy models own a device session family and hashed access/refresh credentials while Fernet-encrypted Cognito tokens remain server-side. A focused credential codec handles canonical 256-bit credentials and versioned HKDF/HMAC replay; a transactional mobile-session service owns login, access validation, refresh, cleanup, and logout; a dedicated `/api/v1` blueprint exposes only JSON/Bearer contracts. PostgreSQL row locks plus optimistic versions and uniqueness constraints serialize first-use refresh, while deterministic replay returns the one committed pair without another provider call or state mutation.

**Tech Stack:** Python 3, Flask, Flask-SQLAlchemy, Alembic, PostgreSQL/SQLite test coverage, cryptography Fernet/HKDF/HMAC, joserfc Cognito JWT validation, boto3 Cognito IDP, Flask-Limiter, pytest.

## Global Constraints

- Preserve existing web login/logout, Flask-Login, cookies, redirects, `CognitoSession`, and all current web CSRF behavior.
- Register only `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, and `GET /api/v1/account/me`.
- Mobile protected routes authenticate only `Authorization: Bearer <AxisAI opaque access credential>`; never fall back to cookies, Flask-Login, or client-supplied user identifiers.
- Keep CORS unchanged and never add an origin allowlist or CORS extension.
- Do not modify Flutter, deploy, mutate AWS/Cognito, or use production users or credentials.
- Generate login credentials from at least 32 CSPRNG bytes; persist only indexed SHA-256 hashes of canonical mobile credentials.
- Derive replacements only with the approved versioned HKDF-SHA-256/HMAC-SHA-256 construction and independent keyring; never use the Fernet key as an HMAC key.
- Defaults: access 900 seconds, absolute family 7 days, grace 10 seconds, provider leeway 60 seconds, validator skew 0 seconds; validate all configuration.
- Compute access expiry as `min(now + access_ttl, family_absolute_expires_at)`.
- Renew Cognito when `provider_exp <= max(now + provider_leeway, access_exp + validation_skew)`; after renewal require `provider_exp > access_exp + validation_skew`.
- First use of refresh N commits one N+1 access row, one N+1 refresh row, one family-version increment, and at most one Cognito renewal.
- Same-parent grace replay returns byte-identical credentials/timestamps, verifies committed hashes, and performs no write or provider call.
- Known post-grace reuse revokes only the affected family, clears Cognito ciphertext, returns `AUTH_REFRESH_FAILED`, and leaves other devices valid.
- Local logout commit is final; Cognito `RevokeToken` is post-commit best effort and never changes the empty `204`.
- Cached JWKS matching `kid` remains usable on fetch failure; unknown `kid` plus fetch failure maps to retryable `AUTH_TEMPORARILY_UNAVAILABLE`.
- Every non-204 mobile error has `{error:{code,message,retryable,request_id}}`; never expose raw Cognito messages.
- Operational rollback unregisters the additive mobile blueprint and leaves new tables unused; do not require destructive schema rollback.
- Run targeted tests after every task and the complete `python -m pytest` suite with at least a two-hour command timeout before completion.

## File Structure

- Create `app/services/mobile_credentials.py`: canonical credential codec, hashing, keyring validation, HKDF/HMAC derivation, safe limiter key.
- Create `app/services/mobile_auth.py`: provider coverage, typed outcomes/errors, transactional login/access/refresh/logout/cleanup.
- Create `app/mobile_auth_middleware.py`: strict Bearer parser and `require_mobile_auth`.
- Create `app/blueprints/mobile_api.py`: versioned JSON routes, error envelope, mobile rate-limit response.
- Modify `app/models.py`: `MobileAuthSession`, `MobileAccessCredential`, `MobileRefreshCredential`.
- Create `migrations/versions/c7d8e9f0a1b2_add_mobile_auth_sessions.py`: additive verify-or-create revision from `a994f9bed783`.
- Modify `app/config.py`, `app/services/session_store.py`, `app/services/cognito_jwt.py`, `app/services/cognito_service.py`, `app/hooks.py`, `app/__init__.py`, and `app/cli.py`.
- Create `tests/test_mobile_credentials.py`, `tests/test_mobile_auth_models.py`, `tests/test_mobile_auth_migration.py`, `tests/test_mobile_auth_service.py`, `tests/test_mobile_auth_api.py`, and `tests/test_mobile_auth_pg.py`.
- Modify Cognito/JWKS, CSRF, audit, migration-graph, env-example, drift-checker, and web-regression tests.
- Modify `.env.example`, `docs/cognito.md`, and the read-only Cognito drift checker; add no deployment or live-mutation code.

---

### Task 1: Validated Configuration and Credential Codec

**Files:**
- Create: `app/services/mobile_credentials.py`
- Modify: `app/config.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_mobile_credentials.py`
- Modify: `tests/test_env_example.py`
- Modify: `.env.example`

**Interfaces:**
- Produces `CredentialConfigurationError`, `MobileCredentialPair`, `parse_keyring(raw)`, `validate_mobile_auth_config(app)`, `generate_credential()`, `canonical_credential(value)`, `hash_credential(value)`, `derive_replacement_pair(...)`, and `credential_rate_limit_key(value)`.
- Consumes Flask config only; it must not import models, routes, Cognito, or Fernet.
- Keyring format is JSON `{v1:<unpadded-base64url-32-or-more-bytes>}`; persisted metadata contains only `v1`.

- [ ] **Step 1: Write failing configuration and codec tests**

```python
def test_generate_credential_is_canonical_256_bit_value():
    value = mobile_credentials.generate_credential()
    assert len(mobile_credentials.canonical_credential(value)) == 32
    assert '=' not in value

def test_replacement_derivation_is_deterministic_and_domain_separated(app):
    parent = _wire(b'p' * 32)
    pair1 = mobile_credentials.derive_replacement_pair(
        parent, 'family-1', 4, 5, 'v1',
        app.config['MOBILE_AUTH_DERIVATION_KEYRING'])
    pair2 = mobile_credentials.derive_replacement_pair(
        parent, 'family-1', 4, 5, 'v1',
        app.config['MOBILE_AUTH_DERIVATION_KEYRING'])
    assert pair1 == pair2
    assert pair1.access != pair1.refresh

@pytest.mark.parametrize('raw', [
    json.dumps({}),
    json.dumps({'v1': 'short'}),
    json.dumps({'v1': '%%%'}),
])
def test_invalid_keyring_fails_closed(raw):
    with pytest.raises(CredentialConfigurationError):
        mobile_credentials.parse_keyring(raw)
```

Also test canonical re-encoding rejection, exact 32-byte decoded credentials, 64-character lowercase SHA-256 hashes, different family/generation/key-version outputs, recorded-old-key replay after an active-key switch, no secret/hash in limiter keys, non-positive TTL rejection, negative skew/buffer rejection, grace not strictly shorter than access TTL, and missing active version.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_credentials.py tests/test_env_example.py`

Expected: collection/import failures because the codec and mobile config do not exist.

- [ ] **Step 3: Implement exact config parsing and cryptographic interfaces**

```python
@dataclass(frozen=True)
class MobileCredentialPair:
    access: str
    refresh: str

def derive_replacement_pair(parent_refresh, family_id, parent_generation,
                            child_generation, key_version, keyring):
    parent = canonical_credential(parent_refresh)
    root = keyring[key_version]
    family = family_id.encode('ascii')
    info = (
        b'axisai/mobile-auth/replacement-subkey/v1' + bytes([0])
        + len(family).to_bytes(2, 'big') + family
        + int(parent_generation).to_bytes(8, 'big')
        + int(child_generation).to_bytes(8, 'big')
    )
    subkey = HKDF(
        algorithm=hashes.SHA256(), length=32,
        salt=b'axisai/mobile-auth/credential-derivation/salt/v1',
        info=info,
    ).derive(root)
    access = hmac.new(
        subkey, b'axisai/mobile-auth/access/v1' + bytes([0]) + parent,
        hashlib.sha256).digest()
    refresh = hmac.new(
        subkey, b'axisai/mobile-auth/refresh/v1' + bytes([0]) + parent,
        hashlib.sha256).digest()
    return MobileCredentialPair(_wire(access), _wire(refresh))
```

Read settings inside `configure_app`, store parsed key bytes in `app.config`, and validate startup bounds. Put a fixed non-production `v1` key into the test environment before app creation. Add placeholders/defaults to `.env.example`; never add a real secret.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `python -m pytest -q tests/test_mobile_credentials.py tests/test_env_example.py tests/test_config_engine_options.py`

Expected: all selected tests pass.

- [ ] **Step 5: Review secrets and commit**

Run: `rg -n 'MOBILE_AUTH_DERIVATION_KEYRING|axisai/mobile-auth' app tests .env.example`

Verify test key material appears only in tests, no parsed key is logged, and `.env.example` contains a placeholder.

```bash
git add app/config.py app/services/mobile_credentials.py tests/conftest.py tests/test_mobile_credentials.py tests/test_env_example.py .env.example
git commit -m 'feat: add mobile credential derivation foundation'
```

### Task 2: Additive Session Models and Migration

**Files:**
- Modify: `app/models.py`
- Create: `migrations/versions/c7d8e9f0a1b2_add_mobile_auth_sessions.py`
- Create: `tests/test_mobile_auth_models.py`
- Create: `tests/test_mobile_auth_migration.py`
- Modify: `tests/test_migration_graph.py`
- Modify: `tests/test_cascade_delete.py`

**Interfaces:**
- `MobileAuthSession`: `family_id`, `user_id`, Cognito username/sub, nullable Fernet ciphertext columns, verified provider expiry, fixed absolute expiry, revocation fields, `version`, and timestamps.
- `MobileAccessCredential`: family FK, unique hash, unique `(session_id,generation)`, issuance/expiry/revocation fields.
- `MobileRefreshCredential`: family FK, unique hash, unique `(session_id,generation)`, unique nullable self `parent_id`, consumption/grace fields, replacement key version, and fixed replacement issuance/access-expiry/refresh-expiry fields.
- Database uniqueness on `parent_id` is the final no-sibling backstop.

- [ ] **Step 1: Write failing model and migration tests**

```python
def test_refresh_parent_can_have_only_one_child(app, user):
    parent = _refresh(session, generation=0)
    db.session.add_all([
        _refresh(session, generation=1, parent_id=parent.id),
        _refresh(session, generation=2, parent_id=parent.id),
    ])
    with pytest.raises(IntegrityError):
        db.session.commit()

def test_revoked_family_allows_null_cognito_ciphertext():
    family = MobileAuthSession(
        family_id='family-1', user_id=user.id, cognito_sub=user.cognito_sub,
        cognito_username=user.username, revoked_at=datetime.utcnow(),
        cognito_access_token=None, cognito_refresh_token=None,
        absolute_expires_at=datetime.utcnow(), version=1)
    db.session.add(family)
    db.session.commit()
```

Test named indexes/constraints, non-negative generations/version, cascade deletion, migration single head `c7d8e9f0a1b2`, upgrade on empty SQLite, verify-or-create on `db.create_all()` schema, fail-closed incompatible pre-existing table, and operational rollback documentation. Test explicit downgrade only against disposable test data.

- [ ] **Step 2: Run model/migration tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_models.py tests/test_mobile_auth_migration.py tests/test_migration_graph.py tests/test_cascade_delete.py`

Expected: missing model/revision failures.

- [ ] **Step 3: Implement models and verify-or-create migration**

```python
class MobileRefreshCredential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer, db.ForeignKey('mobile_auth_session.id', ondelete='CASCADE'),
        nullable=False)
    credential_hash = db.Column(db.String(64), nullable=False)
    generation = db.Column(db.Integer, nullable=False)
    parent_id = db.Column(
        db.Integer, db.ForeignKey(
            'mobile_refresh_credential.id', ondelete='CASCADE'),
        nullable=True)
    consumed_at = db.Column(db.DateTime)
    grace_expires_at = db.Column(db.DateTime)
    replacement_key_version = db.Column(db.String(32))
    replacement_issued_at = db.Column(db.DateTime)
    replacement_access_expires_at = db.Column(db.DateTime)
    replacement_refresh_expires_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)
    __table_args__ = (
        db.Index('uq_mobile_refresh_hash', 'credential_hash', unique=True),
        db.UniqueConstraint(
            'session_id', 'generation',
            name='uq_mobile_refresh_session_generation'),
        db.UniqueConstraint('parent_id', name='uq_mobile_refresh_parent'),
        db.CheckConstraint(
            'generation >= 0', name='ck_mobile_refresh_generation'),
    )
```

Add equivalent named access/family constraints and relationships with `passive_deletes=True`. The migration must mirror model types/names, create only new tables/indexes, detect `db.create_all()` tables, create missing safe indexes, and raise on missing required columns. Its downgrade drops only the three additive tables in FK order; normal application rollback never runs downgrade.

- [ ] **Step 4: Run migration/model tests and schema checks**

Run: `python -m pytest -q tests/test_mobile_auth_models.py tests/test_mobile_auth_migration.py tests/test_migration_graph.py tests/test_cascade_delete.py tests/test_db_init.py`

Expected: all selected tests pass and the migration graph has one head.

- [ ] **Step 5: Commit the additive schema**

```bash
git add app/models.py migrations/versions/c7d8e9f0a1b2_add_mobile_auth_sessions.py tests/test_mobile_auth_models.py tests/test_mobile_auth_migration.py tests/test_migration_graph.py tests/test_cascade_delete.py
git commit -m 'feat: add opaque mobile session schema'
```

### Task 3: Cognito JWT Cache and Provider Operations

**Files:**
- Modify: `app/services/cognito_jwt.py`
- Modify: `app/services/cognito_service.py`
- Modify: `app/services/session_store.py`
- Modify: `tests/test_cognito_jwt.py`
- Modify: `tests/test_cognito_service_tokens.py`
- Modify: `tests/test_session_store.py`

**Interfaces:**
- `validate_token(token, expected_use, leeway_seconds=0) -> dict`.
- `cognito_service.refresh_tokens(...) -> {'access_token','id_token','refresh_token','expires_in'}`, preserving the old refresh token when Cognito does not return one.
- `cognito_service.revoke_token(refresh_token) -> None`.
- Public `encrypt_token(value)` and `decrypt_token(value)`; legacy `_enc`/`_dec` remain behavior-compatible wrappers for web sessions.

- [ ] **Step 1: Write failing JWKS, skew, revoke, and Fernet tests**

```python
def test_cached_matching_kid_survives_fetch_failure(signed_token, monkeypatch):
    cognito_jwt._jwks_cache = {'known': public_key}
    monkeypatch.setattr(cognito_jwt, '_fetch_jwks', _raise_network)
    assert cognito_jwt.validate_token(signed_token, 'access')['sub'] == 'sub-123'

def test_unknown_kid_fetch_failure_is_temporary(unknown_kid_token, monkeypatch):
    cognito_jwt._jwks_cache = {'old': old_key}
    monkeypatch.setattr(cognito_jwt, '_fetch_jwks', _raise_network)
    with pytest.raises(TokenValidationError) as exc:
        cognito_jwt.validate_token(unknown_kid_token, 'access')
    assert exc.value.reason == 'jwks_unavailable'

def test_revoke_token_uses_refresh_token(fake_idp):
    cognito_service.revoke_token('provider-refresh')
    assert fake_idp.calls == [('revoke_token', {
        'Token': 'provider-refresh', 'ClientId': 'client-123'})]
```

Also test unknown `kid` successful refresh, refreshed JWKS still missing `kid` definitive failure, cached known-key bad signature definitive without fetch, malformed header, access/id audience rules, configurable expiry skew, no raw token/error logging, optional Cognito rotated refresh token, and unchanged web Fernet/session behavior.

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `python -m pytest -q tests/test_cognito_jwt.py tests/test_cognito_service_tokens.py tests/test_session_store.py`

Expected: new cache/revoke/helper tests fail.

- [ ] **Step 3: Refactor lookup around untrusted-header `kid`**

```python
def _select_key(token):
    kid = _unverified_kid(token)
    cached = _jwks_cache.get(kid) if _jwks_cache else None
    if cached is not None:
        return cached
    try:
        fresh = _fetch_jwks()
    except Exception as exc:
        raise TokenValidationError('jwks_unavailable') from exc
    key = fresh.get(kid)
    if key is None:
        raise TokenValidationError('invalid_key')
    return key

def validate_token(token, expected_use, leeway_seconds=0):
    key = _select_key(token)
    claims = _decode_with_key(token, key, leeway_seconds)
    _validate_issuer_use_audience(claims, expected_use)
    return dict(claims)
```

Decode only the protected header before verification. Never refresh on a cached matching-key signature failure. Atomically replace the cache only after a complete valid JWKS fetch. Preserve `jwks_unavailable` through callers.

- [ ] **Step 4: Add provider revoke/refresh passthrough and public cipher helpers**

Implement `revoke_token` with `Token`, `ClientId`, and `ClientSecret` only when configured. Return a provider refresh token from `refresh_tokens` only when Cognito supplied one. Make web `_enc`/`_dec` delegate to public helpers without changing key derivation, ciphertext format, or exception behavior.

- [ ] **Step 5: Run focused and web-auth regressions**

Run: `python -m pytest -q tests/test_cognito_jwt.py tests/test_cognito_service_tokens.py tests/test_session_store.py tests/test_require_auth.py tests/test_auth.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit the provider boundary**

```bash
git add app/services/cognito_jwt.py app/services/cognito_service.py app/services/session_store.py tests/test_cognito_jwt.py tests/test_cognito_service_tokens.py tests/test_session_store.py
git commit -m 'fix: harden Cognito key and token lifecycle'
```

### Task 4: Mobile Login, Provider Coverage, and Access Authentication

**Files:**
- Create: `app/services/mobile_auth.py`
- Create: `tests/test_mobile_auth_service.py`
- Modify: `app/blueprints/auth.py`
- Modify: `tests/test_auth.py`

**Interfaces:**
- `MobileAuthFailure(code, status, retryable, reason)`.
- `IssuedSession(access_credential, refresh_credential, access_expires_at, refresh_expires_at)`.
- `MobilePrincipal(user, family, claims)`.
- `calculate_access_expiry(now, family_absolute_expires_at)`.
- `login(username, password, now=None) -> IssuedSession`.
- `authenticate_access(raw_access, now=None) -> MobilePrincipal`.
- Shared identity reconciliation must preserve the exact current web behavior; keep a compatibility wrapper in `app.blueprints.auth` if tests import the private helper.

- [ ] **Step 1: Write failing login and coverage tests**

```python
def test_login_persists_only_hashes_and_encrypted_provider_tokens(app, user, provider):
    issued = mobile_auth.login('alice', 'correct-password', now=NOW)
    family = MobileAuthSession.query.one()
    assert family.cognito_access_token != provider.access_token
    assert family.cognito_refresh_token != provider.refresh_token
    assert MobileAccessCredential.query.one().credential_hash == hash_credential(
        issued.access_credential)
    assert MobileRefreshCredential.query.one().credential_hash == hash_credential(
        issued.refresh_credential)

def test_login_renews_when_provider_cannot_cover_access(provider):
    provider.access_exp = NOW + timedelta(minutes=5)
    issued = mobile_auth.login('alice', 'correct-password', now=NOW)
    assert provider.refresh_calls == 1
    assert issued.access_expires_at == NOW + timedelta(minutes=15)

def test_renewed_provider_too_short_rolls_back(provider):
    provider.renewed_access_exp = NOW + timedelta(minutes=10)
    with pytest.raises(MobileAuthFailure) as exc:
        mobile_auth.login('alice', 'correct-password', now=NOW)
    assert exc.value.code == 'AUTH_TEMPORARILY_UNAVAILABLE'
    assert MobileAuthSession.query.count() == 0
```

Test equality at the coverage boundary triggers renewal, sufficient lifetime avoids renewal, access expiry caps at family absolute expiry, ID/access verified subjects must match, invalid credentials/verification/JWKS mappings, multi-device login creates independent families, access hash lookup, user/sub ownership checks, revoked/expired access rejection, JWKS temporary preservation, and no Flask session/login mutation.

- [ ] **Step 2: Run service tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_service.py tests/test_auth.py`

Expected: missing service interfaces and models-in-use failures.

- [ ] **Step 3: Implement the common provider-coverage helper**

```python
def _coverage(now, family_absolute_expires_at):
    access_exp = min(
        now + timedelta(seconds=current_app.config['MOBILE_AUTH_ACCESS_TTL_SECONDS']),
        family_absolute_expires_at)
    coverage_deadline = access_exp + timedelta(
        seconds=current_app.config['MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS'])
    renewal_trigger = max(
        now + timedelta(
            seconds=current_app.config['MOBILE_AUTH_COGNITO_EXPIRY_LEEWAY_SECONDS']),
        coverage_deadline)
    return access_exp, coverage_deadline, renewal_trigger
```

Trust provider expiry only from a fully validated access JWT. Renew on `provider_exp <= renewal_trigger`; validate the renewed JWT and require `provider_exp > coverage_deadline`. Insufficient renewed coverage raises retryable temporary failure before any local credential/family commit.

- [ ] **Step 4: Implement login and access authentication**

Authenticate Cognito, validate ID and access tokens, require matching subjects, reconcile the local user from verified claims, enforce coverage, encrypt provider tokens, generate/hash generation-zero credentials, and commit before returning raw values. Access authentication hashes the Bearer value, rejects expired/revoked/absolute-expired families, validates the decrypted Cognito access JWT with mobile skew, and requires row user, family sub, JWT sub, and `User.cognito_sub` to agree.

When an active family is found expired, revoke it and clear its ciphertext in the same commit. Definitive ownership/signature failures revoke that family; JWKS unavailability returns temporary failure without mutation.

- [ ] **Step 5: Run focused and web-login tests**

Run: `python -m pytest -q tests/test_mobile_auth_service.py tests/test_auth.py tests/test_cognito_auth.py tests/test_require_auth.py`

Expected: all selected tests pass and existing web response/session behavior is unchanged.

- [ ] **Step 6: Commit login and access service**

```bash
git add app/services/mobile_auth.py app/blueprints/auth.py tests/test_mobile_auth_service.py tests/test_auth.py
git commit -m 'feat: add opaque mobile login and access validation'
```

### Task 5: Exactly-Once Refresh and Idempotent Grace Replay

**Files:**
- Modify: `app/services/mobile_auth.py`
- Modify: `tests/test_mobile_auth_service.py`

**Interfaces:**
- `refresh(raw_refresh, now=None) -> IssuedSession`.
- Internal `_lock_family_and_parent(hash)` always locks family first and parent second.
- Internal `_replay_consumed_parent(parent, raw_refresh, now)` is read-only and returns the stored pair/timestamps after hash verification.
- Internal `_revoke_family(family, reason, now)` clears both Cognito ciphertext columns and marks every access/refresh row revoked without committing.

- [ ] **Step 1: Write failing rotation/replay tests**

```python
def test_first_refresh_creates_exactly_one_child(app, family_with_generation_zero):
    issued = mobile_auth.refresh(PARENT_RAW, now=NOW)
    family = db.session.get(MobileAuthSession, FAMILY_ID)
    assert family.version == 2
    assert MobileAccessCredential.query.filter_by(generation=1).count() == 1
    assert MobileRefreshCredential.query.filter_by(generation=1).count() == 1
    assert MobileRefreshCredential.query.filter_by(parent_id=PARENT_ID).count() == 1
    assert issued.refresh_expires_at == family.absolute_expires_at

def test_lost_response_retry_returns_identical_pair_and_timestamps(
        family_with_generation_zero):
    first = mobile_auth.refresh(PARENT_RAW, now=NOW)
    replay = mobile_auth.refresh(PARENT_RAW, now=NOW + timedelta(seconds=2))
    assert replay == first
    assert db.session.get(MobileAuthSession, FAMILY_ID).version == 2

def test_post_grace_reuse_revokes_only_affected_family(two_device_families):
    mobile_auth.refresh(PARENT_RAW, now=NOW)
    with pytest.raises(MobileAuthFailure) as exc:
        mobile_auth.refresh(PARENT_RAW, now=NOW + timedelta(seconds=11))
    assert exc.value.code == 'AUTH_REFRESH_FAILED'
    assert affected.revoked_at is not None
    assert affected.cognito_access_token is None
    assert other.revoked_at is None
```

Add tests for distinct new credentials on ordinary generation advance, old access revocation, exact stored issuance/expiry replay, no sibling rows, replay no version/generation mutation, unknown random refresh affects no family, known revoked refresh reuse, absolute-expired family, access expiry cap near day seven, coverage-trigger renewal, equality boundary, sufficient provider lifetime, too-short renewed token rollback/unconsumed parent/no child, definitive provider rejection family revocation, temporary provider failure rollback, missing derivation version, and replay hash mismatch returning temporary failure without alternative credentials.

- [ ] **Step 2: Run refresh tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_service.py -k 'refresh or replay or reuse or coverage'`

Expected: `refresh` and transaction behavior tests fail.

- [ ] **Step 3: Implement lock ordering and consumed-parent classification**

Resolve the parent ID/session ID by hash, then start/reuse the request transaction and lock:

```python
family = (MobileAuthSession.query
          .filter_by(id=session_id)
          .with_for_update()
          .one())
parent = (MobileRefreshCredential.query
          .filter_by(id=parent_id, session_id=family.id)
          .with_for_update()
          .one())
```

Recheck constant-time hash equality and all state after acquiring locks. If `parent.consumed_at`:

- `now <= grace_expires_at`: query the unique child by `parent_id` and access by `(session_id, child_generation)`; derive using the recorded key version; constant-time verify both persisted hashes; copy stored timestamps; release transaction without writes; return.
- `now > grace_expires_at`: call `_revoke_family(..., 'refresh_reuse', now)`, commit, then raise `AUTH_REFRESH_FAILED`.

Unknown hashes return the same client error without revoking any family.

- [ ] **Step 4: Implement the one committed N-to-N+1 transition**

Under both locks: capture `expected_version`; compute fixed `now`, child generation, capped access expiry, coverage deadline, and original family refresh expiry. Renew/validate Cognito before consuming the parent. Select the active key version, derive one pair, add one access and one child refresh row, mark parent consumed with grace and replacement metadata, revoke older access generations, and perform a conditional version update:

```python
updated = (MobileAuthSession.query
    .filter_by(id=family.id, version=expected_version, revoked_at=None)
    .update({
        MobileAuthSession.version: expected_version + 1,
        MobileAuthSession.last_used_at: now,
        MobileAuthSession.updated_at: now,
    }, synchronize_session=False))
if updated != 1:
    raise OptimisticRefreshConflict()
db.session.commit()
return IssuedSession(pair.access, pair.refresh, access_exp, family.absolute_expires_at)
```

Return raw credentials only after commit. On temporary provider/coverage/configuration/lock failure, roll back and return temporary failure. On definitive provider refresh rejection, revoke/clear/commit the family and return `AUTH_REFRESH_FAILED`. Catch uniqueness/version conflicts with a bounded retry that reloads state; the retry can only become deterministic replay or temporary exhaustion, never a second child/provider commit.

- [ ] **Step 5: Run refresh and persistence tests**

Run: `python -m pytest -q tests/test_mobile_auth_service.py tests/test_mobile_auth_models.py`

Expected: all selected tests pass, including identical replay, one-child counts, provider coverage, reuse revocation, and rollback assertions.

- [ ] **Step 6: Inspect database state and commit**

Run: `python -m pytest -q tests/test_mobile_auth_service.py -k 'hash or plaintext or replay or version or renewal'`

Verify no model column or captured log contains a raw mobile credential, derivation key, Cognito token, Authorization header, or provider message.

```bash
git add app/services/mobile_auth.py tests/test_mobile_auth_service.py
git commit -m 'feat: rotate mobile refresh credentials atomically'
```

### Task 6: Bearer-Only Mobile API, Error Contract, CSRF, and Rate Limits

**Files:**
- Create: `app/mobile_auth_middleware.py`
- Create: `app/blueprints/mobile_api.py`
- Modify: `app/__init__.py`
- Modify: `app/hooks.py`
- Create: `tests/test_mobile_auth_api.py`
- Modify: `tests/test_hooks.py`
- Modify: `tests/test_auth_audit.py`
- Modify: `tests/test_write_rate_limits.py`

**Interfaces:**
- `parse_bearer_header(value) -> str` accepts exactly one non-empty Bearer value.
- `require_mobile_auth(view)` sets `g.mobile_user`, `g.mobile_session`, and `g.mobile_claims`.
- `mobile_error(code, message, status, retryable, retry_after=None)` builds the approved envelope with `current_request_id()`.
- Blueprint name is exactly `mobile_api`; CSRF exemption checks this name, not a broad path prefix.

- [ ] **Step 1: Write failing API contract and isolation tests**

```python
def test_mobile_error_envelope_contains_only_approved_fields(client):
    response = client.post('/api/v1/auth/refresh', json={})
    assert response.status_code == 400
    assert set(response.json) == {'error'}
    assert set(response.json['error']) == {
        'code', 'message', 'retryable', 'request_id'}

def test_cookie_login_cannot_authenticate_mobile_me(client, auth_user):
    response = client.get('/api/v1/account/me')
    assert response.status_code == 401
    assert response.json['error']['code'] == 'AUTH_SESSION_EXPIRED'

def test_mobile_json_post_is_csrf_exempt_but_web_post_is_not(raw_client):
    assert raw_client.post('/api/v1/auth/login', json={}).status_code != 403
    assert raw_client.post('/login', json={}).status_code == 403
```

Test login/refresh success schemas and exact keys, malformed/non-JSON bodies, invalid credentials, verification required, temporary provider/JWKS errors, refresh failure client action, strict Bearer parsing, access expiry, ownership, `/account/me` allowed/excluded fields, logout empty `204`, no Set-Cookie/session mutation, rate-limit envelope and `Retry-After`, request ID propagation, no CORS headers, and raw provider-error suppression.

- [ ] **Step 2: Run API/hook/audit tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_api.py tests/test_hooks.py tests/test_auth_audit.py tests/test_write_rate_limits.py`

Expected: missing blueprint/middleware routes and CSRF exemption failures.

- [ ] **Step 3: Implement middleware and normalized errors**

```python
def mobile_error(code, message, status, retryable, retry_after=None):
    response = jsonify({'error': {
        'code': code,
        'message': message,
        'retryable': bool(retryable),
        'request_id': current_request_id(),
    }})
    response.status_code = status
    if retry_after is not None:
        response.headers['Retry-After'] = str(retry_after)
    return response

def require_mobile_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        raw = parse_bearer_header(request.headers.get('Authorization'))
        principal = mobile_auth.authenticate_access(raw)
        g.mobile_user, g.mobile_session, g.mobile_claims = (
            principal.user, principal.family, principal.claims)
        return view(*args, **kwargs)
    return wrapped
```

Map only typed internal failures; use stable safe messages. Never include exception text. The API-specific 429 handler returns `AUTH_RATE_LIMITED`; existing global/web 429 behavior remains unchanged.

- [ ] **Step 4: Implement and register the four routes**

Login and refresh require JSON objects and delegate to the service. Logout accepts an optional Bearer access credential and/or JSON refresh credential. `GET /account/me` returns only username, display name, profile completeness, language, goal, and goal type. Do not call `login_user`, `logout_user`, or Flask `session`.

Decorate login with the existing IP and normalized-username limits/fail-closed check. Decorate refresh with configured IP plus safe HMAC-derived credential-family key and logout with its configured limit.

- [ ] **Step 5: Narrow the CSRF exemption**

At the beginning of `_csrf_protect`, after the safe-method return, add only:

```python
if request.blueprint == 'mobile_api':
    return
```

The audit test must fail if another blueprint or arbitrary `/api/v1/` path becomes exempt, or if any mobile protected route reads cookies/Flask-Login.

- [ ] **Step 6: Run API and complete web-auth regressions**

Run: `python -m pytest -q tests/test_mobile_auth_api.py tests/test_hooks.py tests/test_auth_audit.py tests/test_write_rate_limits.py tests/test_auth.py tests/test_auth_phase6_ui.py tests/test_password_recovery.py tests/test_password_reset.py tests/test_require_auth.py`

Expected: all selected tests pass.

- [ ] **Step 7: Commit API boundary**

```bash
git add app/mobile_auth_middleware.py app/blueprints/mobile_api.py app/__init__.py app/hooks.py tests/test_mobile_auth_api.py tests/test_hooks.py tests/test_auth_audit.py tests/test_write_rate_limits.py
git commit -m 'feat: expose Bearer-only mobile auth API'
```

### Task 7: Irreversible Local Logout, Best-Effort Provider Revoke, and Cleanup

**Files:**
- Modify: `app/services/mobile_auth.py`
- Modify: `app/blueprints/mobile_api.py`
- Modify: `app/cli.py`
- Modify: `app/hooks.py` only if the existing cleanup hook is the canonical scheduler
- Modify: `tests/test_mobile_auth_service.py`
- Modify: `tests/test_mobile_auth_api.py`
- Modify: `tests/test_hooks.py`

**Interfaces:**
- `prepare_logout(access_credential=None, refresh_credential=None, now=None) -> LogoutResult`, where `LogoutResult.provider_refresh_token` exists only in request memory.
- `best_effort_provider_revoke(result) -> None` never raises to the route.
- `purge_expired(now=None) -> int` revokes/clears expired families idempotently and retains refresh-hash tombstones through absolute expiry.

- [ ] **Step 1: Write failing logout and cleanup tests**

```python
def test_logout_commits_local_revoke_before_provider_call(app, family, monkeypatch):
    observed = {}
    def fake_revoke(token):
        observed['revoked_at'] = db.session.get(
            MobileAuthSession, family.id).revoked_at
        observed['ciphertext'] = db.session.get(
            MobileAuthSession, family.id).cognito_refresh_token
    monkeypatch.setattr(cognito_service, 'revoke_token', fake_revoke)
    response = client.post('/api/v1/auth/logout', headers=_bearer(ACCESS_RAW))
    assert response.status_code == 204 and response.data == b''
    assert observed['revoked_at'] is not None
    assert observed['ciphertext'] is None

def test_remote_revoke_failure_still_returns_204(client, family, monkeypatch):
    monkeypatch.setattr(
        cognito_service, 'revoke_token',
        lambda token: (_ for _ in ()).throw(CognitoServiceError('raw', 'Internal')))
    response = client.post('/api/v1/auth/logout', headers=_bearer(ACCESS_RAW))
    assert response.status_code == 204
    assert response.data == b''
```

Test access-only, refresh-only, both, neither, unknown, and repeated logout; other-device isolation; all credential rows revoked; no `AUTH_LOGOUT_FAILED`; safe request/family/category logging with no token/provider text; expired-family cleanup clears ciphertext; repeated cleanup is idempotent; and cleanup never deletes tombstones before family absolute expiry.

- [ ] **Step 2: Run logout/cleanup tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py tests/test_hooks.py -k 'logout or purge or cleanup'`

Expected: logout transaction ordering and cleanup tests fail.

- [ ] **Step 3: Implement local-final logout transaction**

Resolve a family without revealing existence. Lock the family, decrypt provider refresh into a local variable, mark family and all credentials revoked, clear both ciphertext columns, and commit. Return the in-memory token only after commit. Unknown/already-revoked credentials return an empty result and still produce `204`.

```python
result = mobile_auth.prepare_logout(access_raw, refresh_raw)
try:
    mobile_auth.best_effort_provider_revoke(result)
except Exception:
    raise AssertionError('best_effort_provider_revoke must contain all failures')
return '', 204
```

`best_effort_provider_revoke` catches every wrapped provider failure and emits a safe structured warning/metric with request ID, public family ID, and stable category. It must not restore state or affect the response.

- [ ] **Step 4: Implement expiry cleanup**

Lock each due family in bounded batches. Mark it revoked with reason `absolute_expired`, clear Cognito ciphertext, revoke child rows, and commit. Retain rows/tombstones until at least absolute expiry; a later retention pass may delete only already-revoked, already-expired families. Integrate with the existing idempotent cleanup invocation without changing `CognitoSession` purge behavior.

- [ ] **Step 5: Run logout, cleanup, observability, and web logout tests**

Run: `python -m pytest -q tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py tests/test_hooks.py tests/test_observability.py tests/test_auth.py tests/test_session_store.py`

Expected: all selected tests pass.

- [ ] **Step 6: Commit revocation lifecycle**

```bash
git add app/services/mobile_auth.py app/blueprints/mobile_api.py app/cli.py app/hooks.py tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py tests/test_hooks.py
git commit -m 'feat: finalize mobile logout and session cleanup'
```

### Task 8: PostgreSQL Concurrency, Drift Audit, and Security Boundaries

**Files:**
- Create: `tests/test_mobile_auth_pg.py`
- Modify: `tests/test_mobile_auth_service.py`
- Modify: `tests/test_auth_audit.py`
- Modify: `tests/test_dependency_boundaries.py`
- Modify: `scripts/check_cognito_pool.py`
- Modify: `tests/test_check_cognito_pool.py`
- Modify: `docs/cognito.md`

**Interfaces:**
- PostgreSQL concurrency tests use `FITX_PG_CONCURRENCY_TEST=1` and `PG_TEST_DATABASE_URL`, matching the existing disposable-database test convention.
- The Cognito drift checker remains read-only and reports only booleans/configured lifetimes and rotation state; it never prints credentials, secrets, tokens, or raw provider errors.

- [ ] **Step 1: Write the gated PostgreSQL race tests**

Create two independent sessions and two threads synchronized by a barrier. Both submit the same parent refresh credential. Use a thread-safe provider-refresh counter and require identical raw responses, one child access row, one child refresh row, one family-version increment, and exactly one provider renewal when coverage requires it.

Also assert that grace replays are read-only, no sibling generation exists, post-grace reuse revokes only that family, and the provider-too-short path rolls back every database mutation.

- [ ] **Step 2: Write failing security and drift tests**

Test that the mobile modules do not import Flask-Login or read Flask cookies, API responses do not add CORS headers, current-user projections contain only approved fields, logs exclude raw opaque/Cognito credentials, and dependency direction is blueprint/middleware to service to model/provider adapters.

Extend the read-only drift tests for provider token lifetimes, secret presence as a boolean, and Cognito refresh-token rotation state. Require safe handling of unavailable provider metadata without mutation or raw error exposure.

- [ ] **Step 3: Run the new tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_pg.py tests/test_auth_audit.py tests/test_dependency_boundaries.py tests/test_check_cognito_pool.py`

Expected: the PostgreSQL module skips unless explicitly enabled; the newly added audit/drift assertions fail before implementation.

- [ ] **Step 4: Implement audit-safe drift reporting and close boundary findings**

Make only the smallest production changes needed for the tests. Keep the checker read-only, normalize provider failures, and document how operators validate derivation-key availability, Fernet configuration, provider lifetimes, and Cognito rotation without changing live configuration.

- [ ] **Step 5: Run concurrency and security validation**

Run: `python -m pytest -q tests/test_mobile_auth_pg.py tests/test_auth_audit.py tests/test_dependency_boundaries.py tests/test_check_cognito_pool.py`

When a disposable PostgreSQL URL is available, additionally run: `python -m pytest -m pg_concurrency -q tests/test_mobile_auth_pg.py`

Expected: all ordinary tests pass; the gated run passes when configured and otherwise reports a documented skip, never a false pass claim.

- [ ] **Step 6: Commit concurrency and security coverage**

```bash
git add tests/test_mobile_auth_pg.py tests/test_mobile_auth_service.py tests/test_auth_audit.py tests/test_dependency_boundaries.py scripts/check_cognito_pool.py tests/test_check_cognito_pool.py docs/cognito.md
git commit -m 'test: cover mobile auth concurrency and security'
```

### Task 9: Final Validation, Rollback Review, and Clean Handoff

**Files:**
- Modify only files required to correct findings from the validation below.

- [ ] **Step 1: Run the complete mobile-auth validation set**

Run: `python -m pytest -q tests/test_mobile_credentials.py tests/test_mobile_auth_models.py tests/test_mobile_auth_migration.py tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py tests/test_mobile_auth_pg.py tests/test_cognito_jwt.py tests/test_cognito_service_tokens.py tests/test_session_store.py tests/test_hooks.py tests/test_auth_audit.py tests/test_dependency_boundaries.py tests/test_check_cognito_pool.py tests/test_migration_graph.py`

Expected: all selected tests pass; PostgreSQL-only tests explicitly skip unless configured.

- [ ] **Step 2: Re-run the accepted web/auth focused baseline**

Run: `python -m pytest -q tests/test_auth.py tests/test_auth_audit.py tests/test_auth_phase6_ui.py tests/test_cognito.py tests/test_cognito_auth.py tests/test_cognito_idp.py tests/test_cognito_jwt.py tests/test_cognito_service_tokens.py tests/test_check_cognito_pool.py tests/test_password_recovery.py tests/test_password_reset.py tests/test_require_auth.py tests/test_session_store.py tests/test_observability.py tests/test_write_rate_limits.py tests/test_redis_compose_security.py`

Expected: the former 216-test design baseline plus any intentionally added cases passes, demonstrating unchanged web authentication, redirects, Flask-Login, cookies, CSRF, and rate limits.

- [ ] **Step 3: Validate collection and the full suite**

Run: `python -m pytest --collect-only -q`

Then run with a tool timeout of at least two hours: `python -m pytest`

Expected: collection succeeds and the entire suite completes successfully. Do not claim full-suite success from a partial or timed-out run.

- [ ] **Step 4: Perform schema, security, and scope review**

Run `python -m pytest -q tests/test_migration_graph.py tests/test_mobile_auth_migration.py` against disposable test databases only. Confirm the migration is expand-only, downgrade removes only the new mobile-auth objects, rollback can leave unused tables in place safely, and no live database or Cognito mutation occurred.

Run `git diff origin/main...HEAD --check`, inspect `git diff origin/main...HEAD --name-only`, and scan changed code for raw credential/token logging and plaintext persistence. Confirm no Flutter, CORS, deployment, infrastructure, or unrelated web-auth file behavior changed.

- [ ] **Step 5: Self-review transactional invariants**

Trace login, first refresh, grace replay, post-grace reuse, provider-too-short rollback, logout, and expiry cleanup. Verify lock order is family then parent; renewal and credential rotation are in one transaction; raw credentials are returned only after commit; replay performs no write; logout commits before remote revoke; ciphertext is unavailable after local revocation/expiry; and all failure envelopes carry a request ID without raw provider text.

- [ ] **Step 6: Correct findings test-first and commit narrowly**

For each finding, add or tighten a failing test, make the smallest correction, and rerun its focused suite plus affected regressions. Use a descriptive commit scoped to the finding; do not squash the logical implementation history.

- [ ] **Step 7: Confirm clean handoff without pushing**

Run: `git status --short` and `git log --oneline origin/main..HEAD`

Expected: the worktree is clean and contains the documentation plan plus logical backend commits. Report exact test totals, PostgreSQL skip/run status, full-suite duration, migration revision, files changed, and commit IDs. Request separate push/PR instructions; do not push or open a PR in this task.
