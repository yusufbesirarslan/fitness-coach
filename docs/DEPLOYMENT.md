# Production deployment operations

This runbook is the production deploy contract. It is intentionally narrow: a
successful CI run for `main` may deploy one immutable revision to the one
configured EC2 instance. No manual workflow dispatch, branch-tip deployment,
or host-side feature-flag change is part of this contract.

The non-production environment is documented separately in `docs/STAGING.md`
and is deliberately outside this contract: it has its own instance, its own
database, its own deploy script, and no job in this workflow. Nothing in this
runbook accepts a staging target, and nothing in the staging runbook may relax
a guarantee described here.

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

The unprivileged `candidate` job does not hold the production lock and does not
request environment approval. It skips when `DEPLOY_SHA` is already behind
`origin/main`, and it cancels sibling Deploy-to-EC2 runs that are only waiting
for the production approval gate on a superseded SHA. That is the skip deploy
run #250 never got: the pending deployment was rejected at the required-reviewer
gate, the controller never started, and the run failed red. The privileged
`deploy` job then serializes host mutation with the `production-deploy`
concurrency group and `cancel-in-progress: false`. GitHub keeps the running job
and at most one pending job for that group; a newer pending job may replace an
older pending job. It never cancels a transaction that has already started. If the GitHub runner is
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
`DeleteParameter` succeeds with empty CLI output, which the runner accepts for
that operation only. B logs exactly one cleanup outcome: `removed`,
`already absent` (`ParameterNotFound`), or `cleanup failed code=<AWS code>
kind=<timeout|start|exit|invalid-json|not-object>`, never the parameter name or
value.

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

Both privileged boundaries hand their child a complete environment rather than
an inherited one, so the execution `PATH` is deployment contract surface. The
canonical value is the standard root search order,
`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`, defined once as
`HARDENED_EXECUTION_PATH` in `scripts/deploy_control.py` and injected into both
the outer root-lock child and the privilege-dropped helper. The sbin entries are
not decoration: Ubuntu ships `runuser` and `nginx` in `/usr/sbin`, so a PATH
without it cannot resolve the staleness proof's `runuser` or the nginx
validation gate, and `aws` lives in `/usr/local/bin`. Each gate only becomes
reachable once the one before it passes, so an unresolvable command surfaces one
deploy attempt at a time; every external command the bootstrap and the helper
invoke is enumerated and pinned to this PATH in `tests/test_deploy_control.py`.

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
`redis:8.8.0-alpine@sha256:9d317178eceac8454a2284a9e6df2466b93c745529947f0cd42a0fa9609d7005`
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
the immediate predecessor of the revision-aware helper: a missing
`/app/BUILD_REVISION` and a missing deep-health revision may each serve as a
one-time compatibility proof; any present rollback revision must still match
exactly. That exception is keyed solely to whether the rollback target predates
`scripts/production_deploy.sh`, never to a SHA, date, or version list. The
container probe reports an absent `/app/BUILD_REVISION` in band and still
succeeds whenever the container answers, so an unreachable container, a
transport error, or an exhausted phase deadline is a hard failure for every
revision and can never be read as the legacy case.

## Database and operational boundaries

Code rollback does not roll back database migrations. Migrations run at
application boot, so migrations must follow expand/contract discipline and be
backward-compatible with the preceding release. For a destructive migration,
take and verify an RDS snapshot and execute the migration as a separately
planned operation; do not expect the deploy rollback to restore database state.

### Startup modes and the dual-revision contract (R6-01A)

Status: R6-01A is complete. The dual-revision startup prerequisite is hardened,
but blue/green deployment is **not yet implemented**. Production still deploys
through the single-container path above and needs no new environment value.

`FITX_STARTUP_MODE` selects what `create_app()` does to shared state
(`app/schema_safety.py`, dispatched once in `app/__init__.py::_run_startup`):

| Mode | Set by | Schema / seeds / backfill | Leaderboard rebuild | Schema proof | Security readiness |
|---|---|---|---|---|---|
| `self-migrating` (default; unset/empty) | current production web | yes (`prepare_release`) | yes | via upgrade | yes |
| `read-only` | future R6 candidate slot | **no** | **no** | read-only, must be exactly at head or boot fails | yes |
| `skip-db-init` (from `FITX_SKIP_DB_INIT=1`) | worker, migration CLI, tests | no | no | no | no |

