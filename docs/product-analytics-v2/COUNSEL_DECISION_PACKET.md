# Product analytics v2: counsel decision packet (one document, decision-ready)

**Status:** for counsel and the controller. **Not legal advice, not an approval,
not publication.** Nothing here changes the active processing authority. The
source runs the **independent, default-denied opt-in** (model C) and keeps it
until written counsel disposition and a separate founder activation decision.

This packet consolidates Q-1…Q-8 from
[`LEGAL_BASIS_ASSESSMENT.md`](LEGAL_BASIS_ASSESSMENT.md) (sources, quotes,
legitimate-interest assessment) into one sheet. The drafts it refers to are
[`PRIVACY_POLICY_AMENDMENT_DRAFT.md`](PRIVACY_POLICY_AMENDMENT_DRAFT.md)
(EN/TR, variants C and L) and [`RETENTION.md`](RETENTION.md).

## 0. Founder positions already taken (2026-11-28)

| Item | Decision |
|---|---|
| Destination | PostHog EU, server-mediated (the app has no vendor SDK) |
| Preferred processing model | **L**, subject to written counsel approval |
| Fallback | **C** (current source) |
| Event catalog | 20 events incl. DELTA-1 approved **as product scope only**. This is not a lawful basis for any event. |
| Raw retention at the vendor | **≤ 90 days including deletion lag** |
| Onboarding | **no** additional standalone consent screen |
| Not allowed in any model | automatic enrollment into a consent; consent implied by Terms or Privacy Policy acceptance, silence, health consent or an AI grant |

## 1. Decisions requested (answer each; one line is enough)

| # | Question | Why it decides | Answer options |
|---|---|---|---|
| **Q-1** | Apple App Review 5.1.1(ii)/5.1.2: may usage data be collected without an affirmative user choice (a) for server-derived events, (b) for device-originated events? (Re-verify the current guideline wording; this draft read it only as a summary.) | If no, model L is unavailable on iOS regardless of KVKK. | no for both / yes for (a) only / yes for both |
| **Q-2** | KVKK Art. 6: which events are special-category (health) data? Proposed: Tier 2 in §2 (workout, check-in, Progress, Nutrition). Are `training_plan_created` and `coach_reply_completed` health data in a fitness app? | Art. 6(3) has no legitimate-interest condition: health events stay consent-only. | confirm Tier 2 / move named events to Tier 2 |
| **Q-3** | Art. 5(2)(f): is product improvement "zorunlu" (necessary) for AxisAI's legitimate interest, and does the balancing in the assessment §3 hold for Tier 1 server events? Does the KVKK Board's analytics/cookie guidance weigh against it? (not read: kvkk.gov.tr unreachable) | The basis of model L itself. | yes / yes with conditions / no |
| **Q-4** | Device-originated observations (app opened, screen viewed, onboarding steps): permissible without consent? Are EU/EEA users in first-public scope (GDPR + ePrivacy Art. 5(3))? | Splits Tier 1 into server-only vs. server + client. | server only / both / market-dependent |
| **Q-5** | Transfer (Art. 9): KVKK standard contract with PostHog (Art. 9(4)(c)), notification to the Authority within 5 business days of signing (Art. 9(5)); PostHog DPA and subprocessor list; onward transfers (group, support access); VERBİS entry update (Art. 16, not quoted here; verify). | Needed under **both** models before any transfer. | list of documents to sign/file |
| **Q-6** | Notice (Art. 10): placement and versioning. Under C: the Settings notice at opt-in + Privacy Policy. Under L: is Privacy Policy + a Settings-reachable notice sufficient without an extra onboarding step? | Determines the publishable variant. | variant C text / variant L text / edits |
| **Q-7** | Rights (Art. 11, 13): offer objection under L as a voluntary safeguard (KVKK has no general objection right; GDPR Art. 21 does, if Q-4 puts EU users in scope)? How to answer access/portability for pseudonymous events (AxisAI holds the mapping)? | Shapes the Settings control under L. | objection yes/no; access answer form |
| **Q-8** | Retention: raw ≤ 90 days incl. deletion lag (founder), aggregates ≤ 12 months with small-cell suppression, outbox ≤ 48 h, receipts ≤ 90 days after completion. Is the vendor's handling of deleted data in its **backups** acceptable, and the restore residual in RETENTION.md §4? | Policy text and G2 questions. | approve / shorten / conditions |

## 2. Event disposition if model L is approved (exact)

Tiers as in the assessment §4. "Value" rows split one event by its enum value.

**A. Model L eligible, server-authoritative (needs Q-1(a), Q-2, Q-3 = yes)**: 8

`axis_v2_onboarding_completed`, `axis_v2_training_plan_proposed`,
`axis_v2_training_plan_created` (Q-2), `axis_v2_coach_reply_completed` (Q-2),
`axis_v2_coach_mutation_applied{domain=training}`,
`axis_v2_coach_change_proposed{domain=training}`,
`axis_v2_coach_change_confirmed{domain=training}`, `axis_v2_feature_failed`.

**A′. Model L eligible only if Q-4 also allows device-originated events**: 8

`axis_v2_onboarding_started`, `axis_v2_onboarding_step_completed`,
`axis_v2_app_opened`, `axis_v2_app_resumed`,
`axis_v2_screen_viewed{today, plan, plan_create, coach, settings, onboarding}`,
`axis_v2_training_plan_requested`, `axis_v2_plan_viewed`,
`axis_v2_coach_message_sent`. If Q-4 = no, these stay consent-only (B).

