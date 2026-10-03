# SEC-001: production runtime identity and S3 remediation preparation

Status: **NOT CUTOVER READY; draft policy only** (2026-10-03). This document contains no credentials. The production RDS incident and SEC-005 PR #378 remain separate.

### Evidence-gate review (2026-10-03)

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
| Can runtime use a role without mobile or API changes? | **Yes in code.** Live container metadata reachability and credential precedence require cutover validation. |
| Required S3 bucket/resources | One configured `S3_BUCKET_NAME`, under `avatars/`, `meals/`, and `pump-checks/`. **The production bucket name remains unverified** because this task's staging operator is explicitly denied account-wide S3 listing and production IAM reads. The proposed policy is a template that must be resolved to the exact production bucket before creation. |
| Required S3 actions | `GetObject`, `PutObject`, `DeleteObject` on those object prefixes. No bucket-level action found in runtime code. |
| Bucket policy / KMS change? | **Unknown until production-authorized metadata review.** Do not cut over until bucket policy, default encryption, KMS key policy, and object ownership are checked. |
| Presigned URL effect? | **Yes.** Most GET URLs request 1 hour; avatar URLs request 6 hours. EC2 role credentials have a maximum validity around 6 hours and rotate, so a URL signed partway through a credential session expires before its requested 6 hours. The existing 6-hour lifetime is not guaranteed. Validate client refresh behavior before cutover. |
| Code change required? | No change to the SDK construction. A backend URL refresh/lifetime change may be needed if the six-hour avatar contract proves necessary; this report does not change behavior. |
| Is production cutover safe after the RDS incident? | **Not yet.** Resolve the evidence gates below and validate both web and worker against the role in a separately authorized task. |

**Current credential source:** host `.env` static key for `fitx-s3-user`, according to the repository's [deployment record](docs/DEPLOYMENT.md). This is high-confidence historical evidence, not an inspection of today's host. The live EC2 instance has an instance profile attached, but that does not prove containers use it: environment credentials take precedence.

**Application dependency on static keys:** NO. **Role-compatible without code change:** YES, conditional on container IMDS access and URL lifetime validation. **Confidence:** high for source behavior, medium for current live credential source, low for inaccessible bucket/policy facts.

## 2. Current credential architecture

```text
production EC2 i-0c6f5352fc214e68d (AxisAI-server)
  -> Docker Compose web + worker; both consume host .env via env_file
  -> SDK default credential chain; environment credentials precede EC2 IMDS
  -> historically host .env key for fitx-s3-user
  -> previously verified broad S3, scoped Bedrock, CloudWatch metric capability

same EC2 currently has instance profile AxisAI-EC2-Role- attached
  -> its role and permissions could not be read with axisai-staging-operator
  -> current effective container identity must be confirmed during cutover
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
| `${PROD_S3_BUCKET}/pump-checks/<user_id>/<YYYY>/<MM>/<uuid>.<ext>` | Body/progress photos; sensitive | bytes for AI/comparison, presigned GET for gallery | Pump Check upload | account deletion / check lifecycle | No | No |
| `${PROD_S3_BUCKET}/meals/<user_id>/<YYYY>/<MM>/<uuid>.<ext>` | Meal images; private user content | presigned GET | meal upload | meal correction / account deletion | No | No |
| `${PROD_S3_BUCKET}/avatars/<user_id>/<YYYY>/<MM>/<uuid>.<ext>` | Profile images; private user content | presigned GET | avatar upload | avatar replacement / account deletion | No | No |

`S3_BUCKET_NAME` is server configuration, not a request parameter. `_build_key` derives the user segment and UUID on the server. Delete functions validate a tight key grammar and owner ID. Download and presign helpers validate the user segment when passed `expected_user_id`; some callers first prove ownership through database queries. IAM prefix isolation can prevent access to unrelated prefixes and buckets, but one shared runtime role cannot enforce per-user isolation for all user IDs. Application authorization remains the per-user boundary. Audit every caller for `expected_user_id` or prior owner query before cutover; this work does not alter authorization.

No runtime `ListBucket`, `ListAllMyBuckets`, `HeadBucket`, tagging, ACL, lifecycle, bucket policy, bucket encryption, bucket create/delete, or object attribute API call was found. `PutObject` sets `ServerSideEncryption="AES256"` and does not set ACL. The repository describes the bucket as private / Block Public Access, but **actual bucket settings, public state, lifecycle, versioning, object ownership, default encryption, bucket policies, and SSE-KMS are unverified** because the staging identity cannot inspect production S3. Do not read user objects to answer these questions.

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

**Production policy template, not deployable until every `${...}` token is replaced and the metadata gates are passed. PROPOSED ROLE POLICY FINALIZED: NO.** The account ID and instance region are confirmed; the bucket, bucket/KMS policy, live model/profile configuration, and contained EC2 role are not. Do not substitute a wildcard bucket. If legacy DB keys exist outside the three prefixes, enumerate keys from application metadata only and add the minimal proven prefix before cutover. The EC2 trust policy below is structurally final for a newly created EC2 role; its use remains conditional on the host-agent/profile review.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "UserImageObjects",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:DeleteObject"],
      "Resource": [
        "arn:aws:s3:::${PROD_S3_BUCKET}/pump-checks/*",
        "arn:aws:s3:::${PROD_S3_BUCKET}/meals/*",
        "arn:aws:s3:::${PROD_S3_BUCKET}/avatars/*"
      ]
    },
    {
      "Sid": "UploadSSEEncryptedUserImages",
      "Effect": "Allow",
      "Action": "s3:PutObject",
      "Resource": [
        "arn:aws:s3:::${PROD_S3_BUCKET}/pump-checks/*",
        "arn:aws:s3:::${PROD_S3_BUCKET}/meals/*",
        "arn:aws:s3:::${PROD_S3_BUCKET}/avatars/*"
      ],
      "Condition": {"StringEquals": {"s3:x-amz-server-side-encryption": "AES256"}}
    },
    {
      "Sid": "RequireTLSForUserImages",
      "Effect": "Deny",
      "Action": "s3:*",
      "Resource": [
        "arn:aws:s3:::${PROD_S3_BUCKET}",
        "arn:aws:s3:::${PROD_S3_BUCKET}/*"
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
      "Condition": {"StringEquals": {"cloudwatch:namespace": ["FitX/AI", "FitX/Runtime"]}}
    }
  ]
}
```

