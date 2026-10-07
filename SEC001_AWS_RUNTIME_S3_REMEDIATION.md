# SEC-001: production runtime identity and S3 remediation — migration record

```text
SEC-001 STATUS:
CLOSED
```

Status: **CLOSED** (2026-10-06).

- **Cutover:** completed 2026-10-04, 07:25–07:44Z. Production web and worker run on the dedicated EC2 role `AxisAIProdRuntimeRole` through the instance profile `AxisAIProdRuntimeProfile`, with no static AWS credentials.
- **Legacy credential and principal retirement:** completed 2026-10-05 to 2026-10-06. The `fitx-s3-user` access key `AKIA…4XUI` and the `fitx-s3-user` IAM user are deleted. The legacy roles and instance profiles `AxisAI-EC2-Role`, `AxisAI-EC2-Role-` and `fitx-ec2-s3-access` are deleted (§C, R1–R3).
- **Orphan-policy cleanup:** completed 2026-10-06. The customer-managed policies `AxisAI-EC2-RolePolicy` and `AxisAI-EC2-Role-Policy` are deleted.

Production runtime identity remains `AxisAIProdRuntimeProfile` → `AxisAIProdRuntimeRole`. R4, R5 and R6 are independent follow-ups that do not keep SEC-001 open (§C). R4 (CloudWatch least privilege) was later remediated and closed on 2026-10-07, separately from SEC-001 (§A.5). R5 and R6 remain open follow-ups. This document contains no credentials.

This file is the durable record of the migration. It has five parts:

| Part | Sections | Nature |
| --- | --- | --- |
| **CURRENT VERIFIED STATE** | §A | Current production truth. §A.1 records the 2026-10-04 cutover; §A.4 records the final state after legacy retirement (2026-10-06); §A.5 records the runtime role after the R4 CloudWatch least-privilege remediation (closed 2026-10-07). Authoritative. |
| **CUTOVER EXECUTION** | §B | What the cutover task did, in order (2026-10-04). Historical execution record. |
| **RESIDUAL RISKS / FOLLOW-UPS** | §C | R1–R3 (done), R4 (independent follow-up, closed 2026-10-07) and R5–R6 (independent follow-ups outside SEC-001). |
| **RETIREMENT RECORD** | §D | The old-key deletion gate and the legacy credential/principal/policy retirement that followed it (executed). |
| **PRE-CUTOVER EVIDENCE** | §0–§13, Appendix, historical verdict | **Historical.** The preparation evidence and plan (2026-10-03) that justified the cutover. Statements there in present tense ("currently", "today", "static env") describe the pre-cutover state. Every IAM object named there other than `AxisAIProdRuntimeRole`/`AxisAIProdRuntimeProfile` has since been deleted. §A supersedes them. |

The production RDS incident and SEC-005 PR #378 remain separate from this work.

## A. CURRENT VERIFIED STATE

### A.1 Final cutover evidence (2026-10-04)

> The `OLD KEY` and `OLD ROLE/PROFILE` lines below record the state at the end of the cutover on 2026-10-04. Both objects were later deleted; the current state is in §A.4.

```text
CUTOVER DATE:        2026-10-04
RESULT:              SEC-001 CUTOVER COMPLETE

NEW ROLE:            AxisAIProdRuntimeRole
                     arn:aws:iam::852128326881:role/AxisAIProdRuntimeRole
NEW PROFILE:         AxisAIProdRuntimeProfile
                     arn:aws:iam::852128326881:instance-profile/AxisAIProdRuntimeProfile

WEB IDENTITY:        AxisAIProdRuntimeRole
                     arn:aws:sts::852128326881:assumed-role/AxisAIProdRuntimeRole/i-0c6f5352fc214e68d
                     credential method = iam-role
WORKER IDENTITY:     AxisAIProdRuntimeRole (same ARN, credential method = iam-role)

STATIC AWS ENV:      REMOVED
                     (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN absent
                      from host .env and from both containers)

OLD KEY (at cutover): fitx-s3-user AKIA…4XUI set INACTIVE
                      (later DELETED 2026-10-05 15:41Z, §A.4)

OLD ROLE/PROFILE (at cutover):
                     AxisAI-EC2-Role- left untouched for rollback
                     (later DELETED 2026-10-06 in SEC-001 R3B, §A.4)

ROLLBACK:            NOT REQUIRED
```

**Role composition.** `AxisAIProdRuntimeRole` has an EC2 trust policy (`ec2.amazonaws.com` → `sts:AssumeRole`) and:

- inline `AxisAIProdRuntimePolicy`, byte-equal to the finalized application policy in §6;
- AWS-managed `AmazonSSMManagedInstanceCore` (host agent);
- AWS-managed `CloudWatchAgentServerPolicy` (host CloudWatch agent).

> **Historical role composition.** This was the role shape at cutover. On 2026-10-06, R4 detached `CloudWatchAgentServerPolicy` from this role and replaced it with the narrow inline `AxisAIProdHostObservabilityPolicy`. The current shape is in §A.5.

**Unchanged by the cutover:** the production bucket stays `fitx-user-bucket-2026`, with the approved prefixes `avatars/`, `meals/` and `pump-checks/`. KMS is still **NOT REQUIRED** (SSE-S3). The Bedrock configuration is unchanged. The deployed application commit is unchanged. No code change was needed: the application already uses the default credential chain (§7).

### A.2 Post-cutover validation results

| Area | Result | Evidence |
| --- | --- | --- |
| S3 | **PASS** | Role-only smoke in production: PutObject, Get/HeadObject, DeleteObject and Presign all passed. Probe objects were cleaned up. No real user object was read. Unrelated-bucket access was **DENIED**, including a live denial against `axisai-user-storage` and simulated denials for the other unrelated buckets. |
| Bedrock | **PASS** | Role-only `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream` passed on the approved Sonnet 4.5 global inference-profile path. In-container deep health confirmed Bedrock reachability under the new role. An unapproved model was denied. |
| CloudWatch Runtime | **PASS** | `FitX/Runtime` publishes under `AxisAIProdRuntimeRole`. Application runtime metrics resumed after container recreation and publish normally. (See the lazy-start note in §B.2 and the permission overlap in §C, R4.) |
| SSM | **PASS** | The instance stays SSM-managed under the new profile. |
| CloudWatch Agent | **PASS** | Host log shipping continues under the new profile. |
| EC2 | **PASS** | Instance healthy. |
| Public health | **PASS** | `GET /health` → HTTP 200 `{"db":"ok","limiter_storage":"redis","status":"ok"}`. |
| Web | **PASS** | healthy, restart count 0. |
| Worker | **PASS** | healthy, restart count 0. |
| DB connectivity | **PASS** | `SELECT 1` passed from web and from worker. `database-1-dr` is healthy. |
| Auth/credential logs | **PASS (0)** | After cutover there were no new `NoCredentialsError`, `PartialCredentialsError`, `AccessDenied`, `ExpiredToken`, `InvalidClientTokenId`, `SignatureDoesNotMatch`, S3/Bedrock/CloudWatch authorization error, `OperationalError` or `Traceback`. |

Negative probes against the effective role (S3 bucket administration and listing, IAM, RDS, Secrets Manager, unapproved Bedrock model) were denied. The one exception is the `FitX/AI` metric-namespace probe, explained in §C, R4. That exception was historical: since R4 (§A.5), the effective role denies `FitX/AI`.

### A.3 Presigned URL condition (accepted, carried forward)

> Presigned URLs generated with temporary EC2-role credentials cannot be guaranteed to remain valid for the full requested six-hour avatar TTL. Current mobile behavior is unaffected because the mobile app does not consume these avatar URLs; long-lived web pages may require refresh after credential-bound URL expiry.

```text
CLASSIFICATION:        ACCEPTED
RELEASE BLOCKER:       NO
MOBILE/XCODE CHANGE:   NONE REQUIRED
```

The analysis is in §0.6 and §12.

### A.4 Final state after legacy retirement (2026-10-06)

```text
SEC-001 STATUS:      CLOSED

PRODUCTION IDENTITY: AxisAIProdRuntimeProfile -> AxisAIProdRuntimeRole
                     instance i-0c6f5352fc214e68d
WEB:                 credential method = iam-role, principal = AxisAIProdRuntimeRole
WORKER:              credential method = iam-role, principal = AxisAIProdRuntimeRole
STATIC AWS ENV:      ABSENT (host .env and both containers)

LEGACY ACCESS KEY:   fitx-s3-user AKIA…4XUI          DELETED 2026-10-05 15:41Z
LEGACY IAM USER:     fitx-s3-user                    DELETED 2026-10-05 16:41:46Z
LEGACY ROLE/PROFILE: AxisAI-EC2-Role                 DELETED 2026-10-05 ~18:57–19:00Z (R3A)
LEGACY ROLE/PROFILE: AxisAI-EC2-Role-                DELETED 2026-10-06 ~02:04–02:07Z (R3B)
LEGACY ROLE/PROFILE: fitx-ec2-s3-access              DELETED 2026-10-06 ~07:23–07:28Z (R3C)
ORPHAN POLICY:       AxisAI-EC2-RolePolicy           DELETED 2026-10-06 08:25:56Z
ORPHAN POLICY:       AxisAI-EC2-Role-Policy          DELETED 2026-10-06 (between 08:25:56Z and 08:28:07Z)
```

Final validation after the last deletion:

- SSM Online.
- Public `/health` 200, with `db` ok and `limiter_storage` redis.
- New credential/IAM errors: 0.

No other IAM object changed during the orphan-policy cleanup.

**Rollback after retirement.** The legacy role-swap rollback path no longer exists. The cutover originally kept `AxisAI-EC2-Role-` untouched so the profile association could be swapped back (§0.2, §10). That role and profile were deliberately retired in R3B, after the confidence and dependency gates passed. The static-key rollback path is gone too, because the key and the user are deleted. Neither is a gap: both were retired on purpose.

