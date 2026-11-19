# Adaptive Coach Foundation V1

Task `AXISAI-ADAPTIVE-COACH-FOUNDATION-V1` (founder: approved for foundation
development, 2026-11-18). This is source only. Production deploy is HOLD,
App Review build 101119 is unchanged, and nothing merges without separate
founder authorization.

Current decision update (2026-11-18): F-1 requires updated editing disclosure;
F-1b requires the nutrition permission plus explicit Coach nutrition scope,
with targeted re-consent preserving ordinary chat. F-2 remains selective,
server-owned confirmation. B2 decouples the prompts and provides deterministic
Confirm/Cancel. F-3 is now approved for independent whole-meal re-estimation
when reliable per-item facts are absent, with explicit estimates and first-slice
confirmation. See [C1 foundation](ADAPTIVE_COACH_NUTRITION_C1.md) for exact
scope and release gates. The architecture/decision questions below are the
historical B1 record, not superseding these decisions.

This document has two parts: the architecture and gap map (deliverable A),
and the record of the first vertical slice (B1, this PR).

---

## A. Current capabilities, verified in source at `2b58ef29`

### Training plan: mostly built, behind a flag that is OFF

| Layer | Module | State |
| --- | --- | --- |
| Canonical plan | `TrainingPlan` (`plan_data` JSON, `lineage_id`, `mutation_version`), `today_facts.get_active_plan` | Shipped |
| Targeted mutation domain | `app/services/plan_mutation/`: 5 typed commands, row lock, no-op detection, versioning, append-only `PlanMutationRecord` journal, idempotent replay, byte-precondition undo | Shipped (S1 PR1/PR2) |
| Coach bridge | `app/services/coach_plan_tools/`: 6 narrow write tools plus confirm/cancel; parser that refuses rather than clamps; server grounding for ambiguity/catalog; per-turn budget 2; `aic:` operation keys from `g.request_id` | Shipped (S1 PR3), flag OFF |
| Impact policy + confirmation | `coach_plan_policy` (APPLY_NOW / REQUIRE_CONFIRMATION / REFUSE) + `plan_confirmation` (one PENDING proposal bound to lineage + version + snapshot + command) + structural CONFIRM/CANCEL parser on the raw user turn | Shipped (S1 PR4), flag OFF |
| Whole-plan replacement | `plan_replacement` + `plan_owner_lock` (`expected_plan` precondition, typed 409) | Shipped |
| Native Coach | `POST /api/v1/coach/messages` runs the same `ai_pipeline` and the same plan tools; `COACH_REPLY_INCOMPLETE` (503, never retryable) after a committed tool | Shipped |
| Mobile convergence | `CoachController.onPlanMayHaveChanged` → Today + Plan re-read after **every** answered turn | Shipped |

Flag: `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED`, default OFF, `staging_only`,
`depends_on=("AI_ADAPTIVE_PLAN_CONTEXT",)`.

### Nutrition plan: whole-document replace only

- `NutritionPlan` is one JSON document per row: `isim` plus 4 meal slots
  (`kahvalti/ogle/aksam/ara_ogun` → `yemekler` free-text item list +
  `kalori/protein/karb/yag`) plus totals. It is closed by
  `nutrition_plan_schema`.
- Its only writer is `nutrition_plan_store.replace_nutrition_plan`: owner
  lock, then a precondition (native `If-Match` revision), then delete/insert,
  under the D1 fence.
- There is no lineage, no version, no journal, no targeted mutation, no undo
  and no Coach read or write.

### Consent (D1) as it stands

- Every Coach turn already runs inside `ai_consent.begin_operation(user,
  "coach")`. That binding is `coach.conversation`, disclosure
  `d1-closure02-coach-candidate-r2`. Every tool dispatch re-checks
  `authorize_operation` (owner + feature).
