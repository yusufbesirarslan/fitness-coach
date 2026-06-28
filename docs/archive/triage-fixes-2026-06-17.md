# FitX — Triage Fixes (2026-06-17)

> Generated from a three-agent deep-dive (security, logic/correctness, structure/architecture).
> This document is **fix-oriented**: each entry describes the problem, where it lives, when it
> triggers, and a concrete remediation. No code was changed in producing this report.
>
> Branch: `claude/bold-lovelace-moy2wz`

**Overall posture is strong** — parameterized SQL throughout, mature SSRF defenses (DNS-pinning,
port allow-listing), per-request CSP nonces, consistent `current_user.id` scoping with ownership
checks, no hardcoded secrets, and 592 tests including cross-user authorization. The items below are
the residual gaps, ordered by priority.

---

## Priority order (do these first)

1. **C1** — non-atomic double/orphaned XP in `complete_workout`
2. **C2** — MCP daily-total uses UTC date instead of the Istanbul `tarih` key
3. **H1** — session fixation on login
4. **DB1** — missing index on `MealLog` (the canonical, hottest ledger)

---

## 🔴 Critical / High

### C1 — `complete_workout` is non-atomic → double / orphaned XP
- **Severity:** Critical
- **Files:** `app/blueprints/training.py:370-394`, `app/services/gamification.py:162-192`
- **Problem:** The route adds `PumpCheck` and `WorkoutLog` to the session, then calls
  `complete_quest_for_user()`, which runs its **own** `db.session.commit()` (gamification.py:180) —
  flushing the PumpCheck/WorkoutLog too. The route then calls `award_xp(base_xp + photo_bonus)` and
  commits **again** (training.py:391-394). Two failure modes:
  - If `complete_quest_for_user` hits its generic `except Exception: db.session.rollback()`
    (gamification.py:190-192), it rolls back the already-added PumpCheck/WorkoutLog, but the route
    does not check the result and still awards the 35 base+photo XP and commits → **XP granted for a
    workout whose rows were rolled back.**
  - The quest XP and the base/photo XP land in two separate commits, so a crash between them leaves
    XP partially applied.
- **Fix:** Wrap the whole flow in a single transaction. Have `complete_quest_for_user` *flush*
  (not commit) or accept a "no-commit" mode, let the route own the single commit, and check the
  quest result before awarding further XP.

### C2 — MCP `log_nutrition_entry` daily total uses UTC `created_at::date`, not the Istanbul `tarih`
- **Severity:** Critical (correctness; user-visible wrong numbers)
- **File:** `fitx_mcp/server.py:601-617`
- **Problem:** The INSERT correctly stamps `tarih = _day_key()` (Istanbul). But the immediately
  following "today total" rollup filters `WHERE user_id = %s AND created_at::date = CURRENT_DATE`.
  `created_at` is naive UTC and `CURRENT_DATE` is the DB-server UTC date. Between Istanbul
  **00:00–03:00**, the just-inserted row (and others logged earlier that Istanbul day) fall on a
  different UTC date, so the daily totals returned to the user are wrong at day boundaries — exactly
  the timezone bug class CLAUDE.md warns about.
- **Fix:** Filter the rollup on `tarih = %s` using `_day_key()` to match the canonical day key,
  not `created_at::date`.

### H1 — Session fixation: no session rotation on login
- **Severity:** High (security)
- **File:** `app/blueprints/auth.py:110`
- **Problem:** `login()` calls `login_user(user)` without rotating the session identifier.
  Flask-Login does not regenerate the session on login by default, and there is no
  `session.clear()` / new-SID step. An attacker who can plant a session cookie in a victim's browser
  before login (shared/subdomain context) could have that session become authenticated post-login.
- **Fix:** Set `login_manager.session_protection = "strong"` and regenerate the session on
  successful auth (e.g. `session.clear()` then reset a fresh value before/after `login_user`).

