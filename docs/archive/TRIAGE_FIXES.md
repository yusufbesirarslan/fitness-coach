# Triage — Bugs, Security & Structure Findings

Codebase triage of the Fitness Coach (FitX) app. Findings are grouped by
severity. Each item lists the location, the problem, and a suggested fix.
The app is overall well-hardened; the items below are the remaining gaps.

> Status: triage only — no code changes have been applied for these items yet.

---

## 🔴 HIGH

### 1. Silent 0-macro meal corrupts the canonical ledger
- **Location:** `app/blueprints/nutrition.py:570-588`
- **Problem:** The free-text meal-logging route wraps the OpenAI call + JSON
  parse in a `try/except Exception` that only logs
  (`current_app.logger.info("MEAL LOG ERROR...")`). On any failure `nutrients`
  stays `{"kalori":0, "protein":0, "karb":0, "yag":0}` and execution falls
  through to `MealLog(...)` + `db.session.commit()` **unconditionally**. This
  writes a permanent zero-macro row into the canonical `MealLog` ledger and
  returns `{"message": "... kaydedildi."}` (success) to the user. Daily totals,
  the protein nudge, and weekly reports are corrupted with no error surfaced.
- **Fix:** Track a `parsed_ok` flag (or detect an all-zero result) and return
  an error (HTTP 502/400) instead of committing, or mark the row so totals can
  exclude it. At minimum, surface the failure to the client.

### 2. MCP server leaks FatSecret OAuth token over cleartext HTTP
- **Location:** `fitx_mcp/server.py:411`
- **Problem:** `FATSECRET_BASE_URL = os.environ.get("FATSECRET_BASE_URL", "http://18.153.156.28:3000")`
  keeps the exact insecure default the main app already removed (`app/config.py:14-17`
  documents the old `http://<public-ip>:3000` default leaked the OAuth bearer
  token in cleartext HTTP; the app now defaults to `""` and enforces TLS via
  `_enforce_fatsecret_tls`). If `FATSECRET_BASE_URL` is unset, the MCP server's
  `search_nutrition_data` / `analyze_and_rank_menu` send
  `Authorization: Bearer <token>` to a plaintext public IP. There is no
  `_enforce_fatsecret_tls` equivalent here, and the MCP server ships standalone
  (can be run directly).
- **Fix:** Default to `""` and refuse to call FatSecret (or require `https://`)
  when unset, mirroring `app/config.py:_enforce_fatsecret_tls`.

---

## 🟡 MEDIUM

### 3. Unhandled `ValueError` (HTTP 500) on non-numeric check-in fields
- **Location:** `app/blueprints/tracking.py:119-125`
- **Problem:** `yogunluk = int(data.get("yogunluk", 3))` (and `fatigue`,
  `uyku_kalitesi`, `beslenme_uyumu`) are cast with no guard. `weight` just above
  has a proper `try/except ValueError`, but these four do not. A client/garbled
  payload sending `"yogunluk": ""` or `"yogunluk": "yüksek"` throws `ValueError`
  → unhandled 500.
- **Fix:** Coerce via a helper (an int variant of `validators._to_float`) with a
  default, or wrap in `try/except` returning 400.

### 4. Serving-weight 150g fallback silently mis-scales FatSecret macros
- **Location:** `app/services/ai_nutrition.py:565-623` (`_estimate_serving_weights_llm`),
  consumed by `app/services/fatsecret.py:273-282`
- **Problem:** When the weight LLM returns a value outside `50 <= grams <= 600`
  or fails, the code substitutes `fallback_weights.get(name, 150.0)`. That 150g
  default is then used to convert a per-serving FatSecret entry to per-100g
  (`scale = 100.0 / weight_g`). For a food whose true serving is e.g. 350g, this
  over-scales macros ~2.3×. The fallback is intentional but, combined with
  per-serving scaling, produces systematically wrong per-100g values for any
  food the weight LLM can't size — silently, into the menu/macro pipeline.
- **Fix:** Flag/log low-confidence sizing; consider skipping per-100g scaling
  when the weight is a fallback rather than an estimate.

### 5. Missing DB indexes on hot social queries
- **Location:** `app/models.py` — `Message.sender_id/receiver_id`,
  `Friendship.sender_id/receiver_id`
- **Problem:** `social.py` queries heavily with `OR(sender_id==me, receiver_id==me)`
  and `filter_by(receiver_id=...)`, but these columns are unindexed. On Postgres,
  a foreign key does not create an index automatically.
- **Fix:** Add indexes on `message.receiver_id` (+ `sender_id`) and
  `friendship.receiver_id` / `sender_id` via an Alembic migration.

### 6. Schema-drift risk across three evolution mechanisms
- **Location:** `app/db_init.py` (raw idempotent `ALTER TABLE` list, ~lines 36-66),
  `db.create_all()`, and the Alembic migration chain
- **Problem:** New columns get `create_all`'d on fresh DBs, `ALTER`'d on legacy
  DBs, and (sometimes) added in Alembic. The three can drift: a model change
  with `create_all` + an `init_database` ALTER but no Alembic revision is
  invisible to `flask db upgrade` on a DB stamped before the change. `db_init`
  also swallows every ALTER exception silently, hiding genuine failures.
- **Fix:** Freeze the raw-ALTER list (treat as historical), route all new
  changes through Alembic only, add a CI check that `flask db migrate` produces
  an empty diff against the models, and at least log ALTER errors at debug level.

