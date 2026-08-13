# Pump Check Architecture

pump_check remains the sole persistence authority. New nullable fields hold
captured time, body region, lifecycle status, structured analysis, contract
version, idempotency key, and semantic fingerprint. Historical rows receive no
fabricated backfill.

Legacy web completion, gallery, feed/share, workout score, validation flags,
and Istanbul daily uniqueness remain unchanged. Legacy completion writes
date_key; mobile rows leave it null, so captured time never controls completion.

Mobile creation uses bounded persistence phases:

1. Claim owner plus idempotency key and commit.
2. Conditionally claim pending or failed to analyzing and commit.
3. Upload privately and invoke Bedrock with no database lock held; persist the
   S3 key before analysis; finalize completed or failed in a short transaction.

The claim carries a two-minute lease and monotonic attempt generation. An
interrupted worker can be reclaimed after expiry; every key/final-state write is
generation-conditional, so a stale worker cannot overwrite the new attempt.

The unique constraint, not process-local locking, is race authority. Bedrock
receives only image bytes and bounded region/environment/description. User text
is labeled untrusted JSON. Strict validation rejects unknown keys, malformed
JSON, HTML, unsafe sizes, false precision, and medical diagnoses.

Create performs bounded queries/transactions, one S3 upload, and at most one
Bedrock call. Existing SDK/model concurrency and retry controls are reused
without application retry amplification. GET performs one owner-scoped query
and optional presign; no Bedrock, S3 fetch, write, or N+1 query occurs.

Feed and mobile authorization remain separate. Shared legacy cards retain
existing feed behavior; only the owner can resolve the mobile ID and canonical
media access.
