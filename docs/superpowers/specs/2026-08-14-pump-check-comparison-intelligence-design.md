# Pump Check Comparison Intelligence Design

Date: 2026-08-14

Status: approved design captured for implementation

## Objective

Add a backend-only, owner-private, explicit Pump Check comparison contract. A
caller supplies one directional baseline Pump Check and one directional current
Pump Check. The backend validates that pair, performs at most one canonical
Bedrock comparison for the pair and analysis version, persists a bounded result,
and returns it through owner-only create/read endpoints.

This is Sprint 10 PR3 only. It does not add history, automatic previous-check
selection, image URLs, Flutter changes, progress scores, heatmaps, body-fat
estimates, numeric body deltas, program rewrites, social behavior, PR4 work, or a
second AI provider.

## Prerequisites and baseline

- Mobile PR 13 is merged on the mobile main branch.
- Backend PR 207 is merged at backend base commit `dc6fda1`.
- Work is isolated on branch `sprint10-pr3-pump-check-comparison-intelligence`.
- Mandatory baseline validation discovered a pre-existing frontend-audit
  `DATABASE_URL` leak. Preparatory commit `a69c958` fixes only that harness
  ownership boundary and adds a regression.
- After that commit, all eight non-overlapping authoritative pytest shards pass:
  3,390 passed, 8 skipped, 3 deselected, and zero failures in the summed shard
  results. Collection reports 3,394 selected and 3 deselected tests.

The preparatory commit must remain explicitly identified in the final PR3 report
as a baseline-only test-harness fix made before PR3 production changes.

## Canonical authority

`PumpCheckComparison` is the only canonical persisted comparison authority.
There is exactly one row for an owner, directional baseline Pump Check,
directional current Pump Check, and comparison version.

`PumpCheckComparisonRequest` is a small request/idempotency ledger. It binds an
owner-scoped `Idempotency-Key` and semantic fingerprint to a canonical
comparison. It is not another comparison result and never owns analysis fields.

This separation provides both required invariants:

- Same key and same command returns the same canonical comparison.
- Same key and a different command returns a deterministic conflict.
- Different keys for the same directional pair/version converge on one canonical
  comparison and do not invoke Bedrock twice.
- The same textual key used by different owners remains independent.

## Data model

### PumpCheckComparison

The table contains:

- Internal integer primary key.
- Owner `user_id`, with user deletion cascade.
- `baseline_pump_check_id` and `current_pump_check_id`, both foreign keys to
  canonical Pump Checks. Deleting either source comparison input deletes the
  derived comparison; deleting a comparison never deletes either Pump Check.
- Opaque owner-facing `public_id`.
- `status`: `pending`, `analyzing`, `completed`, or `failed`.
- Nullable `comparability` while work is not complete; terminal values are
  `comparable`, `limited`, or `not_comparable`.
- Nullable strict JSON analysis payload.
- `analysis_version`, fixed to `pump-check-comparison-analysis/v1` for this PR.
- Lease fencing fields `analysis_started_at` and monotonically increasing
  `analysis_attempt`.
- Bounded internal `analysis_failure_kind`; no raw provider error is persisted.
- Creation timestamp.

Database constraints enforce:

- Unique `(user_id, baseline_pump_check_id, current_pump_check_id,
  analysis_version)`.
- Unique `(user_id, public_id)`.
- Baseline and current internal IDs must differ.
- Status, comparability, and terminal-field combinations remain internally
  coherent.

Directionality is never normalized or sorted. `(A, B)` and `(B, A)` are distinct
commands, although deterministic chronology makes only the chronological form
eligible in v1.

### PumpCheckComparisonRequest

The ledger contains:

- Internal integer primary key.
- Owner `user_id`, with user deletion cascade.
- Required bounded `idempotency_key`.
- Versioned semantic `fingerprint`.
- Foreign key to `PumpCheckComparison`, with comparison deletion cascade.
- Creation timestamp.

The table has a unique `(user_id, idempotency_key)` constraint. It contains no
analysis or comparability fields.

### Migration behavior

