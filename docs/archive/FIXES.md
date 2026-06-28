# FitX — Triage & Needed Fixes

_Generated 2026-06-25 via a 3-agent deep-dive (security, backend logic, structure/data-layer)._

## Executive summary

The codebase is **mature and well-hardened**. The security audit found **no Critical or High vulnerabilities** — authentication, authorization (per-user scoping + ownership checks), CSRF, CSP, SSRF, and secrets handling all have explicit, correct mitigations. The substantive work is in **correctness** (a timezone bug affecting weekly XP/awards, a nutrition day-model mismatch) and **schema-management hygiene** (the triple create_all / legacy-ALTER / Alembic mechanism is drift-prone, with one concrete column-length divergence).

Priority order: **B1, B2** (correctness of XP & nudges) → **S1, S2** (schema drift) → everything else.

| Area | Critical | High | Medium | Low/Info |
|------|:--:|:--:|:--:|:--:|
| Security | 0 | 0 | 0 | 4 |
| Backend logic | 0 | 1 | 3 | 6 |
| Structure/data | 0 | 2 | 3 | 5 |

---

## High priority

### B1 — [High] Weekly rollover uses naive UTC, violating the Istanbul-timezone rule
**File:** `app/services/gamification.py:136` (`_last_completed_week_key`), called from `app/hooks.py:173` (`maybe_weekly_rollover`)

`_last_completed_week_key` computes the week boundary from `datetime.utcnow()` + `isocalendar()` and never goes through `app/timeutil`. The whole "completed week" determination is in UTC, while `weekly_xp` is accrued on **Istanbul** days and the cron runs `59 23 * * 0` (Sunday 23:59 UTC = Monday 02:59 local). Near the boundary the function can resolve to the wrong ISO week, so top-3 weekly winners and the reset can be computed against a window misaligned with how XP was earned.

**Fix:** Derive the week key from `app_now()`/`app_today()` (Istanbul). Compute the Monday boundary in `APP_TZ`, then `isocalendar()`. Align the cron schedule/comment accordingly.

### S1 — [High] Schema drift: `referral_code` column length inconsistent across mechanisms
**Files:** `app/db_init.py:55`, `app/models.py:57`, `migrations/a1b2c3d4e5f6`

Model and Alembic define `referral_code` as `VARCHAR(16)`; the legacy boot ALTER in `db_init.py:55` creates `VARCHAR(12)`. A DB that hit the `db_init` ALTER path first would get 12, while `create_all`/Alembic give 16 — exactly the create_all-vs-ALTER-vs-Alembic divergence this design is prone to.

**Fix:** Change the `db_init.py` ALTER to `VARCHAR(16)`. Better: delete legacy ALTERs already covered by migrations.

### S2 — [High] Triple schema-management mechanism is drift-prone; CI guard is non-blocking
**Files:** `app/db_init.py`, `.github/workflows/ci.yml`

Schema flows from three sources that must be kept in manual sync: `db.create_all()`, ~25 hand-written idempotent `ALTER`/`UPDATE` statements in `db_init.py`, and the Alembic chain. The `db check` drift guard is `continue-on-error: true` (reports only, never fails the build). Raw ALTERs swallow exceptions at `debug` level, so a genuinely failed ALTER is nearly invisible. S1 is a concrete symptom.

**Fix:** Migrate each legacy ALTER into a proper Alembic migration, delete it from `db_init.py`, then flip `continue-on-error` to `false`. Until then, document that `db_init.py` ALTERs are the source of truth for legacy prod DBs only.

---

## Medium priority

### B2 — [Medium] `_check_protein_goal` weekly window uses `created_at` (UTC) instead of canonical `tarih`
**File:** `analytics_engine.py:98-105`

The weekly protein nudge filters `MealLog.created_at >= week_start_utc`, but `MealLog` has a canonical ISO `tarih` column (Istanbul day, `models.py:198`). Using `created_at` (naive UTC) means meals logged late Sunday / early-UTC leak in or out, and the protein total is computed on a different day model than the rest of the app — the goal comparison (line 107) is subtly off near midnight.

**Fix:** Query on the ISO key: `MealLog.tarih >= week_start.isoformat()` (and `<= today.isoformat()`), matching `ai_coach._range_keys` / `_today_nutrition_totals`.

### B6 — [Medium] Boot-time legacy `UPDATE`s re-run every start and overwrite intentional changes
**File:** `app/db_init.py:48`, `:63-67`

