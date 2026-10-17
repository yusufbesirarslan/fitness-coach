# FitX — Needed Fixes (Triage & Security Review)

**Date:** 2026-10-17
**Commit reviewed:** `551f711` on `claude/amazing-dijkstra-g6xyhr` (== `main` HEAD; through MON-02 AI Starter/Premium allowance ledger `#440`, Apple subscription foundation `#436`, fail-closed deploy recovery gate `#437`, privacy fixes `#438`/`#435`).
**Scope:** 3 parallel read-only deep-dive audits — (1) Security vulnerabilities (auth/authz, CSRF, injection, SSRF, secrets, S3, HMAC tokens, rate-limit/DoS, IDOR); (2) Backend correctness (transactions, concurrency, money/quota/allowance math, timezone, workout lifecycle, error mapping); (3) Architecture + API/frontend (module boundaries, blueprint/flag gating, frontend JS state machines, API contracts, migration safety).
**Method:** Adversarial trace of each attack surface and high-risk flow. Every reported item was verified against actual source (cited `file:line`), not against CLAUDE.md/docstrings. The two highest-value items were manually re-confirmed by the lead before writing.
**Baselines:** `NEEDED_FIXES.md`, `NEEDED_FIXES_2026-08-02.md`, `NEEDED_FIXES_2026-08-14.md`, `TRIAGE_FINDINGS_2026-10-01.md`, `SEC001_*`, `SEC005_*`.

---

## Headline

The codebase remains **mature, disciplined, and defense-in-depth**. All three audits independently reached the same conclusion as the prior triages: the classic attack classes (IDOR, JWT confusion, CSRF, SSRF, prompt/template injection, upload abuse, payment spoofing, workout/completion/quota race conditions) already have explicit, correct, test-pinned mitigations.

- **No new Critical or High-severity vulnerabilities were found.**
- **No confirmed Critical/High correctness bug** in the money/quota/allowance, workout-lifecycle, or concurrency core.
- This run surfaced **1 Medium-High** privacy/caching leak (an allowlist that drifted), **1 Medium** architecture fragility that caused it, **1 Low-Medium** API-contract inconsistency, and a handful of Low / defense-in-depth items.

