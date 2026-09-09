# Workout session activation runbook — `FITX_WORKOUT_SESSIONS_ENABLED`

Canonical operator procedure for staged activation of the persisted
workout-session lifecycle (rollout flag **6**).

- Flag inventory and rationale: [FEATURE_FLAGS.md](FEATURE_FLAGS.md)
- Staged order and the general activation prerequisites: [ROLLOUT.md](ROLLOUT.md)
- Metrics, log lines and SLIs: [OBSERVABILITY.md](OBSERVABILITY.md)
- State contract: [WORKOUT_STATE.md](WORKOUT_STATE.md)
- Readiness assessment and evidence record:
  [`2026-09-07-sprint14-pr5-workout-session-activation-readiness.md`](superpowers/specs/2026-09-07-sprint14-pr5-workout-session-activation-readiness.md)

Owner: **@yusufbesirarslan**. Written 2026-09-07 (Sprint 14 PR5).
Review point: **2026-11-01**, the flag's own `review_by`.

---

## 0. Scope, and what this document does not do

This runbook describes how an operator would exercise the flag in an authorized
**non-production** environment, decide GO or NO-GO, and roll back.

**It does not authorize production activation.** Nothing here is a production
instruction, and no command below targets production. Reaching "GO" at the end
of §13 means *the technical prerequisites for a future operator decision are
documented and staging-proven* — it does not mean production activation is
approved, scheduled or performed. Production activation remains a separate,
explicitly authorized operator decision taken against [ROLLOUT.md](ROLLOUT.md).

Stated as the distinction it is: **ready for activation ≠ activated.**

**Nothing in this repository activates a flag, and no automation in this
repository is permitted to.** Flags live in the host `.env`, which the deploy
pipeline never touches.

---

## 1. Environment prerequisite — read this before anything else

