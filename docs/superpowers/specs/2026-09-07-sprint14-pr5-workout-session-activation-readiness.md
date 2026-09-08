# Sprint 14 PR5 — Workout session activation readiness and Sprint 14 closure state

Assessment date: 2026-09-07.
Assessor: bounded release-readiness review of `origin/main` at
`b77a1dc02c4ed050caa50c17f592d0826db084de`.

This document does **not** activate a flag, deploy, approve a production
deployment, distribute a mobile build, or start Sprint 15.

Operator procedure: [`docs/WORKOUT_SESSION_ACTIVATION.md`](../../WORKOUT_SESSION_ACTIVATION.md).
Sprint objective, criteria and PR sequence:
[`2026-09-02-sprint14-pr1-product-engineering-discovery.md`](2026-09-02-sprint14-pr1-product-engineering-discovery.md).

---

## 1. Verdict

```text
PR5 BLOCKED — STAGING EVIDENCE REQUIRED
```

**Sprint 14's execution-correctness work is complete and proven. Its
staged-activation readiness is not, and cannot be completed from this
repository, because no staging environment exists.**

Every artifact PR5 owns that can be produced without a staging environment has
been produced: the canonical activation runbook, the exercise matrix, the
success/abort contract, the rollback and post-rollback inertness procedure, the
go/no-go checklist, the evidence-record template, and the P2 activation-risk
adjudication. What is missing is not a document. It is an environment in which
to execute the one thing the flag's own record names as a prerequisite:

> "staging exercise of the full lifecycle including the abandoned/stale paths"
> — `app/feature_flags.py`, `FITX_WORKOUT_SESSIONS_ENABLED.prerequisites`

That prerequisite is unmet, was never met, and is not weakened here.

**Sprint 14 is therefore NOT closed.** The formal criteria S14-1…S14-11 are
satisfied (§6). The sprint's exit condition — evidence-based activation
readiness — is not.

---

## 2. Baseline

| Field | Value |
|---|---|
| Repository | `yusufbesirarslan/fitness-coach` |
| Brief's expected base | `b77a1dc02c4ed050caa50c17f592d0826db084de` |
| **Actual `origin/main` at PR5 start** | `b77a1dc02c4ed050caa50c17f592d0826db084de` — identical, no drift |
| Subject | `fix(training): harden workout execution reliability (#289)` |
| Branch | `sprint14-pr5-workout-session-readiness` |
| Worktree | `.worktrees/sprint14-pr5-workout-session-readiness` |
| Working tree at start | clean |
| Alembic heads | **1** — `f5a6b7c8d9e0`, across 40 revisions |
| Prerequisite migrations present | `a994f9bed783` ✓, `f5a6b7c8d9e0` ✓ |

### Post-PR4 main CI — run `34143786646`

All five required checks green on `b77a1dc`:

```text
✓ pytest                                     10m10s
✓ PostgreSQL concurrency                      1m32s
✓ schema-drift guard                            51s
✓ authoritative Linux production locks          30s
✓ authoritative image revision immutability     39s
```

No re-baselining was required: main had not advanced, so no commit needed
classification against the `workout_session` / `runtime_metrics` /
`FITX_WORKOUT_SESSIONS_ENABLED` / deployment-semantics trigger list.

---

## 3. Staging access discovery — the blocking finding

The discovery was run against the repository's own configuration and against the
live cloud account, read-only. It is the load-bearing evidence for §1.

| Question | Answer | Evidence |
|---|---|---|
| Is a staging hostname defined anywhere? | **No** | no `staging` token in `.github/workflows/`, `docker-compose.yml`, `.env.example`, `nginx.conf`, `deploy/`, `infra/` or `scripts/` |
| Is a staging deployment mechanism defined? | **No** | `.github/workflows/deploy.yml` has exactly one job with an `environment:` — `production` |
| How many GitHub environments exist? | **One** | `production` (plus its `superb-vitality / production` deployment-branch entry) |
| How many deployment targets does the contract allow? | **One** | `docs/DEPLOYMENT.md` §1: "a successful CI run for `main` may deploy one immutable revision to **the one configured EC2 instance**" |
| How many EC2 instances exist in the account? | **One** | `AxisAI-server`, `i-0c6f5352fc214e68d`, running (read-only `describe-instances`, `eu-central-1`) |
| How many RDS instances exist? | **One** | `database-1`, available (read-only `describe-db-instances`) |
| Is a separate staging AWS account documented? | **No** | single account `852128326881`; no second account referenced in any document |
| Does any authoritative doc define localhost as the staging target? | **No** | `docs/ROLLOUT.md` says "Staging first" for flag 6 but never defines what *staging* is |

