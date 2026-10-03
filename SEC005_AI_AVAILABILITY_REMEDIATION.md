# SEC-005 AI availability and shared-budget isolation

Status: backend branch `security/ai-availability-isolation`; unmerged and undeployed. Production values below are the verified values supplied for this task, not values read from a production service during this work.

## 1. Root Cause

`ai_spend_guard` counted physical provider attempts per user per day and globally per hour/day, but had no shared per-user hourly or burst ceiling. Production used 200 heavy attempts per user/day, 100 globally/hour and 300 globally/day. Thus one account could spend all 100 attempts in one hour; two accounts could spend 200 + 100 of the 300 daily attempts. The Flask route limits are keyed per route, so alternating routes did not create one user-wide hourly pool. The global ceiling became the first effective abuse boundary.

## 2. Current Architecture

```text
web session / mobile Bearer authentication
  -> route Flask-Limiter (per route and owner/IP; mostly 30/hour, heavy 10/hour)
  -> feature work and context construction
  -> ai_provider_call.admit (hard input/output bound)
  -> ai_gate model slot -> ai_spend_guard -> provider attempt
  -> bounded provider retry / recovery / tool round / fallback (each charged)
```

The guard uses Redis `MULTI/EXEC` to increment all applicable fixed UTC-window keys, then compensates a refusal. Concurrent requests cannot both pass an exceeded counter; a race can cause a conservative false refusal. Keys carry the window duration plus five minutes of TTL grace. A past window is ignored when the UTC minute/hour/day bucket changes. Production Redis is configured; local/test can intentionally run without it.

| Entry point / work | Auth / model and call shape | Existing route control and cost |
|---|---|---|
| Web `/ask`, `/ask/stream`; native `/api/v1/coach/messages` | Session/Bearer; Sonnet tool loop, up to 5 rounds; Bedrock transport can retry once per round, with bounded OpenAI fallback; streaming charges each round | Route Coach burst/hour, free weekly chat quota, model slot; high cumulative cost |
| Web `/training-plan`; native `/api/v1/training/plans` | Session/Bearer; Sonnet generation plus at most one repair completion, with bounded retry/fallback | Heavy route limit; high output cost |
| Web `/nutrition-plan`; native `/api/v1/nutrition/plan/generate` | Session/Bearer; Sonnet plan generation, bounded retry/fallback | Heavy route limit; high output cost |
| Web `/api/menu/analyze` | Session; menu extraction, serving estimates and macro fallback batches (up to 80 items, 15 per batch); multiple Sonnet/light attempts and fan-out | Heavy route limit; high cumulative cost |
| Web `/checkin`, `/workout/complete` | Session; Coach feedback or image validation, Sonnet with bounded retries | Heavy route limit; medium cost |
| Native Pump Check and comparison creation; native workout checkpoint AI | Bearer; Sonnet image validation/comparison, bounded retries | Heavy route limit and model slot; medium/high image cost |
| Food search, meal log/review, nutrition helpers, menu OCR | Session/Bearer where exposed; Sonnet or gpt-4o-mini; OCR and macro fallback may fan out | Route controls vary; light/heavy attempt guard remains common |
| Conversation memory summary RQ/deferred job | Server-loaded conversation owner; gpt-4o-mini summary, up to 3 physical attempts | No HTTP route limit; now explicitly charged to DB owner |
| Deep health probe | Internal fixed one-token Bedrock call | Explicitly uncharged exception; no user entry point |

More precise call cardinality by path: `/ask`, `/ask/stream`, native Coach messages and web `/chat` can enter a five-round Coach tool loop; each blocking Bedrock round permits one transport retry, so up to ten Bedrock attempts before any bounded fallback. A single `_heavy_chat` logical completion can make up to four Bedrock attempts (two recovery calls × two physical attempts) and six OpenAI attempts (two recovery calls × three physical attempts). Training generation allows two logical completions (initial and one repair), so its worst case is two such sequences. Nutrition plan generation uses one logical completion. Menu analysis can combine extraction, serving estimation and up to six macro batches (80 items / 15 per batch); each logical completion is bounded separately and the common user guard is the aggregate backstop. A validation/image or meal-review call is one logical completion with its provider's bounded retry count. Streaming rounds are individually admitted but are not replayed after a transport timeout. Background summary is one light logical completion with up to three physical attempts. Cache hits and deterministic branches can make zero model calls. These are upper bounds, not typical costs.

