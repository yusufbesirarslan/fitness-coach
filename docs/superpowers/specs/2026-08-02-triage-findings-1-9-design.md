# PR #194 Findings #1-#9 Design

**Date:** 2026-08-02
**Branch:** `fix/triage-findings-1-9`
**Base:** latest `origin/main` at worktree creation (`95d94cd063e55d22fba7f63e8a575743e8a4f4b8`)
**Source:** PR #194, `NEEDED_FIXES_2026-08-02.md`

## Scope

Implement and verify the nine concrete security, reliability, and correctness findings in PR #194. The changes stay local to the affected services, routes, configuration, and tests. Broad extraction of `ai_coach.py`, `social.py`, and `tracking.py` from finding #10 is excluded.

Small local refactors are permitted only where required to establish a safe transaction boundary, isolate blocking work, or make a behavior testable. Finding #10 will be described as a separately sequenced follow-up after findings #1-#9 pass focused and full verification.

## Cross-cutting concurrency design

The existing `_ai_slots` capacity remains the single shared web-thread budget for synchronous blocking provider work. Mobile Cognito calls and the previously ungated food/suggestion callers will use that same capacity instead of adding a new semaphore to the reserve equation. The existing invariant remains:

`FITX_WEB_THREADS - (AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY) >= 2`

`AI_MAX_CONCURRENCY=4`, `SCRAPE_MAX_CONCURRENCY=2`, and `FITX_WEB_THREADS=8` therefore continue to reserve two web threads. `_model_slots` remains an inner provider-level fan-out bound; it does not add independently consumable route capacity.

All shared-gate and model-gate acquisition uses an absolute deadline derived from `time.monotonic()`. The acquisition budget is exactly `AI_GATE_WAIT_SECONDS`, whose default remains `0.0` seconds. A zero budget is a non-blocking attempt. Positive configured budgets are measured against the monotonic deadline and never restarted by retries or spurious wakeups.

The gate is acquired immediately before blocking external work, never while a database lock or transaction is open, and is released in `finally` on success, fallback, or exception. Code that already owns the shared gate may acquire `_model_slots` only in the established order `shared blocking gate -> model gate`; no path may acquire them in the reverse order. Saturation tests will prove fail-fast behavior, permit release, preservation of the two-thread reserve, and absence of nested-semaphore deadlock.

Route-specific saturation contracts are fixed as follows:

- Mobile login/refresh: return the existing versioned mobile envelope with code `AUTH_TEMPORARILY_UNAVAILABLE`, HTTP 503, `retryable: true`, and `Retry-After: 15`. No Cognito call begins when acquisition fails.
- Food search cache miss: preserve the established search fallback with HTTP 200 and `{"results": []}`. Cache hits do not acquire a permit.
- Accepted meal suggestion: preserve the existing calculation-failure contract. The suggestion is accepted, no `MealLog` is written, and the HTTP 200 response uses `route.accepted_no_calc`. Declines, invalid requests, workout suggestions, and work satisfied entirely from local cache do not acquire a permit.
- Model-slot saturation inside an already admitted request is bounded by the same monotonic acquisition budget and raises an internal busy signal. Existing provider/route fallback handling translates that signal; it must not park another web thread or emit a new generic public payload.

## Finding designs

### #1 — Mobile refresh: no database transaction across Cognito I/O

Provider renewal becomes a strict two-phase operation.

Phase one reads and validates the refresh parent/family, captures an immutable snapshot containing the family row id, public family id, original `version`, parent id/generation, subject, Cognito username, encrypted provider refresh token, provider expiry, and absolute expiry, and then explicitly ends the database transaction. No row lock survives this phase.

If provider renewal is required, the code acquires the shared blocking permit only after the transaction is closed, decrypts the snapshotted provider credential, calls Cognito, validates the returned access token and subject, checks coverage, and prepares encrypted token values. The permit is released in `finally`. There is no open SQLAlchemy transaction or `FOR UPDATE` lock during any Cognito network I/O.

Phase two opens a new transaction, locks the family and refresh parent, and repeats all security-sensitive validation. It compares the locked family id/version and parent generation/state with the original snapshot. Prepared provider tokens are persisted only when that snapshot is still current. The existing optimistic version-guarded family update remains the final write guard.

If another request consumed the parent, changed the family version, revoked/expired the family, or otherwise invalidated the snapshot, the externally issued tokens are discarded. The loser follows the existing replay-safe or stale/conflict contract and never partially updates provider tokens, creates a second child, overwrites the winner, or revokes valid winner credentials merely because its external response arrived later.

A real two-request race test will synchronize both refresh calls after they read the same initial family/version and before phase-two persistence. It will use distinct mocked Cognito token results and separate request/database contexts. Assertions: exactly one child generation is committed, the family version increments once, the provider token belongs to the winner and is never overwritten by the loser, no partial rows remain, and the loser returns the existing replay-safe/stale contract.

### #2 and #3 — Shared blocking capacity and bounded model acquisition

Introduce a small reusable context manager in `ai_gate.py` for the existing `_ai_slots` semaphore. It exposes a typed internal saturation signal instead of constructing a generic response, allowing each caller to preserve its public contract. It uses monotonic absolute-deadline acquisition and unconditional `finally` release.

Mobile login holds one shared permit across its contiguous Cognito authentication/optional renewal sequence; local identity persistence begins only after release. Mobile refresh uses it only for the phase-one-to-phase-two Cognito renewal gap described above. Refreshes that do not need Cognito renewal do not consume a permit.

