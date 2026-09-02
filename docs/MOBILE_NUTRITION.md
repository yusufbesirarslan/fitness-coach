# Mobile Nutrition Contract

The nutrition surface the native AxisAI client reads, and the reasoning behind
its shape. Web nutrition behaviour is unchanged by everything in this file; the
corrections described here happen at a new versioned boundary, not underneath a
live browser surface.

- Endpoints: diary read, food discovery, and canonical LogFood under
  `/api/v1/nutrition/*`
- Route: `app/blueprints/mobile_nutrition.py`
- Service: `app/services/mobile_nutrition/`
- Tests: `tests/test_mobile_nutrition_api.py`
- Auth contract this sits on: [AUTH_CONTRACT.md](AUTH_CONTRACT.md),
  [adr/0001-native-mobile-authentication.md](adr/0001-native-mobile-authentication.md)

## Why a new surface instead of a decorator on the old one

Every existing nutrition route (`/meal-log/today`, `/meal-log/history`,
`/api/diary/today`, `/api/food/*`, `/api/menu/*`; historically also
`/api/progress/nutrition`, retired by Sprint 13 PR5)
carries `@require_auth`, which resolves a Flask-Login cookie plus a
`cognito_sid` session value and answers a browser. A native client has neither,
so no nutrition data was reachable from the app at all.

Adding `@require_mobile_auth` to those routes would have made them reachable and
published their ambiguities as the mobile contract at the same time: a `DD.MM`
day label with no year, no per-entry identity, naive timestamps with no offset,
`0` where a value is unknown, and sequential database identifiers. The mobile
surface is therefore an **adapter over the same canonical ledger**, with the
semantics stated once, here.

## Authentication

The route uses the existing authoritative mobile boundary,
`app/mobile_auth_middleware.require_mobile_auth`, and nothing else:

- credential: `Authorization: Bearer <opaque AxisAI access credential>`;
- the user is `g.mobile_user`, resolved from the verified credential. There is
  no account parameter on the request, so there is nothing to tamper with;
- a browser cookie is never consulted, and a valid Flask-Login session alone
  gets `401`;
- failures use the ADR 0001 envelope
  (`{"error": {code, message, retryable, request_id}}`), never an HTML page and
  never a redirect;
- the route is registered on the existing `mobile_api` blueprint, so it inherits
  `Cache-Control: no-store`, the `429 → AUTH_RATE_LIMITED` handler and the
  `MOBILE_AUTH_ENABLED` feature gate, and it stays inside the approved-route
  allow-list in `tests/test_mobile_auth_feature_gate.py`.

No third authentication mechanism exists, and the route never decodes a JWT.

## The response

```json
{
  "day":    { "date": "2026-08-09", "timezone": "Europe/Istanbul" },
  "meals": [
    {
      "id": "9pJ0m2Qv7bC1sT4xY8Zk3aWn",
      "slot": "kahvalti",
      "description": "Yulaf ezmesi (50 g), 2 yumurta",
      "source": "manual",
      "logged_at": "2026-08-09T08:24:00+03:00",
      "nutrition": {
        "energy_kcal": 412.0,
        "protein_g": 24.5,
        "carbohydrate_g": 34.0,
        "fat_g": 18.2
      }
    }
  ],
  "totals": {
    "energy_kcal": 412.0,
    "protein_g": 24.5,
    "carbohydrate_g": 34.0,
    "fat_g": 18.2
  },
  "goal": { "target_energy_kcal": 2200.0 }
}
```

The four top-level keys are always present. Field set, ordering rule and
nullability are fixed; the contract evolves **additively** and carries no
`schema_version`, the same convention as `GET /api/training/weekly-program`
(see [WEEKLY_PROGRAM.md](WEEKLY_PROGRAM.md)).

### `day`

`date` is a full ISO `YYYY-MM-DD` calendar day and `timezone` is the IANA zone
that produced it. Both come from `app/timeutil` (`app_today()`, `APP_TZ`), the
repository's single day authority — the route does not hard-code a zone and does
not read a clock of its own.

The day boundary is the server's. No query parameter, header or body field can
move it; `?day=`, `?timezone=` and `X-Timezone` are ignored, and a test pins
that.

### `meals`

