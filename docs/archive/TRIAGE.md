# FitX — Codebase Triage Report

> Generated from a three-agent deep-dive (security, logic/correctness, structure/data-integrity).
> Findings are ordered from **confirmed real bugs** → **security** → **structure / data integrity**.
> Items marked ✅ **verified** were confirmed by reading the source directly; the rest are agent triage
> findings worth investigation. No code was changed in producing this report.

Date: 2026-06-16
Branch: `claude/bold-lovelace-9fku7s`

---

## 1. Confirmed real bugs

### 1.1 Day-rollover bug — violates the project's Istanbul-timezone rule ✅ verified
- **File:** `app/blueprints/tracking.py:227`
- **Severity:** High
- **Code:**
  ```python
  today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
  today_checkin = WeeklyCheckIn.query.filter(
      WeeklyCheckIn.user_id == current_user.id,
      WeeklyCheckIn.created_at >= today_start
  ).first()
  ```
- **Problem:** `CLAUDE.md` mandates that all day/time keys come from `app/timeutil.py` (fixed Europe/Istanbul).
  Using `datetime.utcnow()` for the day boundary means that between Istanbul 00:00–03:00, a weight
  check-in is bucketed into the **previous** UTC day. The "update today's check-in" branch is missed and a
  **duplicate `WeeklyCheckIn` row** is inserted instead.
- **Fix:** Use `utc_day_bounds(app_today())` (or compare against an ISO `day_key`) instead of UTC midnight.

### 1.2 UTC-vs-Istanbul drift in elapsed-day math
- **Files:** `app/blueprints/coach.py:56`, `app/blueprints/tracking.py:145`
- **Severity:** Medium
- **Code:** `days_passed = (datetime.utcnow() - previous_date).days`
- **Problem:** Day-count is computed from naive UTC timestamps, not Istanbul day boundaries. A record created
  late in the UTC day (early Istanbul morning) is off by one day in progress calculations.
- **Fix:** Normalize both ends to Istanbul day keys via `app/timeutil.py` before differencing.

### 1.3 Activity calorie calculation silently returns 0 on bad profile input
- **File:** `app/services/calculations.py:37-43`
- **Severity:** Medium
- **Problem:** If `height_cm == 0` or `weight_kg == 0` (incomplete profile), the function returns `0` calories
  with no error, masking invalid input.
- **Fix:** Guard inputs and raise/validate (`if height_cm <= 0 or weight_kg <= 0: raise ValueError(...)`).

### 1.4 FatSecret token refresh — missing error/`access_token` guards
- **File:** `app/services/fatsecret.py:33-56`
- **Severity:** Medium
- **Problem:** `data["access_token"]` raises `KeyError` if the API returns an error payload or HTML; the cache is
  left unset and callers may not handle the exception in all paths.
- **Fix:** After `raise_for_status()`, check for an `error` field and a present `access_token` before caching.

---

## 2. Security

### 2.1 MCP server has no authorization
- **File:** `fitx_mcp/server.py` (all tools), `fitx_mcp/__main__.py`
- **Severity:** Critical (currently mitigated)
- **Problem:** Every tool takes `user_id` as a direct parameter with no auth check — any caller can read/modify
  any user's data. Mitigated today by loopback-only + `FITX_MCP_ALLOW_HTTP` gating (as warned in `CLAUDE.md`),
  but it is a critical gap if the gate is ever removed or the HTTP transport is exposed.
- **Fix:** Add a server-side auth layer for any non-stdio transport; keep HTTP strictly loopback + documented as
  local-only.

### 2.2 No global per-IP login throttle ✅ verified (per-username only)
- **File:** `app/blueprints/auth.py:87-90`
- **Severity:** High
- **Problem:** Throttling is keyed per-username and only deducts on failed (401) attempts. A distributed
  brute-force across many IPs against many usernames is not globally capped.
- **Fix:** Add a global per-IP limit (e.g. `50 per 15 minutes`) counting all auth attempts regardless of outcome.

