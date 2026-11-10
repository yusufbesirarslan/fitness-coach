# F01/D1 activation readiness — 10 November 2026

Task: `AXISAI-F01-D1-LAUNCH-ACTIVATION-READINESS-20261110`.
Production/source audit pin: `c51f142247341c79db979a70ed53461a19bfb3df`.
GitHub main was read back at that same SHA. Production identity and the successful
R6, PB1/PB2 and M1–M3 dispositions are supplied/reused; no AWS read or write,
production test, consent change, activation, provider switch, main merge or
deployment was performed. This draft is a source-only successor to that pin.

## Recovered authority, without new product or legal requirements

The complete recovery inventory, including SHA-256 of all 27 specification,
legal-opinion and exact-text files, is
[`recovered-specifications.json`](../../qualification/f01-d1/recovered-specifications.json).
External paths identify the retained local approval packet; they are not claims
that those files have been published or installed in production.

| Authority | Controlling decision / distinction |
| --- | --- |
| F01 source specification, `F01_FIRST_PARTY_HEALTH_CONSENT.md`, and #500 integration specification | Separate first-party health grant; exact EN/TR v2/digests; collection/use/publication boundary; no grandfathering. Founder `AXISAI-F01-A1-O2B-FOUNDER-DISPOSITION-01`: stop health use immediately; erase health inventory within 30 days; minimum matter-bound holds; no restore on regrant; no export promise. Treat workouts conservatively as health until separately supported classification changes. The older retention/export suggestions are superseded. |
| D1 `D1_LAUNCH_V1_INTEGRATION.md`, `D1_PHASE2_SOURCE_INTEGRATION.md`, `LP21_D1_OPERATION_FENCING.md` plus current code | Five separate native purposes: Coach, Training, Nutrition, single-photo Pump, public web/text QR. Coach's approved-contract helpers and QR nested nutrition use the original authority. Memory, summaries, streaming, OCR/PDF, comparisons, replacement and browser aliases remain outside native launch opening. Historical documents' implementation-gap labels must be reconciled with the integrated source, not revived. |
| Founder commercial lock and failed-action delta (5 November), later full-functional integration, Launch Free #491 | Starter 10/1/1/1/1; Premium 60/2/2/16/2; same monthly pool for monthly/annual/trials; usable result consumes one logical unit; proven unusable technical outcome refunds independently of provider cost; UNKNOWN reconciles without blind replay. Current source also contains approved Launch Free 30/2/2/20/5 in three campaign-relative 30-day windows starting 25 November Istanbul. These decisions are closed; this task does not ask for quantities again. |
| Existing D1 activation decision package | Sonnet 5.5 primary / Haiku 5.5 fallback, preferred Bedrock EU, is a recorded founder preference. Exact available inference profiles, actual serving routes, transfer and retention facts remain evidence requirements. This is not permission to switch today's route. |
| `AXISAI-G4-13PLUS-FINAL-CONSENT-QUALIFICATION-01` and final R1/R2 candidate receipt | Bounded capacity opinion: F01 wording supportable for discerning minors; D1 supportable after exact R1/R2 redlines. All ten replacements already prepared, 100/100 consistency checks in that packet. No new mandatory parental mechanism or blanket 18+ policy is invented here. Opinion is conditional, not final publication/activation approval or a replacement for actual operation-specific capacity/eligibility qualification. |
| R6 readiness receipt, 9 November, monitoring table M4/M5 | Exact gates quoted below. Their activation scope survives the later R6/M1–M3 successes. |

**M4 (exact):** Gauges for pending count, oldest-pending age and last successful
`retry_health_erasures` age. Alarm when no successful retry pass in **>75 min**
(one hourly tick plus 15 min grace). Page when **any erasure is still pending
after 2 consecutive hourly retries**. Critical when **overdue >0 (30 d)**.
Logging alone is not accepted. Blocks F01 `APPROVED_VERSION` activation.