### H2 — CSRF relies solely on Origin/Referer (no synchronizer token)
- **Severity:** High (defense-in-depth)
- **File:** `app/hooks.py:61-95`
- **Problem:** `_csrf_protect` is the *sole* CSRF defense — it rejects only when an Origin/Referer is
  present and mismatches, or when both are absent. The design is sound and `SameSite=Lax` cookies
  mitigate most cross-site POST CSRF, but `SameSite=Lax` does **not** protect GET-triggered state
  changes. Logout (`auth.py:118`) is a GET that mutates state (custom-guarded today), so any future
  state-changing GET route would be unprotected. It also depends on `request.host` derived via
  `ProxyFix`.
- **Fix:** Keep the Origin check, add a defense-in-depth synchronizer CSRF token (Flask-WTF /
  itsdangerous) for POST/PUT/PATCH/DELETE, and ensure no state-changing GET routes exist beyond the
  explicitly-guarded logout.

### DB1 — `MealLog` (canonical, hottest ledger) has no index on `user_id` / `(user_id, tarih)`
- **Severity:** High (performance)
- **File:** `app/models.py:173-203`; queried at `app/blueprints/nutrition.py:606,663`
- **Problem:** MealLog is the single canonical nutrition table and is queried on essentially every
  nutrition page load via `filter_by(user_id=..., tarih=today)`. Yet `user_id` has no `index=True`
  and there is no composite `(user_id, tarih)` index. As the highest-write table grows, these become
  full-table scans. Lower-traffic tables (Activity, WaterLog, WorkoutLog) *do* have indexes — an
  inconsistency, not a deliberate choice.
- **Fix:** Add `index=True` to `MealLog.user_id` and a composite
  `db.Index("ix_meal_log_user_date", "user_id", "tarih")` via a migration.

---

## 🟡 Medium

### M1 — `award_xp` writes Redis leaderboard before the SQL transaction commits
- **Severity:** Medium (bug)
- **File:** `app/services/gamification.py:47-54`
- **Problem:** `award_xp` mutates `user.rank_points`/`weekly_xp` and immediately calls
  `lb_sync_user(user)` (pushes to Redis), but persistence happens only when the route later commits.
  If the route raises and rolls back after `award_xp`, Redis holds XP that Postgres never recorded —
  leaderboard drifts upward until the next `lb_rebuild`.
- **Fix:** Sync Redis *after* commit (post-commit hook), or rely solely on `lb_rebuild`.

### M2 — Unbounded workout XP when the quest row is absent
- **Severity:** Medium (bug)
- **File:** `app/blueprints/training.py:334-396`
- **Problem:** If `quest` is None (quest deactivated/missing), the daily-completion guard is skipped
  and the user can complete a workout repeatedly each day — each call re-adds a WorkoutLog + PumpCheck
  and re-awards `base_xp + photo_bonus` (35 XP) unbounded. Only `@limiter.limit(AI_RATELIMIT)` caps it.
- **Fix:** Gate daily completion on something independent of the quest row (e.g. WorkoutLog/PumpCheck
  count for today).

### M3 — `is_premium` / billing state is not gated server-side
- **Severity:** Medium (authorization / business logic)
- **Files:** `app/blueprints/pages.py:59-65`, `app/blueprints/profile.py`
- **Problem:** `is_premium` is read but never enforced on the AI/Bedrock generation routes. The
  "1 AI plan per week" free limit is UI-advisory only; only per-hour rate limits exist server-side.
  Any user gets unlimited premium-tier AI generation up to the rate limit.
- **Fix:** If premium is meant to gate features, enforce `current_user.is_premium` on the relevant
  routes server-side, not just in UI copy.

### M4 — Rate-limiter fails open when Redis is down
- **Severity:** Medium (security)
- **File:** `app/extensions.py:20-25,66-79`
- **Problem:** `in_memory_fallback_enabled=True`. When Redis is unreachable, the per-account login
  throttle (the distributed brute-force defense, `auth.py:88`) and AI cost limits degrade to
  per-process/per-restart memory. It logs a warning but keeps serving — effectively disabling the
  distributed brute-force protection during a Redis outage.
- **Fix:** Fail closed (or tighten limits) on the login route specifically when Redis is down.

