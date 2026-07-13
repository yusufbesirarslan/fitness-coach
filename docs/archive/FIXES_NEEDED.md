# FitX (fitness-coach) — Triage Report & Needed Fixes

> **ARŞİV — GÜNCEL DEĞİL. BU BELGEYİ AÇIK BULGU LİSTESİ OLARAK KULLANMAYIN.**
>
> 2026-07-13'te `docs/STATUS.md` altında birleştirildi (STATUS.md zaten "tek
> kanonik izleyici" olduğunu söylüyor ve kök dizinde `TRIAGE_*.md` çoğalmasını
> yasaklıyordu). Buradaki bulguların bir kısmı ÇOKTAN ÇÖZÜLDÜ; örneğin
> `TRIAGE_FINDINGS.md`, Sprint 2'de kapatılmış olan auth-bypass (S1) ve
> doğrulanmamış-JWT (S3) bulgularını hâlâ AÇIK HIGH gibi gösteriyordu — bir
> olay anında bu, insanı yanlış yola sokar.
>
> Güncel durum: [`docs/STATUS.md`](../STATUS.md). Tarihsel kayıt olarak saklanır.

**Date:** 2026-07-10
**Method:** Read-only deep-dive triage across three parallel tracks — (1) security,
(2) backend correctness/logic, (3) AI integrations & infrastructure.
**Overall assessment:** The codebase is unusually well-hardened. No **Critical**
issues were found. Most classic hazards (IDOR, CSRF, SSRF, XP/streak/water/quota
races, prompt injection, secrets handling) are already closed with defensive code
and comments citing prior fixes. The items below are the residual gaps worth
addressing, ranked by priority.

---

## Priority summary

| # | Severity | Area | Title | File |
|---|----------|------|-------|------|
| 1 | **Medium** | Backend | `diary_log_meal` non-atomic → duplicate ledger rows (double calories) | `app/blueprints/nutrition/diary.py:350` |
| 2 | **High** (avail/cost) | AI/Infra | AI concurrency gate under-counts real model concurrency (fan-out escapes semaphore) | `app/services/ai_gate.py` |
| 3 | **Medium** | Security | No default rate limits — messaging & write endpoints unthrottled | `app/extensions.py:53` |
| 4 | **Medium** | AI/Infra | Freemium quota bypass via concurrent requests (check-then-act TOCTOU) | `app/blueprints/coach.py:144`, `app/services/premium.py:129` |
| 5 | **Medium** | AI/Infra | Wearable token decryption unguarded against key rotation/corruption → 500s | `app/services/wearables/tokens.py:109` |
| 6 | **Medium** | AI/Infra | Training-plan JSON has no truncation salvage → hard 500 on large plans | `app/services/training_generation/service.py:30` |
| 7 | **Low** | Security | Cognito ID token decoded without signature verification (latent landmine) | `app/services/cognito_service.py:191` |
| 8 | **Low** | Backend | Negative override/quick-add macros → DB CHECK IntegrityError → unhandled 500 | `app/blueprints/nutrition/meallog.py:79` |
| 9 | **Low** | Backend | `clamp_serving_macros` silently returns Atwater-invalid servings unchanged | `nutrition_pipeline.py:335` |
| 10 | **Low** | AI/Infra | Per-serving FatSecret results trigger N sequential heavy-LLM calls | `app/services/fatsecret.py:329` |
| 11 | **Low** | AI/Infra | Gate `acquire` blocks worker thread, can starve `/health` during bursts | `app/services/ai_gate.py:46` |
| 12 | **Low** | Backend | Leaderboard tie-break differs between Redis and Postgres paths | `app/services/gamification.py:13` |
| 13 | **Low** | Backend | `_purge_user` CLI misses newer child tables (orphans on SQLite) | `app/cli.py:48` |
| 14 | **Low** | Backend | Referral reward granted before email verification (Cognito path) | `app/blueprints/auth.py:170` |
| 15 | **Low** | AI/Infra | `whoop_resource` forwards arbitrary client query params upstream | `app/blueprints/wearables.py:100` |

