# ADR 0002: Performed Training Authority and Derived Decision Boundary

- Status: **Accepted**
- Date: 2026-10-10
- Decision owners: AxisAI backend and mobile architecture; human repository owner
- Scope: architecture invariant only. No code, schema, migration, API, flag or
  client change is part of this ADR or authorized by it.
- Verified against: `fitness-coach` `origin/main` `2a437ef` (#425)
- Binding contracts it sits under:
  [TI-00](../superpowers/specs/2026-10-05-ti-00-training-intelligence-contract.md),
  [ADAPTIVE_COACHING](../ADAPTIVE_COACHING.md)
- Supersedes: nothing. Replaces the withdrawn broad proposal "Canonical
  Historical Context & Adaptive Decision Boundary" (review verdict REVISION
  REQUIRED, 2026-10-10). That proposal was never committed.

## Status

ACCEPTED on 2026-10-10, after an adversarial architecture review and two delta
re-reviews. The final verdict was "accepted with constraints", and both
constraints are applied in this text: M-1 (transitive grandfathering) and M-2
(`prior_performance` is not a reference reader).

Acceptance fixes the architectural invariant and nothing more:

- it authorizes no implementation, and no code, schema, migration, API, flag or
  client change;
- `WorkoutLog` migration, re-sourcing and retirement remain undecided (F1, F2);
- O1 (classification of Coach `APPLY_NOW`) remains open;
- U1–U3 remain unresolved operational unknowns.

The only next stage it permits is a technical design for the first slice named
under Follow-up Decisions. Implementing that slice needs separate approval.

## Context

The next training slices (history, progression, adaptive guidance) all need
one answer to the question *"what did this user actually lift?"* The
repository gives two answers today, and they do not agree.

Most of the surrounding architecture is already decided and tested:

- plan authority and the mutation boundary: ADAPTIVE_COACHING §§2–3 and §§19–31;
- facts versus derived outputs, read-only recommendations, the server owning
  decisions, provenance, fail-closed reads and typed client rendering: TI-00
  §§1, 10, 14–17.

This ADR does not restate those contracts. It closes the one invariant they
leave open outside the TI feature: **which store is the authority for performed
sets.** TI-00 §10 already answers that question for TI facts. Nothing answers it
for any other consumer.

Product signal, used only as motivation: in customer discovery, self-directed
lifters said reliable access to prior performed weights drives their next load
choice. One user loses history because they overwrite free-text notes. Another
skips logging because capture costs too much. That supports *durable access to
historical performed training*. It does not support observation stores,
recovery scoring, wearable-driven decisions, automatic deload or AI plan
mutation, and this ADR does not derive any of those from it.

## Current Architecture

All statements in this section are verified on `2a437ef` and cited under
Evidence.

**Plan authority.** `TrainingPlan.plan_data` is the current-plan authority. The
active row is selected by `today_facts.get_active_plan`. Targeted changes go
through `app/services/plan_mutation/` with the append-only
`PlanMutationRecord` journal, and whole-plan replacement goes through
`plan_replacement` under `plan_owner_lock`. This ADR does not touch any of it.

**Workout-session execution.** `WorkoutSession` (`app/models.py`) is the
persisted execution lifecycle (ACTIVE → COMPLETED | ABANDONED):

- While a session is ACTIVE, `checkpoint_data` advances only through
  `workout_session/queries.advance_checkpoint`. That is one conditional UPDATE
  guarded by `status = 'active'` and the exact base `checkpoint_revision`, and
  it is the only application write site of `checkpoint_data`.
- Terminal rows reject checkpoints (`execution.reject_terminal`). The final
  checkpoint of a COMPLETED session is therefore immutable in application code;
  it is removed only by account deletion.
- `execution_context_data` (TI-01A V2 context) is committed atomically on the
  same revision.
- `prescription_data` is an immutable start snapshot. `queries.py` captures it
  for scheduled sessions; it is null for older and unscheduled sessions and is
  never backfilled.

**Two performed-training histories exist today.**

| | A. Completed-session checkpoints | B. `WorkoutLog` |
|---|---|---|
| Shape | Per exercise (catalog `exercise_id`) and per set index: `completed`, `reps`, `weight_kg`; null means unknown | One row per name-only exercise: `exercise_name`, `sets`, `reps`, `weight_kg`, `volume`, `created_at`; no session or plan link |
| Writers | Browser and native checkpoint transports through the shared `execution.record_checkpoint` | (1) `workout_completion.service`: one zero-volume `WORKOUT_COMPLETION_MARKER` per completion; (2) Coach `stage_workout_log` → `confirm_and_commit_workout_log` (`ai_coach.py`); (3) `fitx_mcp/server.py` `log_workout_entry` (raw SQL) |
| Readers | TI-03 `training_intelligence` (AST-gated to this source only); `workout_session/prior_performance` (UI prefill first-set defaults, last 24 completed sessions; a pre-D1a derived reader, not a reference for canonical history, see L11) | `training_history` → `training_progression` → `training_planning` (AdaptivePlan) → `weekly_program`; `progress_summary` / `progress_insights` / `progress_history` (including the mobile Progress `deload` state); `adaptive_plan_context` (Coach); `training_generation` time-series, feature and scoring inputs; `ai_coach._today_workout_totals`; `fitx_mcp` metric queries; `tracking.py` / `analytics_engine` / `context_builder`; `workout_state` (non-marker rows mean `execution_recorded`) |

