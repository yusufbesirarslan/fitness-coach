# Legacy `activity_clap`: deletion compatibility

## Defect

The R6 production schema readback (2026-11-09, migration head `c6f8b0d2e4a1`) found a table `activity_clap` that no model and no migration owns:

```
activity_clap(id serial PK, activity_id int NOT NULL, user_id int NOT NULL, created_at timestamp,
              UNIQUE (activity_id, user_id),
              activity_clap_activity_id_fkey -> activity(id)  ON DELETE NO ACTION,
              activity_clap_user_id_fkey     -> "user"(id)    ON DELETE NO ACTION)
```

It is the only `NO ACTION` foreign key to `user`; every other user-owned table cascades. As a result, two deletions fail with 23503:

- **Deleting a user who clapped.** `_purge_user` deletes the `user` row.
- **Deleting an activity someone clapped.** `_purge_user` deletes the owner's `Activity` rows, and O2-B workout erasure (`health_erasure`, category `workouts`) deletes them too.

Account deletion removes the Cognito identity *before* the local purge, so these accounts end in `ACCOUNT_DELETION_INCOMPLETE`. O2-B erasure stays pending.

## Options compared

| | A: guarded Alembic migration (recreate both FKs `ON DELETE CASCADE` if the table exists) | B: application-level cleanup (chosen) |
|---|---|---|
| Schema change | Yes: a fifth revision after the qualified four-revision release chain | None |
| Release chain | #502's LIVE_HEAD→HEAD qualification and production-shape fixture must be redone | Unchanged |
| Deploy-time locks | `DROP/ADD CONSTRAINT` takes ACCESS EXCLUSIVE on `activity_clap` and SHARE ROW EXCLUSIVE on `user` and `activity` during release-prepare | None |
| Deploy-time data effect | None, but the cascade then fires implicitly on every future delete | None |
| Deletion scope | Implicit DB cascade, one level | Explicit, one level, the same rows a cascade would remove |
| Covers raw SQL / manual deletes | Yes | No (only the two application paths) |
| Fresh / staging / test DBs | Conditional DDL differs per environment | No-op where the table is absent |
| Rollback | Downgrade must restore NO ACTION | Plain revert |

B is the smallest safe fix. It changes no schema, keeps the qualified migration chain, deletes nothing at deploy time, and takes no release-time lock on the hottest table. It also adds no implicit cascade that every future delete would trigger. The one property only A has is covering deletes made outside the application. Every application path that deletes users or activities goes through these two functions, including the operator command `cleanup-test-users`, which calls `_purge_user`. A hand-written SQL delete would still hit 23503, which fails safe.

## Behaviour

`app/services/legacy_activity_clap.py`:

- **`delete_for_user(uid)`**, called by `_purge_user` before it deletes the user's activities and the user, inside the same transaction. It deletes `activity_clap` rows where `user_id = uid` **or** `activity_id` belongs to one of `uid`'s activities.
- **`delete_for_activities(ids)`**, called by O2-B workout erasure next to the existing feed-reference cleanup. It deletes only the claps **on** the erased activities. The erased owner's own claps on other people's activities are social records, so they are kept, as they would be in any other social table O2-B does not cover.
- **When the table is absent** (`inspect().has_table`), both functions are no-ops.

Each removes only rows that reference rows the caller is deleting in the same transaction. Unrelated claps are untouched, and the cleanup goes no deeper than that one level.

## Tests (PostgreSQL, production DDL)

- **`tests/test_account_deletion_pg.py`:**
  - Real `delete_account` succeeds for both the clapper and the activity owner, with exact clap-row assertions; unrelated rows are kept.
  - Non-vacuity: with the cleanup disabled, the same purge fails with 23503.
- **`tests/test_health_erasure_pg.py`:**
  - O2-B `workouts` erasure completes; the clap on the erased activity is removed and the owner's claps elsewhere are kept.
  - Non-vacuity: with the cleanup disabled, the erasure stays pending (`ErasureUnavailable` caused by 23503).

The shared DDL lives in `tests/account_deletion_support.py`. The fixtures drop the table before `db.drop_all()`, which does not know about it.

## Not done

- **Deleting the legacy table or its row.** That needs a separate founder decision; archive first if it is ever approved.
- **Changing the production schema.**