### 2.3 FatSecret default endpoint is plaintext HTTP
- **File:** `app/config.py:14`
- **Severity:** High
- **Problem:** Defaults to `http://18.153.156.28:3000`; TLS enforcement (`_enforce_fatsecret_tls`) only triggers
  under `FLASK_DEBUG`. If `FATSECRET_BASE_URL` is not set in production, OAuth tokens travel in cleartext.
- **Fix:** Remove the hardcoded HTTP default; enforce HTTPS (or a loopback whitelist) regardless of debug mode.

### 2.4 CSP `img-src` allows `https://*.amazonaws.com`
- **File:** `app/hooks.py:41`
- **Severity:** Medium
- **Problem:** The wildcard is broader than needed and `data:` images are permitted.
- **Fix:** Narrow to the specific S3 bucket host; drop `data:` from `img-src` if not required.

### 2.5 CSRF guard relies on Origin/Referer only
- **File:** `app/hooks.py:57-76`
- **Severity:** Medium
- **Problem:** State-changing requests are validated via Origin/Referer; Referer can be stripped by proxies or
  spoofed in some configurations. No synchronizer-token fallback.
- **Fix:** Prefer Origin, accept Referer only as fallback, and consider a token-based fallback. Log Referer-only
  approvals.

### 2.6 Referral code keyspace
- **File:** `app/blueprints/pages.py:51`
- **Severity:** Low/Medium
- **Problem:** Codes are matched as 12-char uppercase strings with no signature; predictable/sequential codes
  could be guessed.
- **Fix:** Ensure codes are generated with high entropy (16+ cryptographically random chars).

---

## 3. Structure & data integrity

### 3.1 No `ondelete="CASCADE"` on any `user.id` foreign key
- **File:** `app/models.py` (~24 FK declarations)
- **Severity:** Critical (data integrity)
- **Problem:** Deleting a user leaves orphaned rows across 24+ child tables (WeeklyLog, MealLog, Activity,
  Friendship, Message, Supplement, WorkoutLog, etc.), inflating analytics/leaderboard scans.
- **Fix:** Add `ondelete="CASCADE"` to user FKs (and define matching relationship cascades), via an Alembic
  migration.

### 3.2 `UserSession` missing relationship and index
- **File:** `app/models.py:85`
- **Severity:** High
- **Problem:** `user_id` is `nullable=True` with no `db.relationship()` and no index, despite being filtered on
  every coach/tracking request → full table scans + orphan accumulation.
- **Fix:** Add `index=True`, a `user` relationship with cascade, and make it `nullable=False`.

### 3.3 Missing cascade on `CustomMealItem` and on `Friendship`/`Message`
- **File:** `app/models.py` (CustomMealItem ~395, Friendship 214–227, Message 229–240)
- **Severity:** High/Medium
- **Problem:** Child rows and sender/receiver references are not cascade-protected at the FK level.
- **Fix:** Add `ondelete="CASCADE"` to these FKs.

### 3.4 `db_init.py` runs raw legacy ALTERs + PL/pgSQL before Alembic
- **File:** `app/db_init.py:36-106`
- **Severity:** Critical (migration drift risk)
- **Problem:** Boot applies ~22 raw idempotent ALTERs and a trigger before Alembic `upgrade()`, creating a dual
  migration path. Fragile and undocumented in ordering; conflicts could crash boot.
- **Fix:** Migrate legacy ALTERs into proper Alembic migrations and remove raw SQL from boot over time.

### 3.5 Single gunicorn worker is load-bearing
- **Files:** `Dockerfile:38-42`, `app/hooks.py:83-93`, `app/extensions.py:66-79`
- **Severity:** High (reliability ceiling)
- **Problem:** Weekly-rollover global state (`_last_rollover_check`) and the in-memory limiter fallback assume a
  single process. Scaling to >1 worker silently breaks rollover idempotency and rate-limit accounting; a single
  worker is also a single point of failure.
- **Fix:** Move rollover to a scheduled CLI/cron job, move rollover state + rate-limiter to Redis, then scale
  workers.

### 3.6 Silent fallbacks hide outages
- **Files:** `app/blueprints/gamification.py:23-35` (leaderboard Redis→Postgres),
  `app/hooks.py:89-92` (rollover swallows all exceptions, unlogged),
  `app/models.py:64-77` (S3 avatar failure returns `None` silently)