Application rollback is unchanged. It is revision-based (git commit → image → redeploy, `docs/DEPLOYMENT.md`) and runs under the current `AxisAIProdRuntimeRole`/`AxisAIProdRuntimeProfile`. Any recovery that needs a different AWS identity is a new, separately authorized IAM operation. It is not a rollback step.

### A.5 Runtime role after R4 CloudWatch least-privilege remediation (2026-10-06 – 2026-10-07)

> R4 was an independent follow-up, not part of SEC-001. SEC-001 closed on 2026-10-06 without it (§C). This section records the current runtime-role shape. §A.1, §B.1 and §6 still describe the historical state at cutover, when `CloudWatchAgentServerPolicy` was attached.

```text
R4 STATUS:           R4 CLOUDWATCH LEAST-PRIVILEGE REMEDIATION CLOSED (2026-10-07)

ACCOUNT:             852128326881
INSTANCE:            i-0c6f5352fc214e68d
PROFILE -> ROLE:     AxisAIProdRuntimeProfile -> AxisAIProdRuntimeRole

AxisAIProdRuntimeRole (current):
  INLINE:            AxisAIProdRuntimePolicy            (application; unchanged by R4)
                     AxisAIProdHostObservabilityPolicy  (host agent; added by R4)
  MANAGED:           AmazonSSMManagedInstanceCore
  NOT ATTACHED:      CloudWatchAgentServerPolicy        (detached by R4)
  PERMISSIONS BOUNDARY: none

PROD REVISION AT CLOSURE: 9e21be55eaa8c20859bbd765c47154781c026153
```

**Problem.** The AWS-managed `CloudWatchAgentServerPolicy` gave the shared runtime role more observability authority than the host agent or the application needed. It granted `cloudwatch:PutMetricData` for any namespace, broad CloudWatch Logs writes and unused X-Ray permissions. A compromised application or container holding the shared IMDS role credentials could therefore publish arbitrary custom metrics, write into unrelated log groups, change log retention and use X-Ray, even though `AxisAIProdRuntimePolicy` on its own restricts metrics to `FitX/Runtime`.

**Change (R4-01, 2026-10-06).** Two IAM mutations, both on `AxisAIProdRuntimeRole` only:

1. Put the inline `AxisAIProdHostObservabilityPolicy`.
2. Detached `arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy`.

Nothing else changed:

- no other role's attachments;
- no AWS-managed policy object;
- no instance-profile association;
- no static credentials;
- no deploy, container restart, agent restart or `.env` change.

**Host-observability policy intent.** It permits only what the host CloudWatch agent was proven to need:

| Permission | Scope |
| --- | --- |
| `cloudwatch:PutMetricData` | namespace `CWAgent` only |
| `logs:CreateLogStream`, `logs:PutLogEvents` | log groups `/axisai/app`, `/axisai/nginx/access` and `/axisai/nginx/error` only |
| `logs:DescribeLogGroups` | needed by the agent at startup |
| `ec2:DescribeTags` | needed by the agent at startup |

`FitX/Runtime` is still authorized by the application policy `AxisAIProdRuntimePolicy` (§6), not by the host policy.

**Intentionally denied after R4.** The effective role now denies:

- `cloudwatch:PutMetricData` to `FitX/AI` and to any other namespace except `CWAgent` and `FitX/Runtime`;
- CloudWatch Logs writes to destinations other than the three AxisAI groups;
- `logs:CreateLogGroup`, `logs:PutRetentionPolicy` and `logs:DescribeLogStreams`;
- `ec2:DescribeVolumes`;
- X-Ray `PutTraceSegments`, `PutTelemetryRecords`, `GetSamplingRules`, `GetSamplingTargets` and `GetSamplingStatisticSummaries`.

The `FitX/AI` deny is intentional because production runs with `AI_METRICS_ENABLED` unset (off). Enabling AI metrics would need a separately authorized policy change.

**Validation record.**

| Layer | Result |
| --- | --- |
| **R4-00 discovery** (read-only, 0 mutations) | Found that `CloudWatchAgentServerPolicy` caused the permission overlap. A narrow same-role replacement was viable, and the candidate policy passed both positive and negative simulation. |
| **R4-01 remediation** (2026-10-06, immediate checks) | `CWAgent` metrics continued, `FitX/Runtime` continued and log ingestion continued. 44/44 effective-permission cases passed. Web healthy, worker healthy, SSM Online. Static AWS credentials absent. 0 new IAM or credential errors. |
| **R4-02 / final closure** (natural host boot on 2026-10-07, read-only checks) | **Agent startup:** passed, including the `DescribeLogGroups` and `DescribeTags` startup paths. **Logs:** post-boot ingestion passed for `/axisai/app`, `/axisai/nginx/access` and `/axisai/nginx/error`. **Metrics:** `CWAgent` post-boot metrics passed. `FitX/Runtime` resumed in the same minute as the first natural non-health traffic: first post-boot datapoint 2026-10-07T07:03Z, latest at closure 17:55Z, 653 consecutive one-minute `ThreadReserve` datapoints with no gaps. **Permissions:** 44/44 effective-permission simulation passed. 0 new runtime-metric publish errors, 0 new CloudWatch authorization errors and 0 new AWS credential errors. **Health:** web, worker, DB, Redis and SSM healthy; static AWS credentials absent. **Unchanged:** S3 and Bedrock authority. **Closure:** 16/16 criteria passed. |

**CreateLogStream (operational confirmation, not an R4 blocker).** The narrowed policy authorizes `logs:CreateLogStream` on the three AxisAI groups, and the IAM simulation passes. No real post-remediation `CreateLogStream` call has happened yet: the existing containers kept their log streams, and container IDs survive host stop/start. Confirm that a natural `CreateLogStream` succeeds during the next ordinary container recreation or deploy. Do not trigger a deploy for this.

**Accepted operational trade-off.** The host policy deliberately does not allow `logs:CreateLogGroup` or `logs:PutRetentionPolicy`. Creating log groups and owning their retention are operator/FinOps responsibilities, not runtime-agent authority. This is by design, not a defect.

**Adjacent findings, out of R4 scope (separate follow-ups, not fixed here).**

- **A1.** `AmazonSSMManagedInstanceCore` carries broad `ssm:GetParameter(s)` authority.
- **A2.** An empty `access.log` log group is orphaned.
- **A3.** `CloudWatchAgentServerPolicy` is still attached to `instanceRole`, `FitX-EC2-Bedrock-Role` and `EC2-CloudWatch-Role`.
- **A4.** Some Lambda log groups have no retention setting.
- **A5.** IMDS hop limit 2 means the host and the containers share one identity.

**Rollback (only on a proven regression).** Re-attach `CloudWatchAgentServerPolicy` first, then delete `AxisAIProdHostObservabilityPolicy`. This is a separately authorized IAM operation, never a deploy step.

## B. CUTOVER EXECUTION (2026-10-04)

### B.1 Sequence

1. A read-only pre-cutover production validation returned GO. Its checks: EC2 and RDS state, alarms, container health, DB `SELECT 1` and the log baseline.
2. Created `AxisAIProdRuntimeRole` (EC2 trust policy), attached `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`, put the inline `AxisAIProdRuntimePolicy` (= §6), and created `AxisAIProdRuntimeProfile` containing the role.
3. Replaced the instance-profile association on `i-0c6f5352fc214e68d`, from `AxisAI-EC2-Role-` to `AxisAIProdRuntimeProfile` (07:25Z). The old role and profile were left untouched. *(Historical note: this role/profile was later retired during SEC-001 R3B on 2026-10-06, after the confidence and dependency gates passed.)*
4. With static keys still present, verified the new role in both containers using role-only subprocesses (`env -u` for the key variables only). Ran the positive and negative smokes.
5. Removed the static AWS key lines from host `.env`, keeping a protected root-only rollback copy. Recreated worker and web (`--no-build`, same image) so the processes dropped the inherited environment.
6. Re-verified the default-chain identity in both containers (`iam-role` → `AxisAIProdRuntimeRole`), then re-ran S3, Bedrock, metrics, health and log checks.
7. Set the `fitx-s3-user` access key `AKIA…4XUI` **Active → Inactive** (07:41Z). It was not deleted during the cutover. *(Historical note: it was deleted on 2026-10-05 after the §D gate passed.)*
8. Final validation passed. The protected rollback copy of the old `.env` lines was securely removed. Rollback was not required.

### B.2 Cutover observations (recorded, not blockers)

1. **Web recreation 5xx window.** Public traffic was disrupted for about 6 seconds while the web container was recreated: 3 × 502 and 1 × timeout. This is expected from the current single-instance, single-container replacement model. It is an availability/deployment follow-up (§C, R6), not a SEC-001 failure.
2. **Runtime metrics lazy start.** After web recreation, `FitX/Runtime` had a short gap until the first gauge write started the metrics flusher thread. Publishing then resumed under `AxisAIProdRuntimeRole`. This is application observability behavior, not an IAM failure.
3. **Deep health from the host.** `/health?deep=1` curled from the host returns the shallow body, because the request arrives from the Compose bridge gateway rather than the internal-allowed address. Full Bedrock deep-health validation currently has to run inside the web container. Health behavior was not changed.

## C. RESIDUAL RISKS / FOLLOW-UPS

R1–R3 were the SEC-001 identity-remediation residuals. All three are **DONE**. R4–R6 are **independent follow-ups that do not block SEC-001 closure**. SEC-001's objective was to move production off static credentials and retire the legacy identities, and that objective is complete. None of R4–R6 was fixed as part of SEC-001. R4 was later remediated and closed on its own (2026-10-07, §A.5). R5 and R6 remain open.

