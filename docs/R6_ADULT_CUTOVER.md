# R6 adult cutover and migration recovery

Source correction only. No production execution, source merge, deployment
approval, provider call, M4/M5 work or staging custody change is authorized.
The accepted nginx barrier and DB metadata reader are not repeated.

## Protected sequence

Candidates whose migration graph contains `ad17de1e0001` take this path.
There is no environment switch that disables it in the transaction.
Retain protected exact-main/CI/workflow identity, target/account authority,
candidate snapshot, canonical exclusive production lock, installed route
helper parity, overlap classification, immutable images and capability gates.

1. Prove the normal existing loaded baseline, or the existing independently
   authorized forward-recovery baseline/schema predicates. Build/prove the
   exact candidate and pin the old worker image before the interruption.
   The previous release's LOADED eligibility is proven after the migration
   gate and before any mutation, except on the FIRST adult transition
   (`ad17de1e0001` is in the migration delta, i.e. the previous source predates
   it): that release cannot carry the adult rules, enabling the flag there would
   switch on its legacy pre-18+ rules, and this sequence removes its web and
   worker before migration and never restores them. Its identity/health/revision
   is still proven; only `--require-eligibility` is deferred for it and the
   transaction records `previous-eligibility-not-required`. The candidate's
   eligibility proof can never be waived, and every later release (adult graph
   already in the previous source) and every non-adult release still require
   the previous release's loaded eligibility. Rollback before closure verifies
   the previous web only to the level the baseline proved.
2. Inspect the complete nginx include tree and all public TCP listeners,
   canonical app proxies (all hostnames, IP/IPv6 and paths), container
   published ports and web identities. Unknown/direct origins refuse before
   migration. Record nginx master/worker PID **and start ticks**.
3. Close any surviving candidate's local registration latch. Remove the exact
   old web using the existing revision-guarded slot/main lifecycle. Remove the
   revision-bound old worker too: its `unless-stopped` policy must not restart
   an unqualified image after DDL. Prove no old/foreign serving web, old worker
   or established old-backend request sockets survives. Keep the old port
   empty. Only then emit `old-registration-closed` and begin migration.
   Existing graceful-stop bounds apply; a request can finish before closure
   or disconnect during shutdown. Completed external/database effects are
   never described as cancelled or undone. A failed absence proof stops.
   For a blue/green release with an unchanged adult migration graph, first
   prove the actual Alembic heads equal the archived graph's full head set.
   Persist the previous SHA, candidate SHA, old slot and candidate image ID
   in the deploy checkout's Git metadata before attempting closure. This
   private mode-0600 receipt survives checkout resets and command cleanup.
4. Run the existing `release-prepare` once. Start the exact candidate in
   read-only mode with literal `R6_REGISTRATION_ADMISSION=closed` and one
   Gunicorn worker. A boot-random local receipt gates both web and mobile
   registration **before their handlers**. Missing, malformed, stale or
   previous-boot receipt leaves registration closed. No HTTP API opens it.
5. Verify the actual loaded adult candidate and its closed latch, immutable
   image/revision, loaded eligibility=true, source adult/minor pins, deletion
   table/sequence runtime privileges and real application pooled UTC. Preserve
   existing full completion fence/floor/claims and pre-switch re-verification.
6. Switch only through the existing installed route helper. Registration is
   still closed even if a request was buffered before the switch. Prove the
   route/public ingress, replace/prove the exact worker, preserve Redis identity.
7. Within **150 seconds at 2-second cadence**, require master continuity,
   unchanged configuration except the canonical upstream include, retirement
   of every captured old nginx worker identity and a stable new generation.
   No forced nginx kill, observation retry, configuration repair or alternate
   deployment mechanism. An already-routed recovery candidate uses one
   explicit unchanged-configuration validation/reload to obtain this generation
   boundary; it never switches back to an old backend.
8. Prove all-origin candidate isolation, real web and worker pool/ACL receipts
   and the full floor/fence again while registration stays closed. Checkout
   commits to the exact candidate. Only then atomically write the boot/revision
   registration receipt and verify it through the actual loaded process.
   Record `adult-registration-open`, durably mark any same-head receipt as
   committed, then emit the release commit event.

This intentionally interrupts web/worker availability during preparation.
The founder's later exact-source/run/attempt/window deployment GO must accept
that interruption and the existing readiness-probe effects. No outage-duration
guarantee is inferred from step ceilings; the existing total phase cutoff
remains binding. Stopping/removing old processes is a future protected-run
action, not permission to run a separate human bootstrap now.

## Blocking runtime proofs

`account_deletion_record` must exist in public. The actual runtime role must
have SELECT, INSERT and DELETE and USAGE on its serial sequence (ownership
can supply them). No test deletion, grant, role change or repair is performed
by this gate. The additive migration and its retention protections are unchanged.

The web health proof reads the loaded serving process's actual SQLAlchemy
pool; it checks out, returns and checks out again in READ ONLY transactions.
The real worker entrypoint obtains the same proof using its cached task app
before consumption/heartbeat, writes a sanitized process PID/start/revision
receipt, and disposes connections before forking. The controller binds that
receipt to the live worker process and exact image. Every later application
pool checkout, including work-horse connections, rejects and invalidates a
non-UTC PostgreSQL session. This is not an assertion that a fresh metadata
reader established existing pool timezone. Failed/missing proof prevents
registration reopening and cutover acceptance.

