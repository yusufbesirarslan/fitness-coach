# Sprint 9 PR2A idempotency evidence

## Executive finding

The current `meal_idempotency` implementation cannot distinguish "same user + same
Idempotency-Key + same canonical LogFood command" from the same key paired with a
materially different command. Safe, deterministic conflict detection cannot be
implemented from the current `MealLog` schema without new durable semantic state.

No migration has been implemented. The smallest sufficient schema change is one
nullable SHA-256 fingerprint column on `meal_log`; it requires an explicit approval
checkpoint before production work continues.

## Existing service behavior

`app/services/meal_idempotency.py`:

- accepts optional keys matching `^[A-Za-z0-9._:-]{8,64}$`;
- looks up only `(user_id, idempotency_key)`;
- inserts with the key and relies on the database uniqueness constraint;
- after `IntegrityError`, rolls back, requeries the winner, and returns it;
- stores no request semantics or response metadata and performs no semantic compare.

The characterization test
`test_commit_once_legacy_replays_same_key_even_when_payload_differs` proves the
current behavior: a breakfast/oatmeal row is returned when the same user/key is
retried with a dinner/salmon payload. Only one row exists and no conflict is raised.
This is baseline truth, not a production behavior change.

## Existing MealLog schema and constraints

`MealLog` currently persists:

- identity/ownership: `id`, `user_id`;
- result/display projection: `ogun`, `yemekler`, `kalori`, `protein`, `karb`, `yag`;
- server context/provenance: `tarih`, `source`, `created_at`;
- replay key: `idempotency_key`;
- optional image object key: `photo_key`.

It has `uq_meal_log_user_idempotency` on `(user_id, idempotency_key)`, the existing
day index, and macro bounds. Migration `bb88cc99dd00` introduced the nullable key
and unique constraint. The current Alembic head is `c7d8e9f0a1b2`.

There is no command kind, provider food ID, provider serving ID, quantity, canonical
request payload, response snapshot, general-purpose MealLog metadata, or request
fingerprint.

## Persisted semantic metadata inventory

No existing field can correctly carry the missing command identity:

- `source` is bounded provenance used by readers and existing writers; overloading it
  would destroy provenance and still could not encode serving semantics.
- `photo_key` is an optional S3 object key with URL behavior; using it as request
  metadata would corrupt that contract.
- `yemekler` is user-visible food text, not opaque metadata.
- macro fields are the resolved nutrition result and are deliberately shared by
  provider-backed and manual writes.
- `tarih`, `ogun`, and timestamps are server-owned diary context, not discovery
  identity.
- `idempotency_key` is the caller's replay key and cannot also represent the payload.
- JSON/payload columns on other models belong to those models and transactions; they
  are not unused MealLog metadata and cannot atomically describe this ledger row.

## Canonical-command reconstruction proof

Reconstruction from a `MealLog` row is non-injective. At least these distinct
canonical commands can persist the same row:

1. two provider foods with different provider food IDs but equal display text and
   resolved nutrition;
2. different serving-ID/quantity combinations whose server-resolved totals are equal;
3. a provider-backed command and a manual command with matching text/macros;
4. provider catalog corrections that preserve the final stored projection while the
   original command identity differs.

Because food ID, serving ID, quantity, and command kind are discarded before or at
persistence, no deterministic inverse exists. Comparing reconstructed rows would
therefore produce false replays for materially different commands.

## Migration-free alternatives evaluated

| Alternative | Verdict | Reason |
| --- | --- | --- |
| Compare existing MealLog fields | Rejected | Stored projection is lossy and non-injective. |
| Encode semantics in `source` | Rejected | Violates provenance vocabulary and length/reader contracts. |
| Encode semantics in `photo_key` | Rejected | Violates the S3 object-key contract. |
| Encode semantics in `yemekler` | Rejected | Corrupts user-visible ledger text. |
| Use another model's JSON metadata | Rejected | Wrong ownership/lifecycle; not atomically constrained to the MealLog winner. |
| In-process map | Rejected | Lost on restart and divergent across workers. |
| Redis/cache entry | Rejected | Expiry/eviction/outage and non-atomicity with the database permit false replay or conflict. |
| Detect only inside the route | Rejected | A preflight compare has no durable semantics to read and cannot close concurrent races. |

