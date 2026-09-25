# FitX — Needed Fixes (Triage Report)

**Date:** 2026-08-07
**Commit reviewed:** `34f8dc7` — *Hardening PR4 — concurrency, overload & recovery closure (#200)* (== `main` HEAD).
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Correctness / logic bugs, (3) Architecture / Reliability / Concurrency / Infra.
**Method:** Adversarial trace of every attack surface and high-risk flow. Every claim was verified against actual code (cited `file:line`), **not** against CLAUDE.md, docstrings, or changelogs. Prior-report items were re-verified at HEAD; only still-open ones are carried forward.
**Baselines re-verified:**
- `NEEDED_FIXES.md` (2026-07-21 @ `21f2608`)
- `NEEDED_FIXES_2026-08-02.md` (2026-08-02 @ `392c556`) — its findings **1–9** were addressed by **PR #199** ("Fix triage findings 1-9"), followed by **PR #200** (Hardening PR4). All three audits independently confirmed those fixes against the code.

---

## Headline

The codebase remains **mature and defense-in-depth**. **No new Critical, High, or Medium issues were found by any of the three audits.**

PR #199 and #200 **genuinely closed every prior finding (1–9)** from the 2026-08-02 report — each fix was re-verified against the actual code by at least two of the three audits (see the "Re-verified FIXED" appendix). The concurrency / thread-reserve posture, mobile-auth lifecycle, and workout-completion transaction machinery are all sound at HEAD.

The audits surfaced only:
- **1 still-open Low–Medium integrity bug**, flagged **independently by both the security and correctness audits** — a gamification-abuse item first raised in the **2026-07-21** report (#2) that was never addressed.
- **1 new Low reliability item** — a residual ungated-network surface of the exact class PR #199 was closing.
- **1 Low structural / tech-debt item** — god-modules continuing to grow.
- **1 negligible drift observation** — no action required.

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| 1 | Low–Medium | `weekly_water` challenge counts toggle-events, not distinct days | Correctness / gamification integrity | **Prior 2026-07-21 #2 — re-verified STILL OPEN** | Confirmed (2 audits) |
| 2 | Low | 3 FatSecret food routes park web threads with no concurrency slot | Reliability / thread-exhaustion | **New** | Confirmed (ungated); plausible (outage) |
| 3 | Low | God-modules continue to accrete (`social.py` +158 lines since last report) | Structure / tech debt | Prior — still open, grown | Confirmed |
| 4 | Negligible | Duplicate Istanbul-date helper outside `timeutil` | Structure / drift risk | Observation | Confirmed (not a bug) |

---

## 1. [Low–Medium] `weekly_water` challenge counts toggle-events, not distinct days — completable in a single day

*Re-verified prior finding (2026-07-21 report #2). Flagged **independently by both the security and the correctness audits** this run. Verified against code below.*

**Files:**
- `app/blueprints/training.py:585` — `prev_count = row.count if row else 0` (derived from the **mutable** current row)
- `app/blueprints/training.py:613` — emit gate `if count > 0 and prev_count == 0:` → `complete_quest_for_user(..., "water_logged")`
- `app/services/gamification.py:281-282` (`_claim_quest`) — `record_event(user_id, quest_type)` fires **unconditionally, before** the DailyQuest per-day dedup at `:289-293`
- `app/services/challenges.py:122-124` (`record_event`) — atomic column `UPDATE progress = progress + amount` keyed **only** by the weekly `period_key`, with **no per-day idempotency**

**Confirmed failure scenario.** The `weekly_water` challenge (`metric="water_logged"`, `target_value=5`, "log water on 5 days this week") is meant to count distinct days. `count` is user-supplied and clamped `0..8` (`training.py:578-581`). The emit gate keys off `prev_count == 0`, and `prev_count` is read from the current `WaterLog` row — which the same user can reset. On a **single Istanbul day**:

1. `POST /water {count:5}` → `prev_count=0` → fires → `record_event` drives challenge progress to 1.
2. `POST /water {count:0}` → `count>0` false → no fire, **but row.count is now 0**.
3. `POST /water {count:5}` → `prev_count=0` again → **fires again** → progress 2.

Repeat the 0↔5 toggle 5× in one afternoon → the "5 days" challenge completes in one day, awarding its XP (+badge/leaderboard bump).

**Why the inline comment's defense is false.** `training.py:609-611` claims parity with the `active_day` pattern, but that analogy does not hold: `active_day` fires exactly once per Istanbul day from a `FOR UPDATE`-locked, day-keyed branch (`hooks.py:315-333`, genuinely idempotent). `prev_count == 0` is mutable state the user controls and can replay. The 2026-08-02 report explicitly said to "verify #2 separately if it was not addressed" — it was **not** addressed; both the emit gate and `record_event` are byte-for-byte the same pattern at HEAD.

**Blast radius (why Low–Medium, not higher).** Integrity-only and self-service — a user can inflate only their **own** weekly challenge counter (+75 XP + a slightly-early leaderboard bump), and only by deliberate toggling. The per-day `DailyQuest` XP itself is **safe** (deduped by the day-keyed `UserQuestProgress` at `gamification.py:289-293`); the leak is confined to the challenge counter/badge.

**Recommended fix.** Give day-semantic challenge metrics a per-day idempotency key rather than relying on mutable `prev_count`:
- Persist a `last_event_day` (Istanbul day) on the `UserChallengeProgress` row and have `record_event` no-op when the metric is day-semantic and `last_event_day == today`; **or**
- Gate the emit on a persisted "first positive water log today" marker (mirroring the `active_day` locked-once-per-day pattern) instead of the resettable `prev_count`.

---

## 2. [Low] Three FatSecret food routes still park web threads with no concurrency slot

*New finding — same thread-exhaustion class PR #199 closed for `food_search`, left applied to only one of four sibling routes.*

**Files:**
- `app/blueprints/food.py:59-73` — `food_by_barcode`
- `app/blueprints/food.py:149-161` — `food_servings`
- `app/blueprints/food.py:164-221` — `food_servings_by_name`
- Network round-trips: `app/services/fatsecret.py:44,196` (`timeout=10` / `timeout=5`)

**Context.** The app runs a **single gunicorn worker with 8 threads**; synchronous blocking network calls can exhaust all of them, which is why Hardening PR4 introduced the reserve-counted `blocking_concurrency_slot()` and a boot invariant. PR #199 correctly wrapped `food_search` (`food.py:46`) in that slot — but left its three siblings ungated. Each of the three makes a blocking FatSecret HTTP round-trip (barcode lookup, servings-by-id, servings-by-name) protected only by `@require_auth` and a **per-user** rate limit (`FOOD_SEARCH_RATELIMIT`, `_user_or_ip_key`). A per-user limit does not bound a **cross-user** burst.

**Failure scenario (plausible).** During a FatSecret latency spike or FatSecret-proxy degradation, a cross-user burst of cache-miss barcode/servings lookups can park up to all 8 gunicorn threads for 5–10 s each on `requests` I/O that is **not counted** in the thread-reserve arithmetic and **not** behind any gate. `/health` then queues behind them → the exact false-rollback / restart-loop the reserve exists to prevent — the same outcome #199 closed for `food_search`. Confidence: **Confirmed** for the ungated state; **plausible** for the outage (requires FatSecret degradation; the barcode route is cheap on cache hits via `barcode_food_cache`).

**Recommended fix.** Wrap the FatSecret call in each of the three routes with `blocking_concurrency_slot()`, degrading to an empty result on `BlockingConcurrencyLimit` — mirroring the existing `food_search` handling.

---

## 3. [Low] God-modules continue to accrete

*Prior structural item (2026-07-21 #6/#7, 2026-08-02 #10) — still open; `social.py` grew the most since the last report.*

Line counts at HEAD (`wc -l`):

| Module | Lines | Δ vs 2026-08-02 |
|---|---|---|
| `app/services/ai_coach.py` | 1247 | +36 |
| `app/blueprints/social.py` | 1191 | **+158** (largest drift; feed/pump-check/friends in one file) |
| `app/services/mobile_auth.py` | 865 | new large module |
| `app/blueprints/tracking.py` | 728 | ~flat |

No functional bug — the concern is blast radius and the monkeypatch/test surface. Incremental extraction recommended, not urgent: `social.py` → per-domain blueprints (feed / pump-check / friends); `ai_coach.py` → a `coach_tools/` package.

---

## 4. [Negligible] Duplicate Istanbul-date helper outside `timeutil`

*Observation only — not a bug, no action required.*

`app/services/coach_context_queries.py:13-22` defines its own `_app_today()` / `_day_key()` via `datetime.now(ZoneInfo("Europe/Istanbul")).date()` instead of `app.timeutil`. It is **functionally equivalent** (same tz, same ISO key) and is deliberately Flask/SQLAlchemy-free for the psycopg2 coach-context read path. It is only a **drift risk** if `timeutil`'s day semantics ever change. No `date.today()` / `utcnow()` day-key bypasses of `timeutil` exist elsewhere in application code (grep-confirmed).

---

## Appendix A — Prior findings RE-VERIFIED FIXED at HEAD (do not re-report)

Each row was confirmed against the actual changed code by at least two of the three audits.

| Prior finding | Status | Evidence |
|---|---|---|
| #1 (08-02) `mobile_auth.refresh()` holds `FOR UPDATE` across Cognito network call | **Fixed** | Two-phase: `_snapshot_refresh` locks + **rolls back / releases** before returning (`mobile_auth.py:495`); `_renew_provider_tokens` does the Cognito round-trip with no lock and asserts `not in_transaction()` (`:515-517`); `_lock_and_revalidate_refresh` re-acquires with optimistic version guard (`:564-598,639-647`). |
| #2 (08-02) Mobile auth endpoints ungated / unaccounted in reserve | **Fixed** | `login` and `_renew_provider_tokens` run inside `blocking_concurrency_slot()` (`mobile_auth.py:201,512`); saturation → `AUTH_TEMPORARILY_UNAVAILABLE` 503 + `Retry-After`; wearables provider calls folded into the same shared slot (`wearables.py:109,158,183`). |
| #3 (08-02) / #1 (07-21) `ai_gate._model_slots` unbounded acquire breaches reserve | **Fixed** | `model_concurrency_slot` acquires via bounded gate-wait + optional deadline and raises `BlockingConcurrencyLimit` (`ai_gate.py:191-200`); reserve invariant now counts model-gate excess (`:255-266`) and the DB pool (`_db_pool_problems` `:230-252`); ungated callers `food_search` / `respond_suggestion` now take the reserve-counted slot (`food.py:48`, `social.py:1062-1065`). |
| #4 (07-21) Mobile login discloses account-verification state (user enumeration) | **Fixed** | `UserNotConfirmedException` now maps to `AUTH_INVALID_CREDENTIALS` / 401, identical to bad credentials (`mobile_auth.py:206-210`); the 403-vs-401 split is gone. |
| #5 (08-02) / #3 (07-21) `ProxyFix` trusts client `X-Forwarded-Host` / `-Port` | **Fixed** | Now `ProxyFix(..., x_for=1, x_proto=1, x_host=0, x_port=0)` (`config.py:292`); nginx sets only `Host`/`X-Real-IP`/`X-Forwarded-For`/`X-Forwarded-Proto` (`nginx.conf:107-110,121-124`). `request.host` / `url_for(_external=True)` / `/logout` Referer fallback can no longer be spoofed. |
| #5 (07-21) `detect_deload_due` gated on "trained today" | **Fixed** | Trailing windows (`training_history/analysis.py:41-61`); `detect_deload_due` now evaluates a complete lived week (`training_progression/analysis.py:137-142`). |
| #6 (08-02) `plan_facts` rejects the `{"program":[...]}` wrapper | **Fixed** | Unwraps the dict before the `isinstance(list)` check (`plan_facts.py:110-113`). |
| #7 (08-02) `/training/bootstrap` 500s on out-of-bounds `set` / `sure_dk` | **Fixed** | Validator bounds them to serializer range — `sure_dk` 0..1440 (`response_validator.py:67-68`), `set` 1..100 (`:83`) — matching `serialization.py:69,79,95-99`. |
| #8 (08-02) Completing today terminalizes a stale previous-day ACTIVE session | **Fixed** | `resolve_for_completion` returns `STALE_SESSION_REQUIRES_RESOLUTION` when `session.workout_date != today` (`workout_session/service.py:282-286`); route enforces it (`training.py:233-237`). |
| #9 (08-02) OpenAI non-stream coach loop has no per-turn wall-clock budget | **Fixed** | `_run_coach_conversation_openai` derives a `deadline`, checks `_remaining_coach_turn_seconds` before and after the model gate, passes `timeout=min(30.0, remaining)` (`ai_coach.py:1015-1084`); stream fallback shares the same deadline (`ai_stream.py:120,134-136,270-272`). |
| (PR4) Unknown-`kid` forced JWKS refresh parks threads | **Fixed** | Single-flight + 60 s cooldown; timestamp written before the fetch so a downed endpoint still cools down; known-kid traffic never touches the lock (`cognito_jwt.py:98-133`). |
| Wearables `callback`/`sync`/`whoop` park ungated threads | **Fixed** | All three wrap the network exchange in `blocking_concurrency_slot()`, DB writes kept outside the slot (`wearables.py:109-124,158-159,183-185`). |
| DB pool below thread count / silent 30 s checkout | **Fixed** | Explicit `pool_size=8`, `max_overflow=4`, `pool_timeout=10` (`config.py:362-376`), enforced by boot invariant `pool_size+max_overflow >= WEB_THREADS` (`ai_gate.py:246`). |

---

## Appendix B — Surfaces audited and found CLEAN (no finding)

**Security**
- **JWT** (`cognito_jwt.py`): `algorithms=["RS256"]` pinned (no `alg=none`); `exp` essential; `iss`/`token_use`/`aud`/`client_id` enforced; leeway pinned to 0 via `auth_contract`; request auth validates the **access** token, login the **id** token.
- **Session** (`session_store.py`, `auth_middleware.py`): Fernet enforced outside dev; `get_valid_access_token` re-checks `row.user_id == expected_user_id`; `require_auth` re-checks `resolved.id == current_user.id`; login is fixation-safe (session cleared before `login_user`); no token/secret values logged.
- **CSRF/CSP** (`hooks.py`): two-layer CSRF (Origin/Referer + session synchronizer token) on all writes; `mobile_api` correctly exempt (Bearer-only, no cookies); no `unsafe-inline` in `script-src`; per-request nonce; only state-changing GET is `/logout`, guarded by `Sec-Fetch-Site` + default-deny Referer fallback.
- **XSS/templates:** no `|safe` on user data; all inline-script injection points use `|tojson`; coach output rendered only via `DOMPurify.sanitize(marked.parse(...))`.
- **IDOR:** every record-by-id load in `social.py`, `notifications.py`, `challenges.py`, `supplements.py`, `profile.py`, `tracking.py`, `training.py`, `wearables.py`, `mobile_api.py` is scoped to `current_user.id` or gated by `can_view_pump_check` / `are_friends` / `_visible_*_or_403`; comment deletes authorize author-or-post-owner; reposts block audience-widening (`social.py:279`).
- **Prompt injection:** third-party friend content is the only untrusted text reaching the coach — fenced + neutralized (fence tokens case-insensitively stripped, zero-width/bidi chars removed; `context_builder.py:205-218`); `adaptive_plan_context` serializes only enums/booleans/floats from the user's own history, no free text.
- **SSRF** (`menu_fetch.py`): positive `is_global` allow-list, 80/443 port allow-list, per-hop redirect re-validation, DNS-rebinding pin, body-size caps; wearables base URLs fixed (not user-controlled), OAuth state validated.
- **Secrets/S3:** `_key_belongs_to` uses segment equality; presigned URLs / `get_object_bytes` carry an `expected_user_id` ownership guard; no hardcoded keys; new observability code uses fixed-cardinality dimensions with no user id/token/path.
- **fitx_mcp:** HTTP transport gated behind `FITX_MCP_ALLOW_HTTP=1` and bound to `127.0.0.1`.

**Correctness**
- **Canonical completion** (`workout_completion/service.py`): PumpCheck + marker + quest + XP + activity + friend-messages atomic under one commit; helpers commit-free; `record_event` savepoint-isolated / self-swallowing; only `is_pump_check_day_violation` → `ALREADY_COMPLETED`, every other `IntegrityError` re-raises; no network inside the tx; race-loser session reconciliation preserves fixed lock order (session row first).
- **Meal idempotency** (`meal_idempotency.py`): per-user unique key; `commit_once` resolves the race to the winner row; XP/quests gated on `created == True`.
- **Streak / `active_day`** (`hooks.py:294-348`): `FOR UPDATE` + column re-read + Istanbul-day fast path → exactly-once per day.
- **Adaptive math** (`weekly_program/analysis.py`): baseline = most-recent positive volume (zero weeks skipped); `target = round(baseline*(1+delta), 2)`; `None` propagated (never `0.0`); windows contiguous/non-overlapping, no off-by-one.
- **AI stream B-rule** (`ai_stream.py:206,237`): provider switch only when `not parts and tools_ran == 0`.

**Architecture / Reliability**
- **No network-inside-transaction / lock-across-I/O anywhere.** Every `with_for_update()` site (`gamification.py:106`, `premium.py:85,113`, `memory_manager.py:88`, `hooks.py:314`, `supplements.py:75`, `workout_session/queries.py:177`, `workout_completion/queries.py:104`) is DB-only; the only prior offender (`mobile_auth.refresh`) is fixed.
- **`coherent_read_snapshot` leaks no connections** — `db.session.remove()` in a `finally` returns the connection on every path (`workout_state/snapshot.py:26-36`); REPEATABLE READ applied only on Postgres.
- **Migration boot path is safe.** The unguarded table-creating migration `e7f8a9b0c1d2` is stamped over on fresh boot (the stamp `aa11bb22cc33` sits after the merge in the DAG); all migrations that actually run post-stamp carry `has_table` guards; no DROP/RENAME since baseline → the A2 rollback risk is dormant.
- **Redis-optional degradation intact:** limiter in-memory fallback + `LOGIN_FAIL_CLOSED`; RQ `get_queue()` None-safe with inline fallback; deferred-summarize path bounded and behind the model gate.
- **Infra:** compose binds web/redis to loopback with mem_limits + log rotation; Redis password kept out of argv; nginx keeps the fatsecret proxy on loopback and the deploy pipeline fail-fasts on a stale CSP `add_header`; gunicorn pinned to 1 worker / 8 threads matching the gate invariant.

---

## Prioritized action list

1. **Fix #1 (`weekly_water`)** — the one genuine open bug; two audits, low effort. Add a per-day idempotency key to day-semantic challenge metrics in `record_event`, or gate the water emit on a persisted first-positive-log-today marker.
2. **Fix #2 (FatSecret routes)** — wrap the three remaining `food.py` FatSecret calls in `blocking_concurrency_slot()`, matching `food_search`. Low effort, closes the last route of a class already fixed.
3. **#3 (god-modules)** — schedule incremental extraction of `social.py` and `ai_coach.py`; not urgent.
4. **#4** — no action required.
