# TI-01B — persistent exercise notes

Authority: [TI-00 §9](superpowers/specs/2026-10-05-ti-00-training-intelligence-contract.md#9-persistent-exercise-notes--product-decision).
One private authority per `User.id` × immutable catalog `ExerciseDefinition.exercise_id`.
The catalog lives in `app/services/training_assets/exercises.json`, not SQL;
TI-00 explicitly requires server-side catalog validation rather than a fabricated
catalog FK. Existing plan cues, weekly notes and feed notes have different scopes.

## Storage and lifecycle

`exercise_note`: internal integer PK, required user FK (`fk_exercise_note_user`,
`ON DELETE CASCADE`), required `exercise_id VARCHAR(120)`, nullable `text
VARCHAR(500)`, required integer revision, required UTC `created_at`/`updated_at`.
`uq_exercise_note_owner_exercise` enforces the pair and its index serves owner
and pair lookups. Named checks bound revision to 0–999999999 and text to 500
code points. The service also enforces at most 2000 UTF-8 bytes.

Migration `e2f3a4b5c6d7` follows merged TI-01A `e1f2a3b4c5d6`. Upgrade creates only this table;
downgrade deliberately erases notes and drops the table. Fresh boot may create
current metadata before the remaining migration chain: the migration verifies
existing columns/types/nullability/PK/unique/FK/checks before accepting that table,
and rejects missing protections. It also works from an empty migrated database.
Normal rollback is flag
disable, retaining data. No boot-time ALTER or create_all dependency.
Account purge explicitly includes notes; raw user deletion also cascades.
The asset has active/inactive entries and no SQL deletion lifecycle: inactive,
removed or unknown IDs reject API access while existing rows remain private and
are cleaned up with the owner. Names, aliases and generated names cannot create
shadow identities. Plans and sessions never own or delete notes.

## Native API

Bearer owner authentication; GET/PUT
`/api/v1/training/exercises/<exercise_id>/note`. No browser-specific store or
bootstrap projection. Browser consumers can later reuse the same service.
No DELETE: clear through PUT retains a revisioned tombstone to prevent ABA.

GET absent: `{"note":{"exercise_id":"ex_...","text":null,"revision":0,"updated_at":null}}`.
Present responses use the same envelope with UTC ISO timestamp and omit internal
PK/user IDs. Responses inherit mobile no-store behavior.

PUT requires `If-Match` containing the note revision (decimal or quoted decimal)
and exactly `{"text": <string-or-null>}`. Missing text, extra keys, wrong types,
invalid Unicode, unsupported controls, and oversized input fail with 400
`TRAINING_NOTE_INVALID`. Bounds apply before normalization. CRLF becomes LF;
outer whitespace is trimmed; null, empty and whitespace-only clear. Newline and
tab are allowed, other control characters rejected. Plain text, including HTML,
is stored without rendering assumptions; clients must escape at presentation.

Accepted writes increment the note revision. Identical normalized text at the
current or immediately previous revision returns current state without changing
timestamps/revision. Different stale text yields 409
`TRAINING_NOTE_REVISION_CONFLICT`, `Session-Resolution: reread`. Exhausted changed
writes yield 409 `TRAINING_NOTE_REVISION_EXHAUSTED`, terminal; reads/retries remain
available. Database failures produce 503 `TRAINING_NOTE_UNAVAILABLE`, retry,
`Retry-After: 15`. Errors never echo note text or database exceptions.

The frozen default-off `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` P0 flag,
`FITX_WORKOUT_SESSIONS_ENABLED`, and note/session schema readiness are required;
dark requests return 404 `TRAINING_SESSION_NOT_FOUND`. This shared rollout flag
does not import or implement TI-01A execution context.

## Concurrency, privacy and independence

Dialect-specific INSERT ON CONFLICT DO NOTHING plus SQL revision CAS operate in
one transaction. The unique pair arbitrates concurrent first writes; differing
competing writes have one winner and one reread conflict; identical retries return
the winner. Concurrent clear/update follows the same rule. Clears retain rows;
account-delete/write races cannot leave orphaned rows because of the FK.

Every query and CAS uses the authenticated owner, never a supplied user ID.
A foreign user's note is indistinguishable from absence. No prose logging,
metrics labels or exception messages. Sentry events, transactions and breadcrumbs
for note requests are excluded to prevent JSON body or stack-local collection.

No note text in checkpoints, fingerprints, execution context, diagnostics,
Coach prompts or AI/provider traffic. No TrainingPlan/WorkoutSession mutation.
No Flutter/UI changes, autonomous plan changes, embedding or inferred preferences.

## Validation evidence

Focused pytest run (20 modules): **661 passed**. Includes note CRUD/validation,
ownership, model constraints, account deletion, migrations, flag registry,
observability and existing workout/session regressions. Lifecycle tests assert
create/update/clear leave checkpoint revision/data/fingerprint and plan unchanged;
checkpoint save, completion, abandonment/new session and plan deletion preserve
the note. Both V1 and V2 checkpoints are exercised; note mutations also leave
existing execution context and immutable prescription unchanged.

```bash
python -m pytest -q tests/test_ti01b_exercise_notes.py tests/test_ti01b_migration.py tests/test_cascade_delete.py tests/test_migration_graph.py tests/test_pump_check_history_migration.py tests/test_sprint14_activation_readiness.py tests/test_mobile_workout_sessions_api.py tests/test_sprint14_workout_execution_contract.py tests/test_feature_flag_registry.py tests/test_feature_flags.py tests/test_mobile_account_deletion_api.py tests/test_account_deletion_resurrection.py tests/test_account_deletion_architecture.py tests/test_mobile_workout_sessions_architecture.py tests/test_observability.py tests/test_db_init.py tests/test_mobile_auth_feature_gate.py tests/test_sprint12_daily_coach_discovery.py tests/test_ti01a_execution_context.py tests/test_ti01a_migration.py
python -m pytest -q tests/test_training_plan_replacement_precondition.py
FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=<isolated-postgres> python -m pytest -q tests/test_account_deletion_pg.py tests/test_training_plan_replacement_pg.py
```

Additional plan replacement preconditions: **46 passed**; real PostgreSQL
account deletion and plan replacement regressions: **19 passed**.

`FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=<isolated-local-postgres> python
-m pytest -q tests/test_ti01b_exercise_notes_pg.py`: **6 passed** on PostgreSQL 16
with separate connections and barrier-synchronized writers.

Empty PostgreSQL `flask --app starter db upgrade`, `db check`, downgrade to
`e1f2a3b4c5d6`, re-upgrade and `db check`: all exit 0; both drift checks report
no new upgrade operations. Dedicated SQLite migration-only test also verifies
upgrade/downgrade without create_all.
Actual fresh PostgreSQL application boot (metadata creation followed by stamp
and migration) also exits 0 and reaches `e2f3a4b5c6d7` with the note schema intact.

Five temporary mutations were each reverted in finally blocks: remove unique
constraint → 1 test failure; remove owner predicate → 1 failure; couple note
write to session revision → 4 V1/V2 lifecycle failures; remove conflict-safe insert → 2 real
PostgreSQL first-create failures; remove cascade → 1 real PostgreSQL FK failure.
The unmodified focused and PostgreSQL suites passed afterward.

## TI-01A merge interaction

Originally branched from main `9e21be55eaa8c20859bbd765c47154781c026153`
independently of TI-01A. TI-01A #395 merged as
`27d1e90c660dd3a955b934b536a3144dc80ed7d2` during qualification. The actual
registration/flag/docs/head-pin conflicts were resolved against merged main;
notes now descend from `e1f2a3b4c5d6`, preserving one migration head. This is
migration ordering, not a note service dependency on execution context.
The shared default-off P0 lifecycle record retains TI-01A semantics and adds note
qualification prerequisites. No TI-01A execution implementation was changed.
Integrated qualification: 661 focused tests including merged TI-01A tests;
29 real PostgreSQL tests across notes (6), TI-01A (4), account deletion/plan
replacement (19); empty-chain drift/round-trip and fresh boot green. Unrelated
main movement alone does not warrant further rebase.

```bash
FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=<isolated-postgres> python -m pytest -q tests/test_ti01b_exercise_notes_pg.py tests/test_ti01a_execution_context_pg.py tests/test_account_deletion_pg.py tests/test_training_plan_replacement_pg.py
```
