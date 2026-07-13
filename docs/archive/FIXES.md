# Fitness Coach — Triage & Needed Fixes

_Generated 2026-07-12 from a deep-dive triage of the `app/` package. Three
independent passes covered (1) backend correctness/logic, (2) security, and
(3) structure/config/deployment. `starter.py.bak` (stale 288 KB backup) was
excluded from review._

**Headline:** The refactored `app/` architecture is clean and the security
posture is genuinely strong (SSRF DNS-pinning, IDOR ownership checks,
parameterized SQL, Jinja autoescape, CSRF Origin/Referer allowlist, non-root
container, OIDC deploy). No Critical or High **security** vulnerabilities were
found. The real risk is a **correctness bug that silently corrupts nutrition
totals**, plus maintenance/config debt.

---

## Priority summary

| # | Sev | Area | Issue | Fix location |
|---|-----|------|-------|--------------|
| 1 | **HIGH** | Correctness | `MealLog.tarih` stored as `%d.%m` (no year) → cross-year data collision | `nutrition.py`, `social.py` |
| 2 | HIGH | Repo/Deploy | `starter.py.bak` (288 KB) committed & baked into Docker image | `starter.py.bak`, `.dockerignore` |
| 3 | HIGH | Deploy | No migrations; `create_all()` + silent raw `ALTER TABLE` on every boot | `app/db_init.py` |
| 4 | HIGH | Docs | `CLAUDE.md` substantially stale/misleading | `CLAUDE.md` |
| 5 | MED | Correctness | `checkin` crashes on `null`/`""` numeric fields (uncaught `TypeError`/`ValueError`) | `tracking.py:110` |
| 6 | MED | Perf/Cost | `_food_search_fatsecret` fires up to 8 sequential OpenAI calls in a loop | `services/fatsecret.py:260` |
| 7 | MED | Correctness | `update_weight`/`chat` crash when profile fields are `None` | `tracking.py:205` |
| 8 | MED | Security | Missing rate limiting on authenticated write endpoints | multiple blueprints |
| 9 | MED | Structure | Dead `register_hooks()` duplicates factory wiring | `app/hooks.py:95` |
| 10 | MED | Testing | Only pure pipeline logic tested; no route/auth/DB/CI coverage | `tests/` |
| 11 | MED | Deploy | Loose dep pins + dev/tooling deps in prod image | `requirements.txt` |
| 12 | LOW | many | See "Lower-priority" section | — |

---

## HIGH priority

### 1. `MealLog.tarih` stored without a year → cross-year totals corruption
**Severity: HIGH (correctness) — the top-impact bug.**

Meal dates are stored as day-month only via `datetime.utcnow().strftime("%d.%m")`
(e.g. `"12.07"`), then read back with exact string equality
`filter_by(tarih=today)`. Any "today"/"consumed today"/history-grouping read
therefore mixes entries from the same calendar day **across different years**.

- **Write sites:** `app/blueprints/nutrition.py:89` (quick_add_meal),
  `:322` (diary_log_meal), `:471`/`:551` (log_meal);
  `app/blueprints/social.py:302`, `:366`.
- **Read/filter sites:** `nutrition.py:582` (today_meals `filter_by(tarih=today)`),
  `:639` (review_meals), `:607-631` (meal_history groups by `m.tarih`);
  `app/blueprints/menu.py:194-195` ("consumed today" for the remaining-budget math).
- **Inconsistency (confirmed):** `CustomMeal`, `DailyActivity`, `WaterLog`, and
  the diary routes correctly use `date.today().isoformat()` (full `YYYY-MM-DD`).
  Only `MealLog` uses the truncated format.
- **Failure scenario:** On 2026-07-12, today/consumed/review totals include any
  meal logged on 12 July of *any* prior year; `meal_history` merges those into
  one bucket → inflated calories/macros and wrong menu "remaining budget."
- **Fix:** Store `date.today().isoformat()` (or migrate `tarih` to a real `Date`
  column) and filter on it. Migrate existing rows (prepend a year). Do all write
  and read sites together so the format stays consistent.

### 2. `starter.py.bak` committed and shipped into the production image
**Severity: HIGH (repo hygiene / image bloat).**

The stale 288 KB monolith backup is in git. `.dockerignore` does **not** exclude
it (nor `tests/`), and the Dockerfile does `COPY . .`, so it is baked into every
prod image. It still `import`s live modules, so it looks "active" to grep.
- **Fix:** `git rm starter.py.bak`; add `starter.py.bak`, `*.bak`, `tests/`,
  `.github/`, `README.md` to `.dockerignore`.

### 3. No migration strategy — `create_all()` + silent raw `ALTER TABLE` on boot
**Severity: HIGH (data/deploy risk).** — `app/db_init.py:9-71`

Schema is built with `db.create_all()`, then ~17 hand-written `ALTER TABLE`
statements + a PL/pgSQL trigger run on **every** startup, each wrapped in
`try/except: rollback()` that **swallows all exceptions**. Statements are
Postgres-only and silently no-op/fail on the SQLite local path. `create_all()`
never alters existing tables, so schema evolution depends entirely on this
fragile list, and real failures (permissions, type mismatch, partial migration)
are hidden.
- **Fix:** Adopt Flask-Migrate/Alembic. Minimum interim step: log the swallowed
  exceptions instead of discarding them, and guard the Postgres-only DDL by
  dialect.