- The coach disclosure's categories include `training_history`,
  `workout_state`, `fitness_preferences`, `nutrition_activity`,
  `nutrition_targets` and `tool_results`. Its text says Coach can *"read your
  records"* and *"prepare a meal or workout log that is saved only after you
  confirm it"*. It says it does **not** permit *"training-plan or
  nutrition-plan generation"*.
- `food_preferences` is a `nutrition.plan` category. It is **not** a
  `coach.conversation` category.

---

## Architecture gaps

| # | Gap | Effect | Slice |
| --- | --- | --- | --- |
| G1 | The Coach could not **read** the current plan. Context carried only an AdaptivePlan guidance line. | The model names days and exercises from memory; server grounding catches misses after the fact. This fails the acceptance line *"references actual current plan data"*. | **B1 (this PR)** |
| G2 | Native turn response carried no plan fact. | The app can re-read Plan (it already does), but cannot show *"updated"* or *"waiting for your confirmation"*, so a pending change is invisible except in prose. | **B1 (this PR)** |
| G3 | Confirmation costs a full LLM turn plus a `chat` quota unit, and depends on the user typing a parser word. | Cost and UX friction; a second turn re-sends context to the provider. | B2: deterministic `POST /api/v1/coach/plan-proposal/{confirm,cancel}` (no provider, no quota, same `plan_confirmation` + `PlanMutationService` path, owner-scoped, idempotent on the proposal public id) |
| G4 | Plan tools require `AI_ADAPTIVE_PLAN_CONTEXT`, which swaps the whole Coach system prompt for every user. | The rollout couples the narrow capability to the broadest prompt change. | B2: qualify both together in staging, or decouple with founder approval |
| G5 | `replace` / `add` / prescription edits apply immediately on an explicit request. Only remove, move and active-session impact require confirmation. | This is a deliberate S1 PR4 decision. The founder brief says *"transparently presented and authorized"*. | **Founder decision F-2** |
| G6 | Nutrition has no targeted mutation domain, versioning, journal or undo. | Nutrition edits cannot meet the safety bar yet. | C1 (domain + migration), C2 (Coach tools) |
| G7 | Nutrition item macros: meal macros are LLM estimates for the whole meal, and there are no per-item macros. | Replacing "eggs" with "meat" leaves the meal's kcal/protein wrong unless something re-estimates them. | **Founder decision F-3** |
| G8 | Consent scope for plan **edits** and for nutrition-plan **content** under `coach.conversation`. | Must not be bypassed. | **Founder/counsel decision F-1** |
| G9 | Web Coach widget shows no plan fact. | Web Plan has a 5-second focus re-read notice already, which is acceptable for V1. | Later |

---

## Proposed tool contracts

Rules for all tools: the schema carries only user intent, never
user/plan/lineage/version/proposal ids. The parser refuses rather than
clamps. Results come from a closed vocabulary, re-projected by
`coach_privacy.tool_result`. The server owns impact, confirmation and
persistence.

### Training (exists, plus B1)

| Tool | Args | Write | Confirmation |
| --- | --- | --- | --- |
| `get_current_training_plan` (**B1, new**) | none (any argument is refused) | no | n/a |
| `replace_training_plan_exercise` | day, exercise, replacement, sets?, reps? | yes | if active-session impact (F-2 may make it always) |
| `add_training_plan_exercise` | day, exercise, sets, reps | yes | same |
| `remove_training_plan_exercise` | day, exercise, match_sets?, match_reps? | yes | always |
| `update_training_plan_prescription` | day, exercise, sets?, reps? | yes | same as replace |
| `move_training_plan_day` | day, target_day (content swap) | yes | always |
| `undo_last_training_plan_change` | none | yes | if active-session impact |
| `confirm_` / `cancel_pending_training_plan_change` | none | yes | gated on a structural CONFIRM/CANCEL in the raw user turn |

### Nutrition (C2, proposed, not implemented)

