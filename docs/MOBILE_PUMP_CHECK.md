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

PumpCheckId is a 144-bit URL-safe owner-bound HMAC in domain
axisai/mobile-pump-check/id/v1, distinct from database, feed, and S3 identities.
Images remain private SSE-S3 objects. Presigned URLs are temporary capability,
not authority, and are issued only after owner validation.

Logs exclude IDs, keys, bucket, URLs, image bytes, descriptions, analysis,
prompts, provider responses, and tokens.

PR2 may rely on only this contract after PR1 is reviewed, CI-green, and merged.
PR3 comparison and PR4 history/retention are deferred. Reanalysis, feed
expansion, notifications, body-fat estimates, heatmaps, and automatic program
rewriting are excluded.
