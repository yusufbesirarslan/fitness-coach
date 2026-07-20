# FitX — Needed Fixes (Triage Report)

**Date:** 2026-07-20
**Scope:** 3 parallel deep-dive audits — (1) Security, (2) Correctness bugs, (3) Architecture/Reliability.
**Method:** Read-only trace of every attack surface and high-risk flow. Every claim was verified against the code, not just CLAUDE.md.
**Commit reviewed:** `022d821` on `claude/amazing-dijkstra-i0rfhl`.

## Headline

This is a **mature, defense-in-depth codebase**. It shows clear evidence of prior
security/correctness review passes (inline `S1–S6`, `H1–H2`, `B16`, `C1`, `A1/A5`,
`I1` triage annotations). **No Critical or High-severity issues were confirmed** in
any of the three audits. Findings are limited to one confirmed low-impact logic bug,
two boot-time hardening opportunities, and some structural tech-debt.

| # | Severity | Area | Type | Confidence |
|---|----------|------|------|------------|
| 1 | Medium | `ai_gate.py` boot invariant only warns | Reliability | Confirmed |
| 2 | Medium | Per-process gates assume `workers=1`, unenforced | Reliability | Confirmed |
| 3 | Low | Hydration nudge divides by logged-days, not window | Correctness | Confirmed |
| 4 | Medium | `ai_coach.py` (1178 lines) god-module | Structure | Confirmed |
| 5 | Low | `social.py` (32 routes) spans 3 product domains | Structure | Confirmed |
| 6 | Low | Inline `WorkoutLog` readers not migrated to `training_history` | Tech-debt | Confirmed |
| 7 | Low | Bedrock 60s timeout is per-call, not per-turn | Reliability | Suspected |
| 8 | Low | Dead quota-counter functions (double-charge trap) | Tech-debt | Confirmed |
| 9 | Low | Search LIKE-pattern wildcards not escaped | Security | Confirmed |
| 10 | Low | Stream quota consumed on immediate client disconnect | Correctness | Suspected (by-design) |

---

## Reliability / Operational

### 1. [Medium] Concurrency-gate boot invariant only *warns* — a bad env ships silently
**File:** `app/services/ai_gate.py:66` (`warn_if_gates_exhaust_threads`), wired at `app/__init__.py:154`

The check that `AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY` leaves at least
`THREAD_RESERVE_MIN` headroom under `FITX_WEB_THREADS` calls `app.logger.warning(...)`
and returns. If an operator sets env values that violate it, the app boots healthy
and the `/health` thread reserve the whole design depends on (A1/I1) is silently gone.
Under load, `/health` then queues behind AI work → Docker HEALTHCHECK / deploy gate
times out → **false rollback**.

**Fix:** Make the invariant **fatal in prod** (raise, like the other `_enforce_*`
guards in `config.py`), keep warn-only in dev. Values are static at import, so hard-gating is safe.

### 2. [Medium] Per-process gates & limiter/cache fallbacks are correct *only* while `workers=1` — nothing enforces it
**File:** `app/services/ai_gate.py:51-53` (in-process `BoundedSemaphore`s); limiter/cache degrade in-memory.

Documented (`ai_gate.py:11-14`, CLAUDE.md), but it's a landmine: a future operator
bumping `gunicorn --workers` to 2 to relieve blocking-AI thread pressure would
**multiply every ceiling by worker count** and quietly break the login throttle and
rate limits — the opposite of the intent, with no error.

**Fix:** Assert `workers==1` at boot and fail loudly (read gunicorn config or a
Dockerfile-set env), or move the gate to a shared Redis semaphore. At minimum, a boot
log asserting the single-worker assumption.

### 7. [Low, Suspected] Bedrock 60s timeout is per provider call, not per coach turn
**File:** `app/extensions.py:99` (timeout=60); loop at `app/services/ai_coach.py:1068`, streaming `app/services/ai_stream.py:78`

The 60s bounds one `messages.create`/`.stream` call. The coach tool loop can run N
rounds → up to N×60s wall, each round holding one of 4 AI slots + one of 8 threads.
Mitigated by burst rate-limit + failure cooldown, but the worst-case single-turn
thread-hold exceeds 60s.

**Fix:** Add a per-turn wall-clock budget (cap total rounds × elapsed) in the
conversation loop, or lower the per-call timeout. *Verify whether the loop already
bounds rounds tightly — if so, downgrade to informational.*

---

## Correctness

### 3. [Low, Confirmed] Hydration nudge divides by logged-days, not elapsed days — under-reports dehydration
**File:** `app/services/analytics_engine.py:205-228` (`_check_hydration`)

The docstring says "average daily water over the last 7 days," but the code computes
`avg_cups = sum(counts) / days` where `days = len({distinct date_keys that have a WaterLog row})` (line 219).

**Failure scenario:** a user logs water on only 2 of the last 7 days, 8 cups each.
`avg_cups = 16/2 = 8 ≥ 6`, so the low-hydration nudge never fires — even though the
true 7-day daily average is ~2.3 cups. Sporadic loggers who are genuinely dehydrated
are systematically missed; the nudge only ever fires for consistent daily loggers.

