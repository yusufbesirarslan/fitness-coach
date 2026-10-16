# FitX — Needed Fixes (Triage Report)

**Date:** 2026-10-16
**Scope:** 3 parallel read-only deep-dive audits — (1) Security vulnerabilities, (2) Correctness & concurrency bugs, (3) Structure / reliability / operations.
**Method:** Adversarial trace of the attack surfaces and high-risk flows against live source. Every claim is cited `file:line` and was verified against the code, not against `CLAUDE.md`. The two highest-value items were re-verified by hand after the agents reported.
**Commit reviewed:** `36d833b` on `claude/amazing-dijkstra-drei9a` (HEAD; even with `origin/main`).
**Baseline:** Prior reports (`NEEDED_FIXES.md`, `NEEDED_FIXES_2026-08-02.md`, `NEEDED_FIXES_2026-08-14.md`, `TRIAGE_FINDINGS_2026-10-01.md`, `SEC00x`) were read first; their items were spot-checked as resolved at HEAD (e.g. `ProxyFix` `x_host=0,x_port=0` at `app/config.py:311`; `food_search` now behind `blocking_concurrency_slot()`; the `created_at DESC, id DESC` tiebreak applied across the `UserSession`/`get_active_plan` selector family). This report lists only **new / still-open** findings.

## Headline