`docs/ROLLOUT.md` is titled **"Production rollout runbook"**. It names staging as
a precondition for flags 6, 7 and 8 and, across the whole repository, never says
where staging is. That gap is the finding.

### 3.1 What was deliberately not done

- **Production was not used, and was not mutated in any way.** No flag was
  changed, no `.env` was edited, no deploy was approved, no `docker compose`
  command was issued against the host, and no production user was touched. The
  only cloud calls made were three read-only describes
  (`sts:GetCallerIdentity`, `ec2:DescribeInstances`, `rds:DescribeDBInstances`).
- **Localhost was not substituted.** A developer workstation is not the deployed
  artifact, exercises neither the deployment nor the migration path, and is not
  defined by any repository document as the staging target. Running the matrix
  there and recording it as "staging evidence" would be a false claim; per the
  evidence-honesty rule in §7 it could at best be `VERIFIED BY
  STATIC/STRUCTURAL TEST`, which the committed suites already provide.
- **No staging environment was created.** Provisioning one is infrastructure
  work outside PR5's readiness-only scope, and would itself require an
  authorization this session does not hold.

---

## 4. What PR5 delivered

| Path | Classification |
|---|---|
| `docs/WORKOUT_SESSION_ACTIVATION.md` | readiness / operator runbook (new) |
| `docs/superpowers/specs/2026-09-07-…-activation-readiness.md` | Sprint 14 readiness + closure record (this file, new) |
| `docs/ROLLOUT.md` | rollout runbook — flag 6 row corrected and linked |
| `docs/FEATURE_FLAGS.md` | feature-flag documentation — flag 6 linked |
| `docs/OBSERVABILITY.md` | observability documentation — lifecycle section linked |
| `docs/superpowers/specs/2026-09-02-…-discovery.md` | sprint spec — PR5 evidence section appended |
| `tests/test_sprint14_activation_readiness.py` | readiness structural test (new) |

**Zero runtime application files changed.** No schema, no migration, no feature
flag value, no default, no route, no service, no client asset, no workflow, no
deployment mechanic, and no Flutter.

### 4.1 Why `app/feature_flags.py` was deliberately left unchanged

Two candidate edits were considered and both rejected on evidence:

1. **Lifecycle transition.** The registry's vocabulary is
   `shipped_dark | staging_only | blocked | rolling_out`. No value represents
   "readiness complete, awaiting an operator decision"; `rolling_out` means
   "activation in progress", which would be false and would imply production
   enablement. Per the brief's §37 rule, a new enum must **not** be minted for
   PR5, so the lifecycle stays **`staging_only`** — which is, in any case, still
   exactly true.
2. **Recording a completed staging exercise.** There was none. The prerequisite
   "staging exercise of the full lifecycle including the abandoned/stale paths"
   remains unmet and is preserved verbatim. Recording readiness evidence that
   does not exist is the specific failure mode this sprint is guarding against.

The runbook pointer therefore lives in the documentation layer
(`FEATURE_FLAGS.md`, `ROLLOUT.md`), not in the registry.

---

## 5. P2 activation-risk adjudication

Required ruling, not repair. **No P2 is fixed in PR5.** Each is re-examined
specifically as an *activation* risk at `b77a1dc`, with the evidence that
supports the ruling.

