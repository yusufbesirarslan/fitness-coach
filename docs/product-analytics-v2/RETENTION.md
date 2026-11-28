# Product analytics v2: retention and deletion

**Founder decision (2026-11-28):** raw events at the vendor live **at most 90
days, INCLUDING deletion lag**. Rotating the pseudonym at 90 days and then
deleting asynchronously does **not** meet that bound by itself, because the
deletion lag comes on top of the rotation interval.

**Status:** the AxisAI-side bound is enforced in source as a precondition for
capture (fail closed). The configuration values below are a **proposal**:
nothing is a production setting, and no value has a production default. An
**effective 90-day maximum is NOT claimed** until G5 has verified vendor
deletion behaviour and G2 has answered the backup question (§3).

## 1. What bounds the age of a raw event at the vendor

An event can be at most this old when the vendor has deleted it:

```
R (rotation interval) + B (client backdate, 1 d) + D (retirement → vendor deletion done)
```

* **R:** a live subject older than `PRODUCT_ANALYTICS_SUBJECT_MAX_AGE_DAYS` is
  retired to vendor erasure and replaced for the same grant.
  * Withdrawal and account deletion retire earlier, so they are inside the
    same bound.
  * Rotation runs in every pass (about 60 s, batch 100). The lag is negligible
    unless a backlog builds.
* **B:** client events may carry `occurred_at` up to 24 h in the past
  (catalog envelope).
  * Outbox delay (≤ 48 h) does not add to this. An undelivered row of a
    retired subject is deleted, never re-bound.
* **D** is the sum of:
  * AxisAI pipeline: 15 min settle, then the marker is ingested, then
    bulk_delete is accepted. Failures back off, capped at 24 h.
  * Vendor processing: PostHog deletes "during non-peak usage times (weekends
    on PostHog Cloud)" with a cutoff two days before each batch. That is
    typically 2–9 days, but **no SLA is documented**.
  * Completion is read from `deletion_status.delete_verified_at`.

## 2. Enforced in source (fail closed)

`settings.retention_bounded(policy)` must hold, or the pass does not capture
(`capture=halted_retention_unbounded`). Erasure keeps running either way.

```
R + B (1 d) + O (overdue alarm) + M (escalation margin, 7 d) <= 90 d
```

* Both `PRODUCT_ANALYTICS_SUBJECT_MAX_AGE_DAYS` (R) and
  `PRODUCT_ANALYTICS_ERASURE_OVERDUE_DAYS` (O) must be configured. Missing
  either one means no capture.
* While **any** erasure job is unfinished after O days, capture halts
  (`capture=halted_erasure_overdue`, error log `event=erasure_overdue`).
  Erasure continues.
* A halt does **not** delete anything. It stops adding data while a human
  uses the M-day margin to escalate: a vendor support request or a manual
  person deletion in the vendor console.

Tests: `test_capture_halts_unless_retention_fits_the_ceiling` (8 configs incl.
the 90/91-day boundary), `test_an_overdue_erasure_halts_capture_but_erasure_keeps_running`.

## 3. Proposed configuration (conservative; approval pending)

| Key / setting | Proposal | Reason |
|---|---|---|
| `PRODUCT_ANALYTICS_SUBJECT_MAX_AGE_DAYS` (R) | **60** | 60 + 1 + 14 + 7 = 82 ≤ 90. D30 retention needs R ≥ 32 days (cohort day + 30 + ingestion), so 60 keeps it. |
| `PRODUCT_ANALYTICS_ERASURE_OVERDUE_DAYS` (O) | **14** | about two vendor weekend batches plus AxisAI retries. |
| Tighter alternative | R = 45, O = 21 | more vendor slack; shorter analysis continuity (still ≥ D30). |
| `PRODUCT_ANALYTICS_OUTBOX_MAX_AGE_HOURS` | 48 (ceiling) | undelivered rows never wait longer. |
| `PRODUCT_ANALYTICS_ERASURE_RECEIPT_RETENTION_DAYS` | 90 | minimal deletion evidence. Unfinished jobs are never purged. |
| PostHog project "Events retention" (G3) | the plan's minimum | **second layer only**. Free = 1 year, fixed. PostHog deletes past the window "at the next monthly cleanup", so a setting S bounds data only at S + ~1 month. It would need S ≤ 59 days to be a standalone bound, and that is not documented as selectable. Verify at G3. |
| Anonymized aggregates | ≤ 12 months | counts only, small cells shown as "<5", no pseudonyms (P9 export, not built). |

## 4. Backups and restoration

* **Vendor backups:** it is unknown whether deleted events persist in
  PostHog's backups, and for how long.
  * This is a G2 question for the DPA.
  * Counsel decides whether backup persistence beyond 90 days is acceptable
    (COUNSEL_DECISION_PACKET.md Q-8).
  * Until it is answered, the bound covers the live vendor dataset only.
* **AxisAI database backups** hold consent rows (the pseudonym mapping),
  outbox rows (≤ 48 h) and erasure receipts. Production backup retention was
  not read, and none is assumed. A restore to an earlier point has three
  effects:
  1. **A withdrawn grant revives.** The restored row shows the old grant and
     subject again, and a device would resume.
  2. **Subjects minted after the backup point and already sent are lost from
     AxisAI's records.** AxisAI erasure can no longer target their vendor
     data.
  3. **Outbox rows already delivered may be sent again.** Whether `uuid`
     de-duplicates replays is unverified (G5).
* **Proposed restore disposition** (runbook item, not built; owner decision
  at G3):
  * Keep `FITX_PRODUCT_ANALYTICS_ENABLED=0` until reconciled.
  * Delete all outbox rows.
  * Retire every restored subject and withdraw every restored grant, so each
    exposed subject gets an erasure job and users opt in again (fail closed).
  * Record the restore window. Item 2 is a **residual** that only vendor-side
    retention or a manual vendor purge can close. Counsel must accept it or
    require a backstop (Q-8).

## 5. Monitoring

* Each pass logs counts and fixed codes: `capture=…`, `erasure_overdue=…`,
  and error lines `event=erasure_overdue` and `event=dispatch_aborted`.
* The CloudWatch metric filter and alarm on these lines are a deployment
  item (G3). They are **not built**: no AWS was touched.

## 6. Other data

| Data | Maximum | Mechanism |
|---|---|---|
| Undispatched outbox row | 48 h | `retention.cleanup`; 48 h ceiling in code |
| Erasure receipts (`verified`/`not_required`) | configured days after completion | `retention.cleanup` purges terminal receipts only |
| Consent / processing-authority evidence | account lifetime | one row per account; no history table |
| Grant validity | 365 days, then re-ask (proposal) | `consent.GRANT_TTL` (consent policy, not retention) |

## 7. Deletion lifecycle (AxisAI side)

| Trigger | Immediate (same transaction) | Then |
|---|---|---|
| Withdrawal | outbox rows deleted; generation bumped (fences in-flight sends); erasure job (`pending` if exposed, else `not_required`) | worker: profile marker → bulk delete → verification |
| Account deletion | erasure job created before the mapping is deleted (consent row locked first) | same |
| Rotation | erasure job for the old subject; new subject minted | same |
| Outbox expiry | row deleted | none (never sent) |

The device never stores an event. A pending withdrawal is persisted on the
device only as an account key (mobile `AnalyticsWithdrawalStore`).
