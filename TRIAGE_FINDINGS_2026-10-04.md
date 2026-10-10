# Fitness Coach (FitX) — Triage & Security Review

**Date:** 2026-10-04
**Branch reviewed:** `claude/amazing-dijkstra-sduigw` (base at `2b50280`)
**Method:** Three parallel read-only deep-dive agents — (1) security vulnerabilities, (2) backend correctness / data integrity, (3) structure / frontend / API contract. Findings were cross-checked against `CLAUDE.md`, `SECURITY.md`, and the prior triage records (`NEEDED_FIXES*.md`, `TRIAGE_FINDINGS_2026-10-01.md`) to exclude already-known or accepted-tradeoff items. The highest-value items below were then manually verified against the source.

---

## Executive summary

**The codebase remains in very good shape.** All three reviewers independently reached the same conclusion as the 2026-10-01 triage: this is an unusually well-hardened application, and the newest surfaces (post-#374 — NUTR-PR7 native nutrition closure, the native profile read, the shared nutrition authorities) hold to the same standard.

- **No new Critical, High, or Medium _security_ vulnerabilities were found.** Auth/authorization scoping, HMAC token construction + replay/idempotency, SQL parameterization, SSRF defenses, secret handling, CSRF, CSP, and cookie security were all independently re-verified clean.
- **No new Critical/High/Medium _correctness_ bugs were found.** The concurrency, locking, idempotency, transaction, and None-vs-0 discipline in the reviewed areas is consistently correct.
- The Oct-01 ranked fixes were confirmed **actually landed in-tree** (food-search 503, `UserSession`/`get_active_plan` `created_at DESC, id DESC` tiebreaks, `fitx_ref` Secure flag, `auth.js` storage guard, ProxyFix host/port spoofing).
- **The one item of real substance is a CI coverage gap** (two PostgreSQL race-test files that never execute in CI). The remainder are Low-severity consistency / defense-in-depth / log-hygiene items, two of which are latent (cannot fire under today's data shape).

### Recommended fix order

1. **#1 (CI gap)** — highest value; a regression in a critical locking invariant would currently pass CI green. One-line-ish CI change.
2. **#2 (NutritionPlan selector tiebreak)** — extend the already-established triage source-scan to `NutritionPlan`; closes a known divergence _class_ before it can bite.
3. **#3 (WeeklyCheckIn tiebreak)**, then **#4 (menu DEBUG logging)**, then **#5 (coach-context date skew)**.

---

## Needed fixes (ranked)

### 1. [HIGH] Two PostgreSQL race-test files never run in CI
- **Files:** `tests/test_workout_completion_pg.py`, `tests/test_workout_session_pg.py`; CI job at `.github/workflows/ci.yml` (`mobile-pg-concurrency`, the "Run deterministic races" step).
- **Problem:** Both files are marked `pytestmark = pytest.mark.pg_concurrency` and skip unless `FITX_PG_CONCURRENCY_TEST=1` **and** `PG_TEST_DATABASE_URL` are set. Only the `mobile-pg-concurrency` job sets those vars — and that job invokes `pytest -m pg_concurrency -q` with an **explicit positional file list of 22 files that does _not_ include these two.** Positional paths restrict pytest collection, so these two files are never collected by the concurrency job; in the main CI job they are collected but skip (env vars absent). **Net result: neither file executes anywhere in CI.** *(Verified: both carry the marker; neither appears in the CI file list.)*
- **Why it matters:** They contain the authoritative deterministic race assertions for critical invariants that SQLite physically cannot prove:
  - `test_concurrent_completion_has_single_winner_on_postgres` — the `uq_pump_check_day` one-completion-per-day invariant.
  - `test_concurrent_start_yields_single_active_on_postgres` — the `uq_workout_session_active_owner` single-active invariant.
  - `test_complete_versus_abandon_only_one_terminal_wins_on_postgres`.
  - `test_concurrent_session_completion_no_duplicate_artifacts_on_postgres`.
  This is exactly the hazard `CLAUDE.md` repeatedly warns about: *"a new PG module not added there never runs in CI."* A regression in the single-active-session or single-completion-per-day locking would ship green.
- **Fix:** Add both files to the explicit list in `ci.yml`. If their coverage is believed redundant with `test_mobile_workout_sessions_pg.py` / `test_sprint14_workout_execution_reliability_pg.py` / `test_progress_pump_check_completion_pg.py`, confirm that explicitly and **delete** the two files — leaving live-but-dead PG test files in-tree is misleading either way.

### 2. [LOW] `NutritionPlan` "latest row" selectors omit the `id DESC` tiebreak
- **Files:** `app/services/nutrition_day_view.py:249` (`_read_plan`), `app/services/plan_facts.py:234`, `app/blueprints/nutrition/diary.py:201`.
- **Problem:** These three surfaces order the newest `NutritionPlan` by `created_at.desc()` only, whereas the canonical store `app/services/nutrition_plan_store.py:42` (`newest_plan_query`) and the native plan read order `created_at.desc(), id.desc()`. This is the exact divergence class the 2026-10-01 triage (#3/#4) fixed for `UserSession`/`TrainingPlan` — but the source-scan was never extended to `NutritionPlan`. *(Verified: the `UserSession` selectors in the very same files — `nutrition_day_view.py:193`, `plan_facts.py:205` — already carry the `id.desc()` tiebreak; only the `NutritionPlan` ones were missed.)*
- **Why it's latent today:** `nutrition_plan_store.replace_nutrition_plan` (both web `POST /nutrition-plan/save` and native `PUT`) does delete-all-then-insert-one under the owner lock, so a user holds at most one `NutritionPlan` row and the tiebreak never actually fires. The risk is forward-looking: a future "plan history" feature (keeping >1 row) or legacy multi-row data would expose non-determinism — the day view (plan name + meal count) could show a different plan than the Plan tab.
- **Fix:** Add `, NutritionPlan.id.desc()` to the three `order_by` clauses (or route them through `nutrition_plan_store.newest_plan_query`), and extend the triage source-scan drift guard (`tests/test_triage_2026_10_01_fixes.py`) to cover `NutritionPlan`.

### 3. [LOW] `WeeklyCheckIn` "latest" selectors omit the `id DESC` tiebreak
- **Files:** `app/blueprints/tracking.py:573`, `app/services/analytics_engine.py:177`.
- **Problem:** `.order_by(WeeklyCheckIn.created_at.desc()).first()` with no `id` tiebreak. Same-day check-ins are explicitly legitimate (Progress V2 PR4 — `POST /checkin` has no per-day limit), so two rows can be close in time; when `created_at` collides, the chosen "current" weight/intensity is non-deterministic. *(Verified.)*
- **Trigger:** Two check-ins written with the same `created_at` (microsecond collision — unlikely but possible, and more likely in tests/seeded data).
- **Fix:** Add `, WeeklyCheckIn.id.desc()` to both selectors.

### 4. [LOW] `[DEBUG]` / `[ALGORITHM DEBUG]` lines logged at INFO on the production menu-analysis hot path
- **File:** `app/blueprints/menu.py:614-616` (plus pervasive `[MACRO ENGINE]` / `[SCRAPER]` INFO logging through `analyze_menu` / `proxy_scan_menu`).
- **Problem:** `analyze_menu` (a live endpoint) emits three `logger.info(f"[DEBUG] …")` / `[ALGORITHM DEBUG]` lines on **every** menu analysis, dumping category lists and the top-3 scored dish names. *(Verified: lines 614-616.)* These are leftover debug lines mislabeled `[DEBUG]` but shipped at INFO. Not PII, but it is high-volume log noise on a per-request path and inconsistent with the "exception TYPE only / PII-free one-liner" logging discipline the rest of the codebase follows (the triage-2026-09-30 #7 hygiene rule).
- **Fix:** Delete them or demote to `logger.debug(...)`.

### 5. [LOW / Informational] AI-coach context uses naive-UTC `created_at.date()` for day labels (Istanbul-boundary skew)
- **File:** `app/services/coach_context_queries.py:128, 180, 231, 316`.
- **Problem:** Weight-log dates, plan-created date, supplement-added date, and the meal-date fallback are rendered with `created_at.date()`. `created_at` is naive UTC, so for an Istanbul (UTC+3) user an event between 00:00–03:00 local shows the *previous* calendar day in the context the model sees — contrary to the repo rule that all day keys come from `app/timeutil`. *(Verified.)* The meal case (line 316) only hits the fallback when `MealLog.tarih` is empty (rare); the weight/plan/supplement cases always use UTC.
- **Impact:** Pre-existing, **display-only inside the coach prompt** — it can make the coach say "yesterday" for a today event near midnight. Not a data-integrity bug; no stored value is wrong.
- **Fix:** Convert with `to_app_tz(...).date()` (or `app_date_of(...)`) before `.date()` at each site.

---

## Structural debt (carried forward — not new)

- **`app/services/ai_coach.py`** continues to grow (~1450 lines; was ~1199 when first flagged in `NEEDED_FIXES.md` #6). **`app/blueprints/social.py`** (~1254 lines, multi-domain) is the sibling still-open item. Both are already recorded as open structural debt; noted here only to confirm the trend is still worsening. Fix path: continue the extraction pattern already used for the AI pipeline (`ai_pipeline` / `context_builder` / `prompt_builder` split).

---

## Areas verified clean (with evidence)

**Security**
- **HMAC tokens / replay / idempotency (NUTR-PR7):** per-class domain labels, subkey-derived HMAC-SHA256, constant-time compare, owner bound through the MAC (never stored in payload), length-prefixed canonical serialization. Supplement/`_resolve` scans only the owner's own ids → forged/cross-user tokens resolve to one private `SupplementNotFound` (no IDOR). Proposal tokens owner-bound + 24h TTL; planned-meal log keyed on durable `(user_id, Idempotency-Key)` + semantic fingerprint. `meal_idempotency` keys regex-validated and every uniqueness constraint is composite `(user_id, idempotency_key)`.
- **Auth / authorization:** every no-auth route is legitimately pre-auth; no protected/state-changing route missing `@require_auth`/`@require_mobile_auth`. New `mobile_account_profile` is owner-bound, closed key set, typed `PROFILE_*` 503 on storage fault (never the auth catch-all).
- **IDOR:** pump checks / comparisons / physique all `user_id`-scoped; token lookups by `(public_id, user_id)`. Presigned URLs pass `expected_user_id`; the one call without it (`User.profile_picture_url`) is self-referential on the user's own server-written key.
- **Injection:** no `render_template_string`/`eval`/`exec`/`pickle.loads`/`subprocess`/`os.system`/`verify=False`/`debug=True` in `app/`, `fitx_mcp/`, `s3_helper.py`. All raw SQL uses `%s` parameter binding.
- **SSRF:** `menu_fetch` uses positive `is_global` allow, IPv4-mapped-IPv6 unwrap, port allowlist {80,443}, per-hop re-validation with manual redirect following, DNS-rebinding TOCTOU closed via pinned `getaddrinfo`, hard body-size caps. Wearable callbacks validate OAuth `state`, gate provider I/O in `blocking_concurrency_slot`, rate-limit per user/IP.
- **Secrets / CSP / redirects:** Cognito tokens Fernet-encrypted; logs carry event names + exception TYPEs + `request_id` only; `menu_fetch.loggable_url` strips query/userinfo. CSP nonce-based, external scripts SRI-pinned to exact jsdelivr versions. No open redirect in `auth.py`.

**Correctness**
- **Plan save / replacement, planned-meal logging, hydration set, supplement cabinet:** validate-before-delete, owner-row → plan-row lock order (a prefix of the documented repository order), atomic delete+insert+commit, race-safe idempotency arbitration by the composite unique constraint. Hydration count commit kept separate from the `water_logged` claim+award (which remain one transaction) — the triage-2026-09-30 #5 fix preserved on both transports.
- **Day-view savepoints:** each section in its own `begin_nested()`; failure → UNAVAILABLE, never `empty`; unknown is `null`, never 0.
- **Workout completion & session execution:** fixed lock order (session row before artifacts); `expected_checkpoint_revision` verified under the lock before the already-completed preflight; checkpoint is one conditional `UPDATE` on base revision; `uq_pump_check_day` race-loser reconciles with no duplicate artifacts; browser `/workout/complete` refuses a `session_id` without a declared revision (Sprint 14 PR2 gap closed).
- **Gamification/challenges commit discipline:** `award_xp`/`_claim_quest`/`log_activity`/`record_event`/`notify`/`award_badge` do **not** self-commit (callers own the commit); `award_xp` uses FOR-UPDATE + column read against lost updates. Premium quota reserve/refund locked-row read; refund on any provider/parse/empty-document failure.
- **Deleted-identity resurrection:** flush-then-check ordering relies on `uq_user_cognito_sub`; tombstone written in the purge transaction.
- **Error-swallowing / unbounded-query scan:** no bare `except:` in `app/services` or `app/blueprints`; no unbounded user-facing `.all()` in the new nutrition code (history day-bounded ≤14; cabinet `limit(CABINET_MAX+1)`; today-meals day-scoped).

**Structure / frontend / API**
- **Mobile `/api/v1` contract:** uniform `{code,message,retryable,request_id}` envelope, single global `no-store` after_request, shared 429/413/Exception handlers, feature-gate allow-list — consistent across all 14 mobile blueprint modules. Newest surface (`mobile_nutrition_closure.py`, 12 routes) uses consistent `_run`/`_render`/`_failure` helpers with rollback-on-error.
- **Frontend XSS:** `nutrition.js`, `coach_widget.js`, `progress.js`, `progress_physique.js`, `today.js` consistently use `esc()`/`textContent`/DOMPurify; coach user text never passes through markdown; non-OK responses handled.
- **Templates:** the only nonce-less inline `<script>` blocks are `type="application/json"` data islands (CSP-exempt); the only templates without `_head.html` are static error pages with no fetch/CSRF needs.
- **Config/deploy:** gunicorn `threads` (8) aligns with `FITX_WEB_THREADS` (8); capacity invariant + DB pool sizing boot-enforced; retired progress routes genuinely removed; all presenters/scorers wired to live callers.

---

## Scope notes

- Carried-forward open items already documented in `NEEDED_FIXES_2026-08-14.md` (ai_gate thread-reserve, mobile_auth FOR-UPDATE-across-Cognito, etc.) were **not** re-derived here — out of scope as "new."
- Accepted/documented tradeoffs (`style-src-attr 'unsafe-inline'`, single-proxy IP trust, `/nutrition-plan/active` deliberately excluded from the `no-store` guard for a Playwright `networkidle` test hazard, `/chat` unbounded `UserSession` row growth) were **not** re-flagged.
- All three agents ran read-only; no files were modified. Line numbers reflect repo state at 2026-10-04.
