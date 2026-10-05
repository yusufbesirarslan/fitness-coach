# TI-01A: canonical execution context V2

Implements the frozen [TI-00 contract](superpowers/specs/2026-10-05-ti-00-training-intelligence-contract.md). Browser and native checkpoint writes still use `workout_session.execution.record_checkpoint` and `queries.advance_checkpoint`. There is no context endpoint or independent context revision.

## Negotiation and wire contract

Native workout-session lifecycle requests use `AxisAI-Workout-Contract`: absent or exact `1` selects V1; exact `2` selects V2. Other values fail with the existing 400 `TRAINING_SESSION_INVALID_REQUEST` envelope and terminal Session-Resolution. V2 is 404 while `FITX_TRAINING_EXECUTION_CONTEXT_ENABLED` is off. The flag defaults off and depends on workout sessions. Capabilities are available at authenticated `GET /api/v1/training/workout-execution-capabilities`; insights remain false.

V2 checkpoint bodies have exactly these two keys:

```json
{
  "checkpoint": "the unchanged V1 checkpoint object",
  "execution_context": {
    "schema_version": 1,
    "sets": [{
      "exercise_id": "catalog identity",
      "index": 1,
      "actual_rir": "2",
      "tempo_adherence": "lost_control",
      "actual_rest": {
        "seconds": 83,
        "method": "completion_gap",
        "quality": "foreground_contiguous"
      }
    }]
  }
}
```

The illustrative checkpoint string above must be replaced by a valid V1 object. Context is a full snapshot. Its envelope and all five entry keys are required; omitted fields are invalid, null clears that field, omitted entries clear those sets, and an empty list clears all context. All-null entries normalize away. V1 requests never mean clear-all.

| Field | Accepted domain |
| --- | --- |
| actual_rir | null or strings `0`, `1`, `2`, `3`, `4_plus` |
| tempo_adherence | null, `as_prescribed`, `faster`, `slower`, `lost_control` |
| actual_rest | null or exact object above: integer seconds 0–3600, fixed method and quality |

Numeric RIR, coercions, booleans, unknown keys, duplicate identities and nonexistent/uncompleted sets are rejected. Context has at most 640 entries with indexes 0–19. Tempo adherence other than lost_control requires a canonical target; faster/slower require a phases target. Current legacy plans capture no tempo target. `actual_rest` is frozen wire terminology for client-observed between-set logging interval evidence, never authoritative physiological rest. It requires the immediately preceding completed set in the same exercise. No client-authored server timestamps are accepted.

The unchanged base bound is 65,536 canonical UTF-8 bytes. Context is bounded to 131,072 canonical bytes; V2 additionally bounds both decoded wire body and canonical combined body to 262,144 bytes before persistence.

V2 successful envelopes add execution_context and prescription beside the existing session/completion fields. One row snapshot supplies the combined projection. V1 JSON, parser, digest, revision grammar and completion/replay behavior remain unchanged. Native responses vary on negotiation; V2 uses private, no-store while V1 retains no-store.

## Persistence, replay and compatibility

Alembic `e1f2a3b4c5d6`, following `d0e1f2a3b4c5`, adds nullable TEXT `execution_context_data` and `prescription_data` to WorkoutSession. Existing sessions are not backfilled. New sessions capture immutable bounded prescription lineage/version and representable legacy reps/rest; load, RIR and tempo targets remain null. This is a read-only prescription snapshot, not execution input or plan mutation.

The context document binds schema 1 and checkpoint revision to completed/reps/normalized-load anchors. Logging intervals also bind the predecessor anchor. The existing owner/ACTIVE/expected-revision/<MAX_REVISION conditional UPDATE installs base JSON, context JSON, fingerprint, idempotency key, next revision and timestamps together. A stale or saturated write changes nothing. Completion terminalizes the existing combined state, including at MAX_REVISION.

V1 hashing is unchanged. V2 SHA-256 hashes `axisai:training-workout-checkpoint:v2\0` followed by canonical JSON containing the normalized checkpoint and context, before persistence binding. Same key and full semantics replay; changed context conflicts. Versions have separate domains. Ordering and all-null normalization are deterministic.