**M5 (exact):** After deploy, read queue/job registry over a **one-hour window
with zero health traffic**. At least one `retry_health_erasures` must have been
enqueued and finished. **Read-only, separate authorization.** Blocks F01
activation. A synthetic clock, local PostgreSQL, an empty erasure table or a
successful daily/CLI pass does not satisfy this gate.

The later R6 source-readiness report labels M4/M5 unimplemented and uses
different shorthand (pending age >75 minutes, a proposed completion-pulse
metric and a synthetic staging drill). No approved supersession of the full
explicit table was found. This task preserves that full table, prepares the
synthetic drill separately, and does not silently adopt the shorthand as a
new requirement. The latest deployed receipt qualifies R6/PB1/PB2/M1–M3; its
idle-queue snapshot and 16-minute watch are not M5's one-hour proof.

## Integrated-source findings

| Area | Already implemented / qualified source | Genuine remaining prerequisite |
| --- | --- | --- |
| F01 approval and versioning | `health_disclosure.APPROVED_VERSION=None`. Candidate `health-fitness-v2-review`, separate purpose and exact locale SHA-256. Server-owned approval; request/env flags cannot publish it. Consent state/events use authenticated owner, CAS generation, exact digest, UTC timestamp, idempotent request digest. Unpublished/retired/malformed grants deny; stopped decisions remain available. | Controller/legal sign-off on exact version and handling of pre-F01 records of accounts that never grant. A later reviewed publication change is intentionally absent. |
| First-party boundary | Default protection for authenticated product routes before body/image/context collection; ordinary account/consent/deletion controls remain. Independent PG shared health locks cover processing/response lifetime; withdrawal exclusive lock waits for admitted work; old worker generation cannot be renewed by regrant. Authority failures deny. | Later paired-client/device and target-environment receipt at the reviewed activation candidate. |
| D1 approval/publication | `POLICIES`, `PUBLISHED_TEXTS`, `LAUNCH_VERSIONS` are empty. Exact ordered purpose/categories, recipient/model pairs, EN/TR body hashes, nested/external scopes are in the contract digest. `ai_launch` requires selected exact version at capture, dispatch, canonical publication and native socket publication. Unsupported transports/streaming fail closed. | Final approved five-purpose contracts and exact ten texts/digests, source registration/publication and qualified service-cohort pin; none may be filled with guesses. |
| Finite policy | `launch_policy.APPROVED_POLICY` **already equals `LAUNCH_V1_POLICY`** at c51f1422, including approved campaign. Older docs saying None are historical. Policy alone cannot open endpoints: enforcement flags, Redis, spend guard, cohort and exact grants remain required. | Actual nonproduction/runtime guard and cohort qualification, then separate activation authorization. No commercial redecision or new ledger is needed. |
| Withdrawal/deletion | F01 commits stopped state + obligation, attempts storage before atomic health-row removal. Failed S3/cache/local deletion remains pending; no new grant until completion. Account deletion must prove storage inventory; missing account alone cannot falsely close debt. #504 legacy `activity_clap` fix is already integrated. | Adopt remaining legacy-never-granted retention/hold/evidence dispositions; target operational evidence, not another implementation of deletion. |
| Retry | Worker timer enqueues at startup and hourly, independent of HTTP; job timeout 900 s, queue TTL 3600 s; bounded retry scans prevent held/failing owners starving later owners. Daily/CLI pass also exists. | At c51f1422, M4 had logs only. M5 runtime proof is still absent. |
| Fail closed | PG-only distributed authorities; SQL/storage/unknown generation failures deny or leave debt pending. Paid entitlement/allowance cannot grant consent. Native opening and purchase readiness need the approved service authority, not a mutable age/profile assertion. | Existing assurance/freshness owner must supply/qualify the service-cohort contract; current source pin stays None. Do not invent an age questionnaire, TTL or parent requirement. |

### Complete provider dispatch boundary

[`provider-boundaries.json`](../../qualification/f01-d1/provider-boundaries.json)
records all 30 direct boundary/forwarding references in `app`. Static tests
also scan `app`, `fitx_mcp` and `scripts` for new direct SDK sends and
unclassified feature boundaries. Domain functions taking an injected chat or
image function resolve to these shared adapters; an injected test function is
not a second production transport.

