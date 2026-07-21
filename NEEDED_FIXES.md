# FitX — Needed Fixes (Triage Report)

**Date:** 2026-07-21
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Correctness bugs, (3) Architecture/Reliability.
**Method:** Adversarial trace of every attack surface and high-risk flow. Every claim was verified against the code (cited `file:line`), not against CLAUDE.md.
**Commit reviewed:** `21f2608` on `claude/amazing-dijkstra-numbn8` (== `main`).
**Baseline:** The prior triage (`NEEDED_FIXES.md` @ `022d821`, PR #171) was resolved by PR #172. All 10 prior items were re-verified as genuinely fixed at HEAD (see appendix). This report lists only **new / still-open** findings.

## Headline

The codebase remains **mature and defense-in-depth**. **No Critical or High-severity issues were found.** The three audits surfaced **1 Medium** reliability gap, **4 Low / Low–Medium** issues (one gamification-integrity, one security-hardening, two latent/low-blast-radius), and **2 open structural tech-debt** items carried over from the prior audit.

| # | Severity | Area | Type | Confidence |
|---|----------|------|------|------------|
| 1 | Medium | `ai_gate.py` thread-reserve breachable by ungated `_model_slots` callers | Reliability | Confirmed |
| 2 | Low–Medium | `weekly_water` challenge counts toggle-events, not days | Correctness / integrity | Confirmed |
| 3 | Low | `ProxyFix` trusts `X-Forwarded-Host`/`-Port` that nginx never sets | Security | Confirmed |
| 4 | Low | OpenAI non-stream coach loop has no per-turn wall-clock budget | Reliability | Confirmed |
| 5 | Low | `detect_deload_due` effectively gated on "trained today" | Correctness | Suspected (not yet runtime-wired) |
| 6 | Low | `ai_coach.py` (1199 lines) god-module — still open | Structure | Confirmed |
| 7 | Low | `social.py` (1033 lines, 3 domains) — still open | Structure | Confirmed |

---

## 1. [Medium] Thread-reserve invariant is breachable by ungated routes blocking on `_model_slots`
**Files:** `app/services/ai_gate.py:53,57-64` (`_model_slots`, `model_concurrency_slot`), invariant `ai_gate.py:68-91`; ungated callers `app/blueprints/food.py:22` (`food_search`) and `app/blueprints/social.py:881` (`respond_suggestion`).

The A1/I1 "reserve 2 threads for `/health`" guarantee is computed as `WEB_THREADS − (AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY)` = `8 − (4 + 2)` = 2 (`ai_gate.py:70`). But `model_concurrency_slot()` acquires a **third** semaphore, `_model_slots` (default 4), with **no timeout** (`_model_slots.acquire()`, `ai_gate.py:60`) — and it is used by routes that are **not** behind `ai_concurrency_gate`: `food_search` (only per-user rate-limited) and `respond_suggestion`. Each request parked on that semaphore holds a gunicorn web thread.

**Failure scenario:** 4 coach turns hold all `_ai_slots` + `_model_slots`. A cross-user burst of `food_search` cache-misses (per-user limits don't stop *different* users) or `respond_suggestion` calls then block on `_model_slots.acquire()` with no timeout → up to 4 more threads parked → all 8 web threads consumed → `/health` queues behind AI work → Docker HEALTHCHECK / deploy gate times out → **false rollback or restart-loop**. This is the exact starvation the reserve was designed to prevent; `test_ai_gate.py` asserts the reserve math only against the two gates it counts, giving false confidence.

**Fix:** Give `model_concurrency_slot` a bounded `acquire(timeout=…)` returning a friendly 503 instead of blocking indefinitely; and/or put `ai_concurrency_gate` on `food_search` and `respond_suggestion`; and/or fold `_model_slots` consumers into the reserve invariant. At minimum, document that ungated model callers can breach the reserve.

---

## 2. [Low–Medium] `weekly_water` challenge counts toggle-events, not days — completable in a single day
**Files:** `app/blueprints/training.py:347-353` (emit) + `app/services/challenges.py:110-133` (`record_event`, no per-day dedup) + seed `challenges.py:184-186`.

The `weekly_water` challenge is `metric="water_logged", target_value=5`, described "log water on **5 days** this week". The funnel event fires only on the day's `0→positive` transition:

```python
if count > 0 and prev_count == 0:
    complete_quest_for_user(current_user.id, "water_logged")  # → record_event("water_logged")
```

But `record_event` has only a **weekly** `period_key` and blindly does `progress = progress + amount` (`challenges.py:122-124`) — **no per-day guard**. The caller's "dedup" is `prev_count == 0`, which is defeatable: `count` is user-supplied, clamped to `0..8` (`training.py:319`), so a user can POST `count=5` (fires), POST `count=0` (prev=5, no fire), POST `count=5` (prev=0, **fires again**) — all on the **same calendar day**.

**Failure scenario:** Toggle water `0→5` five times in one afternoon → `weekly_water` progress reaches 5 → a "log water 5 days" challenge completes in a single day, awarding its XP. (The DailyQuest XP itself is safe — deduped by `UserQuestProgress` per `date_key` — but the **challenge counter is inflated**.) The inline comment at `training.py:347-349` claims parity with the `active_day` pattern, but that analogy is broken: `active_day` fires exactly once per Istanbul day from a `FOR UPDATE`-locked branch (`hooks.py`), which is genuinely idempotent; `prev_count==0` is not.

**Fix:** Give day-semantic challenge metrics a per-day idempotency key — e.g. `record_event` dedupes `water_logged` per Istanbul day (persist `last_event_day` on the progress row), or gate the emit on a persisted "first positive log today" marker instead of the mutable `prev_count`.

---

## 3. [Low] `ProxyFix` trusts `X-Forwarded-Host` / `X-Forwarded-Port` that the reverse proxy never sets
**Files:** `app/config.py:231` (`ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)`) vs `nginx.conf:107-110,121-124`.

nginx sets only `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto` — it does **not** set or strip `X-Forwarded-Host` / `X-Forwarded-Port`. But ProxyFix is configured with `x_host=1, x_port=1`, so it reads those hyphenated headers, which nginx forwards from the client as-is. An external client can send `X-Forwarded-Host: evil.com` and ProxyFix overwrites Flask's host → `request.host` / `request.host_url` / `url_for(_external=True)` reflect the attacker value.

**Blast radius (why Low, not High):** CSRF Layer 1 (`hooks.py:150`, Origin vs `request.host_url`) can be satisfied by a matching spoofed host, but **Layer 2** (per-session synchronizer token, `hooks.py:177-183`) still holds — cross-origin JS cannot read it, so this is **not** a CSRF bypass. External-URL generation (referral link `pages.py:75`) is self-inflicted per-request; logout referer check (`auth.py:590`) is primarily guarded by the unspoofable `Sec-Fetch-Site`. Defense-in-depth holds throughout.

**Fix (simplest, closes it fully):** set `x_host=0, x_port=0` in the ProxyFix call — nginx forwards the correct `Host` natively, so nothing is lost. Alternatively, have nginx explicitly `proxy_set_header X-Forwarded-Host $host;` / `X-Forwarded-Port $server_port;` so the trusted proxy — not the client — controls them.

---

## 4. [Low] OpenAI non-stream coach loop has no per-turn wall-clock budget
**File:** `app/services/ai_coach.py:1010-1013` (`_run_coach_conversation_openai`).

PR #172 added per-turn deadlines (`_coach_turn_deadline` / `_remaining_coach_turn_seconds`) to the Bedrock non-stream loop (`ai_coach.py:1093`) and the stream loop (`ai_stream.py`), but the **OpenAI fallback** loop was not updated. Up to `_COACH_TOOL_LOOP_CAP` (5) rounds × 30 s OpenAI timeout ≈ 150 s single-turn thread-hold on the fallback path — each round holding one `_ai_slot` + `_model_slot` + web thread.

**Failure scenario:** Bedrock down → all coach turns route through OpenAI; a slow multi-tool turn holds an AI slot + thread ~150 s, compounding finding #1's starvation. Bounded by gunicorn `timeout=300` so no hard hang, but exceeds the intended per-turn budget and the parity #172 established.

**Fix:** Apply the same `_coach_turn_deadline` / `_remaining_coach_turn_seconds` guard (`timeout=min(30, remaining)`) to the OpenAI loop.

---

## 5. [Low, Suspected] `detect_deload_due` is effectively gated on "trained *today*" via the forward-looking current-week window
**Files:** `app/services/training_progression/analysis.py:137-142` + windowing `training_history/analysis.py:41-49` (`weekly_windows`).

`weekly_windows(end_day, weeks)` makes the newest window **start** on `end_day`, covering `[today, today+6]`; since data only exists up to today, that bucket only ever contains **today's** entries. `volume_trend` / `series_trend` / `detect_plateau` tolerate this because they filter to active (`v > 0`) windows — but `detect_deload_due` does **not**:

```python
if any(v <= 0 for v in vols[-MIN_DELOAD_WEEKS:]):  # newest window == today only
    return False
```

**Failure scenario:** A user in a genuine multi-week accumulation block, checked on a day they haven't yet trained → newest window volume = 0 → deload never flags. Deload can only ever fire on days the user already trained.

**Severity Low / Suspected** because this progression/planning layer is **not yet wired into runtime** (docs: "later PRs wire it in"; only `tracking.py` heatmap/insights/workout + `time_series_model` consume the foundation today, and those use explicit trailing `[start, today]` ranges). The forward-window convention matches pre-existing `build_performance_history`, so it is consistent — but for `detect_deload_due` specifically it yields systematically never-fire output.

**Fix:** Exclude the current partial window from the deload "all-active" check, or make the newest `weekly_windows` bucket trailing (`[today-6, today]`) if the intent is "last N trailing weeks."

---

## Structural tech-debt (open, unchanged severity — schedule as their own PRs)

### 6. [Low] `ai_coach.py` god-module — 1199 lines
Still the largest module; the prior audit's suggested `coach_tools/` extraction did not happen (it grew from 1178). No behavior change needed; refactor to reduce blast radius and monkeypatch surface.

### 7. [Low] `social.py` — 1033 lines spanning 3 product domains (chat / feed / pump-check + moderation)
Still a catch-all HTTP surface. Consider splitting into per-domain blueprints.

---

## Appendix — prior-audit (PR #171 → #172) status at HEAD

All 10 prior findings verified **resolved**:

| # | Prior finding | Status |
|---|---|---|
| 1 | ai_gate boot invariant only warns | ✅ Fatal in prod (`enforce_gate_invariants`, `ai_gate.py:68-91`) |
| 2 | Per-process gates assume workers=1, unenforced | ✅ `WEB_WORKERS!=1` fatal + single-source env (`gunicorn.conf.py` reads same vars) |
| 3 | Hydration nudge divides by logged-days | ✅ Fixed 7-day window (`analytics_engine._check_hydration`) |
| 4 | `ai_coach.py` god-module | ⚠️ **Still open** → finding #6 |
| 5 | `social.py` multi-domain | ⚠️ **Still open** → finding #7 |
| 6 | Inline `WorkoutLog` readers | ✅ Routed through `training_history.fetch_workout_entries` |
| 7 | Bedrock 60s per-call not per-turn | ✅ Bedrock + stream loops; ⚠️ OpenAI loop missed → finding #4 |
| 8 | Dead quota-counter functions | ✅ Removed (`premium.py`, no dangling refs) |
| 9 | Unescaped LIKE wildcards | ✅ `ilike(..., escape="\\")` (`social.py:566`) |
| 10 | Stream quota on immediate disconnect | By-design, unchanged |

## Areas verified clean (no new issues)

- **AuthZ / IDOR:** every record-by-id load ownership-scoped to `current_user.id`; feed/pump-check via `_visible_*_or_403`; reposts block audience-widening; S3 keys carry per-user segment guard.
- **CSRF / CSP / XSS:** two-layer CSRF on all writes, no state-changing GET; per-request nonce, no `unsafe-inline`, jsdelivr SRI-pinned; zero `|safe` on user data; frontend `esc()` before `innerHTML`.
- **Auth/JWT:** full RS256 + `iss`/`aud`/`exp`/`token_use` validation; session-fixation-safe login; Fernet-encrypted server-side tokens.
- **SSRF:** positive `is_global` allow-list, port allowlist, per-hop redirect re-validation, DNS-pinning, body-size caps.
- **AI failover B-rule / B16 / quota:** fallback text classified as fallback → not persisted, quota refunded; provider switch only before first delta AND `tools_ran==0`.
- **Gamification tx-safety:** `FOR UPDATE` XP, savepoint-isolated challenges, `count_challenge_xp=False` recursion cut, idempotent rollover via `WeeklyResetLog` UNIQUE.
- **Meal idempotency:** `commit_once` resolves uniqueness race; XP/quests only on `created==True`.
- **Deploy/rollback:** no new migrations since baseline; the two recent ones are expand-only + re-runnable; boot-upgrade FATAL fail-fast intact.
- **Layering:** `training_*/` and `app/prompts/` import no Flask/db; dependency direction one-way as documented.
