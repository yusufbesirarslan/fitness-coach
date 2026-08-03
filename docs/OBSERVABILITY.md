# Observability

Four layers: structured request logs, request tracing, CloudWatch **AI** metrics
(`FitX/AI`, Sprint 4 WS6) and CloudWatch **runtime** SLIs (`FitX/Runtime`,
Production Hardening PR1). Sentry (error tracking) is opt-in and unchanged.

> **Two namespaces on purpose.** `FitX/AI` measures *what the coach did*
> (turns, tokens, summarize jobs) and emits per event. `FitX/Runtime` measures
> *whether the service is healthy* (HTTP, capacity, dependencies) and emits from
> a buffer on a timer. They have separate enable flags so either can be rolled
> back alone.

## Structured request log

`app/observability.py` logs one logfmt line per request (`/health` excluded):

```
request id=a1b2c3d4e5f6a7b8 method=POST path=/ask/stream status=200 dur_ms=812.3 user=42 ip=203.0.113.9
```

Greppable and parseable by log aggregators. The client IP is the ProxyFix-resolved
`remote_addr` (the trusted nginx hop), never the raw `X-Forwarded-For`.

## Request tracing (`request_id`)

`assign_request_id` (a `before_request`) stamps every request with a 16-hex-char
`request_id`, always generated **server-side** (a client can't inject one). It
appears in:

- the logfmt line (`id=...`),
- the `/ask/stream` SSE `meta` frame (`request_id`) — so a user-reported streaming
  issue maps to server logs,
- a Sentry tag (`request_id`) when Sentry is enabled.

Helper: `observability.current_request_id()` (returns `-` outside a request).

## CloudWatch AI metrics (`app/services/ai_metrics.py`)

Namespace **`FitX/AI`**. **Default OFF** (`AI_METRICS_ENABLED=0`) — turn on only
after granting `cloudwatch:PutMetricData` to the EC2 instance role. When disabled,
without boto3, or on any error, it is a **total no-op**: metric emission never
blocks or breaks the AI path.

Credentials come from the IAM instance profile (s3_helper pattern) — no keys in
code/`.env`. `put_metric_data` accepts ≤20 datums per call, so batches are chunked.

Emitted today (from `ai_pipeline._emit_metrics`, per streamed turn):

| Metric | Unit | Meaning |
|---|---|---|
| `AITurn` | Count | Successful AI turn (dim: `mode=stream`). |
| `AIErrors` | Count | Error / error-fallback turn. |
| `PromptTokens` / `CompletionTokens` / `TotalTokens` | Count | Real provider usage. |
| `SummarizeJob` | Count | Background summarize (dim: `result=done|skip`). |

Helpers: `increment(name, dims)`, `record_latency(name, ms, dims)`,
`record_tokens(prompt, completion, dims)`, `put_metric(...)`, `put_metrics([...])`.
Additional call sites (cache hit/miss, retry counts, latency histograms) plug into
the same helpers.

### Enabling in production

1. Add `cloudwatch:PutMetricData` to the EC2 instance role policy.
2. Set `AI_METRICS_ENABLED=1` in `.env` and redeploy.
3. Build dashboards/alarms on the `FitX/AI` namespace (latency, token spend, error
   rate, cache hit rate).

## CloudWatch runtime SLIs (`app/services/runtime_metrics.py`)

Namespace **`FitX/Runtime`**. **Default OFF** (`RUNTIME_METRICS_ENABLED=0`), same
reason as `FitX/AI`: it needs `cloudwatch:PutMetricData` on the instance role.

### Why this module buffers instead of emitting per event

`put_metric_data` is a network call. The app serves on **one gunicorn worker with
8 threads**, and heavy AI routes already reserve most of that budget. Calling
CloudWatch inline in `after_request` would add a round-trip to every request —
it would degrade exactly the latency it is supposed to measure, and a CloudWatch
slowdown would become an app slowdown.

So recording writes to a **process-local buffer** (a dict update under a lock)
and a single daemon thread flushes every `RUNTIME_METRICS_FLUSH_SECONDS`
(default 60). Consequences worth knowing:

- A worker restart loses at most one unflushed window. That is deliberate:
  metrics must never be more durable than the request path they measure.
- On a CloudWatch outage the buffer is still drained, so memory stays bounded —
  a window is lost, not the process.
- Counters are **sums per window**, so build alarms on `Sum`, not `Average`.

### Latency uses `Values`/`Counts`, not `StatisticValues`

Percentiles cannot be computed from a StatisticSet (it carries only
count/sum/min/max). Latency is therefore bucketed onto a fixed ladder
(5 ms … 300 s) and shipped as `Values`/`Counts`, from which CloudWatch computes
approximate `p50`/`p95`/`p99`. Bucket edges are the reported values, so a p95 of
`1000` means "at or below 1 s", not exactly 1 s.

### Emitted metrics

| Metric | Unit | Dimensions | Meaning |
|---|---|---|---|
| `HttpRequests` | Count | `Blueprint`, `Status`, `Client` | Request count per window |
| `HttpLatency` | Milliseconds | `Blueprint`, `Status`, `Client` | Bucketed latency → p50/p95/p99 |
| `HttpServerErrors` | Count | `Blueprint`, `Client` | 5xx **excluding** 503 |
| `HttpOverload` | Count | `Blueprint`, `Client` | 503 — deliberate load shedding |
| `HttpThrottled` | Count | `Blueprint`, `Client` | 429 — rate limited |
| `AuthOutcomes` | Count | `Path`, `Outcome` | `Path` ∈ web / mobile; `Outcome` ∈ ok / no_identity / session_invalid / token_rejected / provider_unavailable |
| `AiProviderCalls` | Count | `Provider`, `Outcome` | `Outcome` ∈ success / rate_limit / timeout / connection / transient / api_error / error |
| `AiProviderLatency` | Milliseconds | `Provider` | Provider call duration |
| `AiModelSlotWait` | Milliseconds | `Provider` | Time spent waiting for a model slot |
| `AiModelSlotContended` | Count | `Provider` | Calls that could not take a slot immediately |
| `AiRetries` | Count | `Feature` | Recovery-ladder retries |
| `GateRejections` | Count | `Gate` | Concurrency gate 503s |
| `DbPoolCheckedOut` / `DbPoolOverflow` / `DbPoolSize` | None | — | Sampled on `/health?deep=1` |
| `DbUp` / `RedisUp` / `LoginUp` / `LimiterDegraded` | None | — | 0/1 dependency health |

`HttpServerErrors` and `HttpOverload` are **separate on purpose**. A 503 from the
AI gate is the system working correctly under load; a 500 is a defect. Collapsing
them into one "5xx" series would make healthy load shedding page someone.

Pool and dependency gauges are sampled on the `/health?deep=1` path rather than on
a second timer, because the deploy gate and container probe already call it
regularly.

`AuthOutcomes` answers what the HTTP counters cannot. A 503 on an auth route
could be an overloaded gate or an unreachable identity provider, and a 401 could
be an expired session or a rejected token — the status code alone does not say
which. `provider_unavailable` climbing while `token_rejected` stays flat is an
outage; the reverse is worth looking at as an attack. Throttling is deliberately
absent from the vocabulary: a 429 is not an authentication outcome and is already
counted as `HttpThrottled`. Both boundaries report into the same five-value
vocabulary, defined once in `app/services/auth_contract.py` — see
[AUTH_CONTRACT.md](AUTH_CONTRACT.md).

### Cardinality and privacy rules

Every dimension value comes from a **fixed set**: blueprint name (~16), status
class (~5), client class (2), provider (3), outcome (7), gate label (3), feature
name, auth path (2), auth outcome (5). The following are **never** metric
dimensions or values:

- user IDs, emails, usernames, Cognito `sub`
- raw request paths or query strings
- access/refresh tokens, `Authorization` headers, passwords
- AI prompt or response content

`Client` (`web`/`mobile`) is derived from `request.blueprint == "mobile_api"` — a
server-side fact. A client-supplied header is **never** trusted for it, per
prod-hardening §6 ("Do not trust arbitrary unvalidated client-provided labels").
`tests/test_runtime_metrics.py` asserts both rules.

### Feature-flag visibility

Rollout flags live in the host `.env` and the deploy pipeline does not carry them
(see `docs/ROLLOUT.md`), so the repository cannot answer "what is ON in prod?".
Two mechanisms close that gap:

- a one-line boot log — `[FLAGS] enabled=WEEKLY_PROGRAM_UI_ENABLED,...`
- a `flags` object in `/health?deep=1`, alongside a `capacity` object
  (workers, threads, gate ceilings, DB pool)

Both emit **names and booleans only**. `/health?deep=1` is already restricted to
loopback + `DEEP_HEALTH_TRUSTED_CIDRS` (M3).

The authentication contract has the same problem and the same two mechanisms:
a `[AUTH_CONTRACT]` boot line and an `auth_contract` object in `/health?deep=1`,
carrying the required token use and both paths' expiry leeway (names and
integers only). A divergence also logs a `WARNING`. See
[AUTH_CONTRACT.md](AUTH_CONTRACT.md) §3.