Any other value, or `FITX_STARTUP_MODE` combined with `FITX_SKIP_DB_INIT=1`,
refuses to boot. Set the mode per service, never in the shared `.env` (the
worker would then refuse to start rather than become a migration owner).

The read-only proof compares Alembic's own head set
(`ScriptDirectory.get_heads()`) with `alembic_version`. It runs in a
`SET TRANSACTION READ ONLY` transaction and fails closed on a missing or empty
version table, an unknown revision, a behind or divergent database, or a
database it cannot read. A database that is *ahead* of the candidate also fails;
booting an older revision fresh against a newer schema is a later R6 decision.

**Release-time migration authority.** In the R6 flow, release preparation runs
once, before any candidate boots:
`FITX_SKIP_DB_INIT=1 flask --app starter release-prepare`. That runs Alembic
upgrade, fresh-schema bootstrap, quest and challenge seeds, and the referral
backfill (all idempotent). It then runs the same read-only proof. It never
rebuilds the canonical leaderboard sorted sets, because the serving revision is
reading them. Web slots never migrate concurrently.

**Readiness probes are side-effect free.** A candidate is probed with
`/health` and `/health?deep=1` before traffic switches to it. The global
`maybe_weekly_rollover` before_request hook is skipped for the `health`
endpoint, whatever the query string (`app/hooks.py::request_runs_maintenance`).
So a probe never takes the `fitx:rollover_check` / `fitx:session_purge`
throttles, never runs the weekly rollover and never dispatches daily
maintenance. Every other request keeps the existing throttled maintenance
contract, so maintenance resumes with the first ordinary request after the
switch. The remaining hooks are already inert on an anonymous probe: CSRF only
acts on writes, `update_streak` returns before any query for an anonymous
user, and locale resolution only reads the session. Two Redis effects remain,
and neither is application state:
- Dependency checks are reads (connectivity checks, `EXISTS`, `GET`).
- Flask-Limiter's default limit still counts `/health` per client IP, in its
  own `LIMITER/*` keys with a TTL. These are request-admission counters, and
  the serving revision's own probes already write them.

**Bounded lock wait.** On PostgreSQL every Alembic run goes through
`migrations/env.py`: the boot upgrade, `flask db upgrade` and `release-prepare`.
Each runs on a dedicated NullPool connection started with
`lock_timeout = FITX_MIGRATION_LOCK_TIMEOUT_MS`.
- Default 5000 ms; accepted range 100–60000 ms. Anything else, including 0
  ("wait forever"), refuses to migrate.
- Why 5 s: a DDL lock that is waiting stalls every later query on that table.
  5 s is below today's 7–10 s recreate gap, far above a normal request
  transaction, and ends in a clean failure the health gate can see.
- On timeout: Alembic's single transaction rolls back entirely, and the boot or
  command fails. Retry once the conflicting transaction ends.
- The setting never reaches the request pool.

**Expand/contract gate.** `python -m scripts.migration_expand_contract` runs in
CI through `tests/test_migration_expand_contract.py`.
- The 46 migrations shipped before R6-01A are pinned by revision and content
  digest. They are exempt from the rules, but they may not be edited.
- Every new migration must declare `expand_contract = "expand"`, and the gate
  then rejects drops, renames, type, NOT NULL or default changes, unique or
  foreign-key or check constraints on existing tables, destructive or
  non-literal raw SQL, and dynamic dispatch.
- Alternatively a migration declares `expand_contract = "contract"` together
  with a written `expand_contract_reason`. That marks the release as not
  overlap-safe, and future deploy control must not run two revisions across it
  (`contract_revisions()`).
- The gate's known static-analysis limits are listed in the module docstring.

### Two-slot web runtime foundation (R6-01B)

Status: R6-01B adds the **runtime primitive only**. Nothing deploys through it
yet: `production_deploy.sh`, `docker-compose.yml`, nginx and the legacy web on
`127.0.0.1:5000` are unchanged, and no new `.env` value is needed.