| ID | Debt | Ruling | Evidence |
|---|---|---|---|
| **P2-1** | Unscheduled sessions are resumable in the classifier but a durable checkpoint is refused. | **OPEN — ACCEPTABLE FOR ACTIVATION** | Fails closed by design: with no canonical planned workout there is nothing to validate membership against, so the refusal is the correct outcome, and an ad-hoc workout behaves exactly as it does today (ephemeral). Documented in the runbook §15 so an operator is not surprised. |
| **P2-2** | Native `/api/v1` could not checkpoint a browser-started session lacking `workout_ref`. | **CLOSED** | PR4 derives membership from the row's server-owned weekday slot and versioned plan fingerprint; cross-transport request tests prove web-start → native-checkpoint/complete on the same physical row. |
| **P2-3** | The AST single-authority guard has trivial bypasses. | **OPEN — ACCEPTABLE FOR ACTIVATION** | A test-integrity weakness, not a runtime one. It cannot change the behaviour of a deployed build, so it cannot be an activation risk; it is a future-regression-detection risk. |
| **P2-4** | No committed test proves the checkpoint throttle activates with the flag ON. | **OPEN — ACCEPTABLE FOR ACTIVATION, WITH AN OBSERVATION OBLIGATION** | The throttle is declaratively wired on both transports (`app/blueprints/training.py:730`, `app/blueprints/mobile_workout_sessions.py:169`). Only the flag-OFF property is proven by test (`test_a_dark_checkpoint_answers_absent_rather_than_throttled` — 404 past the bucket, never 429). A mis-wired throttle would be over-permissive on an authenticated, owner-scoped, bounded-payload write — an availability risk, not a correctness or data-loss one. The runbook requires `HttpThrottled` to be observed during the exercise (§10.2), which is the honest closure path. |
| **P2-5** | The snapshot byte-cap rejection test is vacuous / the cap is structurally unreachable under legal bounds. | **OPEN — ACCEPTABLE FOR ACTIVATION** | `MAX_SNAPSHOT_BYTES = 65_536` sits above what the tighter per-field bounds admit (`MAX_EXERCISES = 32` × `MAX_SETS_PER_EXERCISE = 20`), so the structural bounds bind first. The byte cap is defence in depth that the shape bounds already enforce; an unreachable outer guard is not an activation hazard. |
| **P2-6** | Pre-existing PostgreSQL CI-selection gap: `tests/test_workout_session_pg.py` and `tests/test_workout_completion_pg.py` are not in the `pg_concurrency` job's explicit file list. | **OPEN — ACCEPTABLE FOR ACTIVATION** | Confirmed still true at `b77a1dc`: both files exist in `tests/`, neither appears in `.github/workflows/ci.yml`'s explicit list. It does not leave S14-8 unproven — PR4's `tests/test_sprint14_workout_execution_reliability_pg.py` **is** explicitly selected and covers competing same-base checkpoints, duplicate command delivery, both forced completion/checkpoint orderings, concurrent starts, and cross-transport physical-row convergence on real PostgreSQL. The gap is coverage *selection*, not an unproven invariant. |
| **P2-7** | Previous-day ACTIVE sessions can accept checkpoints although the read model reports them non-resumable. | **OPEN — ACCEPTABLE FOR ACTIVATION, AND THE MOST LIKELY DAY-ONE SURPRISE** | `record_checkpoint` calls `reject_terminal(row)` but consults no day rule, while `resumable` is computed only in the read model. The operator-visible consequence is the *other* half: `start_session` returns `conflict` for today while a previous-day ACTIVE row exists (the at-most-one-ACTIVE-owner index), so the user cannot start today's workout until it is resolved. It is recoverable, not stranding — `static/training.js` renders an "Abandon workout" affordance on the blocked state — and it is a typed 409 with a `Session-Resolution` classification, not data loss. The runbook requires a previous-day case in the exercise where the environment allows one; if that exercise shows users actually stranded, this escalates and blocks activation. |
| **P2-8** | A retired idempotency key can later be reused as a new logical command with a fresh base revision. | **OPEN — ACCEPTABLE FOR ACTIVATION** | Only the last accepted key is retained, by design; an older key cannot silently mutate because its base revision is stale by construction and the revision check rejects it first (`_replay_or_conflict`). Reuse with a *current* base revision performs exactly the mutation the client asked for at that revision, so no committed progress is lost. It requires a misbehaving client, and the browser mints a fresh key per command. |

**PR4 review-only notes** remain historical and are not reopened:
`PR4-P2-01` accepted, `PR4-P2-02` accepted.

**Ruling: no P2 blocks activation.** None was fixed, and none was silently
downgraded. The two that most deserve live observation — **P2-4** and **P2-7** —
are written into the runbook's exercise obligations rather than argued away.

