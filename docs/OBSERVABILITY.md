# Observability (Sprint 4 WS6)

Three layers: structured request logs, request tracing, and CloudWatch AI metrics.
Sentry (error tracking) is opt-in and unchanged.

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

## Token usage on `CoachMessage`

Real provider `usage` (`prompt_tokens`/`completion_tokens`) is recorded onto each
`CoachMessage` at `record_turn` time — a durable per-turn token ledger independent
of CloudWatch.
