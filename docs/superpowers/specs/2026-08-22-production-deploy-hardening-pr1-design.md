# Production Deployment Hardening PR1 Design

## Goal

Make the single-host production deployment control plane deploy exactly the
commit approved by CI, reject stale candidates, serialize every host mutation,
bound the SSM command lifecycle, and prove the running revision without
triggering a deployment or changing AWS resources.

## Scope and safety boundary

This change is local repository work only. It must not invoke the production
workflow, call `ssm:SendCommand`, change GitHub settings or secrets, update the
SSM agent, alter feature flags, or mutate AWS resources. It does not change
Adaptive Coaching, training plans, capacity limits, worker counts, Redis limits,
or product behavior other than adding an internal deployment revision signal.

The implementation may change:

- `.github/workflows/deploy.yml`
- runner-side and host-side deployment helpers under `scripts/`
- the internal deep-health response and its tests
- deployment regression tests
- `docs/DEPLOYMENT.md`

No mobile repository or staging infrastructure is in scope.

## Repository baseline

The implementation branch starts at
`7a5b2a7cd4dacd782f7932760e151037ee1b4662`, the current `origin/main` at
discovery time. Existing focused deploy and health tests pass (24 tests).

The current workflow is unsafe because it retains a manual dispatch path,
checks out mutable `origin/main` on the host, has no GitHub concurrency group or
host lock, accepts a single instance secret without structural/runtime checks,
uses the default SSM delivery lifetime, swallows AWS polling errors, conflates
accepted delivery with execution, and treats health without revision equality
as deployment success.

## Trigger and immutable SHA authority

Production deployment has one trigger: completion of the repository's `CI`
workflow for `main`. The deploy job runs only when all of these are true:

- the event is `workflow_run`
- `workflow_run.conclusion == "success"`
- `workflow_run.head_branch == "main"`
- `workflow_run.event == "push"`
- `workflow_run.head_sha` is a non-empty, lowercase 40-hex commit SHA

`DEPLOY_SHA` is exactly `github.event.workflow_run.head_sha`. No code path may
derive it from `github.sha`, the runner checkout, the default branch, an API
"latest commit" result, or the host's `origin/main`.

The existing `workflow_dispatch` trigger is removed. A manual production path
cannot currently prove that its input SHA passed the required CI run, so keeping
it would create a second, weaker deployment authority.

The runner checks out `DEPLOY_SHA` with full enough history to validate the
commit. Before `SendCommand`, it fetches `origin/main`, verifies the commit
exists, and requires `origin/main == DEPLOY_SHA`. This deliberately coalesces
old completed CI events instead of letting every formerly successful commit
deploy eventually.

## Rapid deploy and concurrency policy

The workflow uses one repository-wide production concurrency group:

```yaml
concurrency:
  group: production-deploy
  cancel-in-progress: false
  queue: single
```

An in-progress deployment is never canceled merely because a newer event
arrived; an already-issued SSM command can outlive its GitHub runner, so
cancellation would not revoke host authority. Only one pending event is kept.
For rapid successful candidates A, B, and C, A may finish if it already started,
B is replaced while pending, and C is evaluated next. The current-main guard
rejects any candidate that is no longer the intended main revision.

GitHub serialization is not trusted as the only lock. If a runner disappears
after issuing SSM, GitHub may release the concurrency group while the command is
still boundedly executing. Host serialization remains the final authority.

## Runner-side deployment controller

Create a small Python controller under `scripts/` and call it from
`deploy.yml`. It uses `subprocess.run` with argument arrays to invoke the AWS CLI
and accepts an injectable command runner and clock for tests. It never builds a
shell command from unvalidated GitHub or secret input.

Responsibilities:

1. Validate `DEPLOY_SHA`, `AWS_REGION`, `EC2_INSTANCE_ID`, deploy user, deploy
   directory, and optional public health URL before using them.
2. Require the configured region to be `eu-central-1` and the target to be one
   syntactically valid, explicit EC2 instance ID.
3. Verify through `ec2 describe-instances` that exactly that instance exists in
   the configured region and is `running`.
4. Verify through `ssm describe-instance-information` that exactly that managed
   instance is registered, has `PingStatus == Online`, and has a timezone-aware
   `LastPingDateTime` no older than five minutes.
5. Read the host deployment script from the checked-out immutable tree, encode
   it as base64, and construct one Run Command payload without shell
   interpolation. The remote bootstrap decodes the trusted script to a temporary
   file and invokes it with positional arguments.
