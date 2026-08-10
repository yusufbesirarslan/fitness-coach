# Sprint 9 PR2A Mobile Food Discovery and LogFood Design

## Status and scope

This design continues the existing `sprint9-pr2a-mobile-food-discovery-logfood`
worktree. It does not restart or replace the handoff. The Flutter repository is
read-only, and this work does not begin PR2B, PR3, deployment, or publication.

PR2A adds mobile-authenticated food search, serving discovery, barcode
discovery, and one canonical food-log write boundary. Search, serving, barcode,
and any future menu adapter are discovery sources only. Every mobile food write
converges on `POST /api/v1/nutrition/logs` and persists only to `MealLog`.

The write boundary supports two explicit command kinds:

- `provider_backed`: the client identifies a FatSecret food, serving, quantity,
  canonical slot, and bounded discovery source. The server resolves nutrition.
- `manual`: the client supplies a description and nutrition snapshot. It cannot
  carry provider identity, serving identity, or quantity.

Menu-derived logging is not part of either command. Menu remains behind an
independent security gate.

## Handoff and baseline rules

The worktree started at `78e9d3c`, which remains current `origin/main`. The only
inherited untracked files were `batches.json` and
`tests/test_food_discovery_characterization.py`; no production edits were
present. Nothing inherited may be discarded, reset, restored, cleaned, or
stashed.

Before production extraction:

1. Correct two characterization fixtures to reflect baseline truth:
   `meal_type` becomes the actual legacy field `meal_name`, and the unknown-mass
   example uses a description with no key in `_PORTION_WEIGHTS` instead of
   `dilim`, which the current matrix maps to 30 g.
2. Run the characterization file against unchanged production code. All cases
   must pass.
3. Run the complete six-batch baseline from `batches.json`. The partition has
   151 unique entries and exactly covers the 151 pre-PR2A `test_*.py` files.
4. Report files, passes, skips/deselections, and failures for every batch, plus
   focused auth, mobile nutrition, FatSecret, food, serving, barcode, MealLog,
   diary, menu, nutrition, time, idempotency, ownership, and migration checks.

The inaccessible inherited `.pytest_cache` is a generated artifact. Tests use
`-p no:cacheprovider` so it need not be deleted or altered.

## Architecture

### Versioned route layer

All new routes attach to the existing `mobile_api` blueprint. They inherit the
`/api/v1` prefix, `Cache-Control: no-store`, feature gate, rate-limit handling,
and mobile error envelope. Every route uses `@require_mobile_auth`; the user is
always `g.mobile_user`.

Routes contain request parsing, bounded validation, orchestration, and response
selection only. They do not duplicate provider parsing, nutrition calculations,
identity generation, or persistence rules.

### Boundary-first provider extraction

FatSecret network fetching is separated from legacy transformation. A raw
fetch helper returns provider data without converting absent metric mass to a
portion-matrix estimate or zero. The existing legacy helpers call that raw
fetch helper and then retain their existing parsing, estimation, normalization,
and zero behavior.

The mobile adapter consumes the raw data and builds a normalized mobile model.
It preserves provider food and serving IDs, hides raw payloads, and represents
unknown mass and underivable per-100 g nutrition as `null`.

This boundary prevents the mobile contract from inheriting legacy ambiguity
without changing any live web payload.

### Focused services

The implementation uses focused units following existing repository patterns:

- provider fetch functions: bounded FatSecret calls and fail-soft provider
  result classification;
- mobile discovery projection: pure normalization into mobile food and serving
  objects, including nullable mass/per-100 g values;
- LogFood command validation: an explicit discriminated union with forbidden
  field checks;
- LogFood orchestration: provider resolution before persistence, manual
  snapshot validation, server-owned ledger construction, and idempotent commit;
- existing mobile nutrition identity and serialization: opaque `DiaryItemId`
  and the canonical logged-meal response representation.

No mobile-only nutrition table is introduced.

## Discovery contracts

### Search

`GET /api/v1/nutrition/foods/search?q=<query>`