| Dispatch family | Shared physical door and authority |
| --- | --- |
| Coach blocking/tool rounds, Training and Nutrition generation/recovery/repair | `ai.py` heavy/light adapters, `ai_coach.py` blocking/tool admissions → `ai_provider_call.admit` → every physical `dispatch`. Original operation owner/generation/version persists through primary, fallback, repair and retries. |
| Pump single image; dormant comparison; Coach image helper | Shared Bedrock image adapters → same admission/dispatch. `vision` is not a generic permission; Coach helper must be named in its original contract. Comparison endpoint stays closed. |
| QR extraction, nested normalization/estimation; food helpers | Same adapters. QR nutrition resolves to original QR binding; Coach food helper to original Coach binding when explicitly covered. Independent Nutrition/Pump permission cannot broaden Coach/QR. QR source website fetch needs its exact external scope and original generation. D1 composition avoids direct FatSecret requests. |
| Legacy meal helpers, generic nutrition, Memory/summary, menu OCR | Unbound outside explicitly qualified original operations; legacy endpoint holds remain. Old summary jobs without a health-generation snapshot stop before reading content. `stream_dispatch` refuses user streams. |
| Operational readiness probe | Only `probe_bedrock_readiness()`; fixed `ping`, max output 1 token, bounded 5 s, no caller payload/identity/tools, retry or fallback. It cannot process user data; a `health_probe` feature label supplied to normal admission does not confer this authority. No probe was invoked here. |

Legacy routing remains an implemented possibility: Bedrock Sonnet 4.5 heavy,
OpenAI gpt-4o-mini light/fallback; disabled/unavailable heavy can start on light.
The optional claude55 route uses exact registered Sonnet/Haiku profiles with no
profile default, rejects unknown models and excludes OpenAI. Routing defaults
and package presence are **not** deployed-provider evidence. Dependencies remain
`anthropic[bedrock]==0.120.2`, `openai==2.50.0`, `gunicorn==26.0.0`; SDK retries
are disabled and physical attempts are explicitly metered/consent-checked.

**Bypass result:** no source bypass found. Existing tests cover actual heavy
fallback to an unlisted OpenAI recipient, ungranted Haiku fallback, revoke then
regrant between admissions/physical retries, changed same-version contracts,
missing publication, foreign owners, legacy routes and fixed probes. The new
20-case PG matrix additionally retires a selected version after capture across
**all five purposes × legacy OpenAI / legacy Bedrock / EU Sonnet / EU Haiku**:
zero SDK calls and no publication. A fresh exact successor grant reaches the
same stub SDK as a non-vacuity control. Tests change only synthetic test-local
routing; no runtime provider configuration was changed.

## Focused M4/M5 source completion

New `health_erasure_retry` holds only obligation id, a saturated 1/2 retry
counter and last scheduled UTC-hour slot. It has an erasure-receipt FK with
CASCADE, no account FK, payload or alternate consent authority. New additive
migration `f01a4e000003` follows the unchanged `f01e2a5e0002`; all old migration
bytes remain intact. Populated downgrade refuses evidence deletion. Parent
receipt expiry removes the counter at the existing three-year boundary.

After the actual hourly retry finishes, surviving obligations from the initial
pass snapshot are counted in bounded 200-row transactions. Per-obligation locks
deduplicate overlapping workers. Distinct out-of-order scheduled hours count;
duplicate jobs from the same hour cannot supply two-retry proof. Two is terminal
until that obligation completes. A failed whole pass supplies no success
snapshot; completed passes with individual failures still expose pending debt.
Daily/CLI passes do not manufacture hourly evidence.

