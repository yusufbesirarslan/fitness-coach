# FitX — Needed Fixes (Triage Report)

**Date:** 2026-11-01
**Commit reviewed:** `f438459` on `claude/amazing-dijkstra-wv614g` (== `main` HEAD; through R6 forward-recovery `#477`/`#481`, Apple dual-environment + reconciliation, D1 Phase 2 `#473`, LP21 general cardio `#472`).
**Scope:** 3 parallel read-only deep-dive audits — (1) Security (authz/IDOR, SSRF, auth boundary, CSRF, PII leakage, injection, S3/upload, HMAC tokens); (2) Concurrency / transaction boundaries / data integrity (lock ordering, idempotency/replay, money & subscription ledger, gamification, day-key correctness); (3) Correctness / error-handling / API contract / structure (mobile API surface, nutrition math, workout/plan state, AI pipeline, feature flags).
**Method:** Adversarial trace of each attack surface and high-risk flow. Every new finding was verified against actual source (cited `file:line`), not against CLAUDE.md/docstrings. The four prior triage reports (`NEEDED_FIXES.md` 2026-07-21, `NEEDED_FIXES_2026-08-02.md`, `NEEDED_FIXES_2026-08-14.md`, `TRIAGE_FINDINGS_2026-10-01.md`) were skimmed first; already-fixed items are not re-reported, still-open ones are carried forward.

## Headline

The codebase remains **mature and defense-in-depth**. **No new Critical or High-severity issues were found** by any of the three audits — consistent with all four prior rounds. The classic attack classes (IDOR, JWT/alg confusion, CSRF, SSRF, prompt injection, upload abuse, SQL injection, the workout/completion & money/allowance race surfaces) all have explicit, correct mitigations verified by code trace this run.

This run surfaced **1 Medium** contract bug (new), **4 Low** items (3 new, 1 security-hardening), and refreshed the status of the long-standing carried-forward items — including one prior Low now **resolved**.

| # | Severity | Area | Type | Status | Confidence |
|---|----------|------|------|--------|------------|
| 1 | Medium | Replacement-proposal endpoint flags transient 429/503 as **non-retryable** (contradicts its own sibling + the `retry_after` it sends) | Contract / client-retry | **New** | Confirmed |
| 2 | Low | Same endpoint coerces bare `ValueError` into a retryable 503 persistence error | Correctness (defensive over-breadth) | **New** | Confirmed |
| 3 | Low | Meal-photo cleanup-intent row held `FOR UPDATE` across an un-gated S3 `DeleteObject` | Reliability / thread-starvation | **New** | Confirmed |
| 4 | Low | `meal_idempotency.commit_once` may misclassify a non-idempotency `IntegrityError` as a replay | Correctness (latent) | **New** | Plausible |
| 5 | Low | Wearables OAuth `state` compared non-constant-time | Security hardening | **New** | Confirmed (non-exploitable) |
| — | Resolved | `ProxyFix` trusted client `X-Forwarded-Host`/`-Port` | Security | Prior 07-21 #3 / 08-02 #5 / 08-14 #6 — **now fixed** | Confirmed |
| C1 | Medium | `ai_gate` thread-reserve breachable by ungated `_model_slots` callers | Reliability / DoS | Prior 07-21 #1 / 08-02 #3 / 08-14 #4 — **open** | Carried |
| C2 | Medium | `mobile_auth.refresh()` holds `FOR UPDATE` row lock across a synchronous Cognito call | Reliability / DoS | Prior 08-02 #1 / 08-14 #5 — **open** | Carried |
| C3 | Medium | `weekly_water` challenge completable in a single day by toggling the water count | Correctness / gaming | Prior 07-21 #2 / 08-14 #1 — **open** (never fixed) | Carried |
| C4 | Low | OpenAI non-stream coach loop has no per-turn wall-clock budget | Reliability | Prior 07-21 #4 / 08-02 #9 / 08-14 #7 — **open** | Carried |
| C5 | Low | God-modules: `ai_coach.py` (now **1592**), `social.py` (1222), `tracking.py` (836) | Structure | Prior — **open**, still growing | Carried |