---

## 6. Sprint 14 criteria status

The mapping below is authoritative and must not be swapped:

- **S14-7 = the `WorkoutSessionLifecycle` metric** (lifecycle observability)
- **S14-8 = the real PostgreSQL reliability/concurrency proof**

| ID | Status | Verification class |
|---|---|---|
| S14-1 | **SATISFIED** | VERIFIED BY CI |
| S14-2 | **SATISFIED** | VERIFIED BY CI |
| S14-3 | **SATISFIED** | VERIFIED BY CI |
| S14-4 | **SATISFIED** | VERIFIED BY CI |
| S14-5 | **SATISFIED** | VERIFIED BY CI |
| S14-6 | **SATISFIED** | VERIFIED BY CI |
| S14-7 — lifecycle observability | **SATISFIED** | VERIFIED BY CI (emission, cardinality and replay-awareness). **NOT EXERCISED** as a live CloudWatch datum — that needs §7.2 of the runbook. |
| S14-8 — PostgreSQL reliability proof | **SATISFIED** | VERIFIED BY CI on real PostgreSQL |
| S14-9 | **SATISFIED** | single Alembic head `f5a6b7c8d9e0`; PR5 adds **zero** migrations |
| S14-10 | **SATISFIED** | VERIFIED BY STATIC/STRUCTURAL TEST. **NOT EXERCISED** operationally — that is runbook §12. |
| S14-11 | **SATISFIED** | no production flag value changed by any PR in this sprint, PR5 included |

The distinction in S14-7 and S14-10 is the point of this document: both criteria
are met *as specified*, and both have an operational half that only a staging
exercise can supply.

---

## 7. Evidence honesty ledger

Applied throughout, never collapsed into "verified":

| Claim | Class |
|---|---|
| Revision-gated checkpoint, bounded snapshot, typed refusals | VERIFIED BY CI |
| Cross-transport stale-completion refusal, both directions | VERIFIED BY CI |
| PostgreSQL race correctness (competing checkpoints, forced orderings, concurrent starts) | VERIFIED BY CI |
| `WorkoutSessionLifecycle` emission, vocabulary and cardinality | VERIFIED BY CI |
| Flag-OFF inertness (404 not 429, `contract_version=1`, no session state published) | VERIFIED BY STATIC/STRUCTURAL TEST |
| Single Alembic head, both prerequisite migrations present | VERIFIED BY STATIC/STRUCTURAL TEST |
| Post-PR4 main CI green on `b77a1dc` | VERIFIED BY CI (run `34143786646`) |
| Runbook contract — real metric name, real event vocabulary, real routes, rollback documented, no production activation encoded | VERIFIED BY STATIC/STRUCTURAL TEST (`tests/test_sprint14_activation_readiness.py`) |
| No staging environment exists | VERIFIED — repository configuration + read-only cloud inventory (§3) |
| Live staging lifecycle exercise (cases A–N) | **BLOCKED** |
| Live baseline capture and metric-path proof | **BLOCKED** |
| Live rollback and post-rollback inertness | **BLOCKED** |
| Flutter / native-client acceptance of `contract_version=2` | **NOT EXERCISED** — out of scope, no Flutter build consumes these routes |
| Released-client compatibility beyond the exercised server transports | **NOT EXERCISED** — no shipping native client is wired |

---

## 8. Security and privacy check

Bounded to operational readiness; this is not a general security audit.

| Concern | Finding |
|---|---|
| Test accounts | The runbook mandates dedicated staging test accounts and forbids real user accounts. No exercise was run, so no account of any kind was used. |
| Owner-scoped behaviour | Unchanged. Every session query is `user_id`-scoped and re-derived from the authenticated principal; `advance_checkpoint`'s UPDATE predicate includes `user_id`, so a cross-user checkpoint matches zero rows by construction. |
| Cross-user existence leak | None observed; none possible to observe without an exercise. Listed as an abort signal. |
| Metric identity | `WorkoutSessionLifecycle` carries the single `Event` dimension from a six-value closed vocabulary. No identity. |
| Log identity | **Both** `[WORKOUT_SESSION]` and `[WORKOUT_STATE] anomaly` lines carry `user_id`. The runbook (§9.5) requires it redacted from any committed evidence. This is an evidence-handling rule, not a defect claim. |
| Secrets in committed material | None. The runbook uses `<STAGING_HOST>` / `<PRODUCTION_HOST>` placeholders, commits no hostname, credential, token, key or cookie, and no command defaults to production. |
| Rollback and user data | Rollback deletes nothing: no migration downgrade, no row deletion, no checkpoint cleanup, no manual completion repair. |
| Production | Untouched. Three read-only cloud describes; zero mutations. |