---

## Detailed findings

### 1. `diary_log_meal` is non-atomic → duplicate canonical-ledger rows (double calories) — **Medium**
**File:** `app/blueprints/nutrition/diary.py:350-387`

Idempotency is intended via the `CustomMeal.is_logged` flag, but the check-then-set
is not atomic and `MealLog` has **no** unique constraint (verified):

```python
meal = db.session.get(CustomMeal, meal_id)
if meal.is_logged:            # line 356 — plain read
    return ...
entry = MealLog(...)          # line 374 — writes canonical ledger
db.session.add(entry)
meal.is_logged = True         # line 386
db.session.commit()
```

On the single-worker/8-thread gunicorn, a double-click or client retry fires two
`POST /api/diary/meal/<id>/log` within the thread window: both read
`is_logged == False`, both INSERT a `MealLog` row, both commit → **today's
calories/macros in the single canonical ledger are doubled.** This corrupts
`/meal-log/today` totals, the coach's remaining-budget math, protein nudges, and
weekly reports. This is the outlier: every other idempotent write (water
`uq_user_water_day`, daily-activity `uq_daily_activity`, pump-check
`uq_pump_check_day`, coach `PendingAction` delete-claim, friend-accept, referral)
was explicitly hardened.

**Fix:** Make the transition atomic using a guarded UPDATE as the claim (same
pattern already used in `friend_accept` / `consume_referral`):

```python
claimed = CustomMeal.query.filter_by(id=meal_id, user_id=current_user.id, is_logged=False)\
    .update({"is_logged": True}, synchronize_session=False)
if not claimed:
    db.session.rollback()
    return jsonify({"error": t("route.meal_already_logged")}), 400
# add the MealLog and commit in the same transaction
```

---

### 2. AI concurrency gate under-counts true model concurrency — **High** (availability/cost)
**Files:** `app/services/ai_gate.py:39,74` · `app/services/ai_nutrition.py:835,862-868` · `app/services/fatsecret.py:640,762`

`ai_concurrency_gate` caps concurrent *requests* at `AI_MAX_CONCURRENCY=5`, but the
gated `/api/menu/analyze` route fans out internally: `_estimate_macros_llm` runs up
to `_LLM_MACRO_MAX_WORKERS=4` parallel Bedrock calls, and `_lookup_macros_fatsecret`
up to `_FS_LOOKUP_MAX_WORKERS=6` parallel HTTP calls.

5 concurrent menu analyses → up to **20 concurrent Bedrock invocations** (plus ~30
FatSecret calls) while the gate reports "5 in flight." This blows the Bedrock
account/region throttle; 429s cascade, `_heavy_chat` silently degrades everything to
`gpt-4o-mini`, and cost/latency spike. The gate's stated purpose (bounding heavy-AI
load) is not enforced at the model-call layer.

**Fix:** Make the semaphore reflect real model concurrency — acquire a slot per
`_heavy_chat` (a module-level gate inside `ai.py`), or set
`AI_MAX_CONCURRENCY × _LLM_MACRO_MAX_WORKERS` ≤ the Bedrock TPM/RPM budget and lower
the fan-out worker counts. Document the multiplier.

---

### 3. No default rate limits — messaging & write endpoints unthrottled — **Medium**
**File:** `app/extensions.py:53` (verified: `Limiter(...)` has no `default_limits`)
Affected: `social.py:288` (`friend_request`), `:453` (`chat_send`), `:484`
(`send_suggestion`), plus `tracking.py` `/log`, `/update-weight`, `/water`,
`/api/activity/log`, and the `diary_*` create endpoints.

Only AI/scrape/auth/search routes carry explicit `@limiter.limit`. All other
authenticated state-changing endpoints have **zero** throttling. A single account
can flood a friend with unlimited chat/suggestion messages (harassment/notification
spam) and create unbounded rows in `Message`, `WeeklyLog`, `CustomMeal/Item`, etc.
(application-level DoS / storage exhaustion). Expensive AI paths are protected, so
cost-amplification is contained, but abuse/spam and row-flooding are not.

