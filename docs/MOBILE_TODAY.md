# Mobile Today Contract

The canonical Today state the native AxisAI client reads, and the reasoning
behind its shape. Web Today/Training behaviour is unchanged by everything in
this file: PR3 adds a projection over authorities that already exist, and
modifies none of them.

- Endpoint: `GET /api/v1/today`
- Route: `app/blueprints/mobile_today.py`
- Service: `app/services/mobile_today.py`
- Tests: `tests/test_mobile_today_api.py`,
  `tests/test_mobile_today_architecture.py`
- Canonical authority this projects: [WORKOUT_STATE.md](WORKOUT_STATE.md)
- Auth contract this sits on: [AUTH_CONTRACT.md](AUTH_CONTRACT.md),
  [adr/0001-native-mobile-authentication.md](adr/0001-native-mobile-authentication.md)

## The one rule

> This endpoint **exposes** canonical Today state. It is not a Today authority.

Every fitness fact in the response is decided elsewhere:

| Decision | Owner |
|---|---|
| which plan is active | `app/services/today_facts.get_active_plan` |
| workout / rest / no-plan / unreadable | `app/services/workout_state.resolve_workout_state` |
| completion | the same resolver's `completed_today` (today's `PumpCheck`) |
| today's date | `app/timeutil.app_today()` (Europe/Istanbul) |
| today's plan content | `workout_state.serialization.serialize_today_plan` |

`app/services/mobile_today.py` orchestrates those reads inside one coherent
snapshot and serializes the result. It contains no `if` that classifies a day,
no calendar arithmetic and no second query against a fitness table. That is not
a convention — `tests/test_mobile_today_architecture.py` fails the build if it
stops being true, and each of those guards is proven non-vacuous by mutating the
real source and asserting the guard trips.

## Why not extract from the web presenter

The web Today surface does not hold a reusable "Today" object. `today_facts` and
`today_presenter` are deliberately thin (`read_ok`, `has_active_plan`,
`workout_completed_today`); the real composition lives in `/training/bootstrap`,
which captures auth/date/flag at the boundary, opens
`coherent_read_snapshot()`, and feeds `get_active_plan` +
`resolve_workout_state(strict_reads=True)` into pure serializers.

PR3 performs **that same composition** rather than refactoring a web route into
a shared helper. The cost is a few lines of orchestration; the benefit is that
zero bytes of a live browser surface changed, so there is no way for this PR to
regress web Today.

## Authentication and ownership

The route uses the existing authoritative mobile boundary,
`app/mobile_auth_middleware.require_mobile_auth`, and nothing else:

- credential: `Authorization: Bearer <opaque AxisAI access credential>`;
- the user is `g.mobile_user`, resolved from the verified credential;
- `build_today(user_id)` takes the principal id and **nothing else** — no date,
  no timezone, no owner override. There is no parameter through which one user
  could read another's Today, and none through which a client clock could move
  the day;
- the route reads no query string, no body, no form and no header beyond the
  auth boundary. `?user_id=`, `?owner_id=`, `?date=`, `?today=`,
  `X-User-Id:`, `X-Date:` are all inert — the response is byte-identical with or
  without them;
- a browser cookie is never consulted; a valid Flask-Login session alone gets
  `401` in the shared mobile envelope, never an HTML redirect;
- the route is registered on the single `mobile_api` blueprint, so it inherits
  one `Cache-Control: no-store` policy, one error envelope, one 429 handler, one
  `MOBILE_AUTH_ENABLED` gate, and the pinned approved-route allow-list in
  `tests/test_mobile_auth_feature_gate.py`.

## Response contract

`200 application/json`, `Cache-Control: no-store`. One namespaced root object,
so a later sprint can add a sibling block without renaming anything.

```json
{
  "today": {
    "date": "2026-07-23",
    "server_time": "2026-07-23T12:00:00Z",
    "status": "scheduled_not_started",
    "action": "start",
    "workout": {
      "schedule_state": "scheduled",
      "is_rest_day": false,
      "completed": false,
      "summary": {
        "focus": "Tum vucut",
        "duration_min": 45,
        "estimated_calories": 320,
        "exercise_count": 2
      },
      "session": null
    },
    "plan": { "exists": true, "created_at": "2026-07-01T08:00:00Z" },
    "daily_context": {
      "plan_lineage": "IFFkovLw3diUtHjuNdYMRzCa2Imf8dcZmVTQtxT1hMc",
      "mutation_version": 0,
      "canonical_local_date": "2026-07-23"
    },
    "state": { "...": "the canonical workout-state contract, verbatim" }
  }
}
```

### Field by field

