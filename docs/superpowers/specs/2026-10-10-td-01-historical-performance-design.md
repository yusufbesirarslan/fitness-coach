# TD-01 — Read-Only Per-Exercise Historical Performance Projection

- Status: **Accepted** (not implemented)
- Date: 2026-10-10
- Authority: [ADR 0002](../../adr/0002-performed-training-authority.md) (Accepted),
  under [TI-00](2026-10-05-ti-00-training-intelligence-contract.md) and
  [ADAPTIVE_COACHING](../../ADAPTIVE_COACHING.md)
- Verified against: `fitness-coach` `origin/main` `3df0945` (#427, the merge that
  accepted ADR 0002). Every `path:line` citation below is on that commit.
- Authorizes: planning and implementation of PR1 only, subject to owner
  authorization (§Status). No API, flag, client, schema, migration or index
  change is authorized by this document.

## Status

ACCEPTED on 2026-10-10, after an adversarial technical review, a targeted
revision and a limited re-review. The re-review's verdict was "accepted with
constraints". All four MINOR constraints are applied in this text:

| Constraint | Applied in |
|---|---|
| C-1 parser exception classification | §Exception classification, row classification, §State machine, §Observability, §Failure Semantics, H-A10, H-C19, H-X1 |
| C-2 G9 threshold wording | §Performance and Storage (G9), R5, G9 gate, global HOLD conditions |
| C-3 `UnavailableSession.workout_date` | §Internal Projection, internal invariant check, success body sketch, H-C18, H-C20 |
| C-4 exact identity exception types | H-I3, H-I4 |

Acceptance fixes the technical design and nothing more:

- it authorizes planning and implementation of **PR1 only**, and PR1 remains
  subject to owner authorization;
- the API and PR2 remain NOT AUTHORIZED;
- the feature flag remains NOT AUTHORIZED;
- client work remains deferred;
- RIR and tempo remain deferred (T7);
- no schema, migration or index is authorized;
- Q2 (U1–U3) remains an open rollout unknown;
- Q3 must be resolved before any API is designed.

**Revision 1 (2026-10-10).** The first adversarial review returned REVISION
REQUIRED with no architecture blocker. This revision changes only the text the
findings require:

| Finding | Closed in |
|---|---|
| M-1 RIR/tempo ambiguity | T7, §Internal Projection, §Query Semantics, tests H-S10–H-S13 (RIR/tempo deferred; Q1 removed) |
| M-2 premature API / PR2 | §API / Contract Design and §Feature Flag (both PROVISIONAL — NOT AUTHORIZED), §Implementation Slices (PR1 only), H-A8 |
| m-1 tie order | T2, §The one statement, §Ordering and Bounds, internal invariant, H-D3, H-P4 |
| m-2 `checkpoint_missing` | §Canonical re-validation, row classification, H-C6, H-C15 |
| m-3 state scope | §State machine "What the state describes", H-C13, H-C14 |
| m-4 catalog guard claim | §Exercise Identity, H-I5, R6 |
| m-5 operator failure classes | §Observability, §Failure Semantics, H-C16 |
| m-6 route wording | T9, §API / Contract Design |
| m-7 broad side-effect hashes | H-R2 |
| m-8 staging gate placement | §Rollout and Rollback, G7 |
| m-9 no-index assumption | §Performance and Storage, G9 |

The core design is unchanged: canonical source, eligibility, dating, whole-
checkpoint validation, owner isolation, the four states and the 56/64/8/20
bounds.

This document designs the first follow-up slice that ADR 0002 permits
(`docs/adr/0002-performed-training-authority.md:635-651`). It does not reopen
ADR 0002. Where this TD adds a decision the ADR does not make, the decision is
listed under "Decisions introduced by TD-01" in §Proposed Component Boundary
and repeated in the verdict report.

## Binding Architecture

The following are inputs. TD-01 does not decide them:

| # | Invariant | Source |
|---|---|---|
| B1 | A performed-set fact is a `completed: true` set entry in the final `checkpoint_data` of an owned COMPLETED `WorkoutSession` | ADR D1, D1a (`adr/0002…:121-147`) |
| B2 | Null `reps` or null `weight_kg` means unknown. Zero means zero. Load is never inferred from bodyweight | D1a (`:143-145`) |
| B3 | `execution_context_data` and `prescription_data` are optional enrichment. If either is absent, the fact stays valid | D1a (`:146-148`) |
| B4 | `workout_date` sets day/window membership and `completed_at` sets order. Cross-date sessions are excluded and never re-dated. A null `completed_at` means the session is excluded | D1a (`:149-154`), L11 (`:496-503`), TI-00 §10 |
| B5 | A fact is identified by `public_id` + final `checkpoint_revision` + `exercise_id` + set `index`. There is no new fact-ID table | D1a (`:155-157`) |
| B6 | A stored `exercise_id` is the fact's historical identity. An inactive ID is not an unknown ID. History must not depend on the active-only `resolve_exercise` | D1a (`:168-183`) |
| B7 | A checkpoint that fails canonical re-validation is unavailable as a whole. It is never salvaged and never reported as empty history | ADR follow-up (`:646-647`), L11 (`:496-503`) |
| B8 | Completion evidence is not set evidence. "No facts" never means "no training" | D1a (`:164-167`), Authority Rule 3 (`:278-281`) |
| B9 | ACTIVE and ABANDONED sessions are not history. Stranded sets are not recovered | D1, L10 (`:477`) |
| B10 | No `WorkoutLog`, `workout_log` SQL or `fetch_workout_entries`, and no meaning derived transitively from grandfathered outputs | D2, Authority Rule 6 (`:288-297`), baseline (`:413-464`) |
| B11 | `ExerciseNote` is not history | TI-00 §9, ADR `:103-106` |
| B12 | Read projection only: no recommendation, score, recovery or mutation, and no second store | D3, D7, D8 |
| B13 | No Coach consumption while O1 is open | ADR `:609-616`, `:651` |
| B14 | No generic Observation, event store or CQRS | D8 |
| B15 | `prior_performance` is not a reference implementation | L11 (`:480-508`) |

## Problem

AxisAI cannot answer this question: *"For this authenticated user and one
catalog exercise, what canonical performed-set history exists?"*

Three surfaces read completed checkpoints today, and none of them can answer it:

- `workout_session/prior_performance.py:87-108` returns one first-set default
  per exercise. It runs under pre-D1a rules: it skips unloadable rows silently,
  salvages entries one by one, and keeps cross-date and null-`completed_at`
  sessions. Its only consumer is the browser `/training/bootstrap`
  (`app/blueprints/training.py:660`), a grandfathered baseline member through
  `workout_state`.
- TI-03 `training_intelligence` reads the right store under the right rules
  (`facts.py:52-101`). Its output, though, is a derived insight anchored on one
  session. It does not expose per-exercise history, and ADR D3
  (`adr/0002…:199-208`) classifies TI facts as derived outputs, which a new
  consumer must not treat as facts.
- `WorkoutLog` / Sprint 6 is legacy evidence (D2) and is forbidden as a source.

Any feature that wants "what did I lift last time on this exercise" would
therefore reimplement selection on its own. ADR alternative E rejects that
(`adr/0002…:541-543`).

## Goals

1. Provide one deterministic, owner-scoped, bounded, read-only projection of D1a
   facts for one `exercise_id`.
2. Keep "no history" distinct from "history that cannot be determined" at every
   layer, and keep that distinction mechanically provable.
3. Use the canonical write-path validator for re-validation, so the slice adds
   no second interpretation.
4. Require no migration, persisted model or client change. A new index is
   qualification-dependent (§Performance and Storage).
5. Qualify the service foundation (PR1) on its own, with PostgreSQL authority.
   Any read surface is a later, separately reviewed slice.

**Product connection.** PR1 establishes a trustworthy, reusable
historical-performance read foundation. It does not itself validate user value.
A later approved experiment may test, for example, whether users revisit prior
performance, whether history reduces reliance on external notes or memory, or
whether it improves the logging and progression workflow. This TD selects no UI
and no consumer.

## Non-Goals

- Progression, e1RM, volume, PRs, trend, percentage change, deload, recovery and
  scoring.
- Charts, a history UI (browser or mobile), a Progress redesign and Today
  integration.
- Coach context or tools, including any use while O1 is open.
- `WorkoutLog` migration, backfill, retirement, labelling or reconciliation.
  Sprint 6 convergence is also excluded.
- Changing or converging `prior_performance`, or changing TI-03.
- Recovering stranded ACTIVE or ABANDONED checkpoints, importing out-of-app
  training, and post-hoc logging.
- History older than the bounded horizon. This is not an arbitrary history
  dashboard (TI-00 §10).
- Prescription comparison and adherence.
- RIR, tempo, logging interval and any other execution-context or prescription
  enrichment (deferred; T7).
- Any API, route, feature flag or production caller (PR2 is not authorized).
- Nutrition, wearables, sleep, HRV, RHR and stress.

## Current Architecture

### Source store and lifecycle

- `WorkoutSession` is defined at `app/models.py:1291-1410`. Its status is closed
  by `ck_workout_session_status` (`:1403-1406`) to the values `active`,
  `completed` and `abandoned`.
- Only `workout_session/queries.py:368-…` (`advance_checkpoint`) writes
  `checkpoint_data`. It is a single conditional UPDATE on owner, reference,
  `status='active'` and the exact base revision (ADR `:74-77`; TI-00 §3).
- Terminal rows reject checkpoints (`workout_session/execution.py:90-93`).
- Completion terminalizes only an ACTIVE row. It sets `completed_at = now`, with
  `now` in naive UTC (`workout_completion/queries.py:185-196`).
- `prescription_data` is written only at start
  (`workout_session/queries.py:318-321`).
- Therefore, after COMPLETED, `checkpoint_data`, `checkpoint_revision`,
  `execution_context_data`, `prescription_data`, `workout_date` and
  `completed_at` have no application write path. Account deletion removes the
  row.

### Existing readers of the same store (not reused as authority)

| Reader | Selection rules | Why TD-01 does not reuse it |
|---|---|---|
| `prior_performance.load_prior_performance` (`prior_performance.py:87-108`) | `load_snapshot` only; per-entry salvage (`:37-84`); keeps cross-date and null `completed_at`; 24 rows; `completed_at DESC NULLS LAST, id DESC` | L11: not a reference reader; its rules differ from D1a |
| TI-03 `queries.load_history` + `facts.parse_session` (`training_intelligence/queries.py:60-78`, `facts.py:52-101`) | `parse_stored_exercises(load_snapshot(...))`; whole-checkpoint unavailable; cross-date and null `completed_at` are `inconsistent_date`; 57 Istanbul day keys; `completed_at DESC NULLS LAST, public_id DESC`; `LIMIT 64` | Correct rules, but it is a derived-output package (D3) shaped around one anchor session. TD-01 reuses its **canonical primitives** and its **query shape**, not the package |

### Catalog identity

- `exercise_catalog.ID_PATTERN = ^ex_[a-z0-9_]+$` (`exercise_catalog.py:16`).
- `load_exercise_catalog().by_id` contains active and inactive entries
  (`:177-207`).
- `resolve_exercise` raises `ExerciseIdentityInvalid` for malformed or unknown
  IDs and `ExerciseInactive` for inactive IDs (`:210-225`).
- On `3df0945` the catalog has version 1, 73 IDs, a longest ID of 38 characters
  and 0 inactive entries.
- `exercise_notes._identity` uses the active-only resolver
  (`exercise_notes.py:45-50`). That is correct for notes and wrong for history.

### Transport conventions

- Native routes register on `mobile_api` by module import
  (`app/blueprints/mobile_api.py:246-249`).
- Error envelope: `mobile_error(code, message, status, retryable,
  retry_after)` (`mobile_api.py:29-39`).
- A dedicated-flag read surface answers 404 **before** authentication while OFF
  (`mobile_today_guidance.py:29-45`). The training-session surface gates after
  auth (`mobile_workout_sessions.py:69-99`).
- Native timestamps are rendered as UTC `Z`
  (`mobile_workout_sessions/projection.py:18-…`). `session_ref` is the
  `public_id` (`:46`).
- Each new `/api/v1` path must be listed by exact path in the route
  inventories: `tests/test_mobile_auth_feature_gate.py:90-98, 222-240` and
  `tests/test_sprint12_daily_coach_discovery.py:163-266`.

### Flags and metrics

- Lifecycle records are `FeatureFlag` entries in `ROLLOUT_FLAGS`
  (`app/feature_flags.py:102-136`). The dependent-flag pattern is in the TI
  insights record (`:156-171`).
