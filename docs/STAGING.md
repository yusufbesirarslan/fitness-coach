# AxisAI staging environment

This runbook describes the isolated non-production environment that exists so
release-readiness work can be exercised against real infrastructure without
touching production. It was established as a standalone infrastructure slice;
it deliberately does not activate any feature and does not close any sprint.

`docs/DEPLOYMENT.md` remains the production deploy contract. Nothing here
changes it. If a sentence in this document ever appears to relax a production
guarantee, this document is wrong.

## 1. What this environment is — and what it is not

**It is** a single stopped-by-default EC2 instance running the same application
image as production, against its own container-local PostgreSQL, its own Redis,
its own Cognito user pool, and its own synthetic accounts. It is reachable only
through AWS Systems Manager. It has no public ingress, no DNS name, no TLS
certificate, and no real user data.

**It is not** a second production. It has no load balancer, no NAT gateway, no
multi-AZ database, no automated backups, and no high availability. Losing it
entirely costs one rebuild (§11), not an incident.

**It is not** production started early. The production instance is production in
every state, including `stopped`. Starting it ahead of its schedule, adding a
second compose project to it, creating a "staging" database on the production
RDS instance, or binding another port on it would all produce something that is
still production — with the isolation removed.

## 2. Provisioned inventory

Region `eu-central-1`, account `852128326881`. Every resource carries the tags
`Project=AxisAI`, `Environment=staging`,
`ManagedBy=infra-staging-environment-slice`, `Purpose=sprint14-readiness`.

| Resource | Identifier | Notes |
| --- | --- | --- |
| EC2 instance | `i-086fdd5d201cbf1a5` — `AxisAI-staging` | `t3.medium`, Ubuntu 26.04 (`ami-051eaec1417c5d4ae`, the same AMI as production), 20 GB encrypted gp3, IMDSv2 required, **no key pair** |
| Subnet | `subnet-0e7ebaacf78bfb284` (eu-central-1a) | existing default VPC; no new VPC was created |
| Security group | `sg-09c0d5a9e0586460d` — `axisai-staging-sg` | **zero inbound rules**, default egress |
| Instance role | `axisai-staging-instance-role` (profile `axisai-staging-instance-profile`) | `AmazonSSMManagedInstanceCore` + inline `axisai-staging-observability` |
| Cognito user pool | `eu-central-1_KH1YUFTCK` — "AxisAI Staging" | **no CustomEmailSender Lambda** |
| Cognito app client | `26jq4cukh9v24c8fah039e641k` | public client, `ALLOW_USER_PASSWORD_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH`, `PreventUserExistenceErrors` enabled |
| Scheduler role | `axisai-staging-scheduler-role` | `ec2:StopInstances` on the staging instance ARN **only** |
| Schedule | `axisai-staging-nightly-stop` | 03:00 Europe/Istanbul, stop only |
| Host root | `/opt/axisai-staging` on the instance | `ENVIRONMENT`, `INSTANCE_ID`, `repo/`, `repo/.env` |

The application database and Redis are **containers on this instance**, declared
by `docker-compose.staging.yml`. There is no staging RDS instance, and staging
never dials an RDS endpoint.

## 3. The production isolation boundary

These are the invariants that make this environment safe to use. Each is
enforced by a mechanism, not by an operator remembering it.

| Invariant | Enforced by |
| --- | --- |
| Staging never writes to the production database | `assert_staging_database` in `scripts/staging_deploy.sh` requires **exactly one** `DATABASE_URL` line, refuses one containing `rds.amazonaws.com`, and requires `@db:5432/`. The single-line rule is not pedantry: Compose's `env_file` uses the **last** assignment of a key, so a guard that read the first one could pass on a `.env` whose effective value is a production endpoint — and the app runs migrations at boot, which makes that a write |
| Staging tooling can never target the production instance | `scripts/staging_control.py` requires **both** `AXISAI_STAGING_INSTANCE_ID` and `AXISAI_PRODUCTION_INSTANCE_ID` and aborts if they are equal, then sends the production id to the host, where `assert_staging_instance` compares it against the live IMDSv2 instance id. Invoked directly over an SSM session with no production id supplied, that second comparison is skipped and the marker files below are the guard that remains — so deploy through the controller |
| The host cannot be mistaken for production | `/opt/axisai-staging/ENVIRONMENT` must read exactly `staging`, and `/opt/axisai-staging/INSTANCE_ID` must equal the live instance id |
| Staging metrics cannot contaminate production metrics | the staging instance role allows `cloudwatch:PutMetricData` only for `AxisAI/Staging/Runtime` and `AxisAI/Staging/AI`; `FitX/Runtime` and `FitX/AI` return `AccessDenied` (verified in both directions) |
| Staging cannot email a real person | the staging Cognito pool has no CustomEmailSender Lambda, and `RESEND_API_KEY` is unset |
| Staging cannot write to production storage | `S3_BUCKET_NAME` is unset, so `s3_helper.is_enabled()` is `False` |
| The production deploy path cannot reach staging | production authority (`.github/workflows/deploy.yml` → `scripts/deploy_control.py` → `scripts/production_deploy.sh`) names only the production instance; nothing in it accepts a staging target |
| The staging schedule cannot stop production | `axisai-staging-scheduler-role` is scoped to the staging instance ARN, and its inline policy grants `ec2:StopInstances` only |