**Fix:** Add conservative `default_limits` (e.g. `["600 per hour"]`) to the
`Limiter`, and add explicit per-user limits to `chat_send`, `send_suggestion`,
`friend_request` keyed by `_user_or_ip_key`.

---

### 4. Freemium quota bypass via concurrent requests (TOCTOU) — **Medium**
**Files:** `app/blueprints/coach.py:144-167` · `app/services/premium.py:129-142`

Both gates read `remaining_ai_*(user)` *before* the expensive call and `record_*`
only *after* a 200. `_record_counter` serializes the increment (no lost update), but
nothing stops two concurrent requests from both passing the pre-check while
`remaining == 1`. A free user firing two parallel `POST /training-plan`
(`FREE_WEEKLY_AI_PLANS=1`) gets both Sonnet plans generated and recorded → `used=2`,
double the entitlement and cost per week.

**Fix:** Make the gate atomic — increment-and-check under the same `with_for_update`
lock *before* running and refund on non-200, or use a Redis `INCR` gate keyed on
`user:kind:week`.

---

### 5. Wearable token decryption unguarded against key rotation/corruption — **Medium**
**Files:** `app/services/wearables/tokens.py:109-110` · `app/services/wearables/crypto.py:31-34`

`get_wearable_connection` calls `decrypt_token(...)` with no try/except.
`Fernet.decrypt` raises `InvalidToken` if `WEARABLE_TOKEN_KEY` was rotated or the
ciphertext is corrupt. After a key rotation (or a DB restore with old ciphertext),
every `/api/wearables/*` call for previously-connected users throws uncaught
`InvalidToken` → 500/502, and the user cannot cleanly re-connect.

**Fix:** Catch `InvalidToken` in `decrypt_token`/`get_wearable_connection`, return
`None`, and mark the connection `status="reauth_required"` so the UI prompts a
reconnect instead of 500-ing.

---

### 6. Training-plan JSON has no truncation salvage → hard 500 on large plans — **Medium**
**Files:** `app/services/training_generation/service.py:30-36,47-53` · `app/blueprints/training.py:93`

`_heavy_chat(..., max_tokens=4000)` for a full 7-day plan can hit the token ceiling;
`_extract_json` does a naive `rfind("}")` with no repair. The menu pipeline has
`_salvage_truncated_categories`/`_repair_truncated_json`, but training plans do not.
On truncation → `JSONDecodeError` or a partial object failing `validate_generated_plan`
("program tam 7 gün") → route returns 500. Legitimate generations hard-fail with no
retry/salvage. (It correctly returns 500 not 200, so quota isn't burned — but it is a
dead-end UX.)

**Fix:** Reuse the menu-side JSON repair/salvage, or on parse failure retry once with
a higher `max_tokens`; surface a "try again / shorten" message instead of a generic 500.

---

### 7. Cognito ID token decoded without signature verification (latent landmine) — **Low**
**Files:** `app/services/cognito_service.py:191` (`_decode_claims`) · duplicate at `app/services/cognito_idp.py:188`

