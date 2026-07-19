# Training Progression Layer

`app/services/training_progression/` — the canonical, deterministic
**progression-analysis** layer of the Adaptive Training Engine (Sprint 6 PR2). It
turns the raw training-history foundation (`docs/TRAINING_HISTORY.md`, Sprint 6 PR1)
into normalized progression **signals** that later PRs (adaptive coaching, program
updates) consume. It is pure interpretation on top of the foundation — it never
re-derives Istanbul-day windowing or `WORKOUT_COMPLETION_MARKER` exclusion, it
composes `training_history`.

## Scope

Additive only. **No** schema, **no** route, **no** coach-prompt/UI change — the layer
is a consumable service; wiring it into runtime is future-PR work. It does not build
the adaptive program generator.

## Layering

| Module | Purity | Responsibility |
|--------|--------|----------------|
| `models.py`   | pure  | Frozen value objects (`WeeklyStrength`, `ProgressionReport`). No logic. |
| `analysis.py` | pure  | Deterministic signal functions over foundation value objects. Fixture-free tests. |
| `__init__.py` | impure (reads history via the foundation only) | Public API + `build_progression_report` orchestrator. |

Dependency is strictly one-way: `training_progression` → `training_history`. The
foundation never imports this layer (no cycle).

## Public API

- `build_progression_report(user_id, weeks=4, *, end_day=None) -> ProgressionReport`
  — the entry point. Reads history once through `fetch_workout_entries`
  (`include_markers=True`), then derives every signal purely. `end_day` defaults to
  today (Istanbul); pass explicitly for hermetic tests. Empty history (or
  `weeks <= 0`) → a fully neutral report with `has_data=False`.
- `weekly_best_estimated_1rm(entries, end_day, weeks) -> list[WeeklyStrength]` — per-week
  peak Epley estimate over the foundation's 7-day windows (markers / non-positive load
  excluded → `0.0`).
- `series_trend(values) -> "up" | "flat" | "down"` — earliest→latest active-value direction,
  reusing the foundation's ±5% band. Used for `strength_trend`.
- `is_progressing`, `detect_plateau`, `detect_deload_due`, `assess_consistency`,
  `derive_next_signal` — the pure signal functions (see below).

## The progression model — `ProgressionReport`

| Field | Type | Meaning |
|-------|------|---------|
| `weeks` | int | Window count analysed. |
| `has_data` | bool | Any workout history in the window. |
| `volume_trend` | `up`/`flat`/`down` | Foundation `volume_trend` over weekly real volume. |
| `strength_trend` | `up`/`flat`/`down` | `series_trend` over weekly peak estimated 1RM. |
| `is_progressing` | bool | Either trend is `up` across the **full window** (earliest→latest active value). |
| `is_plateau` | bool | The **recent block** (last `MIN_PLATEAU_WEEKS` active weeks) has stalled (see thresholds). |
| `deload_due` | bool | A long unbroken block has stalled → deload warranted. |
| `load_consistency` | `consistent`/`inconsistent`/`insufficient_data` | Enough regularity to support overload. |
| `next_signal` | enum | The one signal the coach should surface next (precedence below). |
| `weekly_volume` | list[`WeeklyVolume`] | Per-week volume buckets (foundation objects), for transparency. |
| `weekly_strength` | list[`WeeklyStrength`] | Per-week peak estimated 1RM, for transparency. |

## Signal definitions & thresholds

All thresholds are explicit module constants in `analysis.py`.

- **Trend band** — reuses the foundation's public `TREND_BAND = 0.05`. A week-over-week move
  within ±5% is noise (`flat`); volume and strength are judged on the same scale.
- **Plateau** (`detect_plateau`, `MIN_PLATEAU_WEEKS = 3`) — the last 3 *active* volume
  weeks form a flat run (all compressed within the band). Estimated strength is a
  secondary check: if there is enough strength data and it is still trending `up`, the
  user is progressing via intensity, so it is **not** a plateau. Fewer than 3 active
  volume weeks → `False` (neutral).
- **Deload due** (`detect_deload_due`, `MIN_DELOAD_WEEKS = 4`) — the most recent 4
  windows are all active (no rest week — a rest week is itself a deload) **and** training
  is currently plateauing. Deload readiness really depends on fatigue/recovery, which the
  foundation does not carry, so we fire only on this strongest volume-only inference (a
  sustained block that has stalled) and return the neutral `False` otherwise — a healthy,
  still-rising block is never flagged.
- **Consistency** (`assess_consistency`, `CONSISTENCY_MIN_ACTIVE_WEEKS = 3`,
  `MIN_DATA_WEEKS = 2`) — `insufficient_data` when fewer than 2 windows have any trained
  day; else `consistent` when the user trained in ≥ 3 of the last 4 windows, else
  `inconsistent`. A trained window has `session_count > 0` (marker days count — a session
  happened).
- **Next signal** (`derive_next_signal`) — deterministic precedence, so exactly one wins:
  `insufficient_data` → `build_consistency` → `deload` → `plateau` → `progressing` →
  `keep_pushing`. Rationale: no coaching without data; build a consistent base before
  worrying about overload; rest before chasing a stalled lift; a plateau outranks steady
  work; progress is surfaced before the "holding steady" default.

## Overlapping indicators: `is_progressing` vs `is_plateau`

The two booleans deliberately measure **different windows**, so they can both be `True`
for the same report — and that is **not** a contradiction:

- `is_progressing` looks at the **whole** analysed window — the earliest vs the latest
  *active* value, endpoint-to-endpoint (the same shape the foundation's `volume_trend`
  and this layer's `series_trend` use).
- `is_plateau` looks only at the **recent block** — the last `MIN_PLATEAU_WEEKS` active
  volume weeks.

So "jumped early in the block, then flattened over the last three weeks" reads as
`is_progressing = True` **and** `is_plateau = True` — an accurate description of that
history, not a bug.

`next_signal` is the **canonical coaching output**. Its fixed precedence
(`insufficient_data → build_consistency → deload → plateau → progressing → keep_pushing`)
resolves any overlap to exactly one signal, so consumers should surface `next_signal`
rather than reading the raw booleans against each other. Note that the *only* way volume
can be simultaneously rising end-to-end and flat over the recent block is a sustained,
unbroken active block that has stalled — which also satisfies `deload_due`. Therefore
whenever `is_progressing` and `is_plateau` are both `True`, `next_signal` resolves to
`deload`.

## Boundary & neutral-value contract

Following the spec, when a concept cannot be computed reliably the report returns a
safe, explicit **neutral** value rather than a speculative guess:

- Empty history / `weeks <= 0` → `has_data=False`, both trends `flat`, all booleans
  `False`, `load_consistency` and `next_signal` `insufficient_data`.
- Fewer than 2 active values → trend `flat`.
- Fewer than `MIN_PLATEAU_WEEKS` active weeks → not a plateau.
- Fewer than `MIN_DELOAD_WEEKS` weeks, or any rest week in the block → deload not due.
- Bodyweight / zero-load entries count as entries but contribute `0.0` to strength.

## Determinism

Every `analysis` function is a total function of its inputs; the roll-up is
deterministic for a fixed `end_day` (no hidden clock or side effects). User-scoped via
the foundation's `user_id` filter.

## Tests

`tests/test_training_progression.py` — pure signal tests (fixture-free) + DB-backed
roll-up via the `make_user` fixture (trends, plateau, deload, consistency, `next_signal`
precedence, empty history, user scoping, determinism).