| ID | Item | State | Record |
| --- | --- | --- | --- |
| R1 | Old `fitx-s3-user` access key `AKIA…4XUI` | **DONE.** Legacy access key deleted 2026-10-05 15:41Z. | Deleted after the §D gate passed. The key was Inactive, its last use was before the cutover's container recreation, and the dependency proof passed. |
| R2 | Old `fitx-s3-user` IAM user | **DONE.** `fitx-s3-user` deleted 2026-10-05 16:41:46Z, after a dependency audit. | The audit found no active authentication method and no resource-policy, application, deployment or rollback dependency. The user's inline policies were deleted and `AmazonS3FullAccess` was detached before `delete-user`. |
| R3 | Legacy EC2 roles/instance profiles and their customer policies | **DONE.** R3A `AxisAI-EC2-Role` retired; R3B `AxisAI-EC2-Role-` retired; R3C `fitx-ec2-s3-access` retired; orphan customer-managed policies deleted. | R3 was split by risk. Each part had its own inventory, authorization and post-delete production validation (§D.2). |
| R4 | CloudWatch permission overlap | **DONE / CLOSED** (2026-10-07). Independent follow-up, never a SEC-001 blocker; fixed separately after SEC-001 closed. | **Resolution (§A.5):** the broad AWS-managed `CloudWatchAgentServerPolicy` was removed from the production runtime role, and the narrow inline `AxisAIProdHostObservabilityPolicy` was installed. The required `CWAgent`, log and `FitX/Runtime` paths still work. Arbitrary metric namespaces and log destinations are now denied. Cold-start validation passed, and 16/16 final closure criteria passed. Still to confirm operationally: a natural `CreateLogStream` at the next ordinary deploy. **Original record (historical):** The application role also carried `CloudWatchAgentServerPolicy`, which grants unconditioned `cloudwatch:PutMetricData`. **Impact:** the effective CloudWatch permission surface is broader than the application-only policy. `AxisAIProdRuntimePolicy` alone restricts metrics to `FitX/Runtime`, but the effective role can also publish to `FitX/AI` and other namespaces, so that one simulated deny does not hold at the role level. This is not a cutover failure: an EC2 instance carries one instance profile, so host-agent and application identity are combined by design (§0.2). **Follow-up:** split host-agent and application identities, or redesign the telemetry path, so application permissions are isolated from `CloudWatchAgentServerPolicy`. That is a separately authorized change to the runtime role. |
| R5 | Presigned URL TTL | **Independent follow-up. Does not block SEC-001 closure.** Accepted condition (§A.3). | None for the current release. Revisit only if a product contract ever requires a guaranteed 6-hour URL. |
| R6 | Single-container recreation availability | **Independent follow-up. Does not block SEC-001 closure.** About 6 s of 502/timeout during web recreation (§B.2). | Deployment-architecture follow-up (for example overlapping replacement or a health-gated swap). Outside SEC-001. |

The out-of-scope findings recorded in §12 (`fitx-user-storage` public-read policy, the frozen 62-object `axisai-user-storage` copy, no versioning/lifecycle on `fitx-user-bucket-2026`) are still open and still outside SEC-001.

## D. Old-key deletion gate and legacy retirement (EXECUTED)

```text
GATE:              EXECUTED
DEPENDENCY PROOF:  PASSED
OLD KEY:           DELETED 2026-10-05 15:41Z
FOLLOW-ON:         R2 (user) and R3A/R3B/R3C (roles/profiles) retirement followed,
                   then the orphan customer-policy cleanup
```

This section records the gate as it was designed and how it was executed. Nothing in it is pending. No IAM object it names still exists, except `AxisAIProdRuntimeRole` and `AxisAIProdRuntimeProfile`.

### D.1 Gate design (as designed on 2026-10-04)

The gate required a separately authorized task, after an observation window confirming all of the following:

```text
web identity still     = AxisAIProdRuntimeRole
worker identity still  = AxisAIProdRuntimeRole

public /health         healthy
S3                     healthy
Bedrock                healthy
runtime metrics        healthy (FitX/Runtime arriving)

NoCredentialsError     = 0
AccessDenied           = 0
ExpiredToken           = 0
InvalidClientTokenId   = 0
SignatureDoesNotMatch  = 0
```

It also required that the old key show no use after its deactivation (IAM access-key last-used). R2 (the user) and R3 (the old roles/profiles) were to follow only after the key deletion, each with its own dependency audit.

### D.2 Execution record (2026-10-05 – 2026-10-06)

Every step below was a separately authorized task. Each step was preceded by a read-only inventory and followed by production validation:

- web and worker on `iam-role` / `AxisAIProdRuntimeRole`, with no static env;
- healthy containers;
- public `/health` 200;
- 0 new credential/IAM errors.

No step deployed or restarted the application.

| Step | Object | When | Gate evidence |
| --- | --- | --- | --- |
| R1 | Access key `AKIA…4XUI` (`fitx-s3-user`) deleted | 2026-10-05 15:41Z | Key was the user's only key and Inactive. Its last use (2026-10-04 07:33Z, CloudWatch) came before the cutover's container recreation. Host `.env`, effective Compose config and both containers had no static AWS variables. Repository references were docs, comments and test fixtures only. |
| R2 | IAM user `fitx-s3-user` deleted | 2026-10-05 16:41:46Z | No active authentication method; no resource-policy, application, deployment or rollback dependency. Its two inline policies were deleted and `AmazonS3FullAccess` was detached first. |
| R3A | Role + instance profile `AxisAI-EC2-Role` deleted | 2026-10-05 ~18:57–19:00Z | Unused legacy pair. No instance association. Its customer policy `AxisAI-EC2-RolePolicy` was detached, not deleted, and was left as an orphan. |
| R3B | Role + instance profile `AxisAI-EC2-Role-` deleted | 2026-10-06 ~02:04–02:07Z | The **former production** identity, replaced at the 2026-10-04 cutover. Its last observed activity ended 2026-10-04 12:29:56Z. That short post-cutover tail was investigated and attributed to temporary credentials issued before the cutover, used by host agents until they refreshed. No unexplained later use was found. Its customer policy `AxisAI-EC2-Role-Policy` was detached, not deleted, and was left as an orphan. |
| R3C | Role + instance profile `fitx-ec2-s3-access` deleted | 2026-10-06 ~07:23–07:28Z | An older, broader legacy pair (`AmazonS3FullAccess`, `AmazonBedrockFullAccess`, `AmazonAPIGatewayInvokeFullAccess` and the two agent policies). Last used 2026-07-10. The final audit found no active EC2 association; no application, deployment, resource-policy or rollback dependency; and no unique required capability. |
| Orphans | Customer-managed policies `AxisAI-EC2-RolePolicy` and `AxisAI-EC2-Role-Policy` deleted | 2026-10-06: the first at 08:25:56Z, the second between 08:25:56Z and 08:28:07Z | Both had `AttachmentCount = 0`, `PermissionsBoundaryUsageCount = 0`, no entities, and only the `v1` default version. An IAM diff afterwards showed that only these two policies were removed; no other IAM object changed. |

**Evidence limits.** The account had no full CloudTrail trail during these audits. Usage evidence came from IAM last-used and Access Advisor data, the 90-day CloudTrail Event History, resource-policy scans and repository search. The retirements rest on that combined evidence plus post-delete production validation. They do not rest on a complete API audit log.

---

# PRE-CUTOVER EVIDENCE AND PLAN (historical, 2026-10-03)

> Everything below was written **before** the cutover. It is the evidence and plan the cutover executed. Present-tense descriptions such as "currently uses static env credentials" or "`fitx-s3-user` is the active identity" describe production on 2026-10-03 and are **superseded by §A**. Historically, this preparation was graded **CUTOVER READY WITH ONE EXPLICIT CONDITION**.
>
> **Postscript (2026-10-06).** The legacy IAM objects named below no longer exist: `fitx-s3-user` and its key, the `AxisAI-EC2-Role-` role/profile and its `AxisAI-EC2-Role-Policy`. All were deleted during SEC-001 retirement (§A.4, §D.2). They are named here as evidence of the pre-cutover state, not as current resources.

## 0. Production-operator evidence (2026-10-03, historical)

Read-only metadata collected with the production operator `arn:aws:iam::852128326881:user/axisai-deployer` (default profile, `eu-central-1`). No create/put/update/attach/delete/start/stop call was issued. No object was listed or read. No Bedrock model was invoked. No metric was published.

### 0.1 Instance profile and role

| Field | Live value |
| --- | --- |
| Instance profile | `arn:aws:iam::852128326881:instance-profile/AxisAI-EC2-Role-` (created 2026-07-10) |
| Contained role | `AxisAI-EC2-Role-`, `arn:aws:iam::852128326881:role/AxisAI-EC2-Role-`. No permissions boundary. Max session 3600 s. Last used 2026-10-03 13:47Z. |
| Trust | `ec2.amazonaws.com` → `sts:AssumeRole`. No conditions. |
| Attached managed | `AxisAI-EC2-Role-Policy` (customer, v1, attached only here), `CloudWatchAgentServerPolicy` (AWS, v3), `AmazonSSMManagedInstanceCore` (AWS, v2) |
| Inline | none |

`AxisAI-EC2-Role-Policy` v1 grants:

- `secretsmanager:GetSecretValue`/`DescribeSecret` on `secret:axisai/prod*`;
- `s3:ListBucket`/`GetBucketLocation` on `axisai-user-storage`;
- `s3:Get/Put/DeleteObject` and `s3:Put/GetObjectAcl` on `axisai-user-storage/*`;
- `bedrock:InvokeModel`, `InvokeModelWithResponseStream` and `ListFoundationModels` on `*`;
- `logs:Create*`/`PutLogEvents`/`DescribeLogStreams` on `*`;
- `cloudwatch:PutMetricData` on `*` (no namespace condition);
- SSM agent channel actions.

The two AWS policies are the standard CloudWatch agent and SSM agent sets.

**IAM Access Advisor (action-level, generated 2026-10-03):**