## Concurrency and restart analysis

The existing unique constraint is the correct race arbiter, but it only chooses a
winner for user/key. A semantic value must be committed atomically on that same
winner row. Any side store or preflight-only check admits a crash window between the
semantic write and MealLog insert, and a multi-worker race can observe different
state. Durable row-local data is required for deterministic behavior after restart.

## Schema sufficiency verdict

The existing schema is insufficient. A migration is necessary if PR2A must guarantee
that the same user/key replays only the same canonical command and returns a
deterministic conflict for a different provider-backed/manual command, including a
concurrent race.

## Proposed migration (not implemented)

Exact model addition:

```python
idempotency_fingerprint = db.Column(db.String(64), nullable=True)
```

Exact migration:

- revision after current head `c7d8e9f0a1b2`;
- `upgrade`: add nullable `VARCHAR(64)` column
  `meal_log.idempotency_fingerprint`;
- `downgrade`: drop that column;
- no new index;
- no new unique constraint;
- no new check constraint.

The existing `(user_id, idempotency_key)` unique constraint remains the concurrency
arbiter. The fingerprint is a lowercase 64-character SHA-256 hex digest computed by
the server over a versioned, canonical JSON representation of the validated command.
Application construction guarantees the format; omitting an additional database
check keeps the migration minimal and cross-dialect-safe.

The canonical input must include command kind and every material client command
field after normalization:

- provider-backed: version, kind, provider food ID, provider serving ID, quantity,
  and requested slot;
- manual: version, kind, user-supplied description/nutrition snapshot and requested
  slot.

Server-derived user identity is already part of the unique key scope. Server-derived
day, timestamp, source, and provider-resolved nutrition are not client command
identity and must not make an otherwise identical retry conflict.

## Legacy writer impact

The column is nullable. Existing legacy callers keep invoking the current interface
without a fingerprint and persist `NULL`; their replay behavior remains unchanged.
The new canonical mobile boundary supplies a fingerprint and uses semantic compare.
No legacy endpoint is redirected or behaviorally changed by the migration alone.

## Existing-row null semantics

- Existing and legacy-written rows may remain `NULL`; there is no backfill because
  their original command semantics cannot be reconstructed honestly.
- Legacy callers replay those rows according to existing behavior.
- If the new mobile boundary finds the same user/key on a row with `NULL`, it returns
  the deterministic idempotency conflict response. It must not claim equivalence it
  cannot prove and must not overwrite the row.

## Replay and conflict behavior

For the new mobile boundary:

- same user + key + equal fingerprint: return the existing canonical response;
- same user + key + different fingerprint: deterministic conflict;
- same user + key + winner fingerprint `NULL`: deterministic conflict;
- different users may reuse the same key independently;
- provider-backed versus manual always differs because command kind is fingerprinted.

Sequential preflight and the `IntegrityError` race-loser path must apply the same
comparison. The fingerprint is never accepted from the client and is never exposed.

## Rollback

Code rollback first removes mobile fingerprint reads/writes, then the Alembic
downgrade drops the nullable column. No backfill or data rewrite is required. The
existing key and unique constraint remain intact, restoring the current replay-only
behavior.

## Alembic and schema-drift impact

The migration must be based on the single current head `c7d8e9f0a1b2`. Model and
migration changes must land together. Schema-drift tests should assert the column's
name, type, nullability, and absence of extra indexes/constraints, plus a full
upgrade/downgrade path. Existing rows require no data migration.

## PostgreSQL concurrency implications

PostgreSQL's existing unique constraint selects one insert winner. The fingerprint
is stored in that same INSERT transaction. A loser rolls back after the uniqueness
error, requeries the committed winner, and compares fingerprints: equal replays;
different or `NULL` conflicts. No advisory lock and no second transaction/table are
needed. A real PostgreSQL race test remains required because local PostgreSQL test
configuration is unavailable; SQLite tests alone cannot prove PostgreSQL timing.

## Approval checkpoint

No production extraction, schema edit, or migration implementing this proposal has
been made. Explicit migration approval is required before proceeding.
