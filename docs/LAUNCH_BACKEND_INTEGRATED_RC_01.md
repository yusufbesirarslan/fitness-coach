# Backend integrated release candidate and E2E handoff

TASK=AXISAI-LAUNCH-BACKEND-INTEGRATED-RC-01

This is a draft source candidate, not activation or deployment approval. Base:
`8b7f8d0210399bb576a4a8bb144cb1e87cbe831c` (`origin/main`, verified at session
start). Main already contains #497/#493; neither is cherry-picked or recreated.
The integration merges F01 #490 at `64b8b0272e464a2e589cf7a1dd5a87761e128ab3`
and social #492 at `8bc8c8a25371e8d3d3a221a63dfad392b29e393c`.

## Ownership and immutable receipts

The release-integration owner owns conflict resolution, exact-head local/hosted
qualification, review closure and the final PR receipt. The staging operator
owns the deployed-image/configuration receipt; the mobile owner owns the paired
build and real-iPhone evidence; founder/controller/legal own the pending policy
decisions below. Do not convert a source test into a legal or live-runtime pass.

Use the final PR head SHA for every check. Record its tree, parents, CI run and
attempt URLs, shard manifests/count parity, independent review SHA, and any
failures/skips/xfails in the release receipt. Any source change requires new
exact-head CI and review. PR #490/#492 results are supporting history only.

## Preserved release boundaries

| Boundary | Candidate behavior |
|---|---|
| F01 | `health_disclosure.APPROVED_VERSION=None`; no first-party grants can be created against unpublished text; no historical account is grandfathered |
| D1 | `ai_consent.POLICIES`, disclosure `PUBLISHED_TEXTS` and `LAUNCH_VERSIONS` remain empty; all five AI features remain dark |
| Service cohort | `ai_launch.APPROVED_SERVICE_COHORT_VERSION=None`; current admitted AI cohort, if separately qualified later, is signed adult TR; this is not 13+ readiness |
| Social | `launch_social.SOCIAL_AVAILABLE=False`, independent of eligibility; friends/feed/messages/sharing/friends rankings closed before health/body handling |
| Global rankings | Routes and ranking semantics retained, subject to the independent F01 boundary; health-authorized fixtures still receive global leaderboards/challenge rankings |
| FatSecret D-B2 | `PROVIDER_PERSISTENCE_PERMISSION_REF=None`; new provider-backed diary/saved-meal/barcode persistence refused before provider calls; no config bypass |
| FatSecret R-5/advice | Protein nudge withheld on weeks with MealLog rows; Coach/QR safeguards, independent estimates and null-not-zero unproven-intake budgets retained |
| History/manual | Historical rows/replays unchanged, no relabel/rewrite/purge; manual logging remains usable after independent F01 authorization, and stops after withdrawal |
| Purchases | No Apple/ASC/offer/entitlement change; keep `APPLE_SUBSCRIPTIONS_ENABLED=0` for this launch; test no purchase entry/offer and refused verify/context paths |

Historical FatSecret snapshot retention/replay is still an open licensing
blocker. The strict expected failure
`test_unbounded_provider_snapshot_is_not_replayed_after_24h` records that
blocker; it is never a compliance pass. Founder scope approval cannot grant
provider rights. No retention remedy or deletion is implemented here.

The only schema integration edit reparents the new, unshipped F01 expansion
after Launch Free: `c6f8b0d2e4a1 -> d8f1a3c5e7b9 -> f01c0a1e0001`, 62 revisions,
one head. Existing shipped migrations and Launch Free reviewed DDL stay intact.
The F01 source migration creates empty authority/evidence tables and does not
infer grants. Tests may use disposable databases; applying any migration to a
staging/customer database requires separate founder authorization.

## F01 withdrawal against pending O2

O2 is pending. No authoritative decision text labeled O2 was located in the
available F01/closure/triage evidence at integration time. The release owner
requested that source; alignment must remain UNRESOLVED until it is supplied.
This candidate implements the existing #490 contract, not a new legal choice:

1. Decline/withdraw remain available with retired/unpublished text and do not
   depend on a new health grant. Account/auth/recovery/privacy/deletion controls
   remain available; account health projection is masked while closed.
2. PG shared authority lasts through admitted processing, ORM commits and WSGI
   body cleanup. Exclusive withdrawal waits for admitted server work to finish.
   It does not cancel/recall provider work or guarantee remote-device delivery.
   A 2-second lock timeout/5-second statement timeout can return 503; a failed
   withdrawal is not confirmed and must be retried.
