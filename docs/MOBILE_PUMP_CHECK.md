# Mobile Pump Check API — Sprint 10 PR1

All routes require Bearer authentication and return Cache-Control: no-store.
Ownership always comes from the credential; cookie authentication and payload
owner fields are not accepted.

## Create

POST /api/v1/pump-checks uses multipart/form-data and requires Idempotency-Key
(8–64 characters from A-Z, a-z, 0-9, dot, underscore, colon, or hyphen).

Parts:

- image: JPEG, PNG, or WebP; maximum 6,000,000 bytes; decoded format must match
  declared MIME and remain within 40 million pixels.
- body_region: full_body, upper_body, lower_body, back, arms, or legs.
- environment: gym, home, outdoor, or other.
- description: optional plain context, maximum 200 characters.
- captured_at: required UTC RFC 3339 ending in Z; at most ten minutes in the
  future and at most 365 days old.

Success is 201 for creation and 200 for replay. Both return one pump_check with:
opaque id; captured_at and created_at; body_region; environment; description;
one-hour image_url; analysis_status; analysis_version; and analysis.

Analysis version is pump-check-analysis/v1. Its exact fields are summary,
observations, strengths, focus_areas, limitations, next_check_guidance, and
quality. Quality is sufficient, limited, or insufficient. Legacy rows serialize
status unavailable and null version/analysis/canonical fields rather than
fabricating historical truth.

## Read

GET /api/v1/pump-checks/<PumpCheckId> returns the same serializer. GET does not
run Bedrock or mutate status. Unknown, malformed, and another owner's token all
return the same 404 PUMP_CHECK_NOT_FOUND. Feed visibility never authorizes this
owner-only endpoint.

## Errors and retry

| Code | HTTP | Retry/action |
|---|---:|---|
| INVALID_IDEMPOTENCY_KEY | 400 | Correct or generate a key |
| INVALID_PUMP_CHECK_IMAGE | 400 | Correct image |
| INVALID_PUMP_CHECK | 400 | Correct fields/timestamp |
| IDEMPOTENCY_CONFLICT | 409 | Do not replay; key belongs to different semantics |
| PUMP_CHECK_NOT_FOUND | 404 | Treat as private missing |
| PUMP_CHECK_STORAGE_UNAVAILABLE | 503 | Retry with same key |
| PUMP_CHECK_PROVIDER_UNAVAILABLE | 503 | Retry with same key |
| PUMP_CHECK_ANALYSIS_INVALID | 503 | Retry with same key |
| PUMP_CHECK_PROVIDER_BUSY | 503 | Honor Retry-After; retry with same key |
| PUMP_CHECK_PERSISTENCE_UNAVAILABLE | 503 | Fetch/retry same key; analysis is not automatically duplicated |
| AUTH_RATE_LIMITED | 429 | Honor Retry-After; retry with same key |

The semantic fingerprint includes image SHA-256, normalized region,
environment, description, and canonical captured timestamp in domain
axisai/mobile-pump-check-create/v1. Multipart boundaries, filename, header
order, signed URLs, and raw JSON encoding are excluded.

A provider, storage, or invalid-output failure keeps one row and status failed.
Same-key retry reuses and reclaims it. An ambiguous persistence failure after a
provider result is not reanalyzed automatically. Completed replay never
reuploads or reruns Bedrock. A concurrent request observing analyzing returns
that same canonical state; an interrupted attempt becomes reclaimable after its
bounded lease, with generation-aware finalization.

## Privacy and PR2 boundary

PumpCheckId is a persisted 144-bit URL-safe random owner-bound HMAC in domain
axisai/mobile-pump-check/id/v1, indexed separately from database, feed, and S3 identities.
Images remain private SSE-S3 objects. Presigned URLs are temporary capability,
not authority, and are issued only after owner validation.

Logs exclude IDs, keys, bucket, URLs, image bytes, descriptions, analysis,
prompts, provider responses, and tokens.

