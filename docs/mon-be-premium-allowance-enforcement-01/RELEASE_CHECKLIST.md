# Release gate — no automatic advancement

This source package authorizes no deployment, migration execution, staging,
production, AI processing or payment activation. All three allowance flags stay
default OFF, no production policy is constructed, and D1 registry/closed routes
remain unchanged. Source merge itself requires the founder's decision.

| Stage | Required gate | Current disposition |
|---|---|---|
| 0 source | Exact reviewed HEAD, hosted required CI, independent review, P0/P1=0 | Evaluate source package; never equate merge with enforcement |
| 1 isolated | Synthetic accounts/policies, disposable PostgreSQL, fake SDK, no real photos/Apple/provider calls | Local tests and hosted synthetic CI only |
| 2 staging | Separate founder deployment/data-processing authorization, MON-03 coordination, actual clients and D1 transport qualification | NOT AUTHORIZED |
| 3 production dark | Separate release/deploy gate, migration/schema evidence, no paid AI activation | NOT AUTHORIZED |
| 4 controlled paid launch | Exact offer, eligibility, family mappings, billing failures, client disclosure, payments, monitoring and rollback approved | NOT AUTHORIZED |

Before a source-merge recommendation, record exact head and CI/review evidence in
IMPLEMENTATION_REPORT / FINAL_REPORT. No critical skip is waived by a green local
SQLite run. Hosted Linux locks, migration drift and PostgreSQL remain required.

## Founder decisions before activation

- Exact Starter/Premium quantities and `max_billed_releases`, full policy version
  and period; P_30_SAFE is a recommendation for qualification, not approval.
- Starter existing-account cohort/cutoff, NULL timestamps, backfill, exhaustion,
  deletion and re-registration treatment. Never mint an extra grant on login.
- Comparison → Pump and replacement → Training mappings, or keep unavailable.
  Keep unrelated legacy AI refused until separately classified/qualified.
- Offered transports: web streaming stays closed; web split paid-OCR needs an
  approved combined analysis operation. Do not sell an unavailable experience.
- D1 disclosures/original authority/recipient scopes and bounded publication
  qualification with its owner. Never broaden the production registry here.
- Current ASC monthly/annual tax-adjusted proceeds, Apple standard commission,
  refund/FX exposure, MON-03 StoreKit/restoration/reconciliation readiness.
- Measured/tighter physical input, tool-loop, retry and menu batch budgets;
  candidate quantities alone do not certify worst-tail profitability.
- Mobile/web UI, EN/TR offer terms, error/recovery and weekly reset display;
  replace unlimited claims before purchase. No silent subscription claim changes.
- Explicit migration/deploy, data-processing, launch and rollback approvals.

## Atomic readiness and rollback invariants

Unified admission requires `AI_PREMIUM_ALLOWANCE_ENFORCEMENT_ENABLED`, ledger,
native Training enforcement and Apple subscriptions together, plus a complete
five-family Premium policy. Individual route availability still requires D1 and
client readiness. Missing flags/policy/entitlement refuse; no unlimited fallback.
Do not offer partial metering while exposing an unmetered Premium family.

On an authorized future rollback: first close paid AI admission at the approved
product/route gate, stop new dispatch and drain/retire in-flight operations using
their original authorities. Keep the ledger and compatible code/schema available
to settle existing holds. Only then disable unified enforcement. Simply flipping
it OFF re-enters preserved legacy behavior, including unlimited Premium paths,
and is **not a paid-launch-safe rollback**. Retain financial evidence; no database
downgrade/deletion is part of rollback. Coordinate all web/native workers and
rolling versions; old workers must not remain capable of dispatch after admission
opens. Never change a live bucket's granted quantity by policy update.

## Tier and period qualification

Free/eligible Starter → Premium uses a distinct weekly Premium bucket; prior
Starter usage remains lifetime. Cancellation with valid verified expiry retains
Premium. Expired/stale/unverified entitlement cannot mint Premium. Pending or failed
lookup returns temporary unavailability instead of Free/Starter. Restoration,
re-subscription and transaction/reconciliation replay use the same owner/feature/
calendar key and never refill an existing bucket. No account transfer. Account
deletion follows existing cascades/tombstone rules; re-registration commercial
eligibility remains unresolved, with no new cross-account identity mechanism.

Europe/Istanbul calendar comes from existing `app.timeutil`, Monday weekly
boundaries and ISO week-year. Current Istanbul UTC+3 has no DST transition; use
the timezone implementation rather than hardcoding UTC offsets. Retain original
bucket on reset-overlapping settlement/expiry/correction. Include year rollover,
six intersected week exposure, entitlement-change ordering, late result, deletion,
revoke/regrant old keys and canonical commit/lost-response cases in staging.

## Sanitized metrics / warning candidates

No new paid service. Reuse ledger aggregates and existing `ai_usage`, model/route,
retry/fallback and spend/input telemetry. New execution logs carry fixed event,
canonical feature and existing error code only, without identifiers or content.

| Metric | Source / definition |
|---|---|
| Actions requested / authorized / exhausted | Fixed execution events plus durable fresh reservations / typed exhaustions; log counts are advisory, ledger is accounting |
| Success settlements | Committed success outcome; canonical receipt survives textual response failure |
| Billed / unknown failures | Existing billing_evidence and billed_releases; UNKNOWN stays separate from confirmed unbilled |
| Attempts per logical action | Committed physical_attempts; native Training original attempt evidence plus physical AI usage telemetry |
| USD per feature / account / Premium subscriber | Existing sanitized usage token/model price estimates, joined only by approved internal attribution; never public cross-account data |
| P50/P90/P99 spend | Actual physical usage cohort aggregates after sufficient samples; do not relabel synthetic FINOPS profiles as observed percentiles |
| Unattributed invocation cost | Provider invoice/usage minus attributed physical calls; explicitly track coverage and uncertainty |

Candidate internal warnings: 50% and 80% of conservative net monthly proceeds,
break-even at proceeds minus non-AI reserve, and repeated tool/fallback-heavy
actions near their physical limit. Use the annual 30% monthly-equivalent proceeds
when it is lower; revise for refunds/FX. $6 alone is too late relative to $3.61
annual standard proceeds. Alerts trigger investigation, not entitlement cancellation
or an undisclosed customer cost-only rejection. Define threshold ownership,
sampling freshness and incident response before activation.

Keep spend guard enabled; qualify shared-store degradation and per-process fallback,
retry configuration, model prices, input/output limits and all unattributed costs.
Never log prompts, answers, images, URLs, health context, credentials or secrets.
