# Product analytics v2: processing-basis assessment (DRAFT for counsel)

**Status:** draft for counsel and the controller. **Not legal advice, not an
approval, and not a change to the active model.** The source still runs the
**independent, default-denied opt-in** (`consent.py`, Settings → Privacy &
data). Nothing here authorizes collection, automatic enrollment, or reading
Terms acceptance, Privacy Policy acknowledgement, silence, health consent or an
AI grant as analytics authorization.

Founder direction under assessment (2026-11-28): minimize onboarding friction;
no additional standalone analytics consent screen; prefer transparent Privacy
Policy disclosure plus a valid basis that needs no extra onboarding step, if
counsel supports it; keep the controls in Settings → Privacy & data; PostHog EU
stays the destination.

## 1. Sources verified for this draft

| Source | How verified | Use here |
|---|---|---|
| KVKK No. 6698, consolidated text incl. Law 7499 (2 Mar 2024) | official PDF `mevzuat.gov.tr/mevzuatmetin/1.5.6698.pdf` (sha256 `1ad27253…f2b5`), text extracted locally | Art. 4, 5, 6, 9, 10, 11 quoted below |
| Apple, App privacy details | `developer.apple.com/app-store/app-privacy-details/`, read in full | "collect", optional-disclosure criteria, Analytics purpose |
| Apple, App Review Guidelines 5.1.1/5.1.2 | page truncated in the fetch; read as an extracted summary only | **RE-VERIFY the exact wording** before relying on it |
| KVKK Board cookie guidance (2022) | **not read** (kvkk.gov.tr TLS failure) | flagged as an open counsel question only |
| PostHog privacy/data-storage, capture, API docs | `posthog.com/docs/...`, read | transfer, deletion, retention facts |

Verified KVKK text (official Turkish):

* **Art. 5(1):** "Kişisel veriler ilgili kişinin açık rızası olmaksızın işlenemez."
  **Art. 5(2)(f):** without explicit consent where "İlgili kişinin temel hak ve
  özgürlüklerine zarar vermemek kaydıyla, veri sorumlusunun meşru menfaatleri
  için veri işlenmesinin zorunlu olması."
* **Art. 6(1)** lists health data as special-category data. **Art. 6(3)** (as amended in 2024): processing is
  prohibited except (a) explicit consent, (b) expressly provided by law, …,
  (e) confidential health-service purposes, … There is **no
  legitimate-interest condition for special-category data.**
* **Art. 9(1)/(4)/(5):** transfer abroad needs an Art. 5/6 condition **and** an
  adequacy decision or an appropriate safeguard. Art. 9(4)(c) is a Board-announced **standard
  contract** ("standart sözleşme"). Art. 9(5) says it "imzalanmasından itibaren beş
  iş günü içinde … Kuruma bildirilir". Art. 9(6): only incidental ("arızi")
  exceptions otherwise.
* **Art. 4(2):** lawful and fair; specific, explicit, legitimate purposes;
  "bağlantılı, sınırlı ve ölçülü"; retention only as long as needed.
* **Art. 10:** information at collection: identity, purpose, recipients,
  "Kişisel veri toplamanın yöntemi ve hukuki sebebi", rights.
* **Art. 11:** the rights include deletion (e) and objection **only** to
  "münhasıran otomatik sistemler vasıtasıyla analiz edilmesi suretiyle kişinin
  kendisi aleyhine bir sonucun ortaya çıkmasına" (g). **There is no general
  right to object to legitimate-interest processing**, so any opt-out would be a
  voluntary safeguard in the balancing test.

## 2. Two models compared

| | **C: independent opt-in (current source)** | **L: narrow legitimate interest (proposal)** |
|---|---|---|
| Basis | KVKK Art. 5(1) explicit consent, separate from every other consent | Art. 5(2)(f), Tier 1 events only (§4). Tier 2 stays opt-in (Art. 6(3)(a) if health data). |
| Onboarding friction | none (Settings only; no new screen) | none |
| Coverage | consenters only. Survivor-biased. Pre-consent onboarding is unobservable. | everyone who has not objected, for Tier 1. Tier 2 is consenters only. |
| Transfer (Art. 9) | consent basis + standard contract + 5-business-day notification | LI basis + standard contract + 5-business-day notification |
| Apple 5.1.1(ii)* | satisfied (affirmative opt-in, withdrawable) | **open risk**: guideline text (summary) requires consent for collecting usage data "even if … anonymous" |
| Withdrawal/objection | implemented (durable, account-scoped) | voluntary objection switch (same Settings control, inverted default). **Not implemented.** |
| Implementation state | implemented, default OFF | not implemented. Needs a new authority state, an objection record and notice versioning tied to the Privacy Policy. |