- **Role:** authenticated to `cloudwatch`, `ec2`, `ec2messages`, `logs`, `ssm` and `ssmmessages`. That is host agent traffic: `CreateLogStream`, `DescribeLogGroups`, `GetParameter`, `UpdateInstanceInformation` and `ListInstanceAssociations`. The role has **never authenticated to `s3`, `bedrock`, or `secretsmanager`**.
- **`fitx-s3-user`:** authenticated to `s3` (last 2026-09-29 07:09Z), `bedrock` (last 2026-10-02 18:48Z, the deploy deep-health probe) and `cloudwatch` (last 2026-10-03 13:44Z, minutely `FitX/Runtime` flush).

**Effective summary.** The role is today a **host-agent identity only**. Its app grants are dormant and aimed at the wrong bucket. Every application AWS call observed in production is made by the static `fitx-s3-user` key.

### 0.2 Role reuse decision: CREATE NEW DEDICATED ROLE

- **Do not reuse the current policy.** It names `axisai-user-storage`, which is not the live bucket (§0.3). It grants Bedrock on `*`, Secrets Manager `axisai/prod*` (never used; the app reads `.env`), `ListBucket` (no call site) and object-ACL actions (meaningless under `BucketOwnerEnforced`). Its `PutMetricData` has no namespace condition.
- **Create `AxisAIProdRuntimeRole` and a new instance profile.** Attach the finalized application policy (§6) **plus** `AmazonSSMManagedInstanceCore` and `CloudWatchAgentServerPolicy`.
- **Why the agent policies stay:** an EC2 instance carries exactly one instance profile. The SSM agent and the CloudWatch agent actively use the current role (Access Advisor above). A runtime-only role would break SSM deploys and log shipping. Containers can reach IMDS (hop limit 2), so they can also use these host permissions; that is accepted and documented in §12.
- **Why a new role instead of editing the old one:** cutover becomes `replace-iam-instance-profile-association`, and rollback is the same call back to `AxisAI-EC2-Role-`, which stays untouched. *(Historical note: this role/profile was later retired during SEC-001 R3B on 2026-10-06, after the confidence and dependency gates passed. This role-swap rollback no longer exists; see §A.4.)*

### 0.3 Production user-storage bucket: `fitx-user-bucket-2026`

| Bucket | Created | `NumberOfObjects` (AWS/S3 daily storage metric) | Interpretation |
| --- | --- | --- | --- |
| `fitx-user-bucket-2026` | 2026-06-07 | 1 → 62 (Jun 9 – Jul 8), then 62 → 1 on 2026-08-15, then 1 → 6 (Aug 15 – Sep 15); latest datapoint 2026-10-02 | **Live.** It is the only bucket whose contents changed after July. |
| `axisai-user-storage` | 2026-07-10, same day as the instance role/profile | 62 since 2026-07-11, unchanged through 2026-10-02 | A one-time 62-object copy for a role-based migration that never cut over. The role never accessed S3. |
| `fitx-user-storage` | 2026-06-15, 5 min before the `fitx-s3-user` key | no storage datapoints (empty) | Unused. Public-read bucket policy, see §12. |

Supporting evidence:

- Only `fitx-s3-user` has ever authenticated to S3 (§0.1).
- The 2026-10-03 audit independently found current user objects in `fitx-user-bucket-2026`.
- The repository has no committed bucket name: `S3_BUCKET_NAME` comes only from host `.env`, and git history contains none of the three names.

**PRODUCTION USER STORAGE BUCKET:** `fitx-user-bucket-2026`. **CONFIDENCE:** CONFIRMED. The live `S3_BUCKET_NAME` is `fitx-user-bucket-2026` in both the web and worker containers (§0.5).

### 0.4 Live bucket metadata (`fitx-user-bucket-2026`)

| Property | Exact AWS response |
| --- | --- |
| Region | `eu-central-1` |
| Default encryption | `SSEAlgorithm: AES256`, `BucketKeyEnabled: true`, `BlockedEncryptionTypes: [SSE-C]` |
| Public Access Block | all four `true` |
| Versioning | empty response (never enabled) |
| Object ownership | `BucketOwnerEnforced` (ACLs disabled) |
| Bucket policy | `NoSuchBucketPolicy` |
| Lifecycle | `NoSuchLifecycleConfiguration` |
| CORS | `NoSuchCORSConfiguration` |

**KMS REQUIRED: NO.** The bucket uses SSE-S3 and the app sends `ServerSideEncryption="AES256"`. **BUCKET POLICY ROLE CHANGE REQUIRED: NO.** There is no bucket policy, and same-account identity policy is sufficient.

### 0.5 Container identity and role fallback: CLOSED (2026-10-03)

The production operator ran the read-only appendix probe manually via SSM `AWS-RunShellScript` on `i-0c6f5352fc214e68d`, and it completed successfully. In each container, the role-only run unset `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_SESSION_TOKEN` with `env -u` for one subprocess only. The container environment, host `.env`, Compose and IAM were not changed, and no container was restarted. The only AWS call was `sts:GetCallerIdentity`. No S3, Bedrock or CloudWatch call was made, and no credential value was printed.

(An automated SSM attempt from the operator workstation was refused by its tool-permission classifier before it reached AWS. That refusal was not worked around. The probe was run manually instead.)

| | web | worker |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | present | present |
| `AWS_SECRET_ACCESS_KEY` | present | present |
| `AWS_SESSION_TOKEN` | absent | absent |
| `AWS_PROFILE` / `AWS_DEFAULT_PROFILE` | absent / absent | absent / absent |
| Current credential method (`botocore`) | `env` | `env` |
| Current principal | `arn:aws:iam::852128326881:user/fitx-s3-user` | `arn:aws:iam::852128326881:user/fitx-s3-user` |
| Role-only credential method | `iam-role` | `iam-role` |
| Role-only principal | `arn:aws:sts::852128326881:assumed-role/AxisAI-EC2-Role-/i-0c6f5352fc214e68d` | same |
| Role fallback verified | **YES** | **YES** |

What the probe proves:

1. Both containers use static environment credentials today, and those credentials resolve to `fitx-s3-user`. This upgrades the earlier HIGH (inferred) grade to CONFIRMED.
2. The static env credentials take precedence over the EC2 instance role.
3. With those three variables unset for a single subprocess, boto3's default provider chain reaches EC2 IMDS (IMDSv2, hop limit 2) from inside both containers. It resolves the instance role, and `sts:GetCallerIdentity` succeeds through it.
4. The application already uses the standard credential chain, with no explicit keys and no named profile (§7). No credential-specific application change is needed for the cutover.

> Static host `.env` credentials are currently overriding the EC2 instance role, and both containers can already resolve the instance role through the standard AWS credential chain when those environment credentials are absent.

**Live non-secret configuration** (identical in web and worker), with each value resolved against current source defaults in `app/config.py`:

| Key | Live env value | Effective value | Source |
| --- | --- | --- | --- |
| `S3_BUCKET_NAME` | `fitx-user-bucket-2026` | `fitx-user-bucket-2026` | live env |
| `RUNTIME_METRICS_ENABLED` | `1` | enabled | live env |
| `AI_METRICS_ENABLED` | unset | **disabled** | `os.getenv("AI_METRICS_ENABLED", "0") == "1"` |
| `BEDROCK_MODEL` | unset | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | `os.getenv("BEDROCK_MODEL", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")` |

The effective Bedrock model is the global Sonnet 4.5 inference profile, so it matches the profile named in §6. AI metrics are off, so the application emits no `FitX/AI` metric. The probe did not print `RUNTIME_METRICS_NAMESPACE`. Its source default is `FitX/Runtime`. That namespace is the only one `fitx-s3-user` may publish to, and the identity publishes every minute (Access Advisor, §0.1). This document therefore treats the live namespace as `FitX/Runtime`. The cutover's metric-arrival check (§9) re-verifies it.

### 0.6 Explicit cutover condition: presigned URL lifetime

Presigned URLs generated with temporary EC2-role credentials cannot be guaranteed to remain valid for the full requested six-hour avatar TTL. Current mobile behavior is unaffected because the mobile app does not consume these avatar URLs. Web pages holding old signed image URLs may require refresh after credential-bound URL expiry.

This is an **explicit operational/product condition, not an identity blocker** (evidence in §12). Web regenerates avatar URLs on every request. Nothing caches them. The native API exposes no avatar field. **No mobile or Xcode change is required for the current release.**

### Evidence-gate review (first pass, staging operator; superseded by §0)

| Gate | Verified finding | Remaining read-only evidence |
| --- | --- | --- |
| Current container identity and credential source | **Unknown live.** Historical deployment instructions identify a host `.env` IAM-user key; Compose injects `.env` into both containers. Environment credentials win over EC2 metadata credentials if present. | An authorized production operator runs `aws sts get-caller-identity` inside **web and worker**, reporting ARN/account/principal type only, and reports presence/absence (never values) of `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`, shared-credential/config-file overrides, and `AWS_EC2_METADATA_DISABLED`. The staging operator has no production-container execution grant. |
| EC2 instance profile and contained role | `i-0c6f5352fc214e68d` is running with profile `AxisAI-EC2-Role-`; IMDSv2 is required, endpoint enabled, hop limit 2. The 2026-10-03 `DescribeInstances` read reconfirmed this. | Profile's role name, trust policy, inline/attached policies, and last-used metadata from an authorized production IAM reader. `iam:GetInstanceProfile` was already denied; `ec2:DescribeIamInstanceProfileAssociations` returned an explicit deny on 2026-10-03. No alternative access route was attempted. Suitability for reuse and host-agent impact remain unknown. |
| Production S3 and KMS | Code uses one configured `S3_BUCKET_NAME`; all live upload call sites pass `avatars`, `meals`, or `pump-checks`. No runtime bucket listing call exists. | Production config **bucket name/region** (values of these non-secret configuration fields only), bucket encryption/versioning/Public Access Block/policy metadata, exact KMS key and key policy if SSE-KMS, and a database **key-prefix inventory without object reads**. Account-wide bucket listing was explicitly denied earlier. |
| Bedrock and metrics | Source defaults to Sonnet 4.5 global profile in `eu-central-1`; code invokes both normal and streaming inference. Metrics code only calls `PutMetricData`, with default `FitX/AI` and `FitX/Runtime` namespaces. | Non-secret live `BEDROCK_MODEL`, `BEDROCK_REGION`, metric flags/namespaces; authorized `GetInferenceProfile` model ARN list and currently attached identity-policy scope. Staging `GetInferenceProfile` was explicitly denied earlier. |
| Six-hour avatar presigned URL | **Incompatible with a guaranteed six-hour lifetime** when signed by EC2 role credentials. Effective lifetime is `min(21,600 seconds, remaining lifetime of the signing credential)`. | Confirm whether existing clients tolerate an earlier expiry and can obtain a fresh avatar URL. If the six-hour guarantee is required, design a server-side refresh/signing path and validate it before cutover. No production object or client behavior was changed here. |