`init_database` runs data `UPDATE`s on every container start: `UPDATE user_quest_progress SET is_claimed=true WHERE is_claimed=false` (full table write of unclaimed rows each boot) and five `UPDATE daily_quest SET title=...` that will overwrite any future intentional title change back to the hardcoded string on the next deploy. These are data migrations, not schema DDL.

**Fix:** Move the data `UPDATE`s into one-shot versioned Alembic migrations and drop them from the boot loop; keep boot DDL idempotent-only.

### S3 — [Medium] Weekly rollover has no reliable scheduler
**Files:** `app/hooks.py:172`, `app/cli.py:27`

Reset runs either via a `before_request` hook (only if a request arrives after the boundary) or a host cron documented only in a `cli.py` docstring — no APScheduler/Celery, and the cron lives outside the repo/compose so it's invisible to deploy and easily lost on instance replacement. It is idempotent (`WeeklyResetLog`), which prevents double-processing but not *missed* processing.

**Fix:** Make the cron infrastructure-as-code (or add a single-worker-guarded in-process scheduler). At minimum surface "last rollover age" in `/health` so a missed week is observable.

### S4 — [Medium] `db.create_all()` + auto-`upgrade()` on every boot is a latent scaling cliff
**Files:** `app/db_init.py:16-35`, `Dockerfile:39`

The design assumes one worker / one instance. Adding a second worker or container would race on migrations and double-run boot side effects (rollover, leaderboard rebuild). The boot also swallows migration failure so the app can start against a stale schema.

**Fix:** Keep single-worker, but move migrations into an explicit deploy step (the SSM deploy already runs host commands — run `flask db upgrade` there instead of on every container start).

### S5 — [Medium] `MealLog` macro bounds duplicated between model `CheckConstraint` and migration
**Files:** `app/models.py:209-220`, `migrations/a1b2c3d4e5f6`

The macro bound expression (`<= 100000` / `<= 50000`) is hand-duplicated between the model constraint and the migration string. They match today, but there's no single source of truth, so a future tweak to one silently diverges (constraint only enforces on Postgres prod).

**Fix:** Extract the bounds into one module-level constant imported by both, or add a test asserting the two strings are equal.

---

## Low priority / hardening

### Security (defense-in-depth)
- **SEC1 — [Low] `_assert_principal` is a no-op outside request context** (`app/services/ai_coach.py:93-97`). When `has_request_context()` is False the principal check is skipped (documented stdio trust model), but any future non-request caller passing an attacker-influenced `user_id` would get cross-user access with no guard. **Fix:** require an explicit `trusted=True` for the no-context path so it fails closed.
- **SEC2 — [Info] `/health` is unauthenticated and leaks limiter storage state** (`app/__init__.py:34-42`). Returns `limiter_storage: redis|memory|degraded`; `degraded` signals weakened brute-force throttling — minor recon. **Fix:** keep the 200 probe but move/strip the `limiter_storage` detail behind an internal endpoint.
- **SEC3 — [Low/accepted] Account-scoped login throttle enables a short self-healing lockout DoS** (`app/blueprints/auth.py:45-58`). Documented, bounded tradeoff; no code fix needed — accept or document.
- **SEC4 — [Low] Menu scraper pins only `safe_ips[0]`** (`app/services/menu_fetch.py:128-131`). All resolved IPs are already validated (private→reject), so this affects reliability for rotating-IP hosts, not safety. Noting for completeness.

