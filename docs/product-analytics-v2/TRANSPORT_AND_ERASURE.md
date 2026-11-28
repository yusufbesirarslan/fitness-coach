# Product analytics v2: gateway transport, scheduling and vendor erasure

**State:** production-capable source, **default-unreachable**. No PostHog
project, token or key exists for any environment. Missing or invalid
configuration means no transport object, so no vendor call happens. Qualified
with synthetic transports, an in-process HTTPS adapter, a loopback slow endpoint
and real PostgreSQL 16. No live vendor request was ever made.

## 1. Exact destination allowlist (`posthog_transport.py`)

| Call | Method + URL (code constants; config selects region `eu` only) | Auth |
|---|---|---|
| capture | `POST https://eu.i.posthog.com/batch/` | project token in body (`api_key`) |
| person lookup (erasure) | `GET https://eu.posthog.com/api/projects/<id>/persons/?distinct_id=<pseudonym>` | `Authorization: Bearer <personal key>` |
| bulk delete (erasure) | `POST https://eu.posthog.com/api/projects/<id>/persons/bulk_delete/` body `{"distinct_ids":[…],"delete_events":true}` | personal key |
| verification (erasure) | `GET https://eu.posthog.com/api/projects/<id>/persons/deletion_status/?person_uuid=<ref>&status=all` | personal key |

Request discipline:

* TLS is verified. Redirects are refused (3xx = failure). Plain `http://` is
  refused by adapter. `trust_env=False` (no proxy, netrc or CA overrides from
  the environment).
* Timeouts: connect 3 s (0.5–5) and read 10 s (1–15), plus an overall deadline.
* No transport-level retries: the outbox owns bounded retries. Response bodies
  are capped at 64 KiB.
* Failures carry fixed codes only (`timeout`, `network`, `vendor_retry`,
  `vendor_rejected`, `redirect_refused`, `malformed_response`, …). No URL with
  query, token, pseudonym, body or response text reaches a log or exception
  (tested with caplog).
* The architecture test allows a network import only in this module.

## 2. PostHog contract: what was verified and what was not

Read on posthog.com/docs (2026-11-28): `/docs/api` (EU public host
`eu.i.posthog.com`, private host `eu.posthog.com`, Bearer personal keys,
private API rate limit 480/min), `/docs/api/capture` (batch body,
`$process_person_profile: false` for anonymous events, 20 MB body limit,
200 does not mean valid), and `/docs/privacy/data-storage` (deletion).

Verified from docs:

* Personless events "have no person to delete, so the person deletion tools
  can't remove them on their own". The documented workaround is a `$set` event
  for the distinct ID, then `bulk_delete` with `distinct_ids` +
  `delete_events: true`.
* `bulk_delete` returns 202 with `persons_found`, `persons_queued_for_deletion`,
  `persons_deleted`, `events_queued_for_deletion`, `deletion_errors`. It
  reports failures in `deletion_errors`, not as an error status.
* Event deletion is asynchronous ("weekends on PostHog Cloud"; cutoff 2 days
  before a batch). Status comes from `persons/deletion_status` (`pending`,
  `completed`, `all`, `person_uuid`) with `delete_verified_at`.
* Discarding client IP is a project setting. The default GeoIP is a
  transformation, not a property.

**Not documented, so verify at G5 on an authorized synthetic project:**

* the exact trailing-slash path of `bulk_delete` and `deletion_status`
  (inferred from the docs anchor)
* max items per bulk delete (source caps at 50)
* the `deletion_status` response shape
* whether `uuid` de-duplicates replays
* whether `$geoip_disable` is honoured
* whether the request IP is used when `$ip` is absent (moot: server-side
  egress IP only)
* whether an empty-property `$set` creates a profile (source sends one fixed
  marker property)

**This source does not claim that personless-event deletion works.** A
job reaches `verified` only when the vendor reports `delete_verified_at` for
that person.

## 3. Dispatch (worker only)

1. **Exposure marker.** A short transaction commits `vendor_exposed_at` on the
   owner's current subject (`FOR UPDATE SKIP LOCKED`), before anything is sent.
2. **Fenced send.** Lock consent rows (`FOR UPDATE SKIP LOCKED`, user order),
   then the owners' due outbox rows (`FOR UPDATE SKIP LOCKED`). Send only if:
   * the grant is valid now,
   * generation and subject match,
   * exposure is committed, and
   * the row is younger than the outbox age.
   Locks are held across ONE bounded send. Then commit (delete sent rows, or
   apply backoff).
3. **Bounds.** 5 attempts with exponential backoff from 30 s. At most 20
   batches of ≤ 50 rows per pass.

Lock order everywhere is **consent → outbox**: withdrawal, account purge (now
locks consent first), rotation and dispatch. PG-qualified outcomes:

* A withdrawal or purge waits for at most one in-flight send.
* A busy account is skipped, never waited on.
* Duplicate wakeups never double-send.
* Ordinary product writes, emitters and the Settings read are not delayed by
  an in-flight send.

## 4. Vendor erasure receipt lifecycle

`pending` → (`$set` marker batch accepted) `materialized` → (person found
after ≥ 15 min settle; `bulk_delete` 202 with no `deletion_errors` and queued +
deleted ≥ requested) `requested` → (`deletion_status` reports
`delete_verified_at`) `verified`.

* `not_required` = the subject was never sent.
* A job is never terminal-failed. Backoff is capped at 24 h, and unfinished
  jobs older than the overdue threshold are counted and logged.
* Each pass has a 60 s vendor-call budget, so the job-row transaction stays
  short.
* Erasure runs whenever erasure credentials exist, **also while the product
  flag is OFF** (a rollback must not strand owed deletions). It runs only on
  analytics tables.

## 5. Scheduling

* `worker.py::_product_analytics_tick` enqueues one RQ job per 60 s, with fixed
  job id `axisai-product-analytics-pass-v2` (`unique=True`, so duplicate
  wakeups collapse). It does so only when `FITX_PRODUCT_ANALYTICS_ENABLED=1` or
  erasure credentials are present. Neither is true anywhere today, so the tick
  enqueues nothing.
* `app.jobs.tasks.run_product_analytics_pass` → `pipeline.run_pass`: cleanup
  (always) → dispatch (flag ON + config) → erasure (erasure config). It logs
  counts only.
* Manual: `flask product-analytics-run`.

## 6. Durable withdrawal (mobile)

* **Device.** The switch-off denies synchronously, writes an origin-bound,
  account-keyed marker (no payload) **before** the request, and keeps
  analytics off for that account across restarts until the server confirms.
  * The withdrawal is retried first on every session restore or refresh. It is
    idempotent (PUT).
  * A later "allow" first completes the withdrawal, so no stale grant
    revives.
  * Consent decisions are serialized, so a withdrawal is never overtaken by an
    in-flight grant.
  * An unreadable marker fails closed.
* **What can and cannot be prevented.** Queued observations are discarded
  (`stoppedBeforeSend`). A batch whose request already started may have
  reached the server (`stoppedInFlight`). The server then either refuses it
  (409, stale grant) or deletes it with the undispatched outbox at
  withdrawal. Events the vendor already received are subject to vendor
  erasure (§4). **Nothing can recall data a vendor has already received;**
  deletion is requested, not recalled.
