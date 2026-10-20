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
   After a reinstall or on another device, request `?include_origin=1` and
   read `plan.origin` (current plan) or `today.plan.origin` (Today). See
   "Read-side origin" below. Render the general label only for
   `kind: "general_template"`. Treat `unknown`, and any `kind` you do not
   recognize, as "no label".
7. Treat `estimated_calories: 0` on a general plan as "not estimated" and do
   not render "0 kcal". `score` is `null`.

## Read-side origin (per-request opt-in)

Shipped mobile DTOs parse Today's `plan` and the current-plan object with
`exactKeys`: any key they do not know is a `FormatException`, and the screen
becomes "unavailable". So origin is never added globally. A client asks for it
on each request:

```
GET /api/v1/training/plans/current?include_origin=1   -> plan.origin
GET /api/v1/today?include_origin=1                    -> today.plan.origin
```

How the parameter is handled:

| `include_origin` | Response | Ledger reads |
|---|---|---|
| absent | the pre-LP21 contract, byte-for-byte; no `origin` key anywhere | 0 |
| exactly `1` | the same objects plus exactly one key, `origin` | 1 when a plan exists, else 0 |
| anything else (`0`, `true`, empty, repeated) | `400 TRAINING_READ_INVALID_REQUEST` / `400 TODAY_INVALID_REQUEST`, `retryable: false` | 0 |

Other query values stay ignored, as before.

The rest of each request is unchanged:

* Bearer auth and owner scoping;
* `Cache-Control: no-store`;
* plan identity (`plan_lineage`, `mutation_version`, `daily_context`);
* Today Guidance, which composes `build_today` without the opt-in and so pays
  no extra read.

With no plan, current-plan stays `{"plan": null}`, and the opted-in Today
carries `"origin": null`. The general create `201` keeps its own contract: a
top-level `origin`, and a `plan` that is the legacy current-plan object.

### Kinds

| `kind` | `template` | `personalized` | Evidence |
|---|---|---|---|
| `general_template` | `general_full_body_v1` | `false` | a general receipt (`is_general_receipt`) |
| `ai_generated` | `null` | `true` | a native AI first-plan receipt: `SUCCEEDED` with `provider_dispatch_possible` `true` |
| `unknown` | `null` | `null` | anything else |

A kind other than `unknown` requires all of the following:

* exactly one `training_plan_generation_operation` row is bound to the plan's
  lineage;
* that row belongs to the same owner;
* it names this plan row (`training_plan_id`).

The rows of the two known kinds are disjoint: dispatch is `false` for a
general receipt and `true` for an AI one.

`unknown` covers several cases:

* browser-saved plans;
* replacement results (a replacement mints a new lineage, so a replaced
  general plan becomes `unknown`);
* historical AI rows whose dispatch marker is NULL;
* plans that predate receipts;
* contradictory evidence.

`unknown` claims nothing. A missing general receipt is never read as "AI", and
plan content is never used to guess the origin: a receipt-less row holding the
exact template document is `unknown`. Clients must treat any `kind` they do
not recognize as `unknown`.

**Creation, not edits.** Origin describes how the lineage was created. Journal
edits and undo keep the lineage, so a general plan with `mutation_version > 0`
is still `general_template`. `personalized` describes how the plan was
created, not whether its content has changed since; a client that needs to
know about edits reads `mutation_version`.

**Template id.** A receipt stores only the request fingerprint, not the
template. `GENERAL_TEMPLATE_ID` is therefore pinned to the one template the
command accepts. A second template must version this projection before it
ships, and a test pins the two ids together.

**Cost and failure.**

* The resolver issues one owner-scoped `SELECT … LIMIT 2`. It runs only on an
  opted-in read that has a plan.
* It performs no writes and makes no provider call.
* A failed ledger read is never downgraded to `unknown`. It fails closed as
  `503 TRAINING_READ_UNAVAILABLE` or `503 TODAY_TEMPORARILY_UNAVAILABLE`.
* The module imports only `app.models`, enforced by an AST test.

### Mobile integration order

Ship a DTO that accepts the optional `origin` (with the kind allow-list
above) before any build sends `include_origin=1`. Builds that never send it
are unaffected by this change.

Tests live in `tests/test_lp21_general_training_plan.py` under "read-side
origin", and the PostgreSQL check is in
`tests/test_lp21_general_training_plan_pg.py`.

Follow-up, not done: an AI *replacement* result (an LP18-B1 confirm whose
`APPLIED` receipt binds `result_lineage_id`) is reported `unknown`.
Recognizing it means reading a second authority.

## Not done here

* An explicit persisted `template_version`. Today it is bound only through the
  template id in the request fingerprint, because a dedicated column needs a
  migration and K6 #446 also re-parents onto head `f8b2d4e6a0c3`.
* Moving from a general plan to AI personalization later. That uses the
  existing staged replacement proposal/confirm. The general plan is kept until
  the user confirms, and granting consent never replaces it silently.
* Staging and device qualification.