A single aggregate Redis snapshot feeds the existing fork-safe worker metric
publisher. After all bounded counter batches, a fresh monitoring-only PG
transaction takes a disjoint advisory lock (namespace 17922), with 2-second
lock and 5-second statement timeouts. It allocates `txid_current()` only after
that lock, reads aggregate state, stamps actual completion and commits. Lua
compare-and-set orders the v2 snapshot by that sequence, not process-start or
wall-clock order. This prevents a later-finishing pass with the two-retry
state from being rejected as older. A deterministic regression reproduced
the missed alert under the earlier timestamp ordering before the correction.
Stale or duplicate publication is rejected even if Redis stores arrive late.
The parent opens no DB connection. Gauges include pending count, oldest-pending
age, last-completed-retry age and count pending after two retries. Ages advance
with zero health traffic. `HealthErasureOverdue` is explicitly a **0/1 breach
indicator**, not an exact overdue count; it becomes 1 when the oldest unheld
obligation passes 30 days. Active holds retain their existing deadline exception
but still contribute to pending/two-retry paging; expired review dates never
automatically release holds.

Pending/two-retry/hold membership is sampled after each hourly pass, so a
completion/hold change can remain conservatively visible until the next pass.
Last-pass freshness alarms bound a stopped sampler. Missing/corrupt snapshot or
failed publication never fabricates zero age. One fixed `Service=worker`
dimension; no account/job identifiers in CloudWatch.

`f01_erasure_alarm_plan.alarm_plan(page_topic, critical_topic)` prepares three
**unapplied** PutMetricAlarm parameter dictionaries, actions disabled by default:
last-pass age **>4500 s**, two-retry pending **>0**, deadline indicator **>0**,
60-second maximum evaluation; missing last-pass age breaches. Topic ARNs must be
existing, approved channels. Plan shape is validated against the local botocore
service model. This is not a CloudFormation template, change set or AWS action.
No existing M1–M3 alarm plan is changed.

Only erasure jobs now retain aggregate-only RQ results for **7200 s** instead
of immediate deletion, enabling later M5 FinishedJobRegistry inspection. A
source-owned job-id prefix and exact task marker allow the observer to use
HMGET on six explicit metadata fields, without HGETALL, pickle deserialization,
arguments, results or unrelated-job metadata. Qualification requires ordered,
timezone-aware timestamps wholly inside the measured window. Queue
TTL, timeout, scheduling and failure TTL remain unchanged.

## Completed source qualifications and limits

| Selection | Result | Inspectable evidence |
| --- | --- | --- |
| Initial F01/erasure/disclosure/provider inventory/routing/probe selection | 289 passed, 1 existing skip | Captured session result; PostgreSQL-specific cases qualified separately below. |
| Launch/usable-result/consent reconciliation/fencing, new M4, migration graph and affected integration | 157 passed, no skips | `qualification/f01-d1/offline.xml` |
| Real disposable PostgreSQL F01/D1/erasure/operation/publication + new M4 overlap/cascade/adoption | 173 passed, no skips | `qualification/f01-d1/postgres.xml` |
| Five-purpose retired-version matrix + existing QR/cohort composition | 29 passed, no skips | `qualification/f01-d1/selected-version-postgres.xml` |
| Final M4, Lua CAS, real RQ classes with emulated Redis, read-only M5 reader + existing timer | 18 passed, no skips | `qualification/f01-d1/m4-final.xml` |
| New-successor compatibility in the historical migration fixture (seven hosted failures corrected) | 7 passed, no skips | `qualification/f01-d1/successor-compatibility.xml` |
| Final M4 aggregate-order regression, overlapping counters, retention and migration | 6 passed, no skips | `qualification/f01-d1/snapshot-order-postgres.xml` |

Selections overlap and are not additive unique coverage. Local PostgreSQL is
16, not a new PG18 certification; existing R6/hosted PG18 receipts are reused.
Redis/Lua uses `fakeredis==2.31.3` with the Lua engine; it proves source behavior,
not real Redis availability, CloudWatch delivery or target alarm actions. The
real RQ classes complete jobs on that emulator. Upstream deprecation warnings
were non-failing; no test was removed or weakened. Required hosted CI on the
Draft PR remains separately visible. Its first PG18 run found seven stale
historical fixture assumptions that still treated the four-revision R6 prefix
as the current head. The fixture now preserves that frozen prefix and adds
only M4 as its successor; the seven affected checks pass locally. New M4 PG
cases are explicitly included in both hosted PG16 and PG18 selections. This
qualifies the new migration delta; R6 and M1–M3 are not reopened as new gates.

