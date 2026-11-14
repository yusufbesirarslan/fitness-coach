# Account deletion: destruction record and legal-hold guard

Task `AXISAI-V1-DELETION-AUDIT-LEGAL-HOLD-SOURCE-CLOSURE-20261114`. Source only.
Nothing here is deployed, migrated or activated by this change, and source
tests alone do not make account deletion KVKK-complete (section 7).

Founder decisions this implements:

- the fingerprint-plus-time tombstone is **not accepted** as the record of a
  deletion operation;
- a durable deletion record is approved for source preparation;
- deletion and operator cleanup must not bypass an active legal hold.

Related: `docs/MOBILE_ACCOUNT_DELETION.md` (the lifecycle),
`docs/BACKUP_RESTORE_REDELETION_RUNBOOK.md` (what a restore undoes).

## 1. The record

Table `account_deletion_record` (migration `ad17de1e0001`, one empty additive
table, no backfill). Owner module: `app/services/deletion_audit.py`. One row
per purge that commits.

| Field | Content |
|---|---|
| `operation_ref` | Random 128-bit hex. The opaque name of this operation. Unique |
| `request_id` | The server request id of the HTTP request (self-service only). Joins the row to the application log while that log exists |
| `channel` | `self_service` (`DELETE /api/v1/account`) or `operator_cleanup` (`cleanup-test-users`) |
| `outcome` | `completed`. The only value: a row exists only because a purge committed |
| `identity_step` | What happened at Cognito: `deleted`, `already_absent` (a retry), `not_attempted` (operator cleanup does not call Cognito) |
| `subject_fingerprint` | The tombstone's keyed HMAC of the Cognito subject. Not reversible without the server key. NULL for an account that never had a subject |
| `scope` | `full_account` |
| `counts` | `rows`: rows deleted per table by the purge itself (database cascades are not counted). `objects_released`, `unmanaged_references`, `health_erasures_closed`: single counts, NULL where that path does not do the step |
| `completed_at` | UTC, whole seconds |
| `retain_until` | `completed_at` + 3 years |
| `integrity` | HMAC-SHA256 over every other field |

Never stored: health data, consent text or state, names, e-mail, username, the
internal user id, the Cognito subject, tokens, storage keys, legal-hold matter
text. `record()` accepts only non-negative integers under table-name keys in
`counts`, and a closed vocabulary elsewhere, so content cannot be passed in by
a future caller. There is no foreign key to `user`.

## 2. Atomicity, idempotency, retry

- `record()` only adds to the caller's transaction. The row commits with the
  purge or not at all. A purge that fails leaves no record; a record that
  cannot be written fails the purge (`ACCOUNT_DELETION_INCOMPLETE`, account
  intact).
- A retry after an incomplete deletion writes exactly one row, with
  `identity_step = already_absent`.
- A repeated or concurrent request for an account that is already gone writes
  nothing more: the tombstone is re-asserted (idempotent) and no second
  outcome is recorded.
- Existing responses are unchanged: 204, 503 `ACCOUNT_DELETION_UNAVAILABLE`,
  503 `ACCOUNT_DELETION_INCOMPLETE`, 401.

## 3. Protection of the record

- **Not removed by deletion.** No foreign key, so no cascade. `_purge_user`
  does not touch the table. Only `deletion_audit` references the model (AST
  gate in `tests/test_account_deletion_audit.py`).
- **Integrity.** Each row carries a MAC keyed by a subkey of `SECRET_KEY`
  (`axisai/account-deletion-record/v1`). `flask account-deletion-audit-verify`
  (read only, exit 2 on failure) recomputes every MAC and counts tombstones
  written since the first record that have no record. A changed row is
  detected. A removed `self_service` row is detected only through its
  tombstone. Not detectable from inside the database: a row removed together
  with its tombstone, a removed `operator_cleanup` row (that path writes no
  tombstone), and a restore. Known false positive: if the oldest row has an
  extended `retain_until`, younger rows that expire normally leave tombstones
  that `verify` then counts as uncovered.
- **Key rotation.** Rotating `SECRET_KEY` makes old MACs and old fingerprints
  unverifiable. Keep the previous key available for verification.
- **Access.** No HTTP route reads or writes the table. The read-only operator
  commands are `account-deletion-audit-verify`, `account-deletion-audit-export`
  and `account-deletion-reconcile`. Database-level grants are **not** changed
  by this PR: the application role can still update or delete rows. Narrowing
  that (for example INSERT/SELECT for the web role, DELETE only for the
  maintenance job) is a production change that needs its own approval.

## 4. Retention and disposal

- Minimum three years: `retain_until = completed_at + 3 years`.
- A longer legal obligation is met by moving `retain_until` later for that
  row. Nothing shortens it.
- Disposal: the existing daily maintenance job calls
  `deletion_audit.purge_expired()`, which deletes rows whose `retain_until`
  has passed and logs a count.
- Application logs are **not** extended to three years. The log group keeps
  its configured 30 days.
- The migration's downgrade refuses to drop a populated table.

Production retention is **UNVERIFIED** until the migration is applied, a real
deletion writes a row, and the maintenance job is observed in production.

## 5. The copy outside the database

A row in the database it describes is lost by any restore to an earlier
point. Two things exist outside it:

1. After the commit, one log line:
   `account_deletion_audit event=recorded operation_ref=… channel=…
   identity_step=… completed_at=… retain_until=… integrity=… request_id=…`.
   It deliberately carries no subject fingerprint (the application never
   logs it, an LP-11 invariant with its own test). It proves that
   an operation completed and seals the row, for as long as the log group
   keeps it (30 days configured, live value unverified).
