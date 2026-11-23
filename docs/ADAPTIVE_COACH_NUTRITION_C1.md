# Adaptive Coach Nutrition — C1 foundation

> C2 (Coach tools + B2 confirmation over these services) is described in
> [ADAPTIVE_COACH_NUTRITION_C2.md](ADAPTIVE_COACH_NUTRITION_C2.md). It stays
> unavailable behind the same closed source gate and legal disclosure set.

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
  and exact before/after snapshots live in the journal. The affected canonical
  meal retains `tahmini: true`; saved native reads expose `estimated`, including
  after reopening. Unaffected meal labels survive; exact undo restores labels
  together with the original bytes. Generation/save proposal contracts stay
  unchanged.

## Schema and recovery

Migration `19cb998acd2f` adds nullable opaque Nutrition lineage, version zero with
server default, and two separate operation/history tables. Existing plan bytes
are untouched; old rows receive distinct random lineage tokens with no invented
history. Old-code inserts still work during code rollback. For rows inserted
without lineage after migration, preparation binds the canonical row id,
explicit null lineage, version and exact bytes, allocating identity only in the
operation claim. Confirmation adopts it atomically with the plan and journal;
failed/cancelled estimation does not change the plan or its identity. A new
row with identical bytes cannot satisfy that binding. No production migration
has been run by this work. Schema verification refuses drift and supports
create_all boots.

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

## Canonical reconciliation — PR #534 / #536

Compared #534 at `4ebe83893a97c6cc96286a3f68effb7d0b55b038` and #536 at
`ee1016681af5f37db44a1784f0e56e53ea5f3af1`. PR #534 is the canonical candidate;
recommend closing #536 as superseded after founder acceptance. Its worktree
contains separate uncommitted work, which this reconciliation leaves intact.

The only missing functional safeguards carried into #534 are persistent
estimate labels and safe adoption of old-writer null-lineage plans. Additional
PostgreSQL coverage proves actual lock contention before stale rejection and
distinct-key concurrent undo. Existing strict batch estimation, dual original
authority fences, durable estimator claims, shared pending confirmation,
atomic journal/replay/undo, privacy erasure and rollback checks are retained.
Per-item audit estimates in #536 are not needed to restore exact snapshots or
reproduce the complete persisted meal/day result, so no alternate journal is
introduced.

Exactly one C1 migration remains: `19cb998acd2f` after `e5b27aa4101c`
(owner-private exercises), with backfill, old-writer-compatible nullable lineage
and history-preserving rollback. It re-lands the accepted `c1a9b7e5d301`
(parent `ad17de1e0001`, merged 744ca3b2, reverted by R1 05147a04) with
byte-identical schema code and a new identity, because a database stamped
`c1a9b7e5d301` lacks `private_exercise`. `c1a9b7e5d301` is retired and must never
be reused: `app/retired_revisions.py` refuses such a database before any
migration runs. The one reconciliation is a single migration run with
`FITX_RECONCILE_RETIRED_REVISION=c1a9b7e5d301`: it verifies the C1 schema in
place, moves only the version pointer to `ad17de1e0001`, then runs
`e5b27aa4101c` and `19cb998acd2f` (both verify-or-create) in the same
transaction. Nothing is dropped, rewritten or stamped by hand.
Do not merge alternate `c1a0d2e3f4a5`, copy its schema, or join the two heads.
Current main, including #538's Istanbul date-test correction, is integrated.

The last reported production Coach mutation flag is **1**. This is not a
production flag-containment operation and does not claim Training mutations
are disabled. C1 Nutrition remains unavailable independently: both its source
activation constant and its empty legal disclosure allowlist must open, and no
C2 route/tool is registered. Tests explicitly set the Coach flag to 1. No
production configuration, deployment or App Store submission is changed.