- `depends_on` is documentation. Readiness is enforced at request time, as
  `_insights_enabled` does (`mobile_workout_sessions.py:148-153`).
- Metrics are fixed-cardinality and best-effort through
  `runtime_metrics.increment`, with a single `Event` dimension
  (`training_intelligence/metrics.py`, `workout_session/metrics.py`).

## Canonical Fact Semantics

### Source data map (Phase 2 answers)

| Q | Answer | Evidence |
|---|---|---|
| A. Session identity | `public_id` (opaque, unique, ≤64 chars). Exposed as `session_ref`. The sequential `id` is never exposed | `models.py:1316`, `:1401`; `mobile_workout_sessions/projection.py:46` |
| B. Exercise identity in a checkpoint | `exercises[].exercise_id`, unique within a checkpoint. A duplicate is rejected | `checkpoint.py:210-216` |
| C. Set identity | `exercises[].sets[].index`, integer 0–19, unique per exercise. A duplicate is rejected | `checkpoint.py:236-238` |
| D. Set fields | Exactly `index`, `completed`, `reps`, `weight_kg` | `checkpoint.py:64`, `:234-248` |
| E. Nullable | `reps` (null or int 0–1000) and `weight_kg` (null or finite 0–1000, rounded to 0.1). `completed` is a strict bool | `checkpoint.py:240-274` |
| F. RIR | `execution_context_data` → `sets[].context.actual_rir` ∈ {`0`,`1`,`2`,`3`,`4_plus`}. Bound to `bound_revision` and to base-set anchors | `context.py:15`, `:103-141` |
| G. Tempo | The same sidecar: `context.tempo_adherence` ∈ {`as_prescribed`,`faster`,`slower`,`lost_control`}, validated against the prescription | `context.py:16`, `:138` |
| H. Prescription | `prescription_data`: schema 1, `source_plan_lineage`, `source_mutation_version`, and per-exercise targets with provenance. Null for older and unscheduled sessions | `prescription.py:14-46`; `queries.py:318-321` |
| I. Dates and timestamps | `workout_date` (ISO Istanbul start day, String(10)), `completed_at` (naive UTC, nullable), `started_at`, `checkpoint_at` | `models.py:1326`, `:1339-1344`, `:1375` |
| J. Revision and version | `checkpoint_revision` (0 = nothing acknowledged; +1 per accepted checkpoint) and `version` (terminal transitions). Only `checkpoint_revision` belongs to fact identity | `models.py:1349`, `:1367-1368` |
| K. Immutable after COMPLETED | All columns in the previous section; there is no application write path | §Current Architecture |
| L. Malformed legacy shapes | See below | — |

Rows F–H and shape L.6 describe the store. TD-01 v1 reads none of them (T7).

**L. Shapes a COMPLETED row can hold:**

1. `checkpoint_data` NULL with `checkpoint_revision = 0`. The session completed
   with nothing acknowledged. This is legitimate: pre-PR5 native sessions,
   browser sessions before Sprint 14 PR2 (heartbeat only), and a user who
   completes at revision 0.
2. Non-JSON text, a JSON non-object, or an empty string. These can arise only
   from an out-of-band write.
3. A JSON object whose keys are not exactly
   `{current_exercise_index, elapsed_seconds, exercises}`. Out-of-band or
   legacy edit only.
4. Duplicate `exercise_id`, duplicate set `index`, extra keys, a non-bool
   `completed`, or out-of-range or NaN values. Out-of-band only, because the
   write path rejects all of them.
5. `checkpoint_revision > 0` with NULL data, or revision 0 with non-NULL data.
   These violate the `advance_checkpoint` invariant, which moves snapshot and
   revision together.
6. `execution_context_data` that is unbound (`bound_revision` ≠ revision),
   anchor-mismatched or malformed. `project_context` projects it as empty
   (`context.py:107-112`, `:140-141`).
7. `completed_at` NULL on a COMPLETED row. The application writer cannot
   produce this, but it is possible in legacy rows or fixtures (L11 names it).

### Canonical required vs optional

| Field | Class | Required for a fact |
|---|---|---|
| owner = authenticated user | canonical | yes (query predicate) |
| `status = completed` | canonical | yes |
| `workout_date` (parseable) | canonical (membership) | yes |
| `completed_at` non-null and on the same Istanbul day as `workout_date` | canonical (ordering and dating) | yes |
| checkpoint passes `parse_stored_exercises(load_snapshot(·))` | canonical | yes |
| set `completed is True` | canonical | yes |
| set `index`, `reps`, `weight_kg` | canonical fact values (nullable values stay null) | yes |
| `public_id`, `checkpoint_revision` | provenance / identity | yes (always present) |
| `execution_context_data`, `prescription_data` (RIR, tempo, interval, targets) | optional enrichment (B3) | no. **Not selected or projected by v1** (T7) |

### Canonical re-validation (definition used everywhere in TD-01)

A COMPLETED row's checkpoint is canonically valid iff
`parse_stored_exercises(load_snapshot(checkpoint_data))` does not raise
(`checkpoint.py:171-199`). This is exactly the TI-03 `parse_session` primitive
(`facts.py:72`), with no added rule.

`checkpoint_revision` is **not** a validity input. It is used in only two
places:

- as fact identity (B5);
- to label a SQL-NULL checkpoint: NULL at revision 0 is `checkpoint_missing`,
  which means nothing was ever acknowledged; NULL at revision ≥ 1 is
  `checkpoint_invalid`.

`checkpoint_missing` is exactly `checkpoint_revision == 0 AND checkpoint_data IS
NULL`. An empty string is not missing state. It is an out-of-band value (shape
L.2) and is `checkpoint_invalid` at any revision. No extra rule is needed for
this: `load_snapshot("")` returns `None` (`checkpoint.py:175-176`), and
`parse_stored_exercises(None)` raises (`:193-194`).

A non-NULL checkpoint at revision 0 (shape L.5) cannot be produced by
`advance_checkpoint`. If it ever exists, the canonical parser alone decides it,
exactly as in TI-03. Adding a revision rule would be a second interpretation.

Known limits of this validator, which are accepted and not extended:

- It re-validates exercises and sets. It does not re-validate the snapshot
  scalars (`current_exercise_index`, `elapsed_seconds`) or the 64 KiB byte
  backstop. TD-01 projects neither scalar.
- It does not re-check the syntax of a stored `exercise_id`. A stored ID that
  could not match `ID_PATTERN` can never equal a validated request ID, so it
  never surfaces as a fact.

TD-01 must call these two functions and nothing else. It must not add a third
validator.

### Exception classification (binding; C-1)

The implementation engineer has no discretion here.

- **`checkpoint_invalid` comes ONLY from `InvalidSessionRequest`** raised by the
  canonical re-validation call. This is TI-03 parity: `facts.parse_session`
  catches exactly this class (`facts.py:72-75`).

  ```python
  try:
      exercises = parse_stored_exercises(load_snapshot(checkpoint_data))
  except InvalidSessionRequest:
      reason = CHECKPOINT_INVALID
  ```

- **No broader catch.** The re-validation handler names `InvalidSessionRequest`
  and nothing else. The package has no bare `except`, no `except Exception`,
  no `except BaseException`, no `except RecursionError`, and no handler tuple
  that widens the class. Nothing is caught and relabelled as
  `checkpoint_invalid`.
- **The one other handler** in the package is in the row-classification date
  rule (rule 1). It catches `ValueError` from `date.fromisoformat` on a string
  `workout_date`; a non-string is rejected by an `isinstance` check before
  parsing. This is the shape of TI-03's `_parse_day` (`facts.py:45-49`). Its
  only result is `session_invalid`.
- **Every other exception is `invariant_failed`.** Any exception that escapes the
  pure validation or selection layer propagates unchanged through
  `build_exercise_history` to the service boundary. It is classified
  operationally as `invariant_failed`. It is never converted into
  `checkpoint_invalid`, `checkpoint_missing`, `no_history`, `undetermined` or a
  gap marker, and no `ExerciseHistory` is returned.
- **Known limit (accepted, not extended).** `load_snapshot` catches only
  `ValueError` and `TypeError` from `json.loads` (`checkpoint.py:177-180`).
  Out-of-band JSON that is nested deeply enough can make `json.loads` raise
  Python's `RecursionError` before canonical validation can classify the row.
  - That is not user-history corruption TD-01 claims to recover from. It is an
    invariant or internal failure, so it propagates as `invariant_failed`.
  - TI-03 behaves the same way today.
  - The write path cannot produce such a value; only an out-of-band write can.

## Proposed Component Boundary

### Choice

Phase 3 options:

- **A. Extend an existing workout-session query module.** Rejected.
  `workout_session/queries.py` owns lifecycle persistence: locks, CAS and start
  capture. Adding a cross-session historical scan there would put a read model
  next to the module that `prior_performance` (L11) already lives beside, and
  would invite reuse of its looser helpers.
- **B. A dedicated canonical history service package.** **Selected.**
- **C. Reuse the TI-03 `queries` + `facts`.** Rejected as an import. TI is a
  derived-output package (D3). Its `inconsistent_date` lumping and its anchor
  semantics are TI ruleset choices, and its architecture test forbids
  read-time `today`.

### The package

`app/services/exercise_performance_history/`. The name differs on purpose from
the legacy `training_history`.

| Module | Purity | Responsibility |
|---|---|---|
| `models.py` | pure | Constants, closed vocabularies and frozen value objects. The only place a bound or token is defined |
| `queries.py` | impure, read-only | Exactly **one** owner-scoped SELECT over `WorkoutSession` (§Query Semantics) |
| `selection.py` | pure | Row classification, fact extraction, state derivation and the internal invariant check (§Internal Projection). Takes the anchor as an argument; never reads a clock |
| `__init__.py` | orchestrator | `build_exercise_history(user_id, exercise_id)`: validates identity, computes `anchor = app_today()` **once**, runs the one query, then pure selection, then the invariant check |
| `payload.py`, `metrics.py` | — | **Not in PR1.** Provisional only; they belong to a future read surface (§API / Contract Design) |

PR1 has **zero production callers**. Nothing outside the package and its tests
imports it (H-A8).

**Catalog helper (PR1, smallest read-only addition):**
`exercise_catalog.resolve_historical_exercise(exercise_id) ->
ExerciseDefinition`. It reuses `ID_PATTERN`, a length bound and
`load_exercise_catalog().by_id`:

- malformed → `ExerciseIdentityInvalid`;
- not in the catalog → a new `ExerciseUnknown(ExerciseIdentityInvalid)`;
- an inactive ID is **returned, not raised**.

`resolve_exercise` is unchanged. This is a function over the existing catalog,
not a registry.

### Decisions introduced by TD-01 (beyond ADR 0002)

- **T1. Dedicated package.** Canonical primitives are reused; TI and
  `prior_performance` are not imported.
- **T2. One-statement read.** It reuses the TI-03 query shape: 57 Istanbul day
  keys ending at **today**, `LIMIT 64`, `completed_at DESC NULLS LAST`. The
  tie-break is `public_id DESC` in **byte order**: `COLLATE "C"` on PostgreSQL
  and the default `BINARY` collation on SQLite. This is the one comparator
  everywhere (§Ordering and Bounds). It deliberately differs from TI-03, which
  ties on the database collation.
- **T3. Per-row classification with precedence:** session_invalid → date
  exclusion → checkpoint missing (NULL only) / invalid → eligible.
- **T4. Separate reporting.** Date-excluded sessions are reported apart
  (`excluded_cross_date`, `excluded_missing_completed_at`) and do **not**
  degrade coverage. Unavailable sessions
  (missing or invalid checkpoint) **do** degrade it. TI-03 instead counts both
  under `history_unavailable`; see the rationale under §Corruption and
  Availability Semantics.
- **T5. A four-state model** (`available`, `available_with_gaps`,
  `no_history`, `undetermined`). It describes the completeness of the whole
  scanned window, not only the returned occurrences. A read failure is a typed
  exception, never a state.
- **T6. Two missing-checkpoint reasons.** `checkpoint_missing` is exactly SQL
  NULL at revision 0. It is distinct from `checkpoint_invalid`, which includes
  an empty string. Both are coverage gaps, per ADR D1.
- **T7. No enrichment in v1.** RIR and tempo are deferred, along with every
  other context or prescription field. `execution_context_data` and
  `prescription_data` are not selected, and `project_context` is not imported.
  There is no dependency on `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED`.