Independent review was requested under this task's explicit review authority.
It identified and closed stale-snapshot, out-of-order-counter and unbounded
transaction defects, independently ran the new source checks and inspected the
PG evidence. Review also closed the observer's unsafe RQ deserialization and
unbounded timestamp predicates; hostile serialized fields and malformed,
future and out-of-order timestamps now have negative tests. Final review also
confirmed the aggregate snapshot-order race and its serialized-sequence fix;
the regression fails before that correction and passes after it. Final reviewed
commit and disposition belong in the PR/receipt.

## Exact later nonproduction qualification

This is a prepared plan, **not execution authority for a deploy or AWS write**.
Use the current staging identity only: `AWS_PROFILE=axisai-staging`,
`AWS_REGION=eu-central-1`. Production access needs a separately authorized human
operator/workflow; this session cannot obtain or impersonate it. Never login
as an agent, switch profiles, print secrets, or work around MCP denial.

1. Pin the reviewed successor SHA, immutable web/worker image identifiers,
   additive schema head and paired mobile build. Retain F01 approval None,
   empty D1 registries, cohort pin None and current provider routing. Apply
   migration/source/alarms to an isolated nonproduction environment only under
   separate deployment/AWS-write authority. Record runtime role privileges for
   the new retry table, Redis read/write path and existing metric namespace.
2. **M5 zero-traffic proof:** keep all health HTTP/user traffic stopped for a
   measured ≥3600-second interval; do not call protected endpoints or seed real
   accounts. Attach access/worker-log evidence for the entire interval and the
   serving worker-image SHA. Supply a read-only Redis credential out of band as
   `F01_QUAL_REDIS_URL`. From the same reviewed source run:

   ```sh
   python qualification/f01-d1/m5_readback.py --queue fitx:ai > m5-readback.json
   ```

   It polls queue + FinishedJobRegistry with cleanup disabled, reads job
   metadata only and never enqueues/executes/cleans jobs. PASS requires an exact
   `app.jobs.tasks.retry_health_erasures` job enqueued and finished inside the
   interval, including started/ended timestamps. No job arguments/results or
   user payloads are output. Its serving-SHA and zero-traffic fields explicitly
   require the separate receipts. Capture real Redis version and registry
   retention. No success inferred from zero health rows or healthy M1/M2.
3. **M4 delivery:** with permissions still off, observe real empty-pass metrics:
   pending=0, oldest=0, two-retry=0, breach=0, last-pass age resetting after the
   job and increasing between passes. Verify exact namespace/dimension and
   each generated alarm's thresholds, missing-data behavior, approved topic,
   action state and receipt at the intended pager/critical destinations.
   In isolated nonproduction only, suppress hourly job completion for >75 min
   and lose the snapshot/publisher separately: freshness must alarm on stale
   or missing data. Restore normal scheduling and observe recovery.
4. **M4 debt semantics:** use synthetic identities/storage only in that isolated
   environment with a test-only versioned contract. Force an object/cache
   failure; witness pending debt across two actual scheduled hourly retry slots
   and a page, with no double count on duplicate worker/startup jobs. Test
   out-of-order slots and recovery. Put an unheld synthetic obligation just
   below then beyond the 30-day boundary and witness critical escalation;
   test a named hold separately and its review process. Resolve test debt and
   preserve sanitized counters/times. Do not age, hold or delete production
   records, use real personal data, or populate production approvals.
