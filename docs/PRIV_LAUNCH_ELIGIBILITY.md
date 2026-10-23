# PRIV-LAUNCH — five-market eligibility boundary

Source implementation for TR/US/GB/CA/AU. **Not activated or launch-qualified.** Target content rating remains 13+; this is not an 18+ policy or proof of legal consent. Existing D1/AI and StoreKit lanes retain their work. HOLD #127/#460 are not reused.

## Contract

`ACCOUNT_ELIGIBILITY_ENABLED` is registered, strict, default OFF. The paired mobile client does not fall back to unguarded signup if the screen is unavailable. A qualified 13+ release must enable and verify the boundary after its prerequisites; OFF preserves legacy compatibility and is not an acceptable privacy workaround.

`POST /api/v1/auth/eligibility` and web `POST /eligibility` accept exactly `{age: positive integer, country: ISO code}`. No default age/country, DOB, selfie, ID, address, guardian assertion or contact data is requested. Replies are `eligibility: {status,age_band,country,reason,capabilities,registration_token}`. `screen_passed` means a **self-declaration screen** passed, not verified age, lawful health processing, guardianship or AI sharing.

| Declaration | Result now |
|---|---|
| Under 13 or outside the five countries | Ineligible, no registration ticket |
| TR 13–17 | Qualification required pending social-provider applicability and teen authority |
| CA 13 | Qualification required pending provincial/guardian qualification; country alone cannot exclude Québec |
| AU 13–14 | Qualification required pending capacity/guardian qualification |
| Remaining 13–17 in scope | Screen passed; body-photo and automated-weight-target capabilities false |
| Adults in scope | Screen passed; adult capabilities are still independent of health/AI permission |

All cohorts have `social:false` in private v1 eligibility mode. Merely denying teen senders would leave adult search/messages to minors possible. Thus the existing web social routes and new referral association are closed in this mode for everybody; no native social UI exists. This helps isolate private fitness, but does not establish the legal service classification in Türkiye or Australia.

Tickets are purpose-signed and expire after 600 seconds (credential lifetime, not a retention period). Canonical web/native `register_account` validates before provider/local account work; invalid/expired/missing tickets return `AUTH_ELIGIBILITY_REQUIRED` 422. A ticket is not one-time identity proof: a user can re-declare, and self-declaration alone is insufficient where stronger age assurance is required. No client-approved/guardian/capability flags are accepted.

Registration stores the existing `User.age` plus a signed, subject-bound `_account_eligibility_v1` record inside existing `User.user_metadata`: version, age band, country, declaration source and check time. No new schema. Unknown/corrupt/copied/contradictory records restrict capabilities. Metadata loss cannot use a known minor's existing age as proof of adulthood. A profile edit never promotes cohort. Raw signed-record/ticket strings are not logged or exposed in account projection; the existing account purge deletes the user row and this metadata with it. This does not claim deletion from backups or providers.

The signature uses the existing application secret with a separate purpose salt; no new secret is requested. Rotating that key invalidates prior signed records and restricts access. Qualification must cover recovery/review for rotation, corrupted records and genuine age/country changes; it must not recover by silently trusting an adult profile value.

Authenticated `GET/POST /api/v1/account/eligibility` and web `/account/eligibility` provide a one-time existing-account declaration using the same ticket. Repeating the same country/band is idempotent; changing an established cohort/country, known-underage promotion or replacing a corrupt prior record requires review. No automatic birthday promotion or invented guardian approval is implemented. These endpoints preserve access to account deletion, authentication/recovery and help.

`account/me` includes `user.eligibility` only while mode is enabled. Clients must treat missing/unknown state as restricted; they must not derive permissions from editable profile age. The current signup companion consumes the pre-contact contract; signed-in review UX and neutral teen onboarding must still be composed with the existing settings/profile owners before enabling this flag.

## Backend protection

Web/Bearer authentication call the same policy before protected view processing. In eligibility mode: social routes are closed; teen/unknown Pump Check create/comparison and legacy photo completion are refused; mixed workout completion rejects multipart/ambiguous photo transport while preserving its affirmative JSON photo-free path. The existing no-AI boundary still applies. Protected multipart is rejected before application body parsing/storage/queue/provider work. JSON photo classification necessarily parses the JSON transport; it never authorises image processing. No rejection silently strips a supplied photo into successful photo-free completion.