## Failure phases

| Phase | Disposition |
|---|---|
| Before closure attempted | Existing failure behavior preserves the proven baseline. No migration has begun. |
| Closure attempted but not proved | Stop and report `closure_proven=false`. An exact pending same-head receipt allows a separately authorized retry to finish and prove closure. |
| Closure proved, migration not begun | Keep admission closed and report incomplete recovery. Use the pending same-head receipt with a new protected authorization for the exact candidate. |
| Migration failed/timed out | Keep closure; report schema outcome as not inferred, and migrator-cleanup ambiguity where applicable. No automatic old route/worker restoration, schema downgrade or assumed transaction rollback. |
| Candidate preparation / switch / worker / pre-open failure | Keep the candidate, remove its open receipt if present, preserve the empty old port, and report incomplete recovery. Never remove the sole candidate or restore old images. |
| Ambiguous registration-open result before release commit | Close the candidate latch, retain the candidate pair, report incomplete recovery. If closure itself is unproved, explicitly escalate that fact. All mandatory gates precede the open attempt. |
| After release commit | Fix forward under a separately authorized successor. Keep the established route, processes and registration state. A committed same-head receipt refuses replay of that release. |

Both recovery paths require a separate exact
`forward-recovery:<sha>:<run>:<attempt>` founder authorization, the protected
controller's snapshot and freshness checks, the host lock and route authority,
and the full pre-open checks above. Recovery without a pending receipt still
requires actual current heads all in the candidate graph and at least one
outside the previous graph. The pre-migration `f01a4e000003` head does not
satisfy that predicate.

Same-head recovery uses the old slot and immutable image from the pending
receipt. A retry requires the unchanged adult graph, exact live heads, absence
of the identified release-preparation container, and compatible checkout, route,
slot and worker identities. It reuses the original image and reruns preparation
only when success was never durably recorded. Preparation is idempotent and
still runs only after old process and socket absence. A crash after checkout,
including after an open write, may resume while the receipt remains pending.
The retry closes the candidate latch and repeats the pre-open proofs. A commit
receipt I/O error preserves the verified open candidate and reports incomplete
recovery. Inspect the durable receipt to determine retry eligibility.

Hold changes to `origin/main` throughout this release and any same-head retry.
The controller still requires the exact current main SHA. An advanced main ref,
missing pinned image, surviving preparation container, unclassifiable nginx
route or a running unusable routed candidate requires incident reconciliation
before retry. Preserve the Git metadata receipt and its image during incident
handling. A same-head interruption predating the receipt has no automatic
same-head recovery authority.

Repeated attempts preserve the pending receipt and the exact image. Persistent
pre-commit faults can still leave a service outage. A different repair SHA has
no authority over the pending receipt, and a running routed candidate is kept
even when deep health fails. Resolve such an incident through a separately
reviewed procedure. After commit, a distinct normal release can proceed from
the committed slot under the existing protected controller.

A later app-process restart invalidates its open receipt and fails closed.
Reopening requires a separately authorized protected release/recovery action;
restarting a process is never treated as carrying its old admission proof.

## Same-head recovery evidence for post and watch

The recovery attempt emits either fresh preparation or reuse of the durable
prepared receipt. It emits either a route switch or proof of the already
active candidate route after the nginx reload barrier. After the durable
commit, a runtime event records the exact image and the web and worker Docker
IDs and start times. Post and watch must match the live pair to that event.

`scripts/r6_recovery_acceptance.py` checks the attempt-specific protected
deploy job, exact main SHA and tree, reviewed diff, successful main-push CI,
same-head schema evidence, available snapshot marker, and ordered admission and
commit events. It admits evidence only for `post`, `post2`, `watch1` and
`watch2`. Exit with committed residue keeps the candidate serving and fails
this acceptance profile. The regular deployment event profile stays in effect
for normal releases.

The proposed [release checker adapter](../qualification/r1-recovery/release-checker.patch)
is for the strict F3 one-shot package. It adds the recovery profile and Docker
identity fields to its read-only probe, and retains its existing alarm,
snapshot, route, health, containment and journal checks. Its base checker and
probe SHA-256 are respectively
`2844d9a89c0b7ffb8527bd5a8031d46ce944eb2827a157980c673ba58b6654d8` and
`f2e594ed2494a77b6f114dd1ba01ac94ba6e9310125f60048ae41f1730fd5adf`.
The adapter has no finalized release binding. Applying it to the authoritative
package or collecting live evidence requires separate authorization.

## Focused qualification

Hermetic adverse-order tests cover the same-head preparation outage in both slot
directions, durable retry across checkout, phase and image identity refusal,
lock and schema gate behavior, and post-commit replay refusal. They also cover
old registration during migration,
buffered-generation timeout, missing/false loaded eligibility/latch, uncertain
migration completion, post-migration rollback refusal, ACL/pool refusal,
successful last-step opening and post-commit fix-forward. All-origin parser
tests reject alternate handlers/listeners/proxies. The existing PostgreSQL 18
CI job additionally executes `test_r6_adult_runtime_pg.py`: actual role/table
and sequence privilege negatives plus a deliberately drifted pooled timezone.
These tests use only the disposable CI database, not production or staging.
Existing R6 admission, recovery, capability and slot tests retain their gates.
