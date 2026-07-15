# Deployment (Sprint 4)

FitX runs on a single AWS EC2 host via Docker Compose behind host nginx. Database
is external AWS RDS PostgreSQL (`DATABASE_URL`). Redis is a compose service.

## Pipeline

```
push to main
  → ci.yml (pytest + schema-drift `flask db check` on Postgres 16)  MUST be green
  → deploy.yml (workflow_run-gated) via AWS SSM on the EC2 host:
        git reset --hard origin/main
        docker compose build
        docker compose up -d
  → curl /health?deep=1 gate; on failure → rollback to previous commit
```

If CI is red, deploy never starts. The `/health?deep=1` gate is loopback/private
only (docker-bridge source IP allowed for the gate's own curl).

## Services (`docker-compose.yml`)

| Service | Purpose | Notes |
|---|---|---|
| `web` | gunicorn (1 worker × 8 threads) | `mem_limit` 1g, loopback-only, log rotation. |
| `redis` | cache / limiter / queue | 200mb `allkeys-lru`, loopback-only. |
| `worker` | RQ background jobs (WS8) | same image, `python worker.py`, `mem_limit` 512m, log rotation, `depends_on: redis`. |

Adding the `worker` service is automatic on deploy (`docker compose up -d` builds
and starts it). Budget the host RAM: web (1g) + redis (256m) + worker (512m).

## Background jobs (WS8)

- `app/jobs/` — `get_queue()` (None-safe), `enqueue_or_run(func, ...)` (enqueue if
  a worker/Redis exists, else run inline), worker heartbeat, dead-letter helpers.
- `worker.py` — RQ worker entrypoint. Linux/prod uses `rq.Worker` (fork);
  Windows/dev uses `rq.SimpleWorker` (`RQ_SIMPLE_WORKER=1`, auto on win32). A daemon
  thread refreshes the `fitx:worker:alive` heartbeat every `WORKER_HEARTBEAT_TTL/2`.
- **Worker is optional.** If it's down or `rq` is missing, jobs fall back to inline
  execution — the app keeps working. `/health?deep=1` reports `worker: alive|down|
  unknown` as **informational only** (it does **not** gate the deploy).
- Wired task today: `summarize_conversation` (see [MEMORY.md](MEMORY.md)). Add more
  by writing a top-level function in `app/jobs/tasks.py` and calling
  `enqueue_or_run` from the producer.
- Dead-letter (RQ `FailedJobRegistry`): `flask --app starter rq-failed` lists
  exhausted jobs; `flask --app starter rq-requeue <job_id>` requeues one. Retry
  policy: `Retry(max=3, interval=[10, 60, 300])`.

## Migrations — expand/contract (load-bearing)

Rollback restores **code but not the DB** (migrations auto-apply on boot). Write
migrations expand/contract (backward-compatible): no DROP/RENAME unless the old
code ran without it through at least one successful deploy. For an unavoidable
destructive change: take an RDS snapshot, set `FITX_DB_AUTO_UPGRADE=0`, and run
`flask db upgrade` as a separate one-off step. Boot migration failure is fatal
(health gate rolls back); escape hatch `FITX_DB_UPGRADE_FAIL_OPEN=1`.

Sprint 4 PR5 adds **no** migration (the `CoachMessage` token columns already exist).

## New env vars (all safe-defaulted — no `.env` edit needed for first deploy)

```
# WS8 background jobs
WORKER_MEM_LIMIT=512m
WORKER_HEARTBEAT_TTL=120
RQ_SIMPLE_WORKER=0
# WS6 observability (turn on AFTER granting IAM permission)
AI_METRICS_ENABLED=0
AI_METRICS_NAMESPACE=FitX/AI
```

See `.env.example` for the full WS5/WS7/WS9 set. The worker requires `REDIS_URL`
(prod already sets it).

## IAM

- Bedrock: `bedrock:InvokeModel` on the instance role.
- S3 avatars/meal images: existing instance-role policy.
- CloudWatch metrics (WS6, optional): `cloudwatch:PutMetricData`. Grant this
  **before** flipping `AI_METRICS_ENABLED=1`.

## Verification checklist

1. `python -m pytest -q` green (load tests excluded by default; run with
   `-m load`).
2. Local smoke without Redis/boto3: cache, queue, metrics, cooldown all no-op.
3. With local Redis: `enqueue_or_run` enqueues; `python worker.py`
   (`RQ_SIMPLE_WORKER=1` on Windows) drains the queue; heartbeat visible in
   `/health?deep=1`.
4. `curl -N /ask/stream` → `meta/delta/done` frames, first token <1s.
