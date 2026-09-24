# Rate Limiting & Abuse Control (Sprint 4 WS7)

Six different things bound AI use, and none of them is the bill:

| Control | Unit | Where |
|---|---|---|
| Rate limit | requests per route per window | Flask-Limiter |
| Concurrency limit | simultaneous provider calls | `ai_gate` permits |
| Provider-call ceiling | admitted physical provider attempts per account/global window | `ai_spend_guard` |
| Input-token ceiling | upper-bound input tokens per attempt, per feature | `ai_input_budget` |
| Estimated cost | list price × provider-reported tokens, per attempt | `[AI-USAGE]` log events |
| Actual billed cost | what AWS / OpenAI invoice | Cost Explorer / OpenAI billing |

The ceilings bound the maximum; the usage events measure what happened; only
the invoices are the bill.

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

## Recovery retry vs. provider-attempt retry

Two retry layers, and every physical provider attempt in either is charged:

- **Provider attempt** (`app/services/ai_provider_call.py`). Both SDK clients
  are built with `max_retries=0`, so one SDK call is one HTTP attempt. The
  provider door retries connection errors and 408/409/429/5xx itself, up to
  `BEDROCK_MAX_RETRIES` (1) / `OPENAI_MAX_RETRIES` (2) extra attempts, and
  charges the spend guard before EACH retry. **One spend-guard unit = one
  physical provider attempt.** Timeouts are not retried at this layer (a
  timed-out request may already be generating billed output); a streaming
  call is one attempt (a stream is not replayable).
- **Recovery ladder** (`ai_recovery.call_with_recovery`, heavy chat only)
  retries `TransientAIError` (rate-limit / timeout / connection) with bounded
  jittered backoff (`AI_RETRY_ATTEMPTS`, default 2). Each of its attempts is a
  fresh admission (input check + charge). On exhaustion, `_heavy_chat` falls
  to the other provider, then to a **last-good** cached response, then to the
  friendly error. See [AI_ARCHITECTURE.md](AI_ARCHITECTURE.md).

Maximum physical attempts for one `_heavy_chat` call: 2 × (1+1) Bedrock + 2 ×
(1+2) OpenAI = 10, all counted. A spend or input-budget refusal is never
retried and never falls back.

## Spend guard (Phase 2 P2-C)

Rate limits count per route and per hour, and premium has no weekly quota, so
they do not bound spend. `app/services/ai_spend_guard.py` is the emergency
boundary: a finite number of **provider calls** per account per day and in
total per hour/day. `heavy` is Claude Sonnet 4.5 and `light` is Claude Haiku
4.5 EU Geo. Both are Bedrock. The class is the model-policy field; the
transport name does not select it.

- Enforced inside `ai_gate.model_concurrency_slot` after the capacity permit
  and **before** the provider call; every provider call reaches the slot
  through `ai_provider_call.admit()` (menu OCR: `gate=False`, still charged).
  Tool-loop rounds, recovery retries, provider-attempt retries and fan-out
  batches are each a real paid attempt and each count. The deep-health
  Bedrock probe is the only uncharged call (1 output token, fixed prompt,
  cached; refusing it could fail a deploy) — it still passes the input check
  and emits a usage event.
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
- A call ceiling alone is not a dollar ceiling. It becomes one together with
  the hard input budget below: every counted attempt has a bounded maximum
  input and output, so calls × per-attempt maximum is a real bound.

## Hard input budget (Phase 2 closeout)

`app/services/ai_input_budget.py` (policy) + `app/services/ai_provider_call.py`
(enforcement). Every paid call goes through one door:

```
final request kwargs → deep copy → hard input/output/image check (+ reduction,
re-count) → capacity permit → spend-guard charge → provider attempt
```

