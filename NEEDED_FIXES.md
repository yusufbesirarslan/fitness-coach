# FitX — Triage Report & Needed Fixes

_Generated from a 3-agent deep-dive triage (security, backend logic, structure/AI/frontend) of the `fitness-coach` codebase. No code was changed during triage — this document is the action list._

## Status — resolved in this branch

- ✅ **1.1** Dead `water_logged` / `checkin_done` quests now claimed in `set_water`, `checkin`, `update_weight`.
- ✅ **1.2** `new_supplement` activity now committed atomically (no longer dropped).
- ✅ **1.3** Weekly-report nudge fires Monday only (was Sun+Mon).
- ✅ **1.5** Carb penalty-only contract documented (deliberate asymmetry).
- ✅ **2.2** `/set-language` rate-limited (30/hour per IP).
- ✅ **2.3** AI meal names escaped with `esc()` in `nutrition.js`.
- ✅ **2.4** `edit_profile` username TOCTOU → `IntegrityError` returns friendly "taken".
- ✅ **3.1** OCR decompression-bomb guard (`MAX_IMAGE_PIXELS` + header dim check + test).
- ✅ **3.2** Docker base image digest-pinned (`@sha256:`).
- ✅ **3.5** Prod fails fast when `DATABASE_URL` is unset (no silent SQLite fallback).
- ✅ **3.6** `_food_id_cache` reads go through a locked accessor.
- ✅ **3.7** Menu-data fence delimiters stripped from scraped input.
- ✅ **3.8** `hooks.py` docstrings corrected (`csrf.js`, not `actions.js`).
- ✅ **3.9** Orphaned `templates/_chat_widget.html` deleted.
- ✅ **3.11** `docker-compose` redis `restart` aligned to `unless-stopped`.

_Deferred (need a product/ops decision): 1.4 quick-add idempotency, 1.6/1.7 INFO consolidations, 2.1/2.5/2.6 (already bounded / doc-only), 3.3/3.4 deploy & TLS config, 3.10 single-worker note._

## Executive summary

The codebase is **well-engineered and unusually security-conscious**. CSRF (two-layer), CSP (per-request nonce, no `unsafe-inline`), session hardening, SSRF defense on the menu scraper, S3/IDOR ownership scoping, and the MCP authorization gate are all carefully implemented with documented rationale. **No Critical or High security vulnerabilities were found.**

The actionable items are mostly **correctness bugs** (user-visible quest gaps, a lost activity-feed write, a duplicate nudge) and **hardening/operational** improvements (decompression-bomb guard, Docker pinning, deploy config). Priorities below.

### Top priorities
1. **[HIGH · bug]** Dead quests — `water_logged` & `checkin_done` are seeded but never awardable.
2. **[MED · bug]** `new_supplement` activity-feed entry can be silently dropped.
3. **[MED · bug]** Weekly-report nudge fires twice a week (Sun + Mon).
4. **[MED · hardening]** Decompression-bomb guard missing in menu OCR (`Image.open`).
5. **[MED · infra]** Docker base image not digest-pinned; deploy CSP cleanup uses fragile `sed`.

---

## 1. Correctness bugs

### 1.1 HIGH — Dead quests: `water_logged` & `checkin_done` never claimed
- **Where:** seeded at `app/db_init.py:121-130`; claim sites missing in `set_water` (`app/blueprints/training.py:556-586`) and `checkin`/`update_weight` (`app/blueprints/tracking.py:112`, `:208`).
- **Symptom:** Users see "Su Hedefi" and "Haftalık Check-in" quests on the quests page that can **never** be completed, never award their 10/20 XP. (`friend_invited` *is* claimed at `app/services/referral.py:72`, so the gap is specific to these two.)
- **Fix:** Call `complete_quest_for_user(current_user.id, "water_logged")` in `set_water` when the count increases, and `complete_quest_for_user(current_user.id, "checkin_done")` in `checkin`/`update_weight`. Alternatively, mark these quests inactive if intentionally unused.

### 1.2 MED — `new_supplement` activity can be silently dropped
- **Where:** `app/blueprints/supplements.py:77-78`.
- **Symptom:** `log_activity(...)` only does `db.session.add(...)` (no commit). The following `complete_quest_for_user(...)` commits **only on the success path**; when the supplement quest is already claimed that day it returns `None` without committing (`app/services/gamification.py:256-264` → `_claim_quest` returns `None`, no commit). On the 2nd+ supplement of a day, the `new_supplement` Activity stays pending and is flushed/rolled back by an unrelated later request — the feed entry can be lost or bound to the wrong transaction.
- **Fix:** Commit immediately after `log_activity`, or move the activity write into the same committed transaction as the quest update.