### SLIs, SLOs and alerts

Separate what is measured (SLI), the target (SLO), and when a human must act
(alert). Not every metric is an alert.

| SLI | Query (namespace `FitX/Runtime`) | Proposed SLO | Alert threshold | Window |
|---|---|---|---|---|
| Non-AI API success | `1 − HttpServerErrors / HttpRequests` excluding `Blueprint=coach` | ≥ 99.5 % | < 99 % | 15 min |
| AI API success | `AiProviderCalls Outcome=success / total` | ≥ 97 % | < 90 % | 15 min |
| Auth success | `HttpRequests Blueprint∈{auth,mobile_api} Status=2xx` share | ≥ 98 % | < 95 % | 15 min |
| p95 non-AI latency | `HttpLatency p95`, non-coach blueprints | ≤ 800 ms | > 2 s | 15 min |
| p95 AI latency | `AiProviderLatency p95` | ≤ 25 s | > 60 s | 15 min |
| AI timeout rate | `AiProviderCalls Outcome=timeout / total` | ≤ 1 % | > 5 % | 10 min |
| 5xx rate | `HttpServerErrors Sum` | ≈ 0 | > 5 in window | 10 min |
| Overload rate | `HttpOverload Sum` | ≤ 1 % of requests | > 10 % | 15 min |
| DB pool saturation | `DbPoolCheckedOut / DbPoolSize` | ≤ 0.7 | > 0.9 | 10 min |
| AI concurrency saturation | `AiModelSlotContended Sum` | ≈ 0 | > 20 in window | 10 min |
| Dependency health | `DbUp` / `RedisUp` / `LoginUp` | 1 | 0 | 5 min |

