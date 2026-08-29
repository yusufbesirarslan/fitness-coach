# Production Deploy Hardening PR1 Remediation Design

## Goal and scope

Remediate the eight Important findings against candidate
`fa1a48d104409dfe33c6ba91d9a15e43ab0d3cab` without shipping, changing AWS or
GitHub settings, or rewriting reviewed history. Every coherent production fix
is a new normal commit. The final candidate remains blocked pending fresh
independent certification.

The work is limited to deployment controller and host authority, trusted CI
dependencies, immutable build revision truth, production Compose inputs,
CODEOWNERS coverage, regression tests, and the deployment runbook. No feature
flags, production configuration, infrastructure state, or release action is in
scope.

## Base and history rules

The implementation starts from branch `ops/deploy-hardening-pr1` in the
existing isolated worktree. The initial fetched `origin/main` is
`59cff9d54a059f9456f2b83a513784db21eef859`, which is already the branch merge
base. Before the final report, fetch and compare again. If main has advanced in
a deployment-relevant way, integrate it with `git merge --no-ff origin/main`,
never by rebase.

Do not amend, squash, reset branch history, force-push, push, open or merge a
pull request, deploy, invoke SendCommand, mutate AWS, or modify GitHub settings.
The host deployment script may continue to use bounded exact-reset operations
as part of its tested production transaction; this prohibition applies to the
remediation worktree and branch history.

## Chosen architecture

The remediation keeps the existing three-layer deployment model:

1. GitHub Actions identifies the CI-approved immutable `DEPLOY_SHA`.
2. `scripts/deploy_control.py` validates controller authority and creates one
   bounded SSM command.
3. A root bootstrap serializes host execution, proves that the command remains
   current, materializes a stable helper object, drops privilege, and runs the
   host transaction in `scripts/production_deploy.sh`.

The change tightens boundaries rather than introducing an artifact registry,
release database, new host service, or permanent privileged installer.

## 1. Send-time SSM freshness

AWS documents a five-minute managed-node health signal cadence, and the SSM
Agent's default `HealthFrequencyMinutes` is five. The permitted heartbeat age
is therefore **360 seconds**: one normal five-minute interval plus 60 seconds of
jitter and clock/API tolerance. This avoids healthy-host flapping while
rejecting the prior effective 750-second window.

The existing broad preflight remains useful for EC2 identity and state, but it
does not authorize SendCommand. A dedicated `require_fresh_ssm_target`
operation performs `describe-instance-information`, requires exactly the
configured instance, requires `PingStatus == Online`, and requires a timezone-
aware `LastPingDateTime` no more than 360 seconds old.

`send_command` calls this operation itself immediately before invoking the AWS
CLI `ssm send-command` operation. Between the freshness response and starting
SendCommand, only bounded in-memory argument construction is permitted. File
reads, Git calls, sleeps, retries, network calls, and other subprocesses are
forbidden in that interval. Tests exercise offline status, the 360-second
boundary, stale timestamps, a clock advanced after early preflight, and call
ordering at the injected AWS-runner boundary.

## 2. Timeout authority and algebra

Canonical timeout constants live in one Python deployment-contract module and
are consumed by the controller, bootstrap renderer, tests, and generated host
environment. The shell helper rejects missing or inconsistent canonical
values. No independently editable copy of the authority numbers remains in the
workflow, controller, and shell script.

The target host worst case is **1,580 seconds** against an SSM execution timeout
of **1,800 seconds**, leaving **220 seconds** of execution margin:

| Host phase | Maximum seconds |
| --- | ---: |
| Root bootstrap and immutable input validation | 10 |
| Root lock acquisition | 60 |
| Post-lock command authority and stale remote proof | 80 |
| Monotonic clock/state setup | 10 |
| Git fetch and exact checkout | 70 |
| Candidate build and Compose startup | 620 |
| Candidate revision and health verification | 160 |
| Failure diagnostics | 30 |
| Rollback checkout, build, and Compose startup | 440 |
| Rollback revision and health verification | 80 |
| Cleanup | 20 |
| **Host worst case** | **1,580** |
| **SSM execution timeout** | **1,800** |
| **Margin** | **220** |