Staging holds **no production data of any kind**: no real emails, password
hashes, food logs, workout logs, photos, coach conversations, tokens, or
sessions. Its accounts are synthetic (§7). No production snapshot was restored
into it.

## 4. Starting staging

Staging is **stopped by default** and has no morning-start schedule. Start it
only for the duration of a specific piece of work.

```bash
aws ec2 start-instances --region eu-central-1 --instance-ids i-086fdd5d201cbf1a5
aws ec2 wait instance-running --region eu-central-1 --instance-ids i-086fdd5d201cbf1a5
# SSM agent registration lags the instance state by ~30-60s:
aws ssm describe-instance-information --region eu-central-1 \
  --filters Key=InstanceIds,Values=i-086fdd5d201cbf1a5 \
  --query 'InstanceInformationList[0].PingStatus' --output text
```

The public IPv4 address is auto-assigned and **changes on every start**. Nothing
depends on it: there is no inbound rule, so it is not an access path. Do not
allocate an Elastic IP to stabilise it.

Docker containers are `restart: unless-stopped`, so the application comes back
by itself after a start. Confirm that it did (§6) rather than assuming it.

## 5. Deploying a revision to staging

```bash
export AXISAI_STAGING_INSTANCE_ID=i-086fdd5d201cbf1a5
export AXISAI_PRODUCTION_INSTANCE_ID=i-0c6f5352fc214e68d   # required, so the two can be compared
python scripts/staging_control.py <40-hex-sha>
```

`scripts/staging_control.py` runs from a workstation, ships
`scripts/staging_deploy.sh` and `docker-compose.staging.yml` inside an
`AWS-RunShellScript` SSM command, and runs the helper on the host as the
`axisai` user. Shipping the helper rather than reading it from the host checkout
is deliberate: it makes revisions that predate this tooling deployable, and it
means the deployed revision cannot silently choose which deploy logic runs
against it.

Only a full 40-hex SHA is accepted. Branch names are refused, in both the
controller and the host script — "deploy the tip of my branch" is exactly the
ambiguity that makes a staging result unquotable.

The host script:

1. asserts environment, instance identity and database target (§3);
2. `git fetch` + `git checkout --detach <SHA>`, then verifies `HEAD` equals the SHA;
3. generates `.axisai-staging-revision.yml` carrying `BUILD_REVISION`/`APP_REVISION`;
4. runs `docker compose -f docker-compose.yml -f docker-compose.staging.yml -f .axisai-staging-revision.yml build`, then the same command with `up -d --remove-orphans`;
5. waits for `/health`, then proves the running image is the requested revision —
   `/app/BUILD_REVISION` inside the container **and** the `revision` field of
   `/health?deep=1` must both equal the SHA.

Step 5 is the point of the whole script. A staging result is only evidence if
you can name the exact revision that produced it.

## 6. Reaching staging from a browser

Staging has no public ingress and no TLS certificate. Access is an SSM port
forward to `localhost`, which the browser treats as a secure context — so
`Secure` session cookies work without a certificate, and the security group
keeps zero inbound rules.

```bash
aws ssm start-session --region eu-central-1 \
  --target i-086fdd5d201cbf1a5 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["5000"],"localPortNumber":["5000"]}'
```

Then open `http://localhost:5000`.

**Prerequisite:** `start-session` requires the `session-manager-plugin` to be
installed on the workstation. It is *not* required for provisioning, deploying,
or any check in this document that uses `aws ssm send-command` — only for the
interactive port forward. Install it before a session that needs a browser.

Health, revision and flag state without a browser. Run this **inside the `web`
container** — the two details below are load-bearing and both were verified
against the running instance:

```bash
cd /opt/axisai-staging/repo
docker compose -f docker-compose.yml \
  -f /opt/axisai-staging/docker-compose.staging.yml exec -T web python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:5000/health?deep=1', timeout=10) as r:
    print(json.dumps(json.load(r), indent=2, sort_keys=True))
PY
```

* **Inside the container, not on the host.** `?deep=1` is gated by
  `_deep_health_allowed()`, which admits loopback or the CIDRs in
  `DEEP_HEALTH_TRUSTED_CIDRS`. A request to the published port from the host
  arrives from the compose network's gateway — neither loopback nor the default
  trusted CIDR — and the endpoint then answers **HTTP 200 with the shallow
  body**: `{"db", "limiter_storage", "status"}` and nothing else. No `revision`,
  no `redis`, no `login`, no `flags`. That is the worst kind of wrong answer,
  because the command succeeds and an operator reads the absence of a failure as
  a pass.
* **`python3`, not `curl`.** The application image has no `curl`;
  `exec … curl` fails with `executable file not found in $PATH`.

`scripts/staging_deploy.sh` probes exactly this way, which is why its revision
proof is trustworthy.

To run the same thing remotely rather than in a session, put the script in a
file and send it with `--cli-input-json`, not the `--parameters` shorthand — the
shorthand mangles the embedded newlines into literal `n` characters:

```bash
python3 - <<'PY' > /tmp/deep-health.json
import json
script = open('probe.sh').read().splitlines()
json.dump({"InstanceIds": ["i-086fdd5d201cbf1a5"],
           "DocumentName": "AWS-RunShellScript",
           "Parameters": {"commands": script}}, open('/tmp/deep-health.json', 'w'))
PY
aws ssm send-command --region eu-central-1 --cli-input-json file:///tmp/deep-health.json
```

## 7. Staging test accounts

Accounts live in the **staging** Cognito pool and are synthetic. The convention:

* username: a non-identifying handle (the Sprint 14 readiness account is
  `s14pr5tester`);
* email: an address in the reserved-by-RFC `.invalid` TLD, e.g.
  `s14pr5tester@staging.invalid`, which cannot be delivered anywhere;
* created with `--message-action SUPPRESS` and confirmed administratively, so no
  message is generated at all.

Never create a staging account with a real address, and never copy a production
account into this pool.

**Credential handling — known gap, recorded honestly.** The password for the
readiness account was set through an SSM command rather than stored as an SSM
`SecureString` parameter, because parameter creation was unavailable at the
time. The consequences: the value is not centrally stored, so it must be re-set
rather than looked up, and it appeared in a command payload readable by
principals who already hold administrative access to this account. It protects
an isolated pool containing zero real data.

To rotate — or to re-establish the password on a fresh workstation:

```bash
aws cognito-idp admin-set-user-password --region eu-central-1 \
  --user-pool-id eu-central-1_KH1YUFTCK \
  --username s14pr5tester \
  --password '<new value>' --permanent
```

The intended end state is `/axisai/staging/test-account/<username>` as a
`SecureString`, read by the operator at use time. That is a small follow-up, not
a blocker for staging use.

## 8. Stopping staging

Stop it as soon as the work is finished:

```bash
aws ec2 stop-instances --region eu-central-1 --instance-ids i-086fdd5d201cbf1a5
```

`axisai-staging-nightly-stop` runs at 03:00 Europe/Istanbul as a **safety net,
not a routine**. It exists so a forgotten instance cannot bill a full night; it
is not a substitute for stopping the instance yourself, and it will interrupt
work that is still running at 03:00.

There is deliberately **no** morning-start schedule. Staging that starts itself
every day is staging that runs every day.

The schedule targets `i-086fdd5d201cbf1a5` and nothing else, through a role that
can only stop that one instance. It is a separate schedule and a separate role
from the production ones (`axisai-nightly-stop`, `axisai-ec2-morning-start`,
`axisai-rds-nightly-stop`, `axisai-rds-morning-start`), which this slice did not
modify.

## 9. Cost model

On-demand list prices, `eu-central-1`, as published by the AWS Pricing API at
the time of writing. Treat them as the shape of the bill, not a quotation.

**Charged while stopped — the standing cost of the environment existing:**

| Item | Rate | Monthly |
| --- | --- | --- |
| 20 GB gp3 root volume | $0.0952 / GB-month | **≈ $1.90** |

**Charged only while running:**