One element per canonical ledger row, ordered by `created_at` then primary key,
so the list order is stable between reads.

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Opaque entry identity — see below |
| `slot` | string | `kahvalti` / `ogle` / `aksam` / `ara_ogun` / `unknown` |
| `description` | string | The display string the server composed when the meal was logged |
| `source` | string | `manual` / `diary` / `ai_plan` / `search` / `barcode` / `coach` / `suggestion` / `unknown` |
| `logged_at` | string \| null | Offset-aware ISO 8601 instant, or `null` when unrecorded |
| `nutrition` | object | Four nutrients, each a number or `null` |

`description` is a rendered string, not structure. The ledger does not keep the
foods, servings or quantities that produced it, so the client must not split it,
parse quantities out of it or mine food names from it.

**Slots.** `MealLog.ogun` stores a Turkish *display* label, and only some writers
use one of the four canonical labels: the AI coach writes `AI Koç` and a shared
meal suggestion writes a sentence containing the sender's name. The mapping is
therefore exact, with `unknown` for everything else — inferring a slot from free
text would be inventing data. The wire keys are not new: they are the keys
`POST /api/quick-add-meal` already accepts for the same four slots.

**Sources.** Published verbatim when recognised. A row written before the
`source` column existed carries NULL and becomes `unknown`, not `manual`: the
web surface displays NULL as "manual", but that is a display default and copying
it here would be exactly the kind of fabrication this contract exists to stop.

`suggestion` was added by **Sprint 13 PR3** (F8). An accepted shared meal
suggestion previously set no `source` at all, so the column default stamped it
`manual` — a *recognised* value, which meant no reader could even tell the
provenance had been lost. The writer now states what it is. **No historical row
was backfilled and no migration was written**: rows accepted before PR3 keep
whatever they were stamped with, and the vocabulary change is additive.

### `totals`

Server-authoritative, and the client does not recompute them.

They are the sum the server already publishes on `/meal-log/today`, which counts
a NULL macro as zero. That behaviour is preserved deliberately: making a whole
day's total null because one entry is missing its fat figure would erase real
measurements. The consequence is stated rather than hidden — **when any entry
carries a null, re-adding the published entry nutrition can legitimately produce
a smaller number than `totals`.** The server's figure is the answer.

A day with no entries totals `0`, which is a measurement, not a gap.

### `goal`

`null` when no target is configured; otherwise `{"target_energy_kcal": <number>}`.

The value is the newest `UserSession.target_calories` — the same selector
`/meal-log/today`, `/meal-log/review` and the barcode context already
use. This contract normalises what that value *means* at the boundary; it does
not add a second place that decides where a target comes from.

Normalisation: a NULL target and a non-positive stored target both publish as
"no goal". Zero kilocalories a day is not a target anyone configured.
Zero-as-unset stops at this boundary; the persisted column keeps
whatever it holds. (The legacy `/api/progress/nutrition` surface that used to
answer `target_kcal: 0` for both "unset" and "zero" was retired by Sprint 13
PR5.)

## Entry identity

`meals[].id` is an opaque token, not a database key.

It is **derived, not stored**: `MealLog.id` is already a stable internal
identity, so persisting a second one would be schema churn for a naming problem.
The token is `base64url(HMAC-SHA256(subkey, "<user_id>\0<entry_id>")[:18])`,
where `subkey = HMAC-SHA256(SECRET_KEY, "axisai/mobile-nutrition/diary-entry-id/v1")`
(`app/services/mobile_nutrition/identity.py`).

Guarantees:

- **opaque** — no row number, no ordering, no type semantics to reason about;
- **stable** — the same row yields the same token on every read, for as long as
  `SECRET_KEY` is unchanged. Rotating `SECRET_KEY` invalidates every token; this
  is safe because tokens are read-scoped and re-fetched with the day, and it is
  the same blast radius as rotating the key already has for cookies and CSRF;
- **owner-bound** — user A's token for row N is not user B's token for row N, so
  a leaked token cannot address another account's ledger.

Resolution, when the mutation PR needs it: recompute the digest over the
authenticated user's own candidate rows and compare with `hmac.compare_digest`
(`matches_diary_entry_id`). The scan is user-scoped by construction, so an
unknown token cannot reveal whether some other account's entry exists — it is
simply not found.

## Source-of-truth matrix