- **T8. Historical identity helper.** Malformed → `ExerciseIdentityInvalid`,
  unknown → `ExerciseUnknown`, inactive → served. HTTP mapping is provisional.
- **T9. Read surface: provisional only.** A future Bearer-authenticated
  `/api/v1` read surface is sketched but NOT AUTHORIZED (§API / Contract Design).
- **T10. A catalog deletion and coarse-reclassification test guard**
  (test-only) lands with PR1.
- **T11. PR1-only slicing.** The service foundation is the only authorized
  slice. It has zero production callers.

## Query Semantics

### Inputs

| Input | Source | Notes |
|---|---|---|
| owner | `user_id: int`, the service's only owner input. A future caller must take it from its authenticated principal | Never a request field |
| exercise_id | caller-supplied string | Validated by `resolve_historical_exercise` **before** any query runs |
| anchor | `app.timeutil.app_today()`, called once in `__init__` | No client date or timezone input |

### The one statement (PR1 `queries.load_candidates(user_id, anchor)`)

```python
_COLUMNS = (WorkoutSession.public_id, WorkoutSession.workout_date,
            WorkoutSession.completed_at, WorkoutSession.checkpoint_revision,
            WorkoutSession.checkpoint_data)

def _tie_key():
    # One comparator: public_id in byte order on every supported dialect.
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        return WorkoutSession.public_id.collate("C")
    if dialect == "sqlite":
        return WorkoutSession.public_id          # default BINARY = byte order
    raise RuntimeError("unsupported database for exercise performance history")

rows = (db.session.query(*_COLUMNS)
        .filter(WorkoutSession.user_id == user_id,
                WorkoutSession.status == WORKOUT_SESSION_COMPLETED,
                WorkoutSession.workout_date.in_(day_keys(anchor)))   # 57 ISO keys
        .order_by(nullslast(WorkoutSession.completed_at.desc()),
                  _tie_key().desc())
        .limit(MAX_SCAN_ROWS)                                         # 64
        .all())
return rows, len(rows) >= MAX_SCAN_ROWS
```

- **Collation, verified on `3df0945` with SQLAlchemy 2.0.51.**
  - On PostgreSQL, `.collate("C")` renders `public_id COLLATE "C" DESC`. "C"
    compares bytes, and UTF-8 byte order equals code-point order.
  - SQLite has no "C" collation (`no such collation sequence: C`). Its default
    `BINARY` collation already compares bytes.
  - Python `str` comparison is by code point, so all three agree.
  - `public_id` is `secrets.token_urlsafe(32)` (`models.py:1286-1288`), which is
    ASCII only.
  - Branching on the dialect is repository precedent
    (`progress_summary/queries.py:55-67`, `exercise_notes.py:115`). Reading the
    bind's dialect issues no SQL.
  - PostgreSQL is authoritative for tie-order qualification (H-P4).
- **Conservative truncation.** `len(rows) >= 64` is treated as truncation even
  if exactly 64 rows existed. There is no 65th probe. The error is always toward
  "incomplete", never toward `no_history`.

- `day_keys(anchor)` returns the ISO keys `anchor − 56 … anchor`, inclusive,
  derived from `app.timeutil` dates. Exact `IN` equality means no database
  collation or timezone can move a boundary (the same reasoning as
  `training_intelligence/queries.py:8-11`).
- **`exercise_id` never enters SQL.** No JSON operator, no `LIKE` and no
  dialect-specific JSON path is used. Exercise membership is decided only by
  the canonical parser in Python. Two reasons:
  - a text prefilter would hide corrupt rows that do not contain the ID, and so
    misreport coverage;
  - a prefilter would add a second interpretation of the checkpoint.
- `status` and `user_id` are never projected. `weekday_slot`, plan columns,
  fingerprints and idempotency keys are never selected.
- `execution_context_data` and `prescription_data` are never selected (T7).
- Exactly one statement runs (READ COMMITTED gives one consistent snapshot).
  There is no second read, so all projected values come from the same row
  image.

### Bounds

| Bound | Value | Kind | Rationale |
|---|---|---|---|
| Horizon | 56 days back plus the anchor day (57 day keys) | **Product/history semantic window** | TI-00 §10 "Query max 56 days"; same as `HISTORY_DAYS` |
| Candidate scan | 64 rows, `LIMIT` | **Technical scan safety bound** | Same as TI-03 `MAX_HISTORY_ROWS` (`models.py:34`). Post-LP-13 there is at most one completed session per day, so 57 is the realistic maximum; the headroom absorbs legacy same-day rows |
| Occurrences returned | 8, newest first | **Returned-history semantic bound**, inherited from TI-00 | TI-00 §10 "8 sessions per exercise" |
| Sets per occurrence | ≤ 20 | **Canonical write-schema bound**, not a new TD limit | Enforced by the canonical parser (`checkpoint.py:41`) |
| Unavailable markers | ≤ 64 | Derived | Cannot exceed the scan |
| `exercise_id` length | 1–64 chars and matching `ID_PATTERN` | Input validation | Catalog max is 38 |

Hitting the 64-row safety bound can **never** yield `no_history`. It always
degrades completeness (`scan_limit_reached`, §State machine).

Constants are defined in `exercise_performance_history/models.py` with values
equal to TI-00 §10. They are **not** imported from `training_intelligence`, so
that a TI ruleset change cannot silently move them. A test pins the values.
There are no request parameters for any bound.

### Row classification (pure, `selection.classify(row)`)

Rows are evaluated in SQL order. The first matching rule wins:

| # | Condition | Class | Reason token |
|---|---|---|---|
| 1 | `workout_date` is not a string, or `date.fromisoformat` raises `ValueError` (unreachable through the `IN` filter; defensive) | UNAVAILABLE | `session_invalid` |
| 2 | `completed_at IS NULL` | EXCLUDED | — (counted as `missing_completed_at`; an operator anomaly) |
| 3 | `app_date_of(completed_at) != workout_date` | EXCLUDED | — (counted as `cross_date`) |
| 4 | `checkpoint_data IS NULL` **and** `checkpoint_revision == 0` | UNAVAILABLE | `checkpoint_missing` |
| 5 | canonical re-validation raises `InvalidSessionRequest`. This includes NULL data at revision ≥ 1, and an empty string or any other out-of-band value at any revision | UNAVAILABLE | `checkpoint_invalid` |
| 6 | otherwise | ELIGIBLE | — |

No other exception is a row class. Any other exception raised while a row is
classified propagates as `invariant_failed` (§Exception classification).

- Date rules run **before** checkpoint parsing. An excluded row can never be a
  fact, so its checkpoint is irrelevant and is not parsed.
- `app_date_of` is the existing helper (`app/timeutil.py:59`). It treats naive
  datetimes as UTC, which matches how `completed_at` is written. No parallel
  timezone interpretation is introduced.

### Fact extraction (ELIGIBLE rows only)

For an ELIGIBLE row:

1. Find the single entry with `exercise_id == requested`. Uniqueness is
   guaranteed by the parser.
2. Keep sets where `completed is True`, in ascending `index` order (the parser's
   canonical order).
3. If no such entry exists, or no set is completed, the row contributes
   **nothing**. It is not an occurrence and not a gap.
4. Otherwise it is an occurrence. Only the first 8 occurrences in SQL order are
   materialized. Further occurrences set `occurrence_limit_reached = true`.
5. Every scanned row is still classified, including rows after the 8th
   occurrence. A gap anywhere in the scanned window therefore still counts
   (§State machine).

`reps` and `weight_kg` are copied exactly as parsed. Null stays null, `0` stays
`0`, and `0.0` stays `0.0`. Nothing reads the equipment or movement fields of
the catalog, so no bodyweight inference is possible.

### Determinism

For a fixed stored state and a fixed anchor day, the output is a pure function.
The only read-time input is `anchor`. There is one ordering comparator:
`completed_at DESC`, then `public_id DESC` in byte order (T2). SQL, the internal
invariant and every test use it. No environment-dependent collation can change
it.

### Read-only

The read has no `add`, `flush`, `commit`, `rollback`, `with_for_update`,
`execute` or attribute assignment, and it emits no `runtime_metrics` call. The
architecture guard enforces this for PR1. Metrics and logging belong to a
future caller (§Observability). The package emits neither.

## Corruption and Availability Semantics

### Alternatives compared

| | (i) Any unavailable candidate makes the whole projection unavailable | (ii) Valid occurrences plus explicit coverage metadata; zero occurrences with gaps is never `no_history` (**selected**) |
|---|---|---|
| Corruption vs empty | Distinguishable | Distinguishable |
| Blast radius | One immutable bad row blocks history for **every** exercise for up to 57 days, because relevance is unknown | Limited to an explicit gap marker; valid sessions stay usable |
| Partial-salvage rule (B7) | Satisfied | Satisfied: salvage is forbidden **within** a checkpoint, and each checkpoint stays whole-or-nothing |
| Precedent | — | TI-00 §16: an `available` insight may carry `history_unavailable` (`training_intelligence/diagnostics.py:260-263`) |
| Consumer risk | Low, but the feature is unusable | Mitigated: the state token encodes the gap (`available_with_gaps`), and markers carry `completed_at` so a consumer can tell whether a gap is newer than its newest occurrence |

**Why (ii).** Completed checkpoints are immutable. Under (i), a single bad row
becomes an eight-week outage for all exercises, with no repair path short of a
database edit. (ii) keeps the ADR invariant (corruption is never empty) and the
whole-checkpoint rule, and puts the gap in the state token itself, so a client
that reads only `state` still cannot mistake a gap for completeness.

### State machine

Let `O` = materialized occurrences, `U` = UNAVAILABLE rows, `L` =
`scan_limit_reached`, and `complete = (|U| = 0) ∧ ¬L`.

| `state` | Condition | Meaning |
|---|---|---|
| `available` | `|O| ≥ 1 ∧ complete` | Valid matching history exists, **and** the whole scanned window is complete: every scanned candidate was readable and the scan was not truncated |
| `available_with_gaps` | `|O| ≥ 1 ∧ ¬complete` | Valid matching history exists, but window coverage is incomplete: ≥1 scanned candidate was missing or unreadable, or the scan bound cut the window. Other history, older or interleaved, may be missing |
| `no_history` | `|O| = 0 ∧ complete` | No canonical matching facts, and window coverage is complete: the read succeeded, every candidate was readable, and none holds a completed set of this exercise. It does **not** mean "no training" (B8) |
| `undetermined` | `|O| = 0 ∧ ¬complete` | No canonical matching facts, and coverage is incomplete. Absence cannot be asserted: ≥1 unreadable candidate might contain this exercise, or the scan was cut |

**What the state describes.** The state describes the completeness of the
**whole scanned history window**, not only the ≤8 returned occurrences.

- Valid returned occurrences can coexist with a missing or unreadable candidate
  anywhere else in the scanned window, including one older than the 8th
  occurrence. The result is `available_with_gaps`.
- Scan truncation makes coverage incomplete even when 8 valid occurrences were
  already found.
- "8 clean returned occurrences" never implies "the whole window was readable".
  Only `available` says that.
- No state implies "the user did no training" (B8).

The limits relate as follows:

| Signal | Meaning | Effect on `complete` / state |
|---|---|---|
| Returned occurrence limit (8) | At most 8 occurrences are materialized | None |
| `occurrence_limit_reached` | More than 8 matching occurrences exist among the scanned rows | None. A full page is not a gap |
| Candidate scan limit (64) | At most 64 candidate rows are read | — |
| `scan_limit_reached` | The scan returned 64 rows, so the window may hold more | `complete = false`. Never `no_history` |
| `unavailable` rows | Scanned rows that were `checkpoint_missing`, `checkpoint_invalid` or `session_invalid` | Any one makes `complete = false` |
| Date exclusions | `cross_date` and `missing_completed_at` rows | None. They are exclusions, not gaps (T4) |

Not a state. The service raises; it never returns an `ExerciseHistory`:

- **Read failure.** A repository or database error (`SQLAlchemyError`)
  propagates unchanged. It is the transient `read_failed` class.
- **Invariant failure.** `ProjectionInvariantError`, raised by the internal
  invariant check. Any other exception escaping the pure validation or
  selection layer is also the `invariant_failed` class. This includes
  `RecursionError` from pathological out-of-band JSON. It propagates unchanged
  and is never relabelled (§Exception classification). The only exceptions the
  pure layer handles are `InvalidSessionRequest` from re-validation, which
  becomes `checkpoint_invalid`, and `ValueError` from the date parse, which
  becomes `session_invalid`.
- **Catalog failure.** `CatalogConfigurationError` from the catalog loader is the
  `catalog_unavailable` class.
