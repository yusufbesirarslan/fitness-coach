# Adaptive Planning Layer

`app/services/training_planning/` — the canonical, deterministic
**adaptive-planning** layer of the Adaptive Training Engine (Sprint 6 PR3). It turns
the progression engine's normalized signals (`docs/TRAINING_PROGRESSION.md`, Sprint 6
PR2) into a single weekly **plan recommendation** — what kind of week comes next,
whether volume/intensity should move, and whether the user is ready for overload or
due a maintenance/deload week. It is pure composition on top of the progression
layer — it never re-derives windowing, marker exclusion, trends, or signal
precedence; it composes `training_progression`.

**Not the plan generator.** `training_generation/` + the `TrainingPlan` model produce
LLM-authored workout *content* at `POST /training-plan` (level classification + style
guidelines feeding a prompt). This layer emits a deterministic *adjustment
recommendation* object (`AdaptivePlan`) for future AI-coach/UI wiring. The two share
no vocabulary and neither imports the other.

## Scope

Additive service + one behavior-preserving convergence. **No** schema, **no** new
route, **no** coach-prompt/UI change — the layer is a consumable service; wiring it
into runtime is future-PR work. The one runtime change in this PR is
`GET /api/progress/workout` (`app/blueprints/tracking.py`) now reading `WorkoutLog`
through the foundation's `fetch_workout_entries` (byte-identical output,
characterization-tested).

## Layering

| Module | Purity | Responsibility |
|--------|--------|----------------|
| `models.py`   | pure  | Frozen value object (`AdaptivePlan`). No logic. |
| `analysis.py` | pure  | Deterministic decision rules over a `ProgressionReport`. Fixture-free tests. |
| `__init__.py` | impure (reads signals via `build_progression_report` only) | Public API + `build_adaptive_plan` orchestrator. |

Dependency is strictly one-way: `training_planning` → `training_progression` →
`training_history`. Neither lower layer imports this one (no cycle).

## Public API

- `build_adaptive_plan(user_id, weeks=4, *, end_day=None) -> AdaptivePlan` — the
  entry point. Builds one `ProgressionReport` (which reads history once through the
  foundation), then derives the plan purely. `end_day` defaults to today (Istanbul);
  pass explicitly for hermetic tests. Empty history (or `weeks <= 0`) → the fully
  neutral plan.
- `derive_adaptive_plan(report) -> AdaptivePlan` — the pure core; the whole decision
  engine is testable without a DB.
- `derive_week_focus`, `derive_volume_action`, `derive_intensity_action`,
  `volume_delta_for`, `derive_reason_codes` — the individual pure rules (see below).

## The plan model — `AdaptivePlan`

| Field | Type | Meaning |
|-------|------|---------|
| `weeks` | int | Window count analysed (echoed from the report). |
| `has_data` | bool | Any workout history in the window (echoed). |
| `week_focus` | enum | The kind of week to run next: `overload` / `steady` / `maintenance` / `deload` / `build_consistency` / `insufficient_data`. |
| `volume_action` | enum | `increase` / `hold` / `decrease`. |
| `intensity_action` | enum | `progress` / `hold` / `deload`. |
| `volume_delta_pct` | float | Signed fraction; `0.0` whenever `volume_action == "hold"`. |
| `overload_ready` | bool | True exactly when `week_focus == "overload"`. |
| `maintenance_recommended` | bool | True exactly when `week_focus == "maintenance"`. |
| `reason_codes` | tuple[str] | Ordered locale-neutral machine codes; position 0 is always the primary cause. |
| `progression` | `ProgressionReport` | The full underlying report, embedded — trends and weekly series without a second history read. |

The "safest next adjustment" is the (`week_focus`, `volume_action`,
`intensity_action`, `volume_delta_pct`) tuple — there is deliberately no second
summary enum that could drift against `week_focus`.

## Decision rules & constants

All constants are explicit in `analysis.py`:

- `VOLUME_INCREASE_STEP = 0.05` — conservative weekly volume increase when
  overload-ready (deliberately well under the common ≤10% progressive-overload
  guideline).
