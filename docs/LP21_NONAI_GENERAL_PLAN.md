# LP21 — Non-AI general Training plan

A user who declines (or cannot yet give) AI processing still gets a usable
first Training plan. The plan is a reviewed, deterministic, catalog-backed
template persisted as a real canonical `TrainingPlan`. It is not AI output,
not personalized, and is never presented as either.

Discovery: `LP21-NONAI-PLAN-00` (`NONAI_GENERAL_PLAN_DISCOVERY.md`, base
`0d2dbaf`). This document records what shipped and where it deviates.

## Two commands, never one

| | AI first plan | Non-AI general plan |
|---|---|---|
| Route | `POST /api/v1/training/plans` | `POST /api/v1/training/plans/general` |
| Endpoint | `mobile_api.create_training_plan` | `mobile_api.create_general_training_plan` |
| D1 | in `ai_consent._CLOSED_ENDPOINTS` (403 before the view) | not gated: there is no AI processing to consent to |
| Provider / consent / allowance / quota | yes | none, and none imported (AST gate) |
| Rate limit | `AI_RATELIMIT` + `BEDROCK_RATELIMIT` + AI concurrency slot | ordinary per-owner write limit `10/min; 60/h` |
| Input | 11 preference fields incl. injuries | `template`, `gun_sayisi`, `ekipman` only |

The general route is a separate endpoint, not a branch of the AI route, and it
does not disable a provider flag or bypass `ai_consent`. A D1 refusal of the AI
route never turns into a general plan: the client must explicitly call the
general route (test `test_d1_still_closes_ai_generation_and_never_falls_back_to_general`).

## Request

```
POST /api/v1/training/plans/general
Authorization: Bearer <access>
Idempotency-Key: [A-Za-z0-9._:-]{8,64}
{"template": "general_full_body_v1", "gun_sayisi": 3 | 4, "ekipman": "ev" | "minimal" | "spor_salonu"}
```

A closed key set. Any other key (injuries, goal, plan, …), a bool or float day
count, or an unknown template/equipment is `422 TRAINING_PLAN_INVALID_REQUEST`.
Nothing is coerced. The only account value read is `User.language`, which picks
the day labels (`tr` default, `en`).

## Template — `general_full_body_v1` (rules version 1)

`app/services/general_training_plan/template.py`, pure:

* Full-body A/B. Three days: Monday A, Wednesday B, Friday A. Four days:
  Monday A, Tuesday B, Thursday A, Friday B. The other days are rest.
* Five exercises per session, 3 sets each. Reps 6–15, or "30 sn" for the plank.
  Rest is 90 s for compound lifts and 60 s for core work. Sessions are 45 min.
* Every exercise is an active catalog ID inside the equipment context:
  * `ev` is bodyweight only.
  * `minimal` is bodyweight, dumbbells and bands.
  * `spor_salonu` is a gym.
* Each request re-validates the plan through the canonical save boundary
  (`validate_plan_for_save`). That covers structure, semantics, catalog identity
  and equipment compatibility. If validation fails, the request returns
  `503 TRAINING_PLAN_GENERAL_UNAVAILABLE` and no plan is written.
* Some fields are left out on purpose, so the plan cannot be mistaken for an AI plan:
  * There is no `haftalik_ozet`, because those are AI quality scores.
  * `TrainingPlan.score` is NULL.
  * `tahmini_kalori` is 0 on every day. It is never estimated, because this
    path reads no body data.
  * Exercise notes are empty. Coaching or safety copy needs approved wording.
* The same `(gun_sayisi, ekipman, language)` always produces byte-identical plan
  text. Language changes only the day labels.

No injury, medical or goal-specific prescription is made or implied. The
template's suitability for a given person is a product/safety decision, not a
claim this code establishes.

## Persistence, idempotency, concurrency

`mobile_training_generation.store.commit_general_plan` does all of the
following in one transaction:

1. It inserts the receipt into `training_plan_generation_operation`. The
   receipt row is born `SUCCEEDED` with `provider_dispatch_possible=False`,
   `allowance_metered` and `provider_attempt_possible` NULL, and
   `quota_reserved=False`.
2. It takes `lock_plan_owner`.
3. Under that lock it checks for an existing plan with `get_active_plan`.
4. It refuses if an AI first-plan operation is live, meaning IN_PROGRESS or
   GENERATED and touched within `ABANDON_AFTER`.
5. It inserts the `TrainingPlan` and binds the receipt to its id and lineage.
6. It commits.

This is the AI command's ledger and lock order (operation → owner → plan). It
is the third named create writer in `plan_owner_lock`.

Why it is safe and needs no migration:

* No AI path can produce `SUCCEEDED` with dispatch `False`, because
  `authorize_dispatch` commits `False → True` before any candidate exists. So
  the receipt cannot be read as AI work.
* `is_general_receipt` names that state.
* The receipt is never active. It does not take the active-owner slot, and
  abandonment retirement, allowance settlement and quota refunds never see it.
* Abandoned AI rows are left untouched. The general path neither retires nor
  refunds them, and they do not block it.

| Situation | Answer |
|---|---|
| New key, no plan | `201`, `Idempotency-Replayed: false` |
| Same key, same intent | `201`, `Idempotency-Replayed: true`, same plan (current revision) |
| Same key, different intent — or a key the AI command used | `409 TRAINING_PLAN_IDEMPOTENCY_CONFLICT` |
| Any plan already exists | `409 TRAINING_PLAN_REPLACEMENT_REFUSED`; key not consumed |
| Live AI first-plan generation | `409 TRAINING_PLAN_GENERATION_IN_PROGRESS`, retryable, `Retry-After: 15` |
| Replay after the key's plan was replaced/removed | `409 TRAINING_PLAN_GENERAL_SUPERSEDED`, not retryable |
| Failure after flush | rollback of receipt **and** plan → `503 TRAINING_PLAN_PERSISTENCE_UNAVAILABLE`, retryable; same key then succeeds |
| Malformed key | `400 TRAINING_PLAN_INVALID_IDEMPOTENCY_KEY` |
| Rate limit | `429 TRAINING_PLAN_RATE_LIMITED` |

The PostgreSQL races in `tests/test_lp21_general_training_plan_pg.py` (in the
CI list) are forced: an external transaction holds the owner row until
PostgreSQL reports the workers waiting on a lock. They cover:

* same key ×3 (one plan, two replays);
* same key with a different intent;
* different keys ×4 (one plan, one receipt);
* general vs browser `expected_plan: null`;
* an absence check that observes a plan the lock holder committed;
* rollback after flush;
* two owners using the same key.

## Response

```
201 {"contract_version": 1,
     "origin": {"kind": "general_template", "template": "general_full_body_v1", "personalized": false},
     "plan": <exactly GET /api/v1/training/plans/current's "plan">}
```

After creation the plan behaves like any canonical plan:

* `GET /training/plans/current`, `/today` and `/training/workouts/<ref>` read it.
* Workout sessions start from its `workout_ref`.
* Nothing is started, logged or completed on the user's behalf.

## Mobile integration requirements (owner: mobile lane; not implemented here)

PR #116 does not know this route. The mobile owner must do the following:

1. Offer the general plan as an explicit user choice that is separate from AI
   generation. Never call it automatically after an AI refusal. Use
   Legal-approved copy, which is kept separate from this contract.
2. Send the closed body above. Equipment and day count are the only inputs.
   Do not send profile, injuries or goals.
3. Generate one `Idempotency-Key` per user intent and keep it across transport
   retries. Handle an ambiguous outcome (timeout or 5xx) as follows:
   * retry the same key; a `201` with `Idempotency-Replayed: true` is success;
   * or re-read `GET /training/plans/current`; a non-null plan means it succeeded.
