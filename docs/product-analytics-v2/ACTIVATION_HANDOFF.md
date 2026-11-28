# Product analytics v2: G2 / G3 / G5 activation handoff (final checklist)

**Nothing in this file has been executed.** This source session performed no
vendor provisioning, made no PostHog request and touched no AWS. Each gate
needs its own founder approval. The order is fixed:

```
counsel disposition (COUNSEL_DECISION_PACKET.md) → G2 → G3 → G5 → G6 → G7
```

## G2: contract and transfer (founder + counsel)

| # | Item | Done when |
|---|---|---|
| G2-1 | PostHog DPA executed | signed copy filed; EU (Frankfurt) data location stated |
| G2-2 | Subprocessor list reviewed | list version and date recorded; onward-transfer paths (group, support access) answered (Q-5) |
| G2-3 | KVKK Art. 9 transfer | Board standard contract signed with the right PostHog entity, **and** notified to the Authority within 5 business days of signing (Art. 9(5)); notification receipt filed |
| G2-4 | VERBİS / records of processing | entry updated for the analytics purpose, data category and recipient group (counsel to confirm the obligation) |
| G2-5 | Backups | written answer: do deleted events persist in vendor backups, and for how long (RETENTION.md §4) |
| G2-6 | Breach notification and staff access | DPA terms recorded |

## G3: vendor provisioning (founder, separate approval)

| # | Item | Done when |
|---|---|---|
| G3-1 | One **synthetic** EU project for G5, separate from any production project | project id recorded (not a secret); no real user data ever sent to it |
| G3-2 | Project settings OFF | autocapture, session replay, heatmaps, surveys, feature flags, web analytics, person profiles processing (the source sends `$process_person_profile: false`); **discard client IP** ON; GeoIP transformation OFF |
| G3-3 | Events retention | the plan's minimum, recorded. Whether 90 days or less is selectable is verified here (second layer only, RETENTION.md §3) |
| G3-4 | Credentials | project token and a personal API key scoped to that project, with the minimum scopes for persons read/delete. Stored in Parameter Store and **never** printed, logged or committed. The source refuses capture unless the erasure key is present too |
| G3-5 | Worker configuration | `PRODUCT_ANALYTICS_POSTHOG_REGION=eu`, the keys above, retention keys per RETENTION.md §3 (capture is refused unless they fit the ceiling), resolver options on the worker (TRANSPORT_AND_ERASURE.md §1a) |
| G3-6 | Monitoring | metric filter and alarm on `event=erasure_overdue`, `event=dispatch_aborted` and `capture=halted_*` |
| G3-7 | Restore runbook | the RETENTION.md §4 restore disposition is adopted and owned |

## G5: authorized synthetic E2E (analytics owner, separate approval)

Run on the G3 synthetic project only, with synthetic accounts and synthetic
events, from a non-production worker.

| # | Verify | Pass criterion | Source dependency |
|---|---|---|---|
| G5-1 | Exact stored payload | for each of the 20 events, the stored event has only the catalog properties plus vendor-added system fields; list every vendor-added field. No `$ip`, no GeoIP properties, no person profile | `wire.py` allowlist |
| G5-2 | Paths and shapes | `bulk_delete/` and `deletion_status/` trailing-slash paths accepted; `persons` lookup returns `uuid` or a string `id`; `deletion_status` response shape matches the parser | `posthog_transport.py` |
| G5-3 | Personless-event deletion | after the `$set` marker + `bulk_delete(delete_events=true)`, the subject's personless events are **gone** from a HogQL query, and `delete_verified_at` is set. Record the timestamps | erasure lifecycle |
| G5-4 | Deletion lag | measured from retirement to events gone, across at least one weekend batch. It must fit O (proposed 14 days) | RETENTION.md §2 |
| G5-5 | Replay de-duplication | the same batch (same `uuid`s) sent twice is stored once, or the duplicate count is recorded and the dashboards adjusted | lock-cap and retry duplicates |
| G5-6 | Max bulk-delete batch | 50 ids accepted (or the documented maximum) | `MAX_DELETE_BATCH` |
| G5-7 | `$geoip_disable` honoured | no GeoIP properties stored | wire fixed properties |
| G5-8 | Effective retention feasibility | G5-3, G5-4 and the G2-5 answer together show raw data ≤ 90 days including lag for the configured R and O; otherwise keep capture off and return to the founder | RETENTION.md |
| G5-9 | Dashboards | every panel in `dashboards.json` imports and its HogQL executes on synthetic data. Numerator/denominator are checked against a hand count for at least P3a, P5b and P7 | DASHBOARDS.md |
| G5-10 | Fail-closed behaviours | missing erasure key → no capture; unbounded retention → `capture=halted_retention_unbounded`; an artificially overdue job → `capture=halted_erasure_overdue` | pipeline |

Evidence goes to a dated receipt: request counts, timestamps and pass/fail
for each line. It must contain no tokens and no pseudonyms beyond the
synthetic set.

## Not part of these gates (already accepted, reuse)

* PG concurrency (20 tests).
* Durable withdrawal (mobile, 30 tests).
* The transport discipline tests.
* The W1/W2/W3 device evidence. No new device campaign.
* Accepted P3s: **B-5**, where one busy account can occupy a pass's candidate
  window, and **M-4**, where a corrupt device marker fails closed with no
  in-app repair.