- The service never converts one class into another or into a state. A future
  caller answers all three outside any success body. TI-00 §16 precedent:
  "Query failure returns HTTP error, not `history_unavailable` success."
- **Invalid input.** `ExerciseIdentityInvalid` (malformed) and `ExerciseUnknown`
  (unknown) are raised before any history read, so invalid input can never look
  like absence. See §Exercise Identity.

**Critical invariant (tested):** `no_history` is emitted **iff** `|O| = 0`,
`|U| = 0` and `¬L`. No code path can produce `no_history` while a row is
UNAVAILABLE or the scan was truncated.

### Date-excluded sessions (decision T4)

- Rows excluded by B4 are counted by reason (`coverage.excluded_cross_date`,
  `coverage.excluded_missing_completed_at`) and do **not** make coverage
  incomplete. A COMPLETED row with null `completed_at` is still excluded, but it
  is also an operator anomaly (§Observability).
- They are deterministic, ADR-mandated non-facts whose reason is fully known.
  They are not unknowns.
- Counting them as gaps would make every session finished after Istanbul
  midnight an eight-week `available_with_gaps` or `undetermined` marker for
  every exercise. That would erase the meaning of the gap signal.
- The count is still published, so a consumer can say "N sessions in this
  window were not counted" and never present `no_history` as "no training".
- This differs from TI-03, which lumps them into `history_unavailable`. The
  difference is in reporting only; fact eligibility is identical (both exclude
  these rows).

### Revision-0 completions (decision T6)

A COMPLETED session with nothing acknowledged (SQL NULL checkpoint at revision
0) is `checkpoint_missing`. An empty string is not "nothing acknowledged". It is
`checkpoint_invalid`. Per ADR
D1 ("a missing or corrupt checkpoint means execution coverage is unavailable"),
it is a gap, because the user may have performed this exercise without logging
it. It is reported with its own reason, so operators and clients can tell "never
logged" from "damaged". See §Risks R2.

## Exercise Identity

Three concepts are kept apart:

| Concept | Function | Rule |
|---|---|---|
| Request validation | `resolve_historical_exercise` | Syntax (`ID_PATTERN`, ≤64 chars) and catalog membership in `by_id`, including inactive entries |
| Historical identity | the stored `exercise_id` string | Exact string equality inside re-validated checkpoints. No catalog lookup per fact |
| Display metadata | not in v1 | Clients already hold catalog identity. Display names are a presentation concern (deferred) |

| Case | Service semantics (PR1) | Provisional HTTP mapping (not authorized) |
|---|---|---|
| A. Valid active ID | Served | 200 |
| B. Valid inactive (retired) ID | Served identically. `ExerciseInactive` is never raised on this path. A test injects a catalog with `active: false` and proves the facts are returned | 200 |
| C. Syntactically valid but not in the catalog | `ExerciseUnknown`. Not `no_history`: a typo must not read as absence | 404, retryable=false |
| D. Malformed (pattern, length or non-string) | `ExerciseIdentityInvalid` | 400, retryable=false |

- The catalog is global, code-owned product data, so cases C and D reveal
  nothing about any user.
- **Deliberate difference from the sibling note route.** The ExerciseNote
  service collapses malformed, unknown and inactive IDs into one 400
  `TRAINING_NOTE_INVALID` (`exercise_notes.py:45-49`, `:22-25`). TD-01 does
  **not** follow that convention. Inactive ≠ unknown is an ADR invariant (B6),
  and unknown ≠ malformed keeps a typo distinct from a bad request.
- **Catalog deletion and coarse-reclassification guard (test-only, PR1).**
  `tests/test_exercise_catalog_identity_guard.py` holds a frozen list of every
  ID assigned on `3df0945` (73 IDs). It asserts that:
  - the list is a subset of the current catalog, so a known ID cannot be
    deleted by accident (a retirement must flip `active`);
  - each listed ID keeps its movement and primary region, which catches coarse
    reclassification.
- **What the guard does not prove.** It cannot mechanically prove that an ID is
  never semantically re-meant. For example, a changed exercise with the same
  movement and region still passes. "Never re-mean an existing `exercise_id`"
  remains an architecture and review invariant from ADR 0002 (D1a). This test
  alone does not fully enforce it.
- The guard is a small test only. It is not a catalog governance subsystem and
  not a second source of truth for exercise definitions.

## Internal Projection

Frozen dataclasses in `exercise_performance_history/models.py`:

```python
@dataclass(frozen=True)
class PerformedSet:            # one D1a fact
    index: int                 # canonical fact (identity component)
    reps: Optional[int]        # canonical fact; None = unknown
    weight_kg: Optional[float] # canonical fact; None = unknown; kg, 0.1 precision

@dataclass(frozen=True)
class Occurrence:              # one exercise inside one completed session
    session_ref: str           # provenance / identity (public_id)
    checkpoint_revision: int   # provenance / identity (final revision)
    workout_date: date         # canonical (training day)
    completed_at: datetime     # canonical (ordering), naive UTC
    sets: tuple                # PerformedSet, ascending index, >= 1

@dataclass(frozen=True)
class UnavailableSession:
    workout_date: Optional[date]   # None ONLY for session_invalid (C-3)
    completed_at: Optional[datetime]
    reason: str                # checkpoint_missing | checkpoint_invalid | session_invalid

@dataclass(frozen=True)
class Coverage:
    scanned_sessions: int
    excluded_cross_date: int
    excluded_missing_completed_at: int   # also an operator anomaly
    unavailable: tuple         # UnavailableSession, SQL order
    scan_limit_reached: bool
    occurrence_limit_reached: bool
    @property
    def complete(self) -> bool: ...   # not unavailable and not scan_limit_reached

@dataclass(frozen=True)
class ExerciseHistory:
    exercise_id: str
    window_start: date
    window_end: date
    state: str                 # one of STATES
    occurrences: tuple         # Occurrence, newest first, <= 8
    coverage: Coverage
```

**`UnavailableSession.workout_date` (C-3).** `session_invalid` means the
stored `workout_date` itself could not be canonically interpreted (rule 1), so
the marker cannot carry a date for it.

- `workout_date` holds the parsed stored date whenever that date is valid. That
  covers every `checkpoint_missing` and `checkpoint_invalid` marker.
- `workout_date` is `None` only for `session_invalid`. Because rule 1 is the
  only way to reach `session_invalid`, `workout_date is None` ⇔
  `reason == "session_invalid"`.
- No date is invented. The design never substitutes `app_today()`, the
  `completed_at` day or any other value. It never re-dates the session and never
  silently drops the row; the marker is still counted and still degrades
  coverage.

Fact identity is `(session_ref, checkpoint_revision, exercise_id from the
parent, index)`, exactly B5. Each field is classified below.

| Field | Class | Reason |
|---|---|---|
| `index`, `reps`, `weight_kg` | canonical fact | They are the D1a fact |
| `session_ref`, `checkpoint_revision` | provenance / identity | They are the B5 identity and make every value inspectable |
| `workout_date` | canonical | The training day, and the reason a row is inside the window |
| `completed_at` | canonical | The ordering key. It also places gaps relative to occurrences |
| `completed` | **excluded** | Always true for a fact, so it carries no information |
| `actual_rir`, `tempo_adherence` | **excluded** (deferred, T7) | Context enrichment. Its rollout semantics are not approved (see below) |
| `actual_rest` | **excluded** | A logging interval, not rest (TI-00 §8). Interpreting it requires method semantics, which is a derived concern |
| prescription targets, plan lineage | **excluded** (deferred) | A target is not performance. `prescription_data` is not selected |
| `elapsed_seconds`, `current_exercise_index` | **excluded** | Session UI state, not set facts |
| display name, unit, equipment | **excluded** | Presentation. The unit is fixed (kg) by the field name |
| e1RM, volume, PR, trend, % change, deload, recovery | **forbidden** | Derived outputs (B12). The architecture guard rejects these names |

**No enrichment in v1 (decision T7).** TD-01 v1 returns only the canonical
performed-set history the foundation needs: `index`, `reps` and `weight_kg`,
with provenance and dating.

- Existing context-bearing read surfaces disappear while
  `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` is OFF: the note route
  (`mobile_exercise_notes.py:17`) and the TI insight
  (`mobile_workout_sessions.py:145-152`).
  - Returning context while that flag is OFF would add a new rollout semantic.
  - Hiding it while OFF would overload null: null would mean either "unknown" or
    "hidden by configuration".
  - Neither belongs in this first canonical history foundation.
- So PR1 does not select `execution_context_data` or `prescription_data`, does
  not import `project_context`, and exposes no `actual_rir`, `tempo_adherence`
  or other context-derived field.
- Its output is identical whatever the value of
  `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` (H-S12).
- Context enrichment may be proposed later as an **additive** TD and contract
  change, after its rollout semantics are separately approved.
- `reps` and `weight_kg` semantics are unchanged by this deferral.

**Internal invariant check (PR1, `selection.check_invariants`).** The
orchestrator runs this before returning. Any violation raises
`ProjectionInvariantError` (the `invariant_failed` class), and no
`ExerciseHistory` is returned. The invariants:

- `state` ⇔ (occurrences, coverage) matches the state machine, and in
  particular `no_history` ⇒ zero unavailable rows and ¬`scan_limit_reached`;
- tokens belong to closed sets;
- each `UnavailableSession` has `workout_date is None` iff its reason is
  `session_invalid` (C-3);
- `reps` is an int or null in 0–1000, `weight_kg` is finite or null in 0–1000,
  and `0 ≤ index ≤ 19`;
- sets are strictly ascending by `index`, and each occurrence has ≥1 set;
- occurrences are ≤8 and strictly descending by the one comparator
  `(completed_at, public_id)`. `public_id` is compared as a Python `str`, by
  code point, which equals the byte order SQL uses (T2). The check therefore
  cannot disagree with a correctly ordered database result.

## API / Contract Design — PROVISIONAL — NOT AUTHORIZED

> **NON-BINDING / PROVISIONAL.** Nothing in this section is an approved
> contract. No route, path, payload, error code or `contract_version` is frozen
> by TD-01. An implementation engineer must not build from it. It records design
> thinking for a later delta TD only.

**Before any API is finalized:**

1. A named consumer or product experiment exists (Q3).
2. Its access pattern is known, for example one exercise at a time or a whole
   workout's exercises at once.
3. A delta technical-design review compares at minimum:
   - a per-exercise request;
   - a multi-exercise or workout-oriented request;
   - any other repository-consistent shape.
4. Contract and version semantics are then finalized in that delta TD.

PR2 is **not authorized**. PR3 stays deferred.

### Option evaluation