| Stage | Scope | State |
|---|---|---|
| R6-01A | two revisions can share schema/Redis at startup | done |
| R6-01B | two web containers can run side by side on one host | this section |
| R6-02 | nginx traffic switching between slots | **not implemented** |
| R6-03 | exact-SHA deploy transaction (release-prepare, start, verify, switch, drain, rollback, worker order) | **not implemented** |

**Slot model.** `docker-compose.web-slot.yml` plus one identity overlay
(`deploy/compose/web-slot-{blue,green}.yml`), driven only by
`scripts/web_slot_runtime.py`.

| | blue | green |
|---|---|---|
| Compose project | `axisai-web-blue` | `axisai-web-green` |
| service (log identity) | `web` | `web` |
| host port → container | `127.0.0.1:5001 → 5000` | `127.0.0.1:5002 → 5000` |

- Each slot project contains only `web`. There is no redis, worker, volume,
  `depends_on` or `container_name` (Compose derives unique names from the
  project), so slot start, stop and removal cannot touch them.
- A slot joins the main project's default network (`fitness-coach_default`)
  as `external`. That is how `redis` resolves. The helper refuses unless that
  network carries the main project's Compose labels and exactly one healthy
  main-project redis. It never creates a network or a Redis.
- `--remove-orphans` only considers containers of the invoking project, so the
  main project's deploy cannot remove a slot, and a slot cannot remove the
  worker, Redis or the other slot. The helper never passes `--remove-orphans`,
  `-v` or `--rmi`.
- The image is `axisai-web:<40-hex>`, `pull_policy: never`, never built by the
  slot file. The authority is still the baked `/app/BUILD_REVISION` and the
  deep-health `revision`, never the tag.
- `FITX_STARTUP_MODE=read-only` and `APP_REVISION` are set in `environment`,
  which outranks `env_file`, so `.env` cannot turn a slot self-migrating.
  `FITX_SKIP_DB_INIT=1` in `.env` would make the app refuse to boot (R6-01A).
  Because every slot boots read-only, R6-03 must run `release-prepare` before
  starting a candidate.
- The helper validates the rendered model with `docker compose config`. Some
  Compose versions merge `env_file` into that render even with
  `--no-env-resolution`. The helper keeps it in memory only and redacts it to
  `FITX_STARTUP_MODE` and `APP_REVISION`. It never prints the render.
- Both slots keep `com.docker.compose.service=web`, so the CloudWatch agent's
  `(web|worker)` filter ships them unchanged. Slots also log
  `com.docker.compose.project`, which tells the two slots apart inside the one
  `/axisai/app` stream during overlap. The legacy web does not log it.

**Capacity contract** (t3.small, 1905 MiB usable, no swap; R6-00 evidence):

- Per-slot ceiling `mem_limit: 640m`, a literal that is not `.env`-tunable.
  It is ≥3× the observed 201 MiB web working set and ~1.8× the largest web
  footprint the 7-day host peak allows.
- Ceilings alone do not make overlap safe: 2×640 + 512 + 256 MiB > 1905 MiB.
  Safety is the ceiling **and** an admission gate checked immediately before
  the candidate starts:
  `MemAvailable ≥ 640 (candidate ceiling) + 32 (shim/proxy outside the cgroup)
  + 256 (protected host reserve) = 928 MiB`, and fewer than 2 running `web`
  containers.
- The reserve covers the observed 7-day host excursion (633 → 779 MiB used,
  146 MiB) plus kernel watermarks. It can be raised to 192–768 MiB with
  `--host-reserve-mib`; empty, zero, negative or garbage values are refused.
- The worst observed `MemAvailable` (1088 MiB) admits with 160 MiB to spare.
  Below the threshold, the candidate never starts and nothing else is touched:
  no cache drop, swap, or worker or Redis stop.
- R6-03 must keep the two-slot overlap bounded: remove the old slot after the
  drain. A retained rollback slot counts as the second web container.

**Stop semantics.** `stop_grace_period: 45s` is 30 s gunicorn
`graceful_timeout` + 5 s bounded shutdown metric flush + 10 s margin. Docker's
default 10 s SIGKILLs requests that gunicorn would still drain. This does
**not** make 300 s AI requests survive a stop: R6-03 must first move new
traffic away from a slot, then wait a bounded drain, then stop it. Gunicorn
`timeout = 300` and `graceful_timeout = 30` are unchanged.