### 1.3 MED — Weekly-report nudge fires twice per week
- **Where:** `analytics_engine.py:139-140` — `if today.weekday() in (0, 6)`.
- **Symptom:** weekday `0` = Monday, `6` = Sunday, so the "Bugün haftalık rapor günü" nudge triggers on **both** Sunday and Monday. The weekly-reset boundary is conceptually a single day (Sunday 23:59 Istanbul).
- **Fix:** Pick one weekday (likely Monday, `== 0`), or document that two days is intentional.

### 1.4 LOW/PRODUCT — `quick_add_meal` has no per-slot/day idempotency
- **Where:** `app/blueprints/nutrition/diary.py:54-131` (`quick_add_meal`), `log_meal`.
- **Symptom:** Each call writes a fresh `MealLog` row keyed only on `meal_key`; `MealLog` has only a `(user_id, tarih)` index, **no** unique constraint. Tapping "add breakfast from plan" twice double-counts the day's calories/macros. For manual `log_meal` this may be intended, but `quick_add_meal` (one canonical Kahvaltı/Öğle/Akşam/Ara Öğün slot from the active plan) reads like it should be once-per-slot-per-day.
- **Fix (needs product decision):** If single-slot is intended, enforce a `(user_id, tarih, meal_key)` uniqueness / upsert for plan-sourced quick-adds.

### 1.5 LOW — `score_compatibility` hard-zero rule ignores carb overflow
- **Where:** `nutrition_pipeline.py:709` (instant-zero rule checks only `cal_ratio > 1.0 or fat_ratio > 1.0`).
- **Symptom:** A food that blows the **carb** budget past 100% only gets a progressive penalty, not the documented "exceeds budget → 0" treatment (docstring at `:682` says "calories OR fat"). Rarely bites since calories track carbs, but it's an asymmetry vs. the carb ceiling enforced elsewhere.
- **Fix:** Either add `carb_ratio > 1.0` to the hard-zero rule or update the docstring/contract to clarify carbs are penalty-only.

### 1.6 INFO — Duplicated activity-calorie math (two sources of truth)
- **Where:** PL/pgSQL trigger `app/db_init.py:62-73` duplicates the MET/stride formula in `app/services/calculations.py:39-57`.
- **Symptom:** They currently agree, but a future edit to one will silently drift from the other.
- **Fix:** Consolidate, or add a test that pins the two implementations together.

### 1.7 INFO — PumpCheck "today" uses two notions of day
- **Where:** pre-check via `utc_day_bounds()` on `PumpCheck.created_at` (`app/blueprints/training.py:420-425`) vs. unique guard `uq_pump_check_day` on `(user_id, date_key)` (`:461`).
- **Symptom:** Near Istanbul midnight the UTC-window pre-check can be ineffective, but the unique constraint + `IntegrityError` branch (`:486`) is the real guard, so no double XP. Fragile, not a live bug.
- **Fix (optional):** Make the pre-check use `date_key` for consistency.

**Verified clean during triage:** no leftover `UserDailyNutrition` references in app code (MealLog is cleanly the single ledger); timeutil discipline is clean in live request paths (only `tests/` and a backfill migration use raw date formatting); `db_init.py` seed/upgrade idempotency is sound for the single-worker invariant; zero-macro guards are all in place.

---

## 2. Security findings

> No Critical/High issues. The items below are Medium→Low hardening.

### 2.1 MED (operational) — MCP HTTP transport has no authorization (by design, gated)
- **Where:** `fitx_mcp/server.py:915-928`, `fitx_mcp/__main__.py:7-23`; in-process use guarded by `_assert_principal(user_id)` at `app/services/ai_coach.py:120-129`.
- **Detail:** MCP tools take `user_id` as a plain parameter with **no** authorization. Correctly gated: HTTP transport requires `FITX_MCP_ALLOW_HTTP=1` and binds only to `127.0.0.1:8100`; in the Flask app the tools are imported in-process and every entry enforces `current_user.id == user_id`. **Risk is purely operational** — if this is ever placed behind a public reverse proxy it becomes a full cross-user data breach.
- **Action:** No code defect. Document loudly and ensure it is never proxied publicly. (`tests/test_mcp_gate.py` already defends this.)

