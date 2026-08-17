# Canonical Mobile Pump Check History — Sprint 10 PR4A design

Status: design for the bounded backend prerequisite of Sprint 10 PR4.
Scope: backend only. No Flutter, no mobile UI, no comparison intelligence.

## 1. Why this exists

The canonical mobile Pump Check API (Sprint 10 PR1) exposes exactly two
surfaces:

- `POST /api/v1/pump-checks` — create one check
- `GET /api/v1/pump-checks/<PumpCheckId>` — read one check in full

There is no owner-private collection read. A native client that wants to show
"my Pump Checks" has to already know every id it wants, which is impossible for
a first launch. PR4B (mobile history + explicit baseline selection) cannot be
built on that. PR4A adds the missing read surface and nothing else.

## 2. Endpoint

```
GET /api/v1/pump-checks?limit=<int>&cursor=<opaque>
```

Registered on the existing `mobile_api` blueprint, so it inherits the single
`/api/v1` surface, the single `no-store` policy, the single 429 handler, the
single unhandled-failure envelope and the `MOBILE_AUTH_ENABLED` gate. It is
added to the approved-route allow-list in
`tests/test_mobile_auth_feature_gate.py`.

## 3. Authorization and owner scope

`@require_mobile_auth` — the same Bearer path every other mobile route uses. No
second auth mechanism.

Ownership is **structural, not checked**: the query is built from
`g.mobile_user.id`. The request cannot express a `user_id`, and neither can the
cursor (see §6). There is no code path in which a caller supplies the scope, so
cross-user disclosure is not a check that can be forgotten — it is a filter that
is always present.

## 4. Source of truth

Canonical `PumpCheck` rows owned by the authenticated user. Nothing else.

History is **not** derived from Feed, from `PumpCheckComparison`, from the
comparison request ledger, from logs or from cached sessions. Feed answers "who
may see this socially"; it never changes who owns a row. A Pump Check shared to
Feed by user A stays in A's history and never enters B's, because visibility is
not consulted at all — the filter is `user_id == <authenticated user>`.

## 5. Ordering

```
ORDER BY captured_at DESC, id DESC
```

`captured_at` is the user-facing chronology: it is when the photo was taken, which
is what a history screen means by "when". `created_at` is a server write time and
can differ (a check captured at 07:00 may be uploaded at 21:00).

`captured_at` alone is **not** deterministic — two checks can carry the same
capture timestamp (the mobile create path is idempotency-keyed, not day-keyed, so
a user can legitimately post several checks with one timestamp). The tie-break is
the internal primary key `PumpCheck.id`, descending.

`id` is a stable, unique, monotonic, already-indexed server value. It is used
**only** for ordering and inside the signed cursor; it is never serialized into a
response. Using a column for pagination does not make it public API (§6).

## 6. Cursor

Keyset (seek) pagination, not offset. Offset pagination re-scans and can skip or
duplicate rows when the underlying set changes; keyset cannot.

### Encoding

```
<base64url(payload)>.<base64url(tag)>
payload = "v1:<captured_at ISO-8601 microseconds>:<id>"
tag     = HMAC-SHA256(subkey, "<user_id>\0<payload>")[:18]
subkey  = HMAC-SHA256(SECRET_KEY, b"axisai/mobile-pump-check/history-cursor/v1")
```

Same construction as the other canonical mobile identity tokens
(`mobile_pump_checks.identity`, `mobile_nutrition.identity`): a versioned domain
string, a `SECRET_KEY`-derived subkey, base64url, `hmac.compare_digest`.

### Properties

- **Opaque.** The client never parses it, never constructs it, and needs no
  knowledge of `id`, of SQL ordering, or of the tie-break. It is echoed back
  verbatim.
- **Owner-bound.** `user_id` is in the MAC message but not in the payload. A
  cursor minted for A fails verification under B — so cursor reuse across users
  is rejected at the boundary *and* would have been harmless anyway, because the
  owner filter comes from the token, never from the cursor. Two independent
  defences, and the authoritative one is the filter.
- **Tamper-evident.** Editing the timestamp or the id invalidates the tag.
- **Versioned.** The `v1:` prefix is inside the signed payload. An unsupported
  version is rejected, not guessed.

### Validation

Every cursor failure is one deterministic outcome:

```
400 INVALID_PAGE_CURSOR   retryable=False
```

for: wrong length (raw input is length-capped before any decoding), non-ASCII,
bad base64, missing separator, wrong field count, unsupported version,
unparseable timestamp, non-integer id, and bad or absent MAC. A user-supplied
cursor must never raise a 500 and never leak why it failed.

A *validly signed* cursor whose row has since been deleted still works: the
keyset predicate compares values, not row existence, so the next page is still
correct. That is a feature of keyset pagination and is tested.

## 7. Page size

- `limit` absent → **20**
- `limit` present → must be an integer in **[1, 50]**
- anything else (non-integer, `0`, negative, `51`, `"abc"`) → `400
  INVALID_PAGE_SIZE`, `retryable=False`

Rejected rather than clamped. A silently clamped limit lets a client ask for 500,
receive 50 and conclude the history has 50 rows. This boundary already prefers
rejecting a malformed request over guessing at it (`mobile_nutrition` ignores
client-supplied `day`/`timezone`; `plan_mutation` rejects out-of-range
prescriptions instead of trimming them).

## 8. Legacy rows without `captured_at` — EXCLUDED