4. Map `409 TRAINING_PLAN_REPLACEMENT_REFUSED` and `409 TRAINING_PLAN_GENERAL_SUPERSEDED`
   to "a plan already exists": re-read the current plan, and never create
   local plan state.
5. Show success only from the 201 body or a canonical re-read, then navigate to
   Today/Plan.
6. Label the plan as general / not personalized, using `origin` from the 201.
   After a reinstall or on another device, read the label from
   `plan.origin` on `GET /training/plans/current` or `today.plan.origin` on
   `GET /today` (see "Read-side origin" below). Render the general label only
   for `kind: "general_template"`. Treat `unknown`, and any `kind` you do not
   recognize, as "no label". Never treat it as AI or as personalized.
7. Treat `estimated_calories: 0` on a general plan as "not estimated" and do
   not render "0 kcal". `score` is `null`.

## Read-side origin

`GET /training/plans/current` publishes `plan.origin`. `GET /today` publishes
`today.plan.origin`; it is `null` when there is no plan. The 201 body's `plan`
carries the same field, so it stays exactly the current-plan `plan`. Both
reads use one resolver, `app/services/training_plan_origin.py`, so they cannot
label one plan differently.

| `kind` | `template` | `personalized` | When |
|---|---|---|---|
| `general_template` | `general_full_body_v1` | `false` | See the receipt conditions below |
| `unknown` | `null` | `null` | Anything else |

A plan is `general_template` only when all of the following hold:

* exactly one ledger row is bound to the plan's lineage;
* that row belongs to the same owner;
* it names this plan row (`training_plan_id`);
* `is_general_receipt` holds for it.

`unknown` covers several cases:

* AI first plans;
* browser-saved plans;
* replacement results (a replacement mints a new lineage, so a general plan
  that is replaced becomes `unknown`);
* plans that predate receipts;
* contradictory ledger evidence.

`unknown` claims nothing. It is not "AI", and plan content is never used to
guess the origin: a receipt-less row holding the exact template document is
`unknown`.

**Creation, not edits.** Origin describes how the lineage was created. Journal
edits keep the lineage and therefore keep the origin, so a general plan with
`mutation_version > 0` is still `general_template`. `personalized: false`
describes the template, not later edits. A client that needs to know about
edits reads `mutation_version`.

**Template id.** A receipt stores only the request fingerprint, not the
template. `GENERAL_TEMPLATE_ID` is therefore pinned to the one template the
command accepts. A second template must version this projection before it
ships, and a test pins the two ids together.

**Cost and failure.**

* The resolver issues one owner-scoped `SELECT … LIMIT 2` when a plan exists,
  and none without one. Current-plan stays within its 5-statement guard.
  `/today` stays at 3 SELECTs without a plan and becomes 4 with one. Today
  Guidance pays the same +1 (docs/LP17_B1_NATIVE_TODAY_GUIDANCE_HTTP.md).
* The resolver performs no writes and makes no provider call.
* A failed ledger read is never downgraded to `unknown`. It fails closed as
  `503 TRAINING_READ_UNAVAILABLE` (current plan) or
  `503 TODAY_TEMPORARILY_UNAVAILABLE` (Today).
* The module imports only `app.models`, enforced by an AST test. Today
  therefore reaches neither the AI ledger store nor the general-plan package.

Tests live in `tests/test_lp21_general_training_plan.py` under the "read-side
origin" section, and the PostgreSQL check is in
`tests/test_lp21_general_training_plan_pg.py`.

## Not done here

* An explicit persisted `template_version`. Today it is bound only through the
  template id in the request fingerprint, because a dedicated column needs a
  migration and K6 #446 also re-parents onto head `f8b2d4e6a0c3`.
* Moving from a general plan to AI personalization later. That uses the
  existing staged replacement proposal/confirm. The general plan is kept until
  the user confirms, and granting consent never replaces it silently.
* Staging and device qualification.