Completion does **not** copy checkpoint sets into `WorkoutLog`. It writes only
the marker (TI-00 §3), so the Sprint 6 engine never sees checkpoint sets and
TI-03 never sees Coach-logged rows. A completed day does not imply that any
performed-set record exists. Session-less completions write only PumpCheck and
the marker, with no sets. Those include every completion while
`FITX_WORKOUT_SESSIONS_ENABLED` is OFF, the legacy browser path without
`session_id`, and the Coach gym-photo completion.

**Notes.** `ExerciseNote` is one row per (owner, catalog exercise), replaced
under a revision CAS, with a tombstone on clear. It keeps no history and is
intentionally excluded from facts, insights and Coach context (TI-00 §9;
`test_ti03_architecture`).

**Wearables and self-reports.** `WearableSleepLog`, `WearableActivityLog`
(including `resting_heart_rate` and `hrv_rmssd`) and `WearableWorkoutLog` are
stored by `app/services/wearables/`:

- `/api/activity/today` returns the day's activity row for display only.
- No sleep row has a reader outside sync and purge.
- No training, progress or planning module reads any wearable table.

`WeeklyCheckIn` self-reported fatigue and sleep quality reach the Coach context
as prose (see Known Legacy Exceptions).

## Decision

**D1 — Target performed-set authority.** For workout-session execution and for
every new performed-set consumer, the **sole canonical performed-set fact** is
the final checkpoint of an owned COMPLETED `WorkoutSession`. Interpreting it
uses only what is stored with it: the session's execution context and its
immutable start prescription snapshot.

- ACTIVE drafts, ABANDONED checkpoints, the current `TrainingPlan`, PumpCheck,
  notes and client caches are not performed-set facts (as TI-00 §10 already
  says for TI).
- A missing or corrupt checkpoint means execution coverage is unavailable. It
  is never reconstructed from the plan or from another store.
- This is a **target invariant**, not a claim about production. Whether this
  path is live for production users is unknown (see Open Questions, U1).

**D1a — The performed-set fact.** Every new performed-set consumer uses this
definition. It binds to TI-00 §10 and adds no second interpretation.

- *Eligibility.* A canonical performed-set fact is one set entry in the final
  `checkpoint_data` of an owned `WorkoutSession` whose status is COMPLETED, where
  that entry has `completed: true`. A COMPLETED session can also hold entries with
  `completed: false`. Those entries are not facts, even when they carry
  prefilled `reps` or `weight_kg`.
- *Null versus zero.* Null `reps` or null `weight_kg` means unknown. Zero means
  zero. Null is never converted to zero, and a null weight never implies
  bodyweight load.
- *Optional enrichment.* `execution_context_data` and `prescription_data` may
  enrich interpretation. If either is absent, an otherwise valid fact stays
  valid, because unscheduled and pre-TI-01A sessions carry no prescription.
- *Dating.* TI-00 §10 governs:
  - `workout_date` determines training-day and window membership;
  - `completed_at` determines completion ordering;
  - a cross-date completion (`app_date_of(completed_at) != workout_date`) is
    excluded under §10's rule;
  - no consumer may silently re-date it.
- *Identity.* A fact is referenced, without any new database identity, by:
  the session `public_id`, the final `checkpoint_revision`, `exercise_id` and
  the set `index`.
- *Semantic limits.* The checkpoint set schema is exactly `index`, `completed`,
  `reps`, `weight_kg` (`workout_session/checkpoint.py` `_SET_FIELDS`), and load
  is in kilograms. Neither the schema nor the TI-01A context (`actual_rir`,
  `tempo_adherence`, `actual_rest`) types a set as warm-up, drop set, failed
  set, superset or circuit. Consumers must not infer those categories. This
  states a limitation; it does not authorize extending the schema.
- *Completion evidence is not set evidence.* A day can have completion evidence
  (a PumpCheck day claim or a marker) and no performed-set facts at all. This is
  true of session-less completions, for example. "No canonical performed-set
  facts" must never be read as "the user did no training".
- *Longitudinal exercise identity (target rule).* The `exercise_id` stored in a
  fact is its historical identity.
  - Once an `exercise_id` is assigned in the code-owned catalog
    (`training_assets/exercises.json`), it must never be reused for a
    semantically different exercise.
  - Retiring an exercise (`active: false`) does not erase its historical
    identity.
  - A historical reader interprets a fact by its stored `exercise_id`. It must
    not treat an inactive identity as unknown, and must not drop the fact just
    because the active-only `resolve_exercise` raises `ExerciseInactive` for it.
  - A derived ruleset may still decide an inactive exercise is not *comparable*
    under its own contract, as TI-03 `comparability.measurement_supported` does.
    That is a derived decision, not rejection of the fact.
  - Current state: the catalog has no inactive entries, and no test enforces
    append-only IDs or forbids reusing them. This is a new target rule and needs
    no exercise-versioning system.