| Concept | Owner | Notes |
| --- | --- | --- |
| Diary day + zone | `app/timeutil` (`app_today`, `APP_TZ`) | Fixed Europe/Istanbul; server-only |
| Entries | `MealLog` | The canonical consumed-food ledger |
| Totals | `MealLog`, summed server-side | NULL counted as zero |
| Target calories | newest `UserSession.target_calories` | Normalised to `null` at the boundary |
| Timestamps | `MealLog.created_at` (naive UTC) | Converted with `timeutil.to_app_tz` |
| Entry identity | derived from `MealLog.id` + user | Never published as an integer |
| Serving / provider identity | **not available** | The ledger does not store it — see below |

### One nutrition authority, not three

The backend has three related surfaces and only one of them is the ledger:

| Surface | Model | Role |
| --- | --- | --- |
| `/meal-log/today` | `MealLog` | Canonical record of what was eaten |
| `/api/diary/today` | `CustomMeal` + `CustomMealItem` | Web diary *builder* (staging) |

Committing a builder meal writes it into the ledger **and** keeps it in the
builder, so the two totals must never be added; both route docstrings say so.
The mobile contract reads the ledger only. It never queries the builder, so
there is no double-count to avoid at the client, and no third definition of
"today's nutrition" was created.

The cost of that choice is honest: **the ledger keeps no food identity, serving,
quantity, mass or per-100 g reference**, so the mobile entry does not carry
them. Those concepts belong to the discovery and logging path, not to a
persisted ledger row, and fabricating them from `description` would be guesswork.

## Null and zero semantics

| Field | `null` means | `0` means |
| --- | --- | --- |
| `meals[].nutrition.energy_kcal` | not recorded | a measured zero |
| `meals[].nutrition.protein_g` | not recorded | a measured zero |
| `meals[].nutrition.carbohydrate_g` | not recorded | a measured zero |
| `meals[].nutrition.fat_g` | not recorded | a measured zero |
| `meals[].logged_at` | no timestamp recorded | n/a |
| `goal` | no target configured | n/a — a zero target publishes as `null` |
| `totals.*` | never null | nothing logged, or everything logged summed to zero |

`totals` is the one place a missing value is treated as zero, and it is
documented above rather than silently applied.

## Errors

| Condition | HTTP | Code | Retryable |
| --- | --- | --- | --- |
| Missing, malformed or rejected Bearer credential | 401 | `AUTH_SESSION_EXPIRED` | false |
| Provider/JWKS or credential-store failure classified as temporary | 503 | `AUTH_TEMPORARILY_UNAVAILABLE` | true |
| Throttled | 429 | `AUTH_RATE_LIMITED` | true |
| Nutrition read failed (storage or projection fault) | 503 | `NUTRITION_TEMPORARILY_UNAVAILABLE` | true |

`NUTRITION_TEMPORARILY_UNAVAILABLE` is the only code this PR adds. It exists
because the blueprint's catch-all answers `AUTH_TEMPORARILY_UNAVAILABLE`, and a
client that read a storage fault as an authentication outcome would discard a
perfectly good session and send the user back to login. The route catches its
own failures, rolls the session back and answers with a nutrition-shaped error.

There is no `404` on this route: a day with nothing in it is an empty day, not a
missing resource.

## Privacy and logging

The route emits at most one log line, only on failure:

```
mobile_nutrition event=diary_read_failed error_type=<ExceptionClass> request_id=<id>
```

No meal description, macro figure, target, slot, account identifier, credential
or provider text is logged on any path, and no analytics event is emitted. A
successful read logs nothing. Both are pinned by tests.

## Performance and concurrency

Two bounded, user-scoped `SELECT`s per request: one over
`ix_meal_log_user_id_tarih` for the day's ledger rows, one for the newest
`UserSession`. Rows leave the query layer as frozen value objects, so a lazy
attribute access cannot turn one read into N; a test asserts the query count
does not change between a one-entry and a five-entry day.

No provider (FatSecret/LLM) call, no cache, no lock, no write and no transaction
of its own. The read is a snapshot of committed state; the ledger rows are read
in one statement, and the target is independent of them, so no cross-statement
consistency is claimed or needed.

## Backward compatibility

Nothing existing changed behaviour:

- `/meal-log/today`, `/meal-log/history`, `/api/diary/today`,
  `/api/food/*` and `/api/menu/*` keep their paths,
  their auth, their payloads and their Turkish field names. A characterisation
  test pins that `/meal-log/today` still answers `DD.MM` with no entry id.
  Sprint 13 PR5 retired the orphaned `/api/progress/nutrition` read (F9) —
  that path is no longer a live compatibility surface;