- **What is counted.** A deterministic UPPER bound, not an estimate:
  UTF-8 bytes of the JSON-serialized payload (system, messages, tools, tool
  results; image data excluded) + 1,024 fixed overhead + a per-image ceiling
  (Sonnet 4.5: 1,600; gpt-4o-mini high/auto detail: 48,169, low: 2,833).
  Byte-level BPE never yields more tokens than bytes of text; the fixed
  overhead covers role markers and the hidden tool-use prompt. Calibrated
  against Bedrock CountTokens on the production model (2026-09-23): worst
  text ratio 1.00 token/byte (spaced digits), a tool_result call ~300 tokens
  above its byte count, a 5000×5000 image 1,568 tokens. Ordinary Turkish text
  is over-counted ~2× — the price of never undercounting. Every usage event
  logs the bound next to the provider-reported count; `bound_violation:true`
  would mark an undercount.
- **Where.** At the last trustworthy point: `admit()` receives the final
  kwargs, deep-copies them, checks the copy, and the SDK is called with that
  copy. The only argument a caller can add afterwards is the transport
  `timeout` (anything else is a `TypeError`). The streaming producer thread
  and every tool-loop round are admitted separately, so a large tool result
  is re-checked before the next round.
- **Reduction.** Only the coach has droppable content: the oldest history,
  two messages at a time (`history_reducer`). System/security instructions,
  tool schemas, the current request and this turn's tool calls/results are
  never dropped. After reduction the payload is re-counted; if it still does
  not fit, it is refused. No model is called to summarize.
- **Refusal.** `AIInputBudgetExceeded`, a subclass of `AISpendLimitExceeded`:
  the existing localized busy/soft-error handling applies, the provider is
  never called, there is no fallback and no retry, no spend budget or permit
  is consumed, and the free quota is refunded exactly as for a spend refusal.
  The message names no threshold. Log line `[AI-BUDGET]
  input_budget_exceeded provider=... model=... feature=...`, a usage event with
  `outcome=input_budget_rejected`, and `FitX/Runtime AiInputBudgetRejections`
  (dimension `Class` only: 2 series max).
- **Output** is capped per feature too: a call asking for more than the
  feature's `max_tokens` ceiling is refused (a code defect), never clamped.

| Feature | Provider | Input budget | Output cap | Images |
|---|---|---|---|---|
| coach (tool loop, stream, feedback) | Sonnet, Haiku fallback | 64,000 | 700 | 0 |
| training_plan (generate + repair) | Sonnet, Haiku fallback | 32,000 | 7,000 | 0 |
| nutrition_plan | Sonnet, Haiku fallback | 32,000 | 2,000 | 0 |
| nutrition (estimates, macro batches, meal review) | Haiku, some Sonnet | 16,000 | 4,000 | 0 |
| menu_extract | Sonnet, Haiku fallback | 64,000 | 5,000 | 0 |
| menu_ocr (per image / scanned-PDF page) | Haiku | 56,000 | 4,000 | 1 |
| vision (validation, pump-check analysis/compare) | Sonnet | 12,000 | 1,200 | 2 |
| summary | Haiku | 16,000 | 500 | 0 |
| health_probe (uncharged) | Sonnet | 2,048 | 1 | 0 |
| other | either admitted model | 16,000 | 2,000 | 0 |

Scanned-PDF OCR is capped at `AI_MENU_OCR_MAX_PAGES` (5) pages per upload.
Budgets are env-overridable (`AI_INPUT_BUDGET_<FEATURE>`,
`AI_OUTPUT_BUDGET_<FEATURE>`); a non-positive or non-integer value fails boot.
There is no "0 = unlimited".

Evidence for the budgets: AWS/Bedrock `InputTokenCount`, 2026-07-25..09-23,
~150 non-probe calls (small sample): max 19,795 tokens (a coach turn), p95
over the last 15 days ~15.8K–18.5K, non-coach calls ~1.7–2K. The coach
budget (64K bound units) is ~1.5× the largest observed coach call converted
to bound units (19,795 × 2.09 measured coach ratio ≈ 41K).

