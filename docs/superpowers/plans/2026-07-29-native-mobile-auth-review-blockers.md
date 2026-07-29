# Native Mobile Authentication Review Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct refresh failure classification and revocation transaction handling, then add a disabled-by-default mobile-auth dark-launch gate without changing web authentication.

**Architecture:** Keep login validation behavior unchanged and add a refresh-only provider-token classifier that separates transient JWKS failure from definitive refreshed-token rejection. Route every irreversible family revocation through one transaction helper that applies revocation, commits, rolls back on failure, and raises a normalized retryable service error. Gate mobile configuration validation, database readiness, and blueprint registration on one strict `MOBILE_AUTH_ENABLED` boolean that defaults off.

**Tech Stack:** Python, Flask, Flask-SQLAlchemy, Alembic, pytest, PostgreSQL, SQLite.

## Global Constraints

- Preserve AxisAI-issued opaque sessions, exact-one-child rotation, deterministic grace replay, per-device family isolation, and the seven-day non-sliding absolute lifetime.
- Never persist or log plaintext credentials, derivation keys, passwords, Cognito tokens, authorization headers, or provider payloads.
- Keep `/api/v1/` Bearer-only with normalized JSON errors; preserve all existing web Flask-Login, cookie, CSRF, route, and CORS behavior.
- Default `MOBILE_AUTH_ENABLED` to `0`; require a valid independent derivation keyring and active version only when enabled.
- Do not deploy, mutate AWS/Cognito, modify Flutter, force-push, open a PR, or rewrite history.

---

### Task 1: Refresh-Specific Provider Failure Classification

**Files:**
- Modify: `tests/test_mobile_auth_service.py`
- Modify: `tests/test_mobile_auth_api.py`
- Modify: `app/services/mobile_auth.py`

**Interfaces:**
- Consumes: `cognito_jwt.validate_token(token, "access", leeway_seconds=...)` and `MobileAuthSession.cognito_sub`.
- Produces: `_validate_refreshed_provider(token, expected_sub)` returning verified claims, raising retryable `MobileAuthFailure` only for `jwks_unavailable`, and raising a private definitive-refresh exception for all other token or subject failures.

- [ ] **Step 1: Write failing service and API tests**

Add parameterized refreshed-token tests covering invalid signature, malformed token, wrong issuer, wrong audience, wrong use, expiry, missing claims, and subject mismatch. Assert definitive failures revoke the one family, clear ciphertext, revoke credential rows, commit, and surface `AUTH_REFRESH_FAILED`; assert `jwks_unavailable` returns retryable 503 and preserves all rows and ciphertext. Add an API assertion that `/api/v1/auth/refresh` never emits `AUTH_INVALID_CREDENTIALS`.

- [ ] **Step 2: Run the new refresh tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py -k 'refreshed or refresh_validation or subject_mismatch or jwks'`

Expected: definitive validation returns the login-oriented code or leaves the family active.

- [ ] **Step 3: Implement the refresh-only classifier**

Call the canonical JWT validator directly. Map only `TokenValidationError("jwks_unavailable")` to retryable `AUTH_TEMPORARILY_UNAVAILABLE`; map every other `TokenValidationError`, a missing/non-matching `sub`, and invalid refreshed-token expiry to the private definitive-refresh exception. Catch that exception in `refresh()`, roll back pending rotation work, reload and revoke only the locked family, commit, then return `AUTH_REFRESH_FAILED`.

- [ ] **Step 4: Run focused refresh tests and confirm GREEN**

Run the Step 2 command and require all selected tests to pass.

### Task 2: Normalize Every Revocation Commit Failure

**Files:**
- Modify: `tests/test_mobile_auth_service.py`
- Modify: `tests/test_mobile_auth_api.py`
- Modify: `app/services/mobile_auth.py`

**Interfaces:**
- Consumes: `_revoke_family(family, reason, now)`.
- Produces: `_revoke_family_and_commit(family, reason, now)` which applies revocation, commits, rolls back on any storage failure, and raises retryable `AUTH_TEMPORARILY_UNAVAILABLE` with reason `storage_unavailable`.

- [ ] **Step 1: Write failing parameterized revocation tests**

Cover access-time provider validation failure, ownership mismatch, absolute expiry, refresh expiry, post-grace reuse, definitive Cognito refresh rejection, logout, and cleanup. Patch the revocation commit to fail and assert a typed retryable 503-domain failure; exercise API-owned categories through the test client and assert the normalized JSON envelope rather than HTML 500.

- [ ] **Step 2: Run revocation tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py -k 'commit_failure or storage_failure or normalized'`

