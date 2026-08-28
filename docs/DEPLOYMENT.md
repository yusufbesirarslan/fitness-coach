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

`.github/CODEOWNERS` names every production-authority surface: the CI/CD control
plane, the deploy controller and its canonical time contract, the host helper,
image and runtime composition, application startup and the serving-revision
proof, migration authority, and this runbook.
CODEOWNERS file coverage is not a merge gate: GitHub consults it only when
branch protection on `main` requires review from Code Owners. That setting, like
every other gate in this section, is external, is reported here as not proven,
and must be verified on GitHub before merge. All five external gates -- PR-only `main`, required `CI`, required
CODEOWNER review, blocked force-push/deletion, and a protected `production`
environment with required reviewers and restricted OIDC trust -- are currently
recorded as unproven.

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
samples its injected UTC clock immediately after that response. Both
boundaries reject a bare timestamp as a typed configuration error before any AWS
call, and each boundary re-reads the clock after its own describe response, so
controller time spent between preflight and send counts against heartbeat age.
Failure is fail-closed; correct the instance or SSM registration before
retrying.

The send boundary trusts the clock it is given, and that trust is a boundary
rather than a check. Nothing at run time distinguishes a live clock from one
that captured a single instant and answers with it forever: both are callables
returning a timezone-aware datetime, and two honest readings taken microseconds
apart may be equal, so an advance test would reject real deploys without
detecting what it targets.

A stronger check was considered and declined: comparing the injected clock's
elapsed time against `time.monotonic()` across preflight and send, with a
generous tolerance. That would detect a latched clock, and the monotonic source
is already injected. It is not implemented because the seam it would guard is
unreachable in production — the workflow runs the controller with no arguments,
so the deploy always takes the pinned live default, and the clock parameter
exists for tests. Adding runtime code that can abort a real deploy in order to
defend a test-only seam is the wrong trade. If the entrypoint ever gains a
caller that supplies a clock, implement the monotonic cross-check first.

The controller closes the routes instead. The deploy
entrypoint's parameters are whitelisted, the send-time clock is bound exactly
once and its construction is pinned, no boundary declares a default clock, and
the module is permitted exactly one callable taking no arguments — the live
default. Installing a pre-sampled clock therefore means editing the controller,
which the tests refuse. Review any change to how the entrypoint obtains its
clock as a security change.

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
first invocation poll. The host proves that value carries the exact
`DEPLOY_SHA` and command ID before it mutates anything, and it proves it from
inside the root lock. Therefore an ambiguous SendCommand response cannot
authorize an unknown command. The instance profile needs `ssm:GetParameter` only for
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

The root bootstrap runs a fixed order: outer lock, controller authority proof,
deploy-user read-only `ls-remote` staleness proof, mutation gate, then
everything else. Both proofs run behind the lock and ahead of the gate. The
staleness proof runs as the configured deploy user, so the root bootstrap never
borrows root's credentials for a network read, and it uses `git ls-remote`
precisely because that writes no ref and no object; a `git fetch` would be a
mutation, not a proof. A command the controller never authorized, or one whose
`DEPLOY_SHA` is no longer the tip of `origin/main`, exits 75 having changed
nothing in production: no `.env` mode repair, no nginx validation or reload, no
helper script, no privilege drop, no fetched ref or object.

The privileged helper is an object, not a replaceable pathname. After the
mutation gate, root creates a private directory with `mkdtemp` under `/tmp` --
root-owned, mode 0700 at creation, unpredictably named -- opens it with
`O_DIRECTORY|O_NOFOLLOW`, and writes the helper inside it with
`O_CREAT|O_EXCL|O_NOFOLLOW`. It then `fchmod`s the file to 0505 and verifies,
through the same descriptor it wrote, that the SHA-256 of the stored bytes
equals the digest the controller pinned into the bootstrap, that the file is a
single-linked regular file owned by uid 0, and that the descriptor and the path
resolve to the same device and inode. Only then is the directory widened to
0755 so the deploy user can traverse it, and its identity is re-checked after
the widening. The helper is never chowned to the deploy user and never carries
a write bit for anyone. Because the parent directory stays root-owned and
non-writable by others, the deploy user cannot unlink or recreate that entry,
so the pathname handed across the privilege drop cannot be swapped between
validation and `execve`. Root removes the whole directory after the child
terminates.

The directory lives under `/tmp` rather than the root runtime directory because
systemd mounts `/run/lock` `noexec`: a helper materialized there would validate
perfectly and then fail `execve` with `EACCES` on the production host.

Transaction scratch state lives in the root-owned runtime directory as
`/run/lock/axisai-production/monotonic-clock`, never in the production checkout.
Root creates it mode 0600, owned by the deploy user, only after the mutation
gate opens; hands the path down at privilege drop as `AXISAI_MONOTONIC_STATE`;
and removes it once the child terminates. C refuses to start unless that path is
absolute and names a regular, single-link, mode-0600 file owned by C's own
effective UID, exiting 70 otherwise. A command rejected at the gate therefore
never creates it at all.

Before it changes the checkout, C fetches `origin/main`, proves the candidate
and current production commits exist, requires that `origin/main` differs from
`DEPLOY_SHA` **never** (they must be equal), and rejects a candidate older than
or divergent from production. It records the current production SHA as exact
`PREV_COMMIT`, resets only to `DEPLOY_SHA`, then rereads `HEAD`. This revision
equality check prevents a mutable-main checkout.

C materializes each build context from `git archive` of the exact revision into
a private temporary directory. Untracked or ignored host files therefore cannot
enter an image build. C builds that archive with Docker build argument
`BUILD_REVISION` set to the exact candidate SHA and may still inject
`APP_REVISION` as non-authoritative runtime metadata. The image bakes
`/app/BUILD_REVISION` as a root-owned mode-0444 file. Serving truth is
`DEPLOY_SHA == checked-out HEAD == BUILD_REVISION == deep-health revision`.
It requires all of the following before accepting the release:

Every externally sourced production image is immutable: it names an explicit
version and pins the content digest. The only third-party Compose image is
`redis:7.4.11-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf`
(the Docker Hub multi-platform index digest); `web` and `worker` build the exact
local context, and the application base image is likewise digest-pinned in the
Dockerfile. A mutable tag such as `redis:alpine` is a shipment blocker: it lets
two deploys of the identical application SHA resolve to different third-party
bytes.

1. the running `web` container's `/app/BUILD_REVISION` equals the expected SHA;
2. `/health?deep=1`, probed inside the running `web` container, returns HTTP 200
   and JSON `status: ok`;
3. deep health's server-owned `revision` equals the expected SHA.

A revision mismatch, missing revision, failed build/start, health failure, or
post-start failure enters rollback while the locks remain held. An optional
`PUBLIC_HEALTH_URL` is HTTPS, has no credentials, and is checked only after the
internal deep-health gate. Its failure also rolls the candidate back.

Rollback resets exactly to `PREV_COMMIT`, rereads `HEAD`, rebuilds/restarts with
that same revision (`BUILD_REVISION=PREV_COMMIT`), and repeats container plus
deep-health verification against the previous baked revision. A rollback is
reported verified only after those checks succeed. The only legacy exception is
the immediate predecessor of the revision-aware helper: its missing deep-health
revision may serve as a one-time compatibility proof; any present rollback
revision must still match exactly.

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
