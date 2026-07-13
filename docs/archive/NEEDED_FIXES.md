# FitX — Triage Report & Needed Fixes

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

_Living action list. Each run of the deep-dive triage appends its findings at the top and
preserves prior history below. No code is changed during triage — this document is the list._

---

# Run 2 — 2026-07-06 (3-agent deep dive: security · correctness · architecture)

This pass re-ran the full triage. Most of Run 1's findings are resolved (see the appendix).
The items below are **new** — not present in Run 1 — plus a few Run-1 items re-confirmed as
still open. Line numbers were spot-checked against current source; the ★ items were
re-verified directly against the code while writing this report.

Headline: still **no CRITICAL and no clearly-exploitable HIGH security hole.** IDOR/ownership
(the flagged rule) passes on every route examined. The standout new items are two holes in the
**A1 concurrency-gate** mitigation and a wrong-food logging bug in a FatSecret route.

## Priority shortlist

| # | Sev | Track | Fix | File |
|---|-----|-------|-----|------|
| N1 ★ | HIGH | Robustness | Add `@ai_concurrency_gate` to `/workout/complete` (blocking Bedrock **vision**) | `app/blueprints/training.py:123-126` |
| N2 | HIGH | Robustness | Add `@ai_concurrency_gate` to `/checkin` (blocking Bedrock/OpenAI) | `app/blueprints/tracking.py:142` |
| N3 ★ | HIGH | Correctness | Relevance-gate `foods[0]` before returning/caching macros | `app/blueprints/food.py:111-121` |
| N4 | MED | Correctness | Add macro floor + null-name guard to LLM food fallback | `app/services/ai_nutrition.py:327-344` |
| N5 | MED | Robustness | Explicit timeouts on boto3 S3 + Cognito clients | `s3_helper.py:84`, `app/services/cognito_idp.py:62` |
| N6 | MED | Correctness | Route MCP nutrition writes through `clamp_serving_macros` | `fitx_mcp/server.py:658-677` |

### N1 ★ HIGH — `/workout/complete` runs a blocking Bedrock vision call with NO concurrency gate
- **File:** `app/blueprints/training.py:123-126` (route) → `validate_pump_check` (`app/services/menu_extract.py:348,385`, Claude Sonnet multimodal), fired at `training.py:162`.
- **Confirmed:** the route carries only `@login_required` + `@limiter.limit(AI_RATELIMIT)`. It is **missing `@ai_concurrency_gate`** and even lacks the `BEDROCK_RATELIMIT` its siblings use.
- **Why it matters:** exactly the A1 failure mode `ai_gate.py` exists to prevent. Vision calls are the slowest/most expensive Bedrock path. 8 concurrent pump-check submissions (different users, each within its own per-user rate budget) can occupy all 8 gunicorn threads → starve `/health` and cheap routes → the Docker HEALTHCHECK / deploy health-gate flaps → **spurious rollback**.
- **Fix:** add `@ai_concurrency_gate` as the innermost decorator (below the limiter) and `@limiter.limit(BEDROCK_RATELIMIT, key_func=_user_or_ip_key)`.

### N2 HIGH — `/checkin` runs a blocking Bedrock/OpenAI call with NO concurrency gate
- **File:** `app/blueprints/tracking.py:142-195` → `generate_checkin_feedback` (`app/services/ai_coach.py:1510`).
- **Detail:** has `AI_RATELIMIT` + `BEDROCK_RATELIMIT` but **no `@ai_concurrency_gate`**. Rate limits cap per-user *frequency*; the semaphore caps cross-user *concurrency* — same thread-starvation exposure as N1.
- **Fix:** add `@ai_concurrency_gate` as the innermost decorator.
- **Coverage gap:** only 5 routes carry the gate (`nutrition/plan.py:75`, `menu.py:279`, `menu.py:91`, `training.py:73`, `coach.py:23,118`). N1, N2, and the `/api/food/search` LLM fallback are ungated. **Add a test asserting every heavy-AI route carries the gate** — that omission is why N1/N2 went unnoticed.