### 4. `CLAUDE.md` is substantially stale / misleading
**Severity: HIGH (onboarding).**

- Claims "`starter.py` — tüm backend"; the backend is fully refactored into the
  `app/` package (11 blueprints + `services/` + `models.py`). `starter.py` is now
  a 13-line shim.
- Lists 8 models; `app/models.py` actually defines ~23 (`DailyQuest`,
  `DailyActivity`, `Supplement`, `Message`, `Friendship`, `WaterLog`, …).
- Says `postgres:15-alpine`; `docker-compose.yml` uses `postgres:18-alpine`.
- No mention of Redis, `fitx_mcp/`, `analytics_engine.py`,
  `nutrition_pipeline.py`, or the blueprint architecture.
- **Fix:** Rewrite "Yapı"/"Veritabanı" to describe the current `app/` package,
  model set, Redis, and Postgres 18.

---

## MEDIUM priority

### 5. `checkin` crashes on null/empty numeric fields
`app/blueprints/tracking.py:110-116` — `int(data.get("yogunluk", 3))` etc. The
`.get(key, default)` default only applies when the key is **absent**; a present
JSON `null` gives `int(None)` → `TypeError`, and `""` gives `int("")` →
`ValueError`. Neither is caught (the surrounding `try` guards only the weight
`float`). Also affects `log_daily_activity` (`:240`, `int(data.get("steps",0))`,
no try at all).
- **Fix:** Coerce via an int-safe helper, or wrap in `try/except (ValueError,
  TypeError)` returning 400.

### 6. `_food_search_fatsecret` makes one OpenAI call per result, in a loop
`app/services/fatsecret.py:260-262` — inside `for f in foods:`, each per-serving
result calls `_estimate_serving_weights_llm([f.get("food_name")])`, a single-item
LLM call. `foods.search` returns up to 8 results → up to 8 sequential OpenAI
round-trips per search → slow, timeout-prone, and 8× the token cost. The function
already accepts a list (supports batching).
- **Fix:** Collect all per-serving food names first, call
  `_estimate_serving_weights_llm(names)` **once**, then map results back.

### 7. `update_weight` / `chat` crash when profile fields are `None`
`app/blueprints/tracking.py:205-207` calls `calculate_bmr(weight,
current_user.height, current_user.age, current_user.gender)` then
`calculate_tdee(bmr, current_user.current_activity)` with no profile-complete
guard. If `height`/`age` is `None` → `6.25 * None` → `TypeError`
(`calculations.py:7`); if `current_activity` is `None` → `None.lower()` →
`AttributeError` (`calculations.py:18`). Latent because the UI hides it behind
`profile_complete`, but the route is directly reachable.
- **Fix:** Guard the route on `current_user.profile_complete`, or null-check
  before computing.

### 8. Missing rate limiting on authenticated write endpoints
`@limiter.limit(...)` is applied to AI/auth routes but not to state-changing DB
writes: `social.py:196` (`chat_send`, stores up to 2000 chars/call), `:227`
(`send_suggestion`), `:88` (`friend_request`); `nutrition.py:120,143,300`;
`supplements.py:28,83`; `training.py:428` (`set_water`); `tracking.py:236`.
An authenticated user can script an insert loop to inflate DB storage/load with
no per-user cap.
- **Fix:** Add a modest `@limiter.limit(..., key_func=_user_or_ip_key)` to the
  write routes (infrastructure already imported).

### 9. Dead `register_hooks()` duplicates factory wiring
`app/hooks.py:95-102` defines `register_hooks(app)` (before_request hooks + error
handlers) but it is **never called** — `create_app()`
(`app/__init__.py:36-45`) re-implements the same registration inline to interleave
`limiter.init_app`. Two copies drift silently.
- **Fix:** Delete `register_hooks`, or refactor the factory to call it (passing
  the limiter step) as the single source of truth.

### 10. Test coverage limited to pure pipeline logic
`tests/` (3 files) covers only `nutrition_pipeline` and food/menu relevance. No
`conftest.py`, no app fixture, zero coverage of the 11 blueprints, auth/CSRF,
models, gamification/XP, rate limiting, or AI wrappers. CI
(`.github/workflows/deploy.yml`) only deploys — no test gate.
_(Suite not executed here: `pytest`/`flask` not installed in this environment.)_
- **Fix:** Add a `pytest` CI step + app-factory fixture + route/auth smoke tests.

### 11. `requirements.txt` — loose pins + dev deps in prod image
Core Flask stack is pinned (`==`) but `openai>=1.40.0`, `boto3`, `Pillow`,
`requests`, `beautifulsoup4`, `pdfplumber`, `Brotli`, `mcp[cli]` are open-ended,
and `pytest>=8.0.0` + `mcp[cli]` ship in the prod image. The host rebuilds on
every push, so a new upstream release can break prod.
- **Fix:** Pin all runtime deps exactly (or use a lockfile); split dev/tooling
  deps into `requirements-dev.txt` and exclude from the image.

