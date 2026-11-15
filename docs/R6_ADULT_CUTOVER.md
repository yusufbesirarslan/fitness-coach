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
   Record `adult-registration-open`, then the existing release commit event.

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
| Closure attempted but not proved | Stop, report `closure_proven=false`, no migration. No automatic restart or repair. Reconcile the existing operation under incident ownership. |
| Closure proved, migration not begun | Keep admission closed and report incomplete recovery. Do not automatically recreate old images merely to end the interruption. If the DB remains at the previous head, existing post-migration forward recovery is inapplicable; recovery owner/founder must separately disposition this outage. No new recovery mode is invented here. |
| Migration failed/timed out | Keep closure; report schema outcome as not inferred, and migrator-cleanup ambiguity where applicable. No automatic old route/worker restoration, schema downgrade or assumed transaction rollback. |
| Candidate preparation / switch / worker / pre-open failure | Keep the candidate, remove its open receipt if present, preserve the empty old port, and report incomplete recovery. Never remove the sole candidate or restore old images. |
| Ambiguous registration-open result before release commit | Close the candidate latch, retain the candidate pair, report incomplete recovery. If closure itself is unproved, explicitly escalate that fact. All mandatory gates precede the open attempt. |
| After release commit | Existing fix-forward only. Retirement/floor/housekeeping residue cannot trigger old-image rollback. |

After closure, **even a healthy pinned old image or compatible completion
manifest is not rollback authority**. Existing forward recovery still requires
actual current heads all in the candidate graph and at least one outside the
previous graph, compatible partial route/container state, all existing gates,
and separate exact `forward-recovery:<sha>:<run>:<attempt>` founder authorization.
The accepted pre-migration `f01a4e000003` head does not satisfy that predicate.
No blanket availability/restart compatibility is claimed for old `94a3f170`.

A later app-process restart invalidates its open receipt and fails closed.
Reopening requires a separately authorized protected release/recovery action;
restarting a process is never treated as carrying its old admission proof.

## Focused qualification

Hermetic adverse-order tests cover old registration during migration,
buffered-generation timeout, missing/false loaded eligibility/latch, uncertain
migration completion, post-migration rollback refusal, ACL/pool refusal,
successful last-step opening and post-commit fix-forward. All-origin parser
tests reject alternate handlers/listeners/proxies. The existing PostgreSQL 18
CI job additionally executes `test_r6_adult_runtime_pg.py`: actual role/table
and sequence privilege negatives plus a deliberately drifted pooled timezone.
These tests use only the disposable CI database, not production or staging.
Existing R6 admission, recovery, capability and slot tests retain their gates.
