# TD-01 PR1 — Canonical exercise performance history service

Baseline: `6f5668d9bb8b7cf75f9cbc9ddcb11a251bff8b6f` (main, TD-01 accepted, #428).
Branch: `feat/canonical-performance-history-service`. Dedicated worktree:
`/Users/yusuf/develop/fitness-coach-history-pr1`.

Authority: [ADR 0002](adr/0002-performed-training-authority.md) (Accepted) and
[TD-01](superpowers/specs/2026-10-10-td-01-historical-performance-design.md)
(Accepted). This record describes what PR1 ships. It does not amend either
document, and it authorizes nothing beyond PR1.

## What PR1 is

An **internal, read-only service foundation**:
`build_exercise_history(user_id, exercise_id) -> ExerciseHistory` in
`app/services/exercise_performance_history/`.

**Production callers: ZERO.** There is no route, HTTP contract,
`contract_version`, feature flag, mobile/browser client, Coach, Today, Progress
or Nutrition consumer. Guard H-A8
(`tests/test_exercise_performance_history_architecture.py`) fails if any module
outside the package and `tests/` imports the package or calls the service. A
later, separately approved slice must change that guard explicitly.

| Module | Role |
|---|---|
| `models.py` | Bounds (56/64/8), closed tokens, frozen value objects, `ProjectionInvariantError` |
| `queries.py` | Exactly one owner-scoped `SELECT` over `workout_session` (five columns) |
| `selection.py` | Pure classification, fact extraction, state derivation, invariant check |
| `__init__.py` | Orchestrator: identity → `app_today()` once → query → selection → invariants |

Catalog helper (shared module, additive): `exercise_catalog.resolve_historical_exercise`
plus `ExerciseUnknown(ExerciseIdentityInvalid)`. A malformed ID (pattern, length
over 64, non-string) raises exactly `ExerciseIdentityInvalid`. A well-formed ID
the catalog never assigned raises exactly `ExerciseUnknown`. An inactive ID is
served. `resolve_exercise` is unchanged.

## Canonical semantics (as implemented)

- **Source.** Only the final `checkpoint_data` of the owner's COMPLETED
  `WorkoutSession` rows. A fact is a set with `completed is True`. Null
  `reps`/`weight_kg` stay null, and zero stays zero. Nothing is inferred
  from bodyweight.
- **Query.** `user_id = :owner AND status = 'completed' AND workout_date IN (57
  Istanbul day keys ending at app_today())`, `ORDER BY completed_at DESC NULLS
  LAST, public_id DESC` in byte order, `LIMIT 64`.
  - The tie-break is `COLLATE "C"` on PostgreSQL and the default `BINARY` on
    SQLite. Any other dialect raises.
  - Selected columns: `public_id`, `workout_date`, `completed_at`,
    `checkpoint_revision`, `checkpoint_data`.
  - The exercise ID never enters SQL.
- **Classification, first match wins.** Rules are applied in this order:
  1. An unreadable `workout_date` → `session_invalid` (the marker's date is
     `None`).
  2. A null `completed_at` → excluded (`missing_completed_at`).
  3. `app_date_of(completed_at) != workout_date` → excluded (`cross_date`).
  4. SQL NULL at revision 0 → `checkpoint_missing`.
  5. `InvalidSessionRequest` from `parse_stored_exercises(load_snapshot(...))`
     → `checkpoint_invalid`. This includes `""`, `" "` and NULL at revision ≥ 1.
  6. Otherwise the row is eligible.

  Date exclusions never degrade coverage; unavailable rows do.
- **Exceptions (C-1).** Only two handlers exist: `InvalidSessionRequest` around
  the canonical re-validation, and `ValueError` around `date.fromisoformat`.
  Everything else propagates unchanged (`invariant_failed`), including
  `RecursionError` from pathologically nested out-of-band JSON. A database
  error (`SQLAlchemyError`, `read_failed`) and `CatalogConfigurationError`
  (`catalog_unavailable`) also propagate. None of them becomes a state.
- **State.** `complete = no unavailable row and not scan_limit_reached`. The
  state describes the whole scanned window, not just the ≤ 8 returned
  occurrences:
  - `available`: occurrences and `complete`;
  - `available_with_gaps`: occurrences and `¬complete`;
  - `no_history`: no occurrence and `complete`;
  - `undetermined`: no occurrence and `¬complete`.

  Filling the occurrence page (`occurrence_limit_reached`) is never a gap.
- **Invariants.** `selection.check_invariants` runs before every return. A
  violation raises `ProjectionInvariantError`, and no history is returned.

## Not in PR1 (unchanged by this record)

The API/PR2, the feature flag, client consumption, Coach/Today/Progress
consumers, RIR, tempo, rest and prescription enrichment (T7), `WorkoutLog` and
any other legacy history, `prior_performance`, migration, index, cache,
persisted read model, backfill, and staging or production enablement are all
out of scope. Consumer and access-pattern question Q3 remains open.

G9 (representative `EXPLAIN (ANALYZE, BUFFERS)`) is a future
production-exposure qualification, not a PR1 gate.

## Qualification

| Suite | Covers |
|---|---|
| `tests/test_exercise_performance_history.py` | H-S1–S19, H-C1–C20, H-I1–I4, H-O1–O3, H-D1–D4, H-R1 (one `SELECT`, `workout_session` only), H-R2 (`session.new/dirty/deleted` empty, zero commit/flush) |
| `tests/test_exercise_performance_history_parity.py` | H-X1: TI-03 `parse_session` vs TD-01 `classify` on one row matrix. Covers fact semantics and exception boundary only |
| `tests/test_exercise_performance_history_architecture.py` | H-A1–A6, H-A8–A10 as AST guards. Each guard is shown to fire on a representative forbidden edit |
| `tests/test_exercise_catalog_identity_guard.py` | H-I5: 73 IDs assigned on `3df0945` survive with movement and region. This is deletion and coarse-reclassification protection only; it does not prove an ID is never semantically re-meant |
| `tests/test_exercise_performance_history_pg.py` | H-P1–P8 on PostgreSQL 16. It is in the CI `mobile-pg-concurrency` explicit list. H-P4 asserts `a… > _… > B… > -…`, and proves the database default collation orders differently |
| `tests/qualification/run_td01_pr1_mutations.py` | 15 semantic mutations, each killed by its named test. `drop_collate_c` needs PG |

Rollback: revert the commit. There is no data, schema or flag to clean up.
