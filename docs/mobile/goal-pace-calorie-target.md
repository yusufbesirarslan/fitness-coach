# Goal pace and the canonical daily calorie target (PD1, #547)

Source only. Nothing is activated: no flag, no deploy, no data migration of
existing rows. Migration `a7d3c9e1f2b4` (expand-only) adds one nullable
column, `user.goal_pace`.

## 1. Authorities (nothing new, nothing shadowed)

| Fact | Owner | Stored in |
| --- | --- | --- |
| chosen pace | `account_profile` (validation, goal fit, write) | `user.goal_pace` (NULL = never chosen) |
| pace arithmetic | `app/services/goal_pace.py` (pure, constants only) | — |
| the ONE target computation | `account_profile.derive_targets` | — |
| the canonical daily target | unchanged | newest `UserSession.target_calories` |
| read projection | `account_profile.calorie_target_view` | — (read-only) |

Every live writer of the target goes through `derive_targets`: native and
web onboarding (`complete_onboarding`), the pace edit (`set_goal_pace`,
which reuses `complete_onboarding`), and every body-weight update
(`weekly_checkin.apply_body_weight` → `derive_targets_for_weight`). One
legacy writer does not: web `POST /chat` (`coach.chat`) still appends a
session sized by `calculate_target`. It is closed by D1
(`ai_consent._CLOSED_ENDPOINTS`); if it were ever reopened, the read would
report `basis: stored` for the row it writes and the owner can re-apply the
pace. Web `/edit-profile` changes the goal without re-deriving (pre-existing);
the same `stored` rule covers it. Every
reader keeps reading `UserSession.target_calories` (Nutrition day view,
diary goal, Plan facts, Coach context, menu, barcode). No client computes
a target, BMR or TDEE.

## 2. Vocabulary (stable, locale-independent)

| goal | token | meaning | client label |
| --- | --- | --- | --- |
| `lose_weight` | `lose_slower` | target pace about 200 g/week | A |
| `lose_weight` | `lose_faster` | target pace about 500 g/week | B |
| `build_muscle` | `gain_lean` | small surplus, 5 % of maintenance, 100–250 kcal | A |
| `build_muscle` | `gain_steady` | moderate surplus, 10 % of maintenance, 200–450 kcal | B |

A pace is a target used to size a deficit or surplus. It is never a promised
outcome; no muscle-gain rate is modelled or published.

## 3. Rules (`goal_pace.py`)

* Loss: deficit = pace kg/week × 7700 / 7, capped at 25 % of maintenance;
  the result is never below 1500 kcal (male) / 1200 kcal (female; unknown sex
  takes 1500). Either limit sets `bounded: true`.
* Unsuitable loss pace → the target is maintenance (`basis: maintenance`)
  with a closed `reason`: `low_bmi` (BMI < 18.5), `target_weight_reached`
  (stated target ≥ current weight), `calorie_floor` (< 100 kcal of deficit
  left after the limits).
* Gain: maintenance + the clamped surplus of the option.
* Every pace target is rounded half-up to 10 kcal.
* A profile whose values give no positive maintenance estimate (e.g. a height
  typed in metres) cannot be sized by a pace: the legacy formula applies,
  nothing raises, and the read offers no pace options.
* Maintenance (a goal outside the two tokens) and accounts with no pace keep
  the legacy formula (`calculate_target`: −400 / +300 / ±0).

## 4. Compatibility

* NULL pace = legacy: no existing account is re-targeted until its owner
  picks a pace. Reading never writes.
* A pace that does not fit the stored goal (the goal was changed by web
  `/edit-profile`, which does not re-derive) reads back as `null` and is
  ignored by every computation.
* `PUT /api/v1/account/profile` accepts an optional, write-only
  `goal_pace`. Omitted: the stored pace is kept when it fits the submitted
  goal, cleared otherwise. So an old client re-submitting the profile keeps
  the user's pace, and a goal change without a pace drops it.
* `GET /api/v1/account/profile`, `GET /api/v1/account/me` and the PUT
  answer are unchanged (shipped DTOs decode them with exact key sets).