### 2.2 MED→LOW — `/set-language` writable while unauthenticated, no rate limit
- **Where:** `app/blueprints/auth.py:33-44`.
- **Detail:** Not `@login_required` and unthrottled. CSRF-protected and the language value is whitelisted via `set_locale`; DB write only happens when authenticated. Residual risk minor.
- **Fix:** Add a light rate limit.

### 2.3 LOW — XSS defense-in-depth gap: AI meal names rendered without `esc()`
- **Where:** `static/nutrition.js:532` — `(ml.yemekler || []).map(y => \`<li>${y}</li>\`)` is the only `yemekler` render site missing the `esc()` helper used at `:178`, `:305`, `:446`.
- **Detail:** Source is the AI-generated `NutritionPlan` (gpt-4o-mini), not directly attacker-controlled, but AI output is influenced by user prompts. Inconsistent with the rest of the file.
- **Fix:** Wrap in `esc(y)`.

### 2.4 LOW — `edit_profile` username uniqueness is TOCTOU
- **Where:** `app/blueprints/profile.py:110-112`, `:128`, `:141`.
- **Detail:** The "username taken" check and commit aren't atomic and the commit isn't wrapped in try/except. Mitigated by the DB unique constraint (`app/models.py:21`), so worst case is a 500, not duplicate accounts.
- **Fix:** Catch `IntegrityError` on commit and return the friendly "taken" message.

### 2.5 LOW — Google Drive download size caps are generous
- **Where:** `app/services/menu_fetch.py:353` (`_DRIVE_MAX_BYTES` = 50 MB), `:455` (10 MB image), `:358-360` (relies on caller-side streamed cap rather than passing `max_bytes` into `_safe_requests_get`).
- **Detail:** Bounded (≤50 MB/request, 20 req/hr) → mild memory/bandwidth DoS amplification, not SSRF.
- **Fix:** Pass `max_bytes` into the helper and lower the cap.

### 2.6 INFO — `|safe` injections are developer-controlled
- `templates/_head.html:15` `window.I18N = {{ i18n_json|safe }}` (built at `app/hooks.py:113`) — `json.dumps(ensure_ascii=False)` doesn't escape `</script>`, but content is the static translation catalog in a nonce'd script. Hardening only (e.g. escape `<`).
- `templates/friends.html:176` `{{ t('friends.invite_desc')|safe }}` — raw render of a static i18n string with intentional markup. Safe unless translation files ever ingest user data.

**Verified safe during triage:** session fixation handling + `session_protection="strong"`; required `SECRET_KEY` in prod; secure/HttpOnly/SameSite cookies; per-IP + per-username brute-force limits with `LOGIN_FAIL_CLOSED` and constant-time dummy-hash compare; two-layer CSRF wired as global `before_request`; full CSP with per-request nonce; IDOR scoping on every user-owned query and ID-loaded record; S3 key ownership enforcement; thorough SSRF defense (scheme/IP/port allowlists, per-hop redirect re-validation, DNS-rebind pinning); SQLAlchemy ORM + parameterized MCP queries (no SQLi); secrets from env/IAM (none hardcoded, FatSecret TLS enforced); server-side-only quota/premium enforcement.

---

## 3. Structure / AI / Frontend / Infra

### 3.1 MED — Decompression-bomb guard missing in menu OCR
- **Where:** `app/services/menu_ocr.py` `_compress_image_for_vision` — `Image.open` called without `PIL.Image.MAX_IMAGE_PIXELS` or a dimension check.
- **Detail:** A small-file/huge-canvas image just over the 1.5 MB compress threshold decodes fully into memory and can OOM the single gunicorn worker.
- **Fix:** Set `Image.MAX_IMAGE_PIXELS`, reject oversized dimensions, and/or catch `DecompressionBombError`. Add a test asserting such an image is rejected.

### 3.2 MED — Docker base image not digest-pinned
- **Where:** `Dockerfile:3` — `FROM python:3.11-slim` floats.
- **Detail:** Undermines the otherwise reproducible build (requirements are fully pinned).
- **Fix:** Pin by `@sha256:`.

### 3.3 MED — Deploy CSP cleanup via fragile `sed`
- **Where:** `.github/workflows/deploy.yml` strips a stray `add_header Content-Security-Policy` line from a hand-placed nginx file.
- **Detail:** A double CSP header would intersect policies and break nonces.
- **Fix:** Render the full nginx site config from the repo, or fail the deploy if a stray header remains.

