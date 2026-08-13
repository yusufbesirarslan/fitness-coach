# Sprint 10 PR1: Canonical Pump Check Design

The existing `pump_check` row remains the only persistence authority. Legacy web completion, gallery, feed, and sharing continue to use their existing integer references internally; the mobile contract is an additive owner-only adapter and never broadens feed authorization.

## Persistence capability matrix

| Concept | Classification | Final authority |
|---|---|---|
| Internal row ID | EXISTS AUTHORITATIVELY | `pump_check.id`, server-only |
| Owner | EXISTS AUTHORITATIVELY | `user_id` |
| S3 key | EXISTS AUTHORITATIVELY | nullable private `image_key` |
| Captured time | MISSING — ADDITIVE STORAGE REQUIRED | nullable `captured_at` |
| Server created time | EXISTS AUTHORITATIVELY | `created_at` |
| Body region | MISSING — ADDITIVE STORAGE REQUIRED | nullable strict `body_region` |
| Context/environment | LEGACY / AMBIGUOUS | `location_type`; strict values for mobile, unchanged legacy values |
| Validation state | EXISTS AUTHORITATIVELY | `valid` and `fallback` for legacy validation |
| Share state | EXISTS AUTHORITATIVELY | `visibility` and `shared_friend_ids` |
| Workout score | EXISTS AUTHORITATIVELY | nullable `workout_score` |
| Analysis status | MISSING — ADDITIVE STORAGE REQUIRED | nullable canonical lifecycle string |
| Structured analysis | MISSING — ADDITIVE STORAGE REQUIRED | nullable validated JSON |
| Analysis version | MISSING — ADDITIVE STORAGE REQUIRED | nullable contract version |
| Provider/model metadata | SHOULD NOT STORE | operational configuration, not mobile/product truth |
| Prompt/schema version | MISSING — ADDITIVE STORAGE REQUIRED | `analysis_version` identifies both contract and prompt schema |
| Idempotency key | MISSING — ADDITIVE STORAGE REQUIRED | nullable user-scoped key |
| Idempotency fingerprint | MISSING — ADDITIVE STORAGE REQUIRED | nullable typed command digest |
| Opaque mobile identity | DERIVABLE SAFELY | owner-bound HMAC over owner and row ID |

Historical rows remain valid with every new field null. No body region, captured timestamp, quality, version, or structured analysis is inferred from legacy description/environment data.

## Transaction and lifecycle design

Mobile creation validates the multipart command and computes a typed fingerprint before writes. A short transaction inserts the canonical row with `pending`; `(user_id, idempotency_key)` is the database race authority. The winner conditionally changes `pending` or `failed` to `analyzing` and commits. Private S3 upload and one Bedrock call occur with no database lock held. The S3 key is persisted in a separate bounded transaction before analysis; successful strict parsing finalizes `completed`, and provider/parser/storage failure finalizes `failed`. A concurrent replay observing `analyzing` returns the same PumpCheck in progress and does not issue a second model call. A later same-key retry may atomically reclaim `failed`; a completed replay never reruns analysis.

Legacy daily uniqueness is unchanged. Mobile rows use `date_key=NULL`, so client `captured_at` never controls workout completion or the existing Istanbul-day constraint.

## Public contract and privacy

The mobile surface is limited to multipart `POST /api/v1/pump-checks` and owner-only `GET /api/v1/pump-checks/<PumpCheckId>`. Bearer authentication supplies the owner. `PumpCheckId` is a 144-bit URL-safe HMAC token in the distinct `axisai/mobile-pump-check/id/v1` domain. Unknown, malformed, and cross-owner IDs all return the same private 404.

The serializer exposes the opaque ID, captured/server timestamps, body region, environment, description, analysis status/version/data, and a one-hour presigned image URL generated only after owner validation. It never exposes the integer row ID, S3 key, bucket, provider payload, model details, or sharing internals. Feed visibility remains governed by the legacy feed service and does not authorize the private mobile endpoint.

## Structured analysis boundary

The canonical version is `pump-check-analysis/v1`. Required analysis keys are `summary`, `observations`, `strengths`, `focus_areas`, `limitations`, `next_check_guidance`, and `quality`; quality is one of `sufficient`, `limited`, or `insufficient`. Unknown keys, malformed JSON, HTML, excessive values, medical diagnoses, and image-only numeric composition or measurement claims are rejected. User context is placed in a labeled untrusted-data block and cannot alter privileged instructions. Only image bytes plus bounded region/environment/description are sent to Bedrock; no account identifiers or unrelated history are included.

## Threat model

- P0: cross-user row/media access. Mitigated by owner-scoped lookup, owner-bound token, private 404, and S3 owner-segment validation.
- P1: duplicate expensive writes/analysis. Mitigated by database uniqueness, semantic conflict checks, conditional lifecycle claims, rate limits, and one model call per claim.
- P1: unsafe provider output. Mitigated by exact schema/type/bounds/plain-text/safety validation before persistence.
- P1: upload resource abuse. Mitigated by authentication, byte/MIME/decoded-format/pixel bounds, request rate limiting, and no heavy preprocessing.
- P1: prompt injection and sensitive logs. Mitigated by untrusted structured context and event-only logging without IDs, keys, URLs, descriptions, prompts, responses, or analysis.
- P2: stale presigned URL sharing. URLs are bounded capabilities; they expire and are never accepted as API authority.

No unresolved P0 or P1 is acceptable. Real PostgreSQL migration drift and race evidence remains an explicit review condition when a local PostgreSQL test URL is unavailable.