The web session routes use `@require_auth` and `_user_or_ip_key`; native routes use `@require_mobile_auth` and `g.mobile_user.id`. Their Flask-Limiter counters are stored in Redis when configured, with the framework's local fallback; those counters remain per route and are separate from the spend guard. The spend guard key is the server-derived user id plus provider class and fixed UTC window. Its global keys omit user id. Expensive-feature keys additionally include the normalized internal feature name. Free weekly Coach/plan entitlements use the existing database row lock and do not replace the spend guard; premium users share the same attempt isolation.

`ai_provider_call` is the common paid-provider door. The SDK clients have retries disabled. Its own bounded retries charge each physical attempt, including failed attempts; timeout is not retried at that layer. The recovery ladder can make another admitted attempt. Input and output caps differ by feature, so request count alone is not a good cost proxy. Charging physical attempts plus feature-specific input caps and feature daily caps is the chosen practical approximation; actual token use is emitted by the existing usage instrumentation.

## 3. Proposed Architecture

```text
request -> server-authenticated user -> route limit / route concurrency
        -> final payload input/output check
        -> common provider door
        -> user minute, hour, day + expensive-feature day
        -> global hour/day emergency ceilings
        -> provider attempt (including retries and tool rounds)
```

Every Bedrock route shares the heavy user counters. A configured Redis failure now refuses the attempt. Redis-less local/test runs retain local counters. The existing process-level model slots remain; the atomic minute counter bounds concurrent attempts per owner even if many workers race, though it is a burst allowance rather than a held distributed semaphore.

## 4. Limits

| Limit | Previous production | Proposed effective production | Rationale |
|---|---:|---:|---|
| Heavy global hour | 100 | 100 | Emergency ceiling retained |
| Heavy global day | 300 | 300 | Emergency ceiling retained |
| Heavy user day | 200 | 60 | Minimum of configured 200 and 300/5; two users can spend at most 120/day |
| Heavy user hour | none shared | 20 | Minimum of new configured 20 and 100/5; one user can spend at most 20% of hour |
| Heavy user minute | none shared | 5 | Matches the existing Coach 5/minute burst; applies across heavy features |
| Expensive feature day per owner | none shared | 20 attempts per feature | One third of effective user day; applies to training/nutrition plans, menu extraction and vision |
| Light user/global day | 100/500 defaults | unchanged | Separate provider class; no Bedrock budget crossover |

The 1/5 share is an isolation policy derived from the actual 100/300 global ceilings: two accounts can reach at most 40% of either window. The 5/minute value reuses the existing Coach burst behavior. The feature share is a conservative launch default because endpoint-level production distributions are unavailable. The observed 30 Coach turns/day maximum in the existing usage notes is a turn count, not a worst-case attempt count; tool-heavy legitimate users may meet the new ceiling. Tune after staging and production usage review, preserving the share invariant. Code defaults for global limits remain 300/hour and 1500/day; actual effective values depend on deployed configuration.

Configuration: `AI_SPEND_USER_HEAVY_PER_DAY`, `AI_SPEND_USER_HEAVY_PER_HOUR`, `AI_SPEND_USER_HEAVY_PER_MINUTE`, `AI_SPEND_GLOBAL_HEAVY_PER_HOUR`, `AI_SPEND_GLOBAL_HEAVY_PER_DAY`, and existing light limits. Invalid numeric settings now fail at import. Heavy limits cannot be disabled with zero. `AI_SPEND_GUARD_ENABLED` remains the pre-existing explicit operator switch and must remain enabled.

## 5. Implementation Changes

| File | Function/class | Change and reason |
|---|---|---|
| `app/services/ai_spend_guard.py` | `LIMITS`, `_planned_keys`, `charge`, scoped identity helpers | Shared per-user minute/hour/day and expensive-feature counters; user hour/day constrained by global share; configured Redis failure refusal; strict configuration parsing |
| `app/services/ai_provider_call.py` | `Admission.create`, `admit` | Carry feature through the common model gate and charge retries/ungated calls to the same feature counters |
| `app/services/ai_coach.py` | Coach provider functions | Bind the server-owned user argument and reject a mismatch with an authenticated owner |
| `app/jobs/tasks.py` | `summarize_conversation` | Bind summary cost to DB conversation owner outside request context |
| `tests/conftest.py`, `tests/test_ai_spend_guard.py`, `tests/test_jobs.py` | regression and isolation tests | Reset hermetic local counters and pin the auth fail-closed test setting; reproduce SEC-005, then cover hour/burst/feature/identity/races/Redis/job attribution |
| `.env.example`, `docs/RATE_LIMITING.md` | operations docs | Expose new settings and failure mode |

