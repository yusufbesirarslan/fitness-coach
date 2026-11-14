# Backup, restore and re-deletion of deleted accounts

Task `AXISAI-V1-DELETION-AUDIT-LEGAL-HOLD-SOURCE-CLOSURE-20261114`.

**Status: `RESTORE_REDELETION=HOLD`.** This document states the procedure
that would be followed and names the piece that does not exist yet. It is not
an accepted recovery mechanism until section 2 is closed and the procedure has
been rehearsed on a restored copy. Nothing here has been executed.

## 1. Confirmed facts (from existing receipts, not re-read for this task)

- RDS `database-1-dr`: automated backups with point-in-time recovery, 7 days.
- Three encrypted manual pre-deploy snapshots, founder-owned, no automatic
  deletion. Review dates 2026-11-17 and 2026-11-19. One EBS snapshot, review
  2026-11-19.
- Application log group `/axisai/app`: 30 days by configuration; live value
  not read.
- This task modifies or deletes no backup, snapshot or log setting.

## 2. Why a restore is a deletion problem, and what is missing

A restore to a point T brings back every account deleted after T, and removes
every tombstone, destruction record and legal hold written after T. The
restored rows look like ordinary live accounts.

To re-delete them, something outside the database must say which accounts
were deleted after T. Today:

| Source | Survives a restore? | Names the account? |
|---|---|---|
| `account_deletion_record` rows | No (same database) | By fingerprint |
| `deleted_identity_tombstone` rows | No (same database) | By fingerprint |
| Log line `account_deletion_audit event=recorded` | Yes, for 30 days | No. Operation reference, time, MAC |
| Log line `account_deletion event=completed` | Yes, for 30 days | No |
| Cognito user pool | Yes | Indirectly, see section 4 |
| `account-deletion-audit-export` output kept off the database | Yes, if someone takes and stores it | By fingerprint |

The last row is the minimum independent durable source. **It does not exist
yet**: no schedule, no storage location, no access rule, and creating storage
needs separate approval. A manual snapshot can also be older than the 30-day
log. Therefore `RESTORE_REDELETION=HOLD`.

Smallest closure (needs founder approval, not done here): take the export on
a fixed schedule and after every deletion batch, store it encrypted outside
RDS and outside the snapshot set, keep it as long as the oldest retained
snapshot plus the record's own three years, and rehearse section 3 once.

## 3. Procedure after any restore (snapshot or point-in-time)

Keep the application closed to users until step 6 is done.

1. **Record the restore point T** (snapshot creation time or PITR target) and
   who authorized the restore.
2. **Re-place legal holds first.** Holds placed after T are gone. The
   controller re-creates each one from the matter register
   (`flask health-legal-hold-add`) before any deletion step. A hold released
   after T is active again in the restored data; release it again only on the
   controller's instruction.
3. **Identify deletions after T.**
   - With an export (section 2): feed every export line with
     `completed_at >= T` to `flask account-deletion-reconcile` (stdin). It
     verifies each line's MAC, prints the local accounts that exist again
     (`user_id`, `username`, `legal_hold`) and deletes nothing. Feed the
     export from a file; do not paste it into a logged session.
   - Without an export: count `account_deletion_audit event=recorded` log
     lines after T. That gives the number of lost deletions and their
     operation references, not the accounts. Use section 4 for candidates. If
     T is older than the log retention, the number is unknown: stop and
     escalate to the founder; do not reopen on a guess.
4. **Review the list.** Two people. Any account with `legal_hold: true` is
   not deleted; it goes to the controller.
5. **Re-delete** each confirmed account with the operator path:
   `flask cleanup-test-users --username <username>` first WITHOUT `--yes`:
   the match is case-insensitive, so confirm the dry run lists exactly the one
   reviewed user id. Then repeat with `--yes`. It refuses a
   held account, purges in one transaction and writes a new
   `operator_cleanup` destruction record. Media was already released by the
   original deletion; whether the bucket keeps old object versions is
   unverified.
6. **Check.** `flask account-deletion-audit-verify` must exit 0. Run
   `account-deletion-reconcile` again: it must print nothing. Take a fresh
   export. Only then reopen.
7. **File the evidence**: T, the export or log extract used, the reviewed
   list, the commands run, the verify output. The export lines for the
   original deletions remain the record of those operations; they are not
   re-inserted into the database.

## 4. Cognito comparison: candidates, not proof

A restored local account whose Cognito subject no longer exists in the user
pool is a **candidate** for "deleted after T". It is not proof:

- an incomplete deletion (identity deleted, local purge pending) looks the
  same and is a legitimate pending state the user can still finish;
- an identity can be missing for other reasons (administrative removal, a
  pool-side fault, an account created by seed or test tooling);
- an account whose first deletion attempt ended incomplete and was then put
  under a legal hold looks the same and must not be deleted;
- the reverse also fails: operator cleanup does not delete the Cognito user,
  so an operator-deleted account still has an identity and is not found this
  way.

So a missing Cognito user starts a review; it never authorizes a deletion
alone. It needs one corroboration: an export line, a matching count and time
window in the log, or the user's own deletion request on file. Listing the
user pool is an AWS read on production and needs its own authorization.

## 5. Keeping deleted accounts inactive during recovery

- A self-service-deleted account cannot sign in after a restore: its Cognito
  identity is gone. Its restored session rows are another matter. An access
  credential that had not expired at T is valid again in the restored
  database. The application therefore stays closed until step 5 has purged
  those accounts (the purge removes their sessions).
- The tombstone for a re-deleted account is written again by the purge only
  on the self-service path. Operator cleanup writes the destruction record
  but no tombstone; for an account whose identity is already gone at Cognito
  that is sufficient, because no login can present that subject.
- An operator-deleted account (identity still in Cognito) could sign in again
  after a restore. These are test accounts; step 3 with an export finds them.

## 6. Legal holds and destruction records across recovery

- Holds: restored to their state at T. Step 2 repairs them from the matter
  register before anything is deleted. The matter register is kept outside
  the application for this reason.
- Destruction records: rows after T are lost. Their off-database copy (the
  export, or the log line while it lasts) is the surviving evidence. New
  records are written for the re-deletions.
- Never restore over a database to "fix" a record: a restore loses more
  records than it recovers.

## 7. Snapshot expiry and reassessment

| Item | Owner | Date | Decision to record |
|---|---|---|---|
| Manual RDS snapshots (3) | Founder | 2026-11-17, 2026-11-19 | Keep with a new review date and reason, or delete. Either way written down |
| EBS snapshot (1) | Founder | 2026-11-19 | Same |
| PITR window | Automatic | 7 days rolling | None; deleted data ages out |

Rule proposed for founder adoption (not yet adopted): a manual snapshot has a
maximum age, set when it is taken, and is deleted at the first review after
its purpose (rollback of a specific release) has passed. Every snapshot kept
past that holds accounts that users have since deleted, and extends how far
back section 3 may have to reach. Until a maximum age is adopted,
`MANUAL_SNAPSHOT_DISPOSITION` is documented here but the retention limit
itself remains an open founder decision. No snapshot is deleted or modified
under this task.

## 8. Open items

1. Independent export: schedule, encrypted location, access, retention
   (section 2). Blocks `RESTORE_REDELETION`.
2. One rehearsal of section 3 on a restored copy.
3. Snapshot maximum age and the 11-17 / 11-19 review outcomes.
4. Whether the media bucket keeps non-current object versions.
5. Live log-group retention readback.
