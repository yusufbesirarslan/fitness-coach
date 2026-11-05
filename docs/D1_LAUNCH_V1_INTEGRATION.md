# Launch V1 D1 source integration

This follow-up extends merged backend #473/#476 on main `216aa7c2`.
Merged mobile #157 and current mobile main `687efd5a` already supply the
five-purpose EN/TR consent, account isolation, durable request keys, allowance
presentation and StoreKit purchase path. No mobile source change is required.
Source integration does not approve disclosures or activate customer AI.

## Implemented founder policy

`launch_policy.LAUNCH_V1_POLICY` is the immutable source installer input:

| Scope | Coach | Training | Nutrition | Pump Check | QR Menu | Reset |
|---|---:|---:|---:|---:|---:|---|
| Premium | 60 | 2 | 2 | 16 | 2 | Europe/Istanbul calendar month |
| Starter | 10 | 1 | 1 | 1 | 1 | Once per eligible account |

No rollover. Monthly and annual products select the same Premium buckets.
Signed trial entitlements use those same buckets; conversion does not grant
another pool. Seven-day offer duration remains StoreKit configuration, not a
client or ledger entitlement override. All valid creation timestamps qualify
for Starter; NULL does not auto-grant. Re-registration is a new account, with
no cross-account tracking. Previously snapshotted buckets are not rewritten.

Usable completed results consume one logical unit. Fallback shares its
original operation, hold and physical vectors. Proven unusable terminal
results release the customer hold regardless of provider cost; UNKNOWN billing
and physical debits remain recorded. Unknown logical outcomes stay held and
expire conservatively until trusted domain evidence reconciles the original
row/bucket exactly once. Reconciliation does not authorize provider replay.
Canonical receipts take precedence over lost acknowledgements. Deterministic
Coach confirmations do not charge again for an earlier metered proposal.
Existing per-operation physical limits and per-user/global admission controls
remain; refunds do not replenish them or create a paid retry loop.

## Feature integration

Coach retains contextual reads and bounded confirmed first-party plan/log
operations. Nutrition and vision helpers require explicit `nested_features`
in the original Coach disclosure contract; every provider attempt uses that
same account, generation, recipient binding and physical budget. A separate
Nutrition/Pump consent cannot broaden Coach. Memory stays outside this scope.
Coach food estimates avoid direct FatSecret requests in D1 composition.
Photo validation rejects malformed output instead of fabricating a negative
analysis; completion and its allowance receipt share the K6 transaction and
existing session/day/User lock order.

Native Training, including its non-AI general-plan fallback, native Nutrition
and single-photo Pump Check retain their complete generation paths. Native
terminal failures settle from durable outcomes. QR retains public supported
web/text extraction and available nutrition recommendations under the original
QR purpose; PDF, image OCR and Pump comparisons remain outside Launch V1.

## Existing activation holds and LP22/R6 handoff

No disclosure bytes, recipient facts, model routes, flags or eligibility policy
were guessed or published. `POLICIES`, `PUBLISHED_TEXTS`, `LAUNCH_VERSIONS` stay
empty and `APPROVED_SERVICE_COHORT_VERSION` stays unset. Affected AI endpoints
and purchase readiness fail closed. The existing service-authority qualification
hold covers all ages and is separate from the pending 13–17 counsel opinion.
The existing signed eligibility gate stays in place; 13–17 parity is a product
goal, not legal permission.
No permanent 18+ product policy or parental-consent mechanism was introduced.

The existing privacy owner needs this single outstanding approval set:

- Exact approved five-purpose EN/TR text, categories, bytes/digests and bounded
  Coach/QR helper/sharing scope, using the existing disclosure drafts.
- Verified selected Bedrock/OpenAI model/profile and fallback recipients,
  processing locations and applicable retention/transfer facts; source
  defaults are not production evidence.
- Existing FatSecret advice/recommendation and retention permission/remedy;
  direct-call avoidance does not resolve historical diary/cache provenance.
  Preserve the separate compliance owner's pending work and attribution PRs.
- The already-requested counsel opinion for lawful 13–17 health-data access
  and subscriptions; retain current eligibility until cleared.

LP22/R6 must use the final reviewed source SHA together with their existing
K6, D1, signed eligibility, StoreKit (including both seven-day offers), actual
runtime/provider/Redis-budget and production security release receipts before
backend release. This handoff changes no release gate and grants no deployment,
source merge, production read or real-customer activation authority.
