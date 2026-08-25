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

GitHub loads a `workflow_run` workflow definition from the latest default-branch
revision. Before the privileged job can start any step, its job-level gate
therefore requires the default-branch execution SHA (`github.sha`) to equal the
CI candidate SHA. The first unprivileged step also requires the workflow-file
identity (`github.workflow_sha`) to equal that candidate before checkout or OIDC
credential configuration. Protecting `.github/workflows/deploy.yml` with main
branch protection or a repository ruleset is an external prerequisite; this
repository change does not mutate GitHub settings. Before merge, require PRs,
the full `CI` workflow, and CODEOWNER review for the privileged workflow and
deployment helpers; disallow bypass, force-push, and deletion. Before deploy,
protect the `production` environment with required reviewers and restrict the
OIDC role trust policy to this repository, branch, workflow, and environment.

GitHub serializes this work with the `production-deploy` concurrency group and
`cancel-in-progress: false`. GitHub keeps the running job and at most one pending
job for that group; a newer pending job may replace an older pending job. It
never cancels a transaction that has already started. If the GitHub runner is
lost after SendCommand accepts the request, SSM
may still complete C on the host. Do not issue a second deploy to compensate:
recover the command ID and host outcome first, then allow the serialized queue
to continue.

## Controller preflight and SSM lifecycle

B accepts only the configured running EC2 instance. Git candidate commands are
individually bounded to 60 seconds. Its SSM managed-instance
record must be unique and match the configured ID: `PingStatus` must be `Online`
and `LastPingDateTime` must be no more than 360 seconds old (nor more than one
minute in the future). After the EC2/SSM preflight, B performs one more SSM
managed-instance describe as its last AWS operation before SendCommand and
samples its injected UTC clock immediately after that response. Failure is
fail-closed; correct the instance or SSM registration before retrying.

The controller uses the following independent bounds.

| Limit | Value |
|---|---:|
| Workflow job timeout | 65 minutes |
| Controller step timeout | 46 minutes |
| Delivery timeout | 60 seconds |
| Execution timeout | 1,800 seconds |
| AWS expiry | 1,860 seconds |
| Polling horizon | 2,100 seconds |
| Poll interval | 10 seconds |

The timeout source of truth is `scripts/deploy_contract.py`. Its 1,580-second
host worst case consists of root bootstrap (10), lock acquisition (60),
authority and stale proof (80), clock setup (10), Git fetch/checkout (70),
candidate build/start (620), candidate revision health (160), diagnostics
(30), rollback build/start (440), rollback revision health (80), and cleanup
(20) seconds. The host receives these fixed values from B at privilege drop;
its 1,800-second SSM execution timeout therefore retains an exact 220-second
margin.

Before SendCommand, B reserves enough of its 46-minute budget for the bounded
send, 2,100-second poll horizon, authorization, final invocation read, and
authority cleanup. The 65-minute job budget also covers identity, checkout,
credentials, drift checks, and snapshot initiation without terminating the
controller first.

After SendCommand, B immediately logs the non-secret command ID, then polls
detailed invocation output and preserves the raw `StatusDetails` in its logs.
`GetCommandInvocation` can briefly return `InvocationDoesNotExist` while the
accepted command becomes visible. Only that structured error code is retried,
as an explicit "not visible yet" state, within the same 2,100-second monotonic
horizon. Every other AWS error code, unknown error, malformed response, or CLI
failure remains fail-closed. `Pending`, `Delayed`, and `In Progress` are
non-terminal SSM lifecycle states. `In Progress` is an SSM control-plane state,
not proof that the host helper process has started.
`Success` is the only successful terminal `StatusDetails` value. `Failed`,
`DeliveryTimedOut`, `ExecutionTimedOut`, `Undeliverable`, `Cancelled`, and
`Terminated` are terminal failures. AWS's spaced spellings (`Delivery Timed Out`
and `Execution Timed Out`) have the same failure meaning. An unknown value,
malformed response, CLI failure, or exhausted polling horizon is also a failed
deployment. Immediately after SendCommand returns one canonical command ID, B
writes a short-lived, per-command Parameter Store authority value before its
first invocation poll. The host refuses to acquire the
root lock or mutate anything until that value contains the exact `DEPLOY_SHA`
and command ID. Therefore an ambiguous SendCommand response cannot authorize an
unknown command. The instance profile needs `ssm:GetParameter` only for
`/axisai/production-deploy-authority/*`; the deploy role needs narrowly scoped
`ssm:PutParameter` and `ssm:DeleteParameter` on the same prefix. B deletes the
authority value after the terminal result, with bounded best-effort cleanup.

The 1,860-second AWS expiry is derived from the 60-second delivery timeout plus
the 1,800-second execution timeout; the 2,100-second polling horizon retains the
240-second recovery margin from that expiry.

## Host transaction

C holds the root-controlled outer lock at
`/run/lock/axisai-production/production.lock` for the whole transaction. The
helper can run only with inherited descriptor 7 proving that lock is held; it
has no direct-entry fallback or deploy-directory lock. A lock timeout exits
with status 73. Treat this as
contention, not a safe concurrent deploy: retry after lock contention only once
the prior deployment has finished.

Before it changes the checkout, C fetches `origin/main`, proves the candidate
and current production commits exist, requires that `origin/main` differs from
`DEPLOY_SHA` **never** (they must be equal), and rejects a candidate older than
or divergent from production. It records the current production SHA as exact
`PREV_COMMIT`, resets only to `DEPLOY_SHA`, then rereads `HEAD`. This revision
equality check prevents a mutable-main checkout.

C materializes each build context from `git archive` of the exact revision into
a private temporary directory. Untracked or ignored host files therefore cannot
enter an image build. C builds and starts the Compose release with
`APP_REVISION` set to the exact candidate SHA. It requires all of the following
before accepting the release:

1. the running `web` container reports the expected `APP_REVISION`;
2. `/health?deep=1`, probed inside the running `web` container, returns HTTP 200
   and JSON `status: ok`;
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