\* Apple's guideline is a platform requirement independent of KVKK. If counsel
reads it as requiring an affirmative choice for usage-data collection, model L
is unavailable on iOS no matter the lawful basis. **This is the
first question to settle (Q-1).**

## 3. Legitimate interest assessment (draft for Tier 1 only)

**Purpose test.** AxisAI's interest is to operate and improve its own app.
That means finding where onboarding fails, whether first plans get created and
accepted, whether the Coach answers and its authorized changes apply, whether
features fail, and whether people return (D1/D7/D30). It is specific and
internal. There is no advertising, no profiling for decisions about the
person, no sale or sharing beyond one processor, and no tracking across apps or
sites.

**Necessity test.** Aggregate server logs cannot answer funnel, time-to-value
or retention questions without per-person sequencing. A random per-person
pseudonym is the minimum that allows this. Less intrusive alternatives
considered:

1. Server-only aggregate counters. These cannot do funnels or retention.
2. Opt-in only. This is the model C bias; it may still be preferred.
3. Short raw retention plus aggregates. This is adopted in RETENTION.md.

Counsel must judge whether Art. 5(2)(f) "zorunlu" (necessary) is met for
product improvement. That is not self-evident.

**Balancing test.** Mitigations already in source:

* closed catalog and enum-only properties
* no content, measurements, free text or device metadata
* server-mediated: the vendor sees AxisAI's IP, never the user's
* random pseudonym with no account identifier at the vendor; personless events
* exact wire allowlist
* rotation of the pseudonym (configurable) to bound raw retention
* durable erasure on withdrawal or objection and on account deletion
* adults only (V1 is 18+)

Risks:

* the data subject's reasonable expectations: does a fitness-app user
  expect usage analytics without asking? The Privacy Policy alone may be weak.
* a fitness context raises sensitivity (§4).
* cross-border transfer.
* Apple and ePrivacy constraints (Q-1, Q-4).

Proposed safeguards for model L, if counsel accepts it:

* Tier 1 only.
* An in-app notice reachable from Settings, plus the Privacy Policy (Art. 10).
* A one-tap objection with the same durable mechanics as withdrawal.
* A short raw retention.
* No new event without catalog review.

**Draft conclusion for counsel:** model L is *arguable* for Tier 1
server-authoritative events. It is *doubtful* for client-originated events (device
→ app → server observation of in-app behaviour; Q-4). It is *unavailable* for
anything that counsel treats as health data (Art. 6(3) has no LI condition). It
is *possibly blocked on iOS* by Apple 5.1.1(ii) (Q-1).

## 4. Event-level matrix (20 events; catalog `catalog.json`)

Tier 1 = ordinary product interaction; candidate for model L if Q-1 to Q-4
allow. Tier 2 = may reveal or permit inference about health or fitness behaviour
(exercise adherence, body-tracking check-ins, diet-plan activity); **consent
only** (model C) in both models. "Client" events originate on the device.