### 3.4 MED — HSTS/TLS depends on certbot having mutated the config
- **Where:** `nginx.conf` — HSTS set on the `listen 80` block (no-op over HTTP); no committed 443 block / explicit 80→443 redirect.
- **Detail:** Correct only after `certbot --nginx` runs; the committed config alone doesn't enforce TLS.
- **Fix:** Commit an explicit 443 server block + 80→443 redirect, or document the certbot dependency.

### 3.5 LOW — Silent SQLite fallback in production
- **Where:** `app/config.py:141` — `DATABASE_URL` defaults to `sqlite:///chatbot.db`.
- **Detail:** A container without `DATABASE_URL` boots against ephemeral SQLite instead of failing fast → data silently lost on redeploy.
- **Fix:** Require `DATABASE_URL` unless `_is_dev`.

### 3.6 LOW — `_food_id_cache` read without lock
- **Where:** `app/blueprints/food.py:28`, `:69` — `_food_id_cache.get(...)` outside `_cache_lock`.
- **Detail:** Races with the eviction `del` in `_cache_food_id`. A plain dict `.get` won't corrupt, but it's inconsistent with the documented locking discipline.
- **Fix:** Use a locked accessor.

### 3.7 LOW — AI prompt-injection fence not delimiter-escaped
- **Where:** `app/services/ai_nutrition.py` — scraped content fenced with `<<<MENU_DATA ... MENU_DATA>>>`.
- **Detail:** Scraped content containing the literal `MENU_DATA>>>` terminator could break out of the fence. The `user_id`-injection design prevents cross-user access, but a crafted page could steer extraction.
- **Fix:** Strip the delimiter tokens from `menu_input` before interpolation.

### 3.8 LOW — Stale docstrings in `hooks.py`
- **Where:** `app/hooks.py:90-91`, `:126-127` say the `X-CSRFToken` header is added by `static/actions.js`; it is actually added by `static/csrf.js` (`actions.js` makes no network calls).
- **Fix:** Correct the comments.

### 3.9 LOW — Orphaned `templates/_chat_widget.html`
- **Where:** `templates/_chat_widget.html` (469 lines) is never included (live widget is `static/coach_widget.js`). Both define `window.CW` and `#cw-root` — an accidental future include would collide.
- **Fix:** Delete or mark deprecated.

### 3.10 INFO — Single-worker coupling
- In-memory `foodcache` and the in-process rate-limit fallback are correct only at one gunicorn worker. The streak/rollover hook is now idempotent (Redis `NX` lock + `WeeklyResetLog` UNIQUE) and safe at >1 worker, but the caches/limiter fallback are not.
- **Action:** Add a guard or doc note before anyone raises `--workers`.

### 3.11 LOW — Misc infra
- `docker-compose.yml` `restart:` inconsistency (`web: unless-stopped` vs `redis: always`).
- `psycopg2-binary` is discouraged for prod by maintainers (acceptable on slim).

**Verified strong during triage:** clean application-factory pattern, lazy LLM clients, layered AI error/fallback handling (empty choices, `finish_reason=length`, rate-limit/timeout, Bedrock→OpenAI fallback, JSON brace-extraction + truncation salvage), tool dispatch injects `user_id` server-side, FatSecret token fetch locked against thundering-herd with bounded caches, thorough SSRF defense, full CSP-nonce + CSRF-fetch coverage on the frontend (zero `XMLHttpRequest`/`sendBeacon`), loopback-only port binding, non-root container, OIDC-based deploy, pinned requirements, hermetic test harness.

---

## Suggested fix order

| # | Item | Type | Effort |
|---|------|------|--------|
| 1 | 1.1 Dead water/check-in quests | bug (user-visible) | S |
| 2 | 1.2 Lost `new_supplement` activity | bug | S |
| 3 | 1.3 Duplicate weekly-report nudge | bug | XS |
| 4 | 3.1 OCR decompression-bomb guard | hardening | S |
| 5 | 3.2 / 3.3 / 3.4 Docker pin, deploy CSP, TLS config | infra | M |
| 6 | 2.3 / 2.4 / 2.2 esc(), profile TOCTOU, set-language limit | security (low) | S |
| 7 | 3.5–3.11 + 1.4–1.7 cleanups & product decisions | quality | M |
