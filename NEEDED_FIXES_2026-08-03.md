# FitX — Needed Fixes (Triage Report)

**Date:** 2026-08-03
**Commit reviewed:** `8e0458a` on `claude/amazing-dijkstra-fiddgu` (== `main` HEAD; Hardening PR3 / auth-contract verification).
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Correctness bugs, (3) Reliability/Architecture. Focus on the four PRs landed **since** the prior triage (`NEEDED_FIXES_2026-08-02.md` @ `392c556`):

- **#195** observability / runtime SLIs (`app/observability.py`, `app/services/runtime_metrics.py`, `app/services/ai_gate.py`, deep-health gauges)
- **#196** feature-flag lifecycle registry (`app/feature_flags.py`, `app/config.py` `FEATURE_FLAG_KEYS`)
- **#197** rollout ordering + drift gate widening
- **#198** auth-contract verification (`app/services/auth_contract.py`, `app/services/cognito_jwt.py`, retired-setting enforcement)

**Method:** Adversarial trace of each new attack surface and high-risk flow. Every claim verified against actual code (cited `file:line`), not against CLAUDE.md/docstrings. Prior-report items were re-verified at HEAD; only still-open ones are carried forward.

## Headline

The four new PRs are **observability and configuration plumbing** — hardening, not business logic — and they are **well-constructed by design**: single-outcome-per-request metrics, `put_metric_data` kept strictly off the request path (buffer-under-lock + daemon flush), stdlib-only one-directional config registries, strict fail-closed parsing, JWT validation with `alg` pinning and correct exception ordering.

**No new Critical/High/Medium security or correctness issues were introduced by #195–#198.** All three audits independently reached this conclusion. The new work surfaced **4 Low(–Medium) observability/operational gaps**, and one prior open item was confirmed **fixed**. The three Medium reliability items from the prior report remain **open and untouched** by this batch — two of them are gated behind `MOBILE_AUTH_ENABLED` (default OFF), but **one (`ai_gate` slot timeout) is live and un-gated**.

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| N1 | Low–Medium | `ThreadReserve` documented as the mobile-auth rollout **abort signal** but never emitted as a gauge | Observability / operability | **New** | Confirmed |
| N2 | Low | Dependency/capacity gauges (`DbUp`/`RedisUp`/`LoginUp`/`DbPool*`) emitted **only** on `/health?deep=1`, which nothing polls on a schedule → alarms sit in `INSUFFICIENT_DATA` | Observability | **New** | Confirmed (in-repo) |
| N3 | Low | No final flush on shutdown/SIGTERM → up to `RUNTIME_METRICS_FLUSH_SECONDS` (default 60s) of buffered metrics lost on **every deploy** — often the pre-restart spike | Observability | **New** | Confirmed |
| N4 | Low | Strict flag parser + retired-key enforcement is boot-fatal → first deploy can **auto-rollback** on an `.env` value the old idiom tolerated (deploy-ordering hazard) | Ops / deploy coupling | **New** (flagged by 2 audits) | Confirmed (intended fail-loud) |
| P3 | **Medium** | `ai_gate._model_slots.acquire()` has **no timeout**; `food_search`/`respond_suggestion` remain ungated → thread-reserve invariant breachable | Reliability / DoS | **Prior #3 — still open, NOT flag-gated (live)** | Confirmed (3 audits) |
| P1 | Medium | `mobile_auth.refresh()` holds `SELECT … FOR UPDATE` across a blocking Cognito network call | Reliability / DoS | Prior #1 — still open (`MOBILE_AUTH_ENABLED` OFF) | Confirmed |
| P2 | Medium | Mobile auth endpoints make ungated blocking Cognito calls, unaccounted in thread reserve | Reliability / DoS | Prior #2 — still open (`MOBILE_AUTH_ENABLED` OFF) | Confirmed |
| L6 | Low | `plan_facts` is the only plan parser that rejects the `{"program": [...]}` wrapper | Correctness | Prior #6 — still open | Confirmed |
| L7 | Low | `/training/bootstrap` 500s on a valid plan whose `set`/`sure_dk` exceed serializer bounds | Correctness | Prior #7 — still open | Confirmed |
| L8 | Low | Completing today terminalizes a stale previous-day ACTIVE session as `COMPLETED` | Correctness | Prior #8 — still open (flag-off) | Confirmed |
| L9 | Low | OpenAI non-stream coach loop has no per-turn wall-clock budget | Reliability | Prior #9 — still open | Confirmed |
| P5 | Low | `ProxyFix` trusts client `X-Forwarded-Host` / `-Port` that nginx never sets | Security | Prior #5 — still open | Confirmed |
| S | Low | God-modules: `ai_coach.py` (1211), `social.py` (1033), `tracking.py` (728) | Structure | Prior #10 — unchanged | Confirmed |

