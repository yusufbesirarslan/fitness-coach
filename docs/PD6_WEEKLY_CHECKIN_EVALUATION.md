# PD6 automatic weekly Coach review

A new native v2 check-in and its body-weight effects commit through the
canonical weekly check-in writer. The same transaction records the review
intent on that row. A worker evaluates only committed evidence. The check-in
response never waits for a provider. The app shows the saved confirmation and
reads the evaluation automatically, with history readback for the same owner.

## Native API

The trigger is a fresh `POST /api/v1/progress/check-ins?contract_version=2`
with the backend PD6 flag enabled. The existing payload, 201 response and 200
replay snapshot remain exactly PD5 v2. A v2 replay keeps the original ID,
language and operation state. The unversioned v1 and legacy web transports
retain their existing behavior. Unsupported clients continue to use their
existing result journeys. A fresh v2 check-in is eligible for scheduling
independently of other check-ins on the same day or week.

Read by ID with `GET /api/v1/progress/check-ins/{check_in_id}/evaluation?contract_version=1`.

```json
{
  "contract_version": 1,
  "check_in_id": 42,
  "status": "completed",
  "evaluation": {
    "language": "en",
    "summary": "Sleep quality: 2/5. Fatigue: 4/5.",
    "changes": ["Fatigue: 2/5 → 4/5"],
    "next_week_actions": ["Choose a regular bedtime you can keep on most days."]
  }
}
```

- The response is HTTP 200 for an existing operation. The closed status set is
  `pending`, `processing`, `completed`, and `unavailable`. The evaluation
  field is null except on completion. Terminal refusals and failures share
  the unavailable UI. Backend diagnostics are excluded.
- Bearer authentication and current first-party health authority gate reads.
  The ordinary 401, eligibility/health 403 and authority 503 envelopes
  apply. Missing, repeated or unsupported resource versions are 400
  `CHECKIN_INVALID_REQUEST`. Foreign IDs, sparse rows, erased rows and
  rows without an evaluation intent return 404 `CHECKIN_EVALUATION_NOT_FOUND`.
  Storage failure uses retryable 503 `CHECKIN_TEMPORARILY_UNAVAILABLE`.
- Ordinary reads never create or process work. The app safely polls a limited
  number of times while the result is visible, cancels on navigation or
  account transition and reads again on return. The latest historical result
  opens with the check-in history. Older results can be opened in that list.
  The bounded PD5 history window is unchanged. No new client persistence is
  required for AI data.

## Durable operation and crash boundaries

The check-in row is the durable operation slot. The only identity is
`(WeeklyCheckIn.user_id, WeeklyCheckIn.id)`. Existing owner/key and primary-key
constraints make duplicate writes converge. A failed canonical commit
creates neither check-in nor intent. An absent or denied operation grant at
submission is recorded as unavailable immediately.

| Boundary | Recovery |
|---|---|
| Canonical commit and intent creation | Atomic. The scanner has nothing to see before the transaction commits. |
| Commit to worker wakeup | The existing RQ worker enqueues a bounded, generic scan on a 15-second cadence. Redis contains no owner, ID, prompt or result. A lost wakeup or restart is recoverable from pending rows. |
| Worker acquisition | PostgreSQL `FOR UPDATE SKIP LOCKED` commits a short lease. An expired pre-dispatch lease can return to pending. A stale claimant cannot advance a different lease. |
| Provider dispatch | A committed dispatch marker precedes allowance admission and the provider route. Every physical attempt rechecks the claim inside the existing D1 dispatch fence. An expired dispatch marker is terminal and feeds unavailable readback. |
| Provider timeout, worker loss, ambiguous result | No second orchestration dispatch. Only the existing bounded router recovery and physical-attempt controls apply inside the original operation. A remote attempt may already have run. Durable holds/attempt evidence settle under the existing allowance rules. |
| Result persistence | The owner fence, canonical metering marker and completed review commit together. Publication only updates a live, claimed canonical row. A write failure or lost acknowledgement cannot reopen dispatch. |
| HTTP delivery | POST replay returns the original canonical snapshot. Evaluation reads return the same stored result without charging or contacting a provider. |

The worker uses the normal provider route with one bounded textual structured
operation (520 output tokens, no tools or repair prompts). The generic RQ job
does not persist output or user-identifying arguments. Dispatch/refusal after a
claim is terminal for this check-in. The app has no generation or retry button.

## Evidence and output

The evidence snapshot is the worker's first successful, consistent post-commit
PostgreSQL read for the attempt that wins the dispatch marker. It is not the
immutable native POST snapshot. An edit before that read can change the weight
seen by the evaluator. A later edit never refreshes a completed historical
review. The server computes a keyed digest over the exact minimized evidence,
owner, ID, language and contract version. Raw evidence, prompts and provider
output are not stored. A pre-dispatch lease recovered without a physical
attempt can read the then-current committed evidence.

`full_checkin_by_id` and `previous_full_checkin` enforce owner and full-row
qualification. Non-weight comparisons use the preceding full row in
`created_at DESC, id DESC` order, including same-day rows. Weight change uses
the Progress History calculation against the most recent positive recorded
weight from an earlier Istanbul day. Numeric and applicable ternary nutrition
are compared only within their original scale. Missing and not-applicable
answers never imply poor adherence. Progressive overload retains its
independent `yes / partial / no` vocabulary.

