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

The claim carries a fifteen-minute lease (well above bounded S3 plus all
configured Bedrock attempts) and monotonic attempt generation. An
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

## Comparison authority (Sprint 10 PR3)

pump_check stays the sole authority for SINGLE-check analysis. Pair analysis
lives in two new tables and never writes back to pump_check:

- `pump_check_comparison` owns the one canonical result per owner, directional
  pair, and analysis version — including first-class `comparability`.
- `pump_check_comparison_request` only maps an owner-scoped idempotency key to
  that result. It carries no analysis of its own.

Both are user-children with ON DELETE CASCADE from `user` and from BOTH source
Pump Checks. Deleting a comparison never deletes a source Pump Check. Account
erasure deletes the ledger, then the comparison, then the sources
(`app/cli.py`), so explicit ordering does not depend on FK cascade.

Migration `fa1b2c3d4e5f` is additive and fresh-database aware: it verifies an
existing compatible table or creates it, and fails closed on an incomplete or
drifted schema rather than accepting a partial one. Verification is
dialect-aware — it compares against the types reflection actually returns on
PostgreSQL and SQLite, refuses an undetermined dialect, and normalizes check
SQL without erasing meaning inside string literals or quoted identifiers.

Comparison creation reuses the PR1 phase discipline: ledger-first resolution,
short transactions, a fifteen-minute lease with monotonic attempt generation,
and generation-conditional finalization. It reads the two private originals but
performs NO upload and never modifies stored media. `pump-check-analysis/v1`
and `pump-check-comparison-analysis/v1` are separate contracts and separate
prompts; a comparison never inherits a single-check narrative.