6. Issue `AWS-RunShellScript` only after every gate succeeds.
7. Poll `get-command-invocation` without redirecting stderr or replacing AWS
   failures with `Pending`.
8. Print sanitized stdout/stderr on terminal completion and return non-zero for
   every non-success terminal state.

The controller's decision logic is pure where practical so all lifecycle
states can be simulated without AWS.

## Host-side deployment transaction

Create a focused Bash helper under `scripts/`. The exact helper bytes sent to
the host come from `DEPLOY_SHA`, not from the host's previously checked-out
branch. It uses `set -euo pipefail` but never `set -x`.

The helper validates its positional inputs again, changes to the configured
deployment directory, and opens a stable lock file inside that directory. It
acquires `flock -w 60` on a dedicated file descriptor before any repository,
Docker build, Compose, migration/startup, or rollback mutation. Lock timeout is
a closed failure; concurrent deployment is never attempted.

While holding the lock, it performs this transaction:

1. Record and validate `PREV_COMMIT=$(git rev-parse HEAD)`.
2. Fetch `origin main --prune`.
3. Require `DEPLOY_SHA` to exist as a commit.
4. Require fetched `origin/main == DEPLOY_SHA`; a command delivered late for an
   old SHA exits before checkout.
5. Require `PREV_COMMIT` to be an ancestor of or equal to `DEPLOY_SHA`. This
   prevents an old or divergent candidate from overwriting a newer checkout.
6. Reset the controlled production checkout to the exact detached
   `DEPLOY_SHA`, then assert `git rev-parse HEAD == DEPLOY_SHA`.
7. Build from that exact tree and start Compose with a generated temporary
   override that injects `APP_REVISION=DEPLOY_SHA` into the web and worker
   container environments.
8. Verify the web container's server-owned `APP_REVISION`, the deep-health
   response revision, and deep-health status.
9. If configured, verify the public HTTPS health URL after the authoritative
   internal revision/readiness gate.
10. Only after all gates pass, prune old Docker images and report success.

The public URL remains optional because the repository does not contain the
authoritative production hostname and this PR may not change GitHub Variables.
When absent, exact container revision plus internal deep readiness is the
authoritative gate; documentation must state that external TLS/nginx/DNS
coverage is deferred until `PUBLIC_HEALTH_URL` is configured.

## Stale deployment rejection

Staleness is checked at two independent times:

- runner-side immediately before `SendCommand`
- host-side after delivery and immediately before checkout

Both require fetched `origin/main == DEPLOY_SHA`. The host additionally requires
the current production checkout not to be ahead of or divergent from the
candidate. Therefore an A command queued before C became main but delivered
afterward cannot mutate the host: its host-side equality check rejects it.

If A legitimately started before C existed, A may finish under the host lock;
C can deploy afterward. This is forward progress, not a downgrade. No older SHA
may overwrite an already-newer checkout.

## SSM lifecycle contract

The workflow uses separate values:

- SendCommand delivery/start timeout: 60 seconds
- `AWS-RunShellScript` `executionTimeout`: 1800 seconds
- total AWS expiry bound: 1860 seconds
- GitHub polling horizon: 2100 seconds
- GitHub job timeout: 40 minutes

AWS documents `SendCommand.TimeoutSeconds` as the bound after which a command
that has not started will not run. `AWS-RunShellScript.executionTimeout` bounds
an execution that legitimately started. AWS also reports overall expiry using
the sum of those values. The controller therefore keeps polling beyond the
1860-second AWS bound, with 240 seconds of margin before its own 2100-second
failure boundary.

An undelivered command cannot begin after the 60-second delivery/start bound,
and GitHub does not declare the pending lifecycle failed before the AWS expiry
window has closed. A legitimately started command may run for at most 1800
seconds. If its runner disappears, the host lock prevents overlap and the SSM
execution timeout bounds the orphan; a newer host command either waits up to 60
seconds for the lock or fails closed for an explicit retry.

## Polling state machine

The controller reads invocation `StatusDetails` and preserves AWS vocabulary.
It distinguishes:

- pre-execution: `Pending`, `Delayed`
- execution proof: `InProgress`
- success: `Success`
- terminal failure: `Failed`, `DeliveryTimedOut`, `ExecutionTimedOut`,
  `Undeliverable`, `Cancelled`, `Terminated`