- no route switched to Bearer-only auth;
- no persisted total, diary grouping or timezone behaviour changed;
- the PR1 read adapter itself changed no schema; PR2A later added only the
  nullable fingerprint column documented below, without changing legacy writer
  behavior or the existing unique constraint.

## PR2 prerequisites — what exists and what does not

What Mobile Sprint 9 PR2 needs beyond this read surface, with its current
backend status:

| Capability | Path | Mobile-reachable | Notes |
| --- | --- | --- | --- |
| Diary read | `GET /api/v1/nutrition/diary/today` | **yes** | This PR |
| Food search | `GET /api/food/search` | no — `@require_auth` | FatSecret-backed; returns `food_id`, `macros`, `per_100g` |
| Serving detail | `GET /api/food/<food_id>/servings`, `GET /api/food/servings-by-name` | no — `@require_auth` | Provider serving ids and descriptions |
| Barcode lookup | `GET /api/food/barcode` | no — `@require_auth` | Validates EAN/UPC length, DB-cached, `404` with a stable `not_found` body |
| Barcode log | `POST /api/food/barcode/add` | no — `@require_auth` | **DEPRECATED compatibility surface** (Sprint 13 PR3, C13) — still supported for now, responds `Deprecation: true` + `Link: </meal-log>; rel="successor-version"`. Removal requires evidence and is PR5's decision; **no sunset date is promised**. Writes `MealLog` with `source="barcode"` recomputed from server-resolved provider truth (a caller-supplied `food` object is no longer read), honours `Idempotency-Key`, and publishes no raw `MealLog.id` on any variant |
| Manual/AI meal log | `POST /meal-log` | no — `@require_auth` | Writes `MealLog`; already honours `Idempotency-Key`. Since Sprint 13 PR3 the request shape is an explicit, mutually exclusive command: `provider_food` (identities + quantity, recomputed server-side through the SAME `mobile_log_food` authority as `POST /api/v1/nutrition/logs`) · `override_macros` (genuinely manual, validated by the shared `ManualNutritionSnapshot` bounds) · free text / photo (AI estimation). Mixing them is `400`; `ogun` must be one of the four canonical labels |
| Diary builder writes | `POST /api/diary/*` | no — `@require_auth` | Web staging surface; not the mobile logging path |
| Menu scan/analyse | `POST /api/proxy/scan-menu`, `POST /api/menu/analyze` | no — `@require_auth` | Scrape + LLM; behind concurrency gates |

**Idempotency already exists and does not need designing.**
`app/services/meal_idempotency.py` reads an `Idempotency-Key` request header
(`[A-Za-z0-9._:-]{8,64}`), looks the prior write up **within the authenticated
user's scope**, and commits through a `uq_meal_log_user_idempotency`
`(user_id, idempotency_key)` unique constraint, resolving the race to its winner
row on `IntegrityError`. It is durable, user-scoped, bounded and race-safe, and
the canonical `MealLog` writers already use it. A mobile logging adapter should
reuse it verbatim rather than introduce a second scheme.

**The canonical write lifecycle**, for the adapter that will follow:

1. the food is identified by a FatSecret `food_id`, the serving by a provider
   `serving_id`, and quantity is a **serving multiplier**, not grams;
2. the server rescales the serving macros by that multiplier and clamps them
   (`nutrition_pipeline.clamp_serving_macros`) — client-supplied macros are
   never trusted as-is;
3. one `MealLog` row is written with `tarih = day_key()` (server day),
   `ogun` = one of the four Turkish labels, `yemekler` = a composed display
   label, and `source` = the writing path;
4. the response returns the ledger row and the recomputed nutrients.

Provider credentials never leave the server. FatSecret authentication lives in
`app/services/fatsecret.py` behind a loopback proxy; no token, key or upstream
URL appears in any mobile payload.

## Mobile PR1 compatibility

The Flutter contract from mobile Sprint 9 PR1 (`docs/NUTRITION.md`,
`fixtures/nutrition-diary-day.json`, marked `authoritative_contract: false`)
matches this payload field for field: `day.date`, `day.timezone`, `meals[]` with
`slot`/`description`/`source`/`logged_at`/`nutrition`, the
`energy_kcal`/`protein_g`/`carbohydrate_g`/`fat_g` nutrient keys, `totals`, and
an optional `goal.target_energy_kcal`. The slot and source wire vocabularies are
the ones that contract already carries, including `unknown`.

