# Sprint 9 PR3A Diary Mutation Reliability Design

Date: 2026-08-11
Status: Approved
Scope: backend-only, local implementation, no migration

## Objective

Add a versioned mobile mutation contract for the canonical `MealLog` ledger
that safely supports two universally authoritative operations:

- set an entry's canonical meal slot to an absolute desired value;
- hard-delete an entry, with lost-response ambiguity resolved by the canonical
  diary read.

Every mutation is Bearer-authenticated, owner-scoped, addressed by the existing
opaque `DiaryItemId`, and guarded by an opaque `If-Match` revision. Existing web
routes and Flutter remain unchanged.

## Prerequisites and repository truth

Backend `origin/main` is `35bb682d1128af098064ed59d36844c2238dac35`.
It contains PR #204 (`18dd7da`, canonical mobile diary read) and PR #205
(`35bb682`, mobile discovery and canonical LogFood).

Mobile `origin/main` is `389f7aeef363f6c964df6f16b96116c26d4e4ee4`.
Commit `389f7ae` is merged PR #10, the Sprint 9 PR2B food discovery and canonical
logging consumer. The mobile repository is read-only for this work.

## Persisted-truth inventory

`MealLog` persists:

- internal integer `id` and `user_id`;
- `ogun` (meal display label), `yemekler` (rendered description);
- `kalori`, `protein`, `karb`, and `yag` floating-point nutrition snapshot;
- `tarih` server-owned Istanbul diary date;
- `source` string;
- nullable `idempotency_key` and `idempotency_fingerprint`;
- nullable `photo_key`;
- nullable `created_at` naive-UTC timestamp.

Provider-backed LogFood resolves the provider food and serving before insert,
then persists only the rendered description, scaled nutrient snapshot, slot,
server day, discovery source, idempotency fields, and timestamp. It does not
persist provider, food ID, serving ID, original quantity, serving mass, barcode,
or provider metadata.

Manual LogFood persists the explicit description and nutrient snapshot plus the
same common row fields. It does not persist an authoritative entry-kind column.
Historical and non-mobile writers may also use `source="manual"`, so source or
fingerprint cannot safely classify a row for richer editing.

## Mutation capability matrix

| Operation | Classification | PR3A decision |
| --- | --- | --- |
| Delete current-day entry | SAFE WITH CURRENT PERSISTED STATE | Supported for every owned entry with `If-Match` |
| Move canonical meal slot | SAFE WITH CURRENT PERSISTED STATE | Supported for every owned entry with `If-Match` |
| Edit manual description | REQUIRES ADDITIONAL PROVENANCE | Deferred; no durable authoritative entry kind |
| Edit manual nutrition | REQUIRES ADDITIONAL PROVENANCE | Deferred; no durable authoritative entry kind |
| Change provider quantity | REQUIRES ADDITIONAL PROVENANCE | Deferred; food, serving, and quantity are absent |
| Change provider serving | REQUIRES ADDITIONAL PROVENANCE | Deferred; food and serving identity are absent |
| Replace provider food | REQUIRES ADDITIONAL PROVENANCE | Deferred |
| Provider to manual conversion | SEMANTICALLY INVALID | Forbidden |
| Manual to provider conversion | SEMANTICALLY INVALID | Forbidden |

No capability field is added because the two published operations are
universally safe. PR3B does not need to infer capability from `source` or
description.

## Canonical entry revision

The read contract gains additive `meals[].revision`. Mutation success returns
the same canonical entry serializer, including the new revision. The client
treats this value as opaque.

The revision is a base64url-encoded truncated HMAC-SHA256 digest using a subkey
derived from `SECRET_KEY` under the distinct domain
`axisai/mobile-nutrition/diary-entry-revision/v1`. It is owner-bound and row-bound
without exposing either database identifier.

The canonical input is a typed, fixed-order byte encoding of every persisted
field whose change could make a client's entry snapshot stale:

- user identity and internal row identity;
- meal label and rendered description;
- all four nutrition values;
- server diary date, source, photo key, and creation timestamp;
- idempotency key and fingerprint.

Each value carries an explicit type and null marker. Strings use UTF-8 with a
length prefix. Integers use fixed signed encoding. Finite floats use normalized
IEEE-754 binary64 bytes, with negative zero normalized to positive zero;
non-finite historical values receive explicit stable tokens. Datetimes use a
fixed naive-UTC ISO form with microseconds. No Python `repr`, unordered mapping,
or arbitrary JSON serialization is hashed.

Identical authoritative state produces an identical revision; any material row
change produces a different revision. Comparison uses `hmac.compare_digest`.
The existing DiaryItemId derivation and its domain remain unchanged.

## API contract

### Set slot

`PATCH /api/v1/nutrition/logs/<DiaryItemId>`

Headers:

- `Authorization: Bearer <credential>`
- `If-Match: "<opaque-revision>"`
- `Content-Type: application/json`

The only accepted body is:

```json
{"operation":"set_slot","slot":"ogle"}
```

Valid slots are `kahvalti`, `ogle`, `aksam`, and `ara_ogun`. Unknown or extra
fields fail; user, day, timestamp, source, provider identity, quantity,
description, nutrition, idempotency fields, and raw IDs are never writable.