Sprint 10 PR1 deliberately left `captured_at` nullable so that historical web
Pump Check rows were not backfilled with fabricated capture times. Those rows are
**excluded** from canonical mobile history:

```sql
WHERE user_id = ? AND captured_at IS NOT NULL
```

Reasons:

1. The chronology of this endpoint *is* `captured_at`. A row with no
   `captured_at` has no position in it. Putting it in a "legacy bucket" would
   mean ordering it by something else and presenting that as capture order —
   exactly the fabricated chronology PR1 refused.
2. `created_at` is **not** used as a stand-in. That would publish a write time as
   a capture time.
3. It matches the established canonical-mobile boundary. PR3 already makes these
   rows structurally ineligible for the canonical comparison surface
   (`sources lack canonical capture times`). Listing rows in the history that
   feeds PR4B's baseline selector, when PR3 can never accept them as a baseline,
   would surface dead ends.
4. Nothing is lost. Legacy rows remain readable through the unchanged
   single-item `GET /api/v1/pump-checks/<id>` and the unchanged legacy web
   gallery. This endpoint narrows what it *lists*, not what exists.

## 9. Item contract

```json
{
  "id": "0hBv1Yq8Tn2mKQZ6r_pXcA",
  "captured_at": "2026-08-13T05:00:00Z",
  "body_region": "upper_body",
  "analysis_status": "completed",
  "analysis_quality": "sufficient"
}
```

- `id` — the opaque `public_id`, the same identifier the single-item GET accepts.
- `captured_at` — ISO-8601 UTC, `Z`-suffixed, matching the single-item
  serializer.
- `body_region` — the comparability axis PR3 enforces; PR4B needs it to know
  which items can be compared with which.
- `analysis_status` — `pending` / `analyzing` / `completed` / `failed`, or
  `unavailable` when the row carries none, matching the single-item serializer's
  fallback.
- `analysis_quality` — the `quality` field of a completed analysis, else `null`.
  Read from the same row (no extra query, no provider call). PR3 refuses sources
  whose quality is blocking, so without it PR4B could only discover an
  unusable baseline by attempting a comparison and being refused.

Deliberately **absent**: the structured `analysis` body, raw provider output,
`image_key`, presigned URLs, `description`, `environment`, `created_at`, raw
database ids, and every comparison field. The single-item GET remains the
detailed surface, and `environment` is omitted as not-yet-needed — the envelope
evolves additively.

## 10. Envelope

```json
{
  "pump_checks": [ ... ],
  "next_cursor": "…"  | null,
  "has_more": true | false
}
```

`next_cursor` is a string when more rows exist and `null` at the end of the list;
`has_more` is the same fact as a boolean so a client need not treat `null` as
control flow. Empty history is `200` with `[]`, `null`, `false` — not `404`.

Presence of a further page is decided by reading `limit + 1` rows and returning
`limit` (the Feed keyset convention), never by a `COUNT(*)`.

### Snapshot semantics

Pages are read independently; there is no cross-request snapshot and one is not
claimed. Keyset pagination gives the guarantee that actually matters: **no row is
skipped or duplicated because of pagination itself.** A check created *during*
paging with a `captured_at` newer than the first page's cursor simply is not seen
until the client restarts from page one — it sorts above the window already
passed. This is documented rather than papered over.

## 11. No side effects

`GET` is a pure read model. It must not call Bedrock, rerun analysis, presign
media, read image bytes, create or lease a `PumpCheckComparison`, write a request
ledger row, or mutate any row. One bounded `SELECT` per page.

Notably it does **not** presign images. A 20-row page would otherwise mint 20
presigned URLs to private objects and trigger 20 private media loads. The client
calls the single-item GET when a user opens a specific item. No thumbnail
infrastructure is built here.

## 12. No history intelligence

No progress score, trend, similarity, comparison-readiness, best-baseline or
previous-check recommendation. PR4B allows explicit baseline selection; PR3's
comparison POST stays the only comparison authority.

## 13. Query and index

```sql
SELECT public_id, captured_at, body_region, analysis_status, analysis, id
FROM pump_check
WHERE user_id = ?
  AND captured_at IS NOT NULL
  [AND (captured_at, id) < (?, ?)]
ORDER BY captured_at DESC, id DESC
LIMIT ?
```

A column projection, not an entity load — so no relationship (`user`, `likes`,
`comments`) can lazy-load and turn one page into N queries. Exactly one query per
page regardless of page size.

Existing `pump_check` indexes are `user_id`, `visibility`, `date_key`,
`ix_pump_check_user_created (user_id, created_at)`, and the uniqueness
constraints on `(user_id, date_key)`, `(user_id, idempotency_key)`,
`(user_id, public_id)`. **None of them can order by `captured_at`.** Every page
would scan all of the user's rows and sort them, on the canonical read path that
a history screen calls repeatedly and that grows without bound.

So PR4A adds the smallest index that serves it:

```
ix_pump_check_user_captured (user_id, captured_at DESC, id DESC)
```

Additive: one new index, no data rewrite, no column change, no comparison table
change, based on the current single Alembic head `fa1b2c3d4e5f`. The migration is
re-runnable (guarded by an inspector check) because a fresh database runs
`db.create_all()` — which already builds the index from the model — before
Alembic replays the chain.

## 14. Out of scope

Flutter and `axisai-mobile`; mobile history UI; baseline selector; comparison UI;
comparison cleanup; the orphan pending-row reaper; thumbnails; trends; any change
to PR1 create/read semantics or PR3 comparison semantics.