### M5 — `_check_protein_goal` buckets by `created_at` (UTC), not `tarih`
- **Severity:** Medium (bug)
- **File:** `analytics_engine.py:95-102`
- **Problem:** The weekly protein total filters on `created_at >= week_start_utc` rather than on
  `MealLog.tarih`. Rows near day/week boundaries (or AI-coach rows whose UTC instant differs from
  `tarih`) land in the wrong week, slightly mis-triggering the "%90 protein" nudge.
- **Fix:** Filter on `MealLog.tarih >= week_start.isoformat()`.

### M6 — Quest completion TOCTOU masked by a blanket `except`
- **Severity:** Medium (bug / robustness)
- **File:** `app/services/gamification.py:162-192`
- **Problem:** `complete_quest_for_user` does check-then-insert; concurrent double-submit is *contained*
  by the `uq_user_quest_day` unique constraint (good), but the blanket
  `except Exception: db.session.rollback(); return None` also swallows unrelated failures and can't
  distinguish "already done" from "real error" (and rolls back the `award_xp`/`log_activity` from that
  call).
- **Fix:** Narrow the handler to `IntegrityError` for the duplicate case; let other exceptions surface.

### M7 — More frequently-queried `user_id` FKs lack indexes
- **Severity:** Medium (performance)
- **File:** `app/models.py`
- **Problem:** Missing `index=True` on `user_id` for: WeeklyLog (125), WeeklyCheckIn (136),
  NutritionPlan (153), TrainingPlan (164), PumpCheck (234), Supplement (286), UserQuestProgress (314).
  Migration `a1b2c3d4e5f6` indexed only `user_session.user_id`.
- **Fix:** Add indexes (composite where a date/quest key is also filtered, e.g. UserQuestProgress on
  `(user_id, date_key)`).

### M8 — No error tracking / structured logging
- **Severity:** Medium (observability)
- **Files:** `app/config.py`, app-wide
- **Problem:** No Sentry/Datadog/OpenTelemetry/Prometheus. Logging is plain `app.logger` text with no
  JSON/structured output and no exception aggregation; unhandled 500s vanish into gunicorn stdout.
- **Fix:** Add `sentry-sdk[flask]` in `configure_app`; at minimum, structured (JSON) logging.

### M9 — `db_init` legacy ALTERs swallow every exception silently
- **Severity:** Medium (data-integrity risk)
- **File:** `app/db_init.py:67-72`
- **Problem:** Each legacy `ALTER`/`UPDATE` is wrapped in `try/except Exception: db.session.rollback()`
  with no logging. Intentional for idempotency, but it also hides a genuinely failed migration; a
  subtly-wrong schema boots "successfully." (The Alembic and stamp blocks do log — only this loop is
  silent.)
- **Fix:** Log at debug level at minimum; once all prod DBs are on the Alembic baseline, retire the
  inline ALTER block.

### M10 — Loose dependency pins on untrusted-input parsers
- **Severity:** Medium (supply chain)
- **File:** `requirements.txt`
- **Problem:** `openai`, `anthropic[bedrock]`, `requests`, `Pillow`, `boto3`, `beautifulsoup4`,
  `pdfplumber`, `mcp[cli]` use `>=` floors with no upper bound. `Pillow` and `pdfplumber` process
  untrusted uploads (Pump Check photos, menu PDFs) — exactly the libs to pin and CVE-monitor.
- **Fix:** Pin these (or adopt a lockfile / `pip-compile`); add a Dependabot / `pip-audit` step.

---

## 🟢 Low / hygiene

- **AI prompt-injection surface (Low, contained):** Scraped restaurant HTML and user meal text are fed
  to the tool-calling coach (`app/services/ai_coach.py:608-644`, `app/blueprints/menu.py`). Contained
  because `user_id` is injected server-side and re-checked via `_assert_principal`, plus a staging→confirm
  step. Harden by delimiting/instruction-neutralizing scraped content in the prompt.
- **Username enumeration (Low, by-design):** `/friends/search` (`app/blueprints/social.py:65-86`) has no
  per-route rate limit. Add `@limiter.limit(...)` keyed per-user.
- **Friend-request spam (Low):** `friend_request` (`social.py:89-120`) has no cooldown after a
  rejected→pending flip. Add a rate limit / cooldown.