The codebase remains **mature and defense-in-depth**. All three auditors independently reproduced the standing conclusion: authentication/authorization, ownership scoping, SQL parameterization, SSRF defenses, secret/crypto handling, CSRF/CSP, S3 access control, the AI/scrape concurrency gate, and the transaction/idempotency surfaces are implemented to a high standard. The recent privacy work (PA06 coach-context #438, PRIV-LOG #433/#435) is sound.

**No Critical issues. One genuinely new High-severity reliability gap** (untimed Redis client turns a *degraded* Redis into a full app + health-probe outage), one Medium (the same gap on the background Redis connection), one Medium-Low concurrency bug (hydration lock-order inversion → PostgreSQL deadlock), and a handful of Low / informational hardening notes.

| # | Severity | Area | Type | Confidence |
|---|----------|------|------|------------|
| 1 | **High** | Shared Redis client has no socket timeouts → thread exhaustion + `/health`/login hang under a *partitioned* Redis | Reliability | Confirmed (verified by hand) |
| 2 | Medium | RQ/background Redis connection has the same untimed gap | Reliability | Confirmed |
| 3 | Medium-Low | Hydration write locks `User`→`WaterLog`; award path locks `WaterLog`→`User` → cross-request PG deadlock (spurious 503) | Correctness / concurrency | Confirmed (verified by hand) |
| 4 | Low | Cold-start JWKS fetch not single-flighted (boot-window thread parking) | Security / reliability | Confirmed |
| 5 | Low | Spurious-503 vs graceful-degrade asymmetry in the native hydration path | Correctness | Confirmed |
| 6 | Low | `ai_coach.py` latest-`PendingAction` selectors miss the `id` DESC tiebreak | Correctness | Confirmed |
| 7 | Info | `nutrition_native/plan.py` `_native_number` does not reject NaN/±Inf | Hardening | Confirmed |
| 8 | Info | Dead/redundant body-size caps in the Google-Drive menu path | Cleanup | Confirmed |

---

## 1. [HIGH] Shared Redis client has no socket timeouts → request-thread exhaustion under a *degraded* (not cleanly-down) Redis

**Where:** `app/extensions.py:55`
```python
redis_client = redis.from_url(_REDIS_URL, decode_responses=True) if _REDIS_URL else None
```
No `socket_timeout`, no `socket_connect_timeout`. The redis-py default for both is `None` = **block indefinitely**.

**Why it matters:** The whole degradation design (per `CLAUDE.md` and A5) assumes "Redis unavailable → `try/except` → graceful no-op / Postgres fallback." That contract holds for a **cleanly refused** connection (raises fast). It does **not** hold for the common *degraded* modes — ElastiCache/RDS failover, AOF-rewrite stall, memory-pressure/eviction storm, or a middlebox holding a half-open socket — where the TCP connection is established but no reply comes. Those operations block the request thread with **no wall-clock backstop**: gunicorn's gthread worker does not reap an individual hung thread, and `timeout=300` does not interrupt it. Redis is touched on hot paths (leaderboard reads `gamification.py:428-450`, `ai_cache`, quest claims, the limiter), so with only 8 threads the pool exhausts within seconds and the app goes dark **even though Postgres is healthy**. The `try/except` guards everywhere catch exceptions, not hangs.

**Amplifier — the health probe hangs too:** the **shallow** `/health` (Docker HEALTHCHECK + deploy liveness probe) calls `ext.limiter_storage_status()` → `redis_client.ping()` on this same untimed client (`app/extensions.py:166`). A partitioned Redis therefore hangs the very probe meant to detect trouble → container flaps / restart-loops without fixing the partition. `login_throttle_available()` pings the same client (`app/extensions.py:205`); its 5s result cache does not protect the first call of each window, which blocks indefinitely — and with `LOGIN_FAIL_CLOSED=1` (default) that stalls login. (Note: a comment at `app/extensions.py:184` already acknowledges wanting to bound the login ping — the fix generalizes that intent to the client itself.)

**Fix (low-risk, completes the existing design):** pass `socket_timeout` and `socket_connect_timeout` (e.g. 2s) plus `health_check_interval` to `redis.from_url` at `app/extensions.py:55`. The resulting `TimeoutError`/`ConnectionError` is exactly what the existing fallbacks already handle, so the partition case degrades gracefully instead of hanging. Optionally add an explicit short timeout on the two health/throttle pings specifically.

---

## 2. [MEDIUM] RQ / background Redis connection has the same untimed gap

**Where:** `app/jobs/__init__.py:57`
```python
_bytes_redis = redis.from_url(_REDIS_URL)   # no timeouts
```

**Why it matters:** Lower blast radius (RQ enqueue + `worker_alive()` heartbeat), **but** `worker_alive()` is read by `/health?deep=1`, the deploy readiness gate. A partitioned Redis can hang the deep-health probe → a deploy stalls or rolls back for the wrong reason. Same fix: add `socket_timeout` / `socket_connect_timeout` to this `from_url`.

---

## 3. [MEDIUM-LOW] Hydration lock-order inversion → PostgreSQL deadlock on concurrent same-user water writes (native path)

**Files:**
- `app/services/hydration.py:44-46` — `set_today_count` locks **`User` row `FOR UPDATE` → then `WaterLog` row `FOR UPDATE`**.
- `app/services/hydration.py:69-98` — `claim_water_funnel_for_today` issues `UPDATE WaterLog … WHERE quest_fired = false` (takes the **`WaterLog`** row lock), held until commit.
- `app/services/gamification.py:167-168` — `award_xp` takes **`User` row `FOR UPDATE`**, reached via `award_water_logged → complete_quest_for_user → _claim_quest → award_xp`.
- `app/services/nutrition_native/hydration.py:81-82` — `set_today` calls `set_today_count(...)` (commits) **then** `award_water_logged(...)`.
- Route: `app/blueprints/mobile_nutrition_closure.py:205-213` (`PUT /api/v1/nutrition/hydration`).

**The inversion:** the native hydration write acquires locks `User → WaterLog` (Phase 1, `set_today_count`). The water-quest award acquires them in the **opposite** order `WaterLog → User` (Phase 2: the funnel `UPDATE` grabs the `WaterLog` row, then `award_xp` grabs the `User` row, both in one transaction). The two phases are sequential within one request (Phase 1 commits first), so the deadlock is **cross-request**.

**Failure scenario (same user, two concurrent devices/tabs, PostgreSQL):**
1. Request A finishes `set_today_count` (commits), enters `award_water_logged`: the funnel `UPDATE` takes the `WaterLog(user,day)` lock and is about to call `award_xp` (wants `User`).
2. Request B starts `set_today_count`: takes the `User` lock (`hydration.py:44`), about to take the `WaterLog` lock.
3. A holds `WaterLog`, waits for `User` (held by B); B holds `User`, waits for `WaterLog` (held by A) → **circular wait**. PostgreSQL aborts one transaction. The `set_today_count` victim surfaces a spurious **503 (`HYDRATION_*`)** with no retry; the award victim degrades gracefully.

The award path only holds `WaterLog` across `award_xp` on the **day's first positive water log** (when the funnel fires), so the window is the first positive log racing a second concurrent write — realistic for double-taps / two devices. Also reachable **cross-transport** (native write racing a browser award), since both award paths lock `WaterLog → User`.

**Why the browser path is immune:** `set_water` (`app/blueprints/training.py:951-969`) writes `WaterLog` **without** a `User` lock, relying on `uq_user_water_day` + `IntegrityError` for the create race. The inversion was introduced by NUTR-PR7's `set_today_count` adding the `User`-first lock.

**Impact:** not data corruption (absolute-count write + idempotent funnel + `uq_user_water_day`); an occasional spurious 503 on concurrent same-user hydration writes. **PostgreSQL only** — SQLite has no row locks, so the existing test suite cannot surface it, and there is no `test_*_pg.py` covering hydration.

**Suggested fix (don't apply blind):** make the hydration write agree with the canonical `WaterLog → User` order, or drop the `User` lock in `set_today_count` and handle the create race via `uq_user_water_day` + `IntegrityError` exactly as the browser `set_water` does (the unique index already prevents the double insert, so the `User` lock is belt-and-suspenders). Either removes the only `User`-before-`WaterLog` acquirer. Add a `tests/test_*_pg.py` race case.

---

## 4. [LOW] Cold-start JWKS fetch is not single-flighted

**Where:** `app/services/cognito_jwt.py:59-72` (`_load_jwks`), `:125-133` (`_select_key`).

Hardening PR4 correctly put the *forced* refresh (unknown-`kid` flood) behind `_refresh_lock` + cooldown. But the normal path `_select_key → _key_by_kid(_load_jwks(), kid)` calls `_load_jwks()` (no `force`) with **no lock**. On a cold cache (process boot, before the first token is validated), N concurrent requests each see `None` and each issue the 5s blocking `urllib.request.urlopen`, parking up to N web threads — the same thread-exhaustion shape PR4 closed, but at boot.

**Why only Low:** not attacker-triggerable after boot (the cache is warm permanently once one load succeeds; it is only replaced via the single-flighted forced path). Bounded to the boot window under immediate load. **Fix:** fold the initial population under the same `_refresh_lock` with a double-check inside the lock.

---

## 5. [LOW] Spurious-503 vs graceful-degrade asymmetry in the native hydration path

`complete_quest_for_user` (`app/services/gamification.py:368-384`) swallows commit failures (rollback → `None`), but `set_today_count` has no equivalent guard, so an aborted Phase-1 is surfaced to the user as a 503 while an aborted Phase-2 is hidden. The stated design is "water count is authoritative; quest is best-effort" — resolving finding #3 in the direction that keeps the count write robust unifies this too.

---

## 6. [LOW] `ai_coach.py` latest-`PendingAction` selectors miss the `id` tiebreak

**Where:** `app/services/ai_coach.py:293,422` — `order_by(created_at.desc()).first()` with no `id` DESC tiebreak. This is the one "latest single row" selector family that escaped the 2026-10-01 `created_at DESC, id DESC` unification. Low blast radius (per-user staged coach meal/action; same-microsecond ties improbable), but it should carry the same tiebreak for consistency and determinism.

---

## 7. [INFO] `_native_number` does not reject NaN / ±Inf

**Where:** `app/services/nutrition_native/plan.py:218-223`. Unlike `coach_privacy.number`, `_native_number` does not reject NaN/±Inf. Not exploitable today (the save path is gated by `document_digest` + proposal-token match, so a tampered NaN document fails `verify_proposal_token`; the generate path validates server-produced finite documents). Harmless, but relies on an invariant two layers away — a defensive `math.isfinite` check would localize it.

---

## 8. [INFO] Dead/redundant body-size caps in the Google-Drive menu path

**Where:** `app/services/menu_fetch.py:145-166` (`_process_google_drive_url`). The `Content-Length` check and the `resp.iter_content`/`resp.raw.read1` streaming cap run against a `requests.Response` that `menu_remote.fetch()` already fully consumed and byte-capped (it sets `_content`, not a live stream, and never sets `Content-Length`). So `declared_bytes` is always `None` and the re-cap iterates over already-bounded in-memory bytes. Not a vulnerability (`MENU_BODY_LIMIT` enforces the cap before these bytes exist) — just dead defensive code that could mislead a future reader. Cleanup only.

---

## Recommended order

1. **#1 Redis socket timeouts** (`app/extensions.py:55`) — the one change with real outage-prevention value; it closes the only hole where a *slow* dependency becomes a *total* outage plus a flapping health probe. Two-line, low-risk, completes the existing degrade-gracefully design.
2. **#2** the same change on `app/jobs/__init__.py:57`.
3. **#3** hydration lock-order fix + a PG race test.
4. **#4–#6** small hardening/consistency fixes.
5. **#7–#8** informational; fold in opportunistically.

## Surfaces verified sound (not findings)

AuthN/AuthZ (RS256-only JWT with `token_use`/issuer/audience pinned; offline per-request validation of the Fernet-stored provider token; ownership resolved from `g.mobile_user` only; opaque owner-bound HMAC tokens with constant-time compare); SSRF defense in `menu_remote.py` (pinned-literal-IP connect with `getpeername` verification, full public-IP gate, redirect re-validation, credential-free killable subprocess); all raw SQL parameterized and `user_id`-scoped; CSRF two-layer + fail-closed; CSP nonce-per-request + SRI-pinned scripts; S3 server-selected bucket, segment-equality key checks, `expected_user_id` on every presigned-URL/get caller; the provider-call boundary (`ai_provider_call.admit()` → `model_concurrency_slot` + spend guard, `max_retries=0`), the thread-reserve/DB-pool boot invariant, and the 90s coach turn deadline; migration fail-fast + expand/contract CI gate; PA06 coach-context privacy scoping.

---

*Generated by an automated 3-agent triage run (Daily issue triage routine). Read-only audit — no application code was modified; this report is the only artifact.*