The additive Alembic migration creates both tables, foreign keys, checks,
indexes, and unique constraints. It follows the repository fresh-database rule:
`db.create_all()` may already have created model tables before Alembic runs, so
the migration must verify compatible tables or create them, fail closed on an
incompatible partial schema, remain idempotent, and downgrade both tables in
dependency order.

## Opaque identity and command fingerprint

Comparison public IDs use the existing HMAC-based opaque-ID pattern with a new
domain such as `axisai/mobile-pump-check-comparison/id/v1`. Internal database IDs
never appear in the mobile contract.

The idempotency fingerprint uses a separate domain,
`axisai/mobile-pump-check-comparison-create/v1`, and includes:

- Owner-facing baseline Pump Check token.
- Owner-facing current Pump Check token.
- Comparison analysis version.

The ordered inputs are not sorted. Fingerprints and keys are never logged.

## Deterministic eligibility

All deterministic checks finish before any S3 read or Bedrock invocation:

1. Both source IDs resolve through owner-scoped canonical Pump Check queries.
   Unknown, malformed, and cross-owner IDs are indistinguishable private 404s.
2. The two Pump Checks are distinct.
3. Both have canonical `captured_at` values and
   `baseline.captured_at < current.captured_at`.
4. Both have the same non-empty canonical `body_region`; v1 requires exact
   equality.
5. Both are terminal canonical single-check analyses with status `completed`,
   version `pump-check-analysis/v1`, and structurally valid stored analysis.
6. Both have canonical private S3 keys owned by the authenticated user.
7. Neither source analysis has `quality=insufficient`.

If either source has `quality=limited`, the pair may proceed, but the comparison
result is capped at `limited`; provider output claiming `comparable` is invalid.

A deterministic incompatibility returns a typed non-retryable 422 and invokes
neither S3 nor Bedrock. It does not create a canonical comparison row or request
ledger entry.

S3 object absence or corrupted bytes are checked before Bedrock after the
canonical analysis claim. Missing/corrupt canonical media becomes a bounded
non-retryable comparison failure. Transient S3 service failure is retryable with
the same idempotency key.

## Shared image normalization

PR1 accepts valid images up to 6 MB, while the Bedrock Messages image block has a
3.75 MB per-image limit. Persisted Pump Checks therefore cannot be assumed to be
provider-compatible.

The repository already has a bounded Pillow path,
`_compress_image_for_vision`, used by menu OCR and the legacy Pump Check vision
flow. PR3 will promote that algorithm into a shared vision-image utility rather
than inventing a second compressor:

- Existing inputs at or below 1.5 MB pass through with their validated media
  type, preserving the normal PR1 provider path.
- Larger inputs are decoded under the existing decompression-bomb pixel guard,
  converted to RGB JPEG, resized so the longest edge is at most 1,600 pixels,
  and quality-reduced using the existing bounded sequence.
- A strict postcondition rejects normalization if the final payload still
  exceeds 1.5 MB. Thus each provider image remains well below Bedrock's 3.75 MB
  per-image ceiling.
- The original private S3 object is never modified or replaced.
- Logs contain only input/output byte counts, dimensions, and generic failure
  kinds; they contain no object keys, IDs, fingerprints, image content, or model
  output.

The legacy menu/OCR call sites retain compatibility wrappers or imports so their
behavior and tests remain stable. Canonical PR1 analysis calls the shared
preparation function before its existing single-image adapter. The public PR1
schema, prompt, idempotency, persistence, and API response remain unchanged.

## Bedrock adapter

The existing Bedrock client is extended with one explicit two-image function.
The current single-image `_bedrock_validate_image` signature and message layout
remain intact.

The comparison message orders and labels content unambiguously:

1. Comparison prompt text.
2. Text label identifying Image A as the baseline.
3. Baseline image block.
4. Text label identifying Image B as the current image.
5. Current image block.

The adapter uses the configured existing Bedrock model, concurrency gate,
timeout/error normalization, and token ceiling. It does not add a client,
provider, fallback, or metadata to the public response. Tests inspect the real
message payload to prove image order and labels.

