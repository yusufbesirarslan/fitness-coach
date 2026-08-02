# FitX — Needed Fixes (Triage Report)

**Date:** 2026-08-02
**Commit reviewed:** `392c556` on `claude/amazing-dijkstra-0imcp0` (Sprint 7 PR4 + native mobile-auth foundation; == `main` HEAD).
**Scope:** 3 parallel read-only deep-dive audits — (1) Security, (2) Correctness bugs, (3) Architecture/Reliability. Focus on code added **after** the prior triage (`NEEDED_FIXES.md` @ `21f2608`, 2026-07-21): the native mobile-auth stack (`#191`), Sprint 7 workout services PR1–PR4 (`#183`–`#190`), and the UIUX Plan/Today/Coach V2 presenters (`#182`, `#185`, `#189`).
**Method:** Adversarial trace of each attack surface and high-risk flow. Every claim verified against actual code (cited `file:line`), not against CLAUDE.md/docstrings. Prior-report items were re-verified at HEAD; only still-open ones are carried forward.

## Headline

The codebase remains **mature and defense-in-depth**, and the newest subsystems (opaque mobile-session lifecycle, persisted workout-session state machine, Plan/Today presenters) are hardened by construction — pure/impure separation, server-side ownership re-checks on every transition, fail-closed error contracts, bounded serializers.

**No Critical or High-severity issues were found.** The three audits surfaced **3 Medium** reliability items (one flagged by two independent audits), plus **6 Low** items and the two long-standing structural tech-debt items.

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| 1 | Medium | `mobile_auth.refresh()` holds `FOR UPDATE` row lock across a synchronous Cognito network call | Reliability / DoS | **New** | Confirmed (2 audits) |
| 2 | Medium | Mobile auth endpoints make blocking Cognito calls — ungated, unaccounted in thread reserve | Reliability / DoS | **New** | Confirmed |
| 3 | Medium | `ai_gate` thread-reserve breachable by ungated `_model_slots` callers | Reliability / DoS | Prior #1 — re-verified open | Confirmed |
| 4 | Low | Mobile login discloses account-verification state (user enumeration) | Security | **New** | Confirmed |
| 5 | Low | `ProxyFix` trusts client `X-Forwarded-Host` / `-Port` that nginx never sets | Security | Prior #3 — re-verified open | Confirmed |
| 6 | Low | `plan_facts` is the only plan parser that rejects the `{"program": [...]}` wrapper | Correctness | **New** | Confirmed |
| 7 | Low | `/training/bootstrap` 500s on a valid plan whose `set`/`sure_dk` exceed serializer bounds | Correctness | **New** | Confirmed |
| 8 | Low | Completing today terminalizes a stale previous-day ACTIVE session as `COMPLETED` | Correctness | **New** (flag-off) | Confirmed |
| 9 | Low | OpenAI non-stream coach loop has no per-turn wall-clock budget | Reliability | Prior #4 — re-verified open | Confirmed |
| 10 | Low | God-modules: `ai_coach.py` (1211), `social.py` (1033), `tracking.py` (728) | Structure | Prior #6/#7 — open, +1 | Confirmed |

> **Prior-report item resolution:** #2 (`weekly_water` counts toggle-events) and #5 (`detect_deload_due`) from the 2026-07-21 report were **not** independently re-surfaced by this run and are not re-listed here — treat their status as per that report (#5 was marked fixed 2026-07-22). Verify #2 separately if it was not addressed.

---

## 1. [Medium] `mobile_auth.refresh()` holds a `SELECT … FOR UPDATE` row lock across a synchronous Cognito network call

**Files:** `app/services/mobile_auth.py:390-391` (row lock acquired) → `:426-444` (blocking `cognito_service.refresh_tokens(...)` + token validation inside the same transaction) → `:489` (commit).

*Flagged independently by both the correctness and architecture audits — highest-confidence new finding.*