### N3 ★ HIGH — `food_servings_by_name` trusts `foods[0]` with no relevance gate
- **File:** `app/blueprints/food.py:100-122` (esp. 111-121).
- **Confirmed:** the route does a `max_results:1` FatSecret search, blindly takes `foods[0]`, calls `_cache_food_id(name, fid)`, and returns its servings for the user to log. This is the exact bug the team already fixed in `food_search` (comment at `food.py:37-39`: `'patates' → 'Soy Nuts'`) by switching to the relevance-gated `_coach_search_food` — **this sibling route was missed.**
- **Scenario:** a Turkish dish not in `_TR_TO_EN` (e.g. "mercimek köftesi") → FatSecret returns an unrelated generic first → its macros are logged into the canonical `MealLog` (only upper-clamped), and the wrong `name → food_id` is persisted in the **global cache**, so every later lookup for that name stays wrong until eviction.
- **Fix:** gate candidates through `_is_specific_match` / `_fs_relevant_candidates`, raise `max_results`, and only `_cache_food_id` on a relevance-verified match.

### N4 MED — `_food_search_llm` has no macro floor / null-name guard
- **File:** `app/services/ai_nutrition.py:327-344`. Unlike its sibling `_estimate_macros_llm_batch` (~811, which filters `calories > 0`), this coach fallback applies no `>0` floor, no `isinstance(item, dict)` check, and `name = item.get("name", q)` yields `None` on a JSON-null. A 0-kcal LLM row → staged via `PendingAction` → confirmed → written to `MealLog` (clamp only bounds the *upper* side). `None` name also becomes a cache key.
- **Fix:** mirror the batch path — skip `calories <= 0`, require `isinstance(item, dict)`, `name = item.get("name") or q`.

### N5 MED — boto3 S3 & Cognito clients have no explicit connect/read timeout
- **File:** `s3_helper.py:84`, `app/services/cognito_idp.py:62-66`. Every `requests`/OpenAI/Bedrock call sets explicit timeouts; the boto3 clients rely on botocore defaults (~60s+retries → minutes). S3 sits on `/workout/complete` + meal-photo paths; `cognito-idp initiate_auth` sits on the **login** path (already `LOGIN_FAIL_CLOSED`, so a hang compounds thread pressure).
- **Fix:** `config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2})` on both clients.

### N6 MED — MCP `log_nutrition_entry` writes to `MealLog` bypassing `clamp_serving_macros`
- **File:** `fitx_mcp/server.py:658-677`. Only checks negatives, then inserts raw LLM macros. The clamp docstring says every ingest path MUST call it; the DB CHECK only catches >100000 kcal, "not 3000-kcal garbage." The in-app coach path (`ai_coach.py:506`) clamps; this standalone MCP write path does not. (MED: reachable only via stdio/in-process MCP transport.)
- **Fix:** route values through `clamp_serving_macros` before INSERT.

