# Fitness Coach (FitX) — Triage & Security Review

**Date:** 2026-10-01
**Method:** Three parallel deep-dive agents — (1) security vulnerabilities, (2) backend logic / data integrity, (3) frontend / API contract / architecture — each doing read-only triage over the repo. Findings below were cross-checked, and the two highest-value items were manually verified against the source.

---

## Executive summary

**The codebase is in very good shape.** All three reviewers independently reported the same signal: this is an unusually well-hardened application with extensive evidence of prior security and correctness triage (the `S*`, `F*`, `Hardening PR`, and `triage` remediation history throughout `app/` and `CLAUDE.md`).

- **No critical or high-severity vulnerabilities were found.**
- Authentication, authorization/ownership scoping, SQL parameterization, SSRF defenses, secret handling, CSRF, CSP, and the transaction/concurrency surfaces are all implemented to a high standard.
- The findings are low / medium-low severity: a few contract inconsistencies, defense-in-depth gaps, and rare edge-case correctness divergences.

**Recommended order of fixes:** #1 (food-search overload masking) first — it's a one-line contract violation with real user impact and an in-repo correct template to copy. Then #2 (private-data cache headers), then #3 (UserSession selector unification).

---

## Resolution (2026-10-02, PR #374)

| # | Status | Change |
|---|---|---|
| 1 | Fixed | `food_search` returns `_fatsecret_busy_response` (503 + `Retry-After: 15`). This degrade had been pinned by `tests/test_food_routes.py`/`tests/test_ai_gate.py` and `docs/CAPACITY.md` ("Boş sonuç (degrade)"), all from the same PR (#319) that wrote the global "never 'no result'" rule; tests + doc row updated. `static/nutrition.js` (both search dropdowns) now shows the server's busy text as a non-selectable `.autocomplete-status` line (`role="status"`) on a non-OK answer instead of "no result"; the NUTR-PR4 invariant (a failed search offers no selectable item) still holds. |
| 2 | Fixed | Path-keyed `private, no-store` guards extended: `tracking` (7 paths, joined to the Progress set), `training` (`/workout/status`, `/water`), new `nutrition` blueprint guard (`/meal-log/today`, `/api/diary/today`). `/training/bootstrap` already set it (report was wrong there). `/nutrition-plan/active` is deliberately left out: under Playwright request interception a `no-store` answer never fires `requestfinished`, so the PR6 N7 non-vacuity test (an injected duplicate plan read) hangs instead of failing. Reproduced in isolation; over a real network the same concurrent `no-store` fetches resolve normally. |
| 3 | Fixed | Every "latest `UserSession`" selector — 15 ORM sites incl. the weight write path, plus raw SQL in `coach_context_queries.py` and `fitx_mcp/server.py` — orders `created_at DESC, id DESC`. |
| 4 | Fixed | `get_active_plan` and its three inline mirrors (`workout_state`, `workout_session`, `/workout/complete`) order `created_at DESC, id DESC`. |
| 5 | Fixed | `fitx_ref` cookie takes `secure=SESSION_COOKIE_SECURE` (the session cookie's gate). |
| 6 | Not a bug | The address is already masked via `email_service.mask_email` (`y***@example.com`), the repo-wide convention. No change. |
| 7 | Fixed | `auth.js` storage reads/writes wrapped in try/catch; an unguarded throw in `initTheme` also aborted the DOMContentLoaded handler on login/register/reset pages. |

Regression tests: `tests/test_triage_2026_10_01_fixes.py` (each fails on the pre-fix code; includes source-scan drift guards for #3/#4).

## Needed fixes (ranked)

### 1. [MEDIUM] `GET /api/food/search` masks server overload as "no results" (HTTP 200)
- **File:** `app/blueprints/food.py:65-68` *(verified)*
- **Problem:** On `BlockingConcurrencyLimit` the handler sets `results = []` and returns `200 {"results": []}`:
  ```python
  except BlockingConcurrencyLimit:
      current_app.logger.warning("food_search event=blocking_capacity_exhausted")
      results = []
  return jsonify({"results": results})   # 200
  ```
  Every other FatSecret-backed route in the **same file** (`food_by_barcode` at `:85-86`, and `:88,151,205,231,276`) handles the same exception by returning `_fatsecret_busy_response(...)` = **503 + Retry-After**. The `servings-by-name` handler even carries an explicit comment that this is required: *"kapasite reddi bir 'besin bulunamadı' DEĞİLDİR → açık 503 + Retry-After."*
- **Violates the documented global rule** (CLAUDE.md): *"Kapasite reddi (`BlockingConcurrencyLimit`) 503 + Retry-After'a çevrilir, 'sonuç bulunamadı'ya ASLA."*
- **Failure scenario:** Under AI/scrape concurrency pressure, a user typing a food name gets the dropdown's "no result" message (`nutrition.js` renders `nutrition.no_result_freetext`), wrongly concludes the food doesn't exist, and gets no backpressure/Retry-After.
- **Fix:** One line — return `_fatsecret_busy_response("food_search")` instead of `results = []`.

### 2. [LOW-MEDIUM] `Cache-Control: private, no-store` applied inconsistently to private authenticated JSON endpoints
- **Files:** per-path `after_request` guards exist for some routes (`tracking.py:60-65`, `training.py:144-149`, `nutrition/day_view.py:37`, `profile.py:23-27`) and for the whole `mobile_api` blueprint — but **many equally-private web JSON endpoints get no cache header**:
  - `/meal-log/today` (`nutrition/meallog.py:339`), `/water` (`training.py:911`), `/checkin-history` (`tracking.py:364`), `/nutrition-plan/active` (`nutrition/plan.py:73`), `/workout/status` & `/training/bootstrap` (`training.py`), `/api/diary/today` (`nutrition/diary.py:623`), `/api/progress/workout` (`tracking.py:657`), `/api/progress/heatmap` (`tracking.py:842`), `/api/progress/achievements` (`tracking.py:883`), `/last-session`, `/dashboard-nudges`, `/api/activity/today`.
- **Failure scenario:** Per-user data (weights, meals, workout state) can be retained by bfcache/disk cache and shown via the back button after logout on a shared device. Low exploitability (authenticated `fetch()`es), but an inconsistency with the app's own stated policy.
- **Fix:** Apply the same `private, no-store` guard uniformly; ideally a shared helper/decorator so new private endpoints inherit it.

### 3. [MEDIUM-LOW] `UserSession` "latest session" selector diverges — stale calorie/macro target after a weight update
- **Canonical authority** uses `ORDER BY created_at DESC, id DESC`:
  - `app/services/account_profile.py:209` (onboarding, `/` gate, `GET /setup`, mobile training readiness)
  - `app/services/mobile_nutrition/queries.py:85` (native `/api/v1/nutrition/diary/today`)
- **But the weight-WRITE path and ~15 readers use `created_at DESC` only (no `id` tiebreak):**
  - `app/blueprints/tracking.py:288-290` *(verified)* — `_apply_weight_to_profile` **writes** `target_calories`/`bmr`/`tdee` into the row picked here (`POST /checkin`, `POST /update-weight`)
  - `nutrition/meallog.py:387,541`, `nutrition_day_view.py:193`, `barcode.py:287,490`, `menu.py:304`, `plan_facts.py:205`, `analytics_engine.py:114`, `ai_coach.py:153`, `coach.py:129`, `nutrition/plan.py:142`
- **Why reachable:** `POST /chat` (`coach.py:159`) **appends a new `UserSession` row on every coach turn**, so users genuinely accumulate multiple rows. The maintainers added `.id.desc()` to the canonical selectors deliberately — they know `created_at` ties are reachable.
- **Failure scenario:** With two `UserSession` rows at equal `created_at` (two rapid `/chat` turns, or onboarding+chat within the same timestamp resolution), the weight write lands in the `created_at DESC` row while mobile Today / profile / native nutrition read the `created_at DESC, id DESC` row → the recalculated target never reaches the authoritative reader, and web vs native disagree. Rare (microsecond `created_at` resolution), hence medium-low — but a correctness divergence, not style.
- **Fix:** Unify all "latest `UserSession`" reads (and the write path) onto the canonical `created_at DESC, id DESC` ordering.

### 4. [LOW] `TrainingPlan.get_active_plan` lacks the `id` tiebreak that the pump-check selector uses
- **Files:** `app/services/today_facts.py:82` and `app/blueprints/training.py:376` use `ORDER BY created_at DESC` only; `app/services/pump_checks.py:85` uses `created_at DESC, id DESC`.
- **Failure scenario:** In a legacy two-plan state at equal `created_at` (`plan_owner_lock.py` acknowledges such states exist), `get_active_plan` is nondeterministic while pump-check logic is deterministic → workout-state/fingerprint resolution can pick a different plan than pump-check logic, and `plan_replacement.replace_training_plan` can raise a spurious 409 `lineage_mismatch` (self-heals on the next replacement). Edge-case only.
- **Fix:** Add `, TrainingPlan.id.desc()` to `get_active_plan`.

### 5. [LOW] Referral cookie set without `Secure` flag
- **File:** `app/blueprints/pages.py:53` — `set_cookie("fitx_ref", ..., samesite="Lax", httponly=True)` omits `secure=True`, unlike session/remember cookies which gate Secure on non-dev (`config.py:454-457`).
- **Impact:** On a downgraded HTTP request the 16-char referral code is sent in cleartext. Not auth material, HttpOnly + SameSite=Lax — low blast radius.
- **Fix:** Add `secure=not _is_dev` to match the other cookie boundaries.

### 6. [LOW] Recipient email (PII) logged at INFO on password-change
- **File:** `app/services/account_recovery.py:378` — `"[AUTH-EMAIL] password-changed kuyruklandı: user=%s to=%s"` logs the destination email.
- **Impact:** The rest of the codebase logs only exception *types* and user ids (triage #7 log-hygiene). This is an inconsistency letting anyone with log read access enumerate user emails.
- **Fix:** Drop or hash the `to=%s` value.

### 7. [LOW] `auth.js` theme `localStorage` access is unguarded
- **File:** `static/auth.js:15,19` (called from `DOMContentLoaded` at `:427`) — bare `localStorage.setItem`/`getItem`, unlike `analytics.js:22-26` and `coach_widget.js:298-301` which wrap storage in try/catch.
- **Failure scenario:** In a context where storage access throws (historically Safari private mode, sandboxed/storage-blocked contexts), `initTheme()` throws on login/register/reset pages. Contained impact (theme-only), but an inconsistency on a critical auth surface.
- **Fix:** Wrap the storage reads/writes in try/catch per the existing pattern.

---

## Lower-confidence / acknowledged items (not clear bugs)

- **`food.py` raw machine-code error envelopes** (`{"error": "invalid_barcode"}`, `invalid_meal`, `invalid_serving` at `food.py:82,128,164,177`) vs the i18n `{"error": t(...)}` envelopes elsewhere. Likely intentional client-consumed codes — flagging the inconsistency only.
- **Coach-widget menu dish "Add" sends slot tokens `/meal-log` rejects (400)** (`coach_widget.js:860-883` → `/meal-log` accepts only the four canonical labels). This is the **documented known defect** in CLAUDE.md's NUTR-PR4 entry ("NOT fixed (reported)") — live but already tracked.
- **`challenges.record_event` reward-step failure** rolls back the event's progress increment along with the completion (self-corrects on the next matching event; no permanent loss). Very low impact.
- **`POST /chat` appends an unbounded `UserSession` per turn** (`coach.py:159`) — by design, but it is the root cause that makes finding #3 reachable and causes unbounded row growth. Worth noting for the data model.
- **CSP `style-src-attr 'unsafe-inline'`** (`app/hooks.py:67`) — an explicitly documented, accepted tradeoff (CLAUDE.md triage #8). Not a new finding.
- **Single-proxy IP-trust invariant** — `ProxyFix(x_for=1)` + loopback-binding behind host nginx means `remote_addr` (rate-limit + deep-health CIDR keys) is only trustworthy as deployed. Correct as-is; keep the loopback-binding invariant enforced/documented.

---

## Surfaces verified SOUND (no issues found)

**Security:** Cognito JWT validation (RS256, exp leeway pinned 0, issuer/audience/`token_use`, single-flight+cooldown JWKS refresh); auth contract (retired skew knob rejected at boot, access-token-only); mobile Bearer auth (constant-time hash compare, refresh rotation + reuse detection + family revocation, credential-epoch fence); ownership/IDOR scoping (meal/item, social friend-scoping, pump-check delete, comparison owner-check — all non-oracle on cross-owner); CSRF (two-layer, fail-closed, mobile exempt by design); SQL injection (all raw SQL uses `%s` binding; f-strings only in migrations with hardcoded identifiers); secrets (Fernet-encrypted Cognito tokens, boot-enforced keys, no token/password values logged); S3 (server-selected bucket, `expected_user_id` enforcement, key-grammar regex, SSE-AES256); image/upload validation (Pillow verify, MIME match, 40MP bomb cap, size caps, fails closed); account deletion (Bearer-only, anti-resurrection tombstone); coach handoff (closed allowlist, server-re-derived facts); HMAC constructions (subkey derivation, length-prefixed canonical serialization, constant-time compare).

**Backend logic / data integrity:** `gamification.complete_quest_for_user`/`_claim_quest`/`award_xp`; water-quest claim (`_claim_water_funnel_for_today`); `workout_completion.complete_workout` (atomic artifacts, `uq_pump_check_day` race-loser → `ALREADY_COMPLETED`, fixed lock order, revision check under `FOR UPDATE`); `plan_owner_lock`/`plan_replacement`/`commit_plan` (consistent lock order, observed-rows-only delete); `plan_mutation` (apply/undo/`_arbitrated_result` with `finally`-rollback); `workout_session` checkpoint (single conditional UPDATE on base revision, idempotency replay); premium quota reserve/refund; referral + friend-accept once-only claims; `meal_idempotency`; diary meal log (no double-count with `CustomMeal`); streak hook; notifications dedup; wearables sync; weekly rollover idempotency; timezone discipline (all day keys via `app/timeutil`, Istanbul; null-vs-zero and failure-vs-empty discipline correct).

**Frontend / API:** CSP/nonce clean (only `type="application/json"` data blocks are non-nonce'd `<script>`s; GA inline nonce'd; external scripts allowlisted + SRI'd; all 24 full-page templates include `_head.html`); feed keyset pagination `(created_at, source_rank, id)` tie-break correct; `nutrition.js` concurrency (read-generation guards, single-flight, epoch-drop of superseded answers, water state machine, XSS escaping) robust; `coach_widget.js` markdown via DOMPurify+marked with escaped fallback; all mobile `/api/v1` and web `tracking.py`/`social.py`/`coach.py` routes carry auth decorators.

---

## Notes

- This was **read-only triage** — no files were modified.
- Line numbers reflect the state of the repo on 2026-10-01; re-confirm before editing.
- None of the findings are release-blocking. #1 is the only item with meaningful live user impact.
