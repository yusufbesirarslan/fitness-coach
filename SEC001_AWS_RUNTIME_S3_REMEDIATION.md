# SEC-001: production runtime identity and S3 remediation preparation

Status: **CUTOVER READY WITH ONE EXPLICIT CONDITION** (2026-10-03, third evidence pass). The role policy is finalized against live production metadata (§6). The last evidence gate, the in-container identity and role-fallback probe, is closed (§0.5). The one remaining item is the presigned-URL lifetime condition (§0.6). It is an accepted operational/product condition, not an identity blocker. This document contains no credentials. The production RDS incident and SEC-005 PR #378 remain separate. This document does not authorize or perform the cutover.

## 0. Production-operator evidence (2026-10-03)

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
- **Why a new role instead of editing the old one:** cutover becomes `replace-iam-instance-profile-association`, and rollback is the same call back to `AxisAI-EC2-Role-`, which stays untouched.

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
| Is production cutover safe after the RDS incident? | **Cutover ready with one explicit condition (§0.6).** All evidence gates are closed. Execute only in a separately authorized cutover task after the RDS incident, with the §8–§10 validation and rollback. |

**Current credential source:** CONFIRMED live (§0.5). Both containers use the static environment key for `fitx-s3-user`, loaded from host `.env` (`botocore` method `env`), and it overrides the attached instance profile.

**Application dependency on static keys:** NO. **Role-compatible without code change:** YES. Container IMDS access is proven live, and URL lifetime is the accepted condition in §0.6. **Confidence:** CONFIRMED for both the live credential source and the fallback. Bucket and policy facts are from live metadata (§0.3–0.4).

## 2. Current credential architecture

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

**The role also requires two AWS-managed policies, attached unchanged:** `arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore` and `arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy`. One profile per instance; see §0.2. They are host-agent permissions, not application permissions.

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

## 8. Ordered migration plan — later authorization only

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

## 13. Recommended separately authorized cutover task

Authorize a production operator **after the RDS incident** to resolve the metadata gates; produce a final concrete bucket/model/KMS/bucket-policy diff; review existing host-profile dependencies; create the exact role/profile; perform controlled positive and negative tests; replace the live profile association; remove static AWS variables from both containers; observe credential refresh and URL behavior; disable and later delete the old key. Keep the deployer role and SEC-002 work separate. This report does **not** authorize or execute those steps.

## Appendix: container identity probe (§0.5 gate)

This probe is read-only. A production operator runs it via SSM `AWS-RunShellScript` on `i-0c6f5352fc214e68d`.

**What it does:**

- For each of `web` and `worker`, it runs one Python process inside the container.
- That process prints the presence (`present`/`absent`) of `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE` and `AWS_EC2_METADATA_DISABLED`.
- It prints the values of `S3_BUCKET_NAME`, `BEDROCK_MODEL`, `BEDROCK_REGION`, `AI_METRICS_ENABLED` and `RUNTIME_METRICS_ENABLED`, plus the `botocore` credential `method` and the `sts:GetCallerIdentity` ARN.
- It repeats the identity call in a second process with the three key variables unset **for that process only**. That shows whether IMDS role credentials are reachable from the container.

It never prints a credential value or the raw `docker inspect` environment, and it changes nothing.

**Result (2026-10-03, both containers):** exactly the expected outcome. The current process showed method `env`, ARN `user/fitx-s3-user`, and the key variables `present`. The role-only process showed method `iam-role` and ARN `assumed-role/AxisAI-EC2-Role-/i-0c6f5352fc214e68d`. See §0.5.

## Final readiness verdict (2026-10-03)

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
SEC-001 CUTOVER STATUS:          CUTOVER READY WITH ONE EXPLICIT CONDITION
EXPLICIT CUTOVER CONDITION:      presigned-URL lifetime under EC2-role credentials (§0.6)
MOBILE/XCODE CHANGE REQUIRED:    NO (current release)
READY FOR SEPARATE CUTOVER TASK: YES, only after the RDS incident is closed and the cutover is separately authorized
```

The cutover itself is **not** performed by this PR: creating the role/profile, replacing the association, removing the env keys, recreating the containers and disabling the old key.

## Safety counters for this preparation

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