| Field | Type | Null? | Canonical source | Semantics | Stability |
|---|---|---|---|---|---|
| `today.date` | `string` `YYYY-MM-DD` | never | `app_today()` | The server's Istanbul day. The same day the snapshot was resolved for. | stable |
| `today.server_time` | `string` ISO-8601 UTC, `...Z`, second precision | never | `app_now()` | The instant the response was produced. For skew display/logging only — never for selecting a day. | stable |
| `today.status` | `string` enum | never | `snapshot.primary_state` | The one dominant Today state. See the enum below. | additive: new members possible |
| `today.action` | `string` enum | never | `snapshot.action` | The canonical primary action: `start`, `none`, `blocked`, `resume`. Deterministic, not a recommendation engine. | additive |
| `today.workout.schedule_state` | `string` enum | never | `snapshot.schedule_state` | `scheduled`, `rest_day`, `no_plan`, `schedule_unavailable`. | additive |
| `today.workout.is_rest_day` | `boolean` | never | `snapshot.is_rest_day` | True **only** when the canonical schedule says rest. Never true for "no plan" or "unreadable". | stable |
| `today.workout.completed` | `boolean` | never | `snapshot.completed_today` | True **only** when today's `PumpCheck` proves completion. Never inferred from the clock, from a marker alone, or from recorded execution evidence. | stable |
| `today.workout.summary` | `object` | **nullable** | `serialize_today_plan` | A bounded card summary of today's plan day. `null` = the canonical projection has no publishable day (no plan, unreadable schedule, out-of-bounds content). `null` **never** means rest day. | stable |
| `today.workout.summary.focus` | `string` | never (inside the object) | plan document `odak` | Free-text plan **content** an author wrote (like an exercise name). Display it; never branch on it. On a rest day the generator writes `"-"`. | free text |
| `today.workout.summary.duration_min` | `integer` | never | plan document `sure_dk` | Planned minutes. `0` on a rest day. | stable |
| `today.workout.summary.estimated_calories` | `integer` | never | plan document `tahmini_kalori` | Planned estimate, not an observation. | stable |
| `today.workout.summary.exercise_count` | `integer` | never | `len(egzersizler)` | How many exercises today's plan day holds. The exercise payload itself is **not** published here. | stable |
| `today.workout.session` | `object` | **nullable** | `snapshot.session` | The persisted workout session, present only when `FITX_WORKOUT_SESSIONS_ENABLED` is on (default OFF ⇒ always `null`). Carries the opaque `public_id`, `status`, `resumable`, `relationship`, `stale_reason`, `workout_date`. Never an invented identity. | flag-conditional |
| `today.plan.exists` | `boolean` | never | `get_active_plan(...) is not None` | Whether an active training plan exists at all. | stable |
| `today.plan.created_at` | `string` ISO-8601 UTC `...Z` | **nullable** | `TrainingPlan.created_at` | When the active plan was created. `null` iff `exists` is `false`. | stable |
| `today.daily_context.plan_lineage` | `string` | **nullable** | `TrainingPlan.lineage_id` | Opaque, non-guessable, server-generated lineage token (`secrets.token_urlsafe(32)`). Not a database key, not derived from user data. `null` iff no plan. | stable |
| `today.daily_context.mutation_version` | `integer` | **nullable** | `TrainingPlan.mutation_version` | The server-authoritative history position of that lineage. Moves when the plan is mutated. `null` iff no plan. | stable |
| `today.daily_context.canonical_local_date` | `string` `YYYY-MM-DD` | never | `app_today()` | Always equal to `today.date`, restated inside the identity tuple so a cached context is self-describing. | stable |
| `today.state` | `object` | never | `snapshot.to_dict()` | The canonical workout-state contract exactly as `GET /workout/status` publishes it, carried verbatim. Its `contract_version` announces its own shape. **Passthrough, not the integration target** — see below. | versioned by `contract_version` |

The key set is **identical in every domain state**. Nothing appears or
disappears; only values (and documented nulls) change.

### `status` enum

Re-exported verbatim from the canonical workout-state contract rather than
remapped, so PR3 introduces no fourth Today vocabulary. `docs/WORKOUT_STATE.md`
owns these names.

| Value | Means |
|---|---|
| `scheduled_not_started` | A workout is scheduled for today and is not complete. |
| `completed` | Today's workout is proven complete. |
| `rest_day` | The canonical schedule says today is a rest day. |
| `no_plan` | No active training plan exists. **Not** a rest day. |
| `needs_attention` | The plan exists but its schedule could not be read (`schedule_state: schedule_unavailable`, with `state.anomaly` naming why). Neither a rest day nor an empty state. |
| `in_progress` | Only reachable with `FITX_WORKOUT_SESSIONS_ENABLED` on: a resumable persisted session exists. |

Three distinctions this contract holds and a client must not collapse:

```
no plan        != rest day
unavailable    != empty
not completed  != incomplete data
```

### `state` is a passthrough, not the integration target

`today.state` is the same envelope `/workout/status` and `/training/bootstrap`
already publish. It is carried verbatim so that mobile and web read one
contract, and so parity is structural rather than merely asserted in a test.

Clients should read the flattened projection (`status`, `action`,
`workout.*`, `plan.*`, `daily_context.*`). Treat `state` as forward-compatible
detail — useful for diagnostics and for fields a later client wants
(`execution_state`, `plan_relationship`, `anomaly`, `stale_previous_workout`) —
and do not branch product behaviour on it without reading
`docs/WORKOUT_STATE.md` first.