| Option | Verdict |
|---|---|
| A. Service only | **PR1, the only authorized slice.** Qualifiable through service and PostgreSQL tests. Precedent: LP17-A (#423), an isolated read model before its transport |
| B. New authenticated per-exercise read endpoint | **Provisional; not authorized.** A whole-workout "last time" view would need N requests. Compare with B2 at the delta review |
| B2. Multi-exercise / workout-oriented read | **Provisional; not authorized.** Candidate for the delta review once the access pattern is known |
| C. Extend a workout-session endpoint | Rejected. Session routes are keyed per session, but history is per exercise across sessions. Extending `/training/bootstrap` (L11's consumer) would widen a grandfathered baseline member (Authority Rule 6) |
| D. Put history into the TI-03 insight | Rejected. It would mix facts into a derived output (D3) |

### Route sketch (NON-BINDING)

```
GET /api/v1/training/exercises/<exercise_id>/performance-history
```

This would be a **Bearer-authenticated `/api/v1` read surface**. Bearer
authentication establishes the user's identity. It does not prove that the
caller is the native app.

- **Module.** `app/blueprints/mobile_exercise_history.py`, imported in
  `mobile_api.py` beside `mobile_exercise_notes`. It is a sibling of
  `/training/exercises/<exercise_id>/note`, which keeps one per-exercise URL
  space.
- **Decorator order:** `@bp.get(...)`, then `@_rollout_gated` (outermost,
  `abort(404)` while not ready), then `@require_mobile_auth`. This is the
  LP17-B1 pattern (`mobile_today_guidance.py:29-50`).
- **Input.** Only the path segment. No query string, body or header beyond auth
  is read; the architecture test asserts the view does not reference
  `request`.
- **Rate limit.** The `mobile_api` defaults apply. There is no new limit
  (one indexed SELECT and no provider call).
- **Caching.** The `mobile_api` blueprint `no-store` applies.
- **Shared residuals.** Principal binding at limiter time, default-limit 429,
  and OPTIONS/405 rule disclosure would follow the established LP17-B1 record
  (`CLAUDE.md` LP17-B1; `docs/LP17_B1_NATIVE_TODAY_GUIDANCE_HTTP.md`). They are
  not restated here.

**Deliberate provisional differences from the sibling note route**
(`mobile_exercise_notes.py`):

| Aspect | Note route today | This sketch |
|---|---|---|
| Malformed ID | 400 `TRAINING_NOTE_INVALID` | 400, its own code |
| Unknown ID | Same 400 (collapsed) | 404, its own code |
| Inactive ID | Same 400 (`resolve_exercise` raises) | Served (B6) |
| Flag vs auth order | `require_mobile_auth` first, then `_ready` | Flag gate first: `abort(404)` before auth (LP17-B1 pattern) |
| Flag-OFF answer | `mobile_error` 404 JSON | HTML app 404 page via `abort`, if retained |
| Owner | Bearer principal `g.mobile_user.id` | Same Bearer principal binding |

### Success body sketch (NON-BINDING; no `contract_version` is frozen)

```json
{"exercise_performance_history": {
  "exercise_id": "ex_barbell_back_squat",
  "state": "available",
  "window": {"start_date": "2026-08-15", "end_date": "2026-10-10",
             "timezone": "Europe/Istanbul"},
  "occurrences": [
    {"session_ref": "opaque_owned_reference", "checkpoint_revision": 4,
     "workout_date": "2026-10-08", "completed_at": "2026-10-08T15:02:11Z",
     "sets": [{"index": 0, "reps": 8, "weight_kg": 60.0},
              {"index": 2, "reps": null, "weight_kg": 0.0}]}
  ],
  "coverage": {"complete": true, "scanned_sessions": 9,
               "excluded_cross_date": 0, "excluded_missing_completed_at": 0,
               "unavailable_sessions": [], "scan_limit_reached": false,
               "occurrence_limit_reached": false}
}}
```

Closed key sets apply at every level:

- `unavailable_sessions[]` items are exactly
  `{"workout_date", "completed_at", "reason"}`. If such data is ever exposed,
  `workout_date` may be null ONLY for `session_invalid`, mirroring the internal
  `Optional[date]` (C-3). `completed_at` may also be null only for
  `session_invalid`.
- The `no_history` and `undetermined` bodies have the same key set, with
  `"occurrences": []`.
- `available_with_gaps` and `undetermined` have `"complete": false` and either a
  non-empty `unavailable_sessions` or `"scan_limit_reached": true`.

A future payload builder would rely on the PR1 internal invariant check
(§Internal Projection) and add only wire-level checks, such as no extra key
anywhere. Those checks would also raise `ProjectionInvariantError`.

### Errors sketch (NON-BINDING; `mobile_error` envelope)

| Case | HTTP | `code` | retryable |
|---|---|---|---|
| Flag not ready | 404 (`abort`, app not-found page) | — | — |
| Missing or invalid Bearer | existing `require_mobile_auth` answer | unchanged | unchanged |
| Malformed `exercise_id` | 400 | `TRAINING_HISTORY_INVALID_REQUEST` | false |
| Unknown `exercise_id` | 404 | `TRAINING_HISTORY_EXERCISE_NOT_FOUND` | false |
| Read failure, invariant failure or catalog failure | 503 (+`Retry-After: 15`) | one public temporarily-unavailable code | true |
| Other method | 405 (Flask) | — | — |

- One public 503 is acceptable because the client action is identical for all
  three failure classes. The internal events still distinguish them
  (§Observability).
- Every refusal uses one fixed public message per code. No SQL, exception text,
  row data or exercise name is returned.
- The 503 is caught **in the view**, so it never reaches the blueprint's
  auth-flavoured catch-all (LP-04 and LP17-B1 hazard).
- There is no `Session-Resolution` header, because this is not a session
  command.

## Authentication and Ownership

- **Owner** = the `user_id: int` the service receives. The service signature
  has no other owner input. Any future caller must take it from its
  authenticated principal. The provisional read surface would capture
  `g.mobile_user.id` once in the view.
- The single SQL predicate `WorkoutSession.user_id == user_id` is applied
  before the order and limit. The exercise ID is matched after the owner
  filter, in Python, inside rows that already belong to the owner. It cannot
  widen the row set.
- **No existence oracle.**
  - A history response describes only the caller's rows. Another user's
    sessions change nothing: not the count, timing, state or error.
  - "Not found" for an exercise depends only on the global catalog.
- **Unauthenticated requests (provisional read surface only).** They would
  follow the existing `require_mobile_auth` behaviour once the flag is ready.
  While the flag is not ready, every caller would get 404 before auth.

## Ordering and Bounds

| Question | Answer |
|---|---|
| Direction | Newest first |
| Primary key | `completed_at DESC` (ADR D1a: `completed_at` orders) |
| Tie-break | `public_id DESC` in **byte order**: `COLLATE "C"` on PostgreSQL, default `BINARY` on SQLite (T2). This is never the database's default collation. Ties need identical microsecond `completed_at` values and are practically fixture-only. PostgreSQL is authoritative for qualification (H-P4) |
| Comparator | One comparator everywhere: `(completed_at DESC, public_id DESC byte order)`. Python preserves SQL row order and never re-sorts. The internal invariant checks the same comparator (Python code-point order = byte order) |
| Does `workout_date` order? | No. It decides window membership only. For eligible rows `app_date_of(completed_at) == workout_date`, so date order and completion order agree |
| Duplicate-day sessions | Separate occurrences, each with its own `session_ref`, never merged or summed |
| Same exercise twice in one session | Impossible as a fact. The parser rejects a duplicate `exercise_id` (`checkpoint.py:215-216`), so the whole checkpoint becomes `checkpoint_invalid` |
| Duplicate set index | Rejected by the parser (`checkpoint.py:237-238`), giving `checkpoint_invalid` |
| `checkpoint_revision` | Identity and provenance only. Never an ordering input |
| Set order inside an occurrence | Ascending `index` |
| Identity vs display order | Identity = (session_ref, checkpoint_revision, exercise_id, index). Display order = the sort above. Neither is derived from the other |

## Feature Flag / Rollout — PROVISIONAL — NOT AUTHORIZED

- **PR1 has no flag.** It has no reachable surface (no route and no caller).
  Precedent: LP17-A and plan_mutation PR1.
- Everything below is a **NON-BINDING / PROVISIONAL** sketch for the future read
  surface. It is not approved and must not be implemented from this TD. The
  delta TD that approves an API (§API / Contract Design) must finalize or
  replace it.
- The sketch: a future read surface would register one `FeatureFlag` in
  `ROLLOUT_FLAGS`. It would write no other registry.

```
key            FITX_EXERCISE_PERFORMANCE_HISTORY_ENABLED
default        False
lifecycle      shipped_dark
decision       enable
depends_on     ("FITX_WORKOUT_SESSIONS_ENABLED", "MOBILE_AUTH_ENABLED")
capability     TD-01 read-only Bearer GET /api/v1/training/exercises/<exercise_id>/
               performance-history over completed WorkoutSession checkpoints; never writes
prerequisites  FITX_WORKOUT_SESSIONS_ENABLED=1; TD-01 PG + contract qualification green;
               a reviewed consumer exists before production enablement
abort_signals  any cross-account fact; no_history with unavailable rows (must be impossible);
               any WorkoutLog statement observed; unexpected read_failed rate;
               any invariant_failed or catalog_unavailable event
rollback       Set FITX_EXERCISE_PERFORMANCE_HISTORY_ENABLED=0; GET answers 404 before auth.
               No schema or data to clean up.
```

- **Readiness at request time** = history flag ON **and**
  `FITX_WORKOUT_SESSIONS_ENABLED` ON (the `_insights_enabled` precedent). With
  history ON and sessions OFF, the surface is absent; it is never advertised.
  `MOBILE_AUTH_ENABLED` OFF means `mobile_api` is not registered.
- It does not depend on the P0 or P1 TI flags.
- **Rollout states:** OFF (production default) → local/test (config fixture) →
  staging qualification (staging `.env` with history=1 and sessions=1; this run
  is G7) → production enablement. The last step needs a separate human
  decision and the gates listed in §Rollout and Rollback.
- **Availability vs coverage.** Turning the flag on in production says nothing
  about how much history exists. If sessions are OFF or rarely used in
  production (U1–U3), the correct answers are mostly `no_history` or
  `undetermined`. Sparse history is not a failure of this slice.

## Performance and Storage

**Existing indexes** (`models.py:1389-1407`, migration `a994f9bed783`):
- `ix_workout_session_user_id (user_id)`;
- `ix_workout_session_user_status (user_id, status)`;
- `uq_workout_session_public_id (public_id)`;
- the partial `uq_workout_session_active_owner (user_id) WHERE status='active'`.

**Query shape.** The predicate `user_id = ? AND status = 'completed'` matches
`ix_workout_session_user_status` exactly. `workout_date IN (57 keys)` is a heap
filter over the owner's completed rows. Sort plus `LIMIT 64` runs over the
filtered set (realistically ≤57 rows).

**Candidate cost.** Index entries visited = the owner's lifetime COMPLETED
sessions. That is at most about one per day, so about 365 per active year. This
is the same profile TI-03 shipped with (`training_intelligence/queries.py`). It
is per-owner, not global, and grows linearly with one user's tenure.

**Payload cost.**
- Worst case is bounded by write-path validation: 64 rows × 64 KiB checkpoint,
  roughly 4 MiB theoretical. Context and prescription are not selected (T7).
  The worst case is only reachable with pathological maximal workouts every day.
- Realistic rows are 1–5 KiB.
- JSON parsing: ≤64 `load_snapshot` + `parse_stored_exercises` calls.
- Known risk R4 covers the theoretical case. PR1's PG test records the
  statement count and returned row count; it does not claim latency.

**Verdict.**
- MIGRATION REQUIRED: **NO**.
- NEW INDEX REQUIRED: **QUALIFICATION-DEPENDENT**.
- NEW PERSISTED READ MODEL: **NO**.

PR1 may merge without a new index if correctness and its test qualification
pass, because it has no production caller.

**Representative PostgreSQL qualification (G9; before ANY production exposure
or enabling of a future read surface).**

- *Fixture:*
  - one target owner with ≥1,000 COMPLETED `WorkoutSession` rows, one per
    Istanbul day, so 57 fall in the window;
  - ≥20 other owners with ≥1,000 COMPLETED rows each, inserted interleaved with
    the target's, plus some ACTIVE and ABANDONED rows;
  - realistic checkpoints of 2–8 KiB (about 6 exercises × 4–5 sets);
  - `ANALYZE` after the load.
- *Statement:* the exact PR1 selection query (§The one statement), with the
  production bind values.
- *Evidence:* `EXPLAIN (ANALYZE, BUFFERS)`, one cold run and then 5 warm runs.
  Record the plan, the median warm `Execution Time` and the shared buffers
  (hit + read). Repeat on a 4,000-row target owner (about 11 years daily) to
  show tenure growth.
- *Recorded environment (required with every result):*
  - the PostgreSQL version (`SELECT version()`);
  - the host or CI runner;
  - the machine or instance class;
  - the dataset shape: target-owner rows, other owners and their rows, ACTIVE
    and ABANDONED rows, checkpoint size range, and whether `ANALYZE` ran;
  - the warm or cold status of each run.
- *Context only:* the non-AI `HttpLatency` p95 objective is ≤800 ms
  (`docs/OBSERVABILITY.md:230`). This statement should use only a small share
  of it. The numbers below are **not** derived from that objective.

**QUALIFICATION ESCALATION THRESHOLDS FOR TD-01 (C-2).** The thresholds below
exist only to decide whether this slice needs a separate index review. They
are NOT:

- product performance guarantees;
- production SLOs;
- general AxisAI latency standards;
- derived from existing production measurements (none exist for this
  statement).

*Primary signals (plan shape and buffers; environment-stable).* Any one of
these triggers index escalation review:

1. the plan contains a `Seq Scan` on `workout_session` where the owner/status
   index path (`ix_workout_session_user_status` or `ix_workout_session_user_id`)
   is expected;
2. the shared buffers (hit + read) for the statement exceed **4,000 pages**
   (about 31 MiB) on the 1,000-row owner;
3. shared buffers grow faster than owner rows from 1,000 to 4,000, meaning the
   4,000-row figure is more than 4× the 1,000-row figure.

The 1k → 4k scaling (buffers and execution time, each as a ratio) is always
reported explicitly, whether or not a trigger fires.

*Latency (PROVISIONAL HOST-SPECIFIC ESCALATION TRIGGERS).*

- The triggers are a median warm `Execution Time` above **25 ms** on the
  1,000-row owner, or above **50 ms** on the 4,000-row owner.
- They are valid only together with the recorded environment above, and must
  not be interpreted outside it.
- A run on a different host or PostgreSQL version records its own numbers. It
  is not compared against these values.

*Cold run.* The cold execution result is recorded and reported, but it is not a
pass/fail threshold unless later evidence supports one. It exists to inform
the human index decision.

*Outcome.* G9 never authorizes an index by itself.

- If any trigger fires: **HOLD**, and open a separately reviewed
  index/migration decision. The candidate is `(user_id, status,
  workout_date)`.
- No index is authorized by TD-01, and TD-01 invents no migration.

**No caching** of any kind: no Redis, process memory or HTTP cache. Rows are
immutable, but caching adds an invalidation surface (account deletion) that
gives no benefit at this cost.

## Security and Privacy

- Authenticated owner only. There is no client-supplied `user_id`, date or
  limit input.
- No cross-user leakage, by the single owner predicate. Two-account isolation is
  tested on SQLite and PostgreSQL.
- `exercise_id` never reaches SQL, so no injection surface exists. Path
  segments are bounded at 64 chars.
- Errors are code-only and catalog-scoped. No row-existence fact about another
  user exists to leak.
- **Logs (future caller only; PR1 has none).** The caller logs only on failure.
  Each line carries the failure class (`read_failed`, `invariant_failed` or
  `catalog_unavailable`), the exception **type** and `request_id` (the LP17-B1
  and TI-03 `_unavailable` pattern). Logs never contain:
  - checkpoint, context or prescription payloads;
  - `session_ref` / `public_id`, owner ID or e-mail;
  - exercise IDs.
- **Corrupt-row diagnostics (future caller only).** One WARNING line per
  request with ≥1 `checkpoint_invalid` or `session_invalid` row. It carries
  fixed reason tokens and counts plus `request_id` only. `checkpoint_missing`
  is metric-only, not logged, because it is a legitimate state.
- **Finding a corrupt row.** This is an operator action: a documented read-only
  SQL audit filtered by owner and status. It never relies on log contents.
  TI-00 §18 forbids logging session identifiers.
- The service package does not log at all; the architecture guard forbids
  `logger`, `print` and `current_app`.

## Observability

**PR1 emits nothing.** It has no caller, and the package neither logs nor
records metrics (H-A2, H-A6). PR1's obligation is to make every class below
**mechanically distinguishable** to a future caller:

- each failure class is a distinct exception type that the service never
  converts (§State machine);
- every source-row class is a separate reason token or count in `Coverage`
  (H-C16).

**Event vocabulary.** The vocabulary below is binding. Emitting it is
provisional, and belongs to the future read surface. That surface would add
`metrics.py` with one metric, `ExercisePerformanceHistory`, and one dimension,
`Event`. The event set is fixed and closed. The calls are best-effort and
swallow exceptions, as in `training_intelligence/metrics.py`.

| Event | Class | When (once per request) |
|---|---|---|
| `available` / `available_with_gaps` / `no_history` / `undetermined` | outcome | The success state |
| `checkpoint_missing` | source gap (legitimate) | ≥1 row with SQL NULL checkpoint at revision 0 |
| `checkpoint_invalid` | source defect | ≥1 checkpoint whose canonical re-validation raised `InvalidSessionRequest`, including `""` |
| `session_invalid` | source defect | ≥1 row with an unparseable `workout_date` (defensive) |
| `completed_at_missing` | anomaly | ≥1 COMPLETED row with null `completed_at`. It stays excluded under the dating rules (B4) |
| `scan_limit_reached` | coverage | `L` is true |
| `occurrence_limit_reached` | coverage | More than 8 occurrences existed |
| `invalid_request` / `exercise_not_found` | input | `ExerciseIdentityInvalid` / `ExerciseUnknown` |
| `read_failed` | failure (transient) | Repository or database read failure (`SQLAlchemyError`) |
| `invariant_failed` | failure (deterministic) | `ProjectionInvariantError`, or any other exception escaping the pure validation or selection layer (including `RecursionError`). Never emitted for `InvalidSessionRequest` from re-validation, which is `checkpoint_invalid` |
| `catalog_unavailable` | failure (configuration) | `CatalogConfigurationError` |

- The generic `source_unavailable` event of the first draft is retired. It
  conflated legitimate missing state with corruption.
- The three failure classes may share one public client answer, because the
  client action is identical (§API / Contract Design). They never share an
  internal event.
- No exercise, owner, session or count values appear as dimensions. No raw
  checkpoint payload appears anywhere.
- Request volume and latency come from the existing `mobile_api`
  HttpRequests/HttpLatency SLIs.
- An owner-isolation failure is never a metric. It is a test failure and an
  abort signal.

## Failure Semantics

| Failure | Service result (PR1) | Event class | Provisional public answer | Never |
|---|---|---|---|---|
| DB error or timeout | `SQLAlchemyError` propagates | `read_failed` | 503 retryable (rolled back, logged by type) | `no_history`, `undetermined`, an empty success |
| Catalog asset unloadable | `CatalogConfigurationError` | `catalog_unavailable` | 503 retryable | 404 / 400 |
| Projection invariant violated (bug) | `ProjectionInvariantError` | `invariant_failed` | 503 retryable | A non-contract body; `read_failed` |
| Unexpected exception in validation or selection (any class other than `InvalidSessionRequest` from re-validation or `ValueError` from the date parse, e.g. `RecursionError` on pathological out-of-band JSON) | Propagates unchanged | `invariant_failed` | 503 retryable | `checkpoint_invalid`, `checkpoint_missing`, `no_history`, `undetermined`, a gap marker |
| A row's checkpoint missing (NULL, revision 0) | Gap marker `checkpoint_missing`; state `available_with_gaps` or `undetermined` | `checkpoint_missing` | success body | Silent skip; `no_history` |
| A row's checkpoint invalid: re-validation raised `InvalidSessionRequest` (incl. `""`) | Gap marker `checkpoint_invalid`; same states | `checkpoint_invalid` | success body | Silent skip; partial salvage |
| COMPLETED with null `completed_at` | Excluded (B4); counted | `completed_at_missing` | success body | Re-dating; a coverage gap |
| Flag not ready | — (no surface in PR1) | — | 404 before auth | 401/429/503 leaking the surface (beyond the shared residuals named in the LP17-B1 record) |

ADR D5 holds: no fabricated empty, neutral or "stable" result, and no mutation.

## Test Matrix

`H-` IDs are binding implementation obligations. Unless noted, tests are
SQLite unit or service tests. They reuse `tests/ti03_support.py` row builders
where useful; reuse is test-only and the production package must not import
them.

### Architecture guards (`tests/test_exercise_performance_history_architecture.py`, AST, modelled on `tests/test_ti03_architecture.py:52-136`)

| ID | Guard |
|---|---|
| H-A1 | The package has the frozen PR1 module set (`models`, `queries`, `selection`, `__init__`; no `payload`/`metrics`); `selection.py` and `models.py` import nothing from `app.extensions`, `app.models`, `sqlalchemy` or `flask` |
| H-A2 | No write call anywhere in the package (`add`/`add_all`/`flush`/`commit`/`merge`/`execute`/`delete`/`update`/`with_for_update`/`begin_nested`/`rollback`/`connection`), and no attribute assignment |
| H-A3 | Only `queries.py` imports `app.extensions`. Its `app.models` imports are exactly `{WorkoutSession, WORKOUT_SESSION_COMPLETED}`. Its selected column set is pinned exactly to `{public_id, workout_date, completed_at, checkpoint_revision, checkpoint_data}`: `execution_context_data` and `prescription_data` are never selected (T7) |
| H-A4 | Forbidden import prefixes: `training_history`, `training_progression`, `training_planning`, `weekly_program`, `progress_`, `adaptive_plan_context`, `workout_state`, `today_facts`, `today_presenter`, `mobile_today`, `today_guidance_read_model`, `analytics_engine`, `context_builder`, `coach_handoff`, `ai`, `ai_coach`, `ai_pipeline`, `coach_plan_tools`, `coach_plan_policy`, `plan_mutation`, `plan_replacement`, `plan_confirmation`, `training_generation`, `mobile_training`, `mobile_training_generation`, `training_intelligence`, `workout_session.prior_performance`, `workout_session.context` (`project_context`), `workout_session.prescription`, `workout_completion`, `pump_check`, `exercise_notes`, `app.blueprints`, `fitx_mcp`, `app.prompts`, plus providers and randomness |
| H-A5 | Forbidden model or symbol names: `WorkoutLog`, `TrainingPlan`, `PumpCheck`, `ExerciseNote`, `PlanMutationRecord`, `WORKOUT_COMPLETION_MARKER`, `fetch_workout_entries`. The string literals `workout_log` and `exercise_note` are also forbidden in the package source |
| H-A6 | Forbidden derived names: `e1rm`, `epley`, `brzycki`, `one_rep_max`, `volume`, `trend`, `progress`, `recommend`, `deload`, `recovery`, `score`. Also no `logger`, `print` or `current_app`. `app_today` may appear only in `__init__.py` |
| H-A7 | (Provisional; for the future read-surface TD, not PR1) The route view is a thin transport: its calls are a subset of `{_ready, build_exercise_history, history_payload, record_history_event, mobile_error, jsonify, rollback, …}`, and it does not reference `request` |
| H-A8 | (PR1) **Zero production callers.** Repo-wide, no module outside `app/services/exercise_performance_history/` and `tests/` imports the package or calls `build_exercise_history`. No Coach, `context_builder`, Today, Progress or blueprint module imports it. A later approved slice must change this guard explicitly |
| H-A9 | `resolve_exercise` is not called by the package; `resolve_historical_exercise` is |
| H-A10 | (C-1) Exception discipline. The package contains exactly two `except` handlers: `except InvalidSessionRequest` around the canonical re-validation call, and `except ValueError` around `date.fromisoformat` in the date rule. Each `try` body holds only that one statement. There is no bare `except`, no handler naming `Exception`, `BaseException`, `RecursionError` or any other class, and no handler tuple. A non-vacuity mutation, such as widening the re-validation handler to `Exception`, fails the guard |

### Runtime read-only and source proof (SQL capture)

| ID | Test |
|---|---|
| H-R1 | A `before_cursor_execute` listener over a full service call records exactly **one** statement: a `SELECT` whose `FROM` is only `workout_session`. No other table appears, `workout_log` in particular |
| H-R2 | Targeted ORM side-effect checks after each service call: `db.session.new`, `db.session.dirty` and `db.session.deleted` are empty, and a session `after_commit` listener (or `commit` spy) records zero commits. No table hashing: H-R1's runtime SQL capture is the authoritative proof that exactly one `SELECT` on `workout_session` ran and nothing else |

### Canonical selection

| ID | Case | Expected |
|---|---|---|
| H-S1 | COMPLETED with completed sets | Occurrence |
| H-S2 | ACTIVE row with a valid checkpoint containing the exercise | Absent; not scanned or counted |
| H-S3 | ABANDONED with a valid checkpoint | Absent; not scanned or counted |
| H-S4 | `completed:false` sets with prefilled reps/weight | Not facts. If no set is completed, there is no occurrence |
| H-S5 | Mixed completed and uncompleted | Only completed indices, ascending |
| H-S6 | `reps: null` | `null` in output |
| H-S7 | `weight_kg: null` | `null`, never 0 |
| H-S8 | `reps: 0`, `weight_kg: 0` / `0.0` | Exactly `0` / `0.0` |
| H-S9 | A bodyweight-equipment exercise with null weight | `null`; nothing inferred, and the catalog equipment is never read |
| H-S10 | The same checkpoint with `execution_context_data` / `prescription_data` NULL, valid (anchor-bound RIR/tempo) or malformed | Byte-identical results. This proves v1 never reads them (T7) |
| H-S11 | Field-set pin on `PerformedSet`, `Occurrence`, `Coverage` and `ExerciseHistory` | No `actual_rir`, `tempo_adherence`, `actual_rest`, prescription or other context-derived field exists |
| H-S12 | `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` ON vs OFF over the same rows | Identical results; no read of that flag |
| H-S13 | — | Retired in revision 1 (folded into H-S10) |
| H-S14 | `completed_at` NULL | Excluded; `excluded_missing_completed_at += 1`; coverage still complete |
| H-S15 | Cross-date (`completed_at` 00:10 Istanbul the next day) | Excluded, never re-dated; `excluded_cross_date += 1`; coverage still complete |
| H-S16 | Window edges: `anchor−56` included, `anchor−57` not scanned; a session completed today is included | — |
| H-S17 | The window is computed from `app_today()` (the Istanbul day), not UTC; pinned around 21:00–23:59 UTC | — |
| H-S18 | The exercise is present in a session with zero completed sets | Contributes nothing; not a gap |
| H-S19 | More than 8 occurrences | The 8 newest; `occurrence_limit_reached`; state unaffected |

### Corruption and availability

| ID | Case | Expected |
|---|---|---|
| H-C1 | `checkpoint_data` is non-JSON text | `checkpoint_invalid` |
| H-C2 | JSON array, or object with wrong keys | `checkpoint_invalid` |
| H-C3 | Duplicate `exercise_id` | `checkpoint_invalid`; that exercise's sets **not** used |
| H-C4 | Duplicate set index | `checkpoint_invalid` |
| H-C5 | `reps: -1`, `weight_kg: NaN` / `1001` / `true`, `completed: 1` | `checkpoint_invalid` |
| H-C6 | SQL NULL data at revision 0 | `checkpoint_missing` (the only way to get it) |
| H-C7 | NULL data at revision > 0 | `checkpoint_invalid`. A valid snapshot at revision 0 is decided by the parser alone, as in TI-03 (it is ELIGIBLE, with fact identity revision 0) |
| H-C8 | Only candidate is corrupt | `undetermined`, **never** `no_history` |
| H-C9 | One valid occurrence plus one corrupt newer row | `available_with_gaps`; the marker's `completed_at` is newer than the occurrence |
| H-C10 | A corrupt checkpoint that contains otherwise-valid sets for the exercise | No partial salvage: none of its sets appear |
| H-C11 | Property test: for random mixes of valid, corrupt and missing rows, `state == "no_history"` ⇒ zero unavailable ∧ ¬scan_limit. Generated with seeded data, not `random` in product code |
| H-C12 | Scan bound: 64 valid rows in the window with no match | `undetermined` (scan_limit_reached) |
| H-C13 | ≥9 valid occurrences, plus one corrupt row **older** than the 8th returned occurrence | `available_with_gaps`: 8 clean returned occurrences, but the whole scanned window is incomplete (m-3) |
| H-C14 | 64 scanned rows, ≥8 of them valid occurrences | `available_with_gaps` (`scan_limit_reached`), never `available` |
| H-C15 | `checkpoint_data == ""` at revision 0 and at revision ≥ 1 (also `" "`) | `checkpoint_invalid`, never `checkpoint_missing` |
| H-C16 | Class separation: (a) the query raises `OperationalError`; (b) the catalog loader raises `CatalogConfigurationError`; (c) an invariant violation is injected | (a) `SQLAlchemyError` propagates unchanged; (b) `CatalogConfigurationError`; (c) `ProjectionInvariantError`. None returns an `ExerciseHistory`, and none is converted into another class. Coverage keeps separate counts for `checkpoint_missing`, `checkpoint_invalid`, `session_invalid`, `cross_date` and `missing_completed_at` |
| H-C17 | New account (no rows) | `no_history`, `scanned_sessions == 0` |
| H-C18 | Internal invariant check, fed hand-built histories that violate each invariant (out-of-order occurrences by the comparator, `no_history` with an unavailable row, >8 occurrences, unordered set indices, out-of-range values, a non-`session_invalid` marker with `workout_date=None`, a `session_invalid` marker with a date) | `ProjectionInvariantError` for each, and the mutation is proven to fail the check |
| H-C19 | (C-1) Exception boundary: (a) a checkpoint the parser refuses (`InvalidSessionRequest`); (b) `parse_stored_exercises` monkeypatched to raise `RuntimeError`, `KeyError` and `ValueError`; (c) `checkpoint_data` that is pathologically nested JSON (e.g. `"[" * 200_000 + "]" * 200_000`), which raises `RecursionError` | (a) `checkpoint_invalid`. (b) and (c) the exception propagates unchanged out of `build_exercise_history`: no `ExerciseHistory`, no gap marker, and never `checkpoint_invalid`, `checkpoint_missing`, `no_history` or `undetermined` (`invariant_failed` class) |
| H-C20 | (C-3) A row whose `workout_date` cannot be interpreted (`"2026-13-45"`, `"garbage"`, `""`, a non-string), fed to `selection.classify` directly, because the `IN` filter makes it unreachable through SQL. A `checkpoint_invalid` row and a `checkpoint_missing` row with valid dates sit alongside it | The bad row becomes `UnavailableSession(workout_date=None, reason="session_invalid")`, with `completed_at` exactly as stored; it is counted and coverage is incomplete. The date is never `app_today()` or the `completed_at` day. The other two markers carry their parsed `workout_date` |

### Exercise identity

| ID | Case | Expected |
|---|---|---|
| H-I1 | Active ID | Served |
| H-I2 | Catalog fixture with the same ID flipped to `active:false` | Served identically. A spy proves `resolve_exercise` is not called |
| H-I3 | `ex_never_assigned` | `type(exc) is ExerciseUnknown`, asserted on the exact class (provisional HTTP 404) |
| H-I4 | `Squat`, `ex-`, `ex_A`, 65-char ID, `%00`, empty | `type(exc) is ExerciseIdentityInvalid`, asserted on the exact class. `isinstance` is not sufficient: `ExerciseUnknown` subclasses `ExerciseIdentityInvalid`, so an implementation that wrongly raised `ExerciseUnknown` for malformed input would still pass it (C-4). Provisional HTTP 400 |
| H-I5 | Deletion and coarse-reclassification guard (`tests/test_exercise_catalog_identity_guard.py`) | The frozen 73-ID list is a subset of the catalog, with movement and region unchanged. It does not prove semantic identity stability (§Exercise Identity) |

### Ownership

| ID | Case |
|---|---|
| H-O1 | Owner receives own history |
| H-O2 | User B has identical exercise IDs and dates; A's response is byte-identical with and without B's rows present |
| H-O3 | B's corrupt rows do not change A's state |
| H-O4 | (Provisional; future read-surface TD) `?user_id=<B>` and a `user_id` header are ignored; the response equals the plain request |
| H-O5 | (Provisional; future read-surface TD) No Bearer, an expired Bearer, a web cookie only → the existing `require_mobile_auth` answers |

### Ordering

| ID | Case |
|---|---|
| H-D1 | Newest-first by `completed_at` across days |
| H-D2 | Two sessions on one `workout_date` → two occurrences ordered by `completed_at` |
| H-D3 | Equal `completed_at` → `public_id DESC` in byte order. The fixture IDs differ only in case and in `-`/`_` (for example `"B…"`, `"a…"`, `"-…"`, `"_…"`), and the test asserts the required order `a… > _… > B… > -…`. SQLite uses `BINARY`; PostgreSQL is authoritative (H-P4) |
| H-D4 | Repeated calls on fixed data give identical bytes |

### Absence and error state (provisional; carried to the future read-surface TD, not PR1 obligations)

PR1's service-level equivalents are H-C8, H-C16 and H-C17. The transport rows
below are kept only as input for that later TD.

| ID | Case | Expected |
|---|---|---|
| H-E1 | New account | 200 `no_history`, `scanned_sessions: 0` |
| H-E2 | Only corrupt rows | 200 `undetermined` |
| H-E3 | The query raises `OperationalError` (monkeypatched) | 503 `TRAINING_HISTORY_TEMPORARILY_UNAVAILABLE`, `Retry-After: 15`, rolled back; never 200 |
| H-E4 | `ProjectionInvariantError` injected | 503 |
| H-E5 | Malformed / unknown ID | 400 / 404 with fixed messages |
| H-E6 | History flag OFF, or sessions flag OFF | 404 before auth. A spy proves the service never ran, and no 401 is returned without a token |
| H-E7 | Closed key sets for all four states. Golden payload fixtures committed | — |
| H-E8 | Inventories updated: the exact path in `test_mobile_auth_feature_gate.py` and `test_sprint12_daily_coach_discovery.py`; the flag record passes the registry tests | — |

## PostgreSQL Qualification

SQLite cannot prove:

- PostgreSQL ordering of ties and NULLs (`NULLS LAST`, and the explicit
  `public_id COLLATE "C"` tie order, which SQLite cannot even express);
- `IN` matching on the real `VARCHAR(10)` column;
- statement count on the production dialect;
- naive-timestamp round trips;
- owner isolation under the real planner.

`tests/test_exercise_performance_history_pg.py`:
- marked `pg_concurrency`;
- gated by `FITX_PG_CONCURRENCY_TEST=1` and `PG_TEST_DATABASE_URL`, as
  `tests/test_ti03_pg.py` is;
- **added to the explicit CI list**
  (`.github/workflows/ci.yml:354-388`, `mobile-pg-concurrency`). The triage
  guard fails if a `pg_concurrency` module is missing from that list.

| ID | PG proof |
|---|---|
| H-P1 | Exactly one history SELECT per service call, and no other table |
| H-P2 | Window edges: 57 day keys inclusive; a row at `anchor−57` is not returned |
| H-P3 | `NULLS LAST`: a null-`completed_at` COMPLETED row sorts after dated rows and is counted as excluded |
| H-P4 | Ties, asserting the **required** order. Equal `completed_at` with the H-D3 IDs must come back as `a… > _… > B… > -…` (byte order). The test also records `datcollate`. When the database default is not `C`/`POSIX` (CI `postgres:16` defaults to `en_US.utf8`), it proves non-vacuity by showing that the same IDs ordered without `COLLATE "C"` give a different order. Removing the collation from `queries.py` therefore fails the test |
| H-P5 | `LIMIT 64` → `scan_limit_reached`; 65 rows give 64 scanned |
| H-P6 | Two owners with interleaved identical rows → strict isolation, both directions |
| H-P7 | Full lifecycle: start → checkpoint → complete through the real services (pin `workout_completion.service.datetime` as TI-03 does — the TEST HAZARD in CLAUDE.md), then read history and get the exact facts |
| H-P8 | Corrupt row inserted by raw UPDATE on PG → `undetermined` / `available_with_gaps` |

No JSON SQL behaviour is relied on, so no PG JSON test is needed. That absence
is itself asserted by H-R1, which allows only one plain SELECT.

The representative `EXPLAIN (ANALYZE, BUFFERS)` qualification (G9, §Performance
and Storage) is **not** part of the PR1 suite or a PR1 merge gate. It is
required before any production exposure.

## Client / Contract Qualification

**CLIENT IMPLEMENTATION DEFERRED.**

- No Flutter, browser or Coach consumer is part of TD-01.
- The backend foundation is independently qualified by the service and PG
  suites (PR1). API contract tests and a staging smoke belong to a future,
  separately approved read surface.
- A future typed client (PR3, not authorized here) must:
  - decode only the contract version the future API TD freezes, and treat an
    unknown version, state or reason token as a decoding failure (fail closed);
  - never re-select, re-order, filter or merge occurrences, and never compute
    derived values from them;
  - render `undetermined` and `available_with_gaps` as explicitly incomplete,
    never as empty or complete;
  - keep server capability AND the local kill switch (TI-00 §17).
- No mobile UI redesign is implied.

## Rollout and Rollback

- **Gate placement.**

  | Step | Staging required? | Gates |
  |---|---|---|
  | **PR1 merge** | **No.** "Staging is stopped" never blocks merging the fully qualified read-only service | G0–G5, G8 |
  | Future dark read-surface PR merge | Not inherently required merely to merge dark code; decided by that slice's own TD | That TD's gates |
  | Staging flag enablement | The staging run itself **is** G7. It needs owner approval to start staging, which is STOPPED by default | G7 |
  | Production enablement | — | At minimum: G7 PASS; an approved consumer; relevant production configuration evidence (U1); G9 representative PostgreSQL performance qualification; all later contract-specific gates. Separate human decision; outside TD-01 |

- **Rollout.** PR1 merges dark, since it has no surface and no caller.
- **Rollback.**
  - PR1: code revert. There is no data.
  - A future read surface: flag `0` makes the route 404 immediately, with no
    deploy; then code revert.
- **Confirmed:**
  - no backfill;
  - no data migration;
  - no destructive state;
  - no canonical-history rewrite;
  - no cache to purge;
  - no client state to invalidate.

No part of the design makes rollback more complex than this.

## Alternatives Considered

| Alt | Correctness under ADR 0002 | Size | Reuse / coupling | Corruption semantics | Migration | Verdict |
|---|---|---|---|---|---|---|
| A. Dedicated canonical history service over `WorkoutSession` | Exact D1a via canonical primitives | Small (≈4 modules + helper) | Reusable read foundation; coupled only to `workout_session` primitives and the catalog | Explicit 4-state model | None | **Selected** |
| B. Reuse `prior_performance` | Fails L11: silent skip, per-entry salvage, cross-date and null-`completed_at` kept | Smallest | Coupled to a grandfathered browser bootstrap | Corrupt = invisible | None | Rejected |
| C. Reuse `WorkoutLog` / Sprint 6 history | Violates D2 and Authority Rules 1, 2 and 6 | — | — | Name-only rows, 0-for-unknown (L2) | None | Rejected |
| D. Normalized persisted history table / read model | A second store (D8); needs a backfill and a dual-write correctness proof | Large | — | Must re-solve corruption at write time | Required | Rejected |
| E. Each consumer queries checkpoints itself | Rules drift per consumer (ADR Alt E) | Repeated | High duplication | Inconsistent | None | Rejected |
| F1. Import TI-03 `queries`/`facts` | Correct facts, but consumes a derived-output package (D3) and its `inconsistent_date` lumping | Small | Couples history to the TI ruleset lifecycle | TI semantics | None | Rejected; replaced by the parity test H-X1 |
| F2. Extend `/training/bootstrap` or session reads | Widens a grandfathered baseline member, or a per-session surface | Small | Wrong shape | — | None | Rejected |

**Parity safeguard (H-X1, PR1).** A test builds one row matrix and feeds it to
both TI-03 `facts.parse_session` and TD-01 `selection.classify`. It asserts:

- `observed` ⇔ ELIGIBLE;
- `unavailable` ⇔ UNAVAILABLE(`checkpoint_missing` | `checkpoint_invalid`);
- `inconsistent_date` ⇔ EXCLUDED;
- for each exercise, the set of `completed: true` indices and their `reps` and
  `weight_kg` values are identical;
- the exception boundary is identical (C-1). A checkpoint the parser refuses with
  `InvalidSessionRequest` is `unavailable` in TI-03 and `checkpoint_invalid` in
  TD-01. Any other exception, injected or caused by pathologically nested JSON,
  propagates out of both `parse_session` and `classify`, and neither converts
  it.

**Scope limit.** H-X1 compares only the fact semantics shared through ADR 0002:

- canonical checkpoint acceptance or rejection;
- performed-set eligibility;
- core set values;
- dating.

It must **not** make TD-01 inherit TI-03's:

- response states;
- coverage model, including `history_unavailable` lumping and the T4 reporting
  difference;
- recommendation behaviour;
- error handling;
- ordering, including tie collation;
- RIR, tempo or interval fields.

The test protects against fact-semantic drift. It does not assert that the two
are the same service.

The matrix leaves out rows with an unparseable `workout_date`. Those are
unreachable through either query's `IN` filter: TD-01 classifies them
conservatively as UNAVAILABLE(`session_invalid`), while TI-03 calls them
`inconsistent_date`. A separate test (H-C20) pins that row's TD-01 class. Tests may
import both packages. This keeps two readers of one store from
drifting without coupling the production code.

## Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| R1 | Production holds little or no session history (U1–U3), so the feature is correct but sparse | Product | The flag is not enabled without a consumer and U1 evidence. Sparse history is not an architecture failure |
| R2 | Revision-0 completions (completing without logging) produce frequent `checkpoint_missing` gaps, so `undetermined` is common for such users | Product / UX | ADR-mandated (D1). The distinct reason token lets a client phrase it as "a workout here has no logged sets". Changing the rule would need an ADR amendment, not a TD change |
| R3 | Duplicate selection logic with TI-03 drifts | Medium | H-X1 parity test; both readers call the same canonical parser and `app_date_of` |
| R4 | Theoretical payload worst case (about 4 MiB read for one request under pathological maximal rows) | Low | Bounded by write-path validation. Realistic rows are KiB. PR1 records row count and statement count. Escalate if measured |
| R5 | Owner-lifetime index scan grows with tenure | Low | Same as TI-03. G9 representative `EXPLAIN (ANALYZE, BUFFERS)` before any production exposure, with qualification escalation thresholds (plan and buffers primary; latency provisional and host-specific; not SLOs). An index is a separate, reviewed decision |
| R6 | A catalog ID is deleted or semantically re-meant, contrary to the ADR target rule | Medium | H-I5 catches deletion and coarse reclassification only. Semantic re-meaning stays an ADR 0002 review invariant; no test fully enforces it |
| R7 | Tie order depends on the environment's collation | Low | Explicit byte-order comparator (`COLLATE "C"` / `BINARY`); H-P4 asserts the required order on PG; ties practically never occur outside fixtures |
| R8 | A consumer reads `available` occurrences while ignoring gap markers | Medium | Gaps change the **state token** itself (`available_with_gaps`). Client obligations are listed above |
| R9 | TEST HAZARD: real `datetime.utcnow()` completion stamps against a pinned app clock create accidental cross-date rows | Test-only | Pin `workout_completion.service.datetime` (the TI-03 precedent) |
| R10 | v1 carries no RIR or tempo, so a future consumer may find it less rich | Product | Deliberate (T7). Enrichment can be added later as an additive TD and contract change, after its rollout semantics are approved |

## Open Questions

Q1 (RIR/tempo under the P0 flag) is **removed**. It was resolved by deferring
RIR and tempo (T7).

- **Q2 (open; does NOT block PR1).** U1–U3 (ADR `:599-605`) are still
  unresolved. They affect production usefulness and rollout, not PR1
  correctness.
- **Q3 (open for PR1; MUST be resolved before any API/PR2 contract is
  finalized).** Which named consumer or product experiment needs this history,
  and with which access pattern. Whether PR3 (typed client) is ever needed
  follows from it. PR1 does not depend on the answer. No API can be designed
  without it (§API / Contract Design).

## Implementation Slices

Planning only. Nothing is authorized until this TD is accepted, and PR1 is the
only slice the TD can authorize.

### PR1 — Canonical history service foundation (AUTHORIZED AFTER TD ACCEPTANCE)

- **Scope.**
  - `app/services/exercise_performance_history/{models,queries,selection,__init__}.py`,
    or a final reviewed equivalent;
  - the canonical query, selection and classification, minimal internal models,
    and the internal invariant check;
  - `exercise_catalog.resolve_historical_exercise` and `ExerciseUnknown`, if
    they are still needed;
  - tests H-A1–A6, H-A8–A10, H-R1–R2, H-S*, H-C*, H-I1–I5, H-O1–O3,
    H-D1–D4, H-P1–P8 and H-X1;
  - the CI PG module list entry;
  - the record `docs/EXERCISE_PERFORMANCE_HISTORY.md` plus one `CLAUDE.md`
    line, if the repository process requires it.
- **Explicitly not in PR1:**
  - **no production caller**;
  - **no API** and **no feature flag**;
  - **no mobile or browser caller**;
  - **no Coach or Today caller**;
  - **no context enrichment**;
  - no payload or metrics module;
  - no change to TI-03, `prior_performance` or `resolve_exercise`.
- **Dependencies.** ADR 0002 (merged), this TD (accepted).
- **Qualification.**
  - Focused unit tests.
  - Architecture boundary tests, proven non-vacuous: each forbidden edit,
    applied to a scratch copy, makes its guard fail.
  - Runtime SQL capture (H-R1) and targeted ORM no-side-effect checks (H-R2).
  - PostgreSQL suite green in CI.
  - Exact-head CI green.
  - Staging is **not** required (§Rollout and Rollback).
- **Rollback.** Revert. No data.

### PR2 — NOT AUTHORIZED

No PR2 implementation task is created by this TD. A read surface may be
**designed** only after all of these hold:

- a named consumer or product experiment exists;
- Q3 is resolved;
- the access pattern is known;
- a delta TD review approves the API shape (§API / Contract Design).

The provisional sketches in §API / Contract Design and §Feature Flag are input
to that delta TD only.

### PR3 — Typed client consumption (deferred; NOT authorized)

This exists only if an approved product experiment requires it. It would be a
separate design: Flutter decoding with fail-closed tokens and no client
selection.

## Qualification Gates

| Gate | Evidence required | HOLD if |
|---|---|---|
| G0 TD acceptance | Adversarial TD review verdict | Any FAIL in the ADR compliance matrix |
| G1 Implementation (PR1) | Diff limited to the PR1 scope list | Any file outside scope; any model, migration or index; any production caller |
| G2 Focused tests | H-* subset for the PR green locally on SQLite | Any `no_history` with an unavailable row; any partial salvage |
| G3 Architecture guards | AST guards plus SQL-capture H-R1, with non-vacuity mutation evidence | Any `WorkoutLog`/`workout_log`/`fetch_workout_entries`/grandfathered-module dependency, directly or transitively |
| G4 PostgreSQL | `test_exercise_performance_history_pg.py` in the CI list and green | PG and SQLite divergence beyond the documented tie case; owner-isolation failure |
| G5 Exact-head CI | All required checks green on the exact PR head SHA | A stale head; a rebase after the run |
| G6 Contract (provisional; future read-surface TD) | Golden payloads, closed keys, state invariant, inventories, flag registry | Unknown token accepted; a client-side selection need discovered |
| G7 Staging flag enablement (provisional; future read surface) | **Not a PR1 merge gate**, and not automatically a merge gate for a future dark read surface. The staging enablement run itself is G7: history=1 and sessions=1 on staging; two accounts; a real session completed through the native transport; own history read; cross-account empty; corrupt-row injection gives `undetermined`; flag 0 gives 404; teardown | Any cross-account fact; any success body on a read failure |
| G8 Final human review | Owner merge decision | Production enablement attempted before G7 and G9; source semantics differ from ADR D1a |
| G9 Representative PG performance (before any production exposure) | §Performance and Storage fixture; `EXPLAIN (ANALYZE, BUFFERS)` of the exact query, with the recorded environment, the cold run and the 1k → 4k scaling | Any qualification escalation trigger (not an SLO) → HOLD and open a separate index/migration decision. G9 never authorizes an index |

Global HOLD conditions (any stage):

- a dependency on `WorkoutLog` or a grandfathered output;
- an unexpected schema requirement, or a fired G9 qualification escalation
  trigger;
- any production caller, API or flag added under PR1;
- corrupt rows silently skipped;
- client-side selection policy;
- stale exact-head CI;
- PostgreSQL divergence;
- an owner-isolation failure;
- semantics that differ from ADR D1a;
- Coach or Today consumption;
- production enabling before qualification.

## References

- ADR 0002: `docs/adr/0002-performed-training-authority.md`. D1/D1a `:121-183`,
  Authority Rules `:272-297`, baseline `:413-464`, L10/L11 `:477-508`,
  follow-up constraints `:635-651`.
- TI-00: `docs/superpowers/specs/2026-10-05-ti-00-training-intelligence-contract.md`
  §§3, 8, 9, 10, 16 (with the TI-03 amendment), 17 and 18.
- TI-03 record: `docs/TI_03_TRAINING_INSIGHT.md`. TI-01A:
  `docs/TI_01A_EXECUTION_CONTEXT.md`. LP17-B1 record:
  `docs/LP17_B1_NATIVE_TODAY_GUIDANCE_HTTP.md`. Latency objectives:
  `docs/OBSERVABILITY.md:230`.
- Source: `app/models.py:1291-1410`;
  `app/services/workout_session/{checkpoint,context,prescription,execution,queries,prior_performance}.py`;
  `app/services/workout_completion/queries.py:185-196`;
  `app/services/training_intelligence/{queries,facts,models,diagnostics,__init__}.py`;
  `app/services/exercise_catalog.py`; `app/timeutil.py:40-110`;
  `app/feature_flags.py:102-190`; `app/blueprints/mobile_api.py:29-39, 246-249`;
  `app/blueprints/mobile_today_guidance.py`;
  `app/blueprints/mobile_workout_sessions.py:69-197`;
  `app/blueprints/mobile_exercise_notes.py`;
  `app/blueprints/training.py:660`.
- Tests and CI: `tests/test_ti03_architecture.py`, `tests/test_ti03_pg.py`,
  `tests/ti03_support.py`, `tests/test_prior_performance.py`,
  `tests/test_mobile_auth_feature_gate.py`,
  `tests/test_sprint12_daily_coach_discovery.py`,
  `.github/workflows/ci.yml:321-388`.
