# Product analytics v2 — privacy review packet (for counsel and the controller)

Prepared from source and synthetic evidence only. **Not legal advice and not
an approval.** Every item marked DECIDE is a counsel/controller decision; this
packet states facts about the design so it can be reviewed. No data has been
collected; no vendor account exists for this purpose.

## 1. Processing summary

| Item | Fact |
|---|---|
| Purpose | Product analytics: understand feature use (onboarding, plans, workouts, Coach, check-ins), retention and feature failures, to improve the app. Single purpose `product_analytics`. |
| Opt-in | Independent, optional, off by default, one switch in Settings → Privacy & data. Not bundled with terms, health, AI/Coach or any other consent; refusing changes nothing else. Grant is recorded against an exact notice version; a new notice requires a new grant. |
| Withdrawal | Same switch. Device stops synchronously before any network call; server deletes undispatched events, bumps the grant generation (fences anything in flight) and creates a durable vendor-erasure job for the pseudonym. |
| Data subjects | Adult account holders (V1 is adult-only, FDO-AGE18) who opted in. No pre-account (signup) collection. |
| Data categories | Event name, time, a closed set of coarse enum values (screen, onboarding milestone, plan path, Coach domain, failure retryability), a random per-grant pseudonym, a random or keyed event id. See README §3. |
| Not collected | Name, email, username, Cognito sub, account id, device/OS/app/locale/timezone/network/screen metadata, IP address (vendor sees AxisAI's server), advertising ids, health/body measurements, photos, meals or nutrition contents, Coach prompts/replies/topics, check-in answers, error text, free text of any kind. Enforced by catalog validation on ingress and an exact wire allowlist. |
| Pseudonymity | `distinct_id` is a random UUIDv4 minted at grant; a new one after every withdrawal. AxisAI's database maps account → current pseudonym (needed for withdrawal and erasure), so the data is **pseudonymous, not anonymous**. The vendor never receives the mapping. |
| Retention (PROPOSALS — DECIDE) | Reconciled on 2026-11-28 ([RETENTION.md](RETENTION.md)): raw vendor events ≤ 90 days, enforced by configurable pseudonym rotation + erasure (PostHog Free has a fixed 1-year retention and monthly cleanup). Anonymized aggregates ≤ 12 months. Outbox ≤ 48 h. Erasure receipts purged 90 days after verification (implemented, configurable). Consent row: account lifetime. Grant expiry: 365 days. |

## 2. Lawful basis and notice (DECIDE)

Founder direction (2026-11-28): no additional standalone onboarding consent
screen. A narrow legitimate-interest alternative is assessed in
[LEGAL_BASIS_ASSESSMENT.md](LEGAL_BASIS_ASSESSMENT.md): KVKK text verified,
event-level matrix, Tier 1/Tier 2, Apple 5.1.1(ii) risk, questions Q-1…Q-8.
The conditional EN/TR Privacy Policy text is
[PRIVACY_POLICY_AMENDMENT_DRAFT.md](PRIVACY_POLICY_AMENDMENT_DRAFT.md). **The
active model is unchanged (opt-in).**

* Proposed basis: explicit consent (opt-in), withdrawable at any time.
  Counsel to confirm for KVKK and, where applicable, GDPR.
* Notice: draft EN/TR copy in mobile `lib/l10n/app_{en,tr}.arb`
  (`analyticsConsentBody`), version `analytics-notice-v1-draft`. Must be
  reviewed, finalised and re-versioned; privacy policy needs a matching
  section (recipient, transfer, retention, rights).

## 3. Sensitive inference (DECIDE)

Events reveal that a person uses a fitness app's training, workout, Coach and
check-in features, and when. No measurement or content is included. Counsel
to decide whether usage events of a fitness/health-adjacent app are, or can
be combined into, special-category (health) data under KVKK Art. 6 / GDPR
Art. 9 — this affects basis, transfer and DPA terms. Design mitigations:
coarse enums only, no body/health values, no Coach topics, no nutrition
contents, personless (no profile aggregation at the vendor).

## 4. Recipients and subprocessors (DECIDE / VERIFY)

| Recipient | Role | Data |
|---|---|---|
| AxisAI backend (AWS, eu-central-1) | Controller infrastructure | consent rows, outbox, erasure jobs |
| PostHog (EU Cloud) | Processor (DPA required) | events as in §1 |
| PostHog subprocessors | Sub-processors | per PostHog's published list — **must be retrieved and reviewed at contracting time** (not reviewed in this packet) |

## 5. International transfer (DECIDE)

* Data is stored in PostHog's EU region. For Turkish users any transfer
  outside Türkiye (including to the EU) is a cross-border transfer under
  KVKK Art. 9; counsel to choose the mechanism (e.g. standard contract and
  its notification to the Authority within the statutory period) and confirm
  onward transfers by the vendor's corporate group / support access.
* EU hosting alone is not treated as transfer clearance.

## 6. Erasure and data-subject rights

| Trigger | Implemented in source | Gate |
|---|---|---|
| Withdrawal | outbox delete + generation fence + `product_analytics_erasure` job (no user link) | vendor deletion call not implemented (synthetic transport only) |
| Account deletion | job created inside the purge transaction before the mapping is deleted | same |
| Vendor deletion | `process_erasures_once`: `$set` marker (PostHog's documented personless workaround) → person lookup → `bulk_delete` (`delete_events`) → `deletion_status` verification; never-exposed pseudonyms settle with no vendor call | PostHog documents async weekend batches and no SLA. **VERIFY at G5** that personless deletion works end to end. AxisAI overdue alarm proposal: 30 days |
| Rotation | the retired pseudonym is erased like a withdrawal | retention bound |
| Access/portability | not implemented | DECIDE whether analytics events are in scope of access requests (AxisAI holds the mapping) |

## 7. App Store privacy answers and SDK manifests (DECIDE)

Proposed answers for a binary with v2 enabled (server-mediated collection
still counts as collection):

| Apple data type | Collected | Purpose | Linked to user | Tracking |
|---|---|---|---|---|
| Usage Data → Product Interaction | Yes | Analytics | **Yes** (AxisAI holds account→pseudonym mapping) | No |
| Diagnostics → Other Diagnostic Data | Yes, if `feature_failed` is kept | Analytics / App Functionality | Yes | No |

SDK privacy manifests: `posthog-ios` 3.98.2 declares Product Interaction +
Other Usage Data as **not linked**, plus required-reason APIs UserDefaults
`CA92.1`, SystemBootTime `35F9.1`, FileTimestamp `C617.1`; `posthog_flutter`
5.61.1 declares the same data types. Do not copy the vendor's "not linked"
answer. **D-2 approved and applied (2026-11-28, mobile #232):** `posthog_flutter`
and both vendor manifests are removed from the resolved dependency graph (no
binary was built; confirm the Xcode privacy report at the G7 build). The
answers above come from the server-mediated collection. No ATT prompt
(no tracking). Build 101119 under review is unaffected (v2 not in it).

## 8. Activation gates (all OPEN)

| Gate | Owner | What closes it |
|---|---|---|
| G1 Legal | counsel/controller | §2–§6 decisions; final notice EN/TR + privacy policy; transfer mechanism executed/notified |
| G2 Contract | founder | PostHog DPA (+ transfer clauses) executed; subprocessors reviewed. Exact checklist: [`ACTIVATION_HANDOFF.md`](ACTIVATION_HANDOFF.md) §G2 |
| G3 Vendor provisioning | founder (separate approval) | Exact checklist: [`ACTIVATION_HANDOFF.md`](ACTIVATION_HANDOFF.md) §G3. EU project; autocapture, session replay, heatmaps, surveys, feature flags, web analytics OFF; GeoIP off; client-IP discard; person profiles off; retention set; token in Parameter Store (never in source/logs) |
| G4 Implementation | analytics owner | **source done 2026-11-28**: HTTPS transport, erasure client + receipts, scheduling, purge, rotation, durable device withdrawal. Left: the processing-model change only if counsel/founder choose model L. D-4 is resolved as "no standalone screen" (founder) |
| G5 Qualification | analytics owner | Exact checklist: [`ACTIVATION_HANDOFF.md`](ACTIVATION_HANDOFF.md) §G5. **PG concurrency done** (20 cases incl. the B-3 lock cap and its bounded re-send, local PG16 + hosted CI). Left: an authorized synthetic PostHog project E2E (stored properties = allowlist, no IP, personless deletion works, path/shape facts in TRANSPORT_AND_ERASURE.md §2, HogQL panels run); composed device QA (reuse W1/W2/W3 evidence; no new campaign) |
| G6 Composition | integration owner | **source composed 2026-11-28** (final isolated integration #573): analytics `6e25ecb68c09` keeps `down_revision = e6a4b91d702c`, and the retention revision `f14209ba73c6` was re-pointed onto it. The accepted lineage is `e6a4b91d702c → 6e25ecb68c09 → f14209ba73c6`, with the single Alembic head `f14209ba73c6` and exact-head CI green. Left: re-pin the head if any later authorized migration is added |
| G7 Release | founder | App Store answers updated; new binary; backend flag + mobile define enabled only after G1–G6; explicit activation GO |