The refresh transaction locks the `MobileAuthSession` family row with `with_for_update()` at line 391, and — while that lock is still held — performs the blocking Cognito `refresh_tokens` network round-trip at line 429 (when the stored provider token is near expiry), not committing until line 489. The row lock **and** the DB connection are held for the full network latency (~20 s worst case: `connect_timeout=5, read_timeout=10, max_attempts=2` in `cognito_service.py:74-76`).

This directly violates the "no network call inside the transaction" rule the Sprint 7 PR2 completion service deliberately enforces (`workout_completion/service.py` docstring; CLAUDE.md "Transaction içinde ağ (Bedrock/S3) çağrısı YOK").

**Failure scenario:** On the documented deployment (single gunicorn worker, 8 threads, small DB pool), during a Cognito latency spike each in-flight `/api/v1/auth/refresh` for a family ties up a DB connection *while holding a row lock*. Concurrent refreshes of the same family serialize behind that lock for the full network latency; enough stuck refreshes exhaust the connection pool → app-wide 503s. Blast radius is bounded per-family (the grace/replay path makes honest double-submits cheap), but it compounds findings #2/#3.

**Fix:** Renew the provider token (network) *before* opening the locking transaction, then re-acquire the lock briefly to persist and rotate. The optimistic `version`-guarded UPDATE already present at `mobile_auth.py:479-488` provides the concurrency primitive to do this safely without holding a pessimistic lock across I/O.

---

## 2. [Medium] Mobile auth endpoints make blocking Cognito calls — ungated and unaccounted in the thread-reserve invariant

**Files:** `app/blueprints/mobile_api.py` (no concurrency gate on any route — confirmed absent); `app/services/mobile_auth.py:158,191` (`login` → `cognito_service.authenticate` + conditional `refresh_tokens`), `:429` (`refresh`); client budget `app/services/cognito_service.py:74-76`.