**These SLO numbers are proposals, not validated targets.** No production
baseline exists yet — `RUNTIME_METRICS_ENABLED=1` must run for at least one full
weekly traffic cycle before any of them is treated as agreed. Revisit them with
the observed distribution before wiring alarms.

Alert only on actionable conditions, each with an evaluation window so a single
transient request cannot page anyone: sustained 5xx increase, sustained p95
degradation, AI timeout spike, worker restart loop, authentication rejection
anomaly, DB pool exhaustion, Redis unavailability, and a rolled-out flag
exceeding its abort threshold (`docs/ROLLOUT.md`).

### Enabling in production

1. Add `cloudwatch:PutMetricData` to the EC2 instance role policy.
2. Set `RUNTIME_METRICS_ENABLED=1` in the host `.env` and restart the web service.
3. Confirm data lands in `FitX/Runtime` (allow one flush interval — 60 s default).
4. Build the dashboard from the SLI table above, then add alarms.

Rollback is `RUNTIME_METRICS_ENABLED=0` + restart. It is independent of
`AI_METRICS_ENABLED`; neither flag affects request handling either way.

## Token usage on `CoachMessage`

Real provider `usage` (`prompt_tokens`/`completion_tokens`) is recorded onto each
`CoachMessage` at `record_turn` time — a durable per-turn token ledger independent
of CloudWatch.
