# Adaptive Coach Nutrition — C2 Coach tools

Task `AXISAI-ADAPTIVE-COACH-NUTRITION-C2`. Source development only: no merge,
deploy, activation, production flag change or App Store change. App Review
build 101119 is unchanged. Canonical C1 is #534 (`744ca3b2`); C2 adds no
migration and reuses C1's estimator, operation claims, journal, transactions
and schema. Nothing from #536 is used.

## Availability (all must hold; closed in this source)

1. `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` (the flag alone never opens C2).
2. C1 source gate: `access.NUTRITION_EDITS_ENABLED` AND a non-empty
   `access.APPROVED_COACH_EDIT_DISCLOSURES` — both code constants, closed.
3. The turn's own Coach D1 operation is on an approved editing disclosure
   (the r2 disclosure is NOT sufficient; #533 is legal-gated and unmerged).
4. The owner's own current `nutrition.plan` grant, captured once per turn by
   `ai_consent.begin_independent_operation` — the unmodified `begin_operation`
   predicate run with no active operation in a copied context, so the Coach
   operation is never switched or widened.

Missing any → no Nutrition tool is published or executable, saved Nutrition
content is never sent to the model, and ordinary Coach chat (and Training
B1/B2) is unchanged. `plan_effects.nutrition_plan` is omitted entirely.

## Tools (`app/services/coach_plan_tools/nutrition.py`)

| Tool | Args (closed) | Effect |
| --- | --- | --- |
| `get_current_nutrition_plan` | none | Authoritative read; publication rechecks the Nutrition authority. Item text, meal macros, totals, `estimated`. No lineage/version/ids. |
| `replace_nutrition_plan_item` | meal, item, replacement | C1 quote (whole-meal re-estimate) → proposal; nothing saved |
| `add_nutrition_plan_item` | meal, item | same |
| `remove_nutrition_plan_item` | meal, item | same; never empties a meal |
| `undo_last_nutrition_plan_change` | none | C1 exact-bytes undo, no estimate |

Server-owned: owner, plan, version, operation key (`aic:n:<turn>:<digest>`
from the typed edit, never raw JSON), proposal identity, actor `ai_coach`,
reason `None`. Per-turn budget is shared with Training (2).

Grounding before any durable claim or estimate: the authoritative plan, C1's
exact unique targeting (`target_edit`) and C1's quantity grammar for EVERY item
in the affected meal. Missing/ambiguous item, meal or amount → `needs_user_input`
with a server instruction to ask; never a guessed portion. Estimator failure,
incomplete/invalid output or a spend refusal → `ESTIMATE_UNAVAILABLE` and C1
writes only failure bookkeeping (never a zero). No FatSecret/cache.

Results are a closed vocabulary; `coach_privacy.tool_result` rebuilds every
Nutrition result from tokens, finite numbers and bounded control-free item text.
Logging is the single `[COACH][PLAN_TOOL] request_id tool outcome` line.

## Confirmation (founder F-3: every re-estimated edit)

- The quote's reply is server-owned copy (`coach.nutrition.propose`): change,
  affected meal before/after, day totals before/after, and the notice that the
  whole meal was re-estimated and unchanged foods may change. The model cannot
  narrate it and cannot claim completion.
- Native button: `POST /api/v1/coach/plan-confirmation` with the Nutrition
  `proposal_id` → C1 `resolve_edit`. No model, no turn, no allowance.
- Typed "evet/hayır": resolved through the same C1 call before any provider
  call (like Training), consuming the turn as Training does.
- Any other message while a Nutrition quote is pending gets the existing
  "reply yes or no" copy (Training parity); new meal/workout staging and new
  plan proposals are refused while it is pending.
- One pending Training-or-Nutrition proposal per owner (shared index): each
  domain blocks the other; Training tools/effects never expose a Nutrition id.
- C1 guarantees: dual original-authority fence, exact lineage/version/bytes,
  proposal-row lock (confirm vs cancel), replay of repeats, one estimate per
  key, atomic plan+journal, retire-on-withdrawal (regrant never revives).
- Cancel after an applied change answers 409, never "cancelled".
- Estimator claims left by an interrupted request (> 15 min) are released via
  C1 `cancel_preparation` before a new edit — never retried.
- A live claim (< 15 min) answers `PREPARATION_IN_PROGRESS`.
- A yes/no-form turn never starts a new Nutrition change (Training parity).
- Typed-confirm and applied copy is `DisplayOnlyReply`: saved food/macro text
  never becomes model-visible conversation memory.
- Nutrition refusals/clarifications are left to the model, guided by the
  server instruction; they never fall into Training clarification copy.

## Closed-gate rollback (stranded quotes)

If the boundary is closed while a quote is pending, it can no longer be
confirmed (B2 confirm → 409; ordinary chat is not blocked), but the owner can
still CANCEL it — B2 cancel with its id, or a typed "hayır"/"cancel" — through
C1 `release_unavailable`: owner-scoped, proposal→owner lock order, needs no AI
authority (like `cancel_preparation`), never writes a plan or journal, and
refuses while the boundary is open. This frees the shared one-pending slot so
Training proposals are not blocked. B2 keeps its flag-OFF 404.

Cost and order (pinned by tests):

- Typed turn, boundary closed (production): `resolve_pending_turn` reads the
  shared proposal table once (`_plan_pending`) and caches that row on `g`, keyed
  by owner. Only a cancel-intent turn with nothing pending looks at the cached
  row; every other turn (including a plain "no" with nothing stranded) adds no
  proposal read. The later per-turn checks (tool publication, staging/plan
  guards) read exactly as on main before C2; C2 adds no read to a turn. A failed
  pending read drops the cache, so no stale row survives it.
- B2 cancel: the Training path runs first, unchanged; only if it finds no
  Training proposal is the named id tried as a stranded Nutrition quote.
- A found stranded quote logs its real outcome as one closed-vocabulary line
  `tool=nutrition_plan_stranded_release outcome=<cancelled|ERROR_CODE>` (no
  ids, text or amounts). On B2 it follows the Training line that only says no
  Training proposal was pending; a typed cancel logs the stranded line alone.

## Undo (founder decision: explicit Undo, no second confirmation)

`undo_last_nutrition_plan_change` applies on the user's explicit request with
no second confirmation, mirroring Training undo. No C1 undo proposal kind.

- C1 `undo_last_change`: exact prior bytes, owner-scoped, same lineage, new
  increasing version, dual original-authority fence, journal replay (a repeat
  in the same turn replays, never undoes an older change).
- No estimator call, no proposal, no extra model call or Coach allowance (it
  runs inside the user's own Coach turn).
- Refused on a yes/no-form turn, while any quote is pending, and beyond the
  shared per-turn budget.
- "Explicit request" is enforced as for Training undo: the tool description
  and the Coach plan-mutation policy restrict it to an explicit user ask; the
  server guards above stop it on answers and over pending quotes. There is no
  server-side parser of the user's wording (Training parity).

## Native transport and mobile handoff

See docs/MOBILE_COACH.md §Nutrition plan effects. A separate mobile PR
(build ≥ 101120, never 101119) must: render `plan_effects.nutrition_plan.
proposal` (change line, before/after meal, totals, estimate notice + label),
send Confirm/Cancel with its `proposal_id`, re-read the Nutrition Plan when
`changed` is true or after an `applied` answer, and show the C1 `estimated`
meal label on the Plan screen. 101119 ignores the key and the closed gate
never emits it.

## R1 containment dependency (C2 merge sequence)

R1 production containment (draft #543) temporarily reverts C1 #534 and #537 on
backend main and owns main integration. C2 #542 is based on main with C1 and
is NOT rebased onto the revert candidate. Required order:

1. R1 runtime containment completes (R1 owns it; C2 does not touch #543/main).
2. C1 is restored on main by a revert-of-the-revert, with its own CI.
3. C2 is rebased onto that main, hosted CI re-run on the exact new head
   (incl. PostgreSQL jobs), delta re-reviewed for P0–P2.
4. Founder source-merge decision for C2 (still closed; activation separate).

If C1 is restored with changes (not a byte-exact revert-of-revert), the C1
helpers C2 adds (`find_quote`, `release_unavailable`,
`interrupted_preparations`) and the C2 PG suite must be requalified against it.

## Release dependencies (all open)

Legal approval of #533 wording and its implementation (targeted re-consent,
approved Coach editing/nutrition disclosure versions placed in
`APPROVED_COACH_EDIT_DISCLOSURES`), a deliberate source change of
`NUTRITION_EDITS_ENABLED`, staging real-provider qualification, the mobile PR,
and separate founder authorization for merge, deploy and activation.

## Tests

`tests/test_adaptive_coach_c2_nutrition.py` (gates, grants, read, refusals,
estimation failure, replay, confirm/cancel/undo, stale, foreign/tampered ids,
Training isolation, purge, schema/shape guards) and the CI-listed real
PostgreSQL `tests/test_adaptive_coach_c2_nutrition_pg.py` (real D1 capture,
parallel confirm/cancel, double confirm, withdrawal + regrant, Coach regrant,
cross-account). Synthetic estimator only. Query-count, cache, B2-order,
logging and undo-contract regressions are pinned in the unit module.