## 6. Security Properties

- One owner is bounded to 20 of 100 heavy attempts/hour and 60 of 300/day under verified production settings. Two owners are bounded to 120/day.
- User identity comes from Flask-Login or `g.mobile_user`, populated by the existing auth middleware. Headers do not choose a bucket. The RQ summary owner comes from the database conversation row.
- User, feature and global counters increment atomically in one Redis transaction; concurrent admission cannot overshoot an effective counter. Refusal compensation may cause a temporary false refusal under a race, never excess admission.
- All routes reach `ai_provider_call`; feature and user counters sit at that common door. Tool rounds, fan-out and retries each charge an attempt before dispatch. Existing bounded retry counts remain.
- A configured Redis outage returns the existing capacity error path (web busy/soft response or native retryable 503). Log and bounded metrics distinguish `user`, `feature`, `global` and `backend` without exposing ceilings to clients. An internal Coach owner mismatch is classified as `identity`.

## 7. Test Results

The SEC-005 regression was run before implementation and failed: user C was refused by the global daily cap after A/B filled it. After implementation, the same test passes. Focused command: `.venv/bin/python -m pytest -q tests/test_ai_spend_guard.py tests/test_ai_provider_call.py tests/test_jobs.py tests/test_mobile_coach_api.py tests/test_mobile_training_generation_api.py tests/test_mobile_nutrition_api.py tests/test_ai_gate.py tests/test_memory_manager.py tests/test_premium_quota.py tests/test_coach_routes.py tests/test_ai_coach.py tests/test_coach_tools.py tests/test_coach_plan_clarification_continuity.py` — **564 passed**. Follow-up auth/guard/provider/job run — **205 passed**. Tests use fake providers/Redis; no Bedrock invocation. A full-suite sandbox run was stopped after Chromium could not launch (`MachPortRendezvousServer: Permission denied`). An unsandboxed full run then reached 47% with no failure after pinning `LOGIN_FAIL_CLOSED=1` in the hermetic fixture; it was stopped during a slow browser test. The full suite therefore has no complete pass claim. `git diff --check` passed.

## 8. Mobile Compatibility

Mobile code changed: **NO**. Xcode changed: **NO**. API request contract changed: **NO**. Mobile-required response contract changed: **NO**. Native auth, URLs, bodies and typed busy/retryable envelopes remain in the backend. Existing mobile error mappers were inspected read-only. No mobile branch or PR was created.

## 9. Remaining Risks

An account farm or coordinated users can still reach the global breaker. Limits need tuning against real attempt distributions, especially Coach tool rounds and image/menu workflows; the documented observations are sparse. Provider attempts are counted, while tokens vary within the existing hard feature bounds. Redis transaction compensation can temporarily overcount during a race. Local/test Redis-less mode is process-local and must not be used as a production substitute. Existing route-level preparation may do CPU/DB work before provider admission, although no paid model call occurs first.

## 10. Production Rollout Plan (design only)

1. Merge this backend PR after review; leave mobile untouched.
2. Keep `AI_SPEND_GUARD_ENABLED=1`, Redis configured, and existing production global 100/hour and 300/day. Set/verify heavy user hour 20 and minute 5; leave existing user day 200 because the global-share rule makes its effective value 60. Validate configuration at boot.
3. In staging, exercise Coach, plan, menu, Pump Check, concurrent calls, retries, hourly/daily rollover and a controlled Redis outage with fake or staging providers. Confirm native busy envelopes.
4. If a canary is available, watch rejection metrics by scope/class and usage attempts/tokens per feature; monitor legitimate users before broad rollout. No AWS resources are added by this PR.
5. Roll back the backend release if legitimate use is impaired; preserve the global emergency limits throughout. Revert configuration together with code only after review.
6. Validate production through safe telemetry and client smoke flows; do not run abuse/load tests or consume quota for validation.

This work made no production deploy, AWS mutation, Redis/DB mutation or Bedrock call.