| # | Severity | Area | Type | Status |
|---|----------|------|------|--------|
| 1 | **Medium-High** | `/meal-log/history` returns per-user meal data + presigned S3 photo URLs with **no `Cache-Control`** → storable by bfcache / shared cache | Privacy / caching | **New (confirmed)** |
| 2 | Medium | `_PRIVATE_JSON_PATHS` no-store allowlist is opt-in across 3 blueprints; nothing enforces coverage (finding #1 is the proof it drifts) | Architecture / fragility | New (confirmed) |
| 3 | Low-Medium | `mobile_subscriptions` reports policy-disabled feature as `retryable=True` 503, and gates the flag **after** auth (diverges from LP17-B1 404-before-auth) | API contract / consistency | New (confirmed) |
| 4 | Low | `s3_helper` ownership check (`expected_user_id`) is opt-in, defaults to `None` → fail-open by caller omission | Security / defense-in-depth | New (confirmed; no live exploit) |
| 5 | Low | Food-discovery capacity refusal (`mobile_nutrition` search/servings/barcode) drops `Retry-After` under the generic `except` | Reliability / backpressure | New (confirmed) |
| 6 | Low | God-modules: `social.py` (1222), `training.py` (989), `tracking.py` (836), `static/nutrition.js` (2899) | Structure / maintainability | Carried / re-confirmed |
| — | Info | 24h premium-verification flicker (dark, forward-looking); 15-min allowance reservation TTL vs slowest feature; `weekly_water` challenge toggle (prior, verify) | Awareness | Notes |

---

## 1. [Medium-High] `/meal-log/history` leaks per-user data (incl. presigned S3 photo URLs) to bfcache / shared caches

**Files:**
- `app/blueprints/nutrition/meallog.py:488-520` — `GET /meal-log/history`, `@require_auth`, returns 14 days of this user's meals and calls `_meal_photo_url(m)` (presigned S3 URLs) per entry.
- `app/blueprints/nutrition/__init__.py:17-27` — `_PRIVATE_JSON_PATHS = {"/meal-log/today", "/api/diary/today"}`; the `after_request` sets `Cache-Control: private, no-store` **only** for paths in that set.
- `app/hooks.py` — confirmed: there is **no** global `Cache-Control` `after_request`.

**Confirmed.** `/meal-log/history` is not in the allowlist and no global header exists, so the response ships with no `Cache-Control`. It carries per-user meal history and short-lived presigned S3 image URLs. Such a response is storable by the browser back-forward cache and any intermediary/shared cache: the device back button (or a shared/proxy cache) can replay a user's food diary and photos after logout or on a shared device. This is precisely the leak the TRIAGE-2026-10-01 #2 policy was introduced to prevent — the opt-in allowlist simply never got this route added.

**Fix (minimal):** add `"/meal-log/history"` to `_PRIVATE_JSON_PATHS` in `app/blueprints/nutrition/__init__.py`. Add a regression test that fails on the pre-fix code. (The structural fix is #2.)

---

## 2. [Medium] `no-store` is an opt-in per-blueprint allowlist with no coverage enforcement

**Files:** `app/blueprints/tracking.py:~61`, `app/blueprints/training.py:~147`, `app/blueprints/nutrition/__init__.py:17` — three independent `_PRIVATE_JSON_PATHS` frozensets, each applied by its own `after_request`.

**Confirmed.** Every new per-user JSON GET must be manually added to the right blueprint's allowlist; nothing enforces it, and finding #1 is the proof that it silently fails to cover new routes. Separately, `/nutrition-plan/active` is **deliberately excluded** (documented at `nutrition/__init__.py:13-16`) because under Playwright request interception a `no-store` answer never fires `requestfinished`, which would hang the PR6 N7 non-vacuity test — i.e. a production security header is being withheld to accommodate a test-harness quirk.

**Fix:** invert the pattern to **default-no-store for authenticated JSON responses** (opt-out for the rare cacheable case), *or* add a single test that enumerates every per-user authed GET JSON route and asserts `no-store` membership (mirroring the repo's existing source-scan drift guards). Fix the PR6 N7 harness (so the production header on `/nutrition-plan/active` can be restored) rather than bending the contract to the tool.

---

## 3. [Low-Medium] `mobile_subscriptions` disabled-state is `retryable=True` and gated after auth

**File:** `app/blueprints/mobile_subscriptions.py:14-34`.

**Confirmed.** When `APPLE_SUBSCRIPTIONS_ENABLED` is off, `_ready` returns `SubscriptionError("SUBSCRIPTIONS_DISABLED", 503)`, and `_failure` maps `status == 503 → retryable=True` (+ `Retry-After: 30`). So an **administratively-disabled** feature is reported to clients as a *transient outage to keep polling* — clients will retry a feature that is off by policy, forever. Also, decorator order is `@require_mobile_auth` (outermost, runs first) → `@_ready`, so the flag check runs **after** auth. This diverges from the sibling dark flags `mobile_ai_allowances.py` and `mobile_today_guidance.py`, which `abort(404)` **before** auth (the LP17-B1 pattern) so a disabled feature is indistinguishable from an absent route.

**Fix:** adopt the 404-before-auth pattern for the disabled state (consistency with the other dark flags), or at minimum return `SUBSCRIPTIONS_DISABLED` with `retryable=False` so clients don't poll a policy-off feature. Note this is a dark/unwired surface today, so impact is limited until the subscription feature goes live — fix before enabling.

---

## 4. [Low / defense-in-depth] `s3_helper` ownership check is opt-in, defaults to fail-open

**Files:** `s3_helper.py:303-314` (`generate_presigned_url`), `s3_helper.py:281-300` (`get_object_bytes`).

**Confirmed, no live exploit.** Both functions enforce owner scoping only when the caller passes `expected_user_id`; it defaults to `None`, in which case any key is signed/downloaded with no owner check. All current callers (`training.py:575`, `pump_checks.py:59`, `mobile_pump_checks/service.py:364`, `validators.py:201`, `progress_physique/queries.py:192`, `ai_coach.py:686`, `mobile_pump_check_comparisons/service.py:383`) were audited and all pass `expected_user_id` correctly, and `models.py:206` renders the row's own key — so there is no cross-user exposure today. The risk is forward-looking: the ownership boundary relies on caller discipline rather than being fail-closed, so a future endpoint that forgets the kwarg silently gets an IDOR on private pump-check/meal/avatar images.

**Fix:** make `expected_user_id` a **required** parameter, or add an AST/source-scan test (mirroring the repo's existing drift guards) asserting no caller omits it.

---

## 5. [Low] Food-discovery capacity refusal loses its `Retry-After`

**File:** `app/blueprints/mobile_nutrition.py:72-110` — `search` / `servings` / `barcode`.

**Confirmed.** These wrap `blocking_concurrency_slot()` (correct) but catch its `BlockingConcurrencyLimit` under the generic `except Exception → _discovery_failure` (`FOOD_PROVIDER_UNAVAILABLE`, 503, `retryable=True`, **no `Retry-After`**). The repo-wide CAPACITY convention is that a capacity refusal is 503 **+ `Retry-After`** (see the web `food.py` `_fatsecret_busy_response` fix from TRIAGE-2026-10-01 #1). The response is still a correct retryable 503; only the backpressure hint is dropped.

**Fix:** catch `BlockingConcurrencyLimit` explicitly before the generic handler and attach `Retry-After` (reuse the web convention value).

---

## 6. [Low] God-modules / large single files

`app/blueprints/social.py` (1222 lines: Feed V2 + friends + moderation + comments), `app/blueprints/training.py` (989), `app/blueprints/tracking.py` (836), and `static/nutrition.js` (2899) concentrate many concerns. The `nutrition` blueprint was already decomposed into a package (good precedent); these have not been. They are also the files most likely to accumulate the next no-store/ordering drift (cf. #1/#2). Non-urgent maintainability; decompose opportunistically.

---

## Informational notes (design tradeoffs / awareness — not defects)

- **24h premium-verification flicker (forward-looking).** `subscriptions.entitlement_view` drops `effective_premium` to False once `last_verified_at` is older than `MAX_VERIFICATION_AGE` (24h), with no background re-verification. Zero-impact while the subscription foundation is dark; but when enforcement is wired live, a premium client with no periodic re-verify (offline / no background refresh) would lose Premium and hit `AI_FEATURE_NOT_IN_PLAN`. Ensure the enforcement slice ships a re-verification trigger at/before the 24h boundary.
- **15-min allowance reservation TTL.** `ai_allowances/service.py` `RESERVATION_TTL`: a hold expiring before `finalize` is consumed (EXPIRED), so a provider call legitimately taking >15 min loses the unit and can't be retried under the same idempotency key. This is the documented anti-abuse bound ("a crash can never mint unlimited free retries"). Confirm the TTL covers the slowest real feature (heavy vision/menu + repair) before go-live.
- **`weekly_water` challenge single-day completion (prior finding, verify).** The 2026-08-14 report #1 flagged that the `water_logged` challenge funnel dedups on a `0 → positive` *transition* rather than a durable per-day marker, so toggling the water count re-arms it. Not re-traced this run; carry forward and verify whether the `active_day` persisted-marker fix was applied.

---

## Coverage / confirmed-correct (notable negatives)

The audits positively verified these as sound (so the clean result reflects coverage, not omission):

- **JWT/Cognito** (`cognito_jwt.py`, `auth_contract.py`): RS256-only; issuer + audience/client_id + `token_use="access"` enforced (ID tokens rejected as API creds); `jwks_unavailable`→503-keep-session vs definitive-reject-destroy-session; forced-JWKS-refresh single-flight + cooldown (forged-`kid` thread-exhaustion closed); leeway pinned to 0 both paths with a drift test.
- **Mobile auth** (`mobile_auth.py`): Bearer-only; access credential by hash + `hmac.compare_digest`; per-request provider re-validation (honors server-side revoke); ownership coherence re-checked; deleted-identity tombstone refused on bind/create; **fails closed** on storage errors.
- **CSRF** (`hooks.py`): global `before_request`, two layers (Origin/Referer + per-session synchronizer, constant-time); `mobile_api` correctly exempt; OAuth callback validates `state`.
- **SQL injection:** all raw SQL (`fitx_mcp/server.py`) parameterized; no `.format`/f-string SQL; no unsafe `text()`.
- **SSRF** (`menu_remote.py`): DNS-resolve-then-pin-to-IP, TOCTOU-free peer re-verify, public-IP allowlist (incl. mapped/6to4/teredo/NAT64), scheme/port/userinfo rejected, redirects re-validated with downgrade block, per-hop cookie-less session, byte/time/redirect budgets, isolated credential-less subprocess.
- **HMAC tokens** (`nutrition_native/tokens.py` + siblings): per-class versioned domain separation, owner bound through the MAC, length-prefixed field encoding (no length-extension/ambiguity), `compare_digest`, nothing stored.
- **Payment verification** (`apple_subscriptions.py`): cryptographic signed-transaction + status-API cross-check; `appAccountToken` MAC-bound; `original_transaction_id` UNIQUE → OWNERSHIP_CONFLICT (no sub sharing); signed-snapshot freshness; provider I/O outside DB locks under `blocking_concurrency_slot`.
- **MON-02 allowance ledger** (`ai_allowances/`): single-conditional-UPDATE bucket math race-safe, CHECK backstops, billed-release cap increments once, replay/conflict semantics consistent, Istanbul period/reset keys consistent.
- **Freemium quota** (`premium.py`): `populate_existing().with_for_update()` closes the stale-identity lost-update; burn-vs-refund on real `status_code`; refund floored at 0.
- **Workout completion/session** (`workout_completion/`, `workout_session/`): fixed lock order, `uq_pump_check_day` durable claim, race-loser + replay → ALREADY_COMPLETED, `expected_checkpoint_revision` under the session lock; checkpoint = single conditional UPDATE keyed on base revision with replay-before-mutate.
- **Timezone/date:** no raw `date.today()`/`utcnow().date()`/`strftime("%d.%m")` in day-boundary logic; day keys via `app/timeutil`; `utcnow()` only for naive-UTC timestamp columns.
- **Frontend:** water state machine (single-flight, one-reconcile-GET, unknown≠0, superseded drop); coach SSE (abort keeps partial, marked+DOMPurify sanitize); `innerHTML` sinks `esc()`-escaped; storage access try/catch-guarded; no CSP-nonce issues found.
- **Migrations:** single Alembic head (`a9c1d2e3f4b5`); table-creating migrations past the create_all boundary `has_table`-guarded; post-R6 migrations declare `expand_contract`.

---

## Recommended order of fixes

1. **#1** (`/meal-log/history` no-store) — one-line addition + regression test; real privacy impact, in-repo template to copy.
2. **#2** (default-no-store / coverage test) — the structural fix that prevents the next drift; restore `/nutrition-plan/active` by fixing the test harness.
3. **#3** (subscriptions disabled contract) — before the subscription feature is enabled live.
4. **#4** (require `expected_user_id`) — cheap fail-closed hardening.
5. **#5** (food-discovery `Retry-After`) — small backpressure correctness.
6. **#6** (god-modules) — opportunistic.

*No files were modified by this review. All findings are read-only triage; apply individually with their own tests.*