> **Recommended order of fixes:** #1 first — it is a small, self-contained contract divergence with a correct in-repo template to copy (`create_training_plan` in the same file) and real client-retry impact. Then #3 (one-line slot wrap), then #2. #4/#5 are defense-in-depth. The carried `C*` items are tracked by their original reports and unchanged.

---

## New findings

### 1. [Medium] Replacement-proposal endpoint flags transient capacity/rate-limit failures as NON-retryable

**File:** `app/blueprints/mobile_training.py:221-223` (`RateLimitExceeded`) and `:226-227` (`BlockingConcurrencyLimit`), in `create_replacement_proposal` (`POST /api/v1/training/plans/replacement-proposals`).

```python
# :221
except RateLimitExceeded as error:
    return mobile_error('TRAINING_PLAN_RATE_LIMITED', 'Too many Training plan generation requests.', 429, False,
                        retry_after=error.limit.limit.get_expiry())
# :226
except BlockingConcurrencyLimit:
    return mobile_error('TRAINING_PLAN_GENERATION_BUSY', 'Training plan generation is temporarily busy.', 503, False)
```

`mobile_error(code, message, status, retryable, retry_after=None)` — the 4th positional is `retryable`, so both branches declare these transient conditions **permanent**.

The canonical primary generation endpoint `create_training_plan` in the **same file** (`:87-95`, `:99-106`) returns the **identical** conditions with the **same codes and messages** but `retryable=True` + `retry_after=15`. Every other sibling agrees transient → retryable: `mobile_nutrition_closure.py:151` (429 True), `mobile_weekly_checkin.py:83` (429 True), and the shared `mobile_ai_concurrency_gate` (`ai_gate.py`) which returns `"retryable": True` for its 503 busy (pump-checks, coach).

- **Concrete failure:** `BlockingConcurrencyLimit` is raised by `blocking_concurrency_slot()` **before** any provider call or spend (`_native_generation_provider_guard`) — nothing happened, retry is safe and cheap. A native client generating a replacement proposal during momentary concurrency saturation (single worker, 8 threads) reads `retryable=false` and treats a purely transient capacity rejection as a permanent failure, so it does not retry. The 429 branch is **self-contradictory**: it sets `Retry-After` (telling the client *when* to retry) while the envelope says *don't*.
- **Blast radius:** bounded to the replacement-proposal flow and to client retry UX — no data integrity impact.
- **Fix:** make both branches `retryable=True` and add `retry_after=15` to the `BUSY` branch, matching `create_training_plan:99-106` exactly.

### 2. [Low] Same endpoint coerces a bare `ValueError` into a retryable 503 persistence error

**File:** `app/blueprints/mobile_training.py:228-230`

```python
except (SQLAlchemyError, ValueError, mobile_training.PlanUnprojectable):
    db.session.rollback()
    return mobile_error('TRAINING_PLAN_PERSISTENCE_UNAVAILABLE', 'Training is temporarily unavailable.', 503, True)
```

A `ValueError` surfacing from proposal parsing/generation (not a storage fault) is reported as a transient *storage* problem the client should retry. A genuine malformed-input `ValueError` would then loop against an input that can never succeed. `generate_proposal` mostly uses typed errors, so a live raw-`ValueError`-on-client-input path is not proven — this is defensive-catch over-breadth rather than a confirmed loop (hence Plausible on the trigger, Confirmed on the handler shape). **Fix:** drop `ValueError` from this catch tuple, or split it to a 400/non-retryable classification; keep `SQLAlchemyError`/`PlanUnprojectable` as the retryable 503.

### 3. [Low] Meal-photo cleanup-intent row is held `FOR UPDATE` across an un-gated S3 `DeleteObject`

**File:** `app/services/mobile_diary_mutation/service.py:233-250` (`_release_owned_object`), lock taken in `_claim_cleanup` (`:195-217`), S3 call at `:238`; operator path `drain_meal_photo_cleanups`.

`_claim_cleanup` takes `SELECT … FOR UPDATE` on the `MealPhotoCleanup` row, and the lock is held while `s3_helper.delete_meal_photo()` runs — an S3 network round-trip that is **not** wrapped in `blocking_concurrency_slot`. The module docstring is careful that no lock is held across S3 for the *MealLog* row (released by the pre-release commit), but the cleanup-intent lock is a *different* lock and is deliberately held across the call (the docstring acknowledges this).