**CURRENT CONTAINER IDENTITY:** unverified. **CREDENTIAL SOURCE:** historical static `.env` key, current source unverified. **CONFIDENCE:** high for repository behavior and EC2 profile attachment; low for live container/provider and production IAM/S3/Bedrock metadata. The staging role's `sts:GetCallerIdentity` returned `axisai-staging-operator/yusuf`, which establishes the investigator identity only, not the production container identity.

## 1. Executive verdict

| Question | Verdict |
| --- | --- |
| Does application code require static AWS keys? | **No.** S3 and CloudWatch use `boto3.client` without credential arguments; Bedrock uses `AnthropicBedrock` without credential arguments. |
| Can runtime use a role without mobile or API changes? | **Yes, proven live.** Both web and worker resolve `AxisAI-EC2-Role-` via IMDS once the static env key is absent (§0.5). |
| Required S3 bucket/resources | `fitx-user-bucket-2026` (CONFIRMED live, §0.5) under `avatars/`, `meals/`, and `pump-checks/`. |
| Required S3 actions | `GetObject`, `PutObject`, `DeleteObject` on those object prefixes. No bucket-level action found in runtime code. |
| Bucket policy / KMS change? | **No.** No bucket policy; SSE-S3 `AES256`; `BucketOwnerEnforced`; PAB fully on (§0.4). |
| Presigned URL effect? | **Yes, accepted as the explicit cutover condition (§0.6).** Most GET URLs request 1 hour; avatar URLs request 6 hours. A URL signed with EC2 role credentials expires no later than its signing credential, so the 6-hour lifetime is not guaranteed. Web re-signs on every request; mobile does not consume avatar URLs. |
| Code change required? | **No.** No SDK construction change, and none for the URL condition: the six-hour avatar lifetime is not a product contract (§12). |
| Is production cutover safe after the RDS incident? | *(Historical verdict: cutover ready with one explicit condition, §0.6.)* **Superseded: the cutover was executed and completed on 2026-10-04 (§A).** |

**Current credential source:** CONFIRMED live (§0.5). Both containers use the static environment key for `fitx-s3-user`, loaded from host `.env` (`botocore` method `env`), and it overrides the attached instance profile.

**Application dependency on static keys:** NO. **Role-compatible without code change:** YES. Container IMDS access is proven live, and URL lifetime is the accepted condition in §0.6. **Confidence:** CONFIRMED for both the live credential source and the fallback. Bucket and policy facts are from live metadata (§0.3–0.4).

## 2. Pre-cutover credential architecture (historical; current state is §A)

```text
production EC2 i-0c6f5352fc214e68d (AxisAI-server)
  -> Docker Compose web + worker; both consume host .env via env_file
  -> SDK default credential chain; environment credentials precede EC2 IMDS
  -> host .env key for fitx-s3-user (CONFIRMED live in web + worker, method env)
  -> previously verified broad S3, scoped Bedrock, CloudWatch metric capability

same EC2 currently has instance profile AxisAI-EC2-Role- attached
  -> role AxisAI-EC2-Role-: host SSM + CloudWatch agents only (Access Advisor, §0.1)
  -> its dormant app grants target axisai-user-storage, not the live bucket
  -> reachable from web + worker via IMDS once env keys are absent (CONFIRMED, §0.5)
```

Read-only EC2 metadata confirmed the instance is running, IMDSv2 is required, endpoint enabled, and hop limit is 2. EC2 is therefore the appropriate native runtime mechanism. Use a separate `AxisAIProdRuntimeRole` and instance profile. The existing profile association should be **replaced** after reviewing its existing role and host-agent requirements; do not silently edit or remove the existing host role. EC2 supports replacement of a profile association on a running instance; application processes may still need a controlled recreation to remove environment credentials. AWS recommends replacing the profile to force a role change rather than only swapping its contained role. See [EC2 profile attachment](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/attach-iam-role.html) and [instance profiles](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_use_switch-role-ec2_instance-profiles.html).

## 3. AWS usage matrix

| Service | SDK operation | Code path | Runtime required | Resource |
| --- | --- | --- | --- | --- |
| S3 | `PutObject` | `s3_helper.upload_image`; training, mobile workout/Pump Check, meal log, avatar | Yes | configured bucket: `pump-checks/*`, `meals/*`, `avatars/*` |
| S3 | `GetObject` | `s3_helper.get_object_bytes`; coach image analysis, Pump Check comparison | Yes | configured user-image prefixes; coach key is owner-checked but caller-supplied; verify legacy DB keys without object reads |
| S3 | signed `GetObject` | `s3_helper.generate_presigned_url`; galleries, Physique Progress, avatars, meal views | Yes | same three prefixes; signed request needs `GetObject` |
| S3 | `DeleteObject` | `s3_helper.delete_meal_photo` / `delete_managed_object`; account deletion and replacement | Yes | same three prefixes |
| Bedrock Runtime | `InvokeModel` | `app/services/ai.py`, `ai_coach.py` via AnthropicBedrock `messages.create` | Yes when Bedrock enabled | configured Sonnet 4.5 global inference profile and its model ARNs |
| Bedrock Runtime | `InvokeModelWithResponseStream` | `ai_stream.py`, `bedrock_health.py` via `messages.stream` | Yes when Bedrock enabled | same profile/model |
| CloudWatch | `PutMetricData` | `ai_metrics.py`, `runtime_metrics.py` | Yes when corresponding flags enabled | namespaces `FitX/AI`, `FitX/Runtime` by default; confirm live flags/namespaces |
| Cognito IDP | unsigned user-pool calls (`SignUp`, `InitiateAuth`, `ForgotPassword`, `DeleteUser`, etc.) | `cognito_service.py` | Application calls yes; IAM grant **no** (`signature_version=UNSIGNED`) | configured app client |
| CloudWatch Logs | none in app; Docker JSON logs collected by **host** CloudWatch Agent | Compose / host agent | Host identity only, separately assess when replacing profile | `/axisai/app` log group |
| SSM | no app call; deployment control and host SSM agent | `scripts/deploy_control.py` | Deployment/host management only | exclude from application permissions |
| SES/SNS/SQS/Secrets Manager/KMS/ECR/RDS APIs/IAM | no application SDK call found | repository search | No, unless production metadata proves hidden host dependency | none in application role |

The deployment workflow uses GitHub OIDC and its separate deployer role. It is not the application role. `scripts/staging_control.py` uses SSM in staging only. The policy analyzer baseline was generated with `uvx iam-policy-autopilot@latest generate-policies s3_helper.py app/services/ai_metrics.py app/services/runtime_metrics.py app/services/cognito_service.py --region eu-central-1 --account 852128326881 --service-hints s3 cloudwatch cognito-idp --pretty`; it over-expanded S3 ACL, retention, version, Object Lambda, and KMS actions and treated unsigned Cognito calls as signed. Those are excluded based on actual call arguments. Do not upload that generated policy.

## 4. S3 resource and photo data flow

| Bucket / prefix | Purpose and sensitivity | Read | Write | Delete | List | Runtime admin |
| --- | --- | --- | --- | --- | --- | --- |
| `fitx-user-bucket-2026/pump-checks/<user_id>/<YYYY>/<MM>/<uuid>.<ext>` | Body/progress photos; sensitive | bytes for AI/comparison, presigned GET for gallery | Pump Check upload | account deletion / check lifecycle | No | No |
| `fitx-user-bucket-2026/meals/<user_id>/<YYYY>/<MM>/<uuid>.<ext>` | Meal images; private user content | presigned GET | meal upload | meal correction / account deletion | No | No |
| `fitx-user-bucket-2026/avatars/<user_id>/<YYYY>/<MM>/<uuid>.<ext>` | Profile images; private user content | presigned GET | avatar upload | avatar replacement / account deletion | No | No |

`S3_BUCKET_NAME` is server configuration, not a request parameter. `_build_key` derives the user segment and UUID on the server. Delete functions validate a tight key grammar and owner ID. Download and presign helpers validate the user segment when passed `expected_user_id`; some callers first prove ownership through database queries. IAM prefix isolation can prevent access to unrelated prefixes and buckets, but one shared runtime role cannot enforce per-user isolation for all user IDs. Application authorization remains the per-user boundary. Audit every caller for `expected_user_id` or prior owner query before cutover; this work does not alter authorization.

No runtime `ListBucket`, `ListAllMyBuckets`, `HeadBucket`, tagging, ACL, lifecycle, bucket policy, bucket encryption, bucket create/delete, or object attribute API call was found. `PutObject` sets `ServerSideEncryption="AES256"` and does not set ACL. The live bucket settings were verified from production metadata in §0.4: SSE-S3 `AES256`, Public Access Block fully on, `BucketOwnerEnforced`, no bucket policy, versioning never enabled, no lifecycle. No KMS is involved. No user object was read to establish this.