PR2 may rely on only this contract after PR1 is reviewed, CI-green, and merged.
Retention is deferred; the owner-private history collection landed in PR4A and
is documented at the end of this file. Reanalysis, feed expansion,
notifications, body-fat estimates, heatmaps, and automatic program rewriting are
excluded.

# Mobile Pump Check Comparison API — Sprint 10 PR3

A comparison interprets an explicitly ordered PAIR of canonical Pump Checks
with one bounded two-image Bedrock call. It is a separate owner-private
authority: PR1's single-check routes, payloads, schema, and prompt are
unchanged, and no comparison field is added to `pump_check`.

## Create

POST /api/v1/pump-check-comparisons requires Bearer authentication,
`Content-Type: application/json`, and the same Idempotency-Key grammar as PR1.
The body is exactly two keys; unknown fields, missing fields, non-strings, and
malformed tokens are rejected:

```json
{
  "baseline_pump_check_id": "<PumpCheckId>",
  "current_pump_check_id": "<PumpCheckId>"
}
```

The pair is DIRECTIONAL and is never sorted: `(A, B)` and `(B, A)` are
different comparisons. Success is 201 when this request produced the canonical
comparison and 200 for replay or convergence on an existing one. Both return
one `pump_check_comparison` with exactly: `id`; `baseline_pump_check_id`;
`current_pump_check_id`; `status`; `comparability`; `analysis`;
`analysis_version`; and `created_at`.

Analysis version is `pump-check-comparison-analysis/v1`. Its stored fields are
summary, observed_changes, stable_areas, focus_areas, limitations,
comparability_reasons, and next_check_guidance. `comparability` is promoted to
a first-class column — `comparable`, `limited`, or `not_comparable` — so there
is exactly one public authority for it. A provider finding the images visually
non-comparable is a valid COMPLETED comparison with
`comparability=not_comparable`, not a transport failure.

## Deterministic eligibility

Every rule below finishes before any S3 read or Bedrock call, and a failure
creates neither a comparison nor a ledger row:

1. Both tokens resolve through owner-scoped canonical queries.
2. The two Pump Checks are distinct.
3. Both carry `captured_at` and `baseline.captured_at < current.captured_at`.
4. Both carry the same non-empty `body_region` (exact equality in v1).
5. Both are `completed` `pump-check-analysis/v1` rows whose stored analysis
   still parses, and are `valid`.
6. Both carry a private S3 key owned by the authenticated user.
7. Neither source analysis has `quality=insufficient`.

If either source is `quality=limited` the pair proceeds but the result is
CAPPED at `limited`; provider output claiming `comparable` is invalid output.

## Read

GET /api/v1/pump-check-comparisons/<comparison_id> returns the same serializer.
GET is strictly read-only: it never claims, retries, repairs, or calls S3 or
Bedrock. Unknown, malformed, and another owner's token all return the same 404.

## Errors and retry

| Code | HTTP | Retry/action |
|---|---:|---|
| INVALID_IDEMPOTENCY_KEY | 400 | Correct or generate a key |
| INVALID_PUMP_CHECK_COMPARISON | 400 | Correct the two-key body |
| PUMP_CHECK_NOT_FOUND | 404 | Treat as private missing source |
| PUMP_CHECK_COMPARISON_NOT_FOUND | 404 | Treat as private missing comparison |
| PUMP_CHECKS_NOT_COMPARABLE | 422 | Do NOT retry; pick a different pair |
| IDEMPOTENCY_CONFLICT | 409 | Do not replay; key belongs to different semantics |
| PUMP_CHECK_COMPARISON_PROVIDER_BUSY | 503 | Honor Retry-After; retry same key |
| PUMP_CHECK_COMPARISON_UNAVAILABLE | 503 | Retry with same key |
| AUTH_RATE_LIMITED | 429 | Honor Retry-After; retry with same key |