`/api/v1/auth/login` and `/api/v1/auth/refresh` make synchronous Cognito `initiate_auth` calls that can each block a gunicorn web thread for ~20 s under a degraded Cognito, and `login` can chain two of them (~40 s worst case: `authenticate` at `:158` then `refresh_tokens` at `:191`). These endpoints carry **no `ai_concurrency_gate`** and are **not counted** in the `ai_gate` thread-reserve math (finding #3). `login` is reachable pre-auth, throttled only per-IP (`10/min; 50/hour`) + per-username on failures — not globally capped, so distributed or organic bursts aren't bounded.

**Failure scenario:** During a Cognito latency spike, a burst of mobile login/refresh calls holds web threads ~20–40 s each. With 8 threads and a 2-thread reserve that doesn't account for this surface, `/health` starves → the exact false-rollback / restart-loop the reserve was designed to prevent, now reachable from an unauthenticated endpoint.

**Fix:** Put a concurrency gate on the mobile auth routes and/or fold this blocking-network surface into the thread-reserve accounting; consider a global (not just per-IP) cap on concurrent in-flight login attempts.

---

## 3. [Medium] `ai_gate` thread-reserve invariant is breachable by ungated `_model_slots` callers

**Files:** `app/services/ai_gate.py:60` (`_model_slots.acquire()` — no timeout), `:70` (reserve invariant excludes `_model_slots`); ungated callers `app/blueprints/food.py` (`food_search`), `app/blueprints/social.py` (`respond_suggestion`).

*Prior report finding #1 — re-verified unchanged at HEAD by all three audits.*

The A1/I1 "reserve 2 threads for `/health`" guarantee is computed as `WEB_THREADS − (AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY)` = `8 − (4+2) = 2` (`ai_gate.py:70`). But `model_concurrency_slot()` acquires a **third** semaphore, `_model_slots` (default 4), with **no timeout** (`ai_gate.py:60`), and is used by routes **not** behind `ai_concurrency_gate` (`food_search`, `respond_suggestion`). Each request parked on that semaphore holds a web thread.

**Failure scenario:** 4 coach turns hold all `_ai_slots` + `_model_slots`; a cross-user burst of `food_search` cache-misses or `respond_suggestion` calls then blocks on `_model_slots.acquire()` with no timeout → up to 4 more threads parked → all 8 web threads consumed → `/health` queues behind AI work → HEALTHCHECK / deploy gate times out → false rollback or restart-loop.

**Fix:** Give `model_concurrency_slot` a bounded `acquire(timeout=…)` returning a friendly 503 instead of blocking indefinitely; and/or put `ai_concurrency_gate` on `food_search` and `respond_suggestion`; and/or fold `_model_slots` consumers into the reserve invariant.

---

## 4. [Low] Mobile login reveals account-verification state (user enumeration)

**Files:** `app/services/mobile_auth.py:160-164` → `app/blueprints/mobile_api.py:83-91` (`_run_issuance`).

`login()` maps Cognito's `UserNotConfirmedException` to `AUTH_VERIFICATION_REQUIRED` (**HTTP 403**), while invalid credentials map to `AUTH_INVALID_CREDENTIALS` (**HTTP 401**). Because Cognito returns `UserNotConfirmedException` for an unconfirmed username under `USER_PASSWORD_AUTH` largely independent of password correctness, an attacker can submit `{username, "x"}` pairs and use the 403-vs-401 split to enumerate registered-but-unconfirmed accounts. The invalid-credential path already correctly collapses "no such user" and "wrong password" into one response, so only the unconfirmed sub-state leaks; the per-username limiter + per-IP limit throttle bulk probing — hence Low.

**Fix (if hardening desired):** Return a generic `AUTH_INVALID_CREDENTIALS` for the unconfirmed case at the API boundary and surface "resend verification" only after a separately-authenticated step, or enable Cognito's "prevent user existence errors" and reconcile the UX. Note this mirrors the existing web `/verify` behavior.

---

## 5. [Low] `ProxyFix` trusts client-controlled `X-Forwarded-Host` / `X-Forwarded-Port`

**File:** `app/config.py:298` — `ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)`.

*Prior report finding #3 — re-verified still open.* nginx (`nginx.conf`) sets only `Host`, `X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto` — never `X-Forwarded-Host`/`-Port`. Trusting one hop of those two headers lets an external client send `X-Forwarded-Host: evil.com` and overwrite `request.host` / `url_for(_external=True)`.

**Blast radius (why Low):** CSRF Layer-2 (per-session synchronizer token, `hooks.py:194-200`) still holds — cross-origin JS cannot read the token, so this is **not** a CSRF bypass. `x_for=1` is correctly bounded, so `request.remote_addr` (rate-limit keys, `_deep_health_allowed`) stays trustworthy. Impact is limited to self-inflicted external-URL generation (e.g. referral links).

**Fix:** Set `x_host=0, x_port=0` (nginx forwards the correct `Host` natively — nothing lost), or have nginx explicitly set these headers so the trusted proxy, not the client, controls them.

---

## 6. [Low] `plan_facts` is the only plan-data parser that rejects the `{"program": [...]}` wrapper

**File:** `app/services/plan_facts.py:97-114` (`_parse_plan_days`: `if not isinstance(parsed, list): return False, ()`).

Every other `plan_data` parser accepts **both** a bare list and a `{"program": [...]}` dict: `workout_state/queries.py:72-73`, `workout_session/queries.py:73-74`, `workout_state/serialization.py:25-26,45-46`. Only `_parse_plan_days` accepts a list exclusively.

**Failure scenario:** A `TrainingPlan.plan_data` stored in wrapped form (legacy rows, or any alternate writer) renders as `partial_active_plan` in Plan V2 while `/training-plan/active`, `/training/bootstrap`, and the workout-state resolver all render it normally — silent per-consumer divergence. (Not triggered by the current UI, which stores a bare list via `training.js:1027` → `save_training_plan`.)

**Fix:** Mirror the shared parsers — unwrap `{"program": [...]}` in `_parse_plan_days` before the `isinstance(parsed, list)` check.

---

## 7. [Low] `/training/bootstrap` fail-closes to HTTP 500 on a valid plan whose `set`/`sure_dk` exceed serializer bounds

**Files:** `app/services/workout_state/serialization.py:69,79` (`_bounded_int(sure_dk, 0, 1440)`, `_bounded_int(set, 1, 100)` — **raise** on out-of-range) vs. `app/services/training_generation/response_validator.py:62,77` (validator caps `tahmini_kalori` but **not** the upper bound of `set` or `sure_dk`).

The plan validator caps calories and text lengths to match the serializer but leaves `set` (`_to_int(ex.get("set"),1) or 1` — no max) and `sure_dk` (no max) unbounded on write. `serialize_plan` then raises `PlanSerializationError` for `set > 100` or `sure_dk > 1440`, and `training_bootstrap` (`training.py:411-420`) converts that raise into a generic `bootstrap_unavailable` 500.

**Failure scenario:** An AI-generated plan that saved cleanly (e.g. `set: 150`) renders fine via `/training-plan/active` and legacy `training.js`, but 500s the bootstrap endpoint — divergent availability for identical persisted data.

**Fix:** Cap `set`/`sure_dk` in the validator to the serializer bounds, or have `_serialize_day` clamp (not raise) on a large-but-parseable integer (raising remains appropriate for structural violations).

---

## 8. [Low] Completing today's workout terminalizes a stale previous-day ACTIVE session as `COMPLETED`

**Files:** `app/services/workout_session/service.py:271-285` (`resolve_for_completion` returns the id for any non-abandoned session, regardless of `workout_date`) → `app/services/workout_completion/service.py:167` + `workout_session/queries.py:109-119` (`mark_session_completed` flips ACTIVE→COMPLETED checking only `status == 'active'`).

If the client passes the `public_id` of a previous-day session that the lifecycle classifies as stale/`not resumable` (`workout_session/models.py:159-160`), completing *today's* workout marks that stale session `COMPLETED` rather than abandoned.

**Failure scenario:** Yesterday's unfinished (stale) active session + today's completion carrying that stale `session_id` → the stale session is recorded as a genuine completion. No duplicate artifacts (completion identity is user+day), so this is semantic-labeling inaccuracy, not data duplication. Impact is bounded: `FITX_WORKOUT_SESSIONS_ENABLED` defaults OFF and no UI produces this input yet.

**Fix:** In `resolve_for_completion` (or before `mark_session_completed`), only auto-terminalize the linked session when `session.workout_date == command.today.isoformat()`; otherwise route it to the abandon/stale-resolution path.

---

## 9. [Low] OpenAI non-stream coach loop has no per-turn wall-clock budget

**File:** `app/services/ai_coach.py:1013` (`_run_coach_conversation_openai` loop — no deadline guard), vs. the Bedrock loop which does guard it (`ai_coach.py:1105-1124`).

*Prior report finding #4 — re-verified still open.* The OpenAI fallback loop iterates up to `_COACH_TOOL_LOOP_CAP=5` rounds with no deadline; the `chat.completions.create` call passes no per-turn `timeout=`, relying on the client default (`extensions.py:75`: `timeout=30.0, max_retries=2`). Worst case a single fallback turn holds one `_ai_slot` + `_model_slot` + web thread far past the intended budget, bounded only by gunicorn `timeout=300`.

**Fix:** Apply the same `_remaining_coach_turn_seconds` guard and `timeout=min(30, remaining)` as the Bedrock loop.

---

## 10. [Low] God-modules (structure / tech-debt)

**Counts (verified `wc -l`):** `app/services/ai_coach.py` **1211** (grew from 1199 at prior report), `app/blueprints/social.py` **1033** (3 domains: feed/friends/suggestions), `app/blueprints/tracking.py` **728** (~18 routes across dashboard/meal/water/weight/activity/check-in/progress-analytics).

*Prior report #6 (`ai_coach.py`) and #7 (`social.py`) remain open; `ai_coach.py` continues to accrete. `tracking.py` added to the watch-list.* No functional bug — incremental extraction recommended, not urgent.

---

## Verified-clean (audited, no finding — do not re-report as risk)

- **Mobile-auth crypto & lifecycle:** 256-bit random credentials, SHA-256 + `hmac.compare_digest`; HKDF-derived refresh rotation with generation tracking, bounded grace/replay window, `refresh_reuse` → family revocation, optimistic-version-guarded UPDATE. JWT validation pins `algorithms=["RS256"]` (no `alg=none`), enforces `exp`/`iss`/`token_use`/`aud`, fail-closed `jwks_unavailable`→503. Identity reconciliation anchored on `email_verified` + `sub`; account-takeover paths (`username_email_mismatch`/`subject_mismatch`) blocked.
- **Workout completion/session/state (Sprint 7):** completion is atomic (PumpCheck + marker + quest + XP + activity + friend messages under one commit; `award_xp`/`_claim_quest`/`log_activity` genuinely commit-free); `uq_pump_check_day` / `uq_workout_session_active_owner` race handling distinguishes the verified constraint from other `IntegrityError`s and fails closed; every transition re-scopes by `user_id`; opaque `public_id` (`secrets.token_urlsafe(32)`) everywhere, sequential ids never leak. No IDOR.
- **Training-engine layering** `training_history → progression → planning → weekly_program` is genuinely one-directional (grep-confirmed); "pure" layers import no Flask/ORM/DB.
- **Mobile boundary:** Bearer-only, correctly CSRF-exempt (no cookies); `update_streak` and `Cache-Control: no-store` guard/exclude mobile; blueprint registered only under `MOBILE_AUTH_ENABLED` with boot-time fail-closed keyring validation. Fail-closed login (`LOGIN_FAIL_CLOSED`) honored.
- **Mobile-auth migration** `c7d8e9f0a1b2` is expand-only and re-runnable (`has_table()` gate + `_verify_existing` + idempotent `_ensure_indexes`).
- **JWT/session store:** Fernet with enforced `COGNITO_TOKEN_ENC_KEY` outside dev; `get_valid_access_token` re-checks `user_id` ownership; no token/secret values logged.
- **SSRF (`menu_fetch.py`, `s3_helper.py`):** positive `is_global` allow-list, 80/443 port allow-list, per-hop redirect re-validation, DNS-rebinding pin, body-size caps; S3 presigned URLs carry per-user path-segment ownership guard.
- **CSRF/CSP/XSS:** two-layer CSRF on all web writes, no state-changing GET introduced; per-request nonce, `script-src` without `unsafe-inline`, SRI-pinned CDN; coach widget renders model output only through `DOMPurify.sanitize(marked.parse(...))`.
- **Injection:** `fitx_mcp/server.py` parameterized `%s` queries; feed keyset cursors decode to typed values through SQLAlchemy column comparisons — no string SQL.
- **Timezone/date:** Istanbul-day boundaries, `utc_day_bounds`, ISO-week/Sunday-23:59 period math, trailing weekly-window geometry, and weekly_program `None`-vs-`0.0` baseline handling — all consistent and correct.

---

## Suggested priority

1. **#1 and #2 together** — new mobile-surface reliability under Cognito degradation; #1 is a lock-across-I/O anti-pattern, #2 is unauthenticated-reachable thread starvation. Both compound each other.
2. **#3** — long-standing, same failure class, one-line `timeout` fix available.
3. **#9** — pairs naturally with the thread-hold work (per-turn budget).
4. **#4–#8** — low-risk correctness/hardening; batchable.
5. **#10** — schedule incremental extraction; no urgency.
