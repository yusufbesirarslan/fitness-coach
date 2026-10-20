# PRIV-K6-B1 — Photo-free workout completion

Founder Privacy Decision K6: a user who declines AI processing keeps core
workout tracking, completed-session history and **photo-free completion**.
This record covers the backend half. Mobile screens are a separate task.

## 1. Why this was needed

On `8a8f505` the native completion route ran `_completion_proof()` for every
fresh completion, and `_completion_proof()` calls `require_legacy_ai()`, which
always raises `ConsentDenied` after D1. So no fresh native completion could
succeed for anyone. Every completion also needed a PumpCheck, because the
PumpCheck's `date_key` was the only completed-day claim.

## 2. Completion authority (after K6)

| Concern | Owner |
|---|---|
| Completed-day claim | `WorkoutCompletionDay` (`uq_workout_completion_day`, one row per owner + Istanbul day), written ONLY by `workout_completion.service.complete_workout`, for every new completion, photo or not. Historical days stay recognized through `PumpCheck.date_key` (`uq_pump_check_day`). |
| "Is day D completed?" | `workout_completion.queries.completed_days` / `already_completed_today`: ONE `UNION` statement over both claims. Every reader goes through it: `workout_state` (Today, `/workout/status`, `/api/v1/today`, coach state block, barcode context), session start guard, completion preflights (browser, AI tool, native), `training_generation.time_series_model` adherence. |
| Optional photo evidence | `PumpCheck`. Written only for a photo completion (validated photo). A standalone Pump Check (`date_key IS NULL`) is never a completion. |
| Session terminalization | unchanged: `complete_workout`, ACTIVE→COMPLETED, in the same transaction, session row locked first. |
| Serialization | unchanged lock order: session row `FOR UPDATE` → `pg_advisory_xact_lock(owner, day)` → artifacts. NEW: the completed-day state is re-checked **under** the day lock before any claim is written. |

Photo and photo-free completions therefore cannot both succeed for one day:

* both write the same `uq_workout_completion_day` row;
* both take the same day lock and re-check both claims under it. The re-check
  is what refuses a historical-only claim, because no unique constraint spans
  `pump_check` and `workout_completion_day`.

## 3. Migration `f9c6d0a1b2e3` (down `f8b2d4e6a0c3`, expand)

* creates `workout_completion_day (id PK, user_id FK user CASCADE NOT NULL,
  date_key VARCHAR(10) NOT NULL, proof VARCHAR(8) NOT NULL CHECK proof IN
  ('photo','none'), created_at NOT NULL, UNIQUE (user_id, date_key))`.
* **No backfill.** No historical photo is read, rewritten or re-dated.
* create_all-first / re-entry: verify-or-create. An existing table is accepted
  only with exactly this DDL. That means column types, nullability and
  defaults, the PK, the day UNIQUE, the CASCADE FK, the CHECK predicate, and
  `convalidated` on PG. PostgreSQL additionally resolves the ID default through
  `pg_attrdef`/`pg_depend` to its actual sequence OID. That object must be the
  table's own auto-dependent SERIAL sequence in the same namespace and with
  the same owner: INTEGER, increment 1, minimum 1, maximum 2147483647, NO CYCLE.
  Table and sequence must both be permanent (`relpersistence = 'p'`): crash
  recovery or standby promotion empties an UNLOGGED claim table or rewinds an
  UNLOGGED sequence.
  The verifying role must have USAGE or UPDATE on it. START, CACHE and current
  counter state are not normalized or reset. This establishes the expected
  generator structure, not future insert success: exhaustion, manual IDs or
  counter rewinds, later DDL/grant changes and a distinct application role's
  privileges require separate operational qualification. Identity columns and
  custom generators are outside this migration/model's SERIAL contract.
  Anything else verified here fails closed without modifying the table.
  User triggers and rules on the claim table are not inspected.
* downgrade refuses while any claim exists.
* Purge: `WorkoutCompletionDay` is in `app/cli.py::_user_child_models`.

## 4. Native API contract

`POST /api/v1/training/workout-sessions/{session_ref}/complete`

Headers are unchanged:

* `Authorization: Bearer …`
* `If-Match: <revision>` (required)
* `Idempotency-Key` (required syntax)

### 4.1 Photo-free completion

Send **no photo input at all**:

* no body; or
* `Content-Type: application/json` with an object that has none of the keys
  `image`, `location_type` or `description` (for example `{}`).

The server:

* does no D1 consent check, image validation, vision/AI call or S3 write;
* creates no PumpCheck and grants no photo bonus;
* does not record the Pump Check challenge event.

Success: `200`, `Idempotency-Replayed: false`:

```json
{
  "session": { "...": "unchanged projection", "status": "completed" },
  "completion": {
    "outcome": "created",
    "proof": "none",
    "xp_awarded": 10,
    "new_total": 10,
    "level": "<int>",
    "title": "<level title>"
  }
}
```