3. After acknowledged withdrawal, further health reads/writes fail closed;
   stale grant replay cannot restore permission, and regrant cannot revive a
   worker captured under the old health generation. Refusal/history retention
   do not establish a lawful retention ground or cure historical collection.
4. The paired mobile flow must block new dispatch, discard stale responses and
   in-memory forms/controllers, unbind recovery, and persist unconfirmed stop
   intent across restart/logout. Other devices close at their next protected
   request/refresh; no remote erasure is promised.

Legal/controller must decide whether finishing admitted work before confirming
withdrawal, the disclosed unavailable features, prospective historical reuse,
retention and consent-evidence disposition satisfy O2. If immediate cancellation
or a different data disposition is required, implementation and new tests are
required before publication. Keep `APPROVED_VERSION` unset. Publish only a new
reviewed source version with approved exact EN/TR bytes/digests after the
decision. Do not auto-delete records or silently enable processing.

## Five D1 feature activation work

Each row needs actual app-path staging and real-iPhone E2E before it is called
working. Synthetic fixture grants are test-only and must not enter a deployable
image. Memory, generic streaming, Pump comparison, menu OCR/PDF and other
unbound paths are outside these five features. Deferred legacy summaries with
no health-generation snapshot stop; Memory activation is not promised.

| Feature / native entry | Purpose | Required positive E2E |
|---|---|---|
| Coach `/api/v1/coach/messages` | `coach.conversation` | Contextual response, bounded confirmed tool action/replay, FatSecret intake exclusion, unusable-result settlement |
| Training `POST /api/v1/training/plans` | `training.plan` | Generation, fetch/apply, durable operation/recovery, general-plan fallback and replacement fencing |
| Nutrition `POST /api/v1/nutrition/plan/generate` | `nutrition.plan` | Valid usable plan, fetch/confirm, durable replay and invalid/unusable-result settlement |
| Single-photo Pump Check `POST /api/v1/pump-checks` | `pump_check.analysis` | Private result/history, valid image handling, XP/K6 completion and allowance receipt atomically; no social sharing |
| QR/menu `POST /api/v1/nutrition/menu/analyze` | `qr_menu.analysis` | Supported public HTTPS/text input, independent dish estimates, unavailable budget/scores/picks for unproven intake, durable result/replay |

Activation order (owners must supply evidence, not flags alone):

1. Close F01/O2 and the separate lawful health/service-cohort/transfer/recipient
   decisions. Qualify actual retention and provider facts; preserve the
   documented FatSecret fallback and D-B2 hold. F01 is independent of D1.
2. Approve exact five-purpose EN/TR text and categories from existing drafts.
   Install reviewed source `Disclosure` entries in `POLICIES`, exact bytes in
   `PUBLISHED_TEXTS`, exact purpose/version pins in `LAUNCH_VERSIONS`. Name all
   primary/fallback `(recipient, model/profile)` pairs. Coach helpers need the
   same purpose's explicit `nested_features`; QR needs exactly the reviewed
   `public_https_menu_source` external scope. Other-purpose grants do not
   broaden Coach. No environment/client value can publish these registries.
3. Qualify signed cohort assurance/freshness/review and only then propose a
   reviewed `APPROVED_SERVICE_COHORT_VERSION`. The current policy is adult TR;
   setting this pin alone does not implement 13+ access.
4. Obtain separately authorized schema/deployment work. Verify PG authority,
   Redis fail-closed spend guard and all finite physical budgets. Install the
   existing complete five-family `LAUNCH_V1_POLICY`; enable
   `AI_ALLOWANCE_LEDGER_ENABLED`, `AI_TRAINING_ALLOWANCE_ENFORCEMENT_ENABLED`
   and `AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED` together for qualification,
   with mobile auth and qualified routing. Apple stays OFF; do not follow stale
   flag-inventory Apple prerequisites to enable purchases.
5. Exercise separate prospective F01 + feature-specific D1 grant/refusal,
   withdrawal during request/worker/publication, replay, account switch,
   stale generation and Redis/provider failure. Confirm no unauthorized
   provider calls and no extra logical debit on replay/fallback/confirmation.