> **STATUS AT 2026-09-09 (later the same day): THE LIVE STAGING EXERCISE HAS
> BEEN RUN.** Cases A–N, observability, rollback and post-rollback inertness
> were observed against the isolated staging environment in
> **[STAGING.md](STAGING.md)** on tested SHA
> `c3f579a259d13bace018f0296de030aedeb191d7`. §13 now evaluates to **GO**.
> That is technical staging-readiness, not production activation — see §0.
>
> The environmental blocker recorded below is **closed**. An isolated
> non-production environment was established as a standalone infrastructure
> slice (#290) and is documented in **[STAGING.md](STAGING.md)** — a
> stopped-by-default EC2 instance in account `852128326881` / `eu-central-1`,
> running the same application image as production against its own
> container-local PostgreSQL and Redis, its own Cognito pool, its own synthetic
> accounts, zero inbound rules, and Session Manager-only administration. It holds no
> production data and cannot reach the production database, the production
> metric namespaces, production storage, or a real mailbox.
>
> Read [STAGING.md](STAGING.md) before §2: it supplies the concrete identity,
> deploy path (`scripts/staging_control.py`, exact 40-hex SHA only), browser
> access path (Session Manager port forwarding to `localhost:5000`) and test-account
> convention that the placeholders in §2 stand for.
>
> **Earlier the same day, retained as history.** Before the exercise ran,
> this banner said the environment existed and the matrix had not yet been
> run, so §13 still evaluated to **NO-GO** with live rows `BLOCKED`. That
> was correct at the time. An environment that exists is a precondition, not
> evidence; the evidence is the filled record in §14.1.

**Previous status, retained as history.**

> **STATUS AT 2026-09-07: THERE IS NO STAGING ENVIRONMENT.**
>
> This procedure cannot currently be executed. The gap is environmental, not
> code: the repository's deployment contract ([DEPLOYMENT.md](DEPLOYMENT.md))
> describes exactly one target — "the one configured EC2 instance" — and the
> only GitHub Actions environment is `production`. No second host, database,
> deployment path, environment name or flag-management surface is defined
> anywhere in this repository.
>
> Provisioning a staging environment is out of scope for Sprint 14 PR5 and is
> recorded as the sprint's exit blocker.

That paragraph was true when it was written and is superseded by #290. The
prohibitions it carried are **not** superseded and still bind:

- **Do not substitute production.** Do not substitute a developer workstation
  either: a local `docker compose` stack is not the deployed artifact, does not
  exercise the deployment or migration path, and is not the staging target.
  Evidence gathered there is *structural*, not *operational*, and §14 forbids
  recording it as the latter.
- The staging environment establishes nothing about activation on its own. See
  [STAGING.md](STAGING.md) §12: establishing an environment and activating a
  feature are separate acts with separate reviews.

Everything from §2 onwards was written to be executable the day such an
environment exists. That day has arrived; it is deliberately complete rather
than provisional.

---

## 2. Fail-closed environment selection

Every mutating step in this runbook requires the operator to name the target
explicitly. There is no default. An omitted variable aborts the command; it
never silently resolves to production.

Export these once per session, before anything else:

```bash
# The environment's own name, as its owner calls it. Must be exactly "staging".
export FITX_ACTIVATION_ENV=staging
# The host you intend to mutate.
export FITX_ACTIVATION_HOST=<STAGING_HOST>
# The production host, supplied so the guard can refuse it by identity.
export FITX_PRODUCTION_HOST=<PRODUCTION_HOST>
```

Then run this guard before **every** mutating step. It fails closed on an unset
variable, a non-staging environment name, or a target equal to production:

```bash
fitx_assert_staging() {
  : "${FITX_ACTIVATION_ENV:?refusing: FITX_ACTIVATION_ENV is not set}"
  : "${FITX_ACTIVATION_HOST:?refusing: FITX_ACTIVATION_HOST is not set}"
  : "${FITX_PRODUCTION_HOST:?refusing: FITX_PRODUCTION_HOST is not set}"
  [ "$FITX_ACTIVATION_ENV" = "staging" ] || {
    echo "refusing: environment is '$FITX_ACTIVATION_ENV', not 'staging'" >&2
    return 1
  }
  [ "$FITX_ACTIVATION_HOST" != "$FITX_PRODUCTION_HOST" ] || {
    echo "refusing: target host is the production host" >&2
    return 1
  }
}
fitx_assert_staging || return 1
```

This is documentation, not new automation: it is four comparisons an operator
can read in full before trusting it. Do not extend it into a deployment tool.

**Positive identity, established out of band, before any mutation.** The guard
above proves the operator *declared* a non-production target; it cannot prove
the target *is* one. Record all six, from the environment's own owner and from
the running process — not from this repository:

| Field | How it is established |
|---|---|
| environment name | the environment owner's own designation |
| host / instance identifier | cloud console or inventory, not inferred from a DNS name |
| non-production status | explicit confirmation from the environment owner |
| deployment revision | `/health?deep=1` → `revision` (§4) |
| database identity | the connection target recorded by the environment owner |
| flag values | `/health?deep=1` → `flags` (§6) |

If non-production identity cannot be positively established, **do not mutate the
environment.** Stop and record `PR5 STAGING EXERCISE BLOCKED — NON-PRODUCTION
TARGET NOT VERIFIED`.

### 2.1 Where these commands run

Every `.env`, `docker compose`, `grep .env` and `127.0.0.1` command in this
runbook runs **in a shell on the staging host itself**, inside that
environment's compose project directory — the directory holding its
`docker-compose.yml` and its `.env`. Its path is environment-defined and is
deliberately not written here; get it from the environment's owner and confirm
it before your first mutation:

```bash
fitx_assert_staging || return 1
pwd
test -f docker-compose.yml && test -f .env && echo "compose project directory confirmed"
```

`127.0.0.1:5000` therefore means *the service on the staging host*, reached from
that host — never a local development server on your laptop, and never a
tunnelled production port. If that `curl` is answered by anything other than the
staging service, every observation below is worthless. A local
`docker compose` run of this repository on a workstation is **not** a substitute
for a staging environment and must not be recorded as one.

The `fitx_assert_staging` function is defined in §2 and lives only in the
current shell. A new shell, a new SSH session or a resumed handover has **no**
guard — re-export the three variables and re-define the function before the
first mutating command. A missing definition is a hard stop, not a step to skip.

---

## 3. Test accounts and test data

- Use **dedicated staging test account(s) only**. Never a real user account, and
  never a production identity.
- Use controlled, disposable plan and workout data owned by those accounts.
- Keep the exercise bounded: the matrix in §8 needs a handful of sessions, not a
  load test.
- Do **not** delete data as part of rollback (§11). Persisted `WorkoutSession`
  rows are designed to be inert when the flag is OFF; leave them.

---

## 4. Verify the deployed revision

The exercise is only Sprint 14 evidence if the build under test actually
contains Sprint 14. Minimum acceptable revision:

```text
b77a1dc02c4ed050caa50c17f592d0826db084de
  fix(training): harden workout execution reliability (#289)
```

or a later reviewed `main` SHA proven compatible.

```bash
fitx_assert_staging || return 1
# Deep health is INTERNAL-network only by design; run this from the host itself.
curl -fsS 'http://127.0.0.1:5000/health?deep=1' | python3 -m json.tool
```

Record `revision` (that is `BUILD_REVISION`, the serving-revision proof), the
container image revision if the environment surfaces one, and the deployment
time.

**If the environment runs an older build, STOP.** Deploy the reviewed revision
through that environment's own established deployment procedure first. Do not
exercise stale code and report it as Sprint 14 evidence.

---

## 5. Verify the database prerequisites

Both migrations must be applied, and the schema must be at the repository head:

| Revision | Why it is a prerequisite |
|---|---|
| `a994f9bed783` | creates the partial unique index `uq_workout_session_active_owner`, which **is** the at-most-one-ACTIVE-session invariant. Enabling without it is unsafe. |
| `f5a6b7c8d9e0` | adds the execution columns (`checkpoint_revision`, `checkpoint_data`, `checkpoint_at`, `checkpoint_idempotency_key`, `checkpoint_fingerprint`, `workout_ref`, `plan_lineage_id`, `plan_mutation_version`). Without them a checkpoint cannot be persisted at all. |

Repository head at the time of writing: **`f5a6b7c8d9e0`**, single head.

```bash
fitx_assert_staging || return 1
# On the host, inside the web container:
docker compose exec -T web alembic current
docker compose exec -T web alembic heads
```

Expect `f5a6b7c8d9e0` from both, and exactly one head.

If the schema is behind, bring it forward **only** through that environment's
established deployment/migration procedure. **Never run an Alembic downgrade**
as part of this runbook, and never create a migration for activation.

---

## 6. Snapshot the flags before you change anything

```bash
fitx_assert_staging || return 1
# Authoritative: what the RUNNING process believes, not what a file says.
curl -fsS 'http://127.0.0.1:5000/health?deep=1' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["flags"])'
# The .env lines, for the values you are about to edit:
grep -nE '^(FITX_WORKOUT_SESSIONS_ENABLED|RUNTIME_METRICS_ENABLED|MOBILE_AUTH_ENABLED)=' .env
```

Expected safe starting state:

| Key | Expected before the exercise |
|---|---|
| `FITX_WORKOUT_SESSIONS_ENABLED` | `0` or absent |
| `RUNTIME_METRICS_ENABLED` | **unknown — do not assume.** The repository default is `0`. |
| `MOBILE_AUTH_ENABLED` | environment-defined; see the note below |

**`MOBILE_AUTH_ENABLED` decides whether the native half exists at all.** The
entire `/api/v1` blueprint is registered only when it is on. On an environment
where it is OFF, `FITX_WORKOUT_SESSIONS_ENABLED` opens the browser half only,
and matrix cases **E–K** in §8 cannot be exercised. Record which case applies
before starting; do not discover it halfway through.

Also record, as the pre-activation baseline:

- the serving revision from §4;
- shallow `/health` status;
- the `WorkoutSession` row count **for the dedicated test accounts only**;
- the current `[WORKOUT_STATE] anomaly` and `[WORKOUT_SESSION]` log state.

Do not collect data belonging to any other user.

---

## 7. Activation order — observability first, always

Readiness requires that you can *see* the feature before you *enable* it.

```text
1. deploy the verified Sprint 14 build                     (§4)
2. enable / verify RUNTIME_METRICS_ENABLED=1               (§7.1)
3. verify the runtime metric path is actually healthy      (§7.2)
4. capture the baseline                                    (§7.3)
5. only then enable FITX_WORKOUT_SESSIONS_ENABLED          (§8)
6. execute the lifecycle matrix                            (§8.2)
```

**Never reverse steps 2 and 5.** If runtime metrics cannot be observed, do not
enable workout sessions — activate nothing and record
`PR5 BLOCKED — OBSERVABILITY PREREQUISITE FAILED`.

### 7.1 Enable runtime metrics

```bash
fitx_assert_staging || return 1
# Edit .env so the line reads exactly: RUNTIME_METRICS_ENABLED=1
# (exactly 1 — a malformed value is rejected at boot, not read as a silent OFF)
docker compose up -d web worker
```

### 7.2 Verify the metric path is healthy

`WorkoutSessionLifecycle` is buffered in-process and flushed by a daemon thread
every `RUNTIME_METRICS_FLUSH_SECONDS` (default **60 s**) into the CloudWatch
namespace `RUNTIME_METRICS_NAMESPACE` (default **`FitX/Runtime`**). Lifecycle
command paths never call CloudWatch inline.

Verify all three, in order — a healthy flag value is not a healthy metric path:

1. the process reports metrics enabled (`/health?deep=1`);
2. the environment's IAM identity can actually publish
   (`cloudwatch:PutMetricData`);
3. **a datum for an existing metric arrives in the namespace after one flush
   interval.** Use a metric the current traffic already produces (for example
   `HttpRequests`, or the sampled `ThreadReserve` gauge). Do **not** use
   `WorkoutSessionLifecycle` for this check — it is legitimately absent until
   §8, so its absence proves nothing about the path.

If nothing arrives within two flush intervals, the prerequisite has **failed**.
Do not weaken it, and do not proceed to §8.

### 7.3 Capture the baseline

Capture a bounded baseline for the existing training surface **before**
activation. At minimum, record how each of these is observed and what it reads:

| Signal | Where | Expected before activation |
|---|---|---|
| training-blueprint request volume | `HttpRequests`, `Blueprint=training` | the environment's normal, whatever that is |
| 4xx / 5xx on that blueprint | `HttpRequests` by `Status`, `HttpServerErrors` | recorded as-is |
| deliberate load shedding | `HttpOverload` (503) | recorded as-is |
| rate limiting | `HttpThrottled` (429) | recorded as-is |
| **`WorkoutSessionLifecycle`** | `FitX/Runtime`, dimension `Event` | **absent / zero** — the feature is off |
| `[WORKOUT_STATE] anomaly` lines | `docker compose logs web` | recorded count, by `category=` |
| `WorkoutSession` `IntegrityError` | application log | **none** |

**Do not invent thresholds.** A low-traffic environment cannot support
statistical production thresholds, and a fabricated one is worse than none. Use
explicit *exercise expectations* instead — "case B emits exactly one
`checkpointed`" — which is what §8 and §9 are built from.

Record the baseline as: environment · timestamp · build revision · metric
namespace · query and window · observed values · the low-traffic limitation. No
credentials. No user identity. Aggregates in preference to samples.

---

## 8. The lifecycle exercise matrix

Enable the feature only once §4, §5, §6 and §7 are all green:

```bash
fitx_assert_staging || return 1
# Edit .env so the line reads exactly: FITX_WORKOUT_SESSIONS_ENABLED=1
docker compose up -d web worker
```

Immediately verify, before exercising anything:

- shallow `/health` is `200`;
- the application booted with no startup error;
- `/health?deep=1` → `flags` shows the flag on and the revision unchanged;
- the `[FLAGS] enabled=…` boot line lists it;
- no `IntegrityError` on `WorkoutSession` in the boot logs.

### 8.1 Surfaces that appear on activation

| Transport | Routes | Gate |
|---|---|---|
| browser (web, cookie auth + CSRF) | `POST /workout/session/start`, `GET /workout/session/current`, `POST /workout/session/<public_id>/resume`, `POST /workout/session/<public_id>/checkpoint`, `POST /workout/session/<public_id>/abandon` | `FITX_WORKOUT_SESSIONS_ENABLED` |
| native server transport (`/api/v1`) | `POST /api/v1/training/workout-sessions`, `GET …/current`, `POST …/<ref>/resume`, `PUT …/<ref>/checkpoint`, `POST …/<ref>/abandon`, `POST …/<ref>/complete` | `FITX_WORKOUT_SESSIONS_ENABLED` **and** `MOBILE_AUTH_ENABLED` |
| shared completion | `POST /workout/complete` — declares `expected_checkpoint_revision` when a session id is supplied | always registered; revision only at `contract_version=2` |

"Native" in Sprint 14 means the **`/api/v1` server transport**, exercised with
authenticated API tooling. It does **not** mean Flutter. No Flutter client is
wired to these routes, and none is to be wired here.

The state resolver emits `contract_version=2` only while the flag is on;
`contract_version=1` is the exact pre-activation contract.

### 8.2 Required cases

Each case is one row of the evidence record (§14).

| # | Case | Transport |
|---|---|---|
| A | fresh start | browser |
| B | checkpoint | browser |
| C | reload / resume, progress restored | browser |
| D | completion | browser |
| E | start | native `/api/v1` |
| F | checkpoint | native `/api/v1` |
| G | completion | native `/api/v1` |
| H | browser start → native checkpoint | cross-transport |
| I | native start → browser checkpoint | cross-transport |
| J | browser start → native complete | cross-transport |
| K | native start → browser complete | cross-transport |
| L | explicit abandon | either |
| M | revision conflict | either |
| N | stale / plan-drift refusal | either |

The purpose is **not** to re-prove PR4's concurrency work — that is proven on
real PostgreSQL in CI. The purpose is to show that the *operationally deployed
build* behaves according to the contracts already proven.

**Cross-transport (H–K) requires at least one real handoff in each direction.**
Confirm the same session authority, one physical row, no duplicate ACTIVE
session for the owner, revision continuity, and no stale overwrite. Do not
fabricate a `workout_ref`.

**Revision conflict (M)** — generate one *controlled* stale command; no
concurrency tricks:

```text
1. read the session's current checkpoint_revision  -> R0
2. send a valid checkpoint declaring If-Match: R0  -> accepted, revision is R1
3. send another checkpoint still declaring If-Match: R0
   expect: typed revision refusal, no column mutated, no state regression,
           WorkoutSessionLifecycle Event=revision_conflict +1 (exactly once)
```

A malformed payload is **not** a substitute: it exercises validation, not the
revision gate.

**Stale / plan-drift (N)** — start a scheduled session on a test account, then
regenerate or change that account's canonical plan, then attempt progress
against the old session. Expect a fail-closed refusal, no revision movement, and
no silent rebinding. Use an isolated test account only. If the environment
cannot support this safely, say so explicitly and rely on the committed
deterministic tests — but classify it `NOT EXERCISED`, never `VERIFIED LIVE`.

---

## 9. Expected outcomes — the actual response vocabulary

These are the codes the shipped build emits. They are not written from memory;
they are read from `app/blueprints/training.py`,
`app/blueprints/mobile_workout_sessions.py` and
`app/services/workout_session/errors.py`. Verify them against the running build.

### 9.1 Lifecycle outcomes and their HTTP status

| Outcome | Browser HTTP |
|---|---|
| `created` | 201 |
| `existing_active`, `resumed`, `checkpointed`, `abandoned`, `completed`, `already_completed`, `already_abandoned` | 200 |
| `stale_session_requires_resolution`, `conflict`, `invalid_transition` | 409 |
| `not_found` | 404 |

### 9.2 Typed refusals

| Condition | Browser code / HTTP | Native code / HTTP |
|---|---|---|
| session absent | `not_found` / 404 | `TRAINING_SESSION_NOT_FOUND` / 404 |
| no base revision declared | `revision_required` / **428** | `TRAINING_SESSION_INVALID_REVISION` / 428 |
| declared revision is stale | `revision_conflict` / 409 | `TRAINING_SESSION_REVISION_CONFLICT` / 409 |
| malformed idempotency key | `idempotency_key_invalid` / 400 | `TRAINING_SESSION_INVALID_IDEMPOTENCY_KEY` / 400 |
| same key, different snapshot | `idempotency_conflict` / 409 | `TRAINING_SESSION_IDEMPOTENCY_CONFLICT` / 409 |
| snapshot invalid or over bounds | `invalid_checkpoint` / 400 | `TRAINING_SESSION_INVALID_REQUEST` / 400 |
| session already terminal | `session_terminal` / 409 | `TRAINING_SESSION_TERMINAL` / 409 |
| session stale vs. current plan | `stale_session_requires_resolution` / 409 | `TRAINING_SESSION_STALE` / 409 |
| backend failure | `session_unavailable` / 503 | `TRAINING_SESSION_UNAVAILABLE` / 503 |

Both transports also publish:

- **`Session-Resolution: retry | reread | terminal`** — what the client should
  *do*, independent of the status code.
- **`Idempotency-Replayed: true | false`** on checkpoint — a replay is not a
  transition and must not advance the revision.

The browser checkpoint requires `If-Match` (the last read `checkpoint_revision`)
and `Idempotency-Key`, with a bounded **full** snapshot body
`{"checkpoint": {...}}` — never a patch. Both headers are required; there is no
fallback channel.

### 9.3 Observability expectation table

| Action | Lifecycle `Event` | HTTP | Resulting state |
|---|---|---|---|
| start | `started` | 201 | ACTIVE, `checkpoint_revision=0` |
| start again, same workout | *none* (replay) | 200 `existing_active` | unchanged |
| checkpoint | `checkpointed` | 200 | ACTIVE, revision +1 |
| checkpoint replay (same key + same snapshot) | *none* | 200, `Idempotency-Replayed: true` | unchanged |
| explicit resume | `resumed` | 200 | ACTIVE, revision unchanged |
| checkpoint declaring a stale revision | `revision_conflict` | 409 `revision_conflict` | **unchanged** |
| completion declaring a stale revision | `revision_conflict` | typed refusal | **unchanged**, zero artifacts |
| abandon | `abandoned` | 200 | ABANDONED |
| complete | `completed` | 200 | COMPLETED |

Counting rules, as implemented: the five success events count only the
corresponding successful canonical outcome **after its durable commit**;
idempotent replays and reconciliation against an already-existing completion do
not increment. `revision_conflict` counts **one refused logical command** —
checkpoint comparison, completion preflight and the authoritative locked
completion check never double-count one request.

Metric emission is best-effort by design: if `runtime_metrics` is disabled or
raises, execution results are unchanged. That is why §7.2 verifies the metric
path independently, and why a missing metric *after* a verified path is an
**abort** signal (§10.3) rather than a metric bug.

### 9.4 Metric cardinality

Confirm during the exercise that `WorkoutSessionLifecycle` carries the single
dimension **`Event`**, with values drawn only from:

```text
started  resumed  checkpointed  abandoned  completed  revision_conflict
```

No user id, session reference, workout identity, route, replay key or plan
identity appears in a datum. If any other dimension or value appears, treat it
as an abort signal — a cardinality leak is both a cost defect and a privacy
defect.

### 9.5 Evidence hygiene

`WorkoutSessionLifecycle` contains no identity. **The log lines do**: both
`[WORKOUT_SESSION] rid=… event=… user_id=…` and
`[WORKOUT_STATE] anomaly rid=… user_id=… category=… detail=…` carry `user_id`.
Redact it from anything committed to this repository. Prefer aggregate counts to
raw log excerpts. Never commit session public IDs, cookies, tokens or
credentials.

---

## 10. Success and abort signals

### 10.1 Must-pass — all of these, or the verdict is NO-GO

1. No unexpected `IntegrityError` on `WorkoutSession` (in particular no
   `is_active_session_owner_violation`).
2. No `[WORKOUT_STATE] anomaly … category=completion_marker_mismatch`.
3. No session left ACTIVE after a successful completion.
4. Revision transitions are coherent: monotonic, +1 per accepted checkpoint,
   never regressing, and never advanced by a replay.
5. Cross-transport continuity is coherent: one physical row, one authority, no
   duplicate ACTIVE session for one owner.
6. `WorkoutSessionLifecycle` is emitted, with the expected bounded event counts
   from §9.3 and the cardinality of §9.4.
7. No `revision_conflict` on a happy path — conflicts appear only in case M.
8. `contract_version=2` is accepted by every consumer actually exercised.
9. No increase in unrelated training-blueprint 5xx during the exercise window
   (`HttpServerErrors`, `Blueprint=training`).
10. Rollback (§11) and post-rollback inertness (§12) both pass.

### 10.2 Informational — record, do not gate on

- absolute latency in a low-traffic environment;
- `HttpThrottled` counts — *unless* a throttle fires on a normal single-user
  lifecycle, which is a must-pass failure, not an informational one;
- rollback wall-clock duration (§11.1);
- `worker` status in deep health — worker liveness is not deploy-gating.

### 10.3 Abort immediately on any of these

- any unexpected `WorkoutSession` `IntegrityError`;
- a duplicate ACTIVE session for one owner;
- a session stuck ACTIVE after completion;
- a revision regression;
- an **accepted** stale completion;
- missing lifecycle metrics **after** §7.2 verified the metric path;
- an unhandled 5xx caused by the session path;
- `contract_version=2` rejected by an exercised supported client;
- any cross-user ownership anomaly;
- duplicated completion artifacts (`PumpCheck`, `WorkoutLog`, completion marker,
  XP, quest or activity row);
- an unexpected schema or runtime error.

**On any abort signal: disable the flag immediately (§11), then investigate.**
Do not continue the matrix to collect more evidence — a correctness failure is
already the finding, and further mutation only enlarges the blast radius.

If the abort was a correctness failure, stop and report
`PR5 BLOCKED — CORRECTNESS REGRESSION DISCOVERED`, capturing: build SHA,
environment, the exact action, expected result, actual result, safe log/metric
evidence, and the rollback result. **Do not repair runtime code inside PR5** — a
separate bounded remediation slice and an independent review are required.

---

## 11. Rollback

If you arrived directly at this section during an incident, read §2 and §2.1
first — they define `fitx_assert_staging` and the directory these commands run
in. Both take under a minute and neither mutates anything.

The canonical rollback is one flag value and one restart:

```bash
fitx_assert_staging || return 1   # defined in §2; run from the directory in §2.1
# Edit .env so the line reads exactly: FITX_WORKOUT_SESSIONS_ENABLED=0
docker compose up -d web worker
curl -fsS 'http://127.0.0.1:5000/health' -o /dev/null -w '%{http_code}\n'
```

**Do not**, as part of rollback:

- roll back a migration (both are expand-only, by design);
- delete `WorkoutSession` rows;
- clean up checkpoint data;
- manually repair a completion.

Persisted rows stay safe because the `contract_version=1` read contract ignores
them and never deletes them. Verify that property in §12 rather than assuming
it.

Record: time · previous value · new value · build revision · health result.

### 11.1 Operator complexity

| Question | Answer |
|---|---|
| operator steps | two — edit one `.env` line, then `docker compose up -d` |
| restart or redeploy required? | service restart only; **no redeploy, no merge, no release cycle** |
| reversible? | yes — the same two steps in the other direction |
| DB repair required? | **no** |
| migration downgrade required? | **no** |

If you measure the wall-clock duration, record it as *evidence*, not as an SLA.
Do not publish "rollback under N minutes" as a guarantee derived from a single
measurement in a low-traffic environment.

---

## 12. Post-rollback inertness — mandatory closure evidence

This verifies criterion **S14-10** operationally rather than only structurally.
After the rollback in §11, confirm every one of these:

| # | Check | Expected |
|---|---|---|
| 1 | every browser session route | **404**, body `{"error":"not_found","code":"not_found"}` |
| 2 | every native `/api/v1` session route | **404**, `TRAINING_SESSION_NOT_FOUND` |
| 3 | a dark route hammered past its rate-limit bucket | still **404**, never 429 — the flag gate sits outside the throttle, so a switched-off surface must be indistinguishable from one that does not exist |
| 4 | state resolver | `contract_version=1` |
| 5 | any public response | contains **no** session state; no `WorkoutSession` row is created or read into it |
| 6 | legacy `POST /workout/complete` | behaviourally unchanged and requires **no** revision |
| 7 | the browser | issues no session request and starts no checkpoint timer |
| 8 | persisted staging session rows | **still present, and ignored** — not deleted |
| 9 | `/training` | normalized legacy behaviour unchanged |

Check 8 is the one most easily skipped and the most important: rollback safety
is the claim that data survives untouched, so confirm the rows are still there.

**If any inertness check fails, that is a P1 readiness blocker.** Report
`PR5 BLOCKED — ROLLBACK INVARIANT FAILED`. Do not declare Sprint 14 closed.

### 12.1 Restore the environment's documented baseline

Return `RUNTIME_METRICS_ENABLED` to whatever the environment's owner keeps as
its documented baseline. If it was OFF before §7.1 and the exercise turned it
on, restore it to OFF unless the environment owner explicitly chooses otherwise.
Record the final value of both flags.

**No production flag value changes at any point in this runbook.**

### 12.2 What an operator should expect after rollback

- the durable session routes are unavailable again;
- legacy browser workout behaviour is restored — including that a workout in
  progress is **ephemeral** and does not survive a reload;
- persisted staging rows are retained but inert;
- no destructive data cleanup happens, and none is needed;
- **cross-device continuity is not available while the feature is off.** Do not
  promise it.

---

## 13. GO / NO-GO checklist

Deterministic. Every row must be `PASS` for **GO**. Any `FAIL` or `BLOCKED`
makes the verdict **NO-GO** — there is no partial GO.

| # | Requirement | Verification type | Status at 2026-09-09 |
|---|---|---|---|
| 1 | correct reviewed build deployed | live | **PASS** — `c3f579a259d13bace018f0296de030aedeb191d7` |
| 2 | DB at `a994f9bed783` + `f5a6b7c8d9e0`, single head | live | **PASS** — current `f5a6b7c8d9e0`, one head |
| 3 | `RUNTIME_METRICS` functioning (path proven, §7.2) | live | **PASS** — process `RUNTIME_METRICS_ENABLED=1`, `ThreadReserve` flushing into `AxisAI/Staging/Runtime` |
| 4 | baseline captured before activation | live | **PASS** — §14.1 |
| 5 | browser lifecycle A–D passed | live | **PASS** |
| 6 | native `/api/v1` lifecycle E–G passed | live | **PASS** |
| 7 | cross-transport H–K passed, both directions | live | **PASS** |
| 8 | abandon (L) passed | live | **PASS** |
| 9 | revision conflict (M) behaved correctly | live | **PASS** |
| 10 | stale path (N) passed or limitation adjudicated | live | **PASS** — plan-drift 409 `stale_session_requires_resolution` |
| 11 | lifecycle metric coherent + cardinality clean | live | **PASS** — `Event` only |
| 12 | no abort signal observed | live | **PASS** |
| 13 | rollback demonstrated | live | **PASS** — flag OFF, restart, health green |
| 14 | flag-OFF inertness reverified after rollback | live | **PASS** |
| 15 | all CI green on the exercised SHA | CI | see the readiness record |
| 16 | no unresolved P0 / P1 | review | see the readiness record |
| 17 | no production activation included | review | **PASS** — none is encoded here |

**Why rows 1–14 read `PASS` on 2026-09-09.** They were `BLOCKED` earlier the
same day because the exercise had not been run (and, before #290, because no
staging environment existed). Those earlier states are retained in §1. They
are `PASS` now because the matrix, metric path, rollback and inertness were
observed live against staging — see §14.1. An unexercised row is still not a
passing row; these rows are no longer unexercised.

**Verdict at 2026-09-09: GO.** Technical staging-readiness for a future
operator decision. This is **not** production activation.

Reaching GO would mean: *all technical prerequisites for a future operator
decision are documented and staging-proven.* It would **not** mean production
activation is approved or performed — **ready for activation ≠ activated**.
See §0.

---

## 14. Evidence record template

One record per exercise. Fill it from observation, never from expectation, and
write the verdict **last** — after the evidence, never before it.

Use exactly these evidence classes, and do not collapse them into "verified":

```text
VERIFIED LIVE IN STAGING             observed in the authorized non-production environment
VERIFIED BY CI                       proven by a required check on an exact SHA
VERIFIED BY STATIC/STRUCTURAL TEST   proven by a committed test, not by a live run
NOT EXERCISED                        not attempted, or not supportable in the environment
BLOCKED                              attempted or required, but prevented
```

```text
date / time (UTC)          :
environment                :
non-production proof       :
tested build SHA           :
BUILD_REVISION observed    :
migration head             :
test account classification: dedicated staging test account(s) — no identity recorded
initial FITX_WORKOUT_SESSIONS_ENABLED :
initial RUNTIME_METRICS_ENABLED       :
initial MOBILE_AUTH_ENABLED           :
baseline summary           :

case A  browser start                   : result / evidence class / Event observed / state
case B  browser checkpoint              :
case C  browser reload-resume           :
case D  browser completion              :
case E  native start                    :
case F  native checkpoint               :
case G  native completion               :
case H  browser start -> native ckpt    :
case I  native start -> browser ckpt    :
case J  browser start -> native complete:
case K  native start -> browser complete:
case L  abandon                         :
case M  revision conflict               :
case N  stale / plan drift              :

lifecycle metric summary   : counts per Event; cardinality confirmed y/n
anomaly / log summary      : redacted, aggregate
abort signals observed     : none | <list>
rollback result            :
post-rollback inertness    : checks 1-9
final FITX_WORKOUT_SESSIONS_ENABLED :
final RUNTIME_METRICS_ENABLED       :
limitations                :
operator                   :
overall verdict            : GO | NO-GO
```

### 14.1 Exercise of 2026-09-09 (UTC)

Filled from observation. No cookies, JWTs, passwords, session public ids or
user ids.

```text
date / time (UTC)          : 2026-09-09T09:04:20Z baseline;
                             activation ~09:09Z; rollback 09:45:03Z–09:45:13Z
environment                : AxisAI-staging (account 852128326881,
                             eu-central-1, instance i-086fdd5d201cbf1a5)
non-production proof       : ENVIRONMENT=staging; host INSTANCE_ID == IMDS id;
                             DATABASE_URL @db:5432/axisai_staging (not RDS);
                             SG axisai-staging-sg zero inbound; production
                             instance i-0c6f5352fc214e68d remained distinct
                             and was not mutated
tested build SHA           : c3f579a259d13bace018f0296de030aedeb191d7
BUILD_REVISION observed    : c3f579a259d13bace018f0296de030aedeb191d7
migration head             : f5a6b7c8d9e0 (single head; unique active-owner
                             index present)
test account classification: dedicated staging synthetic Cognito accounts
                             in pool eu-central-1_KH1YUFTCK (.invalid emails)
initial FITX_WORKOUT_SESSIONS_ENABLED : 0 / process false
initial RUNTIME_METRICS_ENABLED       : 1 / process 1
initial MOBILE_AUTH_ENABLED           : 1 / process true
baseline summary           : WorkoutSession rows=0; WorkoutSessionLifecycle
                             datapoints empty; HttpServerErrors/Overload/
                             Throttled empty; ThreadReserve Average=8.0
                             flushing; WORKOUT_STATE anomalies=0;
                             WorkoutSession IntegrityError=0

case A  browser start                   : 201 created, ACTIVE, revision 0 / VERIFIED LIVE IN STAGING / Event=started
case B  browser checkpoint              : 200 checkpointed, revision 0→1 / VERIFIED LIVE IN STAGING / Event=checkpointed
case C  browser reload-resume           : 200 existing_active, revision 1, elapsed 120 restored, same session / VERIFIED LIVE IN STAGING
case D  browser completion              : 200 session_completed=true, XP awarded / VERIFIED LIVE IN STAGING / Event=completed
case E  native start                    : 201 ACTIVE revision 0 / VERIFIED LIVE IN STAGING / Event=started
case F  native checkpoint               : 200 revision 0→1 / VERIFIED LIVE IN STAGING / Event=checkpointed
case G  native completion               : 200 completion+session / VERIFIED LIVE IN STAGING / Event=completed
case H  browser start -> native ckpt    : 201 then 200, same session_ref, revision 1 / VERIFIED LIVE IN STAGING
case I  native start -> browser ckpt    : 201 then 200 checkpointed revision 1 / VERIFIED LIVE IN STAGING
case J  browser start -> native complete: 200 after H / VERIFIED LIVE IN STAGING / Event=completed
case K  native start -> browser complete: 200 session_completed=true / VERIFIED LIVE IN STAGING / Event=completed
case L  abandon                         : 200 abandoned / VERIFIED LIVE IN STAGING / Event=abandoned
case M  revision conflict               : valid ckpt R0→R1; stale If-Match R0 → 409 revision_conflict, Session-Resolution=reread, state stayed R1 / VERIFIED LIVE IN STAGING / Event=revision_conflict (1)
case N  stale / plan drift              : after canonical plan replacement, checkpoint → 409 stale_session_requires_resolution, revision unchanged 0 / VERIFIED LIVE IN STAGING

lifecycle metric summary   : AxisAI/Staging/Runtime WorkoutSessionLifecycle
                             Event only (started, checkpointed, completed,
                             abandoned, revision_conflict). Counts: started 6,
                             checkpointed 5, completed 4, abandoned 1,
                             revision_conflict 1, resumed 0. Cardinality
                             confirmed: no other dimension.
anomaly / log summary      : IntegrityError on WorkoutSession = 0;
                             [WORKOUT_STATE] anomaly = 0;
                             completion_marker_mismatch = 0;
                             HttpServerErrors / HttpOverload / HttpThrottled
                             empty in the exercise window
abort signals observed     : none
rollback result            : FITX_WORKOUT_SESSIONS_ENABLED=0, process false,
                             FLAGS line MOBILE_AUTH_ENABLED only, health ok,
                             revision unchanged, ~10 s restart
post-rollback inertness    : browser start/current 404 {"code":"not_found"};
                             native start/current 404 TRAINING_SESSION_NOT_FOUND;
                             8× dark start still 404 never 429;
                             /training/bootstrap contract_version=1 and no
                             session in public state;
                             WorkoutSession rows still stored (6: 4 completed,
                             1 abandoned, 1 active leftover from the N
                             fail-closed session)
final FITX_WORKOUT_SESSIONS_ENABLED : 0
final RUNTIME_METRICS_ENABLED       : 1 (retained; staging observability baseline)
limitations                : evidence is for SHA c3f579a, not later main
                             0e2f604 (#293 plan-replacement, classified as
                             non-WorkoutSession and not mixed into this GO);
                             previous-day ACTIVE (P2-7) NOT EXERCISED;
                             flag-ON 429 throttle (P2-4) not forced — no
                             spurious HttpThrottled on the single-user matrix;
                             Flutter NOT EXERCISED
operator                   : continuation after session-manager-plugin recovery
overall verdict            : GO
```

---

## 15. Known limitations to expect at activation

These are accepted, recorded debt — not defects discovered here. They are listed
so an operator is not surprised mid-exercise, and so the exercise deliberately
observes them. Full adjudication is in the readiness record.

- **An unscheduled (ad-hoc) workout gets no durable checkpoint.** Its session is
  startable and resumable, but a durable checkpoint is refused as stale, because
  there is no canonical planned workout to validate membership against. It fails
  closed. The user's experience of an ad-hoc workout is unchanged from today.
- **A previous-day ACTIVE session blocks today's start with a `conflict`.** The
  read model marks it non-resumable, and the at-most-one-ACTIVE-session index
  means today's start is refused until it is resolved. The UI exposes an
  "Abandon workout" affordance for exactly this. Include a previous-day case in
  the exercise if the environment allows it.
- **No committed test proves the checkpoint throttle fires with the flag ON.**
  The throttle is declaratively wired on both transports; only the flag-OFF
  behaviour (404, never 429) is proven by test. Observe `HttpThrottled` during
  the exercise.
- **Flutter/native client acceptance is `NOT EXERCISED` and out of scope.** No
  Flutter build consumes these routes. Do not fabricate native-client evidence.

---

## 16. Runbook usability check

Before trusting this document in an incident, an on-call engineer with no prior
context should be able to answer all eleven:

1. Can I identify the environment? — §2
2. Can I verify the build? — §4
3. Can I verify the migrations? — §5
4. Can I capture a baseline? — §7.3
5. Do I know the exact activation order? — §7
6. Do I know what success looks like? — §9, §10.1
7. Do I know when to abort? — §10.3
8. Can I roll back without guessing? — §11
9. Can I prove the rollback worked? — §12
10. Can I avoid touching production accidentally? — §0, §2
11. Do I know which machine and directory to run this in? — §2.1

If any answer becomes "no" as the code changes, this runbook is stale and must
be corrected before the next activation attempt.