- **`calculate_bmr`/`calculate_tdee` None-guards (Low):** `.lower()` on `gender`/`activity`/`goal`/`level`
  raises `AttributeError` on a mid-setup NULL profile (`app/services/calculations.py:6,18,23,59,63`).
  Default None to a safe value before `.lower()`.
- **DailyActivity calorie trigger (Low):** Calorie math lives in a Postgres PL/pgSQL trigger
  (`app/db_init.py:76-106`) — Postgres-only (no-op on SQLite/tests), untestable, and diverges from the
  Python `calculate_activity_calories` guards. Move the calc to Python (`services/`).
- **Stale `_purge_user` comment (Low):** Comment claims "no ON DELETE CASCADE" but migration
  `a1b2c3d4e5f6` added CASCADE/SET NULL (`app/cli.py:46`). Update the comment.
- **Legacy `UserDailyNutrition` model lingers (Low):** No write path remains; kept only for purge/drop
  (`app/models.py:375`, `app/cli.py:52,67`). Schedule a drop migration, then remove the model.
- **`nutrition.py` god-module (Low):** ~897 lines / ~13 routes spanning plan CRUD, food diary, and the
  MealLog ledger, with embedded macro math (`_sanitize_meal_macros`). Extract a `services/nutrition.py`.
- **Single-worker gunicorn coupling (Low):** `--workers 1` is required because the weekly-rollover/streak
  throttle is in-process (`app/hooks.py`). Move the throttle to a Redis `SET NX EX` lock to allow scaling
  (DB idempotency via `WeeklyResetLog` already exists).
- **Docs drift (Low):** CLAUDE.md header says "OpenAI (gpt-4o-mini)" but the app also uses Anthropic
  Claude Sonnet via Bedrock for heavy tasks (`app/config.py:44-49`, `app/extensions.py:45-63`). Update the
  stack description.
- **`weekly_reset` UTC week boundary (Low, intentional):** `_last_completed_week_key` uses UTC while the
  rest of the app uses Istanbul (`app/services/gamification.py:96-104`). Consistent with the documented
  "Sunday 23:59 UTC" cron and idempotent via `WeeklyResetLog`; add a comment confirming intent.

---

## ✅ Verified clean (no action)

- **SQL injection** — all queries parameterized (MCP `%s` bound params, ORM elsewhere; `db_init`
  ALTERs are static literals).
- **IDOR** — every record-by-ID load checks ownership (`supplements.py:87,132`, `social.py:127,144,258`,
  `nutrition.py:170,252,314,327`); coach/MCP tools inject `user_id` server-side and re-assert via
  `_assert_principal`.
- **MCP HTTP gating** — behind `FITX_MCP_ALLOW_HTTP=1` + bound to `127.0.0.1` only; default transport stdio.
- **Secrets** — no hardcoded keys; OpenAI/AWS/Bedrock from env or IAM instance profile; `.env`/`*.pem`/`*.csv`
  gitignored; FatSecret TLS enforced.
- **S3 pre-signed URLs** — ownership-scoped via `expected_user_id` path check; private bucket; AES256.
- **File upload / image** — Pillow content verification + decompression-bomb cap + format allowlist;
  base64 data-URL only.
- **XSS** — usernames restricted to `[A-Za-z0-9_.-]`; CSP removes `unsafe-inline` from script-src with a
  per-request nonce; `script-src-attr 'none'`.
- **Open redirect** — redirects use `url_for(...)` to internal endpoints only; no user-controlled `next`.
- **SSRF** — DNS-pinning + per-hop revalidation + port allow-listing in `menu_fetch.py`
  (`_resolve_host_safely` rejects the whole hostname if any resolved address is non-public).
- **Day-key writes** — nutrition/social/menu/training/tracking use `day_key()`/`app_today().isoformat()`;
  `strftime("%d.%m")` usages are display labels, not day keys.
- **Nutrition ledger** — no remaining writes to `UserDailyNutrition`; MealLog is single-source as documented.

---

*This file documents triage findings only — no source code was modified. Tackle C1, C2, H1, and DB1 first.*