Current profile goals are only weight loss/muscle gain. Teen/unknown `/setup`, profile edits and weekly weight updates are therefore refused rather than running adult maths. Canonical profile and weekly-weight services independently refuse before calculating/writing. **This is a safe boundary, not completed teen onboarding.** A neutral fitness profile/read model without weight-change/physique prescriptions must be composed after the actual first-party health authority is qualified. Historical health/target records are not erased by this feature gate, and all historical projections are not claimed to be redesigned here.

Queued AI/result/recovery operations must use the account capability authority alongside the D1 permission generation at dispatch and publication; this PR does not duplicate or complete D1's operation fencing. No current registry policy is added. No backend/client age declaration becomes a health, AI or purchase grant.

## Critical remaining work

1. Complete the existing **D1/Coach grant/withdrawal and operation-fencing lane**, including actual minor recipient/model purpose text, age-appropriate output protections and qualified health-processing actor/grounds. Neutral teen onboarding must precede release; a 403 dead end is not the intended general fitness experience.
2. Complete the existing **StoreKit purchase/restore/verify lane**, testing pending/Ask to Buy, delayed updates, restore/account ownership and revocation. Payment approval is not age/guardian/health/AI proof.
3. Integrate proportionate age assurance, required guardian/capacity flows and signed-in review/birthday/country-change handling. Obtain the focused five-market legal determination, including Québec, AU social scope and TR social-provider applicability. No user ID documents should be collected by default. Selected US regional obligations require the native age-assurance integration/fallback, not only a country dropdown. [Apple regional guidance](https://developer.apple.com/news/?id=f5zj08ey).
4. Qualify actual archived SDK/manifests, health/photo backups and controlled-account deletion/incomplete recovery, actual provider/log/mail retention and the EN/TR/KVKK notices. Controller/contact and any missing retention criteria remain existing publication inputs; no new vote on markets/rating or AI/Premium intent is requested.

Country facts informing temporary holds, not automatic legal bases: [Canada meaningful consent](https://www.priv.gc.ca/en/privacy-topics/privacy-for-businesses/appropriate-handling-of-personal-information/collecting-personal-information-and-consent/consent/gl_omc_201805/), [Québec private-sector Act](https://www.legisquebec.gouv.qc.ca/fr/document/lc/p-39.1), [OAIC capacity](https://www.oaic.gov.au/privacy/australian-privacy-principles/australian-privacy-principles-guidelines/chapter-b-key-concepts), [UK health conditions](https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/childrens-information/childrens-code-guidance-and-resources/age-appropriate-design-a-code-of-practice-for-online-services/annex-c-lawful-basis-for-processing/), [Australian social scope](https://www.esafety.gov.au/about-us/industry-regulation/social-media-age-restrictions), [TR official republication](https://edremit65.meb.gov.tr/www/sosyal-ag-saglayici-tarafindan-cocuklara-ozgu-ayristirilmis-hizmet-sunulmasina-ve-yas-dogrulamaya-iliskin-yonetmelik/icerik/2602). Government publication/applicability and actual contracts require qualification; no compliance declaration follows.

No concrete evidence establishes that the overall 13+ target is impossible by 15–20 November. Unresolved service classification can prevent specific younger cohorts from launching in TR/AU; missing guardian/age-assurance/health authority and neutral onboarding block their affected teen cohorts. Do not advertise universal 13+ eligibility across all five countries until these are resolved. Keep the source changes isolated; no merge, deployment, activation or publication is authorised here.

## Local validation and review

On base `875364a118e0a0ea8bd61be92d0bde4834b53294`: focused registration/profile/weekly/native/flag regression **573 passed**; auth/deletion/eligibility regression **164 passed**; boundary/route suite **56 passed**; hermetic EN/TR pre-contact browser flow **4 passed**. Suites overlap and are not a unique total. Browser cases cover rejected underage input, stage invalidation, expired tickets without automatic contact resubmission, and stale preview responses. No real customer records, AWS/provider calls or purchases were used. An additional enabled-mode real account-erasure check passed for unknown, teen and adult cohorts (48 boundary tests total); account rows and their eligibility metadata were removed with mocked Cognito/S3 only. Hosted exact-head CI remains separate from local results.

The paired mobile owner independently reviews this diff; root independently reviews the mobile change. Concrete findings and closure are recorded in the isolated PRs. No extra reviewer committee or merge authority is created.