**D2 — `WorkoutLog` is legacy evidence.** `WorkoutLog` is classified as legacy
historical evidence. It holds completion markers, Coach-confirmed name-only
entries, MCP-written entries, and the historical rows current legacy consumers
read.

- It is a different source from checkpoint sets. It must never be merged with
  them, summed with them, deduplicated against them or presented as the same
  record.
- New history, progression or adaptive work must not choose `WorkoutLog` as its
  performed-set source.
- This ADR does **not** authorize removing, migrating, backfilling or
  dual-writing `WorkoutLog`. It does not authorize re-sourcing or breaking any
  existing consumer. Those are separate future decisions.

**D3 — Derived outputs stay derived.** Server-computed projections are not
historical facts:

- Sprint 6 signals, AdaptivePlan, the weekly program and Progress
  summaries/insights over B;
- TI facts, diagnostics and insights, and prior-performance defaults over A.

Where a derived output is persisted or becomes actionable, the existing
contracts govern (TI-00 §§15–16; ADAPTIVE_COACHING §§19–31). This ADR creates no
recommendation persistence model.

**D4 — Minimal provenance.** Where an existing contract requires provenance on
a derived output, the smallest sufficient form is the ruleset or policy
identifier plus opaque source references, with the source revision where the
contract already carries one (for example TI-00 §16 `ruleset_version`,
`session_ref` and `checkpoint_revision`). Full input snapshots are not
required. Legacy outputs without provenance are recorded as gaps; they are not
retrofitted by this ADR.

**D5 — Failure semantics.** Failure to compute a derived output must never
fabricate a successful recommendation, an artificial "stable" or neutral state,
a canonical training fact, or a state mutation. Reading canonical history,
executing a workout and completing it must not depend on a recommendation being
computed successfully. Transport mapping stays with the existing contracts; this
ADR defines no new status codes.

**D6 — Client boundary.** Web and mobile clients do not compute progression
policy, load decisions, recovery decisions or scheduling decisions. They may
render server-owned typed outputs, prefill inputs from server-provided prior
values, and keep local presentation or draft state. Local caches, drafts and
recovery files are never canonical evidence (TI-00 §2, §4, §17).

**D7 — Mutation boundary (narrow).**

- Computing, reading, caching or displaying a derived recommendation has no
  side effect on canonical training state.
- Canonical changes occur only through the existing domain authorities:
  `plan_mutation`, `plan_replacement`, the `workout_session` and
  `workout_completion` services, and the other existing owners.
- Ordinary domain transitions that a user action already authorizes stay valid
  and gain no new confirmation requirement. Starting, checkpointing, completing
  or abandoning a workout, or saving a plan, are examples.
- When the substantive content of a plan change originates from a
  system-derived adaptive recommendation rather than the user's own edit
  intent, the governing acceptance and mutation policy must authorize it.
- How current Coach `APPLY_NOW` behavior is classified in that case is
  **unresolved** (Open Questions, O1). This ADR does not decide it.

**D8 — Extensibility boundary.** This ADR approves no generic Observation table,
event store, event sourcing, CQRS or weakly typed key/value physiological data.

- New contextual signals stay in typed structures owned by their domain.
- A signal becomes a decision input only through a later explicit contract or
  ruleset decision.
- Missing data stays unknown and is never imputed, unless another approved
  contract says otherwise.

## Current State vs Target State

| Concept | Current state (2a437ef) | Target invariant |
|---|---|---|
| Performed sets | Split: completed-session checkpoints (TI-03, prior performance) and the legacy `WorkoutLog` ecosystem (Sprint 6, Progress, weekly program, Coach) | Every new performed-set consumer reads only D1a facts: `completed: true` entries in the immutable final checkpoints of owned COMPLETED sessions |
| `WorkoutLog` | Markers plus Coach- and MCP-written name-only rows; read by many legacy consumers | Legacy evidence: retained, never merged or double-counted, never a new consumer's source |
| Legacy consumers | The baseline graph in "Grandfathered legacy baseline" reads `WorkoutLog` directly, or derives meaning transitively from the output of a module that does | Grandfathered as of `2a437ef`; unchanged until separately re-sourced or retired; no new member derives performed-set meaning from it (Authority Rule 6) |
| Session-less completions | PumpCheck plus marker only; no set data persisted | Unchanged; they contribute no performed-set facts |
| Notes | Overwrite-oriented `ExerciseNote` | Remain notes; never promoted to performed-set history |
| Wearables | Provider rows stored; activity HRV/RHR displayed via `/api/activity/today` | Stored facts of their owning domain; not adaptive-decision inputs unless separately approved |
| Self-reported check-in ratings | Coach context prose; the legacy prompt maps them to advice | Not expanded; deterministic use requires a separate contract |
| Derived outputs | Sprint 6 outputs without ruleset or source refs; TI-03 with `ruleset_version` and refs | New derived outputs follow D3–D5 under the existing contracts; legacy gaps documented, not retrofitted |

