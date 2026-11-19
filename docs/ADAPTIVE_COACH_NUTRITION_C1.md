# Adaptive Coach Nutrition — C1 foundation

Internal server services only. No route, Coach tool or mobile Nutrition action
is exposed. Nutrition editing is source-gated OFF; the approved Coach editing
disclosure set is empty. Neither environment flags nor existing r2 consent can
open this boundary. PR #533 remains a separate legal-gated disclosure draft.
Ordinary permitted Coach chat is unchanged. Deploy and activation remain HOLD.

## Founder F-3 decision — 2026-11-18

When reliable independent item nutrients are missing, re-estimate the **entire
affected meal** using the existing independent server estimator. Every such
re-estimation requires explicit confirmation in this first slice. Return and
persist estimate provenance, and explain that unchanged foods in that meal may
also receive updated estimates. Rebuild day totals from canonical meals. Never
subtract guessed old-item nutrients from stored totals. No universal 200 kcal
or 15% threshold is approved. FatSecret and the shared food cache are excluded.

## Implemented boundary

- Canonical owner-scoped current-plan reads and exact unique item targeting for
  replace/add/remove. Missing, duplicate, ambiguous and invalid references, and
  removal of the last item, refuse. An exact no-op preserves bytes without AI.
- A bounded initial quantity grammar requires `Food - number unit` for EVERY
  item in the affected meal, including unchanged items. Units: g/gr/gram/grams,
  kg, ml/l, adet/piece/pieces; positive bounded amounts only. Unspecified or
  ambiguous legacy portions refuse before dispatch. This intentionally favors
  refusal over inventing a serving. Food/preparation ambiguity also requires
  estimator refusal; model estimates are not independently measured facts.
- One existing macro batch call re-estimates all affected foods. Strict mode
  uses the existing Nutrition authority/provider/admission path and rejects
  incomplete, repaired, nonnumeric, inconsistent or physically invalid output.
  Legacy estimator callers retain their existing behavior. No provider call
  occurs with an open SQL transaction. Canonical day totals use Decimal sums.
- `service.prepare_edit` commits a durable operation claim before estimation,
  then persists one exact quote and one proposal. Same-key retries never
  dispatch another estimate. A crash leaves an uncertain `estimating` claim;
  explicit `cancel_preparation` releases it, without retrying uncertain AI work.
  Failed estimates write only failure bookkeeping, no plan/proposal/journal.
- `service.resolve_edit` confirms/cancels one opaque owner-scoped proposal with
  no model call or Coach allowance. The original Coach editing authority AND
  original Nutrition permission are rechecked before application. Regranting
  cannot revive old work. Definitive refusal retires unusable proposals; an
  authority outage keeps them recoverable. Cancellation never applies a plan.
- The existing one-pending-proposal constraint covers Training and Nutrition.
  Training confirmation handlers and transport effects reject/hide other
  domains. This does not wire Nutrition into B2 or publish C2 tools.
- Confirmation atomically commits the complete meal/day document, increasing
  version, operation/proposal state and append-only journal. Exact lineage,
  version and before-bytes checks reject stale/concurrent writes. Full-plan
  replacement and targeted edits share the canonical owner lock.
- `service.undo_last_change` restores exact historical bytes at a new increasing
  version, without estimation. Replay keys and a unique revert constraint
  prevent duplicate undo. A full replacement creates a new lineage, so its
  predecessor's changes cannot be undone onto the replacement.
- Account deletion and health erasure cover both new ledgers. Estimate source
  and exact before/after snapshots live outside the closed Nutrition JSON.

## Schema and recovery

Migration `c1a9b7e5d301` adds nullable opaque Nutrition lineage, version zero with
server default, and two separate operation/history tables. Existing plan bytes
are untouched; old rows receive distinct random lineage tokens with no invented
history. Old-code inserts still work during code rollback; unidentified rows
fail closed for targeted edits. No production migration has been run by this
work. Schema-shape verification refuses drift and supports create_all boots.

Preferred rollback: roll back code while retaining additive schema/history.
A destructive downgrade refuses if either ledger contains records; empty-schema
downgrade is tested. Historical snapshots remain available for exact recovery.

## Qualification and release gates

Focused tests cover targeting/arithmetic, strict estimation, atomic rollback,
replay, stale state, interrupted work, authority outage/withdrawal cleanup,
Training proposal isolation, schema compatibility and drift. Dedicated real
PostgreSQL tests cover same-key claims/conflicts, double confirmation,
confirm/cancel, full replacement, duplicate undo, and original D1 revoke/regrant
(including withdrawal during estimation). Hosted CI remains the full-suite
qualification signal; synthetic providers only, no real-user/device QA.

C2 remains unavailable. Before release: legal-approved editing/nutrition scope,
targeted consent integration preserving ordinary chat, C2 transport/display of
estimate metadata, staging qualification, and separate founder authorization
for deployment and activation. F-2 selective confirmation remains unchanged for
Training and any future reliably sourced low-impact edits.