| Tool | Args | Notes |
| --- | --- | --- |
| `get_current_nutrition_plan` | none | Closed projection: meal slot tokens, item strings (bounded, control-free), meal macros. Gated on F-1. |
| `replace_nutrition_plan_item` | meal, item, replacement | `item` must match exactly one entry in that meal (ambiguous → ask). Always REQUIRE_CONFIRMATION. Macro handling per F-3. |
| `add_nutrition_plan_item` / `remove_nutrition_plan_item` | meal, item | Always confirmation. Remove refuses to empty a meal. |
| `undo_last_nutrition_plan_change` | none | Byte-precondition undo, mirroring training. |
| confirm/cancel | none | Shared single PENDING proposal per user, so a pending training change blocks a nutrition proposal and vice versa. |

C1 domain: `app/services/nutrition_plan_mutation/` mirrors `plan_mutation`.
Persistence goes through `nutrition_plan_store` (owner lock + revision
precondition + D1 fence). Additive migration: `NutritionPlan.lineage_id` +
`mutation_version` + a `NutritionPlanMutationRecord` journal. The native
`If-Match` revision stays valid, because it is a digest over the document.

---

## Consent reuse assessment

| Operation | Under `coach.conversation` (r2)? | Disposition |
| --- | --- | --- |
| Coach reads the current **training plan** (exercises, sets, reps, weekdays) | **Yes**: "read your records", categories `training_history`/`fitness_preferences`/`tool_results`. It is the same data class the existing workout-log tools already send. | Reused. No new prompt. B1 ships under it. |
| Coach **edits** the training plan on an explicit request | **Ambiguous.** The text lists the actions Coach performs ("read records, estimate a food, review an image, prepare a meal or workout log saved only after you confirm"); plan edits are not listed. The only exclusion is plan *generation*, and a targeted edit is not generation. | **F-1.** Options: (a) founder + counsel confirm an edit is a "Coach action" within r2 (no new prompt, recommended to pair with confirm-all, F-2); (b) a disclosure r3 that names plan edits, which means a new grant for every user. Until decided, the write flag stays OFF in production. |
| Coach reads or edits the **nutrition plan** | Partly **no**. Meal items express `food_preferences`, which is a `nutrition.plan` category and not a coach category. | **F-1b.** Either the coach disclosure r3 adds `food_preferences`, or Coach nutrition tools require a valid `nutrition.plan` grant as well (a reuse of an existing grant; a user without it gets the existing consent route, never a bypass). |
| New generation of a whole plan | Out of scope for Coach. | Uses existing `training.plan` / `nutrition.plan` grants and the allowance ledger. Coach redirects to that flow; it never splits a regeneration into edits. |

Nothing in B1 bypasses missing authorization. A user without
`coach.conversation` never reaches a Coach turn (403 before quota).

---

## Implementation phases