### 7. Single gunicorn worker is load-bearing
- **Location:** `Dockerfile`, `docker-compose.yml`, boot path in `app/db_init.py`
- **Problem:** Once-per-boot/once-per-week work (rollover, quest seeding,
  referral backfill, leaderboard rebuild) assumes a single instance. This caps
  horizontal scaling and lets one slow 300s AI request starve concurrency.
- **Fix:** Gate once-per-boot/week work behind a DB advisory lock
  (`WeeklyResetLog` already gives rollover idempotency) so workers can be raised
  safely later.

### 8. Deploy patches live nginx config and has no rollback
- **Location:** `.github/workflows/deploy.yml`
- **Problem:** Each deploy greps the live nginx config and `sed`-deletes any
  `add_header Content-Security-Policy` line (CSP moved to Flask). This mutates
  host state outside version control every deploy and fights `nginx.conf` if CSP
  is re-added there. Separately, `git reset --hard origin/main` +
  `docker compose up -d --build` has no rollback if a commit builds but boots
  unhealthy.
- **Fix:** Make `nginx.conf` the single source of truth and copy it wholesale
  (idempotent), removing the CSP-strip. Keep the previous image and roll back on
  smoke-check failure.

### 9. No container resource limits / Redis eviction policy
- **Location:** `docker-compose.yml`
- **Problem:** No `mem_limit`/`cpus` on any service — a Pillow/AI memory spike or
  Postgres/Redis runaway can OOM the single EC2 host and take everything down.
  Redis has no healthcheck and no `maxmemory`/eviction policy, so leaderboard
  sorted-sets + limiter storage can grow unbounded.
- **Fix:** Set per-service memory limits; add a Redis healthcheck and
  `--maxmemory ... --maxmemory-policy`.

---

## 🟢 LOW / informational

- **Weekly-report nudge fires twice a week** — `analytics_engine.py:111-116`:
  `today.weekday() in (0, 6)` triggers on both Sunday and Monday. Consistent
  with the coach prompt ("Pazartesi/Pazar") but likely duplicates a "weekly"
  cadence on two consecutive days. Confirm intent.
- **`level_up` activity never emitted** — `app/services/gamification.py:77-84`:
  `award_xp` updates points but never detects crossing a 500-point level
  boundary, so the defined `level_up` social-feed icon is never created. Dead
  config or missing feature.
- **Inconsistent confirmation message** — `app/blueprints/tracking.py:63-71`:
  the weight-gain branch uses `message = ...` (drops the "kaydedildi" prefix)
  while loss/equal branches use `message += ...`. Cosmetic.
- **Dead table retained** — `UserDailyNutrition` has no writes (only the class
  def, a backfill migration, and bulk-delete in `cli.py`). Mark deprecated/
  read-only or schedule a drop migration once backfill is verified everywhere.
- **Root-level modules outside the package** — `nutrition_pipeline.py`,
  `analytics_engine.py`, `s3_helper.py` live outside `app/` and are imported as
  top-level modules (works only because cwd is the repo root). Consider folding
  into `app/services/`.
- **No rate limit on social write endpoints** — `social.py:89,197,228`,
  `supplements.py:28`: `friend_request` / `chat_send` / `supplement_add` are
  `@login_required` + CSRF-protected but unthrottled (abuse / row flooding).
  Consider a modest `@limiter.limit`.
- **Postgres-only logic untested** — cascade deletes, the macro CHECK
  constraint, and the `calc_activity_calories` PL/pgSQL trigger run only in
  prod; tests run on SQLite where those migrations are no-ops. Add a Postgres
  service container in CI for a small subset.
- **`_repair_truncated_json` dead condition** — `ai_nutrition.py:639`: the
  `not escape_next` check is always true at that point (the escape case
  `continue`s earlier). Harmless but misleading.
- **`meal_logged` quest reward mismatch** — defined in both the seed block
  (reward 20) and `_extra_quests` (reward 15) in `db_init.py`; the
  `if not ...first()` guard prevents a duplicate row (first writer wins), but
  the mismatched reward is a latent inconsistency if seeding order changes.

---

## ✅ Confirmed solid (no action needed)

IDOR / ownership scoping (every record loaded by ID checks `user_id`), CSRF
(Origin/Referer fail-closed), CSP + per-request nonce, SSRF defenses in
`app/services/menu_fetch.py` (public-IP validation, port allow-list, per-hop
redirect re-validation, DNS-rebinding protection), secrets handling (no
hardcoded keys, IAM instance profile, presigned S3 URLs), session/cookie
security, parameterized SQL, `app/timeutil` discipline for day keys, MealLog as
single canonical ledger, weekly-rollover idempotency, the Redis leaderboard
dirty-flag on `after_commit`, and the MCP HTTP transport correctly gated behind
`FITX_MCP_ALLOW_HTTP=1` + loopback.

---

## Suggested order of work

1. **#1** Silent 0-macro meal commit (data integrity, user-invisible).
2. **#2** MCP FatSecret cleartext token default (security, small fix).
3. **#3** Check-in `ValueError` → 500 (robustness, small fix).
4. **#5** Missing social indexes (cheap, hot queries).
5. **#6 / #8** Schema-drift CI check and nginx-config source-of-truth.