Launch Free is unchanged: 30/2/2/20/5 in each of three 30-day windows from
2026-11-25 00:00 Europe/Istanbul to 2027-02-23 00:00, start inclusive/end
exclusive; LF1/LF2 resets come from the server, LF3 reset is null. Outside the
campaign, Starter is 10/1/1/1/1 once per eligible account. A November 9 E2E
cannot demonstrate a live campaign window by changing runtime flags: use
disposable/test-only clock fixtures for boundary tests, never change the
staging host clock or silently edit campaign dates. A new date needs review.
No purchase is needed for finite Launch Free/Starter admission.

## 13+ eligibility activation work

`ACCOUNT_ELIGIBILITY_ENABLED` is default OFF; OFF is not a qualified 13+ launch.
The existing self-declaration screen is not verified age, guardian authority,
health permission or AI permission. Complete the focused five-market
TR/US/GB/CA/AU determinations for age assurance/capacity, regional obligations
and signed-in recovery/birthday/country changes. Resolve current TR 13–17,
CA age 13 and AU 13–14 qualification holds. Obtain the actual O2/service
authority opinion before opening a teen cohort.

Implement neutral teen onboarding/read models: existing adult weight-goal
maths must not run for teens. Keep body-photo/automated-weight capabilities
restricted unless separately qualified; do not promote by editing profile age.
Qualify age-appropriate D1 text/outputs and dispatch/publication capability
checks. Review `account_eligibility.purchase_eligible` coupling in `ai_launch`
before proposing teen AI admission; changing the age UI or flag alone cannot
open it. Pair pre-contact and signed-in review UI with backend, then test
under-13/out-of-market refusal, every held cohort, unknown/corrupt/copied
records, expiration/key rotation, adult and allowed teen accounts. Social
remains closed for all; purchases remain disabled. Universal five-market 13+
eligibility is not ready in this candidate.

## EU-only Bedrock activation work

Source defaults remain `AI_MODEL_ROUTING=legacy`; source default routing is not
EU-only (it includes a global Bedrock profile and direct OpenAI light/fallback).
No AWS runtime inspection or inference was performed in this integration task.
Do not infer live EU routing from `AWS_REGION=eu-central-1`.

1. Read-only staging operator checks must use only
   `AWS_PROFILE=axisai-staging AWS_REGION=eu-central-1` and confirm assumed
   `axisai-staging-operator`. If expired, stop and ask the human to run
   `aws login --profile axisai-login`; agents never run login. No default,
   deployer, root, new keys, secret output or denial workaround.
2. Re-discover actual available profiles/model access/quotas from eu-central-1.
   The separately authorized staging activation must set `BEDROCK_ENABLED=1`,
   `BEDROCK_REGION=eu-central-1`, `BEDROCK_PROMPT_CACHE=0` and
   `AI_MODEL_ROUTING=claude55`. Source supports
   `BEDROCK_SONNET55_MODEL=eu.anthropic.claude-sonnet-5-5` and
   `BEDROCK_HAIKU55_MODEL=eu.anthropic.claude-haiku-5-5`; these are source
   registry entries, not verified account availability. If unavailable, stop
   and propose a reviewed supported route; never substitute global/OpenAI.
3. Call Bedrock `GetInferenceProfile` for both exact profiles from the source
   region; record profile status, destination model ARNs/regions and verified
   EU-member locations. Review retention/invocation logging and transfer facts
   separately. A geographic prefix alone is not residency evidence. See
   [AWS profile verification](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html).
4. Have the authorized infrastructure owner qualify IAM/SCP access for the
   exact profiles and all destination models, including InvokeModel and stream
   actions as applicable; exclude global/non-EU/direct OpenAI escape paths.
   Changes require the proper separate authorization; a denied operator read
   is an external dependency, not permission to use another identity.
5. After approved recipient/D1 contracts and staging activation, test primary,
   Haiku fallback, refusal, throttling, timeout, invalid profile and vision
   paths. Capture sanitized model/profile and CloudTrail
   `additionalEventData.inferenceRegion` receipts for actual calls; assert all
   destinations remain EU and no OpenAI/global attempt occurs. Keep explicit
   token budgets, existing retry/physical/spend ceilings and prompt cache OFF.
   [AWS routing evidence](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html).

EU inference geography alone does not resolve O2, Article 9/transfer or provider
retention obligations. Runtime region/model access, quotas, primary/fallback
behavior and end-to-end route evidence all remain unverified here.