Food search performs validation and cache lookup first, then gates only `_coach_search_food` on a cache miss. Meal suggestion handling separates calculation from persistence: it snapshots required message/sender data and closes the read transaction; gates only uncached FatSecret/LLM calculation; then starts a new short transaction to conditionally claim the suggestion and persist the reply/meal. This is a narrowly scoped extraction required to avoid acquiring a semaphore or doing network I/O while a database transaction is open.

`model_concurrency_slot()` gains the same bounded monotonic acquisition behavior. Tests saturate `_ai_slots` and `_model_slots` in realistic acquisition order, start concurrent newly gated callers, and prove they return immediately under the configured budget, do not consume the reserved two threads, do not call providers, and recover after permits are released. A nested-acquisition test proves an admitted shared-gate caller can enter and leave the model gate without deadlock or permit leakage.

### #4 — Indistinguishable unconfirmed-account login

`UserNotConfirmedException` maps publicly to the same `AUTH_INVALID_CREDENTIALS`, HTTP 401, non-retryable response as a wrong username/password. The response code, message, status, fields, and timing-visible control path remain as similar as practical.

Internal observability retains a low-cardinality security event/reason such as `unconfirmed_account`, but logs only the event category, request id, and safe reason. It must not log the submitted username/password, Cognito token material, raw provider exception text, or sensitive Cognito response details.

Tests compare the full public envelopes and statuses for unconfirmed and invalid credentials and separately assert the safe internal event is emitted without submitted secrets.

### #5 — ProxyFix trust boundary

Configure `ProxyFix` with `x_for=1`, `x_proto=1`, `x_host=0`, and `x_port=0`. Nginx already forwards the canonical `Host` header directly but does not overwrite `X-Forwarded-Host` or `X-Forwarded-Port`; the application must therefore ignore those client-controllable forwarded headers.

Regression tests send hostile forwarded host/port headers and verify `request.host` and external URL generation continue to use the trusted direct host while forwarded client address/protocol behavior remains intact.

### #6 — Wrapped plan facts

`_parse_plan_days` accepts either a bare list or an object whose `program` field is a list, matching the other plan consumers. Missing, malformed, non-list, or empty wrappers retain the existing partial-plan behavior; no new coercion is introduced.

### #7 — Independent plan field bounds

The two fields and valid inclusive ranges are:

- Day-level `sure_dk`: integer `0..1440`.
- Exercise-level `set`: integer `1..100`.

They are independent; there is no combined `1..100` or ambiguous `1100` range. The generation validator clamps parseable values before persistence and handles zero without truthiness-based defaulting. Tests cover each lower boundary, upper boundary, below-lower value, and above-upper value independently before persistence. Serializer-facing regression tests also prove validator output cannot cause `/training/bootstrap` to fail. Structural/type violations retain the serializer's existing fail-closed behavior.

### #8 — Previous-day workout linkage rejection

`resolve_for_completion` receives the authoritative completion day and returns `SessionOutcome.STALE_SESSION_REQUIRES_RESOLUTION` when the linked active session's `workout_date` is not that day. The caller preserves the existing outcome/view contract.

The rejection path is read-only: it does not mark the old session completed or abandoned, create/reassign a session for today, write completion artifacts, or silently substitute a different session id. Tests snapshot relevant row counts and state before the call and verify no persistence, completion, reassignment, or commit-visible mutation afterward.

### #9 — One coach-turn wall-clock deadline

`_run_coach_conversation` creates one absolute deadline with `time.monotonic()` before the first provider call and passes it through Bedrock, every Bedrock retry/tool round, fallback selection, and OpenAI. Helpers may create a deadline only when called directly by existing unit tests; the normal orchestration path always supplies the single shared deadline.

Before every provider retry or fallback, code computes `remaining = deadline - time.monotonic()`. When `remaining <= 0`, it returns the established localized tool-error/fallback response and does not invoke OpenAI. Every OpenAI call receives `timeout=min(30.0, remaining)`. A fallback from Bedrock consumes the same budget rather than resetting it. Tests use a controlled monotonic clock to prove decreasing timeouts, no call with non-positive budget, no OpenAI fallback after Bedrock exhausts the turn, and unchanged behavior when positive budget remains.

## Test-first execution and verification

Each finding follows red-green-refactor:

1. Add the smallest regression test that would fail on `origin/main`.
2. Run that focused test and record the expected failure.
3. Implement the minimal local change.
4. Re-run the focused test and the affected module's existing tests.
5. Refactor only while green.

Focused coverage will include mobile auth service/API and its real race, gate saturation/nesting and affected route contracts, ProxyFix behavior, plan facts, training generation/bootstrap serialization, workout session/completion authority, and coach provider deadlines. All pytest invocations use the writable worktree-local base temp directory under `.pytest_cache` because the inherited Windows temp directory is not writable in this environment.

After every finding passes focused verification, run the complete suite with the same writable `--basetemp`. Baseline evidence is `2742 passed, 5 skipped, 3 deselected` plus successful representative reruns for all 40 inherited temp-fixture setup errors after switching to the writable base temp. Any new failure is investigated before completion is claimed.

## Finding #10 follow-up recommendation

Finding #10 is not implemented in this branch. After findings #1-#9 are merged and observed, create separate behavior-preserving PRs with characterization and dependency-boundary tests:

1. Extract provider-loop/deadline and tool-orchestration responsibilities from `ai_coach.py`, building on the now-tested deadline boundary.
2. Split `social.py` by feed, friendship, and suggestion domains; preserve the small calculation/persistence seam introduced for #3.
3. Split `tracking.py` by dashboard, nutrition/hydration, body/activity, and check-in analytics.

Each extraction should be independently reviewable, make no product behavior changes, and run focused plus full verification before the next module begins.