Presigned URLs use `GetObject`: default 3,600 seconds; avatar in `app/models.py` requests 21,600 seconds. AWS states that an EC2 role presigned URL expires when its signing credential expires, even if the requested URL expiration is later; EC2 metadata credentials rotate with a maximum validity of approximately six hours ([S3 presigned URL guide](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html)). Thus **REQUESTED URL TTL: 6 hours; EXPECTED EFFECTIVE TTL WITH EC2 ROLE: at most 6 hours and commonly less; SAFE AS-IS: NO for a guaranteed six-hour URL; REQUIRED CHANGE: verify a client/server refresh route or use a separately reviewed server-side signing design if six-hour validity is essential**. Rotation does not extend an already issued URL. A local signing test could verify the `X-Amz-Expires` field but cannot prove S3 accepts it after the signing session expires; AWS's documented lifetime rule is decisive. No mobile change is proposed.

## 5. Existing versus required permissions

| Permission | Existing finding | Required | Decision / evidence |
| --- | --- | --- | --- |
| S3 object get/put/delete on all buckets | Allowed | Three production prefixes in one bucket | Narrow to exact object ARNs; `s3_helper.py` |
| `s3:ListBucket`, `s3:ListAllMyBuckets` | Broad existing S3 access | No | Remove; no call site |
| `s3:CreateBucket`, `s3:DeleteBucket` | Broad existing S3 access | No | Remove; no call site |
| `s3:PutBucketPolicy`, `s3:DeleteBucketPolicy`, bucket ACL/encryption/lifecycle changes | Broad existing S3 access | No | Remove; deployment/admin only |
| Object ACL, tagging, retention, version actions | Broad existing S3 access | No | Remove; upload passes no ACL/tag/retention/version |
| `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` | Previously scoped to Sonnet family | Yes | Preserve only configured profile and associated exact model ARNs |
| `cloudwatch:PutMetricData` | Reportedly used every minute | Yes if flags enabled | Allow only configured namespaces; `runtime_metrics.py` / `ai_metrics.py` |
| Cognito IDP IAM actions | Not established | No | Calls are unsigned; app-client auth applies |
| IAM, RDS, SSM, Secrets Manager, ECR, CloudWatch Logs administration | Not established | No application need | Exclude; host/deployer identities are separate |

## 6. Proposed `AxisAIProdRuntimeRole`

**PROPOSED ROLE POLICY FINALIZED: YES (application policy), against live metadata of 2026-10-03.** Resources are the verified bucket (§0.3–0.4) and the verified inference-profile model set (`aws bedrock get-inference-profile`, status `ACTIVE`, type `SYSTEM_DEFINED`). The profile declares exactly two model ARNs: the region-less global ARN and the `eu-central-1` ARN. No wildcard bucket. No KMS. No `ListBucket`. No Secrets Manager/SSM/IAM/RDS. The last conditional input is now resolved from the live containers (§0.5). `AI_METRICS_ENABLED` is unset, so the source default `0` disables it, and `RUNTIME_METRICS_ENABLED=1`. The `ApplicationMetrics` statement therefore allows only `FitX/Runtime`, and `FitX/AI` is correctly excluded. `BEDROCK_MODEL` is unset, so the source default `global.anthropic.claude-sonnet-4-5-20250929-v1:0` applies, which is the profile named below. `RUNTIME_METRICS_NAMESPACE` is documented as `FitX/Runtime` because that is its source default and existing production publishing behavior matches it (§0.5). The cutover metric-arrival check (§9) is the final verification. No statement remains conditional.

S3 prefixes, re-verified on `origin/main` `d1252df`. Every `upload_image` caller passes an explicit prefix. The default `uploads/` is not a live call site.

| Prefix | Feature | GetObject | PutObject | DeleteObject | ListBucket |
| --- | --- | --- | --- | --- | --- |
| `pump-checks/` | web + native Pump Check, workout completion photo, gallery/Physique, comparison bytes, Coach photo tool | yes (bytes + presign) | yes | yes (`delete_managed_object`, account deletion) | no |
| `meals/` | meal-log photo | yes (presign) | yes | yes (`delete_meal_photo`, correction, account deletion) | no |
| `avatars/` | profile picture | yes (presign) | yes | yes (replacement, account deletion) | no |

**`ListBucket` is not required.** There is no listing call. Without it, a `GetObject` on a missing key returns 403 instead of 404. Both raise `S3Error` and are handled identically. `DeleteObject` does not need it, and missing-object deletes stay idempotent. The Coach `analyze_gym_photo` tool reads a model-supplied key, owner-checked by user segment. Under this policy, any legacy key outside the three prefixes becomes unreadable. That is acceptable: it is a stricter boundary, and the 2026-08-15 purge left one object. The exact legacy-prefix inventory would need object listing or a DB key read, both outside this read-only task.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "UserImageObjects",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:DeleteObject"],
      "Resource": [
        "arn:aws:s3:::fitx-user-bucket-2026/pump-checks/*",
        "arn:aws:s3:::fitx-user-bucket-2026/meals/*",
        "arn:aws:s3:::fitx-user-bucket-2026/avatars/*"
      ]
    },
    {
      "Sid": "UploadSSEEncryptedUserImages",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": [
        "arn:aws:s3:::fitx-user-bucket-2026/pump-checks/*",
        "arn:aws:s3:::fitx-user-bucket-2026/meals/*",
        "arn:aws:s3:::fitx-user-bucket-2026/avatars/*"
      ],
      "Condition": {"StringEquals": {"s3:x-amz-server-side-encryption": "AES256"}}
    },
    {
      "Sid": "RequireTLSForUserImages",
      "Effect": "Deny",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::fitx-user-bucket-2026",
        "arn:aws:s3:::fitx-user-bucket-2026/*"
      ],
      "Condition": {"Bool": {"aws:SecureTransport": "false"}}
    },
    {
      "Sid": "SonnetGlobalInferenceProfile",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "arn:aws:bedrock:eu-central-1:852128326881:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0",
      "Condition": {"StringEquals": {"aws:RequestedRegion": "eu-central-1"}}
    },
    {
      "Sid": "SonnetSourceRegionModelViaProfile",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "arn:aws:bedrock:eu-central-1::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
      "Condition": {"StringEquals": {
        "aws:RequestedRegion": "eu-central-1",
        "bedrock:InferenceProfileArn": "arn:aws:bedrock:eu-central-1:852128326881:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0"
      }}
    },
    {
      "Sid": "SonnetGlobalModelViaProfile",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "arn:aws:bedrock:::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
      "Condition": {"StringEquals": {
        "aws:RequestedRegion": "unspecified",
        "bedrock:InferenceProfileArn": "arn:aws:bedrock:eu-central-1:852128326881:inference-profile/global.anthropic.claude-sonnet-4-5-20250929-v1:0"
      }}
    },
    {
      "Sid": "ApplicationMetrics",
      "Effect": "Allow",
      "Action": "cloudwatch:PutMetricData",
      "Resource": "*",
      "Condition": {"StringEquals": {"cloudwatch:namespace": "FitX/Runtime"}}
    }
  ]
}
```

**The role also requires two AWS-managed policies, attached unchanged:** `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` and `arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy`. One profile per instance; see §0.2. They are host-agent permissions, not application permissions. *(Historical. Since R4 (2026-10-06), `CloudWatchAgentServerPolicy` is no longer attached. Host-agent CloudWatch authority now comes from the narrow inline `AxisAIProdHostObservabilityPolicy` (§A.5).)*

**Bedrock condition fallback.** The conditioned three-statement form follows AWS global-inference guidance, but it has not run in production. The unconditioned set already works in production on `fitx-s3-user` (`FitxBedrockInvokeSonnet45`, since 2026-09-04): both actions on exactly the profile ARN and the two model ARNs above. If `simulate-custom-policy` or the cutover deep-health probe denies the conditioned form, fall back to that proven set. Never fall back to `Resource: "*"` or `bedrock:*`.

**Both Bedrock actions are required.** `InvokeModel` serves `messages.create` (blocking AI paths). `InvokeModelWithResponseStream` serves `messages.stream`, which is used by the live Coach path `/ask/stream` and by `bedrock_health`, the deploy gate's deep-health probe. A role without the stream action passes no deploy.

AWS's [global inference IAM guidance](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html) requires profile, source-region model, and global model authorization; the latter has region `unspecified`. The profile's `models` metadata was verified (two model ARNs). The live `BEDROCK_MODEL` resolves to the source default, which is the same profile (§0.5). CloudWatch `PutMetricData` has no useful resource ARN for this call. Its [namespace condition](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/iam-cw-condition-keys-namespace.html) limits it to `FitX/Runtime`, the only enabled emitter. No `kms:*` or KMS action is included because the code requests SSE-S3 (`AES256`); add only `kms:GenerateDataKey`/`kms:Decrypt` on a verified exact key ARN, with `kms:ViaService`, if bucket/object metadata proves SSE-KMS is necessary.

EC2 trust policy for a **new** role and matching instance profile (create in the later cutover task):

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "EC2InstanceProfile",
    "Effect": "Allow",
    "Principal": {"Service": "ec2.amazonaws.com"},
    "Action": "sts:AssumeRole"
  }]
}
```

The role is exclusively for runtime. It has no deployer, IAM administration, SSM control, bucket administration, or unrelated data permissions. EC2 instance profiles expose credentials to processes on the host; review host agent needs and consider separate host/runtime identities in a later architecture change if the agent cannot retain its management capabilities under the replacement profile.

## 7. Static credential dependency and image exposure