Each phase retains smaller command-level timeouts; the phase ceiling is the
outer bound. Cleanup and rollback are included in the worst case rather than
treated as work after the deadline.

The GitHub deploy job remains 65 minutes and its controller step remains 46
minutes. Controller algebra reserves at most 300 seconds before send, a
2,100-second delivery/execution polling horizon, a final 30-second invocation
read, and 60 seconds for authority cleanup: 2,490 seconds total, leaving 270
seconds inside the 2,760-second controller step. Static/behavioral tests import
the canonical constants and fail when the host plus margin reaches the SSM
timeout or controller authority can expire before a safely observed terminal
state.

## 3. Trusted GitHub Action immutability

Audit every `uses:` reference in `.github/workflows/ci.yml` and
`.github/workflows/deploy.yml`, including service-adjacent and privileged Linux
jobs. Every third-party action is pinned to an authoritative full 40-character
commit SHA with a version comment. Repository-local actions are permitted only
through `./...` paths; Docker action references are permitted only when their
image digest is immutable.

Exact SHAs are resolved from GitHub's upstream tag/ref metadata and are never
invented. One workflow regression test parses all trusted workflows and rejects
tags, branches, abbreviated SHAs, and unqualified third-party references.

## 4. Independent revision truth

`DEPLOY_SHA` is the expected deployment authority. The host proves its exact
checked-out `HEAD` equals that value before building.

The actual artifact authority is a build-generated `/app/BUILD_REVISION` file.
The Docker build requires a `BUILD_REVISION` build argument containing exactly
40 lowercase hexadecimal characters, writes it in a dedicated build step, sets
root ownership and read-only mode, and places it outside runtime environment
control. The host passes the revision obtained from the exact `git archive`
checkout as the build argument.

At application startup, a focused revision loader reads and validates
`/app/BUILD_REVISION` once. Deep health returns that immutable build value.
`APP_REVISION` may remain expected/runtime metadata for diagnostics, but it is
not used as actual serving truth. Host verification establishes:

`DEPLOY_SHA == checked-out HEAD == BUILD_REVISION == deep-health revision`.

The running container is also asked to read `/app/BUILD_REVISION` directly,
which must match the expected SHA. Tests prove that changing `APP_REVISION`
cannot spoof deep health, a healthy response with a wrong baked revision fails,
the build argument comes from the exact checkout, and rollback serves the
previous baked revision.

## 5. Stale authority before production mutation

The host sequence becomes:

`receive immutable command -> validate syntax/identity -> acquire root lock ->
revalidate controller authority -> read-only remote main proof -> mutation
gate opens -> production changes`.

The post-lock authority check verifies the controller's command-specific SSM
parameter. The stale candidate check uses a bounded read-only remote query
(`git ls-remote` for `refs/heads/main`) and compares its exact SHA with
`DEPLOY_SHA`; it does not update local refs. Only after both checks pass may the
bootstrap or helper change `.env`, reload/enable nginx, fetch/update repository
refs, reset checkout, create repository-local transaction files, invoke
Docker/Compose, run startup/migrations, or change revision state.

Read-only path/stat/config inspection is allowed before the gate. Temporary
clock/helper state lives in the root-controlled runtime area, not the production
checkout. A stale-command behavioral harness snapshots `.env`, managed nginx
files, `HEAD` and repository status, Docker trace, and revision markers; stale
rejection must leave them byte/state-equivalent and show no mutating command.

## 6. Stable privileged helper object

The embedded helper bytes are decoded by root into a unique file created with
`O_CREAT|O_EXCL|O_NOFOLLOW` inside the already verified root-owned mode-0755
`/run/lock/axisai-production` directory. The file remains root-owned, regular,
single-linked, and non-writable after creation. Its mode permits execution by
the configured deploy user without granting write access.