- `DELOAD_VOLUME_CUT = 0.40` — a deload week trains at ~60% of recent weekly volume
  (the conservative middle of the standard 50–60% band).

The mapping is 1:1 from `next_signal`:

| `next_signal` | `week_focus` | volume | intensity | `volume_delta_pct` | `overload_ready` | `maintenance_recommended` | primary reason code |
|---|---|---|---|---|---|---|---|
| `insufficient_data` | `insufficient_data` | hold | hold | 0.0 | no | no | `insufficient_history` |
| `build_consistency` | `build_consistency` | hold | hold | 0.0 | no | no | `inconsistent_training` |
| `deload` | `deload` | decrease | deload | `-DELOAD_VOLUME_CUT` | no | no | `deload_due` |
| `plateau` | `maintenance` | hold | hold | 0.0 | no | **yes** | `plateau_detected` |
| `progressing` | `overload` | increase | progress | `+VOLUME_INCREASE_STEP` | **yes** | no | `progressing` |
| `keep_pushing` | `steady` | hold | hold | 0.0 | no | no | `steady_state` |

Unknown/future signal strings fall back to the fully neutral
`insufficient_data` focus rather than guessing.

Two deliberate, conservative judgment calls:

- **`plateau` → maintenance (hold/hold), not an intensity push.** A plateau that did
  *not* trigger deload means short history or a recent rest week. Without
  fatigue/recovery data (which the foundation does not carry) we cannot distinguish
  "stalled because under-recovered" from "stalled because under-stimulated" — and
  pushing intensity in the fatigued case is the harmful branch. The safe explicit
  answer is a consolidation week: `maintenance_recommended=True`, everything held.
- **`keep_pushing` → steady (hold/hold), even when `volume_trend == "down"`.**
  Recommending an increase "back to baseline" on ambiguous signals is speculative;
  the down-trend nuance is carried by reason codes instead.

## One precedence, not two

`next_signal` is already the single canonical winner of the progression layer's
documented precedence (`insufficient_data → build_consistency → deload → plateau →
progressing → keep_pushing`). This layer maps that signal 1:1 and **never**
re-derives decisions from the raw report booleans — a second precedence ladder would
be a second source of truth that could drift. The safety invariants come for free
from upstream: `next_signal == "progressing"` implies consistent training with no
deload/plateau pending, so "never recommend an increase to an inconsistent user"
holds by construction (and is pinned by
`test_never_increase_without_consistent_progression`).

## Reason codes

`derive_reason_codes(report)` returns an ordered tuple: the focus's primary code
first, then `volume_trend_down` and `strength_trend_down` appended in that fixed
order whenever the report shows them — uniformly for every focus. Codes are
locale-neutral machine strings; Turkish UI copy is a later PR's concern. The neutral
default plan carries `("insufficient_history",)` so even a default-constructed
`AdaptivePlan` explains itself.

## Magnitude guidance: volume only