`xp_awarded` is the base 10. It is +50 when the daily `workout_logged` quest
is active and still unclaimed.

### 4.2 Photo completion (unchanged)

Any of these is a photo completion:

* any `multipart/form-data` or form body, even with a missing, empty, misnamed
  or unusable file;
* a JSON body naming `image`, `location_type` or `description`;
* any other non-empty body that is not a JSON object (raw image bytes, for
  example).

It meets the D1 consent gate first and image validation second. Today that
means `403 AI_CONSENT_DISCLOSURE_UNAVAILABLE`. **A submitted photo is never
silently dropped into a photo-free success.** To complete photo-free, the
client must send no photo.

`completion.proof` is `"photo"` on a photo success.

### 4.3 Replay

If the day is already completed (by either path, or historically), or the
session is already COMPLETED, the response is `200`,
`Idempotency-Replayed: true`, with:

```json
{"outcome": "already_completed", "proof": null, "xp_awarded": 0,
 "new_total": null, "level": null, "title": null}
```

Nothing is processed (no photo, AI or S3 work) and nothing is written. The
only exception: an ACTIVE linked session is terminalized.

### 4.4 Unchanged refusals

| Status | Code | Meaning |
|---|---|---|
| 404 | `TRAINING_SESSION_NOT_FOUND` | foreign or unknown session |
| 409 | `TRAINING_SESSION_REVISION_CONFLICT` (`Session-Resolution: reread`) | stale `If-Match` |
| 409 | `TRAINING_SESSION_TERMINAL` | abandoned session |
| 409 | `TRAINING_SESSION_STALE` | session from a previous Istanbul day |
| 503 | `TRAINING_SESSION_UNAVAILABLE` (retry) | persistence failure, fully rolled back |
| 503 | `TRAINING_SESSION_COMPLETION_BUSY` | concurrency gate |

## 5. Rewards / product semantics

| | photo | photo-free |
|---|---|---|
| day claim | `proof='photo'` | `proof='none'` |
| PumpCheck / gallery / feed object | yes | **no** |
| base XP (10) | yes | yes |
| photo bonus (25) | yes | **never** (also refused when the command is built) |
| `workout_logged` quest + its challenge funnel | yes | yes |
| `pump_check_created` challenge | yes | **no** |
| friend share | browser/AI paths only | **never** |
| activity text | "Bugünkü antrenmanını tamamladı (foto eklendi)" | "Bugünkü antrenmanını tamamladı" |
| marker `WorkoutLog` | yes | yes (paired with the claim) |

`CompleteWorkoutCommand(proof="none")` refuses a missing `session_id` and any
photo-shaped field. Those fields are image key, friends, non-private
visibility, photo bonus, location, description, plan score, and the photo
activity text. The refusal fires at construction.

## 6. Intentional behavior change

Deleting the Pump Check photo of a photo completion (gallery delete) no
longer re-opens that day. The day claim survives. Before K6 a gallery delete
erased the claim, so the day could be completed and rewarded again, and a
`completion_marker_mismatch` anomaly appeared. Pre-K6 completions keep the old
behavior because they have no claim row.

## 7. Tests

* `tests/test_priv_k6_no_photo_completion.py` (SQLite, HTTP and service):
  K6-A…L, readers, command contract, classifier, structure, SQLite migration.
* `tests/test_priv_k6_no_photo_completion_pg.py` (PostgreSQL 16, in the CI
  list): concurrent photo-free completions, mixed-mode races, the locked
  re-check negative control, start versus completion, forced rollback, real
  IntegrityError classification, and migration verify/negative controls,
  downgrade and the no-backfill full chain.
* `tests/qualification/run_priv_k6_b1_mutations.py`: 25 semantic mutations.


## R1 remediation boundary

Transport classification checks multipart/form-data and form media types before
any parser/stream access, including empty and malformed multipart. D1 denial
precedes photo admission. Genuine no-photo work and canonical replays bypass
AI admission. A dedicated owner completion throttle (30/minute, 300/hour by
default) applies to all commands; the existing Bedrock quota and shared AI
semaphore apply only after photo classification, ownership/revision/day
preflight and consent. Photo limiter hints are exempt unless this request has
explicitly reached photo admission; exhausting that quota cannot throttle a
later no-photo command. HTTP parsing errors retain their client status.

Application rollback compatibility remains OPEN. See
[the required release gate handoff](PRIV_K6_ROLLBACK_RELEASE_GATE_HANDOFF.md).
Schema downgrade refusal alone does not protect against historical-only images.

R2 sequence verification also rejects a default dependency on a custom function
(e.g. a search-path shadow of `nextval(regclass)`); PostgreSQL's built-in
`nextval` is pinned and has no such `pg_depend` entry. Catalog authority:
[pg_sequence](https://www.postgresql.org/docs/16/catalog-pg-sequence.html),
[pg_depend](https://www.postgresql.org/docs/16/catalog-pg-depend.html), and
[sequence functions](https://www.postgresql.org/docs/16/functions-sequence.html).