| Location | Classification | Finding |
| --- | --- | --- |
| Production host `.env` (historical deployment record) | Runtime injection, **not required by code** | Compose `env_file` passes it to both web and worker. Live keys/values were not inspected. |
| `s3_helper.py`, `ai_metrics.py`, `runtime_metrics.py`, `app/extensions.py` | Runtime | Default SDK chain; no explicit key or named profile. |
| `.env.example` | Documentation | Mentions key variable names only; says role credentials are intended. |
| `.env.staging.example` | Staging template | No static AWS keys; staging S3 disabled. |
| `.github/workflows/deploy.yml`, `scripts/deploy_control.py` | Deployment only | GitHub OIDC deployer and SSM; no runtime key value in repository. |
| Tests | Local only | Some tests set dummy `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for hermetic presigning; they are not production dependencies. |

`.env` is Git-ignored and Docker-ignored. Dockerfile has no AWS key build argument; its only build argument is revision SHA. Source-controlled build configuration therefore does not bake the host `.env` into the image. This is a source-based conclusion, **not** a forensic inspection of deployed image layers, CI artifacts, host logs, or crash output. The host `.env` is passed into container environment, where it is visible to those processes and Docker host administrators. During cutover inspect only variable **names/presence**, never values or `docker inspect` raw environment output. Also check `AWS_PROFILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, and `AWS_EC2_METADATA_DISABLED` without displaying contents.

## 8. Ordered migration plan (historical; executed 2026-10-04, see §B)

Steps 1–8 were executed in the authorized cutover (§B.1). For step 9, the key was disabled on 2026-10-04 and deleted on 2026-10-05, after the §D gate passed (§D.2).

1. Wait for RDS incident closure. Freeze unrelated deploys; capture current health, web/worker identity ARN (never credentials), current profile association ID, metric namespaces, Bedrock model ID, and required S3 bucket name from an authorized operator. Record rollback owners and a short maintenance window.
2. Read production bucket policy, Public Access Block, object ownership, default encryption, versioning, lifecycle, and KMS key policy **metadata only**. Check explicit `fitx-s3-user` principals, endpoint restrictions, encryption/ACL conditions, and any legacy object prefixes in application metadata. Resolve policy placeholders; add only required exact resources. Determine whether a minimal bucket policy or KMS key policy addition is necessary and prepare it separately.
3. Review current profile role and host CloudWatch/SSM agent permissions. Prepare `AxisAIProdRuntimeRole` and a new instance profile with only proven application actions plus separately justified host-agent permissions if unavoidable. Validate trust and policy syntax/size; do not copy deployer privileges.
4. Create role/profile in the separately authorized cutover. Prevalidate with policy simulation and a staging-equivalent workload if available. A local `sts:AssumeRole` test does **not** prove EC2 profile or Docker IMDS behavior.
5. Replace the running EC2 profile association with the new profile using its association ID. No EC2 reboot is inherent to the API, but schedule this as a production identity mutation. Confirm IMDSv2 reachability from **both** containers; hop limit 2 is already configured.
6. While static keys still take precedence, verify the profile's identity using a controlled process/container with `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, and any named-profile overrides **unset for that process only**. Confirm the returned ARN is the new role; do not print credential material. Run positive and negative validation below under that role.
7. Remove static AWS key variables from the host runtime configuration using the approved secrets procedure. Recreate/reload web and worker only as needed for Compose to pick up changed environment; a running process cannot shed an inherited environment variable. Do not restart EC2 or RDS. Confirm both processes resolve the role and refresh it over time.
8. Verify S3 photo flows with controlled test objects, Bedrock and metrics, deep `/health`, user flows, and presigned URL refresh/expiry behavior. Keep the old key enabled but secured for a brief rollback window.
9. Disable the old key only after observation. Confirm no `fitx-s3-user` usage in available usage metadata and that normal flows continue. Observe through an agreed confidence period, then delete the old key in a separately approved cleanup. Do not leave an active dormant key indefinitely.

## 9. Validation plan

Local source and hermetic tests: `tests/test_s3_helper.py`, `tests/test_ai_metrics.py`, `tests/test_runtime_metrics.py`, `tests/test_avatar_object_lifecycle.py`, `tests/test_pump_check_object_lifecycle.py`, and existing provider tests cover the actual object operations and default client construction. No live production integration tests were run in this task. A pre-cutover test must assert the role credential provider is selected from inside **both** web and worker without printing keys and remains usable after refresh. Do not infer this from host STS alone.

Positive production checks during the separately authorized cutover: create a controlled non-user object under each required prefix through the application role, read it, and delete it; exercise a synthetic photo upload/read/delete path, one small authorized Sonnet inference including streaming, metrics arrival in the expected namespace(s), `/health` and deep health, and normal authenticated app flows. Do not use a real user photo. Presign a controlled GET URL near credential renewal and verify its real validity and client refresh path, especially the 21,600-second avatar request.

Negative checks with the new role: `DeleteBucket`, `PutBucketPolicy`, `DeleteBucketPolicy`, `ListAllMyBuckets`, `ListBucket` for the required bucket, `GetObject` and `PutObject` in an unrelated bucket, object access outside the three prefixes, IAM mutation, unrelated Secrets Manager read, RDS administration, and unapproved Bedrock model invocation must be denied. Use policy simulation where a destructive request could have an effect if accidentally allowed; never issue a destructive live API call merely to test denial. For unrelated object access use a controlled object owned by the test operator. Re-test after policy changes. Confirm explicit bucket policy denies do not break the intended role.