### Worst-case exposure (per attempt × production ceilings)

Sonnet 4.5 global list, verified on the Anthropic price card: $3.00 input /
$15.00 output per 1M tokens. Haiku 4.5 EU dollars below are **calculated**
from Anthropic's published Haiku 4.5 list ($1.00 / $5.00) plus the published
10% Bedrock regional/geo premium ($1.10 / $5.50). No eu-central-1 Bedrock
Price List SKU for this profile was found, so application telemetry does not
emit `estimated_cost_usd` for Haiku. Token counts stay provider-reported.

Max $ per attempt = input budget × input price + output cap × output price.

| | Max $ / attempt | Hour | Day | 3 days | Redis-degraded day |
|---|---|---|---|---|---|
| Heavy (menu_extract: 64K in + 5K out) | $0.267 | 100 → $26.70 | 300 → $80.10 | $240.30 | ≤2× → $160.20 |
| Light launch (same shape, Haiku price) | $0.0979 | no hourly cap | 500 → $48.95 | $146.85 | ≤3× → $146.85 |
| Light user launch | $0.0979 | — | 100 → $9.79 | $29.37 | shares the global cap |

Production `.env` sets heavy to 100/hour and 300/day and does not set the
light keys, so the code defaults (100/user/day, 500/global/day) are the
launch ceilings. The retired gpt-4o-mini pair was 400/5000. At the Haiku
calculated price that retired global cap is about $489.50/day, and the
mid-window Redis-degraded reading of it is about $1,468.50/day. Do not
restore it.

Worst single light shapes at the same calculated price: menu OCR page
(56K bound + 4K out) $0.0836, so a 5-page scanned PDF is $0.418 if every
page fills the budget; coach fallback round (64K + 700 out) $0.07425, so a
5-round tool loop is $0.371. The menu-extract fallback remains the single
attempt maximum.

Redis-degraded multiplier, re-read from compose and `gunicorn.conf.py`:
one web process (`FITX_WEB_WORKERS=1`, boot-enforced) and one RQ worker.
Gunicorn's 8 threads and the in-process macro/OCR pools share the web
counter. The worker forks a child per job and cannot dequeue while Redis
is down, so a cold outage is the web process only (1×). An outage that
starts after Redis has already admitted a full window, with one job child
still running, is (1 + web + that child) = 3× light for that window. A
process restart clears its local counter and can spend another full window;
the 3× figure does not cap a restart loop.

A provider 429 is retried once inside `admit()` (`BEDROCK_MAX_RETRIES=1`),
charged as a new light attempt, then surfaces as the existing soft error
(coach), HTTP 502 (meal totals), or an empty OCR result. SDK retries stay
at 0. There is no direct OpenAI fallback. Scanned-PDF OCR is one call per
page, sequential, at most 5 pages.

## Usage telemetry

One `[AI-USAGE] {json}` line per physical provider attempt and per local
refusal, written to stdout by the dedicated `fitx.ai_usage` logger, so it
lands in `/axisai/app` (30-day retention) with the web/worker service label.
No CloudWatch custom metric per user or feature.

Fields: `event, ts, request_id, provider / billing_provider (bedrock|openai),
spend_class (heavy|light), model (normalized:
claude-sonnet-4-5|claude-haiku-4-5|gpt-4o-mini|other), feature (fixed taxonomy), subject_id
(internal account id or null), outcome (success|provider_error|timeout|
client_disconnect|guard_rejected|input_budget_rejected), attempt, tool_round,
usage_source (provider|estimated), input_tokens, output_tokens,
cache_write_tokens, cache_read_tokens, image_units, input_bound, output_cap`,
and for attempted calls with known pricing `estimated_cost_usd,
pricing_version, pricing_model`. Never prompt or response text, email, IP or
credentials. `usage_source=estimated` (timeouts, errors, abandoned streams)
carries the input UPPER bound and no output figure — never a claimed zero.
`estimated_cost_usd` is list-price arithmetic, not a bill.