The target is not reached by this ADR. Today's production data may sit almost
entirely on the legacy side (U1, U2).

## Authority Rules

1. One store per fact. For new consumers, performed sets come from source A
   only.
2. No cross-source arithmetic. A and B are never summed, unioned, deduplicated
   by heuristic, or joined by exercise name or date.
3. Absence is a state. With no completed checkpoint, the answer is "no recorded
   performed sets", never a value taken from B, the plan or a note. It is not
   "no training": completion evidence is reported separately (D1a). A corrupt
   or unavailable checkpoint is unavailable, never empty history.
4. Immutability is load-bearing. Consumers rely on a COMPLETED checkpoint not
   changing. A future change that edits completed checkpoints needs its own
   decision; it cannot slip in as a bug fix.
5. Provenance labels travel with legacy data. If a later decision allows B to
   be displayed at all, it must be labelled as legacy evidence and never shown
   as set-level history (Open Questions, O3).
6. No transitive legacy meaning. A new performed-set consumer must not:
   - read `WorkoutLog` directly;
   - obtain performed-set meaning indirectly from a grandfathered
     `WorkoutLog`-derived projection (see "Grandfathered legacy baseline");
   - treat AdaptivePlan, Progress or other legacy derived outputs as a
     substitute canonical history source.

   Existing consumers may keep using those outputs for their existing purpose.
   This rule only stops `WorkoutLog`-derived performed-set meaning from
   spreading.

## Derived Output Boundary

A derived output is a function of facts and a ruleset. It is not a fact, even
when the server computed it, cached it or persisted it. The existing
fact/derived split and the read-only rule (TI-00 §10, §14–16) already bind TI.
This ADR extends the *classification*, not those contracts, to every new
training output:

- Derived outputs may cite facts, but other derived outputs may not consume
  them as if they were facts.
- Persisting a derived output creates no new historical authority.
- Persistence or actionability goes through the existing contracts. A new
  persistence model is a separate decision.

## Mutation Boundary

The existing structural guarantees are unchanged:

- AI may request but never own plan persistence (ADAPTIVE_COACHING preamble).
- Typed commands only (§§4, 20).
- The journal, replay and undo (§§12–16).
- Server-owned impact and confirmation (§31).
- TI recommendations never reach `plan_mutation`, `plan_replacement` or
  `mobile_training_generation` (TI-00 §15).

The one gap this ADR records and does not close:

- Under ADAPTIVE_COACHING §31, `replace`, `add` and prescription updates are
  `APPLY_NOW` when no confirmation reason applies.
- The rule that a change must be the user's own explicit request, and not the
  model applying its own or an AdaptivePlan-derived suggestion, lives in prompt
  policy (`PLAN_MUTATION_POLICY`), not in server structure.
- `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` depends on `AI_ADAPTIVE_PLAN_CONTEXT`,
  so adaptive context is always present whenever the tools are.
- The server therefore cannot tell "user asked for X" apart from "user said yes
  to the model relaying a system recommendation for X".

Both flags are `staging_only`. Resolving this is an owner decision (O1).

## Client Boundary

D6 restates TI-00 §§2, 4 and 17 for all new training work. It defines nothing
new:

- Prefilled values (for example `prior_performance`) are server-provided
  conveniences.
- A client draft becomes a fact only through an acknowledged checkpoint on a
  session that then completes.
- Mobile capability gating stays server capability AND the local kill switch.

## Failure Semantics

D5 applies to every new derived output. Existing precedents that already
conform include:

- TI-00 §16: query failure is an HTTP error, never `history_unavailable`
  success.
- LP-04: Progress failure returns a typed 503, never a baseline body.
- Sprint 6 PR5: a weekly-program planner failure returns a structured 500,
  never the neutral recommendation.

Execution and completion already have no dependency on insight generation:
the insight is a separate read route (TI-00 §16), and failure telemetry cannot
block execution (§18). This ADR keeps it that way.

## Relationship to Existing Contracts

- **TI-00**: [`docs/superpowers/specs/2026-10-05-ti-00-training-intelligence-contract.md`](../superpowers/specs/2026-10-05-ti-00-training-intelligence-contract.md),
  with the TI-03 §16 amendment and
  [`docs/TI_03_TRAINING_INSIGHT.md`](../TI_03_TRAINING_INSIGHT.md). It remains
  binding.
- **ADAPTIVE_COACHING**: [`docs/ADAPTIVE_COACHING.md`](../ADAPTIVE_COACHING.md),
  §§1–33. It remains binding.

This ADR is intentionally narrower than both:

- It resolves performed-training authority beyond TI's scope, generalizing
  TI-00 §10 from "TI facts" to "every new performed-set consumer".
- It does not replace their fact, provenance, mutation, client, flag or error
  contracts.
- Where either is more restrictive, the more restrictive contract governs. This
  ADR explicitly supersedes nothing in them.

Also consistent with, and not changed:

- [`TI_01A_EXECUTION_CONTEXT.md`](../TI_01A_EXECUTION_CONTEXT.md)
- [`TRAINING_EXERCISE_NOTES.md`](../TRAINING_EXERCISE_NOTES.md)
- [`WORKOUT_STATE.md`](../WORKOUT_STATE.md)
- [`TRAINING_HISTORY.md`](../TRAINING_HISTORY.md),
  [`TRAINING_PROGRESSION.md`](../TRAINING_PROGRESSION.md),
  [`TRAINING_PLANNING.md`](../TRAINING_PLANNING.md) and
  [`WEEKLY_PROGRAM.md`](../WEEKLY_PROGRAM.md), whose `WorkoutLog` sourcing is
  now classified as legacy
- [`FEATURE_FLAGS.md`](../FEATURE_FLAGS.md)

**ADAPTIVE_COACHING §9 terminology.** §9 ("Completed history is untouched")
uses "completed history" to mean the legacy `WorkoutLog` path. That is what the
term meant when §9 was written; it never meant `WorkoutSession` checkpoints.
Its structural guarantee still holds for the target authority:

- plan state does not itself write performed-training history;
- `checkpoint_data` is written only through the ACTIVE-session checkpoint path
  (`workout_session/queries.advance_checkpoint`) and is never synthesized from
  `TrainingPlan`;
- `prescription_data` is an immutable start snapshot, written only at session
  start (`queries.py`).

This ADR does not rewrite ADAPTIVE_COACHING.

## Known Legacy Exceptions

These are grandfathered existing behavior. New work must not expand them for
convenience, and this ADR fixes none of them.

### Grandfathered legacy baseline

The grandfathered set is defined against reviewed baseline `origin/main`
`2a437ef350b275744ef7c417709a1a01b8d66a2d`. It is every product module that,
at that commit, did one of these:

- (a) read the `WorkoutLog` model directly;
- (b) queried the `workout_log` table by SQL;
- (c) called `training_history.queries.fetch_workout_entries`;
- (d) derived performed-training meaning transitively, at any depth, from the
  output of any module meeting (a)–(c). This includes, but is not limited to,
  meaning derived from:
  - `build_progression_report`, AdaptivePlan, the weekly program and Progress
    outputs;
  - `workout_state`'s `execution_recorded` classification;
  - `analytics_engine.get_nudges` outputs;
  - the `training_generation` performance-history and feature inputs, and the
    generators that consume them.

Examples verified on that commit:

| Path | Modules |
|---|---|
| (a)–(c) direct | `training_history`; `training_progression`; `tracking.py` (workout/heatmap routes); `ai_coach` (`_today_workout_totals` via `fetch_workout_entries`, and `_tool_query_fitx_metrics` `volume_lifted` summing `WorkoutLog.volume`); `context_builder` and `tracking.py` `/dashboard-nudges` via `analytics_engine.get_nudges` (passes the `WorkoutLog` model); `analytics_engine`; `training_generation.time_series_model` (`build_performance_history`); `workout_state.queries`; `fitx_mcp/server.py` raw-SQL reads |
| (d) via `build_progression_report` | `training_planning` (AdaptivePlan) → `weekly_program` (`/api/training/weekly-program`), `adaptive_plan_context` (Coach context, `context_builder`), `coach_handoff`; `progress_summary` (web and `mobile_progress`), `progress_insights`, `progress_history`; `today_facts` → `today_presenter` (web Today insight) |
| (d) via `workout_state` (L4) | consumers of `resolve_workout_state` (`/workout/status`, `/training/bootstrap`, `mobile_today` → `today_guidance_read_model`, `mobile_training`, `plan_facts` (Plan page), Coach current-state block, `barcode`) to the extent they surface `execution_recorded` |
| (d) via `training_generation` performance history | `feature_extractor.build_features` → `training_generation.service.generate_training_plan_candidate` (web `/training-plan`), `mobile_training_generation` (native generate-and-persist), `training_plan_replacement.generation` (native replacement proposals) |
| (d) via Coach context and nudges | Coach turns that assemble `context_builder` output (nudges, AdaptivePlan block, current-state block): `ai_pipeline` (blocking, streaming and the native `mobile_coach` transport) |

The criteria are the definition. The table gives examples only: a module that
met (d) at `2a437ef` is in the baseline whether or not it is listed. These are
not members:

- writers (L2, L3, the completion marker) and account purge (`cli.py`);
- modules that only mention `WorkoutLog` in comments, or import it without
  reading it;