- **Interleaving:** user issues `DELETE /meal-log/entry/<token>`; the MealLog row commits, then `_release_owned_object` locks the cleanup row and calls S3. A concurrent **retry of the same delete** for the same entry blocks in `_claim_cleanup`'s `FOR UPDATE` for the full S3 duration, parking one of the 8 gunicorn web threads. The cross-actor claim lock is *necessary* for correctness (it prevents duplicate `DeleteObject`); the issue is only that a hung/slow S3 delete can starve web threads, mildly violating the repo rule "the slot wraps only the network turn; never hold a DB lock across network I/O."
- **Blast radius:** bounded — requires the same user hammering the same entry-delete concurrently, and S3 deletes are normally sub-second.
- **Fix:** wrap the `delete_meal_photo` call in `blocking_concurrency_slot()` (as every other S3/provider turn is) so a hung release cannot starve threads, or document the accepted trade-off explicitly.

### 4. [Low · Plausible] `meal_idempotency.commit_once` may treat a non-idempotency `IntegrityError` as a replay

**File:** `app/services/meal_idempotency.py:27-39`

On any `IntegrityError` the function rolls back and re-queries `find_existing(user_id, key)`; if a row with that key exists it returns it as the "replay winner" — even when the actual violation was a *different* constraint (FK / NOT NULL / unrelated unique). In practice `MealLog` has only the `(user_id, idempotency_key)` unique constraint relevant here and callers pre-check `find_existing`, so reaching this with a pre-existing same-key row is essentially the genuine idempotency race (hence Plausible, very low blast radius). **Fix (stricter):** inspect the violated constraint's name/columns before treating it as a replay, the way `workout_completion/queries.py::_constraint_identity` already does.

### 5. [Low · Hardening] Wearables OAuth `state` compared non-constant-time

**File:** `app/blueprints/wearables.py:101` — `if returned != expected:`. The `state` is 32+ bytes of server-generated, short-lived entropy, so network timing recovery is impractical; this is a defense-in-depth nit, not an exploitable issue. **Fix (optional):** use `secrets.compare_digest`, matching the CSRF token path in `app/hooks.py`.

---

## Prior-report item resolved this run