| Later V1 change | Context outcome |
| --- | --- |
| elapsed time or exercise selection only | Preserve unchanged completed sets, bind to next revision |
| reps or normalized load changes | Invalidate that set's entire context |
| reopen/remove set or exercise | Remove dependent context; no orphan entries |
| predecessor materially changes/reopens/disappears | Clear following logging interval, preserve following RIR/tempo if its own anchor matches |
| write after explicit V2 clear | Cleared values stay absent |

These rules apply with P0 disabled too. An old binary advancing base state without updating the binding makes old context unprojectable, preventing stale resurrection.

## Qualification and rollback

Focused contract, native/browser authority, completion, flag/readiness and migration suites accompany real PostgreSQL barrier races for V1/V2 and V2/V2 at revision zero and MAX-1. Exactly one writer wins and the loser cannot install partial context. Frozen V1 characterization tests and the existing browser checkpoint JavaScript tests remain green.

Temporary mutations independently removed context from the fingerprint, discarded V1 preservation, bypassed base-anchor invalidation, persisted context before a failing CAS, and allowed advancement at MAX_REVISION. Each targeted regression failed; all mutations were restored byte-for-byte. A separate pre-fix reproduction caught subset-checkpoint selection incorrectly suppressing valid context projection.

Disable P0 to withdraw V2 negotiation and capabilities while retaining canonical data and V1 preservation. Deploy the additive migration before enabling P0. Retain both columns on an application rollback; do not downgrade a live database containing context. The migration's deliberate column-dropping downgrade is tested only on disposable databases. No P1 switch deletes P0 data.

Only the fixed `context_accepted` outcome is added to existing bounded session metrics. No context payloads or user identities are logged.

Non-scope: persistent notes, diagnostics, mobile UX, e1RM, recovery inference, video and autonomous plan mutation.

## Local validation evidence

Run from the repository root with the repository virtualenv Python:

```sh
python -m pytest -q tests/test_ti01a_execution_context.py tests/test_ti01a_migration.py tests/test_ti00_v1_checkpoint_characterization.py tests/test_checkpoint_revision_exhaustion.py tests/test_workout_session.py tests/test_workout_state.py tests/test_workout_state_sessions.py tests/test_workout_completion.py tests/test_workout_convergence.py tests/test_mobile_workout_sessions_api.py tests/test_mobile_workout_sessions_architecture.py tests/test_sprint14_workout_execution_contract.py tests/test_sprint14_activation_readiness.py tests/test_feature_flags.py tests/test_feature_flag_registry.py tests/test_migration_graph.py tests/test_pump_check_history_migration.py
# 694 passed
FITX_PG_CONCURRENCY_TEST=1 PG_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:55439/ti01a_test python -m pytest -m pg_concurrency -q tests/test_mobile_workout_sessions_pg.py tests/test_workout_session_pg.py tests/test_workout_completion_pg.py tests/test_sprint14_workout_execution_reliability_pg.py tests/test_ti01a_execution_context_pg.py
# 32 passed, real disposable PostgreSQL
python -m pytest -q tests/test_training_execution_boundary.py
# 17 passed, 2 existing skips; real Playwright browser
node --test tests/js/workout_checkpoint_client.test.js
# 23 passed
```

The disposable PostgreSQL schema experiment ran the full Alembic chain to the predecessor, upgraded to head, checked model drift, downgraded to the predecessor, re-upgraded and checked drift again: PASS. It initialized through migrations, not create_all. No live database was touched.

| Temporary mutation | Target test | Observed result |
| --- | --- | --- |
| Remove execution context from V2 digest | test_fingerprint_versions_context_and_null_normalization | 1 failed |
| Discard preserved V1 context | test_cross_transport_preserve_invalidate_clear_and_replay | 1 failed |
| Bypass changed base anchor invalidation | test_v1_material_changes_invalidate[reps] | 1 failed |
| Commit context before CAS | test_failed_cas_cannot_partially_persist_context | 1 failed |
| Replace revision < MAX with <= MAX | test_v2_saturation_atomicity[999999999] | 1 failed |

All targets are in tests/test_ti01a_execution_context.py. Each mutation was temporary and restored before final qualification. Additionally test_v1_selection_only_preserves_context_with_subset_checkpoint failed before its projection fix and passed afterward. Mutation-only code is absent from this branch.