| Phase | Content | State |
| --- | --- | --- |
| A | Architecture and gap map (this document) | Done |
| **B1** | Training read tool; prompt policy "read before naming"; native `plan_effects`; mobile notice and quick replies | **This PR (+ mobile draft)** |
| B2 | Deterministic confirm/cancel endpoint (no LLM, no quota); qualify G4; apply F-2 | Next, after the F-2 answer |
| C1 | Nutrition mutation domain, migration, journal, undo; no AI | After F-3 |
| C2 | Nutrition Coach tools + shared confirmation | After F-1b |
| D | PG race suite for the new paths; staging E2E with a real provider (the flag's own prerequisite matrix); independent security review; real-device QA on a dev build (not 101119) | Founder GO per window |

---

## Test strategy

- **Unit/state** (`tests/test_adaptive_coach_foundation_b1.py`, 17 cases):
  flag OFF hides and refuses; ON publishes one argument-free read to both
  providers, including while a proposal is pending; calendar order; no
  lineage/version/free text; `not_found` for an owner without a plan while
  another owner has one; unreadable → `PLAN_UNREADABLE`; storage fault →
  unreadable with no open transaction; zero writes (plan bytes, version,
  lineage and journal unchanged); any argument refused; hostile payload
  re-projected from tokens; bounded error vocabulary; prompt policy present;
  **real OpenAI tool loop** for read → `update_training_plan_prescription`,
  with a byte comparison of the whole document except the one field;
  turn-effects changed/pending/unknown; wire shape.
- **Existing suites updated, not weakened**: native Coach success now
  asserts the exact `plan_effects` value; the plan_mutation consumer
  allowlist names `plan_read.py`; the D1 projection spy forwards kwargs.
- **Mobile** (`test/features/coach/presentation/coach_plan_effects_test.dart`,
  10 cases): lenient wire parse; pending outranks updated; unknown is never
  pending; the notice lifecycle (superseded by the next send, cleared by
  dismiss and by an auth transition); quick reply sends the parser token;
  EN/TR labels are parser tokens.
- **Phase D, not done here**: PostgreSQL concurrency for B2 confirm versus
  in-turn confirm; staging real-provider matrix (ambiguous target, wrong
  day, stale proposal, retry after `COACH_REPLY_INCOMPLETE`, cross-account
  probe); device QA reopening Plan after an edit.

---

## Risks

1. **Model ignores the read policy** and still names from memory. This is
   mitigated, not eliminated: server grounding still refuses unknown or
   ambiguous targets, so a wrong name never writes. Measure it in the
   staging matrix.
2. **Name presentation.** The read publishes the stored name when the catalog
   vouches for it, so Coach and the Plan screen agree. A slot whose stored
   name is not catalog-known is `name: null`, and the model must ask.
3. **Cost.** The read adds one tool round (input tokens) on plan turns. It is
   bounded to ≤7 days × ≤20 exercises of tokens and is sent only when the
   model calls it. There is no quota change. Confirmation still costs a turn
   until B2.
4. **Prompt coupling (G4).** Enabling the flag also switches the adaptive
   system prompt.
5. **Consent ambiguity (F-1).** This is the gating risk for any production
   activation of writes.
6. **Session staleness.** An edit to today's exercises stales an ACTIVE
   workout session. This is the documented S1 behavior, and it already
   requires confirmation.

## Release dependencies

- Founder/counsel F-1 (edit scope under r2), F-1b (nutrition content),
  F-2 (confirm-all), F-3 (macro policy).
- Staging qualification of `AI_ADAPTIVE_PLAN_CONTEXT` +
  `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` on a real provider, as the flag
  prerequisites require.
- R6 adult-sequence deploy (every release after 1931ccb4).
- A mobile build ≥ 101120 for the notice. 101119 tolerates `plan_effects`,
  because unknown keys are ignored and its Plan already re-reads after every
  turn.
- No migration in B1. Alembic head unchanged.

---

## B1 record (this PR)

- `app/services/coach_plan_tools/plan_read.py`: `READ_PLAN_TOOL_DEF` and
  `read_current_training_plan(user_id)`, using one `get_active_plan` +
  `parse_plan_document` and a closed projection. No ORM import, no `.query`,
  no logging.
- `executor.execute_plan_read`: the flag gate, fault → `PLAN_UNREADABLE`,
  `_settle_transaction`, and one `[COACH][PLAN_TOOL]` line.
- `coach_plan_tools/turn_effects.turn_plan_effects`: `changed` is this
  request's applied/replayed evidence; `pending_confirmation` is a fresh
  owner-scoped read, `None` on fault, and False with the flag OFF.
- `ai_coach`: the read is published (when ON) ahead of the write family and
  survives a pending proposal. The dispatcher refuses any argument.
- `coach_privacy.tool_result`: a token rebuild (weekday, kind,
  catalog-vouched name, int sets, rep grammar, bounded errors).
- `app/prompts/system.py::PLAN_MUTATION_POLICY`: a "Planı okuma" section,
  and the stale-plan line now points to a re-read.
- Native `POST /api/v1/coach/messages` gains an additive
  `plan_effects.training_plan.{changed, pending_confirmation}`.
  `contract_version` stays 1.
- Unchanged: flag defaults, D1 policies/disclosures, quota, routes, schema,
  migrations, write tools, confirmation policy.