Success is `200` with `{"meal": <canonical meal>}`. The DiaryItemId is
unchanged. A changed slot produces a new revision. Setting the already-current
slot is an idempotent success with the same canonical state and same revision.
Daily totals remain unchanged.

### Delete

`DELETE /api/v1/nutrition/logs/<DiaryItemId>` uses the same Bearer and
`If-Match` headers and accepts no body. Confirmed success is `204`.

The operation hard-deletes the row. A second request receives the same private
not-found result as an invalid or cross-user token. No tombstone, soft delete,
mutation journal, or replay record is introduced.

If a success response is lost, the client refreshes
`GET /api/v1/nutrition/diary/today`: absence of the entry proves the desired
state. The backend does not claim that a later `404` proves which earlier
request deleted it.

Only entries on the server's current Istanbul diary day resolve. PR3A does not
publish historical diary mutation or accept client day/timezone authority.

## Preconditions and errors

`If-Match` is the only precondition transport. It must contain exactly one
strong quoted opaque revision. Wildcards, weak validators, lists, unquoted
values, blank tokens, and a body revision are rejected.

| Condition | HTTP | Code | Retryable |
| --- | ---: | --- | --- |
| Missing `If-Match` | 428 | `DIARY_PRECONDITION_REQUIRED` | false |
| Malformed `If-Match` | 400 | `INVALID_DIARY_PRECONDITION` | false |
| Stale revision | 412 | `STALE_DIARY_ENTRY` | false |
| Invalid/malformed/cross-user/not-current-day ID | 404 | `DIARY_ENTRY_NOT_FOUND` | false |
| Invalid or unsupported command | 400 | `INVALID_DIARY_MUTATION` | false |
| Storage failure | 503 | `NUTRITION_TEMPORARILY_UNAVAILABLE` | true |

Authentication and throttling retain the existing mobile envelope and codes.
Errors never expose whether another user owns a token.

## Transaction and concurrency design

Identity resolution first scans only the authenticated user's current-day row
IDs and compares opaque identities in constant time. Once a candidate internal
ID is known, the mutation opens a bounded transaction and selects that exact
`user_id`/`id`/`tarih` row with `FOR UPDATE`.

The revision is recomputed from the locked current row and checked immediately
before mutation. Under PostgreSQL `READ COMMITTED`, competing writers serialize
on the row; the waiter observes the winner's committed state (or absence) and
cannot reuse the old revision. No provider or other external I/O runs inside the
transaction, and no process-local lock is used.

Expected races from one starting revision:

- slot/slot: exactly one changed mutation succeeds; the other receives `412`;
- slot/delete: one wins; the loser receives `412` or private `404` according to
  whether the row remains, and cannot overwrite or resurrect it;
- delete/delete: one returns `204`, the other private `404`; final state absent.

Real PostgreSQL tests use barriers and independent SQLAlchemy sessions. A fresh
revision returned by a successful slot move permits the next valid move, while
the old revision fails.

## Serialization and service boundaries

The existing pure canonical entry serializer remains the single wire
projection for diary reads, LogFood responses, and slot-mutation responses. It
is extended with a supplied revision function; ORM queries still return frozen
value objects.

A focused mutation service owns identity resolution, row locking, revision
checking, absolute slot assignment, and delete. Transport parsing stays in a
small strict parser. Route handlers translate bounded service exceptions into
the existing mobile error envelope and roll back/log only error type and request
ID on unexpected failures.

## Security, privacy, and performance

- `@require_mobile_auth` is the sole authority; browser cookies alone fail.
- Candidate and locked queries are owner- and current-day-scoped.
- DiaryItemId and revision are opaque and separately domain-separated.
- No generic JSON-to-model assignment exists.
- Tokens, IDs, users, descriptions, nutrition, and request payloads are not
  logged.
- Resolution is bounded by one day's indexed ledger rows, followed by one
  primary-key row lock; response serialization adds no per-entry query.
- Slot mutation and delete perform no provider call and hold no broad lock.

## Legacy compatibility

The legacy `CustomMealItem` PATCH/DELETE routes, `/meal-log` writers,
`/meal-log/today`, history/review, barcode add, diary-builder logging, AI coach
logging, idempotent creation, CSRF, and Flask-Login authentication remain
unchanged. Characterization tests pin legacy update/delete ownership, logged
meal rejection, response shape, numeric branch behavior, and delete behavior.
The new mobile routes are additive and CSRF-exempt only through the existing
mobile blueprint convention.

## Validation

Implementation is test-first and must cover revision canonicalization,
read-contract addition, strict preconditions, ownership and malformed IDs,
unsupported fields, idempotent same-slot behavior, authoritative diary/totals,
legacy characterization, structural guards, and deterministic PostgreSQL
slot/slot, slot/delete, and delete/delete races.

Final validation includes the full backend suite, focused mobile nutrition and
security suites, schema drift with no migration, PostgreSQL concurrency where
locally available, `git diff --check`, secret/Flutter scans, and repository
status. Nothing is pushed, merged, deployed, or changed in Flutter.
