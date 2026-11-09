# R6 release monitoring M1–M3

Source of truth for the alarms that gate the R6 production release. The
deploy pipeline does **not** apply this stack. It is created once, with
founder approval, after the worker that publishes the metrics is serving.

| | Signal | Published by | Alarm | Missing data |
|---|---|---|---|---|
| M1 | `WorkerHeartbeat` = 1/min | worker heartbeat thread, only after a Redis `PING` | `AxisAI-R6-M1-Worker-Heartbeat-Missing` (5 min) | **breaching** |
| M1 | same | same | `AxisAI-R6-M1-Worker-Heartbeat-Escalation-60m` (60 min, escalation topic) | **breaching** |
| M2 | `QueueDepth` (LLEN) | heartbeat thread (Redis only) | `AxisAI-R6-M2-Queue-Not-Draining` (never empty for 15 min) | notBreaching (M1 covers) |
| M2 | `QueueOldestJobAgeSeconds` | heartbeat thread | `AxisAI-R6-M2-Queue-Oldest-Job-Age` (>600 s for 3 min) | notBreaching |
| M2 | `QueueFailedJobsNew` (+ `QueueFailedJobs` total) | heartbeat thread | `AxisAI-R6-M2-Queue-Failed-Jobs` (≥1 in 5 min) | notBreaching |
| M3 | `ProviderRetentionPassCompleted` | retention job, after `expire_due` returned | `AxisAI-R6-M3-FatSecret-Expiry-Pass-Missing` (none for 30 min) | **breaching** |
| M3 | `ProviderRetentionOverdue` | same | `AxisAI-R6-M3-FatSecret-Values-Past-24h` (>0) | notBreaching |

- **Namespace:** `FitX/Runtime`, dimension `Service=worker`.
- **Switch:** `RUNTIME_METRICS_ENABLED`. The worker reads the same host `.env` as web, and web already publishes `FitX/Runtime` in production.
- **Zero traffic:** every signal is driven by the worker's own timers (60 s sample, 300 s retention enqueue, hourly erasure enqueue). None depends on requests.

## Why these shapes

- **M1 fails safe.** A dead worker, a dead heartbeat thread, unreachable Redis, metrics switched off, or a broken IAM or network path all look the same: no datapoint, so ALARM.
- **M2 uses depth for a hung worker.** The retention job is enqueued with `ttl=300`. When the worker hangs, expired job hashes vanish, so the oldest *live* job's age cycles 0→300 s and never stays high. `LLEN` keeps counting those ids until a worker pops them, so the queue never drains. `Minimum` over each 5-minute period ignores the one sample a minute that legitimately catches a fresh enqueue.
- **M3 respects the 22h/24h margin.** Values are removed at 22h. A pass missing for 30 minutes still leaves ≥ 90 minutes before any value reaches 24h. Any value actually past 24h pages at once.
- **No acknowledgement API exists.** CloudWatch has none. "Acknowledged" means the founder records it in the incident log. "Not resolved within an hour" is the escalation alarm, which notifies `EscalationTopicArn` (ideally a different channel, e.g. SMS).

## Production resources (exact, nothing else)

| # | Resource | Notes |
|---|---|---|
| P1 | CloudFormation stack `axisai-r6-release-monitoring` in `eu-central-1`, account `852128326881`, from `template.yaml` | 7 standard alarms. No IAM, no SNS change, no compute. |
| P2 | `AlarmTopicArn` = the existing topic in `AxisAI-EC2-Instance-StatusCheck`'s `AlarmActions` | Read-only lookup at approval time. No new subscription. |
| P3 | `EscalationTopicArn`: either the same topic, or a new topic plus one founder SMS/e-mail subscription | Founder choice. A new topic is a separate resource with its own confirmation. |
| P4 | No IAM change | `AxisAIProdRuntimePolicy` already allows `cloudwatch:PutMetricData` on `FitX/Runtime` (SEC001 §A.5). Confirm read-only with `iam:SimulatePrincipalPolicy` before P1. |
| P5 | Host `.env` must have `RUNTIME_METRICS_ENABLED=1` for the worker (shared with web) | Read-only check. Already true if web publishes. |

**Cost:** ≈ $3.3/month in total.
- 7 alarms × $0.10.
- 7 new custom metrics × $0.30.
- ≈ 52k `PutMetricData` calls per month (1/min heartbeat + 1 per 5-min pass) at $0.01 per 1,000.

## Apply (founder-approved window only)

1. Deploy the release so the new worker publishes. In CloudWatch, confirm `WorkerHeartbeat` and `ProviderRetentionPassCompleted` datapoints for `Service=worker` (read-only).
2. Create the stack with `ActionsEnabled=false`, and wait until every alarm is `OK`. With actions off, the breaching-on-missing alarms cannot page while history fills.
3. Update the stack with `ActionsEnabled=true`.
4. Delivery drill, once per alarm topic. `aws cloudwatch set-alarm-state --alarm-name AxisAI-R6-M1-Worker-Heartbeat-Missing --state-value ALARM --state-reason drill`, confirm the notification arrived, then let the next evaluation return it to OK. Repeat for the escalation alarm if its topic differs.

**Rollback:** delete the stack. Metric publishing stays harmless; set `RUNTIME_METRICS_ENABLED=0` to stop it.

## Runbook

### M1

1. `docker compose ps worker` and `docker compose logs --tail 200 worker`.
2. `redis-cli PING`.
3. `docker compose restart worker`.

Worker down means AI summaries run inline and retention and erasure retries stop (M3 follows within 30 min).

### M2

1. `flask --app starter rq-failed`. Inspect, then `rq-requeue <id>` only if safe.
2. Never empty: the worker is hung on a job. Check its logs, then restart the worker.

### M3

1. Pass missing: check M1/M2 first, then the worker log for `[RETENTION]`.
2. Overdue > 0: a licence-boundary incident. Run the pass manually (`expire_due` via the worker) and record the incident.