422 covers both a deterministic rule failure and permanently unusable canonical
media (undecodable bytes); neither is reclaimable. Transient storage, provider,
invalid provider output, and unreconciled persistence failures are 503 and are
reclaimable with the same key.

## Idempotency, convergence, and leases

The request ledger maps owner + Idempotency-Key to one canonical comparison.
The semantic fingerprint is the ordered baseline token, ordered current token,
and comparison analysis version in domain
`axisai/mobile-pump-check-comparison-create/v1`; it is never sorted or logged.
Reusing a key with a different fingerprint is 409.

Uniqueness on (owner, baseline, current, analysis_version) means two different
keys for the same directional pair CONVERGE on one row and one model call.
Work is claimed by atomically moving `pending`, a reclaimable `failed`, or an
expired `analyzing` lease to `analyzing` with an incremented attempt
generation. An unexpired lease held by another worker returns that canonical
`analyzing` representation with HTTP 200 — it is not an error. Finalization is
conditional on owner, id, status, and attempt, so a stale generation can never
overwrite a newer result. No transaction or row lock spans S3 or Bedrock I/O.

## Privacy and exclusions

Comparison IDs are owner-bound 144-bit URL-safe HMAC tokens in domain
`axisai/mobile-pump-check-comparison/id/v1`. Responses carry NO image URL, S3
key, internal ID, idempotency key, fingerprint, provider response, prompt,
model metadata, lease field, or failure internals.

The original S3 objects are read but never modified, replaced, or re-uploaded;
each provider image is normalized in memory to at most 1,500,000 bytes and a
1,600-pixel longest edge. The provider receives only the two normalized images,
the body region, and bounded instructions — stored PR1 narratives are an
eligibility signal and are never forwarded. Logs carry only event, status,
comparability class, attempt, and duration.

Excluded from PR3: history, automatic previous-check selection, image URLs,
Flutter/mobile client work, progress scores, heatmaps, body-fat estimates,
numeric deltas, program rewrites, social behavior, a second provider, and all
PR4 work.

# Mobile Pump Check History API — Sprint 10 PR4A

The owner-private collection read that PR1 did not have. Until PR4A a client
could create a Pump Check and fetch one by id, but could not discover its own —
so a first launch had nothing to show. This endpoint is the canonical durable
history surface and the prerequisite for PR4B's mobile history and baseline
selection.

## Read

```
GET /api/v1/pump-checks?limit=<1..50>&cursor=<opaque>
```

Bearer authenticated on the same mobile path as every other /api/v1 route.
Ownership is structural: the query is built from the authenticated principal, so
neither the query string nor the cursor can name an owner. Read-only — no
Bedrock, no reanalysis, no comparison, no presigned media, no database mutation.
One bounded SELECT per page.

```json
{
  "pump_checks": [
    {
      "id": "0hBv1Yq8Tn2mKQZ6r_pXcA",
      "captured_at": "2026-08-13T05:00:00Z",
      "body_region": "upper_body",
      "analysis_status": "completed",
      "analysis_quality": "sufficient"
    }
  ],
  "next_cursor": "gAAAAABm…",
  "has_more": true
}
```

The list is deliberately lightweight. The structured analysis body, raw provider
output, image_key, presigned URLs, description, environment, created_at, raw
database ids, and every comparison field are absent; GET
/api/v1/pump-checks/<PumpCheckId> remains the detailed surface. A history page
never mints one presigned URL per row — the client signs a single item only when
the user opens it.

analysis_quality is the quality of a terminal analysis, or null. PR3 refuses a
comparison whose source quality is blocking, so without it PR4B could only
discover an unusable baseline by attempting a comparison and being refused. It
is not history intelligence: no progress score, trend, similarity,
comparison-readiness, or recommended baseline is computed or returned.

## Ordering and paging