## Daily context identity

`(plan_lineage, mutation_version, canonical_local_date)` is the approved daily
workout-context identity. It exists so a client can decide whether a cached
Today is still current:

- same tuple ⇒ the same canonical day of the same plan version;
- a changed `mutation_version` ⇒ the plan was mutated; discard the cached day;
- a changed `canonical_local_date` ⇒ the server rolled over to a new day.

All three are server-owned tokens read straight off the canonical plan row.
Nothing is hashed, minted, defaulted or combined into a presentation key, and
**no internal database identifier is exposed**. When no plan exists, lineage and
version are `null` — a fabricated value here would make a client believe in a
plan the server does not have. There is no new table and no new persisted model:
Today is a read projection.

## Date and timezone

`app/timeutil` is the only clock. `app_today()` supplies the day and
`app_now()` supplies `server_time`, so the two can never come from different
clocks and a frozen test clock freezes both. There is no second timezone
conversion in the projection and no naive `date.today()` anywhere in it.

A client's date, timezone, locale or `?date=` parameter cannot move Today. At
22:30 UTC on 22 July the endpoint reports **23 July**, because that is the
Istanbul day; `tests/test_mobile_today_api.py` pins exactly that boundary.

## Empty, rest and degraded states

| Situation | HTTP | Shape |
|---|---|---|
| No active plan | `200` | `status: no_plan`, `is_rest_day: false`, `summary: null`, `plan.exists: false`, lineage/version `null` |
| Canonical rest day | `200` | `status: rest_day`, `is_rest_day: true`, `plan.exists: true` |
| Workout scheduled | `200` | `status: scheduled_not_started`, `action: start`, `summary` populated |
| Proven complete | `200` | `status: completed`, `completed: true` |
| Plan unreadable | `200` | `status: needs_attention`, `schedule_state: schedule_unavailable`, `action: blocked`, `summary: null` |
| Canonical read failed | `503` | `TODAY_TEMPORARILY_UNAVAILABLE`, `retryable: true` |

The composition runs with `strict_reads=True` inside one coherent snapshot, so
an infrastructure fault raises `TodayUnavailable` and the route answers a typed
`503`. **A failed read is never downgraded into an empty or resting Today.** A
home screen that lies is worse than one that is briefly unavailable.

The `503` deliberately does not fall through to the blueprint's
auth-flavoured handler: a storage fault is not an authentication outcome, and a
client that read it as one would discard a good session and send the user back
to login. The log line carries an exception type name and a request id — never a
plan, a workout, an injury or an account identifier — and no stack trace or DB
error reaches the client.

An unparseable `plan_data` is deliberately **not** an infrastructure fault: the
canonical resolver already classifies it as `schedule_unavailable` /
`needs_attention`, and that honest "your plan needs attention" must survive to
the client instead of being replaced by a `503`.

There is no fixture, sample, demo or placeholder Today in the production path
(the Sprint 12 PR2A production-truth invariant), and an architecture guard
enforces it.

## Cost

Per request: **4 bounded `SELECT`s** — the authenticated principal, the active
plan, the execution-evidence log rows, today's `PumpCheck` — and the count is
the same whether or not the user has a plan. The plan row is loaded once and
passed into the resolver rather than re-queried. No N+1, no write, no flush, no
commit, no lock, no HTTP call.

**Provider calls: exactly 0.** No AI/LLM module is imported or reachable, and
`tests/test_mobile_today_architecture.py` runs a real request with every
provider client replaced by an object that raises on any attribute access.

## Not exposed

Deliberately absent, because no canonical authority owns them today. Inventing
any of them at the API boundary would create exactly the second Today authority
this endpoint exists to avoid:

- readiness score, recovery score, strain
- check-in-due inference
- pending Adaptive Coaching proposals
- plan-change explanations ("why your plan changed")
- next-best-action recommendation
- nutrition, calorie goals, progress aggregates
- the full exercise payload for today (summary + canonical state only; a client
  needing exercises fetches the existing workout surface)

## Feature flags

| Flag | Default | Effect here |
|---|---|---|
| `MOBILE_AUTH_ENABLED` | `False` (unchanged by PR3) | OFF ⇒ no `/api/v1` route is registered at all, including this one. ON ⇒ `/api/v1/today` appears in the pinned approved-route allow-list. |
| `FITX_WORKOUT_SESSIONS_ENABLED` | `False` | OFF ⇒ `state.contract_version` is `1` and `workout.session` is `null`. ON ⇒ the session-aware v2 contract, read once per request and passed in, so one response describes one contract. |
| `FITX_ADAPTIVE_COACHING_ENABLED`, `FITX_COACH_PLAN_TOOLS_ENABLED` | `False` | No effect. Today is correct with both OFF and reads nothing Adaptive Coaching owns. |

PR3 changes no flag default and activates nothing.