The provider ranks one or two recorded observations for the summary, zero to
three verified changes and one or two eligible practical actions. Its closed
schema contains identifiers only. The backend validates exact keys, type,
cardinality, uniqueness, evidence membership and EN/TR tag. It renders the
selected facts and actions in localized plain text. An unsupported comparison,
unsafe instruction, invented cause or arbitrary model prose cannot become a
persisted review. The first check-in has no changes. There is no generic
fallback evaluation. The language is the canonical account preference at
submission, using English for an unset or unsupported preference. A worker
retry or submission replay cannot change that snapshot. This is advisory and
makes no plan or target writes.

## Activation authority and accounting

The existing D1 `coach.conversation` disclosure covers answering the user's
messages. Its reference to body measurements and recovery ratings does not
authorize automatic processing after a check-in. The #533 increment concerns
Coach plan editing and saved Nutrition Plan reads. It supplies no weekly-review
authority. PD6 requires a separately approved exact operation/purpose
(`coach.weekly_checkin_review` is the source's proposed identifier), localized
EN/TR disclosure, defined health categories, current model/recipient and
transfer authority, user grant and approved visibility and retention treatment.
There is no D1 registry entry or disclosure publication in this slice.
Cohort restrictions, including Türkiye minors, are inherited unchanged.

The existing allowance families assign no policy to automatic check-in
reviews. The proposed accounting is one owner-scoped logical action per
canonical check-in in the existing finite Coach family, with all physical
calls and fallback metered under the shared guard and ledger. This is a
proposal, pending explicit product and accounting approval. The source
`APPROVED_ALLOWANCE_FAMILY` is unset and the distinct D1 binding and physical
allowance mapping are absent. Turning on the rollout flag alone cannot reach
the provider. Approval must precede any source pin, D1 mapping or physical
allowance mapping. There is no new entitlement, limit increase or billing
exemption. The operation has its own bounded input/output label and fails
closed on missing Redis or spend rejection. All source tests use synthetic
disclosures, limits and SDKs. They confer no live provider qualification.

The original health and D1 generations are captured before the check-in's
owner lock. The worker rechecks health, canonical cohort, D1 purpose, approved
version and provider route, rollout, logical allowance and spend before
dispatch. Each physical call uses the same D1 authority. Consent revoke
orders against new calls and pending publication. A previously completed
result remains readable while current health authority holds, following the
existing completed Coach history rule. This visibility treatment is subject
to the PD6 disclosure decision. Provider work already transmitted cannot
be recalled.

## Migration, erasure and W3 composition

The expand revision `e6a4b91d702c` follows accepted PD5 `c8e1f3a5b702`.
The operation columns are nullable with no backfill. A state constraint ties
data presence to lifecycle and admits operations only on full rows. Existing
writers can insert rows with those columns null. The repository's exact
schema admission still governs which binary can boot against a new head.
Downgrade refuses to discard any existing PD6 operation.

The existing health-erasure and account-purge paths delete the canonical row,
disposing of its intent, result and evidence digest. Late publication uses
only a fenced update and cannot recreate a deleted record. Existing allowance
receipts carry no raw health inputs or result and continue under their existing
settlement, retention and account-purge contracts. No new retention period is
defined. Confirm derived review and provider-copy retention before activation.

Keep the PD5 parent unchanged in this source candidate. The later integration
owner must verify #565 at `160c887768be1eb08375ba82a7a0a2e09c6c9386`, confirm
`b7d2e9f4a1c3` as composed W1/W2 tip, then compose PD5 after that head and
PD6 after PD5. Reconcile `app/models.py`, native route allowlist,
`app/feature_flags.py`, rollout docs, workflow explicit PostgreSQL file list,
exact route and provider inventory assertions, and Alembic graph/head/count
assertions. Preserve PD1's
`account_profile.derive_targets_for_weight` in the canonical weight writer
when composing the existing PD5 overlap. The PD6 worker never touches weight
or targets. Preserve #561 same-owner Nutrition estimator allowance and #563
Coach opener admission. PD6 uses the standard metered physical call door
without arming a Coach tool-loop opener. When integrated, re-run the PD1/PD5
weight compatibility and #565 allowance/spend regressions in the composed
candidate. Mobile touches composition, check-in screen/controller/history,
protected repository and EN/TR strings. Reconcile composition and strings
against the later accepted W1/W2 mobile stack.

## Rollout and rollback

Both switches default OFF. After separate integration and activation approval,
qualify the compatible backend resource and worker, then the enabled mobile
journey. Backend `FITX_WEEKLY_CHECKIN_AI_REVIEW_ENABLED=1` must be loaded in
both web and worker. Mobile `AXISAI_CHECK_IN_AI_EVALUATION_ENABLED=true`
requires `AXISAI_CHECK_IN_NUTRITION_V2_ENABLED=true` and the compatible backend.
Keep PD5's flag independent.

Disable the backend flag and reload both web and worker to prevent new intents
and processing. Previously completed data and canonical check-ins remain
readable. Incomplete operations return unavailable while disabled. Authorized
pre-dispatch pending intents can be revisited after a separately approved
re-enable only with the original generation and all current checks. A held
dispatch never restarts. A running old process can finish an already admitted
attempt until replaced. Turn the mobile switch off for subsequent builds to
remove the evaluation panel. Device validation and production activation
require separate approval. W1/W2 release eligibility is independent of PD6.