- **`ProxyFix` trusting client `X-Forwarded-Host` / `-Port`** (prior 07-21 #3, 08-02 #5, 08-14 #6) is **fixed**: `app/config.py:327` now sets `x_host=0, x_port=0` on the `ProxyFix` middleware. Remove this from the carried-forward list.

## Carried-forward open items (unchanged; tracked by prior reports)

These were deliberately **not** re-traced in depth this run (they are reliability/correctness items already recorded, not in the new audits' primary scope). Status is per their original reports.

- **C1 [Medium] `ai_gate` thread-reserve breachable by ungated `_model_slots` callers** — open since 2026-07-21 (#1), re-verified open 08-02/08-14.
- **C2 [Medium] `mobile_auth.refresh()` holds a `FOR UPDATE` row lock across a synchronous Cognito network call** — open since 2026-08-02 (#1). (Same class as new finding #3, but with a ~20 s worst-case network window — higher impact.)
- **C3 [Medium] `weekly_water` challenge completable in a single day by toggling the water count** — the water funnel gates on a `count>0 && prev==0` transition rather than a durable per-day marker; re-confirmed 2026-08-14 (#1), still **not fixed**. Canonical correct pattern exists in the same codebase (`WaterLog.quest_fired` conditional UPDATE). Worth closing — it is a concrete integrity/gaming bug, not a theoretical one.
- **C4 [Low] OpenAI non-stream coach loop has no per-turn wall-clock budget** — open since 2026-07-21 (#4).
- **C5 [Low] God-modules** — `ai_coach.py` has grown to **1592 lines** (was 1199 at the first report, ~33% growth) and remains the single place an LLM can trigger persistent user-data writes; `social.py` 1222, `tracking.py` 836. Still open.

## Structure notes

- **`feature_flags.py` `depends_on` is documentation-only — not enforced anywhere.** Verified: `.depends_on` is never read in `app/` outside the dataclass definition and the registry entries; `resolve_rollout_flags` validates only each flag's own `0/1` value, and `app/config.py` / `app/__init__.py` never consult it. Flag dependencies (e.g. `AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED` → `AI_ALLOWANCE_LEDGER_ENABLED`) are enforced, if at all, by per-service fail-closed checks at call time. This is a deliberate design, but there is no central backstop: a dependent flag enabled without its prerequisite relies on every consumer remembering the guard. A boot-time validation (fail boot if an enforceable backend flag is on while a `depends_on` prerequisite is off) would turn folklore into an invariant at essentially zero runtime cost.
- **Service-layer proliferation.** `app/services/` now holds ~**290** `.py` files (CLAUDE.md still says 135). The one-owner "authority" + AST-gate discipline is visibly working and is the right pattern, but the raw count is itself a navigation/onboarding cost worth tracking; consider a top-level `app/services/README.md` index grouping the authorities by domain.
- **`ai_coach.py` split** (see C5) is the highest-value structural change: provider loop / tool executor / context assembly are separable, and it is the riskiest module (LLM-triggered writes).

---

## Appendix — surfaces verified clean (do not re-report as risk)

**Security:** `can_view_pump_check` stale-authz closed (friend-still-accepted re-check); `nutrition/diary.py` `CustomMealItem` loads re-check `custom_meal_id` against an owner-scoped meal; mobile pump-check comparison `_owned_source` is owner+token scoped (cross-owner == 404) and asserts `key_belongs_to_user`; all mobile blueprints derive owner from `g.mobile_user` only. **SSRF:** `menu_remote.py` — scheme allowlist, all-resolved-IPs-public check, connection pinned to the vetted literal IP with connected-peer re-verification (DNS-rebinding TOCTOU closed), per-hop redirect revalidation with HTTPS→HTTP downgrade refusal, killable credential-less subprocess. **Auth:** `cognito_jwt` pins `RS256`, checks `iss`/`token_use`/`aud`/`exp`, request-time leeway hardcoded 0; unknown-kid JWKS refresh single-flight + cooldown. **CSRF:** two-layer (Origin/Referer + per-session synchronizer via `compare_digest`), correctly skipped only for Bearer `mobile_api`. **Injection:** only parameterized binds; the two `SET LOCAL lock_timeout = '{n}ms'` interpolations are int-validated in a bounded range. **S3:** segment-equality ownership, server-selected bucket, strict key-grammar delete primitives, `expected_user_id` on presign/get. **HMAC tokens:** per-class domain separation, `compare_digest`, owner bound through the MAC only, versioned labels, TTL + skew bounds.

**Concurrency/money:** lock order (operation → user → plan; session → completion-day → artifacts → User) obeyed by every writer traced; `ai_consent.dispatch` separate-connection User `FOR UPDATE` never collides with main-session ledger locks; every Bedrock/S3/Cognito/FatSecret/Apple call runs outside the DB transaction and outside row locks; allowance ledger reserve/finalize, `advance_checkpoint` CAS, Apple receipt dedup are all DB-unique-arbitrated; `ai_spend_guard` admit is Redis MULTI/EXEC-atomic with compensating decrement; `premium.reserve/refund_ai_quota` use `populate_existing().with_for_update()` to avoid stale-identity-map lost updates; the 2026-09-30 gamification commit-semantics fixes hold; no production `date.today()`/`utcnow().strftime` day-key usage.

**Correctness:** `nutrition_targets.py` + consumers — null-vs-0 consistent (never a fabricated 2000 kcal), division guards present; `nutrition_day_view.py` reads MealLog only (no CustomMeal double-count); `build_today` wraps every fault into a typed `TodayUnavailable` and never falls through to the auth catch-all; `today_guidance.py` fails closed on unknown/incompatible state/action pairs; `ai_pipeline`/`response_formatter` enforce the B-rule (no provider switch after first delta or tool side effect) and gate quota refund on proven zero-attempt, consistently across blocking and stream paths; every new mobile blueprint wraps faults in route-local typed errors rather than `AUTH_TEMPORARILY_UNAVAILABLE`.

---

*Generated by three parallel read-only deep-dive triage agents + direct source verification of every new finding. No source files were modified by the audit; this report is the only artifact.*
