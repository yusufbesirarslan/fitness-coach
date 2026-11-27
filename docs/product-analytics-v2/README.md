# Product analytics v2 — first-public, privacy-first (SOURCE ONLY, default OFF)

Product authority: #547 (F5: privacy-first opt-in analytics is a mandatory
launch gate). Supersedes, for first public, the v1 drafts in
`docs/product-analytics-v1/` (#541, kept unmerged) and mobile
`docs/posthog-eu-v1/` (#211, merged hard-disabled).

**State:** `SOURCE_READY` candidate only. Not merged, not deployed, not
activated. No vendor project, token or network transport exists. Flag
`FITX_PRODUCT_ANALYTICS_ENABLED` (backend) and define
`AXISAI_PRODUCT_ANALYTICS_V2_ENABLED` (mobile) are OFF by default; OFF is
byte-for-byte inert (routes 404 before auth, emitters return without a DB
read, mobile composes nothing).

## 1. Reconciliation decision (gateway vs. PostHog EU Flutter SDK)

| | Founder selection kept | Changed |
|---|---|---|
| Vendor / region | **PostHog EU Cloud** (`eu.i.posthog.com`) remains the only analytics destination; dashboards are built in PostHog. | — |
| Dispatch path | — | **Server-mediated.** The app never initializes the PostHog SDK. Client observations go to the AxisAI backend; the backend alone sends to PostHog EU, with an exact allowlist. |
| Success authority | — | Success events are **emitted by the backend's canonical writers after commit**. The app cannot send them. |

Why: the synthetic capture (`SDK_SYNTHETIC_CAPTURE.md`) measured that the
native SDK adds 27 device/app/locale/network properties after Dart's
`beforeSend` (S1), calls `/flags` and remote config although disabled and
while opted out (S2), delivers queued events after `optOut()+reset()` (S3),
keeps a stable `$device_id` across withdrawal/regrant (S4), and revives and
duplicates its disk queue on relaunch (S5). Each violates an explicit
requirement. The server path removes all five by construction and also keeps
the user's IP address away from the vendor (the vendor sees AxisAI's egress).

The `posthog_flutter` package stays in the mobile source, hard-disabled
(`posthogPrivacyClearanceApproved = false`, `DeniedAnalyticsConsent`) and is
never composed by v2. **Recommended founder decision D-2:** remove it from the
first-public binary (its privacy manifest declares "not linked" data and three
required-reason APIs that the first-public app would not use).

## 2. Components

Backend (`app/services/product_analytics/`, `app/blueprints/mobile_analytics.py`):

| Module | Role |
|---|---|
| `consent.py` | The ONE independent optional analytics authority. Grant needs the exact current notice version; random per-generation `subject` (pseudonym) + opaque `grant_id`; renewal keeps the subject, a grant after withdrawal mints a new one; 365-day expiry (proposal). Withdrawal: generation+1, outbox rows deleted, durable vendor-erasure job, subject cleared. Account purge enqueues erasure before the mapping is deleted (`app/cli.py::_purge_user`). |
| `emit.py` | `emit_authoritative(user, name, canonical_ref, props)` called right after a canonical writer's commit. `event_id` = HMAC(server key; subject, name, canonical ref) shaped as UUID v8 → replays/retries of one committed transition collapse on `(subject, event_id)`; the domain reference never leaves the server. Never raises into the writer. |
| `ingress.py` | Strict client batch validation (exact keys, closed names with `authority: client`, enum properties, UUIDv4 ids, `[received−24h, received+5min]`, ≤20 events, ≤16 KiB). Account fence: owner = Bearer principal and `consent_grant` must equal that owner's current valid grant (else 409, nothing stored). |
| `wire.py` | Exact PostHog `/batch` body: `{api_key, batch:[{event, distinct_id=subject, uuid=event_id, timestamp, properties}]}`, properties = catalog values + `$process_person_profile:false`, `$geoip_disable:true`. Nothing else can be added. |
| `dispatch.py` | Outbox → wire → transport. Each row re-checked against the owner's CURRENT consent under a share lock held across the send (withdrawal's exclusive lock waits or wins); generation AND subject must match, so a stale retry can never be re-bound to a new grant (fixes #541 A6). Bounded retries (5, exponential), 48 h max age. Erasure jobs → `transport.delete_subjects`. |
| `transport.py` | **Synthetic only.** There is intentionally no network transport in this source (architecture test forbids network imports in the package). |
| Migration `6e25ecb68c09` | Three empty tables (consent, outbox, erasure; erasure has no user FK). Expand-only, re-runnable, downgrade refuses rows. Cut from `e6a4b91d702c` (#568); integration owner re-points `down_revision` once. |

Mobile (`lib/core/analytics/first_party_analytics.dart`,
`lib/features/analytics_consent/analytics_consent.dart`,
`lib/features/analytics_consent/analytics_consent_panel.dart`): Settings → Privacy & data →
"Product analytics" (only when the define is `true`); switch off by default;
withdrawal denies locally and synchronously before the network call; queue in
memory only (≤100, batches of 20, 30 s / background flush, 3 attempts, stable
ids), fenced by grant id + session epoch + generation; server-owned success
events are not mappable; no vendor SDK, no disk.

## 3. Exact event inventory (catalog `catalog.json`, `review_status: PROPOSED_PENDING_CATALOG_REVIEW`)

No event has a free-text, numeric, health, content or identifier property.
Every property value is a closed string enum.

| Event | Authority | Properties | Exact trigger (canonical writer) |
|---|---|---|---|
| `axis_v2_analytics_enabled` | server | — | `consent.grant` minted a new grant generation (atomic with the grant) |
| `axis_v2_onboarding_completed` | server | — | `account_profile.complete_onboarding` committed `profile_complete` false→true |
| `axis_v2_training_plan_proposed` | server | — | `mobile_training_generation.store.stage_candidate` committed a PD2 preview candidate |
| `axis_v2_training_plan_created` | server | `path` ∈ `ai_preview_accepted`, `ai_direct`, `general` | `store.commit_plan` (AI) / `store.commit_general_plan` (general) committed the plan row |
| `axis_v2_workout_completed` | server | — | `workout_completion.complete_workout` CREATED (one canonical completion-day claim) |
| `axis_v2_coach_reply_completed` | server | — | `memory_manager.record_turn` committed a non-interrupted assistant reply |
| `axis_v2_coach_mutation_applied` | server | `domain` ∈ `training` | `plan_mutation.apply_plan_mutation` committed `applied` with actor `ai_coach` (no_op/replay/refusal/undo excluded) |
| `axis_v2_weekly_checkin_submitted` | server | — | `mobile_weekly_checkin.submission.submit` committed a NEW check-in |
| `axis_v2_feature_failed` | server | `feature` ∈ `training_plan_generation`; `retryable` ∈ `yes`,`no` | `store.record_failure` committed a terminal generation failure (consent refusals excluded) |
| `axis_v2_app_opened` | client | — | first authenticated foreground of a process under a valid grant (once per grant per process) |
| `axis_v2_app_resumed` | client | — | foreground after ≥30 s background |
| `axis_v2_screen_viewed` | client | `screen` ∈ `today, plan, plan_create, coach, progress, onboarding, workout, nutrition, nutrition_plan, settings` | coarse top-level screen change, duplicates suppressed |
| `axis_v2_onboarding_started` | client | — | onboarding screen first shown under a valid grant |
| `axis_v2_onboarding_step_completed` | client | `milestone` ∈ `step1..step4` | accepted onboarding advance |
| `axis_v2_training_plan_requested` | client | — | explicit plan-generation request (never success) |
| `axis_v2_plan_viewed` | client | — | existing plan shown |
| `axis_v2_progress_viewed` | client | — | Progress summary shown |
| `axis_v2_coach_message_sent` | client | — | explicit Coach send (no content/length) |

Explicitly excluded: signup started/completed (no pre-auth collection is
approved — **signup funnel N/A**); every v1 `*_observed` client success event;
food log / Nutrition contents; Coach Nutrition WRITE (enum value added only
when that writer exists and is reviewed); person properties, `identify`,
`alias`, groups; any device/app/OS/locale/timezone/network/screen metadata.

Known instrumentation limits (documented, not hidden): web (browser) plan
creation via `plan_replacement` is not instrumented (first public = mobile);
Coach mutation *attempts/refusals* are not instrumented, so a mutation
*success rate* is N/A (counts and per-user rates only); a crash between a
writer's commit and the analytics insert loses that one event (undercount).

## 4. Dashboard specifications (PostHog EU; none created)

Common rules: occurrence time UTC, calendar days Europe/Istanbul; unit =
`distinct_id` (per-grant pseudonym, personless); every panel shows x/n and the
observable cohort ("subjects consented at the time"); 24 h ingestion
watermark; windows reported only when mature; withdrawal deletes history, so
all panels are **survivor-biased** toward people who kept consent (stated on
each panel). Consent itself happens in Settings, so for most users the
pre-consent journey (onboarding, first plan) is unobservable unless decision
D-4 adds an onboarding-time prompt.

| # | Panel | Definition |
|---|---|---|
| P1 | Onboarding completion | Funnel `onboarding_started` → `onboarding_step_completed` (step1..4) → `onboarding_completed`, 24 h window, subjects whose `analytics_enabled` precedes `onboarding_started`. |
| P2 | Time to first value | Per subject: min(ts of `training_plan_created`, `workout_completed`, `coach_reply_completed`) − ts(`onboarding_completed`); median, P75, and % within 24 h / 7 d. Existing users anchor on `analytics_enabled` (separate series). |
| P3 | First plan creation & acceptance | % of P1 completers with `training_plan_created` within 7 d, split by `path`; acceptance rate = count(`training_plan_created` path=`ai_preview_accepted`) / count(`training_plan_proposed`) per week. |
| P4 | Workout completion | Weekly: subjects with ≥1 `workout_completed` / subjects with any activity; median completions per active subject per ISO week. |
| P5 | Coach adoption & authorized mutation success | % of subjects with ≥1 `coach_reply_completed` within 7 d of anchor; weekly Coach-active subjects; `coach_mutation_applied` count and per Coach-active subject (success rate N/A, see §3). |
| P6 | Weekly check-in adoption | Per ISO week: subjects with ≥1 `weekly_checkin_submitted` / subjects active ≥7 d. |
| P7 | D1/D7/D30 retention | Cohort = Istanbul date of first `analytics_enabled`. Exact-day: returned on day N iff ≥1 meaningful event (`training_plan_created`, `workout_completed`, `coach_reply_completed`, `weekly_checkin_submitted`) on cohort day + N; second series uses `app_opened`/`app_resumed`. Mature cohorts only. |
| P8 | Feature reliability | AI plan generation success = created(`ai_*`) / (created(`ai_*`) + `feature_failed`{feature=training_plan_generation}), weekly, plus retryable split. |
| P9 | Weekly founder review | P1–P8 snapshot with x/n, cohort sizes, consent caveat. Opt-in rate is NOT computable from vendor data (refusers emit nothing); if wanted, an aggregate first-party count from the consent table is a separate counsel item. |

## 5. Tests (synthetic)

Backend `tests/test_product_analytics_v2.py` (39 cases): OFF inertness (404 before
auth, zero SQL), consent independence/notice/idempotence/regrant rotation,
withdrawal purge + erasure job, ingress strictness (12 out-of-catalog cases),
envelope bounds, account fence (other account, stale grant, regrant), expiry,
canonical identity dedup, real writer (`commit_general_plan`), exact emitter
inventory (AST), no network imports, exact wire allowlist without account
identity, withdrawal-before-dispatch, #541 A6 regression, bounded retries,
erasure on withdrawal and account purge. Mutation-checked: dispatch fence,
account fence, flag gate. Mobile `test/core/analytics/first_party_analytics_test.dart` (19),
mutation-checked in-flight fence.

## 6. Remaining activation gates

See `PRIVACY_REVIEW_PACKET.md` §8. Nothing in this source authorizes
collection, vendor provisioning, migration, merge, deploy, ASC or TestFlight.