- non-product audit, seed and test tooling, for example `scripts/frontend_audit/*`
  (`seed.py`, `progress_pr3_matrix.py`, `progress_pr5_matrix.py`), equivalent
  seed or audit scripts, and test fixtures. They touch `WorkoutLog` only to
  build audit or test scenarios. They are not product consumers and set no
  precedent for one.

Grandfathering is closed. Baseline members may continue their existing use, for
its existing purpose. The baseline does not include:

- a module that first meets (a)–(d) after `2a437ef`;
- a new purpose given to a baseline member's output.

New performed-set consumers must not obtain performed-set meaning from
`WorkoutLog`, either directly or transitively through any baseline member's
output (Authority Rule 6). A future architecture or allowlist test may freeze
this baseline mechanically. It is not implemented here.

| # | Exception | Where |
|---|---|---|
| L1 | The Sprint 6 engine, Progress (summary/insights/history and the mobile `deload` state), the weekly program, Coach `AdaptivePlan` context and generator inputs derive from `WorkoutLog`, not checkpoints | `training_history/queries.fetch_workout_entries` and its callers |
| L2 | Coach-confirmed entries are name-only. `stage_workout_log` defaults missing or invalid sets/reps to 3/10 and stores unknown load as `0` kg, against the "null is never zero" rule | `ai_coach.py` stage/commit tools |
| L3 | `fitx_mcp` `log_workout_entry` writes `workout_log` by raw SQL, outside the ORM writers | `fitx_mcp/server.py` |
| L4 | `workout_state` treats non-marker `WorkoutLog` rows as `execution_recorded` evidence | `app/services/workout_state/` |
| L5 | `WeeklyCheckIn.coach_feedback` (LLM output) is stored on the same row as check-in facts | `app/models.py`, web `/checkin` |
| L6 | The web `/update-weight` keeps the same-Istanbul-day full-row overwrite | `weekly_checkin.record_legacy_weight_update` (LP16-A) |
| L7 | With `AI_ADAPTIVE_PLAN_CONTEXT` OFF, the legacy Coach prompt tells the model to cut volume/intensity and suggest deload from self-reported sleep ≤2 or fatigue ≥4. This is advisory prose, not a deterministic contract | `app/prompts/system.py` `_LEGACY_CHECKIN_INSTRUCTION` |
| L8 | Sprint 6 outputs carry no ruleset id or source refs. The Coach AdaptivePlan block carries only a prompt `schema_version`, and `weekly_program` deliberately carries none | `adaptive_plan_context.py`; `docs/WEEKLY_PROGRAM.md` F5 |
| L9 | Completions without a session, and sessions started before TI-01A (null `prescription_data`), carry no performed-set or prescription fact | `workout_completion`, `workout_session/queries.py` |
| L10 | Real checkpointed sets can be stranded in an ACTIVE session that never becomes COMPLETED. Examples: the Coach gym-photo completion passes no `session_id`, so the session is not terminalized; a rejected proof (422) leaves the session ACTIVE. Only COMPLETED sessions yield facts, and ACTIVE and ABANDONED checkpoints are excluded by design. Some sets the user actually performed are therefore outside canonical history. This ADR does not authorize recovering, importing or promoting them; that needs a separate decision | `ai_coach.py` gym-photo `CompleteWorkoutCommand` (no `session_id`); `workout_completion/service.py`; TI-00 §3 |
| L11 | `prior_performance` predates D1a. It reads source A under its own, looser rules to produce UI prefill defaults, and it is not a reference implementation of canonical performed-set history. See below | `app/services/workout_session/prior_performance.py` |

### `prior_performance` is not a reference reader

`workout_session/prior_performance` is a legacy derived reader. It supplies
UI prefill defaults (first-set weight and reps per exercise) and nothing else.
It reads the same store as D1a, but it predates D1a and differs from the
canonical fact rules. On `2a437ef`:

- it parses each checkpoint with `load_snapshot` (JSON only), not the canonical
  re-validation (`parse_stored_exercises`). A checkpoint that fails to load is
  skipped silently;
- inside a loaded checkpoint, malformed exercise or set entries are skipped one
  by one and the remainder is used (partial salvage);
- it keeps sessions whose `completed_at` falls on a different date from
  `workout_date`;
- it keeps sessions with null `completed_at`, ordering them last.

For canonical performed-set history, D1a and TI-00 §10 govern instead:

- a checkpoint that fails canonical re-validation is unavailable as a whole for
  new performed-set consumers;
- a partially salvaged checkpoint is never presented as canonical history;
- a session with null `completed_at` cannot satisfy TI-00 §10 dating and is
  excluded;
- cross-date sessions follow TI-00 §10: excluded, never re-dated.

TI-03 `training_intelligence/facts.parse_session` applies these rules. New
performed-set consumers must not reuse `prior_performance` selection as
canonical history. This ADR does not change `prior_performance` and does not
require it to converge; that would be a separate decision.

## Alternatives Considered