`SendCommand` acceptance is logged only as command creation, never as host
execution. The first `InProgress` observation is the earliest allowed "started
on host" message. Unknown states, malformed output, AWS CLI failure, or polling
horizon exhaustion are visible closed failures.

## Running revision proof

The deployment helper injects `APP_REVISION` from the validated immutable SHA as
container environment, not from an HTTP request or mutable branch. The Flask
application exposes this value only in the already-restricted deep-health
response as `revision`; shallow public health remains unchanged.

Success requires all three:

- running web container environment reports `APP_REVISION == DEPLOY_SHA`
- deep-health JSON reports `revision == DEPLOY_SHA`
- deep-health HTTP status is 200

A healthy stale container therefore fails. Tests also prove the shallow health
response does not expose revision or other internal fields.

## Rollback

Any failure after checkout/build/start begins triggers rollback to the exact
validated `PREV_COMMIT`, never a branch name. While the same host lock remains
held, rollback resets to `PREV_COMMIT`, rebuilds, starts Compose with
`APP_REVISION=PREV_COMMIT`, verifies the running container environment equals
that SHA, and requires health to recover.

For revisions containing the new deep-health marker, rollback also requires the
endpoint revision to equal `PREV_COMMIT`. For the one-time transition to this
contract, an older rollback target may not expose the field; container
environment equality plus exact checkout/build and health is the compatibility
proof. This exception is explicit and disappears after the first successful
hardened production revision becomes `PREV_COMMIT`.

Rollback restores code and containers only. It cannot reverse migrations that
ran during application boot. The repository's existing expand/contract policy
is the compatibility requirement; deployment documentation must not describe
code rollback as full database rollback.

## Diagnostics and secret hygiene

The host helper logs only phase names, immutable SHAs, health status codes,
revision equality, Compose status, and bounded application log tails on
failure. It never prints `.env`, environment dumps, tokens, DSNs, credentials,
private keys, or secret GitHub values. It does not use `set -x`.

The workflow retrieves Run Command stdout/stderr explicitly and lets AWS CLI
errors fail the job. CloudWatch/S3 command-output retention is not enabled in
this PR because repository and role evidence do not prove the required log
permissions. The operational document records this as deferred AWS hygiene;
the PR does not grant permissions or create log resources.

## Testing strategy

Implementation follows red-green-refactor. Regression coverage includes:

- semantic workflow guards for `workflow_run` SHA authority, main/push/success
  gating, removal of manual mutable authority, and production concurrency
- controller unit tests with injected AWS responses for Online/fresh success,
  Offline/stale preflight, Pending/Delayed/InProgress/Success, every required
  terminal failure, malformed/AWS-error output, delivery expiry, and revision
  mismatch
- host helper tests using temporary Git repositories and fake Docker/Compose
  commands for exact checkout, stale SHA, divergent/current-newer SHA, lock
  contention, successful revision proof, rollback, and health-200/wrong-SHA
- application tests proving deep health exposes only the server-owned revision
  internally and shallow health hides it
- static safety guards against `origin/main` as checkout authority, unbounded
  `flock`, `set -x`, feature-flag mutation, secret printing, and swallowed AWS
  polling errors
- workflow YAML parsing/syntax validation where local tooling permits it,
  `bash -n` for shell helpers, Python compile checks, focused/adjacent pytest,
  full relevant repository tests, and `git diff --check`

No test calls real AWS, sends a real SSM command, or triggers the production
workflow.

## Operational documentation

`docs/DEPLOYMENT.md` becomes the canonical runbook for:

- immutable CI-approved SHA authority
- trigger and A/B/C coalescing behavior
- GitHub and host serialization
- exact target/region/heartbeat preflight
- delivery, execution, polling, and runner-loss semantics
- exact checkout and revision verification
- public versus internal health authority
- rollback target and revision proof
- code-only migration rollback limitation
- AWS status meanings and troubleshooting
- deferred CloudWatch/S3 retention and SSM-agent version hygiene

## Acceptance criteria

The implementation is acceptable only when all focused and adjacent tests pass,
the complete diff has no secret/flag/AWS-resource changes, an independent
adversarial reviewer reports zero Critical and zero Important findings, the
implementation is committed on the isolated branch, and the worktree is clean.
It must remain local: no push, PR, merge, workflow dispatch, deployment,
`SendCommand`, or AWS mutation.