### Backend logic
- **B3 — [Low] `log_progress` weight-gain message drops the "kaydedildi" prefix** (`app/blueprints/tracking.py:71`). Gain branch uses `message = ...` (assignment) while loss/equal use `message += ...`, so the "X kg kaydedildi." confirmation disappears specifically on weight gain. **Fix:** change `=` to `+=`.
- **B4 — [Low/Medium] `supplement_add` first-entry XP gate is fragile under autoflush** (`app/blueprints/supplements.py:67-72`). `count()` runs after `db.session.add(supp)`; autoflush makes `first_entry` False even for the genuine first supplement — rescued only by the `or ...count()==1` clause. Works by luck; any refactor dropping that clause silently removes first-supplement XP, plus a redundant extra query. **Fix:** capture the count *before* `add()` (or `no_autoflush`) and award once on `prior_count == 0`.
- **B5 — [Low] `calculate_tdee` silently downgrades unrecognized activity keys to sedentary (1.2)** (`app/services/calculations.py:22-27`). A legacy Turkish value (e.g. stored `"aktif"`) logs a warning and quietly returns the sedentary multiplier, understating calorie targets. **Fix:** map known legacy synonyms or surface the fallback; verify no stored rows use the old keys.
- **B7 — [Low] `respond_suggestion` meal detection uses substring match on a mutated field** (`app/blueprints/social.py:330,342`). Checks `"meal" in msg.message_type` *after* appending `_accepted`; works today but couples logic to substring matching. (`nutrients` is safely initialized — no NameError.) **Fix:** decide meal-vs-workout from the original type before appending the suffix.
- **B8 — [Low] `diary_add_item`/`diary_update_item` accept non-positive `metric_serving_amount`** (`app/blueprints/nutrition/diary.py:159-162,244-247`). Guard is truthiness only, so a negative client value produces negative per-100g macros that get stored and rescaled. Unlike coach/menu paths, the diary path doesn't call `clamp_serving_macros`. **Fix:** guard `metric_amt > 0` and run diary totals through `clamp_serving_macros`.
- **B9 — [Low] `analyze_menu` returns HTTP 200 on extraction failure** (`app/blueprints/menu.py:280-282,291-293`). `OUTPUT_PARSING_FAILED` returns `success:False` with status 200, defeating client retry/error monitoring. **Fix:** return 422 (or 502 for downstream LLM failure).
- **B10 — [Low] Menu scan silently degrades failed items to all-zero macros without an aggregate signal** (`app/blueprints/menu.py:326-362,435-442`). If token/LLM lookups fail, items emit zeros but the overall response is still `success:True` — a systemic outage looks like a healthy zero-result. **Fix:** include a `resolved_ratio`/`degraded` flag when a large fraction zeroed out.

### Structure / data
- **S6 — [Low] Verify suspicious dependency pins** (`requirements.txt`). `requests==2.34.2` does not appear to be a published version (latest is 2.32.x); also double-check `Werkzeug==3.1.8`, `redis==8.0.0`, `pytest==9.1.1`, `gunicorn==26.0.0`. If these resolve only via the agent-proxy mirror, a clean public `pip install` would fail. **Fix:** confirm each pin resolves from PyPI.
- **S7 — [Low] Inconsistent quest seeding** — `meal_logged` title is English `"Log a Meal"` in `app/db_init.py:135` vs Turkish `"Öğün Kaydet"` in `app/cli.py:17`; whichever seeds first wins. **Fix:** use `"Öğün Kaydet"` in `db_init.py:135`.
- **S8 — [Low] `db_init` raw-ALTER failures logged at `debug`; trigger block swallows all exceptions silently** (`app/db_init.py:78`, `82-116`). A genuine failure is invisible at prod `INFO` level. **Fix:** log the trigger block at `warning` and distinguish "already exists" from real errors in the ALTER loop.
- **S9 — [Low] Large service modules trending to god-modules** — `ai_coach.py` (1447 lines), `ai_nutrition.py` (827), `nutrition_pipeline.py` (809, at repo root outside `app/`). No urgent action; split `ai_coach.py` (prompts/tools/loop) when next touched, and consider moving `nutrition_pipeline.py`/`analytics_engine.py` into `app/services/`.
- **S10 — [Low] `referrer` relationship lacks `passive_deletes`** (`app/models.py:59,73`). DB-level `SET NULL` + `_purge_user` already cover deletes; the backref's ORM cascade is undefined on SQLite. **Fix:** add `passive_deletes=True` for parity or document the asymmetry.

---

## Verified healthy (no action needed)
- **IDOR**: ownership checks in `diary.py` are present and correctly ordered before read/mutate.
- **MCP**: parameterized SQL + `readonly=True`; `user_id` injected from `current_user`, never from args; HTTP transport gated behind `FITX_MCP_ALLOW_HTTP=1` on loopback.
- **SSRF**: menu scraper validates public IPs, unwraps IPv4-mapped IPv6, port allow-list, per-hop redirect re-validation, DNS-rebind pinning.
- **XSS**: chat bodies escaped via `escapeHTML()` before `innerHTML`; usernames restricted to `[A-Za-z0-9_.-]`.
- **Secrets**: no hardcoded keys/creds; `.env` untracked; no `debug=True`; SECRET_KEY fail-closed in prod.
- **`UserDailyNutrition` migration is fully clean** — no code stragglers reference the dropped table.
- **Migration chain** is linear with a single head; FK `ondelete` coverage complete; indexes sensible.
- Config (SECRET_KEY/TLS fail-closed, lazy AI clients, feature-flag defaults) and CSRF (Origin + synchronizer token) are solid.
