# Native Training Plan Generation Command

`POST /api/v1/training/plans` creates a user's first canonical training plan.
It requires the normal opaque mobile bearer credential and an
`Idempotency-Key` header. Browser cookies never authorize this route.

## Request contract

The JSON body must be an object containing exactly these fields:

| Field | Type | Contract |
| --- | --- | --- |
| `gun_sayisi` | integer | Canonical Training day count |
| `ekipman` | string | Canonical equipment token |
| `odak` | string | Canonical body-focus token |
| `sure` | integer | Canonical duration |
| `kardiyo_tipi` | string | Canonical cardio-type token |
| `kardiyo_gun` | integer | Canonical cardio-day count |
| `kardiyo_sure` | integer | Canonical cardio duration |
| `kardiyo_yogunluk` | string | Canonical cardio-intensity token |
| `antrenman_tarzi` | string | Canonical style token |
| `odak_hedef` | string | Canonical goal-focus token |
| `injuries` | string | At most 2,000 characters; no unsafe controls |

Integers are native JSON integers, not booleans or numeric strings. Tokens are
validated by the shared Training preference and capability authorities. Unknown,
missing, malformed, or unsupported combinations are refused before inference.

`Idempotency-Key` is an opaque 8–64 character value containing only ASCII
letters, digits, `.`, `_`, `:`, or `-`. Its content is never logged or returned.

## Success and replay

A successful request returns `201`, `Cache-Control: no-store`, the same canonical
plan projection as `GET /api/v1/training/plans/current`, and:

```text
Idempotency-Replayed: false
```

Retrying the same owner, key, and semantic request returns the same plan with
`Idempotency-Replayed: true`. Replays do not call the provider or consume its
rate/concurrency capacity. After success, `GET /api/v1/today` and the current-plan
read immediately use the new canonical row.

The key is owner-scoped. Reusing it with a different semantic request returns
`TRAINING_PLAN_IDEMPOTENCY_CONFLICT`. A user who already has a current plan gets
`TRAINING_PLAN_REPLACEMENT_REFUSED`; this command never deletes or replaces one.

## Durable state machine

```text
IN_PROGRESS -> GENERATED -> SUCCEEDED
           \-> FAILED
```

`IN_PROGRESS` owns the request fingerprint and reserved quota. `GENERATED`
contains a bounded, validated recovery candidate. `SUCCEEDED` points to the
canonical plan lineage. `FAILED` stores only a bounded public error category.
At most one active (`IN_PROGRESS` or `GENERATED`) operation exists per owner.

| Condition | HTTP | Retryable | Client action |
| --- | ---: | --- | --- |
| Invalid key/body | 400/422 | no | Correct the request |
| Unsupported preferences | 422 | no | Choose supported preferences |
| Same key, different fingerprint | 409 | no | Resolve the caller bug |
| Operation active | 409 + `Retry-After` | yes | Retry the same key/body |
| Existing current plan | 409 | no | Use current-plan reads |
| Quota exhausted | 402 | no | Follow premium policy |
| Provider unavailable | 503 | yes | Use a new key only after the recorded definitive failure |
| Provider output invalid | 422 | no | Do not retry unchanged input |
| Capacity/rate limited | 429/503 + `Retry-After` | yes | Retry the same key/body |
| Persistence unavailable | 503 | yes | Retry the same key/body |

When a response is lost or the outcome is ambiguous, retry the exact same key and
body first. A recorded failure replays without provider work. After a definitive,
retryable provider failure, use a new key for a new inference attempt; the failed
key remains a durable record of that outcome.

## Crash boundaries

- Before a claim commits: there is no operation and no quota charge.
- During provider-limit entry: a fresh claim is removed and quota is refunded.
- During inference: recovery may resume the in-progress attempt.
- After candidate staging: recovery persists `GENERATED` without inference.
- During final persistence: the plan and `SUCCEEDED` transition commit atomically.

The provider request itself has no external idempotency guarantee. A worker crash
during that request may safely re-run inference. It cannot create a partial or
duplicate canonical plan: owner locking, the active-operation constraint, and the
atomic final transaction preserve that invariant.