| Item | Rate | Per hour |
| --- | --- | --- |
| `t3.medium` on-demand | $0.048 / hour | $0.048 |
| Public IPv4 address (auto-assigned; released on stop) | $0.005 / hour | $0.005 |
| | | **≈ $0.053 / hour** |

So a four-hour working session costs roughly **$0.21**, and a month of standing
availability with no sessions costs roughly **$1.90**. Left running
continuously it would be about **$40/month** — which is what stopping it by
default avoids.

Negligible but non-zero: EventBridge Scheduler (one invocation per day, far
inside the free tier), SSM Session Manager (no charge), Cognito (a handful of
users), and CloudWatch custom metrics — the free tier covers 10 metrics, and
staging publishes to its own namespaces, so watch this if staging metric
cardinality ever grows.

**Stopped is not zero.** The EBS volume bills every day the environment exists,
whether or not anyone uses it. If staging is not going to be used again, destroy
it (§11) rather than leaving it stopped indefinitely.

Not provisioned, on purpose: NAT gateway, load balancer, Elastic IP, RDS
instance, ElastiCache, ECS/EKS, a second VPC, a Route 53 hosted zone, cross-AZ
redundancy. Each would be a recurring charge for a capability staging does not
need.

## 10. Why staging does not reuse the production deploy path

`scripts/staging_deploy.sh` is not `scripts/production_deploy.sh` with the
environment name swapped, and that is a decision rather than an omission.

Generalising the production script into an environment-parameterised one would
have meant making its guarantees conditional: the `/run/lock/axisai-production`
capability lock, the monotonic phase-deadline budget, the `origin/main`
staleness proof, the ancestor check, and the automatic rollback path would each
have needed an "unless staging" branch. Every one of those branches is a place
where production can later lose a guarantee to a staging convenience — and the
loss would be invisible until a production deploy needed the guarantee.

So the production contract is untouched, and staging carries a smaller script
that keeps only the properties a staging result depends on:

* exact-SHA deployment, with branch names refused;
* `BUILD_REVISION` proof from inside the running container;
* fail-closed environment, instance and database identity assertions.

It deliberately omits what staging does not need: no capability lock (staging
has one operator), no phase budget, no staleness proof (staging is expected to
run revisions that are not `main`), and no automatic rollback — a broken staging
deploy should stay broken and visible, not silently revert the thing you were
trying to look at.

The same reasoning applies to CI: staging deployment is a script plus this
document, not a GitHub workflow. Adding a staging job to the deployment workflow
would put a non-production target inside the production deploy authority.

## 11. Rebuilding and destroying

**Rebuilding** the host from scratch — the instance is disposable and nothing on
it is a source of truth: launch a `t3.medium` from the same AMI into
`subnet-0e7ebaacf78bfb284` with `axisai-staging-sg` and
`axisai-staging-instance-profile`, install Docker, create `/opt/axisai-staging`,
write `ENVIRONMENT` (`staging`) and `INSTANCE_ID` as root-owned `0444` files,
clone the repository into `repo/`, fill `repo/.env` from `.env.staging.example`
with freshly generated values, `chmod 600` it, and deploy a revision (§5). The
database starts empty and Alembic builds the schema at boot.

**Destroying** it, when staging is no longer needed:

1. Delete the schedule: `aws scheduler delete-schedule --region eu-central-1 --name axisai-staging-nightly-stop`
2. Terminate the instance **by explicit id**: `aws ec2 terminate-instances --region eu-central-1 --instance-ids i-086fdd5d201cbf1a5`
3. Delete the security group `sg-09c0d5a9e0586460d` once the instance is gone.
4. Delete the instance profile, then the roles `axisai-staging-instance-role`
   and `axisai-staging-scheduler-role`, removing their inline policies first.
5. Delete the Cognito pool `eu-central-1_KH1YUFTCK` (this deletes the synthetic
   accounts with it).

Delete by explicit identifier, one resource at a time. Never delete by name
pattern — `axisai-*` matches production. If an inventory step turns up something
unexpected, stop and report it rather than guessing: an orphaned staging
resource costs a few cents a month, and a wrong deletion costs production.

## 12. What this environment does not decide

Establishing staging and activating a feature are separate acts with separate
reviews. The staging host boots with `FITX_WORKOUT_SESSIONS_ENABLED=0`, and
`.env.staging.example` documents it as `0`.

Turning any feature flag on, exercising a lifecycle against it, and judging
whether it is ready for production belong to the work that uses this
environment — not to the environment itself.
