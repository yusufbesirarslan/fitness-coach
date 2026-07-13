# FitX — Triage Findings & Needed Fixes

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

Deep-dive triage of the Fitness Coach (FitX) codebase across three tracks:
**Security**, **Backend correctness**, and **Data / infra / concurrency**.
Findings are ranked by severity within each track. Each item cites `file:line`,
a failure/exploit scenario, and a suggested fix.

> Overall the codebase is unusually well-hardened (SSRF DNS-pinning, two-layer
> CSRF, fail-closed login throttle, session-fixation prevention, Fernet token
> encryption, IAM-based secrets, server-side `user_id` injection for LLM tools,
> clean linear migration chain with CI model↔migration parity). No **Critical**
> issues were found. The items below are real defects worth addressing, led by
> one **HIGH** authentication bypass.

---

## Priority summary

| # | Severity | Area | Issue | File |
|---|----------|------|-------|------|
| S1 | **HIGH** | Security | Cognito MFA/challenge & empty-claims treated as successful login (auth bypass) | `app/blueprints/auth.py:232` |
| B1 | **HIGH** | Backend | Concurrent wearable sync → IntegrityError drops entire sync | `app/services/wearables/sync.py:81` |
| B2 | **HIGH** | Backend | Training level permanently capped at self-reported; hysteresis is dead code | `app/services/training_generation/classifier_service.py:16` |
| D1 | **HIGH** | Infra | Synchronous AI calls can exhaust all 8 worker threads (app-wide stall incl. /health) | `Dockerfile`, `app/extensions.py:78` |
| D2 | **HIGH** | Infra | Deploy rollback reverts code but not auto-applied DB migrations | `.github/workflows/deploy.yml:32`, `app/db_init.py:33` |
| D3 | **HIGH** | Infra | `/health` returns 200 even when the DB is unreachable → no auto-rollback | `app/__init__.py:34` |
| B6 | **MED** | Backend | Diary macros bypass sanitation for negative values → corrupts canonical MealLog | `app/blueprints/nutrition/diary.py:190` |
| D-M1 | **MED** | Infra | `award_xp` lost-update race (no row lock) → leaderboard corruption | `app/services/gamification.py:77` |
| D-M2 | **MED** | Infra | Weekly AI quota bypassable under concurrency (TOCTOU + JSON lost-update) | `app/services/premium.py:102` |
| B3–B5, B7–B12 | **MED** | Backend | Wearable/AI/validator correctness defects (see below) | various |
| D-M3, D-M4 | **MED** | Infra | Nullable `PumpCheck.date_key`; DailyActivity trigger drift | `app/models.py:282`, `app/db_init.py:52` |
| S2–S4 | **LOW** | Security | i18n `|safe` XSS sink; unverified JWT signature; unrate-limited wearable routes | various |
| B13–B22 | **LOW** | Backend | Timeutil bypass, None-guards, menu/token edge cases | various |
| D-L1–L4 | **LOW** | Infra | create_all masking drift; lazy-init race; dep pins; referral collision | various |

---

## Track 1 — Security

### S1 — HIGH: Cognito challenge / empty-claims treated as a successful login (auth bypass)
**File:** `app/blueprints/auth.py:232-234` — root cause `app/services/cognito_idp.py:159-185`

`initiate_auth()` returns `_decode_claims(id_token)` where
`id_token = (resp.get("AuthenticationResult") or {}).get("IdToken", "")`.
When Cognito responds with a **challenge** instead of tokens
(`SMS_MFA`, `SOFTWARE_TOKEN_MFA`, `NEW_PASSWORD_REQUIRED`, …) or when token
decode fails, `claims` becomes `{}`. The identity guard
`if claims.get("sub") and claims["sub"] != user.cognito_sub:` short-circuits on
the falsy `sub`, is skipped, and `_login_fresh(user)` logs the user in.

**Exploit:** If the pool enforces MFA, an attacker with only the *password*
(phished/reused) submits it; Cognito validates the password and returns an MFA
`ChallengeName` with no `AuthenticationResult`. The app receives empty claims and
grants a full authenticated session — **the second factor is never requested.**
Same fall-through logs in on `NEW_PASSWORD_REQUIRED` (admin-created / forced-reset
accounts) and on any malformed-token decode.