**Candidate readiness** (`verify`) needs all of the following. Docker
`healthy` alone is not readiness.
- the container is `healthy` (`docker inspect` checks, at most 36 × 5 s);
- the baked `/app/BUILD_REVISION` is the expected SHA;
- an in-container `/health?deep=1` reports `status: ok` and that `revision`
  (at most 6 tries);
- the slot's own loopback port answers `/health` 200;
- the container's port binding is exactly `127.0.0.1:<slot port>`.

The deep check runs inside the container because a published-port request
arrives from the Docker gateway, which is not a deep-health trusted source.
The deep check may make the existing cached, bounded Bedrock probe. There is
no polling loop.

**`/health` limiter budget.** The default limit (`600 per hour`) is counted per
client key per endpoint in the shared Redis. Every in-container probe is keyed
`ip:127.0.0.1`, so all web containers share one `/health` budget. Host-side
loopback probes arrive from the Docker gateway under a separate key. Worst
case per hour on the shared key:
- 2 running web containers × 120 Docker HEALTHCHECKs (30 s interval) = 240;
- 60 legacy `production_deploy.sh` deep probes (candidate + rollback);
- 24 slot `verify` deep probes;
- total 324 of 600, which leaves 46 % headroom.

The 2-container admission cap is what bounds this. A host `.env` that lowers
`DEFAULT_RATELIMIT` below ~540/h would break the margin.

**Failure model** (no traffic ever moves in R6-01B, so there is no traffic
rollback):
- Low memory, an occupied candidate port, a missing or foreign shared network,
  a missing image or a mis-baked revision: refused before start, active
  untouched.
- An existing candidate container that is not a running, matching, healthy or
  starting candidate: refused, never overwritten. Remove it explicitly.
- A candidate that is unhealthy or reports the wrong revision fails `verify`.
  It can then be removed with
  `remove --slot <s> --expected-revision <sha>`. The revision must match, and
  only that project is torn down.

The deploy path does not print `.env` contents or AWS credentials, and it does
not assign feature flags. Host `.env` permission repair and nginx validation are
separate safeguards within the locked bootstrap, not configuration management.

CloudWatch and S3 retention are deferred operations work. SSM-agent upgrades
are separate host hygiene work. Plan, authorize, and verify each of those
changes outside this immutable deploy transaction.

## Runtime AWS identity — resolved

**Status: resolved by SEC-001 (closed 2026-10-06).** The durable migration and
retirement record is
[`SEC001_AWS_RUNTIME_S3_REMEDIATION.md`](../SEC001_AWS_RUNTIME_S3_REMEDIATION.md).

Production runtime capability is granted by EC2 instance-profile attachment:

```text
AxisAIProdRuntimeProfile
  -> AxisAIProdRuntimeRole
```

Both `web` and `worker` resolve credentials through the standard AWS SDK
credential chain, from the instance role via IMDS (`iam-role`, principal
`AxisAIProdRuntimeRole`). The static environment credentials
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` are absent
from the production host `.env` and from both containers.

History:

- Production used to run on long-lived static credentials of the IAM user
  `fitx-s3-user`, supplied through the host `.env`. That is how the Coach once
  ended up served entirely by the OpenAI fallback: the user carried only
  `AmazonS3FullAccess`, so every Bedrock call returned `AccessDenied`.
- The 2026-10-04 cutover removed that static credential path.
- The legacy access key, the `fitx-s3-user` user, the legacy EC2
  roles/instance profiles and their orphaned customer policies were retired on
  2026-10-05 and 2026-10-06. None of them remains available as a fallback.

Deployment contract:

- A normal deploy does **not** create AWS static credentials, does **not**
  switch back to a legacy instance profile, and does **not** change runtime IAM.
  It runs under the current runtime profile.
- Application rollback is revision-based (see above). It runs under the same
  `AxisAIProdRuntimeRole`, because no legacy identity remains to roll back to.
- Any change to the runtime IAM architecture is a separate, explicitly
  authorized infrastructure/security operation, never a step inside a deploy or
  a feature change. That includes any change to the host-observability
  policy (`AxisAIProdHostObservabilityPolicy`), which replaced
  `CloudWatchAgentServerPolicy` on the runtime role in SEC-001 R4 (closed
  2026-10-07).
- Do not add AWS key variables back to the host `.env`.