2. `flask account-deletion-audit-export [--since YYYY-MM-DD]` prints the
   sealed rows as JSON lines, fingerprints included, so its output must not
   be left in a session or command log. This is the minimum independent preservation
   mechanism: an export kept somewhere other than the database and its
   snapshots survives a restore, and `account-deletion-reconcile` reads it
   back. **Where that export is stored, how often it is taken and who may
   read it are not decided and nothing is configured.** No storage resource
   is created by this change.

Until (2) is operated, post-restore re-deletion is HOLD (runbook section 2).

## 6. Legal hold

A `HealthLegalHold` with `released_at IS NULL` is an active hold. One active
hold on any category refuses deletion of the whole account, because account
deletion destroys everything.

**Self-service (`account_deletion.delete_account`)**

1. Preflight as before (no side effect).
2. Take the owner's exclusive health authority (`_hold_fence`). This is the
   same PostgreSQL advisory lock `health-legal-hold-add`, consent withdrawal
   and erasure take, so no hold can be placed until the deletion ends. If the
   lock cannot be taken within its 2-second bound, the answer is
   `ACCOUNT_DELETION_UNAVAILABLE` and nothing is deleted.
3. Under it, check for an active hold. If one exists: refuse before the
   Cognito call. Nothing is deleted, the account stays usable.
4. Cognito, storage release and the purge run inside the same lock.
5. The purge transaction checks again after locking the owner row
   (`DeletionIncomplete("legal_hold")`), and `_purge_user` itself refuses a
   held owner before its first delete (`LegalHoldActive`). Both are backstops
   for a writer that skips the authority, and for the one case the lock does
   not cover: if the authority's database connection is terminated mid-deletion
   (failover, an idle-in-transaction timeout), the lock is released silently.
   A hold placed in that gap still stops the row purge (`INCOMPLETE`), but
   storage objects released before it are gone. Not hardened in this PR.

**Operator cleanup (`cleanup-test-users`)**

- The listing marks held accounts.
- With `--yes` it locks every matched owner row (`FOR UPDATE`, id order) and
  then checks. A hold INSERT needs a key-share lock on the same row, so it has
  either committed (and is seen) or waits and then fails its foreign key.
- If any matched account is held, the whole run is refused: exit 2, nothing
  deleted, hold ids are not printed.
- Every account it deletes gets an `operator_cleanup` record.

**Never reported as success.** A held account is answered 503, never 204.
There is no partial deletion: either the account is purged in one transaction
or nothing local changes.

### What the client sees, and the limit of that

No new response code or field. A held account gets the existing

    503 ACCOUNT_DELETION_UNAVAILABLE   (retryable)

so build 101116 and the mobile contract are unchanged, and nothing about the
hold is disclosed. Limits of keeping the generic 503:

- The user is told to try again later and will keep getting the same answer
  for as long as the hold lasts. The app cannot explain why.
- The deletion request is not queued. Nothing deletes the account
  automatically when the hold is released.
- The only trace is one log line with the internal user id:
  `account_deletion event=refused stage=legal_hold user_id=…`. It lives 30
  days.

**Controller responsibility.** Whoever places a hold owns these steps:

1. Search the application log for `event=refused stage=legal_hold` at least
   every 30 days while any hold is active, and keep the finding in the matter
   file. There is no durable in-database marker for "a held user asked for
   deletion".
2. Answer the user's deletion request directly, in writing, within the
   statutory period. The app will not.
3. When the hold is released, complete the pending deletion (the user retries
   in the app, or the operator path is used on their written request).

Two more 503 causes introduced here, both `ACCOUNT_DELETION_UNAVAILABLE`:

- another request of the same user holds the shared health lock for more than
  two seconds (a long AI call, for example). A retry succeeds;
- a duplicate deletion request arrives while the first is still working for
  more than two seconds. The first completes; the duplicate's retry is 401.

One state where that 503 understates what happened: a first attempt ended
`ACCOUNT_DELETION_INCOMPLETE` (identity deleted, some media released), a hold
was then placed, and the retry is refused. The 503 says "nothing deleted",
which is true of that request only. The user cannot sign in again. No held
row is purged and nothing is reported as success. The controller owns this
account until the hold is released and an operator completes the deletion.

Rollback note: reverting to the code before this change restores a
`_purge_user` that deletes active holds. Place no hold while that code runs.
Operator cleanup's post-lock hold check relies on the session's READ COMMITTED
default (no engine-level isolation override exists today).

## 7. What this does not prove

- Production retention of the record (section 4).
- Database grants on the table (section 3).
- Survival across a restore (section 5, runbook).
- Deletions before this revision have no record and none is invented.
- Legal sufficiency of the record's content is a counsel question.

## 8. Tests

| File | Covers |
|---|---|
| `tests/test_account_deletion_audit.py` | Record content and minimality, atomicity, retry, hold refusal, operator cleanup, disposal, verify/export/reconcile, architecture gates |
| `tests/test_account_deletion_audit_pg.py` | Real locks: hold versus in-flight deletion in both orders, busy authority, operator cleanup versus hold in both orders, one record under concurrent duplicates, constraints (CI PostgreSQL list) |
| `tests/test_account_deletion_audit_migration.py` | Additive, idempotent, verify-or-create, downgrade refuses records |
| `tests/test_health_erasure.py` | `_purge_user` refuses an active hold, removes a released one |
| `tests/test_r6_forward_chain_pg.py` | The new revision in the release chain |