### 12. Styled 500 page orphaned and misnamed
`app/hooks.py:90-91` (`server_error`) returns plain-text `"Internal Server
Error"`, but a fully styled 500 page exists as `templates/505.html` (HTTP 505 =
"HTTP Version Not Supported") and is never rendered. JSON clients also can't
parse the plain-text 500.
- **Fix:** Rename to `templates/500.html`, and `return render_template(
  "500.html"), 500` (or `jsonify` for API routes).

---

## LOW priority

- **`float()`/`int()` catch only `ValueError`, not `TypeError`** — JSON
  array/object/`null` escapes → 500. `tracking.py:46-48,105-108,198-201,240`;
  `coach.py:29-32`. Fix: `except (ValueError, TypeError)`.
- **Redis has no authentication** — `docker-compose.yml:29-39`, no `requirepass`.
  Bound to loopback/compose net, so bounded risk (Limiter has in-memory
  fallback), but a co-located compromised container could `FLUSHALL` / tamper
  leaderboard scores. Fix: set `requirepass` in `REDIS_URL`.
- **FatSecret bearer token can traverse plaintext HTTP outside Flask** — the
  Flask app enforces TLS in prod (`config.py:26-46`), but `fitx_mcp/server.py:397`
  defaults to `http://18.153.156.28:3000` with no guard, and the nginx
  `/fatsecret/` upstream is `http`. Mitigated today (MCP not run in prod). Fix:
  apply the same TLS enforcement to the MCP server; use `https` upstream if not
  strictly loopback.
- **`log_progress` message logic** (`tracking.py:63-71`) — loss branch has no
  leading space (`"80.0 kg kaydedildi.Geçen…"`); gain branch uses `message =`,
  dropping the prefix. Cosmetic. Fix: use `+=` consistently and add the space.
- **Username enumeration** via `/friends/search` and `/friend/request/<username>`
  (`social.py:64,88`). Largely by-design for a social feature; consider
  rate-limiting search.
- **Targeted login-lockout DoS** — username-keyed login limiter
  (`auth.py:16-29`) lets any IP burn a victim's failure budget for a short
  window. Acknowledged as an accepted tradeoff in code comments; self-healing.
- **Root-level modules outside `app/`** — `analytics_engine.py`,
  `nutrition_pipeline.py` are actively imported but sit at repo root, relying on
  repo root being on `sys.path`. Fix: relocate into `app/services/`.
- **Railway relics after EC2 migration** — `Procfile` (`web: gunicorn
  starter:app`) and `cli.py:29-30` ("Wire to Railway Cron") docstring. Fix:
  remove `Procfile`; update the docstring to the EC2 scheduling mechanism.
- **`.env.example` gaps** — code reads `LOG_LEVEL`, `FLASK_ENV`, `PORT`, none
  documented; `FATSECRET_BASE_URL` defaults to a hardcoded plaintext-HTTP IP in
  `config.py:51`. Fix: document the vars; move the FatSecret default out of
  source or make it required.
- **Single-worker gunicorn is a scaling ceiling** — `--workers 1 --threads 8`
  (`Dockerfile:38-42`) because the weekly-rollover self-heal
  (`hooks.py:37-46`, module-global `_last_rollover_check`) assumes one instance;
  rollover only fires when a request happens to arrive, and the `weekly-reset`
  CLI is never invoked (no cron). Fix: move weekly rollover to a scheduled job
  (systemd timer / cron calling `flask weekly-reset`, or a Redis-lock guard),
  then allow multiple workers.

---

## Ruled out (checked, not bugs)

- `respond_suggestion` (`social.py:274-286`): `nutrients` short-circuits behind
  `action == "accept"`, so no `NameError`.
- `supplement_add` first-entry XP (`supplements.py:67-72`): `count() == 1`
  fallback still awards correctly on the first supplement.
- `run_weekly_rollover` weekly reset and ISO-week formatting are correct.
- Coach LLM tool-dispatch injects `user_id` from `current_user`, not the model
  (`ai_coach.py:471-492`) — no prompt-injection IDOR.
- SSRF host-pinning / redirect handling in `menu_fetch.py` is sound; metadata IP
  (169.254.169.254) is blocked.
- All raw SQL (`fitx_mcp/server.py`, `db_init.py`) is parameterized or static.
- `profile_picture` restricted to base64 image data-URLs; `username` restricted
  to `[A-Za-z0-9_.-]`; passwords hashed with Werkzeug pbkdf2; cookies
  HttpOnly/SameSite=Lax/Secure-in-prod.

## Recommended order of attack
1. **#1** (yearless meal dates) — silent data corruption, clear fix, highest impact.
2. **#2 / #3** — stop shipping the backup; make schema changes safe (log the
   swallowed `db_init` exceptions at minimum).
3. **#5 / #7** — small guards that remove real 500s.
4. **#6** — cuts OpenAI cost/latency on food search.
5. **#4, #10, #11** — docs, tests+CI gate, dep pinning to stop regressions.