Chronology is captured_at DESC — when the photo was taken, not when the server
wrote the row. captured_at alone is not deterministic (several checks may share
one timestamp), so the tie-break is the internal row id, descending. That id is
used for ordering and inside the cursor only; it is never serialized.

Paging is keyset, not offset: the page reads limit + 1 rows and returns limit,
so has_more never costs a COUNT(*). next_cursor is a string while rows remain and
null at the end; has_more carries the same fact as a boolean. Empty history is
200 with an empty list, not 404.

Pages are read independently and no cross-request snapshot is claimed. Keyset
paging guarantees the property that matters — no row is skipped or duplicated
because of pagination itself. A check created mid-walk with a newer captured_at
sorts above the window already passed and appears when the client restarts.

## Cursor

The cursor is encrypted, not merely signed: a signed-but-readable cursor is
base64 a client can decode, which would publish sequential database ids as public
API semantics. Fernet over a SECRET_KEY-derived subkey in domain
axisai/mobile-pump-check/history-cursor/v1 authenticates as well as encrypts.
The owner is sealed inside and re-checked after decryption, so a cursor minted
for one user is refused for another — a second line of defence behind the owner
filter, which is the authoritative one.

Every rejection — non-string, empty, over-long, non-ASCII, inauthentic,
truncated, non-UTF-8 payload, wrong field count, unsupported version, foreign
owner, non-integer tie-break, unparseable or timezone-aware timestamp — is one
deterministic 400. A user-supplied cursor never produces a 5xx. A validly sealed
cursor whose row was since deleted still pages correctly, because the seek
compares values rather than row existence.

## Legacy rows

Rows without captured_at are excluded from this collection. PR1 deliberately left
the column nullable rather than backfilling historical web rows with invented
capture times, and this endpoint's chronology *is* captured_at: such a row has no
position in it, and created_at is not substituted as a stand-in. PR3 already
makes these rows ineligible as comparison sources, so listing them in the history
that feeds PR4B's baseline selector would surface dead ends. They remain readable
through the single-item GET and the legacy web gallery.

## Errors and retry

| Code | HTTP | Retry/action |
|---|---:|---|
| INVALID_PAGE_SIZE | 400 | Correct limit; must be an integer in 1..50 |
| INVALID_PAGE_CURSOR | 400 | Discard the cursor and restart from the first page |
| AUTH_RATE_LIMITED | 429 | Honor Retry-After |

Both 400s are retryable=False: the request is permanently invalid, and a
retryable classification would make a client loop on it. Absent limit defaults to
20; an out-of-range or non-numeric limit is rejected rather than clamped, so a
client asking for 500 is never silently served 50 and left to conclude the
history holds 50 rows.

## Database

The page runs

```sql
WHERE user_id = ? AND captured_at IS NOT NULL
  [AND (captured_at, id) < (?, ?)]
ORDER BY captured_at DESC, id DESC LIMIT ?
```

as a column projection, not an entity load, so no relationship can lazy-load and
turn one page into N queries. The seek is a row-value comparison rather than the
equivalent OR spelling because only that form is planned as an index bound.

PR4A adds ix_pump_check_user_captured (user_id, captured_at, id), migration
c1d2e3f4a5b6 on top of fa1b2c3d4e5f. No existing index could order by
captured_at, so every page previously sorted all of an owner's rows in a
temporary b-tree. Both sort keys are DESC, so an ascending composite index is
scanned backwards; a DESC index would add nothing and make the definition
dialect-sensitive. The migration is additive and re-runnable — a fresh database
builds the index from the model via db.create_all() before Alembic replays the
chain. Evidence and the PostgreSQL plan are in
docs/superpowers/specs/2026-08-17-mobile-pump-check-history-design.md.

## Boundary

Logs exclude the cursor, page contents, ids, and owner identity; the request
logger records the route template, never the query string. PR4A changes no PR1
create/read semantics and no PR3 comparison semantics. Flutter is untouched:
PR4B begins only after PR4A has its own review and merge.