One additive difference: `LoggedMeal` there has no identifier, because the
ledger exposed none when it was written. This surface publishes `meals[].id`,
which the PR1 documentation itself lists as a required backend prerequisite. The
mobile change is one required string field on `LoggedMealFixtureDto` and a
`DiaryItemId` on `LoggedMeal` — additive, and no backend distortion.

The builder-shaped types (`DiaryFoodItem`, `FoodServing`, `ProviderFoodRef`,
`MassAmount`, per-100 g reference) have no counterpart on this surface, and that
is correct: they describe the discovery/logging path, which is a separate
backend surface still to be adapted.

## PR2A discovery and canonical LogFood

The mobile discovery routes are read-only:

- `GET /api/v1/nutrition/foods/search?q=...`
- `GET /api/v1/nutrition/foods/fatsecret/<food_id>/servings`
- `GET /api/v1/nutrition/foods/barcode?code=...`

They project raw provider data before legacy portion estimation. Missing metric
mass and underivable per-100 g nutrition are `null`, never fabricated zero. The
barcode adapter may read an existing cache row but does not populate the cache,
and no discovery route writes `MealLog`.

`POST /api/v1/nutrition/logs` is the only mobile food persistence boundary. It
accepts a strict `provider_backed` or `manual` command. Provider commands carry
provider/food/serving identity, quantity, slot and discovery source; client
nutrition is forbidden and the server resolves/scales the serving. Manual
commands carry a trimmed description, slot and explicit bounded nutrition
snapshot; provider identity, serving and quantity are forbidden.

Both variants write one `MealLog` row and return the same `meal` shape with the
owner-bound opaque ID and server diary day. A required `Idempotency-Key` is
scoped by authenticated user. The server hashes the typed, versioned
`axisai/mobile-log-food/v1` semantic command:

- equal fingerprint replays the original row and opaque ID;
- different fingerprint returns `409 IDEMPOTENCY_CONFLICT`;
- a legacy row with a `NULL` fingerprint also conflicts because its semantic
  ownership cannot be proven.

The sole schema change is nullable
`MealLog.idempotency_fingerprint VARCHAR(64)`. Legacy writers leave it `NULL`;
the existing `(user_id, idempotency_key)` unique constraint remains the sole
database race arbiter. The fingerprint excludes user identity, credentials,
request IDs, timestamps, diary day, resolved provider nutrition and raw provider
payloads. Changing v1 canonicalization requires a new fingerprint version.

Menu discovery is not published on mobile in PR2A. Its independent gate is
recorded in
`docs/superpowers/reports/2026-08-10-sprint9-pr2a-menu-security-gate.md`.

## Sprint 9 PR3A diary mutation contract

PR3A adds stale-safe mutation for the two operations that are authoritative
with the fields `MealLog` already persists. It does not add a schema migration.

| Operation | Status | Reason |
| --- | --- | --- |
| Delete a current-day entry | supported | Entry identity and ownership are authoritative |
| Set canonical meal slot | supported | `MealLog.ogun` is authoritative persisted state |
| Edit manual description or nutrition | deferred | No durable authoritative entry-kind provenance |
| Change provider quantity or serving | deferred | Provider food, serving, and original quantity are not persisted |
| Replace provider food | deferred | Provider identity is not persisted |
| Convert provider/manual kind | forbidden | Would fabricate provenance and change semantics |

The supported actions apply to every current-day entry, including legacy rows,
so no capability booleans are required. A client must not infer future
capabilities from `source`, description, macros, fingerprint, or barcode text.

### Entry revision

Every canonical `meals[]` element now includes required string `revision`. The
same field appears in a successful LogFood response and PATCH response. It is an
opaque precondition, not a database version, and Flutter must only store and
echo it.

The backend derives it from a typed canonical encoding of the owner, row
identity, slot, description, four nutrition values, diary date, source,
idempotency fields, photo key, and creation timestamp. The digest is an
owner-bound HMAC under the separately domain-separated context
`axisai/mobile-nutrition/diary-entry-revision/v1`. Identical authoritative state
has an identical token; material changes have a different token. It is not
derived from timestamp alone and exposes no raw database value.

### Set slot

