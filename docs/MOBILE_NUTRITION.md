# Mobile Nutrition Contract

The nutrition surface the native AxisAI client reads, and the reasoning behind
its shape. Web nutrition behaviour is unchanged by everything in this file; the
corrections described here happen at a new versioned boundary, not underneath a
live browser surface.

- Endpoint: `GET /api/v1/nutrition/diary/today`
- Route: `app/blueprints/mobile_nutrition.py`
- Service: `app/services/mobile_nutrition/`
- Tests: `tests/test_mobile_nutrition_api.py`
- Auth contract this sits on: [AUTH_CONTRACT.md](AUTH_CONTRACT.md),
  [adr/0001-native-mobile-authentication.md](adr/0001-native-mobile-authentication.md)

## Why a new surface instead of a decorator on the old one

Every existing nutrition route (`/meal-log/today`, `/meal-log/history`,
`/api/diary/today`, `/api/progress/nutrition`, `/api/food/*`, `/api/menu/*`)
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
| `source` | string | `manual` / `diary` / `ai_plan` / `barcode` / `coach` / `unknown` |
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
`/api/progress/nutrition`, `/meal-log/review` and the barcode context already
use. This contract normalises what that value *means* at the boundary; it does
not add a second place that decides where a target comes from.

Normalisation: a NULL target and a non-positive stored target both publish as
"no goal". Zero kilocalories a day is not a target anyone configured, and
`/api/progress/nutrition` currently answers `target_kcal: 0` for both "unset"
and "zero". Zero-as-unset stops at this boundary; the persisted column keeps
whatever it holds.

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
| `/api/progress/nutrition` | `MealLog` | Multi-day aggregation of the ledger |

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
  `/api/progress/nutrition`, `/api/food/*` and `/api/menu/*` keep their paths,
  their auth, their payloads and their Turkish field names. A characterisation
  test pins that `/meal-log/today` still answers `DD.MM` with no entry id;
- no route switched to Bearer-only auth;
- no persisted total, diary grouping or timezone behaviour changed;
- **no database migration.** No model, column, index or constraint was touched.

## PR2 prerequisites — what exists and what does not

What Mobile Sprint 9 PR2 needs beyond this read surface, with its current
backend status:

| Capability | Path | Mobile-reachable | Notes |
| --- | --- | --- | --- |
| Diary read | `GET /api/v1/nutrition/diary/today` | **yes** | This PR |
| Food search | `GET /api/food/search` | no — `@require_auth` | FatSecret-backed; returns `food_id`, `macros`, `per_100g` |
| Serving detail | `GET /api/food/<food_id>/servings`, `GET /api/food/servings-by-name` | no — `@require_auth` | Provider serving ids and descriptions |
| Barcode lookup | `GET /api/food/barcode` | no — `@require_auth` | Validates EAN/UPC length, DB-cached, `404` with a stable `not_found` body |
| Barcode log | `POST /api/food/barcode/add` | no — `@require_auth` | Writes `MealLog` with `source="barcode"`; already honours `Idempotency-Key` |
| Manual/AI meal log | `POST /meal-log` | no — `@require_auth` | Writes `MealLog`; already honours `Idempotency-Key` |
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
