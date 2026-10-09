# Native weekly check-in (LP16-B)

Status: backend only. Stacked on LP16-A (`app/services/weekly_checkin/`).
No Flutter, no schema/migration, no provider/AI call.

```
POST /api/v1/progress/check-ins   one full check-in (Idempotency-Key required)
GET  /api/v1/progress/check-ins   last 12 full check-ins + current Istanbul week
```

| Layer | Module | Owns |
|---|---|---|
| Transport | `app/blueprints/mobile_weekly_checkin.py` (on `mobile_api`) | Bearer owner, write rate limit, `Idempotency-Key` syntax, error mapping, logs |
| Native contract | `app/services/mobile_weekly_checkin/` (`contract`, `submission`, `history`) | strict parser, `native.v1` fingerprint, wire ↔ stored mapping, write composition, history read |
| Write authority | `app/services/weekly_checkin/` (LP16-A) | owner lock, the row, `User.weight`, derived session, the single commit |
| Read rules | `app/services/progress_history` | qualifying read (`yogunluk IS NOT NULL`), weight-delta rule |

The transport is a sibling of `mobile_progress.py`, not part of it:
`tests/test_mobile_progress_summary_architecture.py` pins that module to the
Progress summary transport alone.

## 1. POST request

- Auth: `require_mobile_auth`. Owner = `g.mobile_user.id`, nothing else.
- Header `Idempotency-Key`: required, `^[A-Za-z0-9._:-]{8,64}$` (the shared
  native key syntax). Missing/invalid → 400 `INVALID_IDEMPOTENCY_KEY`.
- Body: `Content-Type: application/json`, ≤ 4096 bytes, exactly one JSON
  object with exactly these six keys:

| Field | Type | Range |
|---|---|---|
| `weight_kg` | JSON number (int or decimal), not bool/string | finite, 20–500 inclusive |
| `training_intensity` | JSON integer, not bool, not `3.0`, not `"3"` | 1–5 |
| `fatigue` | JSON integer | 1–5 |
| `sleep_quality` | JSON integer | 1–5 |
| `nutrition_adherence` | JSON integer | 1–5 |
| `progressive_overload` | string, closed wire enum | `yes` · `partial` · `no` |

The parser is new and strict (`contract.parse_request`); the legacy web parser
is never used. Nothing is defaulted (a missing metric is NOT 3), clamped or
coerced. Rejected: unknown keys (`user_id`, `note`, `coach_feedback`,
`checked_in_at`, plan ids, the legacy Turkish field names), duplicate keys,
`NaN`/`Infinity` literals, non-JSON bodies, integers too long to parse.

Stored mapping (wire tokens are never localized; the stored token never
reaches the wire): `yes → evet`, `partial → kismen`, `no → hayir`.

Server-owned, never client-supplied: `checked_in_at` (the server clock, read
after the owner lock), `analysis_day` (its Europe/Istanbul date). `note` and
`coach_feedback` are stored NULL. Deferred: note, coach feedback,
readiness/recovery/composite score, sleep hours.

## 2. POST response

One closed shape for 201 (new write) and 200 (replay):

```json
{
  "contract_version": 1,
  "checked_in_at": "2026-07-23T15:00:00+03:00",
  "analysis_day": "2026-07-23",
  "weight_kg": 78.4,
  "training_intensity": 4,
  "fatigue": 2,
  "sleep_quality": 5,
  "nutrition_adherence": 4,
  "progressive_overload": "yes"
}
```

Never exposed: DB id, user id, idempotency key, fingerprint, the stored
Turkish token, session ids, internal timestamps.

## 3. Errors

Envelope: `{"error": {"code", "message", "retryable", "request_id"}}`.

| Code | HTTP | retryable | When |
|---|---|---|---|
| `CHECKIN_INVALID_REQUEST` | 400 | false | not the closed six-field JSON object of the right JSON types |
| `CHECKIN_INVALID_VALUE` | 422 | false | well-typed value outside the contract; adds `field` (one of the six) and `reason` (`out_of_range` \| `unsupported_value`) |
| `INVALID_IDEMPOTENCY_KEY` | 400 | false | missing/invalid key |
| `IDEMPOTENCY_CONFLICT` | 409 | false | same owner + key, different intent |
| `CHECKIN_RATE_LIMITED` | 429 | true | write ceiling hit; `Retry-After` set |
| `CHECKIN_TEMPORARILY_UNAVAILABLE` | 503 | true | any write or read failure (nothing durable) |
| (blueprint) `AUTH_*` 401 / `REQUEST_TOO_LARGE` 413 | | | auth boundary / body over `MAX_CONTENT_LENGTH` |

No raw exception text reaches a body or a log.

## 4. Idempotency and replay

- Reuses `WeeklyCheckIn.idempotency_key` / `request_fingerprint` /
  `response_snapshot` and `uq_weekly_checkin_user_key (user_id, key)`.
  PostgreSQL (owner lock + unique constraint) is the final arbiter; nothing is
  process-local.
- Fingerprint = sha256 of the canonical JSON (sorted keys, no spaces) of
  `{"domain": "native.v1", <the six parsed fields>}` — key order and numeric
  spelling (`78` vs `78.0`) do not change intent. The domain separates native
  from the web's legacy fingerprint on the same columns: a key used by web
  `/checkin` conflicts (409) on native and vice versa; it never replays across
  transports.
- Same owner + key + same intent → 200 with the original stored body; no
  second row, no second `User.weight`/session write (a weight moved meanwhile
  by another write stays moved).
- Same owner + key + different intent → 409 `IDEMPOTENCY_CONFLICT`.
- Same key text under two owners → independent; a key one owner used tells
  another nothing (no existence oracle).
- A response lost after the commit is recovered by retrying the same key → 200
  replay of the committed write.
