# Adaptive Coach Nutrition — C1 preparation

Source only. No route, Coach tool, provider call, saved-plan write, migration
or production activation. This PR prepares the pure document boundary before
the separate atomic persistence and consent integration.

## Founder F-3 decision — 2026-11-18

Approved: when independent per-item nutrients are not reliable, re-estimate
the **entire affected meal** using the existing independent server estimation
path. Tell the user that these are estimates, including unchanged items whose
estimates can move. Rebuild day totals from canonical meal totals. Every such
re-estimation requires confirmation in this first slice because the old item
contribution is uncertain. This is a bounded fallback policy, not a universal
confirm-all policy for reliably sourced low-impact edits. No 200 kcal/15%
threshold was approved. Failed/ambiguous calculation writes nothing.

FatSecret Food Search is not a nutrient authority for Coach. No provider data,
shared food cache, or inferred old-item contribution enters this boundary.

## Implemented preparation

- `read_current_plan(owner)` reuses `nutrition_plan_store.newest_plan`, including
  its owner scope and newest-row/tie-break ordering; detached exact snapshots.
- Replace/add/remove target only the requested meal and a unique exact item
  (trim/casefold comparison). Ambiguous, missing, duplicate, invalid and
  last-item removal requests refuse before any estimation call.
- A pure preparation step accepts complete numeric whole-meal facts, replaces
  the affected meal atomically **in memory**, and rebuilds all daily nutrients.
  It never subtracts anything from stored day totals. Missing other-meal
  nutrients refuse; missing is never zero. Exact no-ops preserve bytes and need
  no estimation or confirmation. Decimal sums avoid binary accumulation drift.
- Estimate metadata is separate from the existing closed plan schema; the
  eventual journal/response must retain and display it. This PR changes no UI.

## Remaining before C1 can persist, and before C2

1. Independent whole-meal estimator adapter with strict input/output validation,
   bounded quantities, explicit failure on incomplete/ambiguous estimates and
   server-attested provenance tied to owner, exact before-state and proposed
   items. The existing `_estimate_macros_llm_batch` is **not** safe to call
   unwrapped: it coerces absent/invalid nutrients to zero and repairs partial
   JSON. Reuse its provider/prompt path, not those permissive success rules.
2. Keep database transactions closed across provider I/O. On confirmation,
   atomically recheck owner, original Coach authority, nutrition permission,
   legal-approved editing disclosure and exact canonical before-state.
3. Extend the existing Nutrition store with lineage/version and a durable
   append-only mutation journal. One owner lock must serialize whole replacement
   and targeted edits; journal, plan and increasing version commit together.
   Exact before/after snapshots enable byte-precondition undo, not re-estimation.
4. Persist one prepared result per operation key before confirmation. Retries
   reuse those nutrients with no second AI call; replay returns original durable
   evidence. Replacing the entire plan invalidates old pending edits and undo.
5. Share one active pending training-or-nutrition proposal, then adapt B2 named
   Confirm/Cancel without a Coach turn/allowance. Recheck stale/cancel/revoke,
   concurrent replacement/edit and double-confirm on PostgreSQL.
6. F-1/F-1b targeted capability re-consent remains a separate legal-gated PR.
   Ordinary r2 Coach chat must remain available. No C2 tool is published until
   the above guarantees exist. Production mutation flags remain OFF.

Tests: `tests/test_nutrition_plan_mutation_preparation.py` covers ownership,
unreadable storage, targeting, no-op, complete meal/day arithmetic, missing
facts, estimate validation and forged target rejection. This is not durable
mutation, provider, PostgreSQL race or real-device qualification.