## Actionable staging and real-iPhone run order

**Lane A — closed-boundary staging qualification:** after source review/CI and
separate founder authorization for staging deployment and any schema work,
deploy the exact reviewed SHA through the established R6 path. Record image
digest, baked `BUILD_REVISION`, serving slot, schema revision and sanitized
boolean configuration. Do not auto-run migrations to bypass that authorization.
Keep F01/D1/cohort closed, social closed, D-B2 held, Apple OFF. Use synthetic
controlled accounts/data, never production data. Verify health denial before
payload/provider/storage, usable account controls, social 404/no-store before
DB/body work, masked account projection and no purchase availability. This
lane can prove negative gates; it cannot prove usable health or five AI flows.

**Lane B — authorized positive qualification:** requires the reviewed F01/O2,
D1/cohort/EU-route activation changes above and fresh exact-head CI/review.
If the legal decision has not arrived, leave this lane BLOCKED. Use the paired
mobile F01 implementation from mobile draft #174 or a later explicitly
integrated equivalent; also include current Launch Free allowance/error UX
from mobile main and verify it accepts `LAUNCH_FREE`. Resolve/pin the actual
mobile SHA before building; #174's mention is not proof that current mobile
main includes it. Backend integration does not modify mobile or ASC.

On a real iPhone, record device/iOS, mobile SHA/build/flavor, API staging host,
backend SHA/image and UTC times. Test EN and TR, fresh and existing synthetic
accounts, account switch and foreground/restart recovery. Use the native
installed build with staging credentials, not a screenshot/simulator substitute.

| E2E group | Required evidence / pass condition |
|---|---|
| F01 refuse/grant/withdraw | Exact text/digest; refusal before health UI/dispatch; controls work; server-confirmed stop; network failure persists stop intent; retry/restart/logout; stale response discarded; second device refused at next request; historical rows unchanged |
| Five D1 features | Each positive row above plus per-purpose refusal/withdrawal, stale generation, cross-account isolation; actual provider route and durable usable result/receipt required |
| Allowances | Starter now; test-only campaign boundary/exhaustion fixtures for LF1/LF2/LF3; displayed server resets; one debit per usable result, no duplicate charge, unusable outcome/refund vs unknown hold, provider failure/Redis loss fail closed |
| FatSecret | Provider search/attribution permitted only under existing lookup rules; new provider write held/nonretryable; manual write works with F01; historical replay unchanged; no historical nutrients in Coach/QR; unproven budget/score null, picks empty; R-5 withheld |
| Social/global/private | All social routes and friends scopes closed, including direct web calls; global rankings and private Pump history work only with required health/capability authority; no new social notification or share; historical metadata unchanged |
| Purchases OFF | No purchase/trial/paywall offer; purchase context/verify refused; no StoreKit transaction/ASC edit; allowance scope never grants Premium |
| Failure/recovery | Timeout/disconnect/invalid output, idempotent replay, concurrent confirmation, stale operation after regrant, cancel/foreground, no provider duplication or result leakage |

Capture sanitized request IDs, response codes, operation/receipt identities,
before/after synthetic row counts and logical/physical debit counts. Keep health
payloads, credentials, signed tokens and secrets out of shared logs. Link actual
E2E artifacts and explicitly mark each row PASS/FAIL/BLOCKED with its owner and
next action. A checklist with no execution evidence is not an E2E pass.

## Release holds and recovery

Open blockers: O2/F01 legal/publication and retained-record disposition;
FatSecret historical retention; D1 recipient/text/transfer approvals and actual
five-feature E2E; signed service/13+ authority and neutral teen UX; EU-only
runtime/profile/quota/permission qualification; paired real-iPhone integration;
any final CI/review findings. Do not call this ready for positive staging E2E
until those prerequisites are satisfied. Closed-boundary testing is a separate
readiness state, conditional on authorized deploy/schema work.

Stop admission on an unexpected disclosure/provider attempt, purchase offer,
social opening, history mutation or duplicate debit. The staging operator must
use the existing R6 recovery procedure and a reviewed compatible image, retain
all records/consent evidence and keep activation gates closed. Do not downgrade
away populated F01 evidence. Previous code may lack F01/social protections;
rollback does not authorize serving those routes openly. No production deploy,
main merge, operational migration, historical deletion or ASC change is
authorized by this candidate.