Logs Insights over `/axisai/app`. Events arrive as docker json-file lines, so
the event is the `log` field; the key order is fixed, so one regex `parse`
extracts it. Every query starts with this prefix (PAID = attempts that reached
a provider):

```
filter log like /^\[AI-USAGE\]/
| parse log /"request_id":(?<rid>"[^"]*"|null),"provider":"(?<provider>[a-z]+)","model":"(?<model>[a-z0-9-]+)","feature":"(?<feature>[a-z_]+)","subject_id":(?<subject>[0-9]+|null),"outcome":"(?<outcome>[a-z_]+)","attempt":(?<attempt>[0-9]+|null),"tool_round":(?<round>[0-9]+|null),"usage_source":(?<src>"[a-z]+"|null),"input_tokens":(?<inp>[0-9]+|null),"output_tokens":(?<outp>[0-9]+|null),"cache_write_tokens":(?<cw>[0-9]+|null),"cache_read_tokens":(?<cr>[0-9]+|null)/
| parse log /"estimated_cost_usd":(?<usd>[0-9.e-]+)/
```

```
# 1-2 usage by provider (set the time range to "today")
<prefix> | filter outcome in ["success","provider_error","timeout","client_disconnect"]
| stats sum(inp) as input_tokens_total, sum(outp) as output_tokens_total,
        sum(usd) as cost_usd, count(*) as attempts by provider
# 3, 5, 10 tokens / distribution / estimated COGS per feature
<prefix> | filter outcome in [...PAID...]
| stats sum(inp) as input_tokens_total, sum(outp) as output_tokens_total,
        pct(inp, 50) as in_p50, pct(inp, 95) as in_p95, pct(inp, 99) as in_p99,
        max(inp) as in_max, sum(usd) as cost_usd by feature | sort input_tokens_total desc
# 4 accounts by paid attempts
<prefix> | filter outcome in [...PAID...] | filter subject != "null"
| stats count(*) as attempts, sum(usd) as cost_usd by subject | sort attempts desc | limit 10
# 6-7 timeouts and refusals before the provider
<prefix> | stats count(*) as n by outcome, provider
# 8-9 COGS per active / paying user: cost_usd ÷ the account count for the
#     window (accounts_with_ai here; premium count from the database)
<prefix> | filter outcome in [...PAID...]
| stats sum(usd) as cost_usd, count_distinct(subject) as accounts_with_ai
```

Distribution percentiles use provider-reported tokens where present; filter
`src = '"provider"'` to exclude estimated upper bounds.

## Env vars

```
AI_SPEND_GUARD_ENABLED=1
# production (2026-09-23): AI_SPEND_GLOBAL_HEAVY_PER_HOUR=100, ..._PER_DAY=300
AI_SPEND_USER_HEAVY_PER_DAY=200
AI_SPEND_USER_LIGHT_PER_DAY=100
AI_SPEND_GLOBAL_HEAVY_PER_HOUR=300
AI_SPEND_GLOBAL_HEAVY_PER_DAY=1500
AI_SPEND_GLOBAL_LIGHT_PER_DAY=500    # 0 disables that one ceiling
AI_INPUT_BUDGET_<FEATURE>=<int>     # see the input budget table; must be > 0
AI_OUTPUT_BUDGET_<FEATURE>=<int>
AI_MENU_OCR_MAX_PAGES=5
BEDROCK_MAX_RETRIES=1                # provider-door retries (SDK retries are 0)
OPENAI_MAX_RETRIES=2
AI_RATELIMIT=30 per hour          # (config constant)
AI_BURST_RATELIMIT=5 per minute
AI_FAILURE_THRESHOLD=3
AI_FAILURE_COOLDOWN_SECONDS=60
AI_CHAT_QUOTA_ENABLED=1
FREE_WEEKLY_AI_CHATS=200
```
