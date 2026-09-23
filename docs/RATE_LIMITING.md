# Rate Limiting & Abuse Control (Sprint 4 WS7)

Four layers protect the AI paths, from cheapest to most specific. All are
per-user (keyed by user id when authenticated, else client IP).

| Layer | Where | Limit / trigger | Response |
|---|---|---|---|
| Sustained rate | `AI_RATELIMIT` | 30 / hour on `/ask*` | 429 |
| Burst | `AI_BURST_RATELIMIT` | 5 / minute on `/ask*` | 429 |
| Weekly quota | `premium.reserve_ai_quota` | `FREE_WEEKLY_AI_CHATS` (free tier; premium unlimited) | 402 `premium_required` |
| Failure cooldown | `ai_recovery` | `AI_FAILURE_THRESHOLD` consecutive AI failures | 429 + `Retry-After` |

Flask-Limiter (`_user_or_ip_key`) enforces the sustained + burst limits;
`in_memory_fallback_enabled` keeps them working (process-local) if Redis is down.

## Weekly quota

Per-user weekly counter in `User.user_metadata` JSON, Istanbul ISO-week key, with
`SELECT FOR UPDATE`. `/ask*` reserves one chat up front and **refunds** it when the
provider returns a friendly error-fallback (so a failed reply doesn't cost the
user). Premium users are unlimited. Kill switch: `AI_CHAT_QUOTA_ENABLED=0`.

## Failure cooldown (WS9/WS7)

Per-user consecutive AI failures are counted in Redis (`ai:failstreak:<uid>`). When
the streak reaches `AI_FAILURE_THRESHOLD` (default 3), a cooldown key
(`ai:cooldown:<uid>`, NX + EX) is armed for `AI_FAILURE_COOLDOWN_SECONDS` (default
60). While it's set, `/ask*` returns **429 + `Retry-After`** — checked **before**
reserving quota, so a cooling-down user spends no weekly right. The first success
clears the streak.

- Purpose: when a provider is genuinely down, stop replaying expensive tool loops
  for a user who keeps retrying.
- Redis-less (local/test): the cooldown is a **no-op** (fail-open — never blocks).
- Failures are recorded on error-fallbacks and on unhandled pipeline exceptions,
  both for `/ask` and `/ask/stream`.

## Ordering in `/ask*`

```
auth → rate limit (sustained) → burst limit → cooldown check (429 before quota)
     → quota reserve (402 if exhausted) → pipeline
     → on success: clear failure streak
     → on error-fallback / exception: record failure + refund quota
```

## Recovery retry vs. SDK retry

`ai_recovery.call_with_recovery` retries only `TransientAIError` (rate-limit /
timeout / connection) with bounded jittered backoff (`AI_RETRY_ATTEMPTS`, default
2). To avoid multiplying attempts, the Bedrock SDK `max_retries` is dropped to 1
(`BEDROCK_MAX_RETRIES`). On exhaustion, `_heavy_chat` falls to the other provider,
then to a **last-good** cached response, then to the friendly error. See
[AI_ARCHITECTURE.md](AI_ARCHITECTURE.md).

## Spend guard (Phase 2 P2-C)

Rate limits count per route and per hour, and premium has no weekly quota, so
they do not bound spend. `app/services/ai_spend_guard.py` is the emergency
boundary: a finite number of **provider calls** per account per day and in
total per hour/day, split into `heavy` (Bedrock) and `light` (OpenAI).

- Enforced inside `ai_gate.model_concurrency_slot` after the capacity permit
  and **before** the provider call (menu OCR, the one ungated call site,
  charges itself). Tool-loop rounds, recovery retries and fan-out batches are
  each a real paid call and each count. The deep-health Bedrock probe is the
  only exclusion (1 output token, cached; refusing it could fail a deploy).
- Refusal raises `AISpendLimitExceeded` (a `BlockingConcurrencyLimit`): every
  existing capacity handler answers with its localized busy/soft-error state.
  No OpenAI fallback, no recovery retry, no provider call to explain it; the
  message names no threshold. Log line `[AI-SPEND] provider call refused
  scope=... class=... user=...` + metric `FitX/Runtime AiSpendGuardRejections`
  (Scope × Class, 4 series max).
- Redis MULTI/EXEC INCR (+EXPIRE NX), admit only if every post-increment
  count is within its limit, compensate on refusal → concurrent callers can
  never over-admit. Redis down → same limits process-locally (bounded, not
  fail-open). The local counters do not see what Redis already admitted, so
  an outage that starts mid-window allows up to (1 + processes) × limit. Prod
  runs 1 gunicorn process (enforced at boot) + 1 RQ worker, and only the web
  process makes heavy calls: ≤2× heavy, ≤3× light, plus one more allowance per
  process restart during the outage.
- Not a billing kill switch for calls already made, and not a product quota:
  defaults sit far above observed use and do not change entitlement.
- **A call ceiling is not a dollar ceiling.** `max_tokens` caps output only.
  Input has no app-enforced token cap (the context budgets are character
  heuristics that undercount Turkish), so the hard per-call input bound is the
  provider context window. SDK retries (Bedrock 1, OpenAI 2) are extra HTTP
  attempts inside one counted call.

### Worst-case exposure at the default ceilings

List prices verified 2026-09-23: Bedrock Price List API, eu-central-1, Sonnet
4.5 **global** profile (what prod uses), $3.00 input / $15.00 output per 1M
tokens (regional profiles +10%); OpenAI gpt-4o-mini $0.15 / $0.60 per 1M.
AWS-enforced quota: 10 requests/min on the global Sonnet 4.5 profile (600/h).

| Case | Per call | Global hour | Global day | 3 days |
|---|---|---|---|---|
| Heavy, absolute (200K in + 8K out) | $0.72 | 300 → $216 | 1,500 → $1,080 | $3,240 |
| Heavy, p99 observed (19.3K in + 2.3K out) | $0.093 | $28 | $139 | $417 |
| Light, absolute (128K in + 16,384 out) | $0.029 | no hourly cap | 5,000 → $145 | $435 |

One account: heavy 200/day → $144 absolute, $18.5 at p99; light 400/day → $11.6
absolute. Observed peak (30 days to 2026-09-23): 40 Bedrock calls in a day,
all in one hour, $1.27 at list price. Bedrock spend draws on AWS credits;
OpenAI spend does not.

The absolute day at 1,500 heavy calls exceeds the ~$950 AWS credit balance.
That case needs every call to fill the 200K context, which no app path is
known to reach, but no app code prevents it either. Tighter ceilings are an
env change (`AI_SPEND_GLOBAL_HEAVY_PER_DAY`, `..._PER_HOUR`). Making the
ceiling a real dollar bound needs a pre-provider input-token cap.

## Env vars

```
AI_SPEND_GUARD_ENABLED=1
AI_SPEND_USER_HEAVY_PER_DAY=200
AI_SPEND_USER_LIGHT_PER_DAY=400
AI_SPEND_GLOBAL_HEAVY_PER_HOUR=300
AI_SPEND_GLOBAL_HEAVY_PER_DAY=1500
AI_SPEND_GLOBAL_LIGHT_PER_DAY=5000   # 0 disables that one ceiling
AI_RATELIMIT=30 per hour          # (config constant)
AI_BURST_RATELIMIT=5 per minute
AI_FAILURE_THRESHOLD=3
AI_FAILURE_COOLDOWN_SECONDS=60
AI_CHAT_QUOTA_ENABLED=1
FREE_WEEKLY_AI_CHATS=200
```