```http
PATCH /api/v1/nutrition/logs/<DiaryItemId>
Authorization: Bearer <opaque access credential>
If-Match: "<meals[].revision>"
Content-Type: application/json

{"operation":"set_slot","slot":"ogle"}
```

`slot` is exactly one of `kahvalti`, `ogle`, `aksam`, or `ara_ogun`. The request
sets an absolute target; there is no increment, next-slot, quantity, or delta
operation. The body must have exactly `operation` and `slot`. Description,
nutrition, provider/food/serving identity, quantity, user, ID, day, timezone,
timestamp, source, fingerprint, idempotency key, and body revision are rejected.

Success is `200`:

```json
{"meal":{"id":"opaque","revision":"opaque","slot":"ogle","description":"...","source":"manual","logged_at":"2026-08-11T08:24:00+03:00","nutrition":{"energy_kcal":320.0,"protein_g":12.0,"carbohydrate_g":40.0,"fat_g":9.0}}}
```

The response uses the same canonical entry serializer as the diary read. The
DiaryItemId is unchanged. A real slot change returns a new revision. Setting the
already-current slot is an idempotent `200` with unchanged state and revision.
Daily totals are unchanged by a slot move.

### Hard delete and ambiguous transport outcomes

```http
DELETE /api/v1/nutrition/logs/<DiaryItemId>
Authorization: Bearer <opaque access credential>
If-Match: "<meals[].revision>"
```

DELETE accepts no body. Confirmed success is an empty `204`. It hard-deletes the
row; PR3A introduces no tombstone, soft-delete flag, mutation journal, or delete
idempotency table. A second request receives private `404 DIARY_ENTRY_NOT_FOUND`,
the same answer as a malformed, unknown, historical, or cross-user token.

If the server commits but the response is lost, repeating DELETE cannot prove
what happened because the identifying row is gone. PR3B must refresh
`GET /api/v1/nutrition/diary/today`: if the entry is absent, the desired state
has been achieved. The refreshed server totals are authoritative; Flutter does
not subtract macros locally.

### Preconditions, errors, and retryability

`If-Match` is the only mutation precondition transport. It must contain exactly
one strong quoted revision. Weak validators, wildcards, lists, unquoted or blank
tokens, and body revisions are invalid.

| Condition | HTTP | Mobile code | Retryable |
| --- | ---: | --- | --- |
| Bearer absent/rejected | 401 | `AUTH_SESSION_EXPIRED` | false |
| `If-Match` absent | 428 | `DIARY_PRECONDITION_REQUIRED` | false |
| `If-Match` malformed | 400 | `INVALID_DIARY_PRECONDITION` | false |
| Revision stale | 412 | `STALE_DIARY_ENTRY` | false; refresh diary |
| ID malformed/unknown/cross-user/not current day | 404 | `DIARY_ENTRY_NOT_FOUND` | false; reconcile |
| Command/body unsupported | 400 | `INVALID_DIARY_MUTATION` | false |
| Database/storage failure | 503 | `NUTRITION_TEMPORARILY_UNAVAILABLE` | true |
| Blueprint throttled | 429 | `AUTH_RATE_LIMITED` | true |

PATCH is retryable as an absolute desired-state request. If a response was lost,
the old revision may now return `412`; refresh and compare the canonical slot.
DELETE ambiguity is always resolved by the canonical diary read, not by treating
arbitrary missing tokens as successful deletes.

### Ownership, concurrency, and transaction boundary

Both routes use `@require_mobile_auth`; user authority is only `g.mobile_user`.
The server resolves DiaryItemId across that owner's current Istanbul diary day,
then selects the exact `user_id`/row/day with PostgreSQL `FOR UPDATE`. It
recomputes and compares the revision while holding that row lock immediately
before changing or deleting state. No provider call or process-local lock is
inside the transaction.

Consequences for two writers using one starting revision:

- slot/slot: one changed mutation succeeds and one is stale;
- slot/delete: one wins and the loser is stale or privately missing; no
  resurrection or overwrite occurs;
- delete/delete: one actual delete succeeds and the other is privately missing.

The existing PostgreSQL concurrency CI job runs these races in
`tests/test_mobile_diary_mutation_pg.py`. Legacy web PATCH/DELETE routes keep
their Flask-Login, CSRF, payload, numeric, response, and no-precondition
behavior. PR3B must wait until this backend contract is reviewed, CI-green, and
merged.