Expected: currently unguarded commits raise raw database exceptions in at least provider-validation, ownership, reuse, and provider-rejection paths.

- [ ] **Step 3: Centralize the revocation transaction**

Replace every `_revoke_family(...); db.session.commit()` pair with `_revoke_family_and_commit(...)`. Preserve the required success code only after the helper returns. Keep logout provider revocation post-commit and best-effort. Let cleanup raise the same typed storage failure so its CLI caller cannot mistake a failed revocation for success.

- [ ] **Step 4: Run focused revocation tests and confirm GREEN**

Run the Step 2 command and require all selected tests to pass.

### Task 3: Disabled-by-Default Dark Launch

**Files:**
- Create: `tests/test_mobile_auth_feature_gate.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_mobile_credentials.py`
- Modify: `app/config.py`
- Modify: `app/__init__.py`
- Modify: `app/services/mobile_credentials.py`

**Interfaces:**
- Produces: `app.config["MOBILE_AUTH_ENABLED"]` as a strict environment boolean, defaulting to `False`.
- Consumes: the flag in blueprint registration and derivation-key readiness startup checks.

- [ ] **Step 1: Write failing startup and route tests**

Create apps with the flag absent/`0` and both key settings absent; assert startup succeeds, no endpoint starts with `mobile_api.`, and `/api/v1/auth/login`, `/api/v1/auth/refresh`, `/api/v1/auth/logout`, and `/api/v1/account/me` are unavailable. Create enabled apps with missing/invalid keys and assert fail-closed startup; create an enabled app with valid keys and assert the exact approved routes exist. Compare web route methods plus representative web login/CSRF/cookie behavior in enabled and disabled apps.

- [ ] **Step 2: Run feature-gate tests and confirm RED**

Run: `python -m pytest -q tests/test_mobile_auth_feature_gate.py tests/test_mobile_credentials.py`

Expected: disabled startup still parses the keyring and mobile routes remain registered.

- [ ] **Step 3: Implement one strict gate**

Parse `MOBILE_AUTH_ENABLED` with default `0` and accepted values `0`/`1`; reject malformed values. When disabled, store only `MOBILE_AUTH_ENABLED=False`, skip all mobile numeric/keyring parsing, omit the mobile blueprint import/registration, and skip derivation-key readiness. When enabled, retain every current fail-closed validation and route invariant. Set `MOBILE_AUTH_ENABLED=1` only in the hermetic test environment so existing mobile tests continue to exercise the enabled mode.

- [ ] **Step 4: Run feature-gate tests and confirm GREEN**

Run the Step 2 command and require all selected tests to pass.

### Task 4: Rollout Documentation and Complete Validation

**Files:**
- Modify: `.env.example`
- Modify: `docs/adr/0001-native-mobile-authentication.md`
- Modify: `docs/superpowers/specs/2026-07-28-native-mobile-authentication-design.md`
- Modify: `docs/superpowers/plans/2026-07-28-native-mobile-authentication.md`
- Modify: `docs/cognito.md`

**Interfaces:**
- Produces: the five-step disabled deploy, additive migration, independent key provisioning, readiness verification, and controlled enablement runbook.

- [ ] **Step 1: Update configuration and architecture documentation**

Document `MOBILE_AUTH_ENABLED=0`, the absence of any default derivation key, and this exact rollout order: merge/deploy disabled; apply the additive migration; provision independent keyring and active version; verify readiness; enable in a controlled deployment. State that disabling omits routes and mobile readiness checks while leaving web auth untouched.

- [ ] **Step 2: Run the full required validation ladder**

Run focused tests; the complete mobile/auth gate from the approved plan; the web/auth regression suite; the opt-in disposable PostgreSQL concurrency test; migration graph, upgrade/downgrade/upgrade, head, and schema-drift checks; then one complete pytest suite with a sufficiently large timeout.

- [ ] **Step 3: Audit scope and security**

Run `git diff --check`; scan changed and committed files plus fixtures/snapshots/log calls for raw credentials, hashes, keys, passwords, tokens, authorization headers, and provider payloads; confirm no Flutter, infrastructure mutation, deployment, CORS expansion, or web-auth behavior change.

- [ ] **Step 4: Self-review and commit logically**

Trace every refresh and revocation branch, verify result codes occur only after successful commit, inspect the feature gate in both modes, and commit the implementation and documentation in logical reviewable commits without amend/squash/rebase.

- [ ] **Step 5: Fast-forward push and verify**

Fetch the reviewed remote branch, require it to be an ancestor of local HEAD, push normally without force, refresh the remote-tracking ref, and require exact local/remote HEAD equality plus a clean worktree.