---

## 9. Sprint 14 closure state

Closure requires **all** of: PR1–PR4 shipped ✓, PR4 post-merge CI green ✓, PR4
integration provenance closed ✓, S14-1…S14-11 satisfied ✓, PR5 staging
prerequisites validated ✗, staging lifecycle exercised ✗, observability verified
live ✗, rollback exercised ✗, post-rollback inertness verified ✗, go/no-go = GO
✗, PR5 reviewed / CI-green / merged / post-merge-verified ✗.

```text
SPRINT 14 STATUS: OPEN — STAGING EVIDENCE REQUIRED
```

Upon (a) provisioning of an authorized non-production environment, (b) a
completed exercise recorded through the runbook's §14 template with a **GO**
verdict, and (c) PR5 merge plus green post-merge CI, Sprint 14 may be considered
closed as:

> **SPRINT 14 CLOSED — DURABLE WORKOUT EXECUTION READY FOR OPERATOR-CONTROLLED
> ACTIVATION**

where *ready for activation ≠ activated*. That wording is prepared here and is
**not** yet true. Sprint 14 is not closed by this document.

| PR | State |
|---|---|
| PR1 discovery | SHIPPED (`#278`) |
| PR2 canonical execution contract | SHIPPED (`#280`) |
| PR3 browser client convergence | SHIPPED (`#285`) |
| PR4 reliability + observability | SHIPPED (`#289`), post-merge verified |
| PR5 staged-activation readiness | **BLOCKED — STAGING EVIDENCE REQUIRED** |

Production flag state: `FITX_WORKOUT_SESSIONS_ENABLED` remains **OFF** unless
separately authorized later. PR5 changes no production state.

---

## 10. Handoff — what Sprint 14 does not include

Recorded explicitly so none of it is silently pulled into closure:

- Flutter `WorkoutSession` repository / composition wiring
  (`AppComposition.configured` still wires `UnavailableWorkoutSessionRepository`)
- `MOBILE_AUTH_ENABLED` / `AXISAI_NATIVE_AUTH_ENABLED` rollout
- authenticated native production smoke
- the contiguous 24-hour production/native soak
- Coach streaming restoration (discovery Candidate B, F-2)
- Today / Plan UX work beyond what shipped
- any Nutrition work

Sprint 15 is **not** started here.

### 10.2 Unrelated defect observed, deliberately not fixed

While validating relative links across the docs PR5 touches, one broken link was
found in `docs/ROLLOUT.md` — the flag-9 (`MOBILE_AUTH_ENABLED`) row points at
`../superpowers/specs/2026-08-26-sprint12-mobile-auth-today-production-rollout-readiness.md`,
which resolves one directory too high; the file is at
`docs/superpowers/specs/...`. It pre-dates PR5 (present verbatim at `b77a1dc`),
sits on a row PR5 does not touch, and concerns a different flag. It is recorded
here rather than repaired, because a readiness PR that silently edits unrelated
rollout rows is harder to audit than one that reports what it saw.

### 10.1 The one thing that unblocks PR5

A single dependency, stated plainly so it is not lost in the surrounding detail:

> **An authorized non-production environment running a build at or after
> `b77a1dc`, with the schema at `f5a6b7c8d9e0`, and an operator authorized to
> mutate its `.env`.**

Nothing else in this sprint is waiting on anything. The moment such an
environment exists, `docs/WORKOUT_SESSION_ACTIVATION.md` is executable end to
end and PR5 completes without further engineering.

Provisioning it is infrastructure work and should be scoped as its own slice —
it is a second deployment target, a second database, and a second set of
environment protections, none of which exist today. Whether that belongs to
Sprint 15 or to an ops slice ahead of it is an owner decision, not one this
document should make.