**Fix:** Treat login as a *positive* assertion — reject (401) unless
`claims.get("sub")` is present **and** equals `user.cognito_sub`. In
`cognito_idp.initiate_auth`, detect a `ChallengeName` in the response and raise
`CognitoIdpError` (or explicitly reject) rather than returning `{}`.

### S2 — LOW: `i18n_json` injected into an inline `<script>` via raw `json.dumps` + `|safe`
**File:** `templates/_head.html:15` (data built in `app/hooks.py:107-114`)

`window.I18N = {{ i18n_json|safe }}` embeds `json.dumps(..., ensure_ascii=False)`
directly inside a `<script>` block. `json.dumps` does not escape `<`, `>`, `&`, or
the `</script>` sequence. Not exploitable today (developer-controlled catalog) but
a latent XSS sink that the CSP nonce would not stop (executes inside the nonced block).

**Fix:** Use `|tojson` (`window.I18N = {{ catalog|tojson }}`), which escapes
`<`, `>`, `&`, and `U+2028/2029` for safe embedding in HTML `<script>`.

### S3 — LOW: Cognito ID token signature not verified (no defense-in-depth)
**File:** `app/services/cognito_idp.py:170-185`

`_decode_claims` base64-decodes the JWT payload without verifying signature,
issuer, audience, or expiry. Defensible today (token arrives over TLS from the
app's own boto3 call), but there is no fallback if that trust assumption breaks
(endpoint/region misconfig). Ties into S1. **Fix:** verify against the pool's JWKS,
or at minimum treat empty/invalid claims as failure.

### S4 — LOW: Wearable endpoints trigger outbound requests with no rate limiting
**File:** `app/blueprints/wearables.py:70-96`

`/api/wearables/<provider>/sync` and `/api/wearables/whoop/<resource>` carry no
`@limiter.limit`, unlike the AI/menu/food routes. Each call issues outbound HTTP
to WHOOP/Google. An authenticated user can loop them for third-party rate-limit
exhaustion / cost amplification. **Fix:** add a per-user limit consistent with
other outbound routes. (Note: `whoop_resource` endpoints are whitelisted, so this
is *not* SSRF.)

### S5 — INFORMATIONAL: `friends.html:176` renders a translation string with `|safe`
Safe today (static string), fragile pattern. Prefer rendering without `|safe`.

**Security areas verified SOUND (no action):** IDOR/ownership scoping across all
by-ID loads and S3 keys (`_key_belongs_to`); MCP HTTP transport gated behind
`FITX_MCP_ALLOW_HTTP=1` + loopback, `user_id` injected server-side; SSRF defenses
in `menu_fetch.py` (public-IP validation, port allow-list, DNS-rebinding TOCTOU
closed); parameterized DB access; two-layer CSRF + per-request nonce; client-side
`escapeHTML`/`esc` on all untrusted text; no hardcoded secrets; Fernet-encrypted
wearable tokens; secure/HttpOnly/SameSite cookies; fail-closed login throttle.

---

## Track 2 — Backend correctness

### B1 — HIGH: Concurrent wearable sync throws IntegrityError and drops the entire sync
**File:** `app/services/wearables/sync.py:15-59, 81`

Each `_upsert_*` does SELECT-then-INSERT with no lock, and `sync_provider_day` has
a single trailing `commit()` with no `IntegrityError` handling (unlike `tokens.py`).
Double-click "sync" or a cron overlapping a manual `POST .../sync` (likely with
1 worker/8 threads): both threads see `row is None`, both `add()`, the second
`commit()` violates `uq_wearable_sleep_source`/`uq_wearable_activity_day` → the
whole commit fails, `wearable_sync` returns 502 with no rollback, all synced data
for that call is lost. **Fix:** wrap commit in `try/except IntegrityError` with
rollback + per-row re-fetch-update retry (mirror `save_wearable_tokens`), or use
`INSERT ... ON CONFLICT DO UPDATE`.

### B2 — HIGH: Training level permanently capped at self-reported; hysteresis is dead code
**File:** `app/services/training_generation/classifier_service.py:16-20`

`final_numeric = min(suggested, reported, hard_cap, observed_cap)` includes
`reported`, so `final_numeric ≤ reported` always — the next line
`if final_numeric > reported and ...stable_score_weeks < 3:` can never be true.
Because `build_features` defaults `self_reported_level` to `"beginner"` when no
`fitness_level` is stored (`feature_extractor.py:60`), any user without a stored
level is locked to Beginner regardless of observed performance. **Fix:** drop
`reported` from the `min()` so the stability-window hysteresis actually gates upgrades.

### B6 — MED: Diary item macros bypass sanitation for negative/crafted values → corrupts canonical MealLog
**File:** `app/blueprints/nutrition/diary.py:190-208, 290-307`

The B8 fix clamps only `qty`/`metric_amt` to ≥0, not the per-serving macro values
(`srv_cal`/`srv_pro`/…); the quantity-rescale and grams branches clamp neither
`serving_quantity` nor `grams`. `_clamp_item_macros`→`clamp_serving_macros` only
scales *down positive over-caps* — negatives pass unchanged. A crafted PATCH/POST
with negative `serving_calories`/`grams`/`serving_quantity` writes negative macros
into `CustomMealItem`; `diary_log_meal` sums them into MealLog, silently dragging
daily totals / protein nudge / weekly reports downward. **Fix:** clamp all macro
inputs and grams/quantity to ≥0 (floor in `_clamp_item_macros`).

### Other backend MED findings
- **B3** `app/services/wearables/adapters.py:266-327` — Google Health adapter calls
  `requests.get/post` directly, bypassing `BaseWearableAdapter.request()` and its
  401→refresh→retry path. A stale Google token 401s and sync fails.
- **B4** `app/services/ai_coach.py:1148-1156` (raise site `1306-1313`) — only
  `_BedrockFallback` is caught; other Bedrock-loop exceptions (e.g. `resp.content is
  None` → `TypeError`) escape the OpenAI fallback and 500 the coach. Broaden to
  `except Exception` that degrades to OpenAI when `tools_ran == 0`.
- **B5** `app/services/ai_coach.py:405-408` — `float(per_100g.get("calories", 0))`
  raises when the dict has an explicit `None`. Use `float(per_100g.get("calories") or 0)`.
- **B7** `response_validator.py:41-48` — `tip=="antrenman"` day accepted with zero
  exercises. Require `len(exercises) >= 1`.
- **B8** `response_validator.py:35-37` — weekdays not required unique (7× "Pazartesi"
  passes). Track seen days / reject repeats.
- **B9** `response_validator.py:44,45,55` — `int("3-4")`/`"45 dk"`/`"~300"` raise a
  bare `ValueError` that escapes the caller's `(JSONDecodeError, PlanValidationError)`
  catch → generic 500. Parse defensively.
- **B10** `sync.py:71` + `adapters.py:225` — WHOOP `fetch_workouts` called twice per
  sync (double rate-limit; possible inconsistency). Pass the fetched list into
  `fetch_activity`.
- **B11** `adapters.py:188,211,317` — fallback `source_id` (`whoop-sleep-{date}`) is
  identical for every record on a day when the provider omits `id`; upsert dedup
  collapses distinct naps/workouts. Add a per-record discriminator (start time/index).
- **B12** `adapters.py:22-28` — `_parse_dt` does `.replace(tzinfo=None)` without
  converting non-UTC offsets; a Google `+03:00` timestamp is stored 3h off.
  Use `dt.astimezone(timezone.utc).replace(tzinfo=None)` when tz-aware.

### Backend LOW findings
- **B13** `ai_coach.py:177` — check-in day label from naive-UTC `created_at.date()`
  bypasses timeutil; 00:00–03:00 Istanbul labeled a day early. Use `app_date_of`.
- **B14** `ai_coach.py:1343,1464` — `goal.lower()` outside try/except; `goal=None` →
  `AttributeError`. Use `(goal or "").lower()`.
- **B15** `ai_coach.py:1092-1114` — `_sanitize_client_history` doesn't merge
  consecutive same-role turns; Anthropic 400s → silent gpt-4o-mini fallback.
- **B16** `ai_coach.py:1158-1168` — provider error-fallback strings persisted into
  `session["coach_history"]` and fed back as context. Skip history append on fallbacks.
- **B17** `feature_extractor.py:39` — `{"advanced":2}` maps advanced == intermediate;
  movement subscore can't distinguish advanced. Likely `"advanced":3`.
- **B18** `menu_extract.py:112-128` — JSON-LD `item.get(...)` on a list containing a
  string/`null` raises `AttributeError`, uncaught. Add `isinstance(item, dict)` guard.
- **B19** `menu_fetch.py:380` — Content-Length `int()` (Google Drive) after the
  request try/except; non-numeric header propagates.
- **B20** `menu_fetch.py:293-296,135-136` — a 3xx with no `Location` returns the raw
  streamed response, later read with no `_MAX_FETCH_BYTES` cap.
- **B21** `tokens.py:21-24,51-53` — `_expiry_from_payload` may assign a raw string/epoch
  `expires_at`/`token_expiry` into a DateTime column. Only accept `datetime`, else parse.
- **B22** `adapters.py:116-119` — missing `token_expiry` forces a refresh every request
  and raises if `refresh_token` also missing though the access token may be valid.

**Backend verified SOUND:** `analytics_engine.py:32` / `hooks.py:197` `utcnow()`
correctly compares naive-UTC columns (not a day-key violation); ai_coach day-keys
use `day_key`/`app_today`/`utc_day_bounds`; double-confirm meal/workout logging is
atomic/idempotent; nutrition_pipeline scoring & gamification weekly rollover are sound;
`meallog.log_meal` refuses all-zero macros on parse failure.

---

## Track 3 — Data / infra / concurrency

### D1 — HIGH: Synchronous AI calls can exhaust all 8 worker threads (app-wide stall, incl. /health)
**Files:** `Dockerfile` (gunicorn `--workers 1 --threads 8 --timeout 300`) +
blocking Bedrock `app/extensions.py:78-96` (timeout 60s) / coach tool-loops.

8 concurrent `/ask` or plan-generation requests occupy every thread for up to 300s;
all other requests — including the Docker `HEALTHCHECK` and the deploy health-gate
curl — queue behind them. Per-user `BEDROCK_RATELIMIT=10/hr` doesn't stop N distinct
users from saturating the single worker. (This is the exact risk flagged in CLAUDE.md.)
**Fix:** move AI endpoints to a separate worker/process or async queue; or add a
dedicated lightweight health worker; don't raise `--workers` until the in-memory
limiter/cache assumptions are removed.

### D2 — HIGH: Deploy rollback reverts code but not the auto-applied DB migrations
**Files:** `.github/workflows/deploy.yml:32` (`git reset --hard "$PREV_COMMIT"`) +
boot-time auto-upgrade `app/db_init.py:33-36`.

A failed deploy still boots the new container once, which auto-runs `flask db upgrade`
against shared RDS. The health gate rolls the *code* back but the schema stays
forward — no `downgrade()` runs. Additive migrations are tolerated, but a destructive
one (`f6a7b8c9d0e1` DROP `user_daily_nutrition`, `b8c9d0e1f2a3` `meal_log.tarih` SET
NOT NULL) leaves rolled-back code against an incompatible schema. **Fix:** gate
migrations behind a separate one-shot job (`FITX_DB_AUTO_UPGRADE=0` on web, which
db_init.py already anticipates), migrating only after the new code passes health;
or make rollback include a schema downgrade to the pinned revision.

### D3 — HIGH: `/health` returns 200 even when the database is unreachable
**File:** `app/__init__.py:34-42` — checks only `limiter_storage_status()`, never DB.

If RDS is down/misconfigured, every real request 500s but `/health` still says 200:
Docker `HEALTHCHECK` stays green and the deploy health-gate passes, so a deploy that
breaks DB connectivity will **not** auto-rollback and monitoring won't fire.
**Fix:** add a cheap `SELECT 1` (short timeout) to `/health`; keep limiter status as
an informational field but let DB failure flip status to 503.

### Infra MED findings
- **D-M1** `gamification.py:77-92` — `award_xp` does a plain ORM read-modify-write
  (`rank_points = old + amount`) with no row lock, unlike `update_streak`
  (`hooks.py:220` uses `with_for_update()`). Concurrent XP grants for one user
  → lost update → leaderboard undercount. Use an atomic in-DB increment
  (`User.rank_points = User.rank_points + amount`) or `with_for_update()`.
- **D-M2** `premium.py:102-129` + `64-99` — weekly AI quota is check-then-record
  (TOCTOU) and counters are a whole-object JSON rewrite on `user_metadata`
  (lost-update). A free user (quota 1) firing parallel requests all see
  `remaining==1`, all generate. Enforce with an atomic conditional UPDATE / row lock;
  store counters in separate columns.
- **D-M3** `app/models.py:282-287` — `PumpCheck.date_key` is `nullable=True` under
  `UniqueConstraint("user_id","date_key")`; in Postgres NULLs are distinct, so the
  documented double-XP idempotency guard only holds if every writer sets `date_key`.
  Make it NOT NULL (backfill first).
- **D-M4** `app/db_init.py:52-92` — `calc_activity_calories()`/`trg_calc_activity`
  are created on every boot (Postgres only) and overwrite app-supplied
  calories/distance/duration; SQLite dev/test has no trigger → divergent stored
  values and untestable calc. Also outside the Alembic chain, so the drift guard
  can't see it. Move into a migration; make the app the single source of truth.

### Infra LOW findings
- **D-L1** `db_init.py:42` — `db.create_all()` runs on every boot after `upgrade()`,
  silently creating any table/index a migration forgot (masks drift; CI catches it
  only via `FITX_SKIP_DB_INIT=1`). Consider skipping when `alembic_version` exists.
- **D-L2** `extensions.py:59-96` — lazy AI client init isn't thread-safe; two threads
  race the first call and both construct a client (benign, wasteful). Add a lock /
  eager singleton.
- **D-L3** `requirements.txt` — several pins look implausibly high / possibly
  nonexistent: `requests==2.34.2` (real max 2.32.x), `Werkzeug==3.1.8`,
  `gunicorn==26.0.0`, `redis==8.0.1`. If any don't resolve on PyPI the Docker build
  and deploy fail hard. Verify they resolve in the index.
- **D-L4** `referral.py:20-32` — `generate_referral_code` is check-then-insert with no
  `IntegrityError` handling; a collision on `uq_user_referral_code` surfaces a raw
  error. Negligible at ~79 bits, but a try/except-retry is fully robust.

**Infra verified SOUND:** migration chain is a clean linear `down_revision` chain
(no gaps/branches) with CI `flask db check` enforcing model↔migration parity against
real Postgres; FK cascades/indexes/unique indexes match migrations; foodcache in-memory
dicts guarded by `_cache_lock` + bounded FIFO + copy-on-read; weekly rollover idempotent
via `WeeklyResetLog` UNIQUE + Redis `SET NX EX`; SQLite `PRAGMA foreign_keys=ON`;
`LOGIN_FAIL_CLOSED` fails closed to 503 when Redis is down; secrets from IAM/env;
services bound to loopback; CSP-duplication fail-fast guard matches the nginx/hooks design.

---

## Suggested remediation order

1. **S1** — patch the Cognito auth bypass (reject unless non-empty claims + matching
   `sub`; treat challenges as failure). *Highest security priority.*
2. **D3** — add a `SELECT 1` to `/health` so broken deploys actually roll back.
3. **D2** — decouple migrations from the web-container boot before the next
   destructive migration ships.
4. **B1 / D-M1 / D-M2** — add IntegrityError handling / atomic increments to close
   the concurrency data-corruption paths (wearable sync, XP, quota).
5. **B6** — clamp diary macro inputs to ≥0 to protect the canonical MealLog ledger.
6. **B2** — fix the training-level classifier so users can progress past Beginner.
7. **D1** — plan the AI-endpoint isolation (worker/queue) — larger effort, but the
   single-worker stall risk is real.
8. Remaining MED/LOW items as capacity allows; verify **D-L3** dependency pins now
   (a hard build failure risk).