**Fix:** divide by the intended window (`7`, or `today - min(date_key) + 1`), not the
count of days that happen to have records. Impact is limited to a coaching-prompt
directive (not stored data), hence Low.

### 10. [Low, Suspected — by design] Stream quota consumed with zero output on immediate client disconnect
**File:** `app/blueprints/coach.py:311-314`, `app/services/ai_pipeline.py:245-255`

`reserve_ai_quota` commits the `+1` in the view body *before* the response generator
runs. If the client disconnects before the first token, `generate()` raises
`GeneratorExit` before any `done`/`error` frame, so no refund path runs. The code
comment explicitly treats disconnect as quota-spent ("hak harcanmıştır"). Deliberate
tradeoff — noted for completeness only.

---

## Structure / Tech-debt

### 4. [Medium, Structural] `ai_coach.py` (1178 lines) is a god-module mixing four concerns
33 top-level functions: tool *implementations* (nutrition/workout staging+commit,
memory CRUD, metric queries, ~220-line gym-photo vision `_tool_analyze_gym_photo`),
tool dispatch/schema translation, provider conversation loops (Bedrock + OpenAI +
fallback), and prompt assembly. The pipeline was already modularized, but the tool
implementations still live here, which is why it stayed large.

**Fix:** Extract `_tool_*` implementations into `app/services/coach_tools/` (they're
self-contained, `user_id`-scoped); leave dispatch + provider loops. No behavior change,
shrinks the blast radius. Unrelated failure modes (vision vs provider loop) currently
share a file and test surface.

### 5. [Low, Structural] `social.py` (1032 lines / 32 routes) spans 3 product domains
Feed V2 (items/likes/comments/repost/moderation), friends, direct messages, and
pump-check comments. Business logic is already thin (extracted to `feed`/`friends`/
`notifications` services), but the blueprint is still a catch-all HTTP surface.

**Fix:** Split into `feed.py` / `friends.py` / `messages.py` blueprints when convenient. Not urgent.

### 6. [Low, Tech-debt] Inline `WorkoutLog` readers not migrated to `training_history`
**Files:** `app/blueprints/tracking.py`, `app/services/analytics_engine.py`

Both still query `WorkoutLog` directly instead of routing through canonical
`training_history.fetch_workout_entries` / `is_completion_marker` (CLAUDE.md flags this
as a pending PR). Concrete hazard: the `WORKOUT_COMPLETION_MARKER` exclusion logic is
duplicated — if the marker rule changes in `training_history` but not these inline
readers, analytics and the tracking UI will **silently disagree** on what counts as a
completed workout.

**Fix:** Finish the migration to the single reader — the whole point of the base layer.

### 8. [Low, Tech-debt] Dead quota-counter functions are a future double-charge trap
**File:** `app/services/premium.py` — `record_ai_chat`, `record_ai_plan_generation`, `_record_counter`

No call sites anywhere in `app/**` (grep-verified). Both chat and plan quotas are
consumed purely via `reserve_ai_quota`'s committed increment + refund-on-failure. Not
a bug today, but a trap: a future maintainer wiring these in would double-charge.

**Fix:** Delete the unused functions, or add a comment documenting that `reserve_ai_quota` is the sole counter.

---

## Security (informational — no exploitable finding)

The security audit found **no Critical/High/Medium vulnerabilities**. Verified solid:
Cognito JWT (RS256/JWKS, exp/iss/aud/token_use all checked), session store (Fernet at
rest, user-id-bound), IDOR scoping (every record-by-ID route enforces ownership),
SSRF hardening in `menu_fetch.py` (allowlist + redirect re-validation + DNS-rebinding
guard + port/size caps), CSRF (two-layer Origin + synchronizer token), CSP (nonce-per-
request, no `unsafe-inline`, SRI-pinned CDN), S3 path safety (server-generated keys),
no hardcoded secrets, no SQL/command/template injection, no open redirects, no mass-assignment.

### 9. [Low, Optional] Unescaped LIKE wildcards in user search
**File:** `app/blueprints/social.py:565` — `User.username.ilike(f"%{q}%")`

Not SQL injection (ORM-parameterized), but `%`/`_` in `q` are not escaped, so a user
can pass wildcard chars to broaden matching. Impact minimal (capped at 10 results,
rate-limited, min length 2).

**Fix (optional):** escape `%`/`_`/`\` in `q` before building the pattern.

---

## Recommended order

1. **#1 + #2** — cheap boot-time asserts that convert silent misconfigurations into loud boot failures. Best reliability ROI.
2. **#3** — one-line correctness fix in the hydration nudge.
3. **#8 + #9** — trivial hygiene (delete dead code / escape wildcards).
4. **#4, #5, #6** — structural refactors to schedule as their own PRs.
5. **#7** — verify the tool-loop round cap; add a per-turn budget if unbounded.