## Strict comparison analysis contract

The provider must return exactly these keys:

- `summary`: non-empty plain text, at most 400 characters.
- `observed_changes`: at most five plain-text items, each at most 240 characters.
- `stable_areas`: at most four items, each at most 240 characters.
- `focus_areas`: at most four items, each at most 240 characters.
- `limitations`: at most four items, each at most 240 characters.
- `comparability_reasons`: at most five items, each at most 240 characters.
- `next_check_guidance`: non-empty plain text, at most 300 characters.
- `comparability`: exactly `comparable`, `limited`, or `not_comparable`.

All keys are required and no unknown keys are accepted. Arrays may be empty.
The stored JSON contains the seven narrative/list fields. `comparability` is
promoted to the first-class comparison column and serialized at the comparison
level so there is only one public authority.

The PR1 plain-text, HTML, medical-claim, prohibited body-claim, metric, imperial
measurement, and false-precision validators apply to every comparison text
field. The comparison-specific policy additionally rejects progress scores,
body-fat estimates, circumference deltas, muscle-growth percentages, causal
claims, and medical inference. The prompt treats source context as untrusted
data and requests observation language rather than certainty.

Provider-discovered visual non-comparability is a valid `completed` comparison
with `comparability=not_comparable`, limitations, reasons, and next-check
guidance. It is not a transport failure. Structurally invalid or unsafe provider
output is a retryable bounded analysis failure and is never exposed or stored as
public analysis.

## State, lease, and generation fencing

No database transaction or row lock spans S3 or Bedrock I/O.

The synchronous POST flow is:

1. Parse and validate auth, exact JSON shape, opaque-token syntax, and the
   idempotency key, then calculate the semantic fingerprint from the ordered
   command.
2. Resolve an existing owner/key ledger entry first. A different fingerprint is
   an immediate 409 conflict. The same fingerprint reuses its mapped canonical
   comparison without creating another row.
3. For a new key, resolve both owner-scoped source Pump Checks and finish all
   deterministic eligibility checks.
4. In a short transaction, resolve or create the canonical pair and request
   ledger under database unique constraints.
5. Return an already completed canonical result without external I/O.
6. Claim eligible `pending` or retryable `failed` work, or reclaim an expired
   `analyzing` lease, by atomically setting `analyzing`, current start time, and
   incremented attempt generation. Commit. If another worker owns an unexpired
   `analyzing` lease, return the owner-visible canonical row with HTTP 200 and do
   not invoke external services.
7. The claim winner loads and normalizes the two private images, then invokes
   Bedrock outside the transaction.
8. Finalize with an owner/id/status/attempt conditional update. A stale worker
   whose generation no longer matches cannot overwrite newer state.
9. Re-read and serialize the canonical row.

Concurrent insert conflicts are handled by rollback and owner-scoped re-query.
They are not interpreted as provider failures. A loser never invokes Bedrock.

The GET endpoint is read-only. It never claims, retries, repairs, or invokes S3
or Bedrock.

## API contract

### POST `/api/v1/pump-check-comparisons`

Requires mobile bearer authentication, `Content-Type: application/json`, and a
valid `Idempotency-Key` header. The exact JSON body is:

```json
{
  baseline_pump_check_id: opaque-token,
  current_pump_check_id: opaque-token
}
```

Unknown fields, missing fields, non-string values, malformed tokens, and invalid
idempotency keys are rejected through the existing mobile error envelope.

A newly completed canonical analysis returns 201. A replay or convergence on an
existing canonical pair returns 200. The response contains:

- Opaque comparison ID.
- Opaque directional baseline and current Pump Check IDs.
- Status.
- First-class comparability when completed.
- Strict bounded analysis when completed.
- Analysis version.
- Creation timestamp.

It contains no image URL, S3 key, internal ID, idempotency key, fingerprint,
provider response, prompt, model metadata, lease field, or failure internals.

### GET `/api/v1/pump-check-comparisons/<comparison_id>`