Root hashes the open descriptor and compares it with the controller-generated
SHA-256 of the embedded helper bytes, then verifies owner, type, link count,
mode, and descriptor/path inode identity. The directory is not writable by the
deploy user, so after privilege drop that user cannot unlink, rename, replace,
or modify the validated file. `execve` names this stable root-controlled object;
the parent root bootstrap retains cleanup authority and removes it after the
child terminates.

This is preferred over `/proc/self/fd` script execution, whose shebang and
descriptor inheritance semantics add unnecessary interpreter complexity. A
Linux race test pauses after validation, attempts replacement as the deploy
user, and proves that replacement fails and the validated bytes execute.
Additional authoritative Ubuntu tests cover `O_NOFOLLOW`, inode identity,
permissions, descriptor behavior, and the existing OFD/root lock contract.

## 7. CODEOWNERS and external governance

CODEOWNERS explicitly covers the file itself, both trusted workflows, deploy
controller and contract modules, host helper, all Dockerfiles, production
Compose files, startup entrypoints, migration/init authority, deep-health and
build-revision implementation, and the deployment runbook. A canonical test
list maps every current production-authority surface to a matching ownership
rule and fails when a new listed surface is uncovered.

Repository ownership does not assert enforcement. The runbook records these
external pre-merge/release requirements exactly:

- complete required CI/ruleset;
- required CODEOWNER review;
- protected production GitHub Environment;
- production required reviewers; and
- narrowly restricted OIDC trust for the intended repository, branch/workflow,
  and environment.

The remediation does not create or alter any of those settings.

## 8. Immutable production images

Replace `redis:alpine` with a compatible explicit Redis version plus an
authoritative Docker Hub manifest-list digest. Do not upgrade Redis across a
major version. Audit all other third-party production images, including CI
service images where they influence trusted validation, and pin equal-authority
mutable references or report them explicitly.

Compose regression coverage parses the production model and rejects mutable
third-party image references. Application images built from the exact local
context are distinguished from external image dependencies.

## Minor findings

The remediation request records two Minor findings but omits their text. A
search of the supplied desktop files and repository did not locate the source
certification report. Implementation will inspect reachable Git history and
review artifacts once more. If the exact findings remain unavailable, the final
report will state that they could not be retrieved and will not invent or
silently fix them. This uncertainty cannot be used to claim shipment readiness.

## Test and commit strategy

For each Important finding, add the smallest failing characterization or
regression first, run it and confirm the expected failure, then implement the
minimum correction and rerun focused and adjacent tests. Configuration-only
pins still receive a failing parser/contract test before mutation.

Use separate normal commits for coherent groups:

1. SSM freshness and canonical timeout bounds.
2. Trusted Action pins and Linux authority CI coverage.
3. Independent immutable build revision.
4. Post-lock stale rejection before mutation.
5. Stable privileged helper execution.
6. Immutable images, CODEOWNERS, and governance documentation.

If adversarial review finds a new Critical or Important defect, reproduce it in
a failing test, fix it in a new commit, and rerun affected validation.

## Validation and handoff

Portable local validation covers controller/workflow tests, host transaction
tests available on Windows, health/revision tests, auth/Compose-adjacent tests,
CODEOWNERS tests, Compose configuration, YAML parsing, Bash syntax where a Bash
runtime is available, Python compileall, `git diff --check`, and the full
repository-standard non-load pytest suite. Record exact counts and command exit
status.

Linux-only lock, privilege, descriptor, and race tests must be wired into
required CI but reported locally as `PENDING EXACT-SHA UBUNTU CI`; Windows
execution is not accepted as substitute evidence. Before reporting, refetch
main, integrate material drift without rebase, rerun affected tests, and require
a clean working tree.

Even with all local checks green, the only positive verdict allowed is:

`IMPORTANT FINDINGS FIXED LOCALLY — FRESH INDEPENDENT CERTIFICATION REQUIRED`

Otherwise the verdict is `NOT READY`.
