# Training History Foundation

`app/services/training_history/` — the canonical, deterministic, ORM-based source
of truth for **reading a user's workout history and computing progression
baselines**. Introduced in Sprint 6 PR1 as the foundation the Adaptive Training
Engine builds on (progression analysis, plateau / deload detection, recovery
scoring, adaptive program updates land in later PRs — **not** here).

## Why it exists

Before this package, workout-history windowing (Istanbul-day boundaries via
`utc_day_bounds`), `WORKOUT_COMPLETION_MARKER` exclusion, and volume/session
aggregation were re-implemented inline in at least four places
(`training_generation/time_series_model.py`, `ai_coach.py`, `blueprints/tracking.py`,
`fitx_mcp/server.py`). This package consolidates that into one tested primitive so
future intelligence work has a single, stable contract.

## Layering

| Module | Purity | Responsibility |
|--------|--------|----------------|
| `models.py`   | pure  | Frozen value objects (`WorkoutEntry`, `WeeklyVolume`, `TrainingHistorySummary`). No logic. |
| `queries.py`  | impure (needs app/DB) | `WorkoutLog` reads → normalized `WorkoutEntry` rows. The only DB touch point. |
| `analysis.py` | pure  | Deterministic calculations over value objects. Fixture-free unit tests. |
| `__init__.py` | —     | Public API + `build_training_history_summary` orchestrator. |

## Public API

- `fetch_workout_entries(user_id, start_day, end_day, *, include_markers=False) -> list[WorkoutEntry]`
  — canonical windowed read over `[start_day, end_day]` inclusive (Istanbul days),
  scoped to `user_id`, oldest first. Completion markers excluded unless
  `include_markers=True`.
- `is_completion_marker(exercise_name) -> bool` — the one predicate for the synthetic
  "session completed" marker row. Callers must not re-hardcode the constant.
- `total_volume(entries)` / `total_sets(entries)` — sums over **real** entries (markers excluded).
- `session_days(entries)` / `count_sessions(entries)` — distinct trained days (marker or real).
- `weekly_windows(end_day, weeks)` — week-start dates, oldest first.
- `bucket_by_week(entries, end_day, weeks) -> list[WeeklyVolume]` — non-overlapping 7-day buckets.
- `volume_trend(weekly) -> "up" | "flat" | "down"` — earliest→latest active-week volume, ±5% band.
- `estimated_1rm(weight_kg, reps) -> float` — Epley `w*(1 + reps/30)`; a minimal building
  block for future intensity trends (not progressive-overload logic).
- `build_training_history_summary(user_id, weeks=4, *, end_day=None) -> TrainingHistorySummary`
  — the roll-up entry point. `end_day` defaults to today (Istanbul); pass explicitly for
  hermetic tests. Empty history → `has_data=False`.

## Rules & assumptions

- **Time:** `WorkoutLog.created_at` is naive UTC with no day-key column. Istanbul-day
  boundaries are always derived via `app.timeutil.utc_day_bounds` / `app_date_of` — never
  compare `created_at` raw.
- **Markers:** `/workout/complete` and the coach Pump-Check tool write a synthetic
  `WORKOUT_COMPLETION_MARKER` row (`volume=0`) as a "a session happened" signal. Volume and
  exercise-count reporting exclude it; `session_count` **includes** marker-only days as
  trained days.
- **Determinism:** every `analysis` function is a total function of its inputs; the roll-up
  is deterministic for a fixed `end_day`. No hidden clock or side effects.
- **Scope:** additive foundation. `time_series_model.build_performance_history` and
  `ai_coach._today_workout_totals` delegate to it (behavior unchanged). `tracking.py`,
  `fitx_mcp/server.py`, and `analytics_engine.py` still have their own inline readers —
  converging them is future-PR work.

## Tests

`tests/test_training_history.py` — pure analysis tests (fixture-free) + DB-backed reads via
the `make_user` fixture (windows, volumes, session counts, marker exclusion, user scoping,
empty history, determinism).