- **Severity:** Medium
- **Problem:** Infrastructure failures degrade behavior with no log/metric, making incidents hard to detect.
- **Fix:** Log a warning on each fallback path and emit a metric/counter.

### 3.7 Weekly rollover idempotency is check-then-act
- **File:** `app/services/gamification.py:107-142`
- **Severity:** Medium
- **Problem:** Concurrent processes can pass the `WeeklyResetLog` existence check and both award winners before
  one hits the unique-constraint and rolls back; the race path is unlogged.
- **Fix:** Rely on the unique constraint (or a Redis lock) and log the race; avoid duplicate award work.

### 3.8 Macro validation is application-side only
- **File:** `app/blueprints/nutrition.py:20-33`
- **Severity:** Medium
- **Problem:** `_sanitize_meal_macros()` caps values before insert, but there is no DB-level guard; a code path
  that skips it could persist absurd values (e.g. 9999 kcal).
- **Fix:** Add a `CHECK` constraint on `MealLog` matching the sanitizer's bounds.

### 3.9 `stamp("head")` ordering on a fresh DB
- **File:** `app/db_init.py:23-34`
- **Severity:** Medium
- **Problem:** On a brand-new DB, `create_all()` builds the model schema then `stamp("head")` records the
  baseline; a migration added after models were frozen makes the stamp outdated.
- **Fix:** Keep models and migrations in lockstep; document the boot ordering.

### 3.10 Pending action cleanup
- **File:** `app/services/ai_coach.py:328,397`
- **Severity:** Low/Medium
- **Problem:** Pending actions are deleted one at a time after confirmation; double-confirm races can target a
  missing row, and there is no cleanup of stale (>1h) pending actions.
- **Fix:** Add a periodic bulk cleanup and make confirmation idempotent.

---

## 4. Lower-severity / hardening notes

| # | File:Line | Note |
|---|-----------|------|
| 4.1 | `app/db_init.py:48` | Boot-time `UPDATE ... SET is_claimed = true` should live in an Alembic migration. |
| 4.2 | `.github/workflows/deploy.yml:29` | `sed`-based nginx CSP cleanup is fragile; prefer templating nginx.conf. |
| 4.3 | `app/blueprints/tracking.py:21-22` | `profile_complete` enforced only on home route, not via a global before-request guard. |
| 4.4 | `app/models.py:167,203` | `MealLog.photo_key` / `PumpCheck.image_key` nullable → possible orphaned/empty S3 refs. |
| 4.5 | `app/blueprints/menu.py:78-93` | 1-week menu scrape cache with no manual invalidation endpoint. |
| 4.6 | `app/extensions.py:59` vs `Dockerfile:40`, `nginx.conf:67-69` | Upstream AI client timeout (60s) shorter than gunicorn/nginx (300s) → wasted work on slow calls. |
| 4.7 | `nginx.conf:25-26` | FatSecret upstream pinned to a hardcoded public IP; prefer a DNS name. |
| 4.8 | `app/config.py:91` | `SESSION_COOKIE_SECURE` only set in prod. |

---

## 5. False positive (excluded) ✅ verified

- **`app/hooks.py:98-99` — `last_login` NULL "deref"** was flagged by the logic agent but is **not a bug**.
  When `last_login` is `None`, `None != today` is `True` and `None == today - 1day` is `False`, so the `else`
  branch correctly sets `streak_count = 1` for new users.

---

## 6. Healthy / no action

- Application factory + blueprint registration is clean; no circular imports.
- IDOR ownership checks present on the endpoints sampled (queries scoped to `current_user.id`).
- CSRF guard + per-request CSP nonce working as documented.
- Middleware order matches the documented sequence.
- 47 test files with an isolated test env (in-memory SQLite, mocked keys, auto-CSRF client).

---

## 7. Suggested fix order

1. `tracking.py:227` day-rollover bug (1.1) — correctness, violates project rule.
2. FatSecret plaintext HTTP default (2.3) — credential exposure.
3. `ondelete="CASCADE"` on user FKs (3.1) — data integrity.
4. Global per-IP login throttle (2.2).
5. `UserSession` index + relationship (3.2).