* TR 13–17: never a pace, never a target. A pace on the teen write is
  refused whole (422); the edit is refused (422); the read says `not_set`.

## 5. `GET /api/v1/account/calorie-target` — the PD2 handoff contract

Bearer-only, health-consent gated, `no-store`. Read-only (no write, commit
or lock).

```json
{
  "contract_version": 1,
  "calorie_target": {
    "state": "available",
    "daily_kcal": 2210,
    "maintenance_kcal": 2760,
    "goal": "lose_weight",
    "goal_pace": "lose_faster",
    "basis": "pace",
    "bounded": false,
    "reason": null,
    "pace_options": [
      {"goal_pace": "lose_slower", "daily_kcal": 2540, "basis": "pace",
       "bounded": false, "reason": null, "nominal_weekly_change_g": -200},
      {"goal_pace": "lose_faster", "daily_kcal": 2210, "basis": "pace",
       "bounded": false, "reason": null, "nominal_weekly_change_g": -500}
    ]
  }
}
```

* `state`: `available` | `not_set` (no complete onboarding, a minor, or a
  NULL / non-positive stored target). `not_set` ⇒ `daily_kcal`,
  `maintenance_kcal`, `basis` are `null`, `bounded` false, options `[]`.
* `daily_kcal`: the canonical stored `UserSession.target_calories`, rounded
  half-up to a whole kcal. It is the value Nutrition and Today read (and
  PD2's `nutrition_day_view.read_daily_calorie_target`). Pace targets are
  multiples of 10, so it equals the stored value exactly; a legacy target may
  carry a fraction, which the mobile client rounds the same way (a Python
  reader using `round()` can differ by 1 kcal on an exact .5).
* `basis`: `pace` | `maintenance` | `legacy` | `stored`. `stored` means the
  stored profile no longer reproduces the stored target (changed elsewhere);
  the number is still the canonical one, nothing is guessed.
* `maintenance_kcal`: the maintenance estimate, rounded to 10; `null` when
  the stored profile cannot be re-derived.
* `pace_options`: one item per pace of the stored goal (empty for any other
  goal), in display order A, B; `daily_kcal` is exactly what
  `PUT /goal-pace` with that token would persist. `nominal_weekly_change_g`
  is only set for loss paces.
* Additive evolution only; a breaking change bumps `contract_version`.

**PD2 consumption rule:** show `daily_kcal` when `state == "available"`;
otherwise show "no target" — never 0 and never a client-side estimate. Do
not derive anything from `pace_options` except what it states.

## 6. `PUT /api/v1/account/goal-pace`

Body is exactly `{"goal_pace": "<token>"}`. Answers the same body as the
GET (the read after the commit).

| status | code | when |
| --- | --- | --- |
| 200 | — | applied (repeat = same answer, nothing appended) |
| 400 | `PROFILE_INVALID_REQUEST` | not a JSON object, other keys, non-string |
| 409 | `PROFILE_INCOMPLETE` | no complete adult profile (or a stored legacy value outside today's ranges) |
| 422 | `PROFILE_INVALID_VALUE` | unknown token, a pace of the other goal, a minor |
| 403 | eligibility / health consent | as every adult weight-target route |
| 503 | `PROFILE_TEMPORARILY_UNAVAILABLE` | storage fault; rolled back, retry is safe |

Transaction: owner row `FOR UPDATE` → re-read the committed profile → the
`complete_onboarding` write (pace + canonical session updated in place, one
commit). A concurrent profile write wins whole: an edit that re-reads a
changed goal is refused rather than applied to a stale goal (PG proof in
`tests/test_account_profile_pg.py`).

## 7. Tests

`tests/test_pd1_goal_pace.py` (rules, onboarding, parity with day view and
diary, edit idempotency, goal change, legacy, invalid input, rollback,
isolation), `tests/test_tr_minor_full_onboarding.py` (teen + consent),
`tests/test_account_profile_pg.py` (PG races; CI list).