| Event (property values) | Origin | Purpose / KPI | Minimization | Health-inference risk | Tier | Expectation / note |
|---|---|---|---|---|---|---|
| `analytics_enabled` | server | cohort anchor | no props | none | C-only | meaningless under L (no grant). Under L the cohort anchors on the subject's first event. |
| `onboarding_started` | client | P1 funnel | no props | low | 1 | client origin (Q-4) |
| `onboarding_step_completed` (`step1..4`) | client | P1 funnel | step ordinal only, no answers | low | 1 | answers (age, goals, body data) are NEVER sent |
| `onboarding_completed` | server | P1, P2 anchor | no props | low | 1 | |
| `app_opened` / `app_resumed` | client | P7 secondary | no props | none | 1 | client origin (Q-4) |
| `screen_viewed` (`today, plan, plan_create, coach, settings, onboarding`) | client | navigation | coarse screen enum | low | 1 | |
| `screen_viewed` (`workout, nutrition, nutrition_plan, progress`) | client | navigation | coarse | **medium**: diet/body-progress interest | 2 | under L these values are dropped from the enum, or the screens merge into `other` |
| `training_plan_requested` | client | P3 | no props | low | 1 | |
| `training_plan_proposed` | server | P3 denominator | no props | low | 1 | |
| `training_plan_created` (`ai_preview_accepted, ai_direct, general`) | server | P3, P8 | path only, no plan contents | low/medium (training intent) | 1 | counsel to confirm; fallback is Tier 2 |
| `plan_viewed` | client | engagement | no props | low | 1 | |
| `progress_viewed` | client | engagement | no props | **medium**: body-progress tracking | 2 | |
| `workout_completed` | server | P4, P7 | no props, no exercise/load/duration | **medium/high**: exercise adherence is health behaviour | 2 | |
| `weekly_checkin_submitted` | server | P6, P7 | no answers, no weight | **high**: body/wellbeing tracking | 2 | |
| `coach_message_sent` | client | P5 | no content/length/topic | low (topic never sent) | 1 | |
| `coach_reply_completed` | server | P5, P2 | no content/topic | low | 1 | |
| `coach_mutation_applied` (`training`) | server | P5 success | domain only | low/medium | 1 | |
| `coach_mutation_applied` (`nutrition`) — DELTA-1 | server | P5 success | domain only | **medium**: diet-plan change | 2 | |
| `coach_change_proposed` (`training`) — DELTA-1 | server | P5 denominator | domain only | low/medium | 1 | |
| `coach_change_proposed` (`nutrition`) — DELTA-1 | server | P5 denominator | domain only | **medium** | 2 | |
| `coach_change_confirmed` (`training`) — DELTA-1 | server | P5 acceptance | domain only | low/medium | 1 | |
| `feature_failed` (`training_plan_generation`; `yes,no`) | server | P8 | retryability only, no error text | none | 1 | Apple "Diagnostics" label |

**Maximum useful scope without an affirmative interaction** (subject to
written counsel approval and Q-1): Tier 1 **server-authoritative** events
(`onboarding_completed`, `training_plan_proposed`, `training_plan_created`,
`coach_reply_completed`, `coach_mutation_applied{training}`,
`coach_change_proposed{training}`, `coach_change_confirmed{training}`,
`feature_failed`). That covers P2 (anchor = `onboarding_completed`), P3, P5
(training), P8, and a weak P7 (plans + Coach only).

Tier 1 client events add the P1 funnel and app-open retention only if Q-4
allows them.

Workout, check-in, Progress and Nutrition remain opt-in, so P4 and P6 are
measurable only for consenters.

**If LI is unavailable for a category:** exclude it from model L and keep it
behind the existing independent Settings opt-in. Low-friction opt-in
alternatives that add no standalone screen are founder options F-2: an
unchecked, unbundled inline toggle on an existing onboarding step, or an
inline card on an existing surface after first value.

## 5. Questions for counsel (DECIDE)

* **Q-1 (Apple).** Does App Review Guideline 5.1.1(ii) (consent for user or
  usage data "even if … anonymous"; re-verify the text) and 5.1.2 (no
  repurposing "without further consent unless otherwise explicitly permitted
  by law") allow model L for (a) server-derived events and (b) client events?
* **Q-2 (Art. 6).** Which events are health data under KVKK? Tier 2 is
  proposed as such. Is Tier 1 `training_plan_created` or
  `coach_reply_completed` health data in a fitness app?
* **Q-3 (Art. 5(2)(f)).** Is product improvement "zorunlu" for AxisAI's
  legitimate interest, and does the balancing hold for Tier 1? Does the KVKK
  Board's cookie guidance on analytics cookies (not read here) weigh against
  it?
* **Q-4 (device-originated events / ePrivacy).** For any EU/EEA users
  (GDPR + ePrivacy Art. 5(3)), are client-originated observations
  permissible without consent? Which markets are in first-public scope?
* **Q-5 (transfer).** Art. 9(4)(c) standard contract with PostHog and the
  5-business-day notification. Are onward transfers by PostHog's group or
  support access covered?
* **Q-6 (notice).** Art. 10 notice placement and versioning for each model.
  Is a Settings-reachable notice plus the Privacy Policy sufficient under L?
* **Q-7 (rights).** Should objection be offered under L as a voluntary
  safeguard? How do access/portability requests treat pseudonymous events
  (AxisAI holds the mapping)?
* **Q-8 (retention).** Approve the RETENTION.md proposals (raw ≤ 90 days via
  pseudonym rotation; aggregates ≤ 12 months; outbox ≤ 48 h; receipts).
