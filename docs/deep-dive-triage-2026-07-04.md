# FitX — Deep-Dive Triage & Needed Fixes (2026-07-04)

_Generated from a fresh 3-agent deep dive (Security · Correctness/Bugs · Architecture/Structure)
of the `fitness-coach` codebase. **Read-only triage — no code was changed.** This document is the
action list._

> **Durum güncellemesi (aynı gün):** Bu listedeki maddelerin tamamı ele alındı.
> Kod düzeltmeleri: C1 (kilit altında kolon-okuma, 3 nokta + eşzamanlılık testleri),
> C2 (clamp), C3 (dile göre yedek + geçmiş bastırma), C4 (kısıt `(user_id, date_key)`
> + migration `c5d6e7f8a9b0`), C5 (override görev ödülü), S1 (`is_global` + deny-list),
> S2 (arkadaşlık yeniden-doğrulama), A1 (`app/services/ai_gate.py` semaforu),
> A3 (`pool_pre_ping`/`pool_recycle`), A4 (boot migration hatası artık FATAL),
> A6 (prod'da error-level misconfig logları). Süreç/politika: A2 ve A5 CLAUDE.md'de
> belgelendi (expand/contract migration kuralı; Redis==login availability tradeoff'u).

This run re-assessed the **current** state of the branch and cross-checked the earlier triage
docs (`NEEDED_FIXES.md`, `TRIAGE_FINDINGS.md`, `docs/triage-2026-07-02.md`). Several previously
tracked HIGH items are now confirmed **fixed** (see "Delta vs. prior triage"). The headline of
this run is one **new HIGH concurrency bug** that the prior triage missed because the row locks it
recommended were added — but are ineffective.

---

## TL;DR — priority order

| # | Severity | Track | Issue | Location |
|---|----------|-------|-------|----------|
| **C1** | **HIGH** | Correctness | `with_for_update()` locks are defeated by SQLAlchemy identity-map staleness → lost updates still occur for XP / streak / weekly quota | `gamification.py:82`, `hooks.py:223`, `premium.py:75` |
| **A1** | **HIGH** | Architecture | Single worker + 8 threads: synchronous blocking AI calls can stall the whole app incl. `/health` | `Dockerfile`, `ai_coach.py`, `coach.py` |
| **A2** | **HIGH** | Architecture | Deploy rollback reverts code but **not** the boot-auto-applied DB migrations → rollback can cause an outage | `deploy.yml:32`, `db_init.py:33` |
| **A3** | **MED→HIGH** | Architecture | No `pool_pre_ping`/`pool_recycle` against external RDS → stale-connection 500s after idle/failover | `app/config.py` |
| **S1** | **LOW** | Security | SSRF: CGNAT / shared-address space `100.64.0.0/10` passes the deny-list guard (runtime-confirmed on Py 3.11.15) | `menu_fetch.py:10` |
| **C2** | **LOW** | Correctness | Friend-suggestion meal ingest bypasses the `nutrition_pipeline` clamp gate | `social.py:629` |
| **C3** | **LOW** | Correctness | Coach Bedrock loop returns hard-coded Turkish error to EN users + pollutes history | `ai_coach.py:1320` |
| **C4** | **LOW** | Correctness | `log_daily_activity` can persist two rows/day (constraint includes `intensity`) | `tracking.py:313`, `models.py:476` |
| **C5** | **LOW** | Correctness | Manual-override meal log doesn't grant `meal_logged` quest (inconsistent with AI path) | `meallog.py:79` |
| **S2** | **LOW** | Security | Retained pump-check visibility after unfriend (stale authorization) | `pump_checks.py` |
| **A4** | **MED** | Architecture | Boot does heavy synchronous work; failed migration is swallowed (silent schema drift) | `db_init.py` |
| **A5** | **MED** | Architecture | Redis is a single-container SPOF for **login** (`LOGIN_FAIL_CLOSED=1`) | `docker-compose.yml`, `auth.py:200` |
| **A6** | **MED** | Architecture | Config/feature-flag misconfig risks (`BEDROCK_ENABLED`, `FATSECRET_BASE_URL`, `FITX_SKIP_DB_INIT`) fail silently | `app/config.py` |
| — | info | Security/Correctness | AI prompt-injection surface (contained by design); account-scoped login lockout (accepted) | — |

**Overall:** the codebase remains unusually well-hardened. The security sweep found **no Critical
or High vulnerabilities** — layered SSRF defenses, parameterized SQL throughout, two-layer CSRF,
per-request CSP nonces, ownership-scoped queries, IAM-based secrets, and the in-process MCP authz
gate all verified sound. Residual risk is concentrated in **one concurrency correctness bug (C1)**
and **availability/scale under the single-worker deploy model (A1–A3)**.

---

## Delta vs. prior triage (what's already fixed)

- ✅ **S1 (auth bypass, Cognito challenge/empty-claims)** from `TRIAGE_FINDINGS.md` — the Cognito
  identity-integrity re-check is now present (`auth.py:235`); the auth surface re-swept clean this
  run (session-fixation reset, `session_protection="strong"`, constant-time dummy hash,
  `LOGIN_FAIL_CLOSED` 503-on-Redis-down).
- ✅ **D3 (`/health` returns 200 when DB down)** — now returns **503** on DB failure, so the deploy
  health gate can actually roll back.
- ✅ Numerous items from `NEEDED_FIXES.md` §"resolved in this branch" (dead quests, dropped
  supplement activity, duplicate weekly nudge, OCR decompression-bomb guard, Docker digest pin,
  prod fail-fast on missing `DATABASE_URL`, `_food_id_cache` locking).

The items below are **still open** (or newly discovered).

---

## Track 1 — Correctness / Bugs

### C1 — HIGH · Row locks don't prevent lost updates (identity-map staleness) — XP, streak, quota

**Files:** `app/services/gamification.py:82` (`award_xp`), `app/hooks.py:223` (`update_streak`),
`app/services/premium.py:75` (`_record_counter`)

All three functions serialize concurrent same-user writes with a row lock and a re-read:

```python
user = db.session.query(User).filter_by(id=user_id).with_for_update().first()
old = user.rank_points or 0
user.rank_points = old + amount   # read-modify-write, absolute assignment
```

The comments claim this fixes "kayıp güncelleme" (lost updates). **It does not.** SQLAlchemy's
identity map: when a query returns a row whose PK is already present in the session with
**non-expired** attributes, the ORM returns the cached instance and **discards the freshly-SELECTed
column values** (refresh requires `.populate_existing()` / `db.session.refresh(...)`). `current_user`
is loaded at request start (`load_user` → `db.session.get(User, id)`, `models.py:16`) and, in
`update_streak` (a `before_request` hook running before any commit that would expire it), its
attributes are non-expired. So `FOR UPDATE` serializes the transactions at the DB level, but each
transaction re-uses its **stale in-memory value** for the read-modify-write → concurrent increment
silently overwritten.

**Repro (streak double-count):** On the first page load of the day the browser fires several
parallel requests (index HTML + `/api/...` fetches). All pass the `last_login == today` fast-path as
False. Thread A takes the lock, sets `streak_count += 1`, `last_login = today`, commits, releases.
Thread B unblocks on its `FOR UPDATE` SELECT (DB row is now `today`) but keeps its cached
`last_login = yesterday`, so the guard at `hooks.py:224` is False → B increments the streak **again**
and can re-award milestone XP (`hooks.py:238-241`). Same mechanism gives duplicate XP in `award_xp`
(e.g. two concurrent `friend_accept`, `social.py:353`) and lets a free user generate 2 AI plans/week
in `premium._record_counter` (two concurrent `/nutrition-plan` both read `used=0`).

**Fix:** force a fresh read under the lock or make the write atomic —
- `...with_for_update().populate_existing().first()`, or `db.session.refresh(user, with_for_update=True)`, **or**
- atomic UPDATE: `db.session.query(User).filter_by(id=user_id).update({User.rank_points: User.rank_points + amount})` then re-read.
- For `update_streak`, do the guard as a conditional UPDATE: `filter_by(id=..., last_login=prev)` and act only if a row was updated.

> This supersedes the prior triage's D-M1/D-M2 ("no row lock"): the lock was added, but is
> ineffective. Add a concurrency test that asserts a single increment under parallel same-user calls.

### C2 — LOW · Friend-suggestion meal ingest bypasses the clamp gate

**File:** `app/blueprints/social.py:629-638` (`_process_meal_suggestion_accept`)

Every other write into the canonical `MealLog` ledger passes `nutrition_pipeline.clamp_serving_macros`
(`meallog.py:86`, `diary.py:_clamp_item_macros`, `ai_coach.py:477`). This path writes FatSecret/LLM
per-serving sums straight to `MealLog` with **no clamp**. A pathological outlier (or a suggestion
scaled by the large fallback serving weight at `social.py:587-592`) persists an implausible macro row
that skews daily totals, the protein nudge, and weekly reports. **Fix:** run the summed macros
through `clamp_serving_macros` before constructing the `MealLog`.

### C3 — LOW · Coach Bedrock loop: hard-coded Turkish error + history pollution

**File:** `app/services/ai_coach.py:1320,1337,1340`

`_run_coach_conversation_bedrock` returns the literal `"İşlemi tamamlayamadım, tekrar dener misin?"`
on a post-tool error/empty reply regardless of `language` (everywhere else localizes via
`_COACH_FALLBACKS[_coach_lang(language)]`). An English user gets a Turkish message. Worse, because
the string is non-empty, `is_error_fallback = not final_text` (`ai_coach.py:1172`) is False, so in
cookie-history mode the Turkish error text is written into `session["coach_history"]` and fed back as
context next turn. **Fix:** return `_COACH_FALLBACKS[_coach_lang(language)]["tool"]` (or an empty
string so the router applies the localized fallback and suppresses history persistence).

### C4 — LOW · `log_daily_activity` can persist two rows per day

**Files:** `app/blueprints/tracking.py:313-338`; constraint `uq_daily_activity(user_id, date_key, intensity)` at `app/models.py:476`

The handler deletes today's rows then inserts one, relying on the `IntegrityError` branch for
concurrency. But the unique constraint includes `intensity`, so two concurrent requests with
**different** intensities each delete-then-insert without violating it → **two** rows survive,
contradicting the "günde tam bir satır" invariant. Display is still correct (`today_activity` picks
newest by `id.desc()`), so it's data-model corruption, not a crash. **Fix:** make the constraint
`(user_id, date_key)` so the upsert path is actually exercised, or do a proper `date_key`-scoped
upsert.

### C5 — LOW · Manual-override meal log doesn't grant the `meal_logged` quest

**File:** `app/blueprints/nutrition/meallog.py:79-105` vs. `:200`

The `override_macros` branch returns at line 105 without `complete_quest_for_user(current_user.id, "meal_logged")`,
while the AI-computed branch awards it at line 200. Logging the same meal with user-supplied macros
silently skips the daily quest/XP. **Fix:** award the quest in the override branch too.

---

## Track 2 — Security

> Full route-by-route sweep found **no missing ownership checks / IDOR**, no raw SQL with user
> input, no hardcoded secrets, no unsafe deserialization, and confirmed the layered SSRF defenses,
> two-layer CSRF, per-request CSP nonce, session hardening, S3 ownership-by-segment, and the
> in-process MCP `user_id`-injection gate are all sound. Only the items below remain.

### S1 — LOW · SSRF: CGNAT / shared-address space not blocked

**File:** `app/services/menu_fetch.py:10-20` (`_is_safe_public_ip`)

```python
return not (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
```

The guard is a deny-list rather than a positive "globally routable" assertion. **Runtime-confirmed**
on the deployment's Python 3.11.15: `ipaddress.ip_address('100.64.1.1').is_private` is `False` and
`is_global` is `False`, so `100.64.0.0/10` (RFC 6598 carrier-grade NAT / shared address space)
**passes** this check. `is_private` only absorbed that range in Python 3.13.

**Scenario:** a menu URL to `POST /api/proxy/scan-menu` resolving into `100.64.0.0/10` could steer
the server-side fetch at an internal host in that space (some AWS topologies carry internal traffic
there). Blast radius is narrow on the single-EC2 layout (IMDS `169.254.169.254` is already blocked as
link-local; RDS/Redis are RFC1918/loopback), hence Low.

**Fix (one line):** replace the deny-list with a positive global check (also closes TEST-NET,
benchmarking `198.18/15`, `192.0.0.0/24`, etc.), applied after the existing IPv4-mapped-IPv6 unwrap:

```python
return ip.is_global
```

### S2 — LOW · Retained pump-check visibility after unfriend

**File:** `app/services/pump_checks.py` (`can_view_pump_check`, "friends" branch)

For posts shared to specific friends, visibility trusts the stored `shared_friend_ids` without
re-confirming the viewer is still an accepted friend. If the friendship is later removed, that friend
keeps view access to the one historical post. Not an IDOR (access limited to owner-selected,
share-time-validated IDs) — stale authorization. **Fix:** re-check current friendship in the
"friends" branch.

### Informational (monitor / accepted by design)
- **AI prompt injection** into the tool-calling coach: untrusted menu/FatSecret/chat text enters an
  LLM context that can invoke *write* tools. Contained: `user_id` is never LLM-supplied
  (`_assert_principal`, `ai_coach.py:121`), commits require a second user turn, memory keys are
  whitelisted. Worst case is a bogus staged meal/workout **for the same user**. Keep the "treat menu
  text as data only" system-prompt guidance; never let tools accept an LLM-supplied owner id.
- **Account-scoped login lockout** (`auth.py:66-79`): a short, auto-healing self-lockout is an
  accepted tradeoff of any per-username throttle (already documented in code).

---

## Track 3 — Architecture / Reliability

### A1 — HIGH · Single worker + 8 threads: blocking AI calls can stall the whole app (incl. `/health`)

**Area:** `Dockerfile` (gunicorn `--workers 1 --threads 8 --timeout 300`), `app/blueprints/coach.py`, `app/services/ai_coach.py`

`/ask` runs `_fetch_coach_context` (5 sequential MCP/psycopg2 round-trips) then a tool loop of up to
`_COACH_TOOL_LOOP_CAP = 5` LLM calls (60s Bedrock / 30s OpenAI each) under a 300s thread timeout —
in one process with 8 threads. `/chat`, menu scan, and image validation are similarly synchronous.

**Scenario:** 8 concurrent AI requests (or a few slow Bedrock calls) park all 8 threads in blocking
I/O → every other request queues, **including the Docker `HEALTHCHECK` and the deploy `/health`
gate** → container marked unhealthy / deploy gate fails → restart or rollback churn caused by load,
not a bug. Single largest availability risk.

**Fix:** move AI endpoints to a separate worker pool / task queue, or (minimum) cap concurrent AI
requests with a semaphore so a reserve of threads always serves `/health` and cheap routes. Do not
raise `--workers` until the in-memory cache/limiter single-worker assumptions are removed.

### A2 — HIGH · Deploy rollback reverts code but not the boot-auto-applied migrations

**Area:** `.github/workflows/deploy.yml:32`, `app/db_init.py:33-40`

On boot `db_init` runs `flask db upgrade` automatically (`FITX_DB_AUTO_UPGRADE=1` default). The
rollback path does `git reset --hard $PREV_COMMIT` + `compose up` — **code only**. A failed deploy's
container has already migrated the DB forward (the chain already contains a destructive
`f6a7b8c9d0e1_drop_user_daily_nutrition`, plus NOT-NULL/rename ops).

**Scenario:** a release ships a destructive migration; the new container applies it, then fails the
health gate for an unrelated reason → rollback restores old code that now runs against a schema which
dropped/renamed columns it expects → persistent 500s the health gate can't heal (schema is ahead).
Rollback creates an outage instead of resolving one.

**Fix:** make migrations backward-compatible (expand/contract), or split schema migration into a
separate one-shot deploy step (`FITX_DB_AUTO_UPGRADE=0` already anticipates this) with an explicit
down-migration/restore plan, and snapshot RDS before destructive releases.

### A3 — MED→HIGH · No SQLAlchemy connection resiliency against external RDS

**Area:** `app/config.py` (no `SQLALCHEMY_ENGINE_OPTIONS` anywhere — confirmed by grep)

DB is now external RDS over the network, but the engine uses stock pool defaults: no `pool_pre_ping`,
no `pool_recycle`. A single-worker app that idles (overnight/low traffic) holds pooled connections
that RDS/NAT idle-timeout or an RDS failover silently closes.

**Scenario:** first requests after idle, or after an RDS failover, hit a dead pooled connection →
`OperationalError: server closed the connection unexpectedly` / 500s until the pool cycles. Also
affects `/health`'s `SELECT 1`, which could flap the deploy gate.

**Fix (cheap, high-value):**
```python
SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}  # recycle < RDS idle timeout
```

### A4 — MED · Boot does heavy synchronous work; failed migration is swallowed

**Area:** `app/db_init.py`

Every boot runs, before serving traffic: pending Alembic upgrade → `create_all` → CREATE OR REPLACE
trigger → quest seeding → referral backfill → `lb_rebuild()` (rehydrates Redis from Postgres). Two
concerns: (a) if the Alembic upgrade raises, it's caught, logged, and **boot continues** → the app
serves requests with a schema behind the code (silent drift until a 500); (b) all of it lengthens
cold start and the deploy health window. The whole design is pinned to the single-worker/single-
container invariant — correct today, but concurrent boot upgrades would race the moment anyone scales
horizontally against the same RDS. **Fix:** treat a failed boot migration as fatal (fail fast so the
health gate rolls back); move migration + `lb_rebuild` out of the request-serving boot path.

### A5 — MED · Redis is a single-container SPOF for login

**Area:** `docker-compose.yml`, `app/blueprints/auth.py:200`, `app/extensions.py`

Good news: sessions are cookie-based, leaderboard falls back to Postgres, foodcache falls back to
L1/in-memory, limiter has `in_memory_fallback_enabled=True` — so **Redis down does not take the app
down, except login**: `LOGIN_FAIL_CLOSED=1` (default) makes all logins 503 while Redis is
unreachable. Redis is a single `redis:alpine` on the same host with `allkeys-lru` + 200mb cap; a
crash/OOM = login outage, and memory pressure can silently evict brute-force counters. Deliberate
availability/security tradeoff worth stating: **Redis availability == login availability.**
**Fix:** accept + document + monitor Redis liveness explicitly (already surfaced via `/health`
`limiter_storage`), or add a short-lived degraded-but-throttled login mode.

### A6 — MED · Feature-flag / config misconfiguration fails silently

**Area:** `app/config.py`, `app/db_init.py`

- `BEDROCK_ENABLED` defaults to `"0"` — a prod `.env` omitting it silently degrades the "primary
  heavy AI path" to OpenAI `gpt-4o-mini` for coach/menu/plan with **no error** (looks healthy).
- `FATSECRET_BASE_URL` unset → only a `logger.warning`; nutrition lookups silently fail.
- `FITX_SKIP_DB_INIT=1` left in prod → fresh DB never gets `create_all`/seed.
- `LOGIN_FAIL_CLOSED` (default on) compounds A5.

**Fix:** for prod, fail-fast or emit error-level alerts when `BEDROCK_ENABLED` / `FATSECRET_BASE_URL`
are unset, and assert `FITX_SKIP_DB_INIT` is never set outside CI/migration contexts.

### Lower-severity architecture notes
- **Observability gap:** structured request logs + optional Sentry are solid, but there's no metric
  for thread-pool saturation / AI latency-cost / queue depth — i.e. **A1 has no direct signal**. Add
  per-provider AI latency/error counters and a thread-busy gauge (`/metrics` or on `/health`).
- **`ai_coach.py` (1540 lines)** is a god-module with **two near-duplicate** conversation loops
  (`_run_coach_conversation_openai` / `_run_coach_conversation_bedrock`) kept in lockstep by hand
  (root cause of C3). Split into prompt / tools / a single provider-abstracted runtime loop.
- **Coach context opens 5 unpooled raw psycopg2 connections per request** (`ai_coach.py:216` →
  `fitx_mcp/server.py:get_conn`), outside the SQLAlchemy pool, plus a duplicated FatSecret
  token-cache/parser in both `fitx_mcp/server.py` and `app/services/fatsecret.py` (drift risk).
  Reuse a pooled connection / read via the ORM in-process; consolidate the duplicated FatSecret code.
- **Test coverage** is broad functionally, but the reliability edges above (thread exhaustion A1,
  stale-pool A3, migrate-forward-then-rollback A2, the two coach loops' fallback divergence) are
  untested. A concurrency test for C1 and a stale-connection test for A3 would lock in the fixes.
- **`nginx.conf`:** `proxy_buffering off` + `Connection: upgrade` on `location /` though the app
  doesn't stream — harmless but misleading, and mildly compounds A1 by holding upstream threads.

---

## Suggested sequencing

1. **C1** (data-integrity bug in core accounting, routine trigger) — `.populate_existing()` / atomic
   UPDATE + a concurrency test. Cheap, high-value.
2. **A3** (`pool_pre_ping`/`pool_recycle`) — one-config change, removes a class of stale-connection
   500s.
3. **A2** (decouple destructive migrations from rollback) — before the next schema-changing release.
4. **A1** (semaphore/thread-reserve for AI endpoints, or a metric to at least see it) — the biggest
   availability risk; a semaphore is a small first step.
5. **S1** (`ip.is_global`) + **C2–C5, S2, A4–A6** — batch the small, well-scoped fixes.