`volume_delta_pct` is the single quantified magnitude. Intensity magnitude is
deliberately **not** modelled: a single global intensity percentage is meaningless
without per-exercise data (rep ranges and plate increments live in the generation
layer's domain), and `WorkoutLog` has no per-set granularity. Volume is the one knob
the history actually measures, so it is the one magnitude quantified. Per-lift
intensity guidance is an intentional limitation left for a later PR.

## Boundary & neutral-value contract

When a decision cannot be computed reliably the plan returns a safe, explicit
**neutral** value rather than a speculative guess:

- Empty history / `weeks <= 0` → `AdaptivePlan(weeks=…)` defaults: focus
  `insufficient_data`, both actions `hold`, delta `0.0`, no flags,
  `reason_codes == ("insufficient_history",)`.
- Marker-only history (attendance without measurable load) reads `keep_pushing`
  upstream → `steady`, hold everything — markers prove attendance, never justify
  overload.
- `weeks > 4`: consistency is still judged over the last four windows (inherited
  from the progression layer); the plan adds no new window handling.

## Determinism

Every `analysis` function is a total function of its inputs; the roll-up is
deterministic for a fixed `end_day` (no hidden clock or side effects). User-scoped
via the foundation's `user_id` filter.

## Tests

`tests/test_training_planning.py` — pure decision-rule tests (fixture-free: mapping,
actions, deltas, flag exclusivity, the keys-off-`next_signal`-only invariant, reason
codes, neutrality) + DB-backed roll-up via the `make_user` fixture (overload, deload,
maintenance, build-consistency, marker-only, empty history, `weeks=0`, user scoping,
determinism). Convergence characterization for `/api/progress/workout` lives in
`tests/test_progress_api.py`.

## Sprint 6 PR4 — AI Coach contract

### Serializer ownership and Version 1 evolution

`app/services/adaptive_plan_context.py` is the only component allowed to transform
`AdaptivePlan` into prompt-ready data. The Version 1 contract is compact canonical
JSON with fixed field names/order, complete non-null fields, ordered reason codes,
and additive-only evolution. Consumers ignore unknown/appended fields and never infer
meaning from absence. Breaking semantics require a new `schema_version`.

### Read-only consumer policy

The Coach is read-only: it explains, personalizes, motivates, educates, and presents
the deterministic plan. It never reconstructs progression, overload, plateau,
deload, volume, or intensity decisions. Future runtime consumers either consume
`AdaptivePlan` directly or use this sole serialized contract.

### Prompt authority: one planning source

The read-only policy is enforced by the system prompt itself, not only by the
serialized block. `app/prompts/system.py` derives `ADAPTIVE_COACH_SYSTEM_PROMPT`
from the legacy `COACH_SYSTEM_PROMPT` by:

- rewriting the injury rule so the Coach personalizes exercise selection and
  contraindications, but no longer adapts volume/intensity itself (and stops and
  refers to a health professional when the plan cannot be presented safely);
- rewriting the weekly check-in rule so sleep, fatigue, and progressive-overload
  answers are recovery/safety/education context only — never raw inputs the Coach
  turns into a deload, overload, volume, intensity, or progression decision;
- appending an explicit authority block that names the contract as the single
  canonical planning decision and forbids recomputing, re-deriving, or overriding
  those five decision classes.

Without this rewrite the enabled path would carry two planning authorities: the
deterministic plan and the legacy heuristics ("cut volume when fatigue ≥ 4") the
same prompt still granted the model.

`build_coach_system(language, adaptive_plan_context=...)` selects between the two
prompts. The switch is an explicit argument threaded from `ai_coach`
(`_adaptive_plan_context_enabled()` reads `AI_ADAPTIVE_PLAN_CONTEXT` at call time;
`ai_stream` inherits it through `_build_bedrock_system`), **never** inferred from the
context text: the composed context also carries user-written fields
(`manage_user_memory` values, friend activity), so a string that reproduces the
canonical header must not be able to flip the system prompt or pass a forged block off
as canonical. Both providers take the same decision — the OpenAI message array and both
Bedrock `system` shapes (plain and prompt-cached). Default OFF: `build_coach_system()`
returns the untouched legacy prompt, so no disabled-path bytes change. Pinned by
`tests/test_prompt_builder.py`
(`test_coach_system_keeps_adaptive_plan_as_sole_planning_authority`,
`test_planning_authority_comes_from_flag_not_from_context_text`) and by
`tests/test_adaptive_plan_context.py::test_prompt_authority_is_flag_driven_on_both_providers`.

### Rollout and rollback contract

`AI_ADAPTIVE_PLAN_CONTEXT` defaults OFF and is the only rollout gate. OFF performs no
plan construction/execution, serialization, adaptive logging, or prompt modification.
Setting it back to `0` restores the pre-PR4 runtime behavior without a code revert.

### Enabled fallback, logging, and payload budget

Enabled failures catch `Exception` (not process-level `BaseException`), restore
session usability when necessary, and emit the complete neutral
`AdaptivePlan(weeks=0)` contract. Logs are generic debug lifecycle events and contain
no user or training data. The normalized payload excludes rows and weekly/history
series; its prompt-footprint target is approximately 100-160 tokens.

### Exact Version 1 key order

| Object | Keys in canonical order |
| --- | --- |
| Top level | `schema_version`, `source`, `plan`, `progression` |
| `plan` | `weeks`, `has_data`, `week_focus`, `volume_action`, `intensity_action`, `volume_delta_pct`, `overload_ready`, `maintenance_recommended`, `reason_codes` |
| `progression` | `volume_trend`, `strength_trend`, `is_progressing`, `is_plateau`, `deload_due`, `load_consistency`, `next_signal` |

The dependency direction is strictly one-way:
`training_history -> training_progression -> training_planning ->
adaptive_plan_context/context_builder -> coach`.

## Sprint 6 PR5 — weekly-program consumer

`app/services/weekly_program/` is the planner's **second** approved consumer, after
the PR4 prompt contract. It translates `AdaptivePlan` into a structured weekly
recommendation and publishes it over one read-only endpoint,
`GET /api/training/weekly-program` — see `docs/WEEKLY_PROGRAM.md`.

The two consumers do not compete:

| | `adaptive_plan_context` (PR4) | `weekly_program` (PR5) |
|---|---|---|
| Output | Serialized v1 JSON block | Frozen `WeeklyProgramRecommendation`, projected to a dict at the edge |
| Audience | Coach system prompt | HTTP clients (`GET /api/training/weekly-program`) |
| Gate | `AI_ADAPTIVE_PLAN_CONTEXT` (default OFF) | none — `@require_auth` only; no flag needed for a read-only surface |
| Versioning | `schema_version: 1`, additive-only | none — additive-only by rule; see `docs/WEEKLY_PROGRAM.md` (F5) |
| On planner failure | Neutral contract + session rollback, so the coach still answers | Structured JSON 500 — a neutral recommendation is a valid user state and must not stand in for an outage |

The failure difference is deliberate, and it follows from the audience. The coach
degrades usefully with a neutral block; an API client cannot tell a neutral
recommendation caused by an outage apart from one caused by an empty history, so the
endpoint reports the failure instead of disguising it.

`adaptive_plan_context` remains the **sole** owner of any `AdaptivePlan` → JSON
serialization; PR5's `payload.py` serializes the *recommendation*, never the plan, and
does not import `AdaptivePlan` — so `test_adaptive_plan_prompt_serializer_has_one_owner`
still finds exactly one prompt contract. Both are read-only presenters: neither
recomputes overload, deload, volume, intensity, or progression. The endpoint likewise
pins `weeks`/`end_day` to the service defaults rather than reading them from the query
string; the analysis window is a planning knob, not an HTTP parameter.

`ADAPTIVE_PLAN_IMPORT_ALLOWLIST` (`tests/test_dependency_boundaries.py`) therefore now
names three files — `adaptive_plan_context.py` plus `weekly_program/__init__.py` and
`weekly_program/analysis.py`. Any further importer is an explicit, reviewable decision,
not a default.

## Sprint 6 PR6.1 — UI rollout boundary (no third consumer yet)

PR6.1 adds a **presentation** boundary on `/training`, not a third planner consumer.
Behind the default-OFF `WEEKLY_PROGRAM_UI_ENABLED` flag it renders one inert mount
shell plus a no-op initializer; it fetches nothing, embeds no recommendation data, and
imports neither `weekly_program` nor `training_planning` from the page route (pinned by
an AST guard in `tests/test_weekly_program_ui.py`). The allowlist above is therefore
unchanged. When PR6.2 wires the card up it will consume the existing
`GET /api/training/weekly-program` response over HTTP — which needs no allowlist entry
either, because reaching the planner is exactly what that endpoint already does on the
client's behalf. Details: `docs/WEEKLY_PROGRAM.md` → *UI rollout (Sprint 6 PR6)*.

`WEEKLY_PROGRAM_UI_ENABLED` and `AI_ADAPTIVE_PLAN_CONTEXT` are independent by
construction: separate env names, separate config keys, separate read paths. Enabling
the UI cannot change a prompt, and enabling the coach context cannot render a shell.