5. **F01/D1 full composition:** with provider SDKs stubbed first, run the saved
   selections and the five-purpose exact-version matrix against the final
   source/paired clients. Include pre-body refusals, foreign account, withdrawal
   in flight, durable failed stop across logout/restart, publication, old worker
   generation, storage failure/retry and regrant after completion. For any
   later paid/provider qualification, obtain explicit separate authority and
   exact recipient/model/location/retention facts first; no route switch is
   implied. Synthetic contracts never become approval evidence.

## Later production activation checklist — retains HOLD

1. Reuse qualified R6, PB1/PB2 and M1–M3 receipts at their actual SHAs and scope;
   do not repeat the restore drill or reopen already-closed AWS signing/filing,
   commercial quantities, model ordering or O2-B choice. Assess only the new
   successor's migration and operational delta through normal release review.
2. Controller and the existing legal owner sign an exact-version approval
   receipt (reviewer, date, purpose/scope, EN/TR text hashes, contract versions
   and remaining-condition disposition). Carry forward the bounded G4 opinion
   and its exact R1/R2 corrections. Required unresolved items: final F01/D1 and
   linked Privacy Notice publication/adoption; selected-provider processing/
   transfer/retention coverage within the existing AWS arrangement; purpose-
   bounded holds/evidence retention and lawful handling/end-of-retention of
   pre-F01 health records for owners who never grant. Workouts remain in the
   conservative erasure inventory unless a separate supported classification
   and paired wording change is approved. No generic second legal review or
   new parental-verification requirement is added.
3. Obtain owner-qualified service-cohort assurance/freshness disposition. The
   legal opinion's capacity support does not itself fill the source pin or
   establish actual runtime evidence. Verify intended provider profiles,
   dependencies, IAM, allowed processing destinations and retention, and all
   primary/helper/fallback pairs against **each** final consent contract. A
   legacy/OpenAI route outside an approved Bedrock-only contract must refuse;
   it cannot be treated as an approved fallback. Any routing change is a
   separate reviewed and authorized change.
4. Complete M4 nonproduction delivery/fault receipts and M5 measured runtime
   receipt. Have a separately authorized production release operator review
   the focused successor, schema privileges, alarm plan/channels and rollback/
   forward-recovery compatibility. A later authorized rollout while features
   remain off precedes production M4/M5 readback, using separate read-only
   authority and no health traffic. No enablement based only on this source PR.
5. Verify the intended public notice URL serves the legally approved stamped
   EN/TR version/hashes. The R1/R2 packet's saved public URL was older than its
   PR #78 source; source correspondence alone is not live publication proof.
   Reuse the separate landing/publication owner's final receipt; no landing
   rollout is performed here. Pair mobile exact server-text display/refusal/
   withdrawal with the final backend candidate.
6. Prepare a separate reviewed **publication/activation source change** that
   names the approved F01 version, all five D1 policies/texts/selected versions,
   exact scope/recipient tuples and qualified cohort version. Do not synthesize
   these values from env/client flags. Requalify only that delta and its final
   integration; carry current finite allowances and existing independent
   provider/FatSecret/Apple gates according to their actual receipts.
7. Founder gives explicit final activation authorization naming the exact
   source/images, scope/cohort/purposes, approved versions, model routes,
   monitoring receipts, operator and rollback/stop plan. No such authority is
   granted or requested by this source-only qualification. Unpublished/held
   purposes and unsupported routes stay closed until their own gates pass.

## Disposition

- M4: source/test completion prepared in this Draft PR; target metric delivery,
  alarm actions and activation acceptance remain HOLD.
- M5: scheduling/source evidence and readback tooling qualified; the required
  after-deploy one-hour zero-traffic proof remains NOT EXECUTED / HOLD.
- F01: bounded source closure complete for review; legal/publication, legacy
  disposition, target M4/M5 and later activation change remain.
- D1: consent/route/version fences source-qualified; final approved contract
  installation, service-cohort and provider/runtime receipts remain.
- Next minimum founder decision: review the focused M4/M5 Draft PR and route
  the exact remaining approval set to the existing controller/legal and runtime
  owners; retain activation HOLD. No quantities, model ordering, O2-B, R6 or
  M1–M3 reapproval is needed.
