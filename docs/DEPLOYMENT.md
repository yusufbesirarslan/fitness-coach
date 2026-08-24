# Production deployment operations

This runbook is the production deploy contract. It is intentionally narrow: a
successful CI run for `main` may deploy one immutable revision to the one
configured EC2 instance. No manual workflow dispatch, branch-tip deployment,
or host-side feature-flag change is part of this contract.

## Authorities and hand-off

- `A` — `.github/workflows/deploy.yml`
- `B` — `scripts/deploy_control.py`
- `C` — `scripts/production_deploy.sh`

`DEPLOY_SHA` is the sole deployment authority. It is the SHA from the completed
successful CI `workflow_run` event, not whatever `main` contains later. A checks
out that exact SHA with full history and invokes B once. B reads C from that
exact checkout, validates the candidate and target, and sends C through the
`AWS-RunShellScript` SSM document. C performs the one host transaction.

GitHub serializes this work with the `production-deploy` concurrency group:
`cancel-in-progress: false` and `queue: single`. A new CI result coalesces
behind the current deploy; it never cancels a transaction that has already
started. If the GitHub runner is lost after SendCommand accepts the request, SSM
may still complete C on the host. Do not issue a second deploy to compensate:
recover the command ID and host outcome first, then allow the serialized queue
to continue.

## Controller preflight and SSM lifecycle

B accepts only the configured running EC2 instance. Its SSM managed-instance
record must be unique and match the configured ID: `PingStatus` must be `Online`
and `LastPingDateTime` must be no more than five minutes old (nor more than one
minute in the future). Failure is fail-closed; correct the instance or SSM
registration before retrying.

The controller uses the following independent bounds.

| Limit | Value |
|---|---:|
| Delivery timeout | 60 seconds |
| Execution timeout | 1,800 seconds |
| AWS expiry | 1,860 seconds |
| Polling horizon | 2,100 seconds |
| Poll interval | 10 seconds |

After SendCommand, B polls detailed invocation output and preserves the raw
`StatusDetails` in its logs. `Pending`, `Delayed`, and `In Progress` mean the
command is waiting; `In Progress` also means host execution has started.
`Success` is the only successful terminal `StatusDetails` value. `Failed`,
`DeliveryTimedOut`, `ExecutionTimedOut`, `Undeliverable`, `Cancelled`, and
`Terminated` are terminal failures. AWS's spaced spellings (`Delivery Timed Out`
and `Execution Timed Out`) have the same failure meaning. An unknown value,
malformed response, CLI failure, or exhausted polling horizon is also a failed
deployment.

## Host transaction

C holds the root-controlled outer lock at
`/run/lock/axisai-production/production.lock` and a deploy-directory lock for
the whole transaction. A lock timeout exits with status 73. Treat this as
contention, not a safe concurrent deploy: retry after lock contention only once
the prior deployment has finished.

Before it changes the checkout, C fetches `origin/main`, proves the candidate
and current production commits exist, requires that `origin/main` differs from
`DEPLOY_SHA` **never** (they must be equal), and rejects a candidate older than
or divergent from production. It records the current production SHA as exact
`PREV_COMMIT`, resets only to `DEPLOY_SHA`, then rereads `HEAD`. This revision
equality check prevents a mutable-main checkout.

C builds and starts the Compose release with `APP_REVISION` set to the exact
candidate SHA. It requires all of the following before accepting the release:

1. the running `web` container reports the expected `APP_REVISION`;
2. loopback `/health?deep=1` returns HTTP 200 and JSON `status: ok`;
3. deep health's server-owned `revision` equals the expected SHA.

A revision mismatch, missing revision, failed build/start, health failure, or
post-start failure enters rollback while the locks remain held. An optional
`PUBLIC_HEALTH_URL` is HTTPS, has no credentials, and is checked only after the
internal deep-health gate. Its failure also rolls the candidate back.

Rollback resets exactly to `PREV_COMMIT`, rereads `HEAD`, rebuilds/restarts with
that same revision, and repeats container plus deep-health verification. A
rollback is reported verified only after those checks succeed. The only legacy
exception is the immediate predecessor of the revision-aware helper: its
missing deep-health revision may serve as a one-time compatibility proof; any
present rollback revision must still match exactly.

## Database and operational boundaries

Code rollback does not roll back database migrations. Migrations run at
application boot, so migrations must follow expand/contract discipline and be
backward-compatible with the preceding release. For a destructive migration,
take and verify an RDS snapshot and execute the migration as a separately
planned operation; do not expect the deploy rollback to restore database state.

The deploy path does not print `.env` contents or AWS credentials, and it does
not assign feature flags. Host `.env` permission repair and nginx validation are
separate safeguards within the locked bootstrap, not configuration management.

CloudWatch and S3 retention are deferred operations work. SSM-agent upgrades
are separate host hygiene work. Plan, authorize, and verify each of those
changes outside this immutable deploy transaction.
