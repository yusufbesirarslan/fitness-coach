# F01 first-party health consent — founder review candidate

TASK=AXISAI-F01-FIRST-PARTY-HEALTH-CONSENT-01

Proposed condition: **ARTICLE_6_3_A_REQUIRES_SUBSTANTIATION**. The founder selected evaluation of purpose-specific explicit consent. This implementation does not establish legal sufficiency. The candidate remains unpublished (`health_disclosure.APPROVED_VERSION=None`); no client/env flag can approve it. Existing F01/F02 paired privacy candidates (landing #76) and ten D1 drafts remain the notice/AI review sources. No provider or transfer approval is inferred.

## Collection boundary reused from F01

Classification below is an engineering handling boundary for review. Ordinary account attributes remain separate; mixed health records are conservatively protected in full. It does not assign a new Article 5 condition to ordinary data.

| Existing collection/use | Data and source | Enforced boundary |
|---|---|---|
| Onboarding and profile editing | Weight, height, target weight, age, gender, fitness/activity level and goals; `account_profile.OnboardingProfile`, `User`/`UserSession` | Before native `/api/v1/account/profile` GET/PUT body handling and web `/setup`, `/edit-profile`; mobile health form not mounted before confirmed permission |
| Injury/medical constraints and training | `TrainingPreferences.injuries`; health-bearing `User.user_metadata` (including injuries), preferences, plan/history, workout/activity records and free text | Native `/training/preferences`, `/training/plans*`, workouts/history, Coach and web equivalents; no new Memory purpose or intake is enabled |
| Recovery and weekly check-in | `weight_kg`, training intensity, fatigue, sleep quality, nutrition adherence, progressive overload; history and derived summaries | Native `/progress/check-ins`, progress/history and web tracking/check-in/weight paths; recovery owners unbound while permission is closed |
| Nutrition | Food descriptions/amounts, nutrients, diary/drafts, retained daily nutrition aggregates, plans, water and supplement records, related history | Native `/nutrition/*` (including discovery/log/day/plan/hydration/history/supplements) and authenticated web counterparts; current FatSecret decision unchanged |
| Notes and selected images | Health-bearing Coach input/history and images, Pump Check image/results, QR/menu input that may include health details | Health boundary before body/image handling; five existing D1 permission gates still apply independently before provider processing |
| Ordinary account controls | Identity/contact/authentication, preferred language, eligibility declarations, subscription controls, privacy/permission management, account deletion | Remain accessible. `/account/me` masks goal/profile-completion health projection unless it can hold a current health authority scope. This table does not declare all age/gender data intrinsically health data outside their health context |

All authenticated product endpoints default to health-protected except the explicit account-control allowlist in `app/health_boundary.py`. Authentication reuses real Bearer/web session ownership. The boundary precedes eligibility body parsing, maintenance hooks, views and D1 admission. Unknown future product routes fail closed. Routine auth may load an existing User row; this boundary prevents health-purpose use/disclosure and request-body collection, not incidental database row loading by identity resolution. Anonymous authentication/eligibility inputs remain under their existing controls.

## Exact text and evidence

`app/services/health_disclosure.py` contains the equivalent EN/TR candidate bytes, SHA-256 digests and version `health-fitness-v2-review`. The mobile UI shows the server's exact localized text, a separate affirmative action, refusal, withdrawal, refresh and the existing Privacy Notice link. No generic onboarding acknowledgment grants permission. The text covers selected first-party tracking, calculations, plans and history, stopping health features, available account controls, prospective use of existing records and erasure within 30 days after withdrawal (v2, A1 = O2-B). AI-provider permissions and international transfers are explicitly separate.

GET/POST `/api/v1/health-consent` (and CSRF-protected web `/api/health-consent`) share one authority. Grants require the published exact purpose/version/locale/digest and the existing eligibility capability; the authenticated user is authoritative. No grant is accepted while the candidate is unpublished. Refusal/withdrawal remain possible with a retired/unpublished text.

`health_consent_state` + append-only decision events record user, purpose, version, locale, exact-text digest, server UTC time, decision/withdrawal state, generation, request ID and command digest. They store no health payload, IP or device fingerprint. Compare-and-swap and idempotency prevent replayed grants from restoring revoked permission. Existing D1 state/generations/registries are untouched.

PostgreSQL shared transaction advisory locks hold health authority through processing and WSGI consumption, independently of ORM commits. Withdrawal uses an exclusive lock and returns confirmation only after admitted server work exits. Failure/timeout stays closed and requires retry; neither device/network delivery nor provider recall is promised. Existing bounded stream/disconnect behavior retains the same lifetime lock. Joined AI workers carry the original health generation; renewed consent cannot revive an old worker. Legacy summary jobs without an explicit health-generation snapshot stop before reading content; Memory remains outside the five launch purposes.

## Refusal, withdrawal and existing accounts

Mobile checks server evidence on account/epoch changes and foreground resume; there is no persisted local allow flag. It blocks health request dispatch and stale responses, drops in-memory health forms/controllers and unbinds recovery before further use. Existing durable recovery records are retained, not replayed while closed. A separate device-only, account-partitioned secure marker persists unconfirmed stop requests across logout/restart; unreadable storage also blocks. It stores stop intent only. Withdrawal is confirmed by the server; a failed request must be retried. Other devices are checked at their next protected request/refresh, not remotely erased.

The expand-only migration creates empty consent tables. No existing field, account, D1 permission or historical collection becomes a grant. Existing health records stay paused until a new prospective grant; the new grant never rewrites collection history. Withdrawal after a recorded grant (and a decline after a valid grant) erases health records; see the next section. A withdraw or decline with no recorded grant erases nothing, and records collected before F01 for accounts that never grant still need a founder disposition (legacy population, not covered by A1). Existing account deletion purges the new owner evidence alongside its current data inventory; no new retention schedule or historical deletion is introduced. Backup/provider-copy limits remain the existing F02 matter.

## Withdrawal erasure (A1 = O2-B, founder decision 2026-11-09)

Source: founder disposition AXISAI-F01-A1-O2B-FOUNDER-DISPOSITION-01 and the O2 verification memo (`F01_O2_POST_WITHDRAWAL_RETENTION_VERIFICATION.md`). The export sentence from the memo's candidate wording is not used: the app has no export feature, and none is built here.

- **Stop.** Withdrawal blocks every health route and AI endpoint at once (the existing boundary; nothing new).
- **Erase.** A withdrawal whose current record is a grant (any text version), or a decline after a valid grant of the current text, records a `health_erasure` row; with no recorded grant nothing is erased. The same request then runs `health_erasure.run`, which releases Pump Check images and meal photos first, then changes every health row in one transaction under the owner row lock. The independent exclusive health authority serializes consent decisions, retries and controller holds across storage I/O. Failures stay pending and are retried hourly by the RQ worker's timer thread (`worker._health_erasure_tick` enqueues `tasks.retry_health_erasures`; enqueue only, never inline, so retry needs no user traffic), by daily maintenance and by `flask health-erasure-run` (all pending owners, in bounded batches so holds/failures cannot starve later owners); a pending erasure older than 30 days logs `event=overdue`, unless an active legal hold keeps it open (`hold_review_due` reports that hold instead).
- **Scope (conservative).** Body fields on `User` (`weight`, `height`, `target_weight`, `goal_type`, `goal`, `fitness_level`, `current_activity`; reset `profile_complete`), Coach memory keys in `user_metadata`, `UserSession`, check-ins and weight logs, daily activity and wearable logs, training and nutrition plans and their journals, workout logs, sessions, completion days, exercise notes and activity entries (workouts are treated as health data until a confirmed classification says otherwise), meals, water, supplements, custom meals, Coach conversations and pending actions, and Pump Checks with comparisons and the feed items that repost them. Generation ledgers keep their idempotency and allowance rows; only the plan content they carry is removed.
- **Kept.** Identity and sign-in, language, eligibility record, referral, subscription and AI allowance ledgers, age, gender, avatar, permission evidence, badges and wearable connections.
- **No restore.** A new grant is refused with 409 `HEALTH_ERASURE_PENDING` while any erasure for the owner is unfinished, so a new grant always starts from no health history.
- **Legal hold.** `flask health-legal-hold-add --user-id --matter --review-date --category …` (controller only; named categories, a matter reference, a future review date). Held categories are skipped and the erasure stays pending, so the owner cannot grant again and the held rows stay behind the boundary. `health-legal-hold-release` ends the hold and completes the erasure. Holds past their review date are logged, never released automatically.
- **Erasure record.** `health_erasure` holds ids, times and row counts only, has no account foreign key (it outlives the account) and is deleted three years after completion.
- **Status.** `GET/POST /api/v1/health-consent` adds `erasure: {state, requested_at, due_by}` or `null` (additive).

## Before F01 activation (founder disposition 2026-11-09)

Source only. `APPROVED_VERSION` stays `None`; D1 and the service cohort stay inactive. Before anyone sets `APPROVED_VERSION`:

1. **Retry without health traffic.** Implemented: the worker timer above reuses the timer thread already qualified for the #499 FatSecret 24h retention tick. The remaining precondition is operational: the RQ worker must run in the target environment and `event=overdue` must be monitored.
2. **Legal hold necessity.** The CLI accepts only explicitly named categories, a matter reference and a future review date; there is no "all categories" shortcut. Procedure: the controller names only the categories that the identified legal matter needs, records why each is needed against the matter reference, and releases the hold at or before review. A hold is never placed for a matter that has not been identified.
3. **Pre-F01 historical records.** Fail-closed is preserved. No historical row becomes a grant, and health routes stay closed until a new prospective grant. No automatic historical deletion runs: a withdraw or decline with no recorded grant erases nothing. Outstanding minimum disposition (founder/controller, separate from A1): the lawful handling and retention end of health records collected before F01 for accounts that never grant. Until it is decided those records stay paused, and only account deletion removes them.
4. **Wording correspondence.** Backend F01 v2 (`health_disclosure.py`, `health-fitness-v2-review`), the mobile withdrawal dialog (#177 `healthConsentWithdrawConfirmBody`) and landing #78 Privacy §§3, 4, 10 state the same facts: health features stop at once; health records are deleted within 30 days, except a record needed for a specific legal claim or request, which is kept only for that purpose; nothing is restored on a new grant; non-health account data stays. None promises an export.

## Minimum decisions before release/publication

A1 withdrawal disposition is now O2-B as selected by the founder. The previous retain-on-withdrawal question is superseded by the section above. Publication, Article 6 substantiation and activation still require separate approval.

| Decision | Type / owner | Exact approval needed |
|---|---|---|
| First-party purpose and UX | FOUNDER_DECISION_REQUIRED / founder | Accept this bounded tracking/calculation/plans/history purpose and the disclosed stop behavior, including unavailable health features on refusal; approve equivalent candidate EN/TR wording for legal review |
| Article 6 condition | EXTERNAL_DEPENDENCY / independent legal reviewer + controller | Substantiate Article 6(3)(a) applicability, specificity/freedom/information and withdrawal effect for these fields/purposes; assess existing eligibility/capacity restrictions without treating 13+ intent as permission |
| Existing records and evidence | EXTERNAL_DEPENDENCY / controller + legal reviewer | Determine lawful handling/retention while consent is absent/withdrawn, prospective reuse and consent-evidence disposition under the existing F02 criteria; no retroactive cure; O2-B withdrawal erasure as specified above |
| Notice clauses | EXTERNAL_DEPENDENCY / existing F01/F02 and D1 owners | Resolve only first-party health condition, collection-time action, refusal/withdrawal, and prospective-existing-data paragraphs in existing paired privacy texts. D1's five purpose/recipient and Article 9 clauses retain separate unresolved approvals; this first-party consent supplies neither |

After those determinations, the existing owner can propose a versioned publication change and coordinate backend expand migration/mobile release for separate authorization. This task does not approve source policy publication, fill D1 registries, change model ordering, transfers, AWS or release gates. No merge, deployment or production records changed.

## Validation receipt

- Health HTTP/evidence/refusal/withdrawal/API bypass, auth/stream lifecycle and bounded checkpoint regressions: **238 passed**, with unraisable lifecycle errors treated as failures.
- Separate D1/allowance/jobs/eligibility regression selection: **262 passed**.
- Profile/D1/migration/deletion/mobile-route selection: **208 passed**.
- Expanded disposable PostgreSQL consent/D1/worker/migration selection: **244 passed, 2 existing skips** (two existing PostgreSQL-18-only catalog cases are skipped by the local PostgreSQL 16 harness). Final health/D1 PostgreSQL rerun: **55 passed**, including response isolation and worker context/generation checks.
- Paired mobile: [draft #174](https://github.com/axisaiapp/axisai-mobile/pull/174), 13 new flow tests + 105 existing regressions; analysis clean.

Selections overlap; these counts are separate runs, not additive unique coverage. SQLite synthetic domain fixtures explicitly construct prospective health evidence; PostgreSQL tests use real advisory locks. Synthetic disclosures, identities, provider stubs and local databases are not production approval or runtime evidence. Full hosted CI is reported separately at its actual status.

The wider local smoke stopped at 2,341 passing checks with three changed-boundary assertions and seven sandbox-blocked Chromium fixtures; it is not a full-suite pass. After preserving existing response-close callback timing and updating health-cache/new-account expectations, the affected transport selection passed **110 checks** with unraisable errors fatal, and all **7 browser checks** passed with local Chromium permissions. No test gate was weakened or skipped for the new health implementation.


## Exact-head CI follow-up

The response boundary adds `no-store` without erasing existing `private` cache
contracts. Explicit scope ownership and a context-bound finalizer retain body
and authority until response close, including cyclic GC, disconnect/error and
closed-iterator cases. Shared processing locks still order withdrawal after
admitted processing. Dark endpoint 404s and ordinary health/referral controls
retain their contracts. Subscription identity binding on mobile depends on the
existing eligibility restriction, independently of the health permission.

Domain query guards explicitly assert and separate exactly one fixed health
state read from their previous domain budgets. Positive domain fixtures supply
prospective synthetic evidence; no historical account is grandfathered by
application code. The additive migration now declares frozen metadata rather
than importing mutable runtime models; the existing expand safety gate is
unchanged. No operational migration was executed.


The post-RC integration uses a new additive revision `f01e2a5e0002` after
`f01c0a1e0001`; merged migration bytes stay unchanged. PostgreSQL completion
claim retention has one reviewed exception: the same owner's pending erasure,
stopped permission and no active workout hold. Binding deletion remains an FK
cascade; every other completion fence remains exact. Old images that verify
only the earlier retention function reject the new schema, so rollback to such
an image requires a separately reviewed forward-recovery plan.

Only canonical account deletion, after proving its storage inventory released,
may close an O2-B obligation when purging the account. An unproven controller
row purge leaves the obligation pending for investigation; it never becomes a
false storage-erasure success. Expired hold review dates never release a hold.
