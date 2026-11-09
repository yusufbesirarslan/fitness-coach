# F01 O2-B bounded post-RC source integration

Task: `AXISAI-F01-O2B-POST-RC-INTEGRATION-01`.

## Provenance and scope

Requested base: `7e7dcd6600843b2a573a53e21e08e8cdd92ba8f8` (#498).
Current target main: `066a17d2e83d6a55b48929d8155196e9a379ae30` (#499).
Source #490: `cbde46d76317bb004e647e9dcea9b0fbfb3ca75d`.
That head merges #498 into prior reviewed `ed0294af`; O2-B runtime bytes are unchanged.
Only the O2-B delta after `701838b8` is integrated: `a65ce6fa` and
`ed0294af`. The original F01 boundary and Social integration are already in
main; neither the older branch nor its unrelated history is merged again.
The source's stale revision-count changes are replaced by 64-revision head
assertions. The O2-B revision follows current-main FatSecret revision
`e1a5f0c3d2b4`; existing migration bytes, including F01, remain unchanged.
The newly merged `MealLogRetainedTotal` health aggregates are erased after
meal rows, preserving owner isolation and concurrent retention-worker ordering.

## Behavior

`health-fitness-v2-review`, with `APPROVED_VERSION=None`. Withdrawal commits
stopped health authority plus a durable erasure obligation atomically. The
request attempts storage deletion before one atomic row erasure. Failure or a
named legal hold leaves the debt pending and regrant returns 409
`HEALTH_ERASURE_PENDING`. Retried requests do not duplicate debt; a new
withdrawal cannot postpone the oldest debt's 30-day deadline. No restore.

The scope and preserved ordinary account data are listed in
[F01_FIRST_PARTY_HEALTH_CONSENT.md](F01_FIRST_PARTY_HEALTH_CONSENT.md).
Daily maintenance and the controller CLI scan all initially pending owners in
bounded batches. Overdue debts and overdue hold reviews are logged without
health content. Completed erasure evidence expires after three calendar years.

Integration reconciliations close source defects: duplicate-worker/regrant and
hold/storage races, retry starvation, avatar-key aliases, remaining canonical
health profile fields, unproven missing-account completion, and PostgreSQL
completion-claim retention. The new migration narrowly reconciles that last
retention rule; admission, immutable claims, binding ownership, capability floor
and truncation protections remain exact. This never raises the floor.
Workout-derived Redis scores require successful owner-specific removal before
the debt completes; configured cache failures remain pending and retry safely.
PostgreSQL cache publishers (including rebuild and delayed commit callbacks)
share health authority through publication and re-read current counters, so
pre-erasure snapshots cannot republish erased scores. Global ranking semantics
and ordinary account data are preserved.

## Exact paired-source consistency

Read-only source pins:

- Landing #78: `74b2b3c44aeb4484eb165b0fd2fd9d2720400e25`,
  `docs/legal/launch-v1/LAUNCH_PRIVACY_{EN,TR}.md` §§3,4,10 and
  `PUBLICATION_CHECKLIST.md` RT-7.
- Mobile #177: `b1c9912c8f1808bf8ff702c083d32daadf4b6ca4`,
  `health_consent.dart`, `health_consent_panel.dart`, EN/TR ARB confirmation and
  pending strings, and its decode/confirmation tests.

The common contract is immediate health-feature stop, erasure within 30 days,
no restoration after a later grant, and preservation of the account and
non-health records. Landing/F01 additionally describe the specific legal
claim/request exception, held only for that purpose and as long as needed.
RT-7 records the 30-day bound, holds until the matter ends and three-year
erasure evidence. Mobile's shortened confirmation omits that exception, but
its full disclosure is the exact server text and its pending state prevents
regrant throughout a hold. No export is offered or promised. No landing or
mobile files change in this task.

The status API remains additive: `erasure: null | {state, requested_at, due_by}`.
Mobile reads `erasure.state == pending`, hides grant and displays its pending
notice. Its endpoint and request keys match the backend. F01 EN and TR match the
reviewed source v2 bytes exactly. Digests are calculated from those candidate bytes;
neither candidate is published and no v1 grant is grandfathered.

## Preserved boundaries and limits

Health processing/publication fencing, D1 empty approval registries, service
cohort closure, Social default closure, global rankings under independent
health authority, Launch Free ledgers, current-main FatSecret 24-hour retention/R-5/advice protections
and Apple OFF remain closed or unchanged as applicable. #499's covered-path
D-B2 disposition and retention safeguards are preserved; this task makes no
new FatSecret licensing/legal decision or operational deletion.

The new retention function is strictly verified. Earlier images that only
recognize the original function fail their schema verifier closed; this is a
forward-recovery constraint for a separately authorized rollout. No operational
migration, staging/production data, historical deletion, deployment, merge,
publication or activation is authorized or performed here.

## Qualification

Disposable SQLite and PostgreSQL fixtures cover two populated accounts,
partial storage/local failure, retry and regrant, repeated withdrawal, holds,
expired hold reviews, deadlines, canonical deletion proof, avatars and foreign
keys. Installed PostgreSQL fence tests cover allowed erasure, forbidden direct
binding deletion, absent/mismatched obligation, current grant, other-owner debt,
workout hold and verifier tampering. Exact-head hosted full CI and independent
review are recorded in the draft PR, rather than treating local subsets as a
full-suite pass.