**A. Keep both histories indefinitely without naming an authority.** Rejected.
Each new consumer would pick a source by convenience, and the two sources
already give different answers for the same user and week. That is the
failure this ADR exists to stop.

**B. Make `WorkoutLog` the canonical performed-set store.** Rejected on
repository evidence:

- it has no set index, completion flag, catalog identity, session or plan link,
  or revision;
- unknown load is stored as 0 and the writers default counts;
- it has three writers, one of them raw SQL;
- adopting it would mean either copying checkpoints into a lossy shape at
  completion or demoting the store TI-00 already froze as canonical, which
  contradicts TI-00 §§2, 10 and 14.

**C. Make completed `WorkoutSession` checkpoints the target authority while
grandfathering `WorkoutLog`.** **Selected.** It uses the store TI-00 §10
already treats as canonical, under the rules TI-03 `facts.parse_session` already
applies. (`prior_performance` reads the same store but is not a reference; see
L11.) It needs no schema change. It
keeps legacy consumers working, and leaves migration or retirement as an
explicit later decision gated on production evidence.

**D. Introduce a new generic history, event or observation store.** Rejected as
premature. No current consumer needs it. It would add a third history, and it
would run against D8 and the narrow, typed authority style of both binding
contracts.

**E. Defer all architecture and implement history locally per feature.**
Rejected. It leaves the next slice free to choose an authority by accident, and
that is alternative A by default.

## Consequences

### Positive

- One unambiguous answer for every new performed-set consumer.
- No double counting by construction.
- New consumers read the same store, under the same rules, as TI-03. The
  prior-performance defaults read that store under looser, pre-D1a rules (L11).
- Legacy features keep working; nothing is migrated under uncertainty.
- Wearable and self-report data cannot quietly become decision inputs.

### Negative / Costs

- Two histories persist for an unknown period, and the product reports
  training through two lenses: Sprint 6 Progress versus TI.
- New features have nothing to show for users whose training sits only in
  `WorkoutLog` or in session-less completions. Unlocking that needs a later
  decision (O3, F2).
- Out-of-app or retroactive performed training still has no target-authority
  write path (O4).

### Risks

- If the workout-session path is not live in production (U1), the target
  authority holds little or no production data. New history features would be
  correct but sparse.
- Users may see Progress (Sprint 6, `WorkoutLog`) and TI (checkpoints) disagree.
  This ADR documents that and does not resolve it (F1).
- The prompt-only boundary for Coach `APPLY_NOW` (O1) remains until the owner
  decides.

## Non-Decisions

This ADR does **not** decide:

- a recovery score, recovery algorithm, or HRV, RHR, sleep or stress as
  decision inputs;
- Garmin or Apple Health;
- automatic deload, automatic rescheduling or automatic plan mutation;
- hybrid-athlete planning, Coach product workflows, or ML or LLM adaptation;
- generic observation or event storage, event sourcing or CQRS;
- recommendation persistence;
- history UI in any form: web, browser or mobile;
- `WorkoutLog` retirement, backfill or migration, or Sprint 6 convergence;
- nutrition adaptation.

Existing provider and wearable records are stored facts of their owning domain.
This ADR does not approve them as adaptive-decision inputs.

## Open Questions

Operational and migration unknowns. They do not block the invariant, but they
**block any claim that migration, re-sourcing or retirement is safe**.

- **U1** Production state of `FITX_WORKOUT_SESSIONS_ENABLED`. The registry says
  `staging_only`, and flags live in the host `.env`, which the repository
  cannot see. Not queried; production access is out of scope for this review.
- **U2** Production volume of non-marker `WorkoutLog` rows (Coach and MCP
  writers), and whether the MCP writer is used in production at all.
- **U3** Even with sessions ON, what share of production completions are
  session-linked (and so carry checkpoints) rather than session-less?

Owner decisions:

- **O1** Classification of Coach `APPLY_NOW` (replace, add, prescription update)
  when the substantive content comes from AdaptivePlan or another
  system-derived recommendation that the user assents to. Options include
  status quo with prompt policy only, structural confirmation, or a separate
  acceptance policy. It must be decided before
  `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` or `AI_ADAPTIVE_PLAN_CONTEXT` graduate
  beyond `staging_only`, and before any TI insight is placed in Coach context
  while plan tools are available.
- **O2** Should a future decision bring Coach-reported or other out-of-app
  performed training into the target authority, and if so, through which
  typed path?
- **O3** May a future read surface show legacy `WorkoutLog` rows as separately
  labelled legacy evidence? The default under this ADR is no.

## Follow-up Decisions

None of these is authorized here.

- **F1** Re-source or retire the Sprint 6 / Progress / weekly-program / Coach
  consumers (L1). This needs U1–U3 evidence.
- **F2** Represent legacy `WorkoutLog` history (keep, label, migrate or retire).
  This needs U2 and O3.
- **F3** The Coach adaptive-mutation acceptance policy (O1).
- **F4** Any contextual signal (wearable, self-report) as a decision input,
  through its own contract and ruleset.