AWS's [global inference IAM guidance](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html) requires profile, source-region model, and global model authorization; the latter has region `unspecified`. Confirm the actual profile's `models` metadata and live `BEDROCK_MODEL` before finalizing. If the profile's model IDs differ, use those exact IDs only. CloudWatch `PutMetricData` has no useful resource ARN for this call; its [namespace condition](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/iam-cw-condition-keys-namespace.html) limits both namespaces. Confirm live namespace overrides before finalizing. No `kms:*` or KMS action is included because the code requests SSE-S3 (`AES256`); add only `kms:GenerateDataKey`/`kms:Decrypt` on a verified exact key ARN, with `kms:ViaService`, if bucket/object metadata proves SSE-KMS is necessary.

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

These are **policy-template expectations**, not proof of effective production authorization. Effective denial also depends on the final role's other attached policies, permissions boundary, resource policies, and organization controls; inspect the complete role and simulate the final identity before cutover. Never add a broad policy merely to make a positive test pass.

## 10. Rollback plan

At the first failed S3 write/read/presign, Bedrock inference, or sustained metric delivery, stop the cutover and diagnose the exact denied action, resource, bucket/KMS policy, namespace, or credential provider. Metrics loss alone is nonblocking for user requests but still fails the acceptance gate. Re-enable the previous known-working **configuration** only if service recovery requires it, with access to the old key limited to the designated operator; do not print or recreate a key in logs. Recreate only the affected web/worker containers after restoring their prior environment, then verify identity and all positive flows. If profile replacement itself caused host-agent failure or role resolution failure, restore the previously recorded profile association and validate host management. If a process keeps stale or environment credentials, restart/recreate that process at a controlled time, then verify the effective ARN and refresh; do not widen role permissions to mask precedence problems. If the old key was already disabled, temporarily re-enable the same known key only as a last-resort recovery step, with time-boxed monitoring and immediate follow-up. Never grant AdministratorAccess or broad S3 to the new role. Do not modify RDS during rollback.

## 11. Mobile / Xcode compatibility

```text
MOBILE CHANGES: 0
XCODE CHANGES: 0
MOBILE API CONTRACT CHANGES: 0
```

The backend still returns the same URL fields. Early expiry of an avatar presigned URL is an unresolved behavior risk and must be tested before cutover.

## 12. Remaining risks and evidence gates

- A compromised runtime can still access every object under the three legitimate prefixes and may issue presigned URLs. IAM cannot distinguish application users sharing one EC2 role. Application ownership checks remain essential.
- The exact production bucket, bucket policy, public state, encryption/KMS policy, existing profile role, live Bedrock model and metric overrides, and legacy stored key prefixes could not be read with `axisai-staging-operator`. `ListBuckets`, `iam:GetInstanceProfile`, `iam:GetUser`, and `bedrock:GetInferenceProfile` were explicitly denied. The AWS MCP server's read-only policy must not be worked around either. These gates block a final deployable policy and cutover approval.
- If bucket policy names the IAM user ARN, add the new role principal minimally during the later cutover, validate, then remove the user after observation. If the bucket uses SSE-KMS, add exact KMS key permissions and key-policy access only if required. If an encryption or TLS condition conflicts with current requests, resolve it before cutover.
- Six-hour avatar URLs **will not have a guaranteed six-hour lifetime** with EC2 role credentials. Existing clients must obtain fresh URLs; if they cannot, a backend-only URL refresh strategy or separately reviewed signer is required before cutover. No mobile/API change is assumed or made.
- Replacing the sole EC2 instance profile can affect host CloudWatch and SSM agents. Inventory those host permissions separately, keeping deployer, developer/admin, and application runtime privilege boundaries distinct.
- The new role/profile and validation requests have small IAM/CloudWatch/S3 request and Bedrock inference cost implications; no new storage service or migration of user objects is proposed.

## 13. Recommended separately authorized cutover task

Authorize a production operator **after the RDS incident** to resolve the metadata gates; produce a final concrete bucket/model/KMS/bucket-policy diff; review existing host-profile dependencies; create the exact role/profile; perform controlled positive and negative tests; replace the live profile association; remove static AWS variables from both containers; observe credential refresh and URL behavior; disable and later delete the old key. Keep the deployer role and SEC-002 work separate. This report does **not** authorize or execute those steps.

## Safety counters for this preparation

```text
PRODUCTION AWS MUTATIONS: 0
PRODUCTION CREDENTIAL CHANGES: 0
PRODUCTION DEPLOYS: 0
RDS CHANGES: 0
MOBILE CHANGES: 0
XCODE CHANGES: 0
PR #378 CHANGES: 0
PRODUCTION USER-DATA READS: 0
SECRET VALUES PRINTED: 0
```