> **Resolved since last report:** the long-standing `weekly_water` double-count (2026-07-21 report #2, left explicitly unverified by the 2026-08-02 report) is now **fixed** — see "Resolved" below. No action needed.

---

## New findings (PRs #195–#198)

### N1. [Low–Medium] The `ThreadReserve` guardrail for the highest-risk flag does not exist as a metric

**Files:** `app/feature_flags.py:429` (lifecycle record) + `docs/ROLLOUT.md:189` (abort-trigger table) vs. `app/services/ai_gate.py:147` (only place a reserve is computed).

The lifecycle record for `MOBILE_AUTH_ENABLED` — the one flag classified as an **attack-surface** change — names as its primary success/abort signal *"ThreadReserve gauge stays above its floor under mobile load"* (`feature_flags.py:429`), and `docs/ROLLOUT.md:189` tells the operator to abort the rollout when `ThreadReserve` is *"approaching its floor."*

**No `ThreadReserve` gauge is ever emitted.** Grep of every `set_gauge`/`increment` call site yields only `DbPool*`, `DbUp`, `RedisUp`, `LoginUp`, `LimiterDegraded`, `Http*`, `Ai*`, `GateRejections`. The only `reserve` computation is `ai_gate.py:147` (`reserve = WEB_THREADS − (AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY)`) — a **static boot-time invariant check** inside `enforce_gate_invariants`, never a runtime measurement of consumed threads.

**Why it matters:** This is exactly the guardrail meant to catch the still-open Mediums P1/P2 (blocking Cognito calls exhausting the 8 web threads from a pre-auth endpoint). The runbook instructs the operator to abort when the gauge approaches its floor, but no such gauge exists. Indirect coverage (`HttpOverload` 503 at `observability.py:90`, `GateRejections` at `ai_gate.py:171`) rises only *after* starvation, not as headroom shrinks.

**Fix (pick one):** Emit a real `ThreadReserve` gauge (`WEB_THREADS − active_ai_slots − active_scrape_slots`) from the flush thread or deep-health path; **or** correct the runbook/registry to name signals that actually exist (`HttpOverload`, `GateRejections`, `AiModelSlotContended`). Cheap and closes a documented-but-nonfunctional abort trigger.

### N2. [Low] Dependency/capacity gauges are emitted only by an endpoint nothing polls regularly

**Files:** `app/__init__.py:82-97` (`_record_capacity_gauges`), `:100-117` (`_record_dependency_gauges`), both called only inside `if request.args.get("deep") == "1" and _deep_health_allowed():` (`:215-216`); `Dockerfile:44` (shallow healthcheck).

`DbUp`/`RedisUp`/`LoginUp`/`LimiterDegraded` and `DbPoolCheckedOut`/`Overflow`/`Size` are set only on the deep-health path. But the Docker `HEALTHCHECK` polls **shallow** `/health` every 30s (never `deep=1`), and the deploy gate hits `deep=1` only a handful of times per deploy. In steady state — unless an out-of-band monitor polls deep-health on a schedule — these gauges produce **no datapoints**, so a CloudWatch alarm on them sits in `INSUFFICIENT_DATA` rather than firing on an actual Redis/DB/login outage. Gauges are drained each flush, so a value set once per deploy does not persist. (Only relevant when `RUNTIME_METRICS_ENABLED=1`.)

**Fix:** Emit dependency/capacity gauges from the daemon flush thread (already interval-driven), or document that a scheduled deep-health poll is a prerequisite for these alarms.

### N3. [Low] Buffered metrics for the final ~flush-interval are lost on every deploy/shutdown

**Files:** `app/services/runtime_metrics.py:102-116` (daemon `_flush_loop`, `daemon=True`, no exit hook); `gunicorn.conf.py:16` (`graceful_timeout=30`).

`_flush_loop` is a daemon thread that `sleep(FLUSH_SECONDS)` then flushes. There is **no** `atexit`, SIGTERM, or gunicorn `worker_exit` hook that calls `flush()` a final time (grep-confirmed: no signal/atexit registration anywhere in `app/`). On graceful shutdown (every deploy runs `docker compose up -d`), the daemon thread is killed mid-interval and the current buffer window (up to 60s by default) is discarded — and that window is exactly the pre-restart interval, often the error/latency spike that motivated the restart or triggered the rollback. Impact is monitoring-fidelity only; no request is affected.

**Fix:** Register an `atexit`/`worker_exit`/SIGTERM handler that calls `flush()` once. Bounded and cheap (client already has `connect_timeout=2, read_timeout=3, max_attempts=1`).

### N4. [Low] First deploy of the strict parser can auto-rollback on a value the old idiom tolerated

**Files:** `app/config.py:287-303` (`enforce_retired_settings` + `resolve_rollout_flags` in `configure_app`); `app/services/auth_contract.py:84-115`; `app/feature_flags.py:44-70`. *(Flagged independently by the correctness and reliability audits.)*

`configure_app` now boots **fatally** when (a) `MOBILE_AUTH_VALIDATION_CLOCK_SKEW_SECONDS` is present with any non-`0`/non-numeric value, or (b) any of the 7 registry rollout flags holds a value that isn't exactly `0`/`1`/empty/unset. The old idiom `os.getenv(X,"0") == "1"` silently read `true`/`yes`/`"1 "` as OFF.

This is deliberate fail-loud design, and the deploy health gate rolls back to the recovering prior commit. But it couples sharply with the documented contract that **the deploy pipeline never touches `.env`** (`git reset --hard origin/main`) and **rollback reverts only code**: a host that has long carried e.g. `WEEKLY_PROGRAM_UI_ENABLED=true` (tolerated as OFF by old code) will fail its **first** boot on the new parser → deploy gate fails → auto-rollback that looks "mysterious" (cause only in the container boot log). `#198` also requires an out-of-band `.env` edit (delete the retired skew line, `docs/ROLLOUT.md §1`) *before* the push-triggered deploy; if forgotten, the deploy auto-rolls-back.

**Fix:** Not a code bug — add to the release checklist: before deploying #196/#198, audit host `.env` for non-`0/1` rollout-flag values and the retired skew key, and normalize/delete them first. Optionally have `resolve_rollout_flags` log the offending key+value explicitly at boot (it may already) so the rollback cause is obvious.

### Two by-design observations (no action recommended)

- **Flush partial-batch failure** (`runtime_metrics.py:203-222`): `_drain()` atomically copies-and-clears all buffers, then sends ≤20-datum batches. If `put_metric_data` fails on batch *k*, batches *k+1…n* are lost (buffer already cleared) — a partial window plus a gap, slightly worse than the code comment's "one window lost." Correct bounded-memory tradeoff; no double-send. Not worth changing.
- **Latency percentiles snap to bucket ceilings** (`runtime_metrics.py:86-91,181-200`): samples are rounded **up** to the nearest bucket edge and anything > 300000 ms reports as exactly 300000 ms, so p50/p95/p99 are over-estimates bounded by bucket width. Standard histogram behavior; set SLO thresholds with the ceiling bias in mind.

---

## Resolved since the prior report

### `weekly_water` challenge double-count — FIXED

**File:** `app/blueprints/training.py:571-616`.

The 2026-07-21 report #2 flagged `weekly_water` counting every water POST as a "day"; the 2026-08-02 report left it explicitly unverified. It is now fixed: `set_water` captures `prev_count` (`:584`) and fires the completion funnel **only** on the `0 → positive` transition (`if count > 0 and prev_count == 0:`). The concurrent-first-insert race re-reads `prev_count = row.count` from the winning row (`:601`) so the loser doesn't re-fire, and `complete_quest_for_user` / challenge `active_day` dedup are both day-scoped. **No action needed.**

---

## Prior-report items — re-verified still open at HEAD

Untouched by #195–#198 (diff-stat confirms the relevant files did not change), so these remain exactly as documented in `NEEDED_FIXES_2026-08-02.md`:

- **P3 (Medium) — `ai_gate._model_slots.acquire()` has no timeout.** *Highest-priority still-open item because it is NOT flag-gated and is live on the AI paths.* PR1 wrapped the slot in `_measured_model_slot` (`ai_gate.py:85-119`) and now emits `AiModelSlotContended` for visibility, but the acquire is still unbounded: non-blocking probe → fall through to blocking `_model_slots.acquire()` (`ai_gate.py:98-100`, legacy path `:137`). `food_search`/`respond_suggestion` remain ungated. Fix is a one-liner: bounded `acquire(timeout=…)` → 503 + Retry-After on contention, matching the gate's existing overload semantics.
- **P1 (Medium) — `mobile_auth.refresh()` holds `FOR UPDATE` across the Cognito `refresh_tokens` round-trip.** Gated behind `MOBILE_AUTH_ENABLED` (default OFF); the flag registry (`feature_flags.py:418-424`) names PR4 capacity-hardening as a hard prerequisite. Fix: renew provider token before opening the locking transaction, then use the existing optimistic `version`-guarded UPDATE (`mobile_auth.py:479-488`).
- **P2 (Medium) — mobile `/login` + `/refresh` make blocking Cognito calls, no concurrency gate, unaccounted in the thread reserve.** Gated, default OFF. Same rollout prerequisite as P1.
- **L6 (Low)** — `plan_facts` rejects the `{"program": [...]}` wrapper other parsers accept.
- **L7 (Low)** — `/training/bootstrap` 500s on a valid plan whose `set`/`sure_dk` exceed serializer bounds (`workout_state/serialization.py`).
- **L8 (Low)** — completing today terminalizes a stale previous-day ACTIVE session as `COMPLETED` (flag-off path).
- **L9 (Low)** — OpenAI non-stream coach loop (`ai_coach.py:~1015`) has no per-turn wall-clock budget.
- **P5 (Low)** — `ProxyFix(x_host=1, x_port=1)` trusts client `X-Forwarded-Host`/`-Port` nginx never sets (`config.py:273`). CSRF Layer-2 still holds; impact limited to self-inflicted external-URL generation.
- **S (Low, structure)** — god-modules `ai_coach.py` (1211), `social.py` (1033), `tracking.py` (728). No new bug risk on their own; carried as tech-debt.

---

## Recommended priority order

1. **P3** — bound `_model_slots.acquire(timeout=…)` and gate `food_search`/`respond_suggestion`. Live, un-gated, one-liner, protects the documented thread-reserve invariant. *Do this first.*
2. **N1** — make the `ThreadReserve` abort signal real (emit the gauge) **or** fix the runbook to name existing signals — a prerequisite for safely turning on `MOBILE_AUTH_ENABLED` (which then also needs P1/P2).
3. **N3** — add a final `flush()` on shutdown (cheap, closes the deploy-time metrics gap that hides exactly the spikes you deploy to fix).
4. **N4** — add the `.env` normalization step to the release checklist before the next deploy carrying #196/#198.
5. **N2 / L6–L9 / P5 / S** — low-severity; batch as convenient.

*(P1/P2 stay deferred with `MOBILE_AUTH_ENABLED` OFF; address them together with N1 as the gate to enabling native mobile auth.)*

---

*Audit performed read-only across 3 parallel deep-dive agents (security, correctness, reliability). Every finding cites verified `file:line` evidence at commit `8e0458a`. No files were modified during this triage.*