- A failed attempt (validation, 503) consumes nothing: the same key works.

## 5. Write authority

`submission.submit` composes the LP16-A primitives, provider-free:
`claim_submission` (owner `FOR UPDATE` + key lookup) → `reload_locked_owner`
→ `load_context` → server clock → `stage_full_checkin(coach_feedback=None,
note=None, checked_in_at=…)` → `commit_full_checkin` (the ONE commit, replay
snapshot included). One transaction owns the full row, `User.weight` and —
only for a complete profile — the canonical session's BMR/TDEE/target
calories. Never written: `target_weight`, TrainingPlan, NutritionPlan, Coach,
Today. The native package never constructs a `WeeklyCheckIn`.

Two additive LP16-A service changes, both unused by the web path (its SQL
trace is unchanged):

- `stage_full_checkin(..., checked_in_at=None)` — a naive-UTC server instant so
  the replay snapshot can carry the timestamp before the commit.
- `reload_locked_owner(owner)` — the owner re-read under the lock. Without it
  a writer whose principal was loaded before a concurrent commit stages
  against a stale weight; when its new weight equals that stale value the ORM
  emits no `UPDATE` and an earlier check-in's weight stays current (found by
  the PostgreSQL race suite; the web keyed `/checkin` keeps this pre-existing
  race — recorded debt).

Concurrency (same owner, different keys): serialized by the owner lock, and
the clock is read after it, so per owner commit order = `checked_in_at` order
and `User.weight` is always the weight of the latest check-in by
`created_at, id`. Both observations stay — there is no per-day or per-week
uniqueness.

## 6. Week semantics

- Application timezone Europe/Istanbul; `analysis_day` = Istanbul date of
  `checked_in_at`.
- "Current week" = Monday–Sunday in Istanbul, computed from the server's
  Istanbul today. It is display semantics only, never a uniqueness rule:
  several full check-ins per week and per day are valid writes.
- Progress keeps its rule (latest FULL check-in per Istanbul day is the
  representative point); nothing here deduplicates.

## 7. GET history

```json
{
  "contract_version": 1,
  "current_week": {"timezone": "Europe/Istanbul", "start_day": "2026-07-20",
                   "end_day": "2026-07-26", "submitted": true},
  "check_ins": [
    {"checked_in_at": "2026-07-23T12:00:00+03:00", "analysis_day": "2026-07-23",
     "weight_kg": 78.4, "weight_delta_kg": -0.6, "training_intensity": 4,
     "fatigue": 2, "sleep_quality": 5, "nutrition_adherence": 4,
     "progressive_overload": "yes"}
  ]
}
```

- Bearer-only; no owner/limit/cursor parameter (the query string is ignored).
- Last 12 FULL check-ins (`yogunluk IS NOT NULL`, via
  `progress_history.fetch_qualifying_checkins`), newest first: `created_at
  DESC, id DESC`. The id only breaks ties; it is never published.
- Weight-only `/update-weight` rows never appear.
- `weight_delta_kg`: the Progress History rule (`previous_daily_row` +
  `historical_body`): this check-in's weight minus the latest valid weight on
  an OLDER Istanbul day (same-day check-ins are never each other's prior
  point), rounded to 0.1. History applies it inside a bounded read; here, when
  the 12-row window was cut and the prior point lies past it, one bounded
  lookup finds the same point. Not derivable → `null`. Computed server-side
  only.
- `current_week.submitted` is true iff at least one FULL check-in exists in
  the current Istanbul week; weight-only rows never count. There is no
  `can_submit`.
- Cost: two bounded selects (13 rows; the week `EXISTS`-style probe) plus at
  most one bounded prior-point lookup per cut day (in practice ≤ 1).

## 8. Null semantics

`null` = unknown, never 0 or 3. In history, a stored rating outside 1–5 (or
NULL), an overload token outside `evet/kismen/hayir`, a non-positive weight
and an underivable delta are all `null`. The row still counts as a full
check-in when `yogunluk` is set.

## 9. Privacy

- Every response: `Cache-Control: no-store` (blueprint).
- Logs: `mobile_checkin event=write outcome=created|replayed|conflict
  request_id=…` and `mobile_checkin event=write_failed|history_read_failed
  error_type=<type> request_id=…` only. Never weight, metrics, body, key,
  fingerprint, user/email/sub or history values; never exception text.

## 10. Rate limit

`CHECKIN_WRITE_RATELIMIT` (env, default **`10 per minute; 60 per hour`**),
per Bearer owner, checked in the view before any work → typed 429
`CHECKIN_RATE_LIMITED`. Its own ceiling — no AI/Bedrock quota is borrowed.
Replays count against it. GET is not separately limited beyond the app
defaults; it is bounded by construction.

## 11. Non-goals

Flutter/mobile UI; AI coach feedback; note; readiness/recovery/composite
score; sleep hours; weekly/daily dedup or "can't submit"; pagination; fixing
legacy `/update-weight` (overwrite, no lock, no ORDER BY); fixing
lock-across-Bedrock or the web keyed stale-owner race; LP17/18/19.

## 12. Tests

| File | Scope |
|---|---|
| `tests/test_lp16b_native_checkin_api.py` | contract literals, parser matrix, replay/conflict, cross-transport keys, failure atomicity, no provider, log privacy, rate limit, history/delta/week, isolation (real opaque credentials) |
| `tests/test_lp16b_native_checkin_architecture.py` | transport imports/ownership, provider-free package, read-only history, primitive order, closed vocabularies, sole `WeeklyCheckIn(...)` constructor, log arguments |
| `tests/test_lp16b_native_checkin_pg.py` | real PostgreSQL: same key/same intent, same key/different intent, two keys (held + stale-equal + free-running), two owners, derived-session fault, lost response replay (CI PG list) |