`_decode_claims` base64url-decodes the JWT payload and reads `sub`/`email` **without
verifying the RS256 signature** against Cognito's JWKS. This is **not currently
exploitable**: the token is consumed only from the server's own `initiate_auth`
response over TLS, never from client input, and login additionally requires
`claims["sub"] == user.cognito_sub`. The risk is *reuse* — if this helper is ever
wired to a client-supplied token (a future mobile/SPA "log in with ID token"
endpoint, or re-enabling the currently-404'd Hosted-UI callback), an attacker could
forge a JWT with an arbitrary `sub` and authenticate as any user.

**Fix:** Verify the signature against the Cognito JWKS (validating `iss`,
`aud`/`client_id`, `exp`) using `python-jose`/`PyJWT`. If keeping the trusted-transport
optimization, add an explicit guard/warning that the function is unreachable from
request input, and collapse the two duplicate copies into one.

---

### 8. Negative override/quick-add macros → DB CHECK IntegrityError → unhandled 500 — **Low**
**Files:** `app/blueprints/nutrition/meallog.py:79-101` (override path) · `app/blueprints/nutrition/diary.py:113-131` (`quick_add_meal`)

`diary.py` deliberately floors negatives to 0 before clamping, but the override path
in `log_meal` and `quick_add_meal` call `clamp_serving_macros` **without** that
flooring. `clamp_serving_macros` passes negatives through unchanged, so a
client-supplied negative macro reaches the commit (no try/except) and trips the
`ck_meal_log_macro_bounds` CHECK constraint → **HTTP 500** instead of a clean 400.
(DB integrity is protected; the defect is the ungraceful 500.)

**Fix:** Floor negatives to 0 before `clamp_serving_macros` in these two paths
(mirror the diary code), or wrap the commit and return 400 on `IntegrityError`.

---

### 9. `clamp_serving_macros` silently returns Atwater-invalid servings unchanged — **Low**
**File:** `nutrition_pipeline.py:335-368`

The "physical sanity gate" only builds rescale ratios for the absolute caps
(`MAX_SERVING_KCAL`, `MAX_SERVING_MACRO_G`, `MAX_SERVING_FAT_G`). When `check_serving`
returns invalid for another reason (e.g. Atwater energy-conservation:
`{cal:2000, protein:0, carbs:0, fat:0}`), `ratios` is empty, `scale` stays `1.0`, and
the **original values are returned verbatim** and written to the ledger. Callers
relying on this gate to guarantee sane macros don't get that guarantee for the
non-cap violation class.

**Fix:** Either document that the gate only enforces absolute caps, or add a
proportional/zeroing correction for Atwater-inconsistent totals.

---

### 10. Per-serving FatSecret results trigger N sequential heavy-LLM calls — **Low**
**File:** `app/services/fatsecret.py:329-374` (call at :353)

Inside `for f in foods:` (up to 8 results), every per-serving result independently
calls `_estimate_serving_weights_llm([food_name], ...)` → `_heavy_chat` (Bedrock)
once per result, **sequentially**. One coach food lookup can become up to 8
sequential Bedrock round-trips: multi-second latency and 8× cost, inflating the
AI-gated request's hold time (compounds #2/#11).

**Fix:** Collect all per-serving `food_name`s first and make one batched
`_estimate_serving_weights_llm(names)` call (the function already accepts a list),
then map results back.

---

### 11. Gate `acquire` blocks the worker thread, can starve `/health` during bursts — **Low**
**File:** `app/services/ai_gate.py:46`

The docstring promises remaining gunicorn threads stay free for `/health`, but
`semaphore.acquire(timeout=AI_GATE_WAIT_SECONDS)` (default 10s) **blocks the worker
thread while waiting**. With `--threads 8` and 5 slots, ≥8 concurrent AI requests =
5 running + 3 blocked-in-acquire → all 8 threads occupied, and ungated `/health` has
no thread for up to 10s. During a deploy this can miss the `HEALTHCHECK --timeout=5s`
and trip the deploy health-gate rollback.

**Fix:** Drop `AI_GATE_WAIT_SECONDS` toward 0 (fail-fast 503 instead of holding a
thread), reserve threads explicitly (`threads > AI_MAX_CONCURRENCY + wait depth`), or
serve `/health` from a path that never blocks on the semaphore.

---

### 12. Leaderboard tie-break differs between Redis and Postgres paths — **Low**
**File:** `app/services/gamification.py:13-15, 338` vs `359-365`

Postgres breaks exact ties with `User.id.asc()` (numeric). The Redis composite score
`xp*100000 + min(streak,99999)` is identical for two users with equal XP *and* equal
streak; `ZREVRANGE` then orders them by **reverse lexicographic** member id (`"10"`
vs `"2"`) — neither numeric nor the same direction as Postgres. Tied rows appear in a
different relative order depending on whether Redis is up. Cosmetic only.

**Fix:** Encode id into the Redis score's low bits (sign-inverted), or resolve ties
in application code after fetching.

---

### 13. `_purge_user` CLI misses newer child tables (orphans on SQLite) — **Low**
**File:** `app/cli.py:48-89`

The manual dependent-delete list (needed because SQLite doesn't enforce FK CASCADE)
omits tables added later: `PumpCheckLike`, `PumpCheckComment`,
`UserWearableConnection`, `WearableSleepLog`, `WearableActivityLog`,
`WearableWorkoutLog`. `flask cleanup-test-users --yes` against SQLite leaves orphaned
rows. Postgres prod is covered by `ondelete="CASCADE"`, so this is dev/test-only —
but it defeats the sweep's purpose.

**Fix:** Add the missing models to the manual delete loop (PumpCheck children before
PumpCheck; wearable tables before `User`).

---

### 14. Referral reward granted before email verification (Cognito path) — **Low**
**Files:** `app/blueprints/auth.py:170` · `app/services/referral.py:44-75`

In the Cognito branch, `consume_referral(user, ref_code)` runs immediately after the
local `User` row is committed — before the invitee confirms their email. Self-referral
is blocked, but nothing requires the invited account to ever verify, so a user can
farm `REFERRAL_REWARD_XP` (75) per unverified signup. `register` is rate-limited
(5/hour), which throttles but does not prevent it.

**Fix:** Defer `consume_referral` until after successful email confirmation
(`verify_confirm`) for the Cognito flow.

---

### 15. `whoop_resource` forwards arbitrary client query params upstream — **Low**
**File:** `app/blueprints/wearables.py:100-106`

`request.args.to_dict()` is passed straight into `adapter.request(...)`. Scoped to
the caller's own token (not an IDOR), but it lets a user inject arbitrary
params/paging into upstream WHOOP calls and amplify third-party quota use.

**Fix:** Whitelist the params (`start`/`end`/`limit`) per resource instead of
forwarding the raw dict.

---

## Verified sound (examined, no action needed)

- **IDOR/access control:** every by-ID load re-checks ownership (`supplement_*`,
  `friend_*`, `respond_suggestion`, `diary_*`, `pump_check_*`); S3 keys bound with
  `expected_user_id`.
- **CSRF:** two-layer (Origin/Referer + per-session synchronizer token,
  `compare_digest`); GET state-change routes removed; `/logout` guarded.
- **SSRF (menu scraper):** positive `ip.is_global` allow-list, port allow-list,
  per-hop re-validation, DNS-rebind pinning, size caps.
- **MCP server:** stdio/in-process only; HTTP behind `FITX_MCP_ALLOW_HTTP=1` +
  loopback; coach-tool `user_id` always injected server-side, never from the model;
  `_assert_principal` defense-in-depth.
- **Prompt injection:** friend/menu data fenced and fence-tokens stripped;
  `manage_user_memory` key allow-list + length cap.
- **Secrets/config:** no hardcoded keys; AWS via IAM instance profile;
  `SECRET_KEY`/`DATABASE_URL` fail-closed in prod; secure/HttpOnly/SameSite cookies;
  services bound to loopback in compose.
- **Crypto/upload:** Fernet (authenticated) wearable tokens; base64 + Pillow decode +
  `MAX_IMAGE_PIXELS` bomb guard; S3 SSE-AES256 + private bucket + presigned URLs.
- **Timezone/day-keys:** no production bypass of `app/timeutil`; streak/XP/water use
  locked column reads; MealLog canonical-ledger migration chain is sound.
- **Provider fallback:** any Bedrock exception → OpenAI; correctly avoids
  provider-switching after a tool side-effect.

---

## Recommended order of work

1. **#1** (diary double-count) — real data corruption, small localized fix.
2. **#2 / #4** (AI gate fan-out, quota TOCTOU) — cost/availability under real load.
3. **#3** (rate limits) — abuse/spam surface, low-effort config change.
4. **#5 / #6** (wearable decrypt guard, training-plan salvage) — 500-on-failure UX.
5. **#7–#15** — hardening and polish as capacity allows.