**B. Separate affirmative consent required (existing Settings opt-in, unchanged)**

`axis_v2_workout_completed`, `axis_v2_weekly_checkin_submitted`,
`axis_v2_progress_viewed`,
`axis_v2_screen_viewed{workout, nutrition, nutrition_plan, progress}`,
`axis_v2_coach_mutation_applied{domain=nutrition}`,
`axis_v2_coach_change_proposed{domain=nutrition}`, plus
`axis_v2_analytics_enabled`, which records that grant and is the cohort
anchor for consenters.

**C. Excluded in every model** (catalog `excluded_by_design`; not negotiable
by basis)

* Signup start/completion. Nothing is collected before an account exists.
* Any content: Coach messages, meals and food logs, plan contents, onboarding
  answers, measurements, photos, free text, error text.
* Person/profile properties, `identify`, `alias`, groups. The sole exception
  is the fixed erasure marker for a **retired** pseudonym, used only to
  delete it.
* Device, OS, app, locale, timezone, network or screen metadata, and the
  user's IP address (server-mediated).
* Under L, any event for an account that objected. Under both models, any
  event outside the 20-event catalog without a new catalog review.

## 3. Exact requirements by model

| Requirement | Model C (current) | Model L (if approved) |
|---|---|---|
| Lawful basis | Art. 5(1) explicit consent, independent of every other consent | Art. 5(2)(f) for A (+A′); Art. 6(3)(a) explicit consent for B |
| Collection starts | after the user turns the Settings switch on | A/A′: after account creation, for accounts that have not objected; B: after opt-in |
| Notice (Art. 10) | controller identity, purpose, recipients (PostHog, EU), method **and** legal basis, rights. Shown at the Settings opt-in and in the Privacy Policy (variant C) | the same elements per category (A/A′ vs B). In the Privacy Policy (variant L) and reachable from Settings → Privacy & Data. No onboarding screen. The notice version is bound to the Privacy Policy version. |
| Withdrawal / objection | implemented: durable, account-scoped, immediate stop, vendor erasure | objection for A/A′ (Q-7) with the same mechanics, inverted default; withdrawal for B unchanged |
| Deletion (Art. 7, 11(e)) | vendor erasure pipeline (receipts, verification) | same |
| Access (Art. 11(a)–(c)) | answer from the AxisAI mapping: pseudonym exists, categories, recipient, transfer | same |
| Response time (Art. 13) | per KVKK Art. 13 (not quoted in this packet; verify) | same |
| Transfer (Art. 9) | Art. 5 condition + standard contract signed with PostHog + notification within 5 business days + DPA/subprocessors (G2) | same, with the LI condition for A/A′ |
| Apple App Privacy | Usage Data → Product Interaction; Diagnostics → Other Diagnostic Data (`feature_failed`); purpose Analytics; "linked to you" recommended conservatively (AxisAI holds the mapping); no tracking | the same answers; plus Q-1 outcome |
| Retention | RETENTION.md (≤ 90 days incl. lag; not yet verified, G5) | same |

## 4. Minimal implementation delta after counsel disposition

**If counsel rejects L, or answers Q-1 "no":** no source change. Model C stays.
What remains: publish variant C after approval, then G2 → G3 → G5 → G7.

**If counsel approves L (fully or partly):** one bounded change set, behind a
new server switch that defaults OFF, with founder approval of the authority
change at that time:

1. **Catalog.** A per-event (per-value where split) `basis` of `li` or
   `consent`, frozen by a test. The approved A/A′ scope becomes `li`.
2. **Backend authority.** The consent row gains an LI state:
   `li_notice_version` and `li_objected_at`. The emit gate becomes: a `consent`
   event needs a valid grant (unchanged); an `li` event needs a valid grant OR
   (LI switch ON and the notice is current and the account has not objected).
   The subject is minted at the first eligible event after account creation,
   never before.
3. **Objection.** Reuse the withdrawal path: same transaction, outbox delete,
   generation fence and vendor erasure job.
4. **Migration.** Additive, after `6e25ecb68c09`, with a lossy-downgrade guard.
5. **Mobile.** Settings → Privacy & Data gets two controls. "Product
   analytics" is on unless objected, and the switch records an objection with
   the existing durable withdrawal store. "Health-related usage analytics" is
   the existing opt-in, off by default. Client events follow Q-4. Nothing is
   added to onboarding.
6. **Notice.** The variant L text, versioned with the Privacy Policy.
7. **Dashboards.** Cohorts anchor on the first `onboarding_completed` for A
   panels, and B panels stay consenters-only (coverage table in DASHBOARDS.md).
8. **Tests.** The emit-gate matrix by basis × objection × grant; objection
   durability (mobile); a PG objection-vs-dispatch race (reuse the withdrawal
   race tests).

Out of the delta in every case: an onboarding screen, automatic enrollment
into a consent, or reading Terms acceptance, Privacy Policy acceptance,
silence, health consent or an AI grant as consent.

## 5. Open items for counsel that only the vendor can answer (G2)

* the PostHog DPA, the subprocessor list and EU data location
* the standard-contract counterparty
* whether deleted events persist in vendor **backups**, and for how long
* support and staff access paths
* breach notification terms