- Bearer authentication is required.
- The trimmed query has explicit minimum and maximum lengths.
- Result count is bounded.
- Results preserve `provider="fatsecret"` and provider food IDs.
- Raw FatSecret payloads, secrets, internal exceptions, and cache internals are
  never returned.
- Provider unavailable/malformed states use deterministic mobile errors.
- The route performs no database mutation and never writes `MealLog`.

### Servings

`GET /api/v1/nutrition/foods/fatsecret/<food_id>/servings`

- The provider is fixed by the route and unsupported providers cannot be
  smuggled in.
- Food and serving identities remain distinct strings.
- Every serving contains a description, per-serving nutrition, nullable metric
  mass, and nullable per-100 g nutrition.
- A provider-declared measured zero remains zero. Missing or underivable mass
  remains `null`; it is never estimated or fabricated as zero.
- The route performs no database mutation.

### Barcode

`GET /api/v1/nutrition/foods/barcode?code=<barcode>`

- Barcode validation runs before cache or provider work and preserves leading
  zeroes.
- Existing cache rows may be read. A cache miss may call the provider, but the
  mobile discovery request does not populate or mutate the cache.
- Not-found, malformed, and provider-unavailable states are distinct and
  deterministic.
- The response preserves provider food and serving identities and uses the same
  nullable serving semantics as the serving endpoint.
- The barcode value is never included in application, access, diagnostic, or
  exception logs. Tests capture all relevant logs and prove its absence.
- Lookup never logs food consumption and performs no database mutation.

## Canonical LogFood contract

`POST /api/v1/nutrition/logs`

The request requires a valid `Idempotency-Key` and one command object. Unknown
fields and fields belonging to the other variant are rejected rather than
ignored.

### Provider-backed command

```json
{
  "kind": "provider_backed",
  "provider": "fatsecret",
  "food_id": "33691",
  "serving_id": "5501",
  "quantity": 1.5,
  "slot": "kahvalti",
  "discovery_source": "search"
}
```

`quantity` is a serving multiplier. It must be finite, positive, and bounded;
zero is invalid. `slot` is one of `kahvalti`, `ogle`, `aksam`, or `ara_ogun`.
`discovery_source` is `search` or `barcode`. The server persists `source` as
`search` or `barcode` respectively; `search` is added to the mobile serializer's
bounded known-source vocabulary. The request may not contain nutrition, manual
description, barcode value, or arbitrary source.

The server resolves the exact provider food and serving before opening the
bounded persistence transaction. Provider calories and macros are authoritative
and scaled by quantity. The client cannot submit or override them.

### Manual command

```json
{
  "kind": "manual",
  "description": "Ev yapımı yemek",
  "slot": "ogle",
  "nutrition": {
    "energy_kcal": 420,
    "protein_g": 25,
    "carbohydrate_g": 40,
    "fat_g": 14
  }
}
```

The description is required, trimmed, and bounded. Nutrition values are an
explicit user-supplied snapshot. Each value must be numeric, finite,
non-negative, and within the existing `MealLog`/nutrition-pipeline bounds.
Manual commands may not contain provider, food, serving, barcode, discovery
source, or quantity fields. Serving-multiplier semantics are not invented for
manual logging.

### Shared server authority and persistence

Both variants derive the user from mobile authentication, translate the stable
slot token to the canonical stored meal label, assign a bounded source, derive
the Istanbul diary day, and assign the timestamp on the server. They write
exactly one canonical `MealLog` row and no `CustomMeal`, diary-builder, or
mobile-specific row.

Both return the same logged-meal shape: opaque owner-bound `DiaryItemId`, stable
slot token, server-owned source, offset-aware timestamp, server day, description,
and server-applied nutrition. The opaque identity reuses
`mobile_nutrition.diary_entry_id`; the write response and subsequent
`GET /api/v1/nutrition/diary/today` must identify the same row consistently.

## Idempotency evidence gate

The existing `app/services/meal_idempotency.py` remains the only idempotency
implementation. Before any schema change, Phase 2/3 must establish, in order:

1. whether the current service persists request semantics or response metadata;
2. whether an existing `MealLog` row contains enough canonical data to
   reconstruct the exact provider-backed or manual command fingerprint;
3. whether any existing unused/general-purpose metadata field is semantically
   correct for this purpose rather than merely technically writable;
4. whether conflict detection can be made persistent, race-safe, and limited to
   the new mobile boundary without a migration.

The required behavior is:

- same user + same key + same canonical command: replay the winner and return
  the same `DiaryItemId`;
- same user + same key + materially different command or payload: deterministic
  conflict;
- different users + same key: independent writes;
- concurrent duplicates: one row, one winner, consistent response.

An in-memory map, process-local cache, payload comparison that cannot be
reconstructed after restart, overloading `source`, `photo_key`, description, or
another unrelated field, and encoding semantics into the client key are not
safe solutions.

No column, table, index, constraint, or migration is approved by this design.
If the existing schema cannot satisfy the behavior, implementation pauses
before schema edits and reports: proof of insufficiency, rejected alternatives,
the exact proposed column/index/constraint, legacy-writer impact, existing-row
null semantics, replay behavior, rollback, Alembic/schema-drift effects, and
PostgreSQL concurrency implications. Migration work proceeds only after a new
explicit approval.

## Error behavior

Every error uses the existing mobile JSON envelope with a stable code, safe
message, retryability flag, and request ID. There are no redirects or raw
exceptions.

- malformed, mixed, unknown-field, invalid slot, invalid quantity, invalid
  manual nutrition, and unsupported provider requests return deterministic
  `400` errors;
- missing/invalid Bearer credentials return the existing mobile `401` envelope;
- not-found provider food/serving/barcode uses deterministic not-found errors;
- same-key/different-command returns a deterministic `409` conflict response;
- provider/storage unavailability returns a bounded retryable error without
  leaking provider payloads, credentials, barcode values, nutrition content, or
  database details.

## Menu security gate

Manual logging does not make menu analysis safe or in scope. Before adding any
mobile menu route, audit URL parsing, redirects, DNS/IP resolution, localhost,
private/link-local ranges, timeouts, response size, scrape and prompt bounds,
capacity controls, rate limits, duplicate expensive requests, and logs.

The result is exactly one of:

- `SHIPPED`, with evidence that the existing architecture safely enforces the
  gate; or
- `DEFERRED — SECURITY PREREQUISITE`, with no weak partial endpoint.

Menu discovery never owns persistence. A future confirmed menu result would
still require a separate explicit command design; it is not silently treated as
manual or provider-certified nutrition in PR2A.

## Testing and compatibility

All production changes follow red-green-refactor. Contract tests cover auth,
bounds, unknown fields, provider identity, provider failures, null versus zero,
no discovery persistence, barcode privacy, server-owned nutrition, manual
validation, mixed-payload rejection, one-row persistence, diary reconciliation,
opaque identity, and every idempotency dimension.

Architecture guards must first prove their target file set is non-empty, then
prove: every mobile nutrition route uses mobile auth; only one mobile LogFood
write route exists; discovery routes do not persist; route code does not
duplicate nutrition math; no second idempotency implementation exists; provider
macros cannot be client-authored; raw database IDs and provider secrets are not
exposed; and unknown mass/per-100 g never becomes zero at the mobile boundary.

Legacy characterization tests remain permanently separate from new mobile
contract tests. The existing `/api/food/*`, `/api/food/barcode/add`,
`/meal-log`, `/api/diary/*`, menu, web auth, and CSRF behavior remain unchanged.
After extraction, the corrected characterization file and all focused legacy
suites run again.

PostgreSQL concurrency tests run only when a local PostgreSQL test database is
available. Lack of local PostgreSQL is reported honestly and never represented
as executed coverage.

## Completion boundary

PR2A stops when backend implementation, documentation, local commits, complete
baseline-to-final validation, security/performance review, and the required
handoff report are complete. Nothing is pushed, merged, deployed, or changed in
Flutter, and PR2B does not start.
