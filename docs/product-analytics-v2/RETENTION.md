# Product analytics v2: retention and deletion (PROPOSALS pending approval)

This reconciles the earlier v2 proposal (12-month raw vendor retention) with
the privacy-first v1 proposal (90-day raw). **Recommended starting policy:**
raw ≤ 90 days. Nothing here is a production setting. All AxisAI-side values
are configuration with no production default, and the code enforces only
technical bounds.

| Data | Proposed maximum | Mechanism | Config key (bounds) | Unset |
|---|---|---|---|---|
| Undispatched outbox row | 48 h | `retention.cleanup` deletes rows older than the configured age; the 48 h ceiling is absolute in code | `PRODUCT_ANALYTICS_OUTBOX_MAX_AGE_HOURS` (1–48) | 48 h ceiling |
| Raw events at the vendor | **90 days** (+ vendor deletion lag) | **pseudonym rotation**: a live subject older than N days is retired to vendor erasure and replaced for the same grant (the user sees no change) | `PRODUCT_ANALYTICS_SUBJECT_MAX_AGE_DAYS` (7–400) | no rotation |
| Raw events at the vendor, second layer | as low as the plan allows | PostHog project setting "Events retention" (gate G3) | vendor console | n/a |
| Anonymized aggregates | 12 months | weekly founder-review snapshot (P9) exported as counts with small-cell suppression (n < 5 shown as "<5"), no pseudonyms | not built (no vendor) | n/a |
| Erasure receipts (`verified`/`not_required`) | 90 days after completion (proposal) | `retention.cleanup` purges terminal receipts only; unfinished jobs are never purged | `PRODUCT_ANALYTICS_ERASURE_RECEIPT_RETENTION_DAYS` (1–400) | kept (evidence) |
| Erasure overdue alarm | 30 days (proposal) | pass counts unfinished jobs older than N and logs `event=erasure_overdue` | `PRODUCT_ANALYTICS_ERASURE_OVERDUE_DAYS` (1–90) | no count |
| Consent / processing-authority evidence | account lifetime | one row per account: status, generation, notice version, timestamps. No history table | n/a | n/a |
| Grant validity | 365 days, then re-ask (proposal) | `consent.GRANT_TTL` | code constant (consent policy, not retention) | n/a |

## Vendor facts that shape this (posthog.com, read 2026-11-28)

* Event retention is configurable per organization and project. The plan
  table says Free = 1 year, fixed. Paid defaults and maxima are larger.
  Whether 90 days is a selectable value is **not documented**, so verify it
  at G3.
* "PostHog deletes events older than the window at the next monthly cleanup,
  not right away". So a retention setting alone means up to window + ~1 month.
* Events without a person profile "have no person to delete". The documented
  deletion path is: create a profile, then bulk-delete persons with
  `delete_events`. Deletions run asynchronously "during non-peak usage times
  (weekends on PostHog Cloud)", with a cutoff two days before each batch. No
  SLA is documented.

Hence **pseudonym rotation is the AxisAI-enforced bound**. With N = 90, raw
vendor data for a subject lives at most about 90 days plus one deletion batch
(≈ 2–9 days), independent of the vendor's plan. The trade-offs:

* Analysis cannot follow a person across a rotation. D30 retention is
  unaffected with N = 90 (cohorts mature within one subject lifetime).
* A rotated subject has no `analytics_enabled` anchor and is therefore
  excluded from cohort panels. It is still counted in activity panels
  (DASHBOARDS.md).
* Undispatched rows of the old subject are deleted at rotation, never re-bound
  (a small undercount).

## Deletion lifecycle (AxisAI side)

| Trigger | Immediate (same transaction) | Then |
|---|---|---|
| Withdrawal | outbox rows deleted; generation bumped (fences in-flight sends); erasure job (`pending` if the subject was ever exposed, else `not_required`) | worker: profile marker → bulk delete → verification |
| Account deletion | erasure job created before the mapping is deleted (consent row locked first) | same |
| Rotation | erasure job for the old subject; new subject minted | same |
| Outbox expiry | row deleted | none (never sent) |

The device never stores an event. A pending withdrawal is persisted on the
device only as an account key (mobile `AnalyticsWithdrawalStore`).