Returns the same owner-only serializer. Unknown, malformed, and cross-owner IDs
all return the same private not-found response.

### Error taxonomy

The endpoint uses the existing mobile envelope with bounded codes:

- `PUMP_CHECK_NOT_FOUND`: private source lookup failure, 404.
- `PUMP_CHECK_COMPARISON_NOT_FOUND`: private comparison lookup failure, 404.
- `INVALID_PUMP_CHECK_COMPARISON`: malformed command, 400.
- `PUMP_CHECKS_NOT_COMPARABLE`: deterministic incompatibility or permanently
  unusable canonical media, 422 and non-retryable.
- `IDEMPOTENCY_CONFLICT`: same owner/key with a different fingerprint, 409.
- `PUMP_CHECK_COMPARISON_PROVIDER_BUSY`: the shared AI concurrency gate is
  saturated, 503 and retryable. An unexpired canonical analysis lease is not an
  error; it returns the current `analyzing` representation with HTTP 200.
- `PUMP_CHECK_COMPARISON_UNAVAILABLE`: transient S3, Bedrock, invalid provider
  output, or unreconciled persistence failure, 503 and retryable.

Messages reveal neither cross-owner existence nor sensitive internals.

## Privacy, safety, cost, and observability

- Every source, comparison, and ledger query is owner-scoped.
- S3 reads use the existing expected-owner key validation.
- Images remain private and are never returned by comparison endpoints.
- Provider calls receive only the two normalized images and bounded non-sensitive
  comparison instructions; stored PR1 narrative analysis is an eligibility
  signal and is not forwarded to avoid compounding interpretations.
- The endpoint reuses mobile auth, rate limiting, and the shared AI concurrency
  gate. Pair uniqueness and idempotency prevent duplicate model spend.
- Logs/metrics use generic outcomes, durations, status, comparability class, and
  attempt counts. They exclude raw images, base64, keys, tokens, descriptions,
  prompts, provider output, idempotency keys, and fingerprints.

## Test strategy

Implementation is test-first and covers:

- Shared image normalization: pass-through, resize/re-encode, strict byte ceiling,
  decompression-bomb guard, media type, and unchanged S3 original.
- PR1 characterization: same public schema and single-image adapter behavior;
  oversized valid uploads are normalized before Bedrock.
- Strict comparison parser, bounds, exact keys, comparability enum, source-quality
  ceiling, medical safety, body claims, and numeric false precision.
- Two-image Bedrock block order, explicit labels, configured client reuse, token
  bounds, and single-image non-regression.
- Owner isolation, private 404s, chronology, exact body region, source analysis
  version/status/quality, deterministic no-S3/no-Bedrock rejection, and visual
  `not_comparable` persistence.
- Same key/same command replay, same key/different command conflict, different
  keys/same pair convergence, reversed direction, cross-user identical keys,
  transient retry, pair uniqueness, and stale-worker fencing.
- GET read-only behavior and absence of URLs/keys/internal metadata.
- Fresh/create-all/idempotent/incompatible/downgrade migration coverage.
- Architecture guards against Flutter, history endpoints, automatic selection,
  second providers, unsafe logging, and unbounded schemas.
- Opt-in real PostgreSQL races for same-key commands, same-pair different keys,
  cross-user keys, canonical uniqueness, and stale worker finalization. CI is
  extended to run the new PostgreSQL comparison race module.

Final verification includes focused tests, migration graph/schema drift, all
authoritative pytest shards, and real PostgreSQL races when local infrastructure
is available. Lack of real local PostgreSQL proof permits only the sprint's
`READY WITH CONDITIONS` verdict, with CI coverage and the exact missing proof
recorded.

## Documentation and delivery

Update the canonical Pump Check API/design documentation, Sprint 10 plan, CI
notes, and `docs/handoff.md`. The final report must use the exact 27-section
structure required by the sprint brief and explicitly separate preparatory
commit `a69c958` from PR3 implementation commits.

Delivery remains local only: commit the clean worktree, but do not push, open a
pull request, merge, deploy, modify Flutter, or begin PR4.