### New LOW / cleanup
- **fatsecret null-`foods` → 500:** `app/services/fatsecret.py:271` `data.get("foods", {}).get("food", [])` sits *outside* the try; a present JSON-null returns `None` → `AttributeError` → 500 instead of empty. Same pattern at `food.py:111`. Fix: `foods = (data.get("foods") or {}).get("food", [])`.
- **Bedrock text without `or ""`:** `app/services/ai.py:97,134` return `block.text` raw (vs `_openai_chat` `or ""` at :55) → a `None` block can surface as a blank "successful" coach reply. Fix: `return block.text or ""`.
- **Empty Bedrock reply skips OpenAI fallback:** `app/services/ai.py:145-161` — fallback only on exception; an empty `""` is returned as valid. Fix: treat falsy/blank output as failure and fall through.
- **Staged vs committed coach macros differ:** `app/services/ai_coach.py:435-462` scores from *unclamped* macros; clamp applied only at commit (:506-511). Coach may say "9999 kcal, score 5" but persist ~1200. Stored value safe; user-facing inconsistency. Fix: clamp in the staging tool.
- **CLI `seed-quests` seeds 4 of 7 types:** `app/cli.py:13-18` omits `water_logged`, `checkin_done`, `supplement_added`. Not live-breaking (boot seeder covers them), but a CLI-only path (`FITX_SKIP_DB_INIT=1`) leaves them unearnable. Fix: align with `db_init`.
- **A1 gate coverage test / heavy-AI-route registry** (see N2) — prevents future regressions.
- **`workout/status` vs `workout/complete` idempotency sources differ:** `training.py:266-271` reads `UserQuestProgress`, `complete_workout` (:135-142) gates on today's `PumpCheck`; can diverge if the quest row is unseeded.
- **boto3 clients / migration CI guard (N5, A-arch):** add a CI check failing on `op.drop_column`/`op.drop_table`/rename in new migrations unless an override label is present (A2 rollback can't undo migrations); keep an RDS auto-snapshot pre-deploy.
- **`maybe_weekly_rollover` swallows all exceptions** (`app/hooks.py:199-209`) — emit to Sentry so a persistently-failing rollover isn't buried in WARN logs.
- **`/health` reports `ok`** even with Bedrock misconfigured or login 503'ing on degraded Redis — add non-gating advisory fields; keep DB as the only 503 trigger.

### New security (all latent / low)
- **S-new-1 MED (latent):** MCP tools take `user_id` with no authorization (`fitx_mcp/server.py:130-910`). Gated by default (`FITX_MCP_ALLOW_HTTP=1`, bound `127.0.0.1:8100`) — but add an auth layer (shared secret/bearer) before the loopback bind so an accidental proxy exposure isn't immediately cross-tenant. (Same surface as Run-1 item 2.1.)
- **S-new-2 LOW:** pump-check card always returns `sharedFriendIds` (`app/services/pump_checks.py:106-119`, consumed `social.py:430`) — a `friends`-visibility check delivered via chat leaks the poster's recipient list. Fix: include only when `viewer_id == check.user_id`.
- **S-new-3 LOW:** Cognito ID token not signature-verified (`app/services/cognito_idp.py:183-198`) — safe today (token from Cognito over TLS), but a defense-in-depth gap. Verify against pool JWKS and assert `iss`/`aud`/`token_use`.
- **S-new-4 LOW:** `FATSECRET_ALLOW_INSECURE=1` (`app/config.py:106-112`) can send the OAuth bearer over plaintext HTTP to a non-loopback host — operator foot-gun. Restrict to private/loopback or refuse outside dev.

### Verified sound this run (don't re-investigate)
Day-key discipline (all keys via `app/timeutil`; `MealLog.tarih` ISO `YYYY-MM-DD`); no live
`UserDailyNutrition` refs; concurrency counters (`update_streak`, `award_xp`,
`_record_counter`) use `with_for_update()`; like/comment counters atomic; weekly rollover
Istanbul-boundary + `WeeklyResetLog` UNIQUE; all `requests`/OpenAI/Bedrock calls have explicit
timeouts (boto3 the sole gap, N5); secrets never logged (IAM instance profile, env-only keys);
IDOR/ownership + two-layer CSRF + per-request-nonce CSP + menu-fetch SSRF guards all solid;
broad test suite (~60 files) — the visible gap is no test pinning the concurrency gate (N1/N2).

---

# Appendix — Run 1 (historical)

_Preserved from the initial triage. Items marked ✅ were fixed in this branch; the report
body is kept for context and for the deferred/decision items still relevant._

## Status — resolved in Run 1

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

## Run 1 items still open / worth tracking
- **1.4 LOW/PRODUCT** — `quick_add_meal` has no per-slot/day idempotency (`app/blueprints/nutrition/diary.py:54-131`); tapping "add breakfast from plan" twice double-counts. Needs a product decision on `(user_id, tarih, meal_key)` uniqueness.
- **1.6 INFO** — activity-calorie math duplicated between PL/pgSQL trigger (`app/db_init.py:62-73`) and `app/services/calculations.py:39-57`; add a test pinning them together.
- **2.5 LOW** — Google Drive download size caps generous (`app/services/menu_fetch.py:353` 50 MB); pass `max_bytes` into `_safe_requests_get` and lower.
- **3.3 MED** — deploy CSP cleanup via fragile `sed` (`.github/workflows/deploy.yml`); render nginx config from repo or fail on stray header.
- **3.4 MED** — HSTS/TLS depends on certbot having mutated nginx config; commit an explicit 443 block + 80→443 redirect or document the dependency.
- **3.10 INFO** — single-worker coupling (in-memory foodcache + limiter fallback + ai_gate semaphore + boot seeder); guard/doc before raising `--workers`.

_(Full Run-1 detail — executive summary, per-finding writeups for sections 1–3, and the
"verified strong/safe" notes — is retained in git history for commit prior to this rewrite.)_