| Expected-deny probe | Why the proposed template denies it | Later validation |
| --- | --- | --- |
| `s3:DeleteBucket`, `s3:PutBucketPolicy`, `s3:DeleteBucketPolicy` | No allow for bucket administration; the template's bucket TLS deny does not grant an action. | Simulate on the exact production bucket ARN; do not call a destructive live API. |
| `s3:ListBucket`, `s3:ListAllMyBuckets` | No bucket-listing allow. | Simulate; runtime code has no listing call. |
| `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on unrelated buckets or prefixes | Object allows name only the three configured production prefixes. | Simulate unrelated ARNs; use only controlled objects for any later real probe. |
| IAM modification, Secrets Manager reads, RDS administration | No service actions granted. | Simulate representative actions and resources. |
| `bedrock:InvokeModel` and streaming invocation on unapproved models | Allows only the named inference profile and matching model resources with profile conditions. | Simulate exact approved and unapproved ARNs after profile metadata is verified. |
| S3 on `axisai-user-storage`, `fitx-user-storage`, `aws-sam-cli-managed-default-samclisourcebucket-tu9ljea0ritx` | Object allows name only `fitx-user-bucket-2026/{pump-checks,meals,avatars}/*`. The old role's `axisai-user-storage` grant is not carried over. | `simulate-custom-policy` per bucket ARN. |
| `s3:PutObject` without `AES256` header | `PutObject` is conditional on `s3:x-amz-server-side-encryption = AES256`. | Simulate with and without the context key. |
| `s3:PutObjectAcl`, `s3:GetObjectAcl` | Not granted. The bucket is `BucketOwnerEnforced`, so ACLs are disabled anyway. | Simulate. |
| `secretsmanager:GetSecretValue` on `axisai/prod*` | Not granted. The old role's grant is dropped; Access Advisor shows it was never used. | Simulate. |
| `cloudwatch:PutMetricData` to any namespace other than `FitX/Runtime` | Namespace condition. | Simulate `FitX/Runtime` (allow) and `FitX/UnauthorizedProbe` (deny). Do not publish. |
| `bedrock:ListFoundationModels`, `bedrock:*` admin | Not granted. | Simulate. |

**Host-agent caveat.** The two required AWS-managed agent policies carry `logs:*Create*/Put*`, `ssm:GetParameter(s)` (including `AmazonCloudWatch-*`), `ec2:DescribeTags/Volumes`, `xray:Put*` and SSM channel actions. The final-role simulation will therefore show those as **allowed**. That is expected and is not a runtime grant. Expected-deny tests must target the services and actions above, not "everything except S3/Bedrock/CloudWatch".

These are **policy-template expectations**, not proof of effective production authorization. Effective denial also depends on the final role's other attached policies, permissions boundary, resource policies, and organization controls; inspect the complete role and simulate the final identity before cutover. Never add a broad policy merely to make a positive test pass.

## 10. Rollback plan

> **Historical (cutover-window plan).** This plan applied only during the 2026-10-04 cutover, and rollback was not required. It is no longer executable: the old key, the `fitx-s3-user` user and the `AxisAI-EC2-Role-` role/profile it relies on were all deleted in 2026-10-05 – 2026-10-06 (§A.4, §D.2). Current application rollback is revision-based and runs under `AxisAIProdRuntimeRole` (§A.4).

At the first failed S3 write/read/presign, Bedrock inference, or sustained metric delivery, stop the cutover and diagnose the exact denied action, resource, bucket/KMS policy, namespace, or credential provider. Metrics loss alone is nonblocking for user requests but still fails the acceptance gate. Re-enable the previous known-working **configuration** only if service recovery requires it, with access to the old key limited to the designated operator; do not print or recreate a key in logs. Recreate only the affected web/worker containers after restoring their prior environment, then verify identity and all positive flows. If profile replacement itself caused host-agent failure or role resolution failure, restore the previously recorded profile association and validate host management. If a process keeps stale or environment credentials, restart/recreate that process at a controlled time, then verify the effective ARN and refresh; do not widen role permissions to mask precedence problems. If the old key was already disabled, temporarily re-enable the same known key only as a last-resort recovery step, with time-boxed monitoring and immediate follow-up. Never grant AdministratorAccess or broad S3 to the new role. Do not modify RDS during rollback.

## 11. Mobile / Xcode compatibility

```text
MOBILE CHANGES: 0
XCODE CHANGES: 0
MOBILE API CONTRACT CHANGES: 0
```

The backend still returns the same URL fields. The early-expiry condition (§0.6) does not touch mobile, because the native API exposes no avatar field and `axisai_mobile` has no avatar consumer. **No mobile or Xcode change is required for the current release.** During the cutover window, re-check the native gallery's handling of an expired 1-hour Pump Check URL (§12).

## 12. Remaining risks and evidence gates

**Six-hour avatar URL (verified 2026-10-03).** `User.avatar_src` signs with `expires_in=21600`. Every web consumer calls it per request, from server-rendered pages and JSON payloads (social, feed, leaderboard, challenges, notifications). No avatar URL is cached in Redis or the DB, so reloading always yields a fresh URL. The native `/api/v1` surface exposes no avatar field. `axisai_mobile` `origin/main` `7a7ccfa` has no avatar consumer, so **mobile does not consume avatar URLs**.

Under EC2 role credentials, botocore refreshes when 15 minutes remain (10 minutes mandatory). A URL's effective lifetime is therefore between about 10 minutes and about 6 hours. The same bound shortens the 1-hour Pump Check, meal and Physique URLs, including those the native gallery consumes.

- **6H URL REQUIREMENT:** not a product contract; nothing depends on it surviving 6 h.
- **CURRENT CLIENT DEPENDENCY:** web only, regenerated on every load; mobile none for avatars.
- **SAFE FOR ROLE CUTOVER:** CONDITIONAL. Accept that a web page left open long enough can show the initials fallback or a broken image until reload. Re-check the native gallery's expired-URL behavior during the cutover window.

**New findings outside SEC-001 scope (record only; no change made):**

- `fitx-user-storage` is empty but has a **public-read bucket policy** (`Principal: "*"`, `s3:GetObject` on `/*`; `PolicyStatus.IsPublic = True`). Its Public Access Block is fully disabled, and CORS allows `GET/PUT/POST/DELETE` from `https://fitx-chatbot.duckdns.org`. Pointing `S3_BUCKET_NAME` at it would publish every upload. This is the audit's SEC-021, now re-confirmed.
- `axisai-user-storage` holds a **frozen 62-object copy** of production user images, taken on 2026-07-10. In-app account deletion only releases objects in the configured bucket, so photos of users deleted since July may persist in this copy. Treat it as a retention/erasure finding, not as a runtime dependency.
- `fitx-user-bucket-2026` has versioning disabled and no lifecycle policy. Deletes are permanent, and there is no object-level backup.

- A compromised runtime can still access every object under the three legitimate prefixes and may issue presigned URLs. IAM cannot distinguish application users sharing one EC2 role. Application ownership checks remain essential.
- *(Resolved.)* The first pass could not read the production bucket, bucket policy, encryption, profile role, live model or metric flags with `axisai-staging-operator`. All of them were read in §0. Only the legacy stored-key-prefix inventory remains unread. It is not a gate: keys outside the three prefixes become unreadable by design (§6).
- If bucket policy names the IAM user ARN, add the new role principal minimally during the later cutover, validate, then remove the user after observation. If the bucket uses SSE-KMS, add exact KMS key permissions and key-policy access only if required. If an encryption or TLS condition conflicts with current requests, resolve it before cutover.
- Six-hour avatar URLs **will not have a guaranteed six-hour lifetime** with EC2 role credentials. This is accepted as the explicit cutover condition (§0.6). Web clients get fresh URLs on every load, and mobile consumes none. No mobile/API change is needed.
- Replacing the sole EC2 instance profile can affect host CloudWatch and SSM agents. Inventory those host permissions separately, keeping deployer, developer/admin, and application runtime privilege boundaries distinct.
- The new role/profile and validation requests have small IAM/CloudWatch/S3 request and Bedrock inference cost implications; no new storage service or migration of user objects is proposed.

## 13. Recommended separately authorized cutover task (historical; done 2026-10-04)

Authorize a production operator **after the RDS incident** to resolve the metadata gates; produce a final concrete bucket/model/KMS/bucket-policy diff; review existing host-profile dependencies; create the exact role/profile; perform controlled positive and negative tests; replace the live profile association; remove static AWS variables from both containers; observe credential refresh and URL behavior; disable and later delete the old key. Keep the deployer role and SEC-002 work separate.

**Outcome:** every step except "later delete the old key" was performed on 2026-10-04 (§B). The key was deleted on 2026-10-05 after the §D gate passed. The legacy user, roles, profiles and orphan customer policies followed (§D.2).

## Appendix: container identity probe (§0.5 gate)

This probe is read-only. A production operator runs it via SSM `AWS-RunShellScript` on `i-0c6f5352fc214e68d`.

**What it does:**

- For each of `web` and `worker`, it runs one Python process inside the container.
- That process prints the presence (`present`/`absent`) of `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE` and `AWS_EC2_METADATA_DISABLED`.
- It prints the values of `S3_BUCKET_NAME`, `BEDROCK_MODEL`, `BEDROCK_REGION`, `AI_METRICS_ENABLED` and `RUNTIME_METRICS_ENABLED`, plus the `botocore` credential `method` and the `sts:GetCallerIdentity` ARN.
- It repeats the identity call in a second process with the three key variables unset **for that process only**. That shows whether IMDS role credentials are reachable from the container.

It never prints a credential value or the raw `docker inspect` environment, and it changes nothing.

**Result (2026-10-03, both containers):** exactly the expected outcome. The current process showed method `env`, ARN `user/fitx-s3-user`, and the key variables `present`. The role-only process showed method `iam-role` and ARN `assumed-role/AxisAI-EC2-Role-/i-0c6f5352fc214e68d`. See §0.5.

## Pre-cutover readiness verdict (2026-10-03, historical — superseded by §A)

The "CURRENT" fields below are the 2026-10-03 production state, before the cutover.

```text
CURRENT PROD WEB IDENTITY:       arn:aws:iam::852128326881:user/fitx-s3-user
CURRENT PROD WORKER IDENTITY:    arn:aws:iam::852128326881:user/fitx-s3-user
CURRENT CREDENTIAL SOURCE:       static env (host .env), botocore method "env"
WEB ROLE FALLBACK VERIFIED:      YES (iam-role -> assumed-role/AxisAI-EC2-Role-/i-0c6f5352fc214e68d)
WORKER ROLE FALLBACK VERIFIED:   YES (same)
LIVE S3_BUCKET_NAME:             fitx-user-bucket-2026
LIVE AI_METRICS_ENABLED:         unset -> source default 0 (disabled)
LIVE RUNTIME_METRICS_ENABLED:    1
LIVE RUNTIME_METRICS_NAMESPACE:  FitX/Runtime (source default + observed publishing; re-verified by cutover metric-arrival check)
LIVE BEDROCK_MODEL:              unset -> source default global.anthropic.claude-sonnet-4-5-20250929-v1:0
INSTANCE PROFILE:                arn:aws:iam::852128326881:instance-profile/AxisAI-EC2-Role-
INSTANCE ROLE:                   arn:aws:iam::852128326881:role/AxisAI-EC2-Role-
PROPOSED RUNTIME ROLE:           AxisAIProdRuntimeRole (new role + new instance profile; §0.2, §6)
PROPOSED ROLE POLICY FINALIZED:  YES
SEC-001 CUTOVER STATUS:          CUTOVER READY WITH ONE EXPLICIT CONDITION   (historical; cutover COMPLETE 2026-10-04, SEC-001 CLOSED 2026-10-06, §A)
EXPLICIT CUTOVER CONDITION:      presigned-URL lifetime under EC2-role credentials (§0.6)
MOBILE/XCODE CHANGE REQUIRED:    NO (current release)
READY FOR SEPARATE CUTOVER TASK: YES, only after the RDS incident is closed and the cutover is separately authorized
```

The preparation PR did not perform the cutover itself. The separately authorized cutover task performed it on 2026-10-04 (§B).

## Post-cutover verdict (2026-10-04, historical — superseded by the final verdict below)

The cutover verdict recorded on 2026-10-04 was: cutover **COMPLETE**, production on `AxisAIProdRuntimeRole`, static AWS credentials removed, rollback not required. At that point the old key was only deactivated and `AxisAI-EC2-Role-` was still kept. Both have since been deleted (§D.2).

## Final verdict (2026-10-06, authoritative)

```text
SEC-001 STATUS:                  CLOSED

PRODUCTION IDENTITY:             AxisAIProdRuntimeRole / AxisAIProdRuntimeProfile
PRODUCTION ROLE:                 arn:aws:iam::852128326881:role/AxisAIProdRuntimeRole
PRODUCTION INSTANCE PROFILE:     arn:aws:iam::852128326881:instance-profile/AxisAIProdRuntimeProfile
WEB IDENTITY:                    assumed-role/AxisAIProdRuntimeRole/i-0c6f5352fc214e68d (iam-role)
WORKER IDENTITY:                 assumed-role/AxisAIProdRuntimeRole/i-0c6f5352fc214e68d (iam-role)

STATIC AWS CREDENTIALS:          ABSENT
LEGACY ACCESS KEY:               DELETED (fitx-s3-user AKIA…4XUI, 2026-10-05)
LEGACY IAM USER:                 DELETED (fitx-s3-user, 2026-10-05)
LEGACY ROLES/PROFILES:           DELETED (AxisAI-EC2-Role, AxisAI-EC2-Role-, fitx-ec2-s3-access; 2026-10-05 – 2026-10-06)
LEGACY ORPHAN CUSTOMER POLICIES: DELETED (AxisAI-EC2-RolePolicy, AxisAI-EC2-Role-Policy; 2026-10-06)
CREDENTIAL / IAM ERRORS AFTER RETIREMENT: 0

PRESIGNED URL CONDITION:         ACCEPTED (R5; independent follow-up, not an SEC-001 blocker)
CLOUDWATCH PERMISSION OVERLAP:   CLOSED 2026-10-07 (R4; independent follow-up, not an SEC-001 blocker; §A.5)
SINGLE-CONTAINER AVAILABILITY:   FOLLOW-UP (R6; independent follow-up, not an SEC-001 blocker)

NEXT SEC-001 ACTION:             NONE
```

## Safety counters for the preparation PR (historical)

```text
PRODUCTION AWS MUTATIONS: 0
PRODUCTION CREDENTIAL CHANGES: 0
PRODUCTION DEPLOYS: 0
CONTAINER RESTARTS: 0
HOST ENV CHANGES: 0
RDS CHANGES: 0
MOBILE CHANGES: 0
XCODE CHANGES: 0
PR #378 CHANGES: 0
PRODUCTION USER-DATA READS: 0
SECRET VALUES PRINTED: 0
```
