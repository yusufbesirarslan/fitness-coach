# PRIV-K6 completion-authority fence and capability floor

Status: source implementation on a Draft PR stacked on K6 (#446). Not merged,
not deployed, not activated anywhere. Qualified only on disposable PostgreSQL 16
with synthetic data. Production evidence E1–E7 is outstanding.

## Why

K6 writes a `WorkoutCompletionDay` claim for every completion. Every pre-K6
image recognises completion only through `PumpCheck.date_key`, so it cannot see
a photo-free claim. It also cannot see a photo claim once the photo has been
deleted from the gallery. If such an image runs after claims exist, through a
rollback, a manual restore, an old script or another environment, it completes
the same day again. That gives 45 XP after a photo-free claim and 70 XP after a
photo claim plus gallery delete, reproduced on PG16.

## Layer A: the database fence (migration `d7e3a9c1f5b8`)

Two tables:

* `completion_capability_floor`: insert-only. The row
  `workout_completion_claims_v1` is the durable, monotonic floor.
* `completion_claim_transaction`: one row per claim. The claim's own AFTER
  INSERT trigger writes it, binding the claim to `pg_current_xact_id()`
  (64-bit, top-level, never wraps, the same inside savepoints).

Deferred constraint triggers enforce the rules at COMMIT for every writer.
Only DDL bypasses them.

| SQLSTATE | Refused | Retryable |
|---|---|---|
| `AXF01` | a claim before the floor is raised | yes (activation pending) |
| `AXF02` | a dated `PumpCheck` (insert, or update of `date_key`/`user_id`) or a completion-marker `WorkoutLog` while the floor is raised **or any claim exists**, unless THIS transaction inserted the owner's claim (same day for a PumpCheck) | no |
| `AXF03` | UPDATE/DELETE/TRUNCATE of the floor; INSERT without the exclusive floor lock | no |
| `AXF04` | a photo-free claim beside committed historical `PumpCheck.date_key` evidence for the same owner/day | yes (a retry resolves to `already_completed`) |
| `AXF05` | claim UPDATE, TRUNCATE, or DELETE while its owner still exists; any direct write to a binding; a claim without its binding | no |
| `AXF06` | any fenced write outside READ COMMITTED | no |

Mechanics:

* **Shared/exclusive floor lock.** Every check first takes advisory lock
  K = `-3391128469959823719` SHARED; the raise takes it EXCLUSIVE. The raise
  therefore waits for every commit already past a check, and every later commit
  sees the floor. The database itself refuses a floor insert that does not hold
  K exclusive.
* **Claims imply the floor.** Dropping and re-creating the floor table with
  DDL does not reopen historical writers while any claim exists.
* **Hardening.** Every relation is schema-qualified (`public.`). Every function
  pins `search_path = pg_catalog, pg_temp`, is `SECURITY INVOKER`, and its
  trigger is `ENABLE ALWAYS`. Phase Zero showed two bypasses this closes: an
  unqualified function is satisfied by a session TEMP table, and an ORIGIN
  trigger is skipped under `session_replication_role = replica`.
* **Exact verifier.** `verify_fence()` in the migration checks:
  * function bodies, config, security and volatility;
  * `pg_get_triggerdef` and `tgenabled = 'A'` for every trigger, and no extra
    trigger on the fence tables;
  * no rewrite rules;
  * ordinary, permanent tables (an UNLOGGED floor is emptied by crash
    recovery), and no row-level security on the tables the fence reads;
  * exact table DDL, and PostgreSQL >= 13.

  A re-entered upgrade verifies and never "repairs". Downgrade refuses while
  any floor, claim or binding row exists.

The SQL is not overlap-provable by the static expand gate, so the file is
pinned by digest in `scripts/migration_expand_contract.py::REVIEWED_DDL`. Any
edit fails the gate until it is re-reviewed.

Application side:

* `app/services/completion_fence` holds the closed refusal vocabulary,
  `floor_status` (read-only) and `raise_floor` (the only floor writer). It
  loads its verifier from the migration file itself.
* `complete_workout` raises `CompletionFenceRefused(reason, retryable)` after a
  full rollback. Each path maps it:

  | Path | Retryable refusal | Permanent refusal |
  |---|---|---|
  | native | 503 `TRAINING_SESSION_UNAVAILABLE` + `Session-Resolution: retry` | 503 `TRAINING_COMPLETION_UNAVAILABLE`, `retryable=false`, `terminal` |
  | browser | 503 `completion_unavailable` with `Retry-After` | 503 `completion_unavailable`, `retryable: false` |
  | AI coach tool | `status=error`, `retryable` | `status=error`, `retryable` false |

* CLI: `flask completion-floor status|raise --revision <sha>`. Each prints one
  `completion-floor: {json}` line, exactly what r6 parses (pinned end to end on
  PG16). `status` is a READ ONLY transaction bounded by lock and statement
  timeouts: a blocked, unreachable or unprivileged probe is an error (unknown,
  treated as raised), never `absent`.

## Layer B: release admission (`scripts/r6_deploy_transaction.py`)

The engine trusts the baked `/app/COMPLETION_CAPABILITIES` only when all of
these hold:

1. it was read from the exact image ID;
2. it is byte-equal to the reviewed `release/completion-capabilities` of that
   image's proven revision;
3. that tree contains `d7e3a9c1f5b8`.

It then reads the floor, refuses an incapable candidate before any DB mutation
unless the floor is PROVEN absent with no claim, and re-reads before the switch.
Automatic rollback never restores an incapable web or worker past such a floor
(exit 2 `ROLLBACK_BELOW_FLOOR`, fix forward). The floor is raised only after
commit and proven retirement (exit 3 on failure). See `docs/DEPLOYMENT.md`.

## First activation (order enforced by code)

1. The K6 release ships claims and the fence in one deploy, so release-prepare
   installs the fence dormant.
2. Before the floor exists, K6 completions are refused retryably (`AXF01`)
   while pre-K6 writers keep working. Rollback is safe because no claim can
   exist.
3. Route switch, worker, then the commit point.
4. The old backend drains and its retirement is proven.
5. The floor is raised. The exclusive lock waits out in-flight commits.
6. From that commit on, any historical-only writer anywhere is refused. The
   floor is never lowered, whatever happens to flags, restarts or images.

Break-glass (DDL that disables or drops the fence) needs founder authorization,
an operator identity, and fresh E1–E4 evidence. If claims existed while the
fence was off, treat it as a data-integrity incident.

## Known residuals

* **Pre-2026-06-15 images.** They write neither a dated PumpCheck nor the
  marker, and Layer A cannot tell their undated PumpCheck from a legitimate
  standalone one. Layer B refuses them (no manifest). A manual restore of such
  an image could still grant completion XP. No supported path reaches one: the
  pipeline is forward-only, automatic rollback restores only the immediately
  previous image, and housekeeping keeps current/previous only. It takes host
  root and a deliberate build of a months-old checkout. Residual P3 (manual
  only), PRODUCTION_UNVERIFIED.
* **Marker binding is owner-level, not day-level.** A writer that performs a
  real claim can write extra markers in that same transaction. That is a
  correctness property of claim-writing (K6-family) code and cannot produce a
  second claim for a day.
* **Historical photo deletion on activation day.** Deleting the photo of a
  pre-K6 completion re-opens that day for K6 itself, which is K6's documented
  pre-K6 behaviour. After activation this matters only for a day completed by a
  legacy image on activation day.
* **DDL privilege.** A principal with DDL on the schema can disable the fence.
  Whether the production runtime role owns the tables is E2,
  PRODUCTION_UNVERIFIED.
* **Isolation and version.** The fence requires READ COMMITTED and PostgreSQL
  13 or later. Production RDS parameters are E5, PRODUCTION_UNVERIFIED.

Tests:

* `tests/test_completion_fence_pg.py`: PG16 integrity, concurrency,
  adversarial schema and tooling (F1–F5: CLI line → r6 parser, tampered fence,
  missing privilege, lock timeout, unreachable database).
* `tests/test_completion_fence.py`: contract and mapping.
* `tests/test_r6_completion_capability.py`: Layer B, including every injected
  fault on slot and legacy topologies: after activation no path restores an
  incapable previous web or worker; with a capable previous, or before
  activation, rollback is unchanged.
* `tests/qualification/fence_everywhere_plugin.py`: runs existing PG suites with
  the reviewed fence installed (`FITX_FENCE_EVERYWHERE=dormant|raised`).
* Mutation controls: `tests/qualification/run_priv_k6_rollback_gate_mutations.py`.
