# PD5 native weekly check-in contract

`GET` and `POST /api/v1/progress/check-ins?contract_version=2` opt into v2.
Omit the parameter for the [shipped v1 contract](MOBILE_WEEKLY_CHECKIN.md).
A blank, repeated or unsupported `contract_version` is a 400. Other query
parameters do not affect selection or ownership. The existing Bearer,
health-consent, account eligibility, rate and size boundaries still apply.

## Request and response

V2 uses the same closed six-field JSON request. Nutrition uses exact,
case-sensitive strings `yes`, `partly`, `no` or `not_applicable`.
The fourth token is sent by the app only when the server confirms there is
no applicable plan. It is never a selectable answer. Progressive overload
retains its separate `yes` / `partial` / `no` vocabulary and original
meaning. All four other fields keep the v1 types and ranges.

Example POST with `Content-Type: application/json`,
`Idempotency-Key: weekly-example-001` and optional
`Check-In-Week-Start: 2026-07-20`:

```json
{
  "weight_kg": 78.4,
  "training_intensity": 4,
  "fatigue": 2,
  "sleep_quality": 5,
  "nutrition_adherence": "partly",
  "progressive_overload": "partial"
}
```

For an account without a saved plan, the same request uses
`"nutrition_adherence": "not_applicable"`. V1 continues to use an integer
1–5 under that same field on the unversioned URL. V2 POST returns 201 for
the first commit and 200 with the same stored body for a replay:

```json
{
  "contract_version": 2,
  "check_in_id": 42,
  "checked_in_at": "2026-07-23T15:00:00+03:00",
  "analysis_day": "2026-07-23",
  "weight_kg": 78.4,
  "training_intensity": 4,
  "fatigue": 2,
  "sleep_quality": 5,
  "nutrition_adherence": "partly",
  "progressive_overload": "partial"
}
```

Example v2 GET response:

```json
{
  "contract_version": 2,
  "nutrition_applicability": "applicable",
  "current_week": {
    "timezone": "Europe/Istanbul",
    "start_day": "2026-07-20",
    "end_day": "2026-07-26",
    "submitted": true
  },
  "check_ins": [
    {
      "check_in_id": 42,
      "checked_in_at": "2026-07-23T15:00:00+03:00",
      "analysis_day": "2026-07-23",
      "weight_kg": 78.4,
      "weight_delta_kg": -0.2,
      "training_intensity": 4,
      "fatigue": 2,
      "sleep_quality": 5,
      "nutrition_adherence_scale": "ternary_v2",
      "nutrition_adherence": "partly",
      "progressive_overload": "partial"
    },
    {
      "check_in_id": 40,
      "checked_in_at": "2026-07-16T15:00:00+03:00",
      "analysis_day": "2026-07-16",
      "weight_kg": 78.6,
      "weight_delta_kg": null,
      "training_intensity": 3,
      "fatigue": 2,
      "sleep_quality": 4,
      "nutrition_adherence_scale": "numeric_1_5",
      "nutrition_adherence": 4,
      "progressive_overload": "yes"
    }
  ]
}
```

- `nutrition_applicability` is a current fact about the owner's newest
  canonical saved Nutrition Plan. A valid document is `applicable`, an
  absent row is `not_applicable`, and an unreadable document is `unavailable`.
  An underlying read failure yields a 503. Existing calorie targets, diary
  entries and unsaved proposals do not count as a saved plan.
- A fresh v2 POST checks the same fact again under the owner lock. A plan
  becoming applicable or inapplicable between GET and submission can
  invalidate the frozen answer. Legacy v1 requests keep their original
  behavior regardless of current plan state.
- History carries `ternary_v2` and the original token, including
  `not_applicable`, or `numeric_1_5` and the original score. If the
  historic value is missing or unusable, both adherence fields are null.
  Weight-only rows stay outside full check-in history.
- V1 GET includes full check-ins from either version but projects v2
  adherence to its existing nullable rating slot. V1 POST success and
  replay shapes and fingerprints are unchanged.

## Errors and replay

Errors use the existing mobile envelope. Each entry below shows the HTTP
status and application code.

| HTTP | Code | Condition |
|---|---|---|
| 400 | `CHECKIN_INVALID_REQUEST` | Malformed JSON, wrong field type, missing, duplicate or extra keys, mixed representations, invalid version or week precondition |
| 400 | `INVALID_IDEMPOTENCY_KEY` | Missing or invalid key |
| 422 | `CHECKIN_INVALID_VALUE` | Well-typed unsupported token or out-of-range value, with `field` and `reason` |
| 409 | `CHECKIN_NUTRITION_APPLICABILITY_CHANGED` | Fresh v2 intent mismatches the authoritative plan state. Refresh and review before freezing a new intent. |
| 409 | `CHECKIN_WEEK_CHANGED` | A fresh intent's Istanbul week no longer matches its precondition |
| 409 | `IDEMPOTENCY_CONFLICT` | Owner and key already identify a different intent or contract version |
| 429 | `CHECKIN_RATE_LIMITED` | Owner write rate exceeded. Honors `Retry-After`. |
| 503 | `CHECKIN_TEMPORARILY_UNAVAILABLE` | Check-in storage or applicability could not be confirmed. Retry a pending write with its original version, bytes and key. |

429 and 503 are retryable. Existing authorization and consent errors and the
global 413 still apply. A matching replay takes precedence over fresh week
and plan checks and returns the original snapshot. Versions share the
owner/key namespace, with `native.v1` and `native.v2` fingerprint domains.
Reusing an old key under a different version conflicts. The app persists
the original six-field body and obtains the original version from its typed
adherence representation, so a rollout change cannot reinterpret a retry.

## Migration and composition

- `c8e1f3a5b702` expands `weekly_check_in` with nullable
  `nutrition_adherence_v2`. Its name encodes the scale. A check constraint
  admits closed tokens on full rows with a null legacy score. Existing
  rows, their `beslenme_uyumu` and old replay snapshots are untouched.
  Existing owner delete and health-erasure paths remove the whole row.
  Downgrade refuses when any v2 answer exists. The pre-PD5 writer can
  still insert legacy rows into the expanded schema.
- The composed W3 migration chain extends W1/W2 #565
  (`160c887768be1eb08375ba82a7a0a2e09c6c9386`) with PD5
  `c8e1f3a5b702` after W1/W2 `b7d2e9f4a1c3`, followed by PD6
  `e6a4b91d702c`. Existing G4, PD1 and PD2 revisions retain their lineage.
- Full check-ins continue to derive the applicable calorie target through
  `account_profile.derive_targets_for_weight` in the canonical writer.
  Weight, target and the original response snapshot commit together.
  Replays return that snapshot without reapplying the weight or target.
- Mobile defaults off via `AXISAI_CHECK_IN_NUTRITION_V2_ENABLED`. New
  submissions and history use v1 while off. A v2 intent already stored
  by a previous build retains its original protocol on retry. Enable
  only after separate backend compatibility verification and approval.

[PD6 handoff](PD6_WEEKLY_CHECKIN_HANDOFF.md)