First follow-up slice: a read-only per-exercise historical-performance
projection over completed `WorkoutSession` checkpoints. Acceptance permits a
**technical design** for this slice within the constraints below.
**Implementation is NOT authorized**; it needs separate approval. The slice
must:

- read D1a facts only: owned COMPLETED sessions and `completed: true` sets;
- keep null as unknown and zero as zero, and never infer bodyweight load;
- follow TI-00 §10 for cross-date sessions, and exclude sessions with null
  `completed_at`;
- use the stored `exercise_id` as historical identity;
- report a checkpoint that fails canonical re-validation as unavailable as a
  whole: no partial salvage, and never empty history;
- not reuse `prior_performance` selection as canonical history (L11);
- never read `WorkoutLog` or legacy derived projections as facts;
- be read-only, with no migration;
- stay out of Coach context until O1 is resolved.

## Evidence / References

All references are on `origin/main` `2a437ef`.

- Models: `app/models.py`: `WorkoutSession` (`checkpoint_data`,
  `execution_context_data`, `prescription_data`, status check), `WorkoutLog`,
  `WORKOUT_COMPLETION_MARKER`, `ExerciseNote`, `WeeklyCheckIn`,
  `WearableSleepLog`, `WearableActivityLog`, `WearableWorkoutLog`.
- Checkpoint authority: `app/services/workout_session/queries.py`
  (`advance_checkpoint`, the only `checkpoint_data` write; prescription capture
  at start), `execution.py`, `checkpoint.py`, `prescription.py`.
- Readers of source A: `app/services/training_intelligence/`
  (`tests/test_ti03_architecture.py` forbids `WorkoutLog`, `TrainingPlan`,
  `PumpCheck` and `ExerciseNote`);
  `app/services/workout_session/prior_performance.py`
  (`tests/test_prior_performance.py`): a pre-D1a prefill reader and not a
  reference (L11). It uses `checkpoint.load_snapshot`, where TI-03
  `facts.parse_session` uses `parse_stored_exercises`.
- Writers of source B: `app/services/workout_completion/service.py` (marker);
  `app/services/ai_coach.py` `_tool_stage_workout_log` /
  `_tool_confirm_and_commit_workout_log`; `fitx_mcp/server.py`
  `log_workout_entry`.
- Readers of source B: `app/services/training_history/queries.py`
  `fetch_workout_entries` and its importers (`training_progression`,
  `training_planning`, `weekly_program`, `progress_summary`,
  `progress_insights`, `progress_history`, `adaptive_plan_context`,
  `training_generation/*`, `ai_coach`, `tracking.py`, `analytics_engine`,
  `workout_state`); `ai_coach` `_tool_query_fitx_metrics` direct `WorkoutLog.volume` sum;
  `fitx_mcp/server.py` raw-SQL `workout_log` reads; transitive consumers
  `today_facts`, `today_presenter` and `coach_handoff` (via
  `progress_insights`); the generators fed by `training_generation.feature_extractor`.
  The full transitive baseline, and the non-product tooling it excludes, is
  under Known Legacy Exceptions.
- Catalog identity: `app/services/exercise_catalog.py` `resolve_exercise`
  (`ExerciseInactive`); `training_assets/exercises.json`;
  `tests/test_training_catalog_identity_contract.py`.
- Performed-set schema: `app/services/workout_session/checkpoint.py`
  `_SET_FIELDS`; `context.py` `_FIELDS`; TI-03 `facts.py` (completed-only,
  null-excluding volume) and `comparability.py`.
- Stranded sessions (L10): `app/services/ai_coach.py` gym-photo
  `CompleteWorkoutCommand` without `session_id`;
  `app/services/workout_completion/service.py`.
- Notes: `app/services/exercise_notes.py`.
- Wearables: `app/services/wearables/{adapters,sync}.py`;
  `app/blueprints/tracking.py` `today_activity`.
- Flags: `app/feature_flags.py`:
  - `FITX_WORKOUT_SESSIONS_ENABLED`, `AI_ADAPTIVE_PLAN_CONTEXT` and
    `AI_COACH_PLAN_MUTATION_TOOLS_ENABLED` are `staging_only`, and the last
    depends on `AI_ADAPTIVE_PLAN_CONTEXT`;
  - `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` and
    `FITX_TRAINING_INSIGHTS_ENABLED` are `shipped_dark`.

  Activation runbook:
  [`WORKOUT_SESSION_ACTIVATION.md`](../WORKOUT_SESSION_ACTIVATION.md) §0.
- Coach policy: `app/prompts/system.py` (`PLAN_MUTATION_POLICY`,
  `_LEGACY_CHECKIN_INSTRUCTION`, `_ADAPTIVE_CHECKIN_INSTRUCTION`);
  `app/services/coach_plan_policy`; ADAPTIVE_COACHING §31.
- Contracts: TI-00 §§2, 3, 9, 10, 14–17, 26; ADAPTIVE_COACHING §§2, 9, 10, 28,
  31.
