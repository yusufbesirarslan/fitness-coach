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
| Nutrition | Food descriptions/amounts, nutrients, diary/drafts, plans, water and supplement records, related history | Native `/nutrition/*` (including discovery/log/day/plan/hydration/history/supplements) and authenticated web counterparts; current FatSecret decision unchanged |
| Notes and selected images | Health-bearing Coach input/history and images, Pump Check image/results, QR/menu input that may include health details | Health boundary before body/image handling; five existing D1 permission gates still apply independently before provider processing |
| Ordinary account controls | Identity/contact/authentication, preferred language, eligibility declarations, subscription controls, privacy/permission management, account deletion | Remain accessible. `/account/me` masks goal/profile-completion health projection unless it can hold a current health authority scope. This table does not declare all age/gender data intrinsically health data outside their health context |

All authenticated product endpoints default to health-protected except the explicit account-control allowlist in `app/health_boundary.py`. Authentication reuses real Bearer/web session ownership. The boundary precedes eligibility body parsing, maintenance hooks, views and D1 admission. Unknown future product routes fail closed. Routine auth may load an existing User row; this boundary prevents health-purpose use/disclosure and request-body collection, not incidental database row loading by identity resolution. Anonymous authentication/eligibility inputs remain under their existing controls.

## Exact text and evidence

`app/services/health_disclosure.py` contains the equivalent EN/TR candidate bytes, SHA-256 digests and version `health-fitness-v1-review`. The mobile UI shows the server's exact localized text, a separate affirmative action, refusal, withdrawal, refresh and the existing Privacy Notice link. No generic onboarding acknowledgment grants permission. The text covers selected first-party tracking, calculations, plans and history, stopping health features, available account controls, prospective use of existing records and no automatic deletion. AI-provider permissions and international transfers are explicitly separate.

GET/POST `/api/v1/health-consent` (and CSRF-protected web `/api/health-consent`) share one authority. Grants require the published exact purpose/version/locale/digest and the existing eligibility capability; the authenticated user is authoritative. No grant is accepted while the candidate is unpublished. Refusal/withdrawal remain possible with a retired/unpublished text.

`health_consent_state` + append-only decision events record user, purpose, version, locale, exact-text digest, server UTC time, decision/withdrawal state, generation, request ID and command digest. They store no health payload, IP or device fingerprint. Compare-and-swap and idempotency prevent replayed grants from restoring revoked permission. Existing D1 state/generations/registries are untouched.

PostgreSQL shared transaction advisory locks hold health authority through processing and WSGI consumption, independently of ORM commits. Withdrawal uses an exclusive lock and returns confirmation only after admitted server work exits. Failure/timeout stays closed and requires retry; neither device/network delivery nor provider recall is promised. Existing bounded stream/disconnect behavior retains the same lifetime lock. Joined AI workers carry the original health generation; renewed consent cannot revive an old worker. Legacy summary jobs without an explicit health-generation snapshot stop before reading content; Memory remains outside the five launch purposes.

## Refusal, withdrawal and existing accounts

Mobile checks server evidence on account/epoch changes and foreground resume; there is no persisted local allow flag. It blocks health request dispatch and stale responses, drops in-memory health forms/controllers and unbinds recovery before further use. Existing durable recovery records are retained, not replayed while closed. A separate device-only, account-partitioned secure marker persists unconfirmed stop requests across logout/restart; unreadable storage also blocks. It stores stop intent only. Withdrawal is confirmed by the server; a failed request must be retried. Other devices are checked at their next protected request/refresh, not remotely erased.

The expand-only migration creates empty consent tables. No existing field, account, D1 permission or historical collection becomes a grant. Existing health records stay paused until a new prospective grant; the new grant never rewrites collection history. Refusal/withdrawal do not delete records. Passive retention and any historical collection still require an independently supported legal disposition. Existing account deletion purges the new owner evidence alongside its current data inventory; no new retention schedule or historical deletion is introduced. Backup/provider-copy limits remain the existing F02 matter.

## Minimum decisions before release/publication

| Decision | Type / owner | Exact approval needed |
|---|---|---|
| First-party purpose and UX | FOUNDER_DECISION_REQUIRED / founder | Accept this bounded tracking/calculation/plans/history purpose and the disclosed stop behavior, including unavailable health features on refusal; approve equivalent candidate EN/TR wording for legal review |
| Article 6 condition | EXTERNAL_DEPENDENCY / independent legal reviewer + controller | Substantiate Article 6(3)(a) applicability, specificity/freedom/information and withdrawal effect for these fields/purposes; assess existing eligibility/capacity restrictions without treating 13+ intent as permission |
| Existing records and evidence | EXTERNAL_DEPENDENCY / controller + legal reviewer | Determine lawful handling/retention while consent is absent/withdrawn, prospective reuse and consent-evidence disposition under the existing F02 criteria; no retroactive cure or automatic purge |
| Notice clauses | EXTERNAL_DEPENDENCY / existing F01/F02 and D1 owners | Resolve only first-party health condition, collection-time action, refusal/withdrawal, and prospective-existing-data paragraphs in existing paired privacy texts. D1's five purpose/recipient and Article 9 clauses retain separate unresolved approvals; this first-party consent supplies neither |

After those determinations, the existing owner can propose a versioned publication change and coordinate backend expand migration/mobile release for separate authorization. This task does not approve source policy publication, fill D1 registries, change model ordering, transfers, AWS, retention or release gates. No merge, deployment or production records changed.

## Validation receipt

- Health HTTP/evidence/refusal/withdrawal/API bypass, auth/stream lifecycle and bounded checkpoint regressions: **238 passed**, with unraisable lifecycle errors treated as failures.
- Separate D1/allowance/jobs/eligibility regression selection: **262 passed**.
- Profile/D1/migration/deletion/mobile-route selection: **208 passed**.
- Expanded disposable PostgreSQL consent/D1/worker/migration selection: **244 passed, 2 existing skips** (two existing PostgreSQL-18-only catalog cases are skipped by the local PostgreSQL 16 harness). Final health/D1 PostgreSQL rerun: **55 passed**, including response isolation and worker context/generation checks.
- Paired mobile: [draft #174](https://github.com/axisaiapp/axisai-mobile/pull/174), 13 new flow tests + 105 existing regressions; analysis clean.

Selections overlap; these counts are separate runs, not additive unique coverage. SQLite synthetic domain fixtures explicitly construct prospective health evidence; PostgreSQL tests use real advisory locks. Synthetic disclosures, identities, provider stubs and local databases are not production approval or runtime evidence. Full hosted CI is reported separately at its actual status.
