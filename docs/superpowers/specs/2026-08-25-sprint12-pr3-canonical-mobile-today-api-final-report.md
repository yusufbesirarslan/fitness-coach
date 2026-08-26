# Sprint 12 PR3 — Canonical Mobile `GET /api/v1/today` — Final Report

**Date:** 2026-08-25

---

## 1. Executive verdict

**READY TO SHIP** *(full-suite result recorded in §28; verdict restated in §38.)*

`GET /api/v1/today` exists, is authenticated and owner-scoped, and publishes
canonical Today state without becoming a Today authority. Every fitness fact it
returns is decided by `app/services/workout_state`, `today_facts.get_active_plan`
or `app/timeutil`; the projection holds no rest-day inference, no completion
inference, no calendar arithmetic and no second fitness query. Those claims are
not prose — they are source-level guards, each proven non-vacuous by mutating
the real implementation and asserting the guard trips.

No migration, no model, no schema change, no flag default change, no web
behaviour change, no `axisai_mobile` change, zero provider calls.

**P0 = 0, P1 = 0** (one P1 found in review and fixed), three accepted P2s.

---

## 2. Repository / worktree / branch

| | |
|---|---|
| Repository | `C:\Users\yusuf\develop\fitness-coach` |
| Worktree | `.worktrees/sprint12-pr3-canonical-mobile-today-api` (isolated, created for this PR) |
| Branch | `sprint12-pr3-canonical-mobile-today-api` |
| Remote state | 5 ahead of `origin/main`, 0 behind — **not pushed** |

---

## 3. Base SHA

Branched from the latest `origin/main` at the time of the fetch:

```
c72740a  fix(training): canonicalize exercises before injury annotation (#243)
```

That commit is Sprint 12 PR2B, so PR1 (discovery), PR2A (fixture elimination)
and PR2B (injury annotation ordering) are all present on the base. The worktree
was created clean and no unrelated worktree was reused.

---

## 4. Sprint 12 architecture constraints honoured

| Constraint | Status |
|---|---|
| Must expose canonical Today state, never create a new Today authority | Held — §5, §25 |
| No `daily_coach_state` | Not created |
| No second workout-selection engine | Not created |
| No mobile-specific "today workout" algorithm | Not created |
| No client-clock selection | Impossible by construction — §17 |
| No duplicate rest-day inference | Guarded — §25 |
| No duplicate completion inference | Guarded — §25 |
| No readiness/recovery scoring | NOT EXPOSED — §21 |
| No check-in-due inference | NOT EXPOSED — §21 |
| No migration / schema change | None — §29 |
| No fixture fallback in the production route (PR2A invariant) | Guarded — §25 |
| PR2B `plan_mutation/document.py` residual untouched | Untouched |
| PR4/PR5 not started | Not started — §33 |

---

## 5. Existing authority map (discovery)

| Authority | What it owns |
|---|---|
| `app/services/workout_state/` | The canonical current-workout-state authority (Sprint 7 PR1 / Sprint 12 PR1). Resolves five dimensions — schedule, execution, plan-relationship, action, and one dominant `primary_state` — plus `completed_today` and `is_rest_day`. Read-only, fail-safe, no AI. |
| `app/services/today_facts.py` | The active-plan selector `get_active_plan`, plus `read_ok` / `has_active_plan` / `workout_completed_today`. Thin. |
| `app/services/today_presenter.py` | Thin presentation of those facts for the web template. |
| `app/services/workout_state/serialization.py` | Pure, query-free serializers, including `serialize_today_plan` — the bounded plan-day projection `/training/bootstrap` publishes. |
| `app/services/workout_state/snapshot.py` | `coherent_read_snapshot()` — one coherent read transaction (PostgreSQL REPEATABLE READ; a fresh transaction on SQLite). |
| `app/timeutil.py` | The single clock authority (Europe/Istanbul), with `audit_clock()` for hermetic tests. |
| `app/blueprints/mobile_api.py` | The single `/api/v1` blueprint: one no-store policy, one error envelope, one 429 handler, one `MOBILE_AUTH_ENABLED` gate, one approved-route allow-list. |
| `app/mobile_auth_middleware.py` | `require_mobile_auth` → `g.mobile_user`. |

**The key discovery finding:** `today_facts` and `today_presenter` are
deliberately thin, and the real canonical Today *composition* lives in
`/training/bootstrap`. There was no reusable "Today object" to extract.

---

## 6. Web Today composition

`/training/bootstrap` (`app/blueprints/training.py`) does the following, in
order:

1. captures auth, the canonical date and the sessions flag **before** the
   read boundary, so one response describes one contract;
2. opens `snapshot.coherent_read_snapshot()`;
3. calls `get_active_plan(user_id)`;
4. calls `resolve_workout_state(user_id, today=…, plan=…, sessions_enabled=…,
   strict_reads=True)`;
5. feeds the result into the pure serializers;
6. fails closed — any read/schema/boundary error becomes one generic
   `bootstrap_unavailable` 5xx, never partial or contradictory data.

---

## 7. Mobile API auth boundary

`require_mobile_auth` verifies an opaque `Authorization: Bearer <credential>`
and populates `g.mobile_user`. It consults no browser cookie; a valid
Flask-Login session alone gets `401` in the shared JSON envelope, never an HTML
redirect. Registration of the whole `/api/v1` surface is gated by
`MOBILE_AUTH_ENABLED` (default `False`, unchanged).

---

## 8. Chosen API layering

```
GET /api/v1/today                       app/blueprints/mobile_today.py   (thin route)
  → g.mobile_user                       the verified principal, and nothing else
  → mobile_today.build_today(user_id)   app/services/mobile_today.py     (projection)
      → coherent_read_snapshot()
      → app_today()                     the canonical Istanbul day
      → today_facts.get_active_plan
      → workout_state.resolve_workout_state(strict_reads=True)
      → workout_state.serialization.serialize_today_plan
  → _project(...)                       pure serialization: no reads, no decisions
```

**PR3 performs the `/training/bootstrap` composition rather than refactoring it
into a shared helper.** That was a deliberate choice: extracting a shared
composition would have edited a live browser surface for no functional gain,
whereas repeating ~10 lines of orchestration guarantees the web Today path is
byte-identical. The cost is bounded and the guards below prevent the copy from
drifting into an independent authority.

---

## 9. Route contract

| | |
|---|---|
| Method / path | `GET /api/v1/today` |
| Blueprint | `mobile_api` (the single mobile blueprint) |
| Auth | `@require_mobile_auth` — opaque Bearer credential |
| Input | **none**: no query parameter, body, form, or header beyond the auth boundary |
| Success | `200 application/json`, `Cache-Control: no-store` |
| Failure | `503` `TODAY_TEMPORARILY_UNAVAILABLE` (retryable) via the shared error envelope; `401` `AUTH_SESSION_EXPIRED` at the boundary |
| Provider calls | 0 |
| Queries | 4 bounded `SELECT`s, constant across states |

Full field-by-field documentation lives in
[`docs/MOBILE_TODAY.md`](../../MOBILE_TODAY.md).

---

## 10. Field-by-field authority table

| Field | Type | Null? | Canonical source | PR3 behaviour |
|---|---|---|---|---|
| `today.date` | `str` `YYYY-MM-DD` | never | `app_today()` | Re-published verbatim; the same day the snapshot was resolved for |
| `today.server_time` | `str` ISO-8601 UTC `…Z` | never | `app_now()` | Derived from the one clock authority, so a frozen clock freezes both fields |
| `today.status` | `str` enum | never | `snapshot.primary_state` | Re-exported **verbatim** — no remapping, no fourth vocabulary |
| `today.action` | `str` enum | never | `snapshot.action` | Re-exported verbatim |
| `today.workout.schedule_state` | `str` enum | never | `snapshot.schedule_state` | Re-exported verbatim |
| `today.workout.is_rest_day` | `bool` | never | `snapshot.is_rest_day` | Re-exported verbatim; never derived here |
| `today.workout.completed` | `bool` | never | `snapshot.completed_today` | Re-exported verbatim; never derived here |
| `today.workout.summary` | `obj` | **yes** | `serialize_today_plan` | Narrowed to `focus`, `duration_min`, `estimated_calories`, `exercise_count`; `null` when there is no publishable day |
| `today.workout.session` | `obj` | **yes** | `snapshot.session` | Passed through; `null` unless `FITX_WORKOUT_SESSIONS_ENABLED` is on |
| `today.plan.exists` | `bool` | never | `get_active_plan(...) is not None` | The canonical selector's answer |
| `today.plan.created_at` | `str` ISO-8601 UTC | **yes** | `TrainingPlan.created_at` | `null` iff no plan |
| `today.daily_context.plan_lineage` | `str` | **yes** | `TrainingPlan.lineage_id` | Read straight off the row; `null` when no plan |
| `today.daily_context.mutation_version` | `int` | **yes** | `TrainingPlan.mutation_version` | Read straight off the row; `null` when no plan |
| `today.daily_context.canonical_local_date` | `str` `YYYY-MM-DD` | never | `app_today()` | Always equals `today.date` |
| `today.state` | `obj` | never | `snapshot.to_dict()` | The `/workout/status` envelope carried verbatim |

### Required authority table (§65 of the brief)

| Concept | Canonical owner | PR3 behaviour |
|---|---|---|
| canonical local date | `app/timeutil.app_today()` (Europe/Istanbul) | Projected as `date` + `daily_context.canonical_local_date` |
| plan existence | `today_facts.get_active_plan` | Projected as `plan.exists` |
| plan lineage | `TrainingPlan.lineage_id` (`secrets.token_urlsafe(32)`) | Projected as `daily_context.plan_lineage`; `null` when no plan |
| mutation version | `TrainingPlan.mutation_version` | Projected as `daily_context.mutation_version`; `null` when no plan |
| workout / rest state | `workout_state.resolve_workout_state` | Projected as `status`, `workout.schedule_state`, `workout.is_rest_day` |
| workout completion | the same resolver's `completed_today` (today's `PumpCheck`) | Projected as `workout.completed` |
| workout identity | `WorkoutSession.public_id`, only under `FITX_WORKOUT_SESSIONS_ENABLED` | Passed through inside `workout.session`; `null` at the current default. No other workout identity exists in this repository, so none is invented |
| primary action | `snapshot.action` (deterministic, part of the canonical contract) | Projected as `action` |
| readiness | **no authority exists** | **NOT EXPOSED** |
| recovery / strain | **no authority exists** | **NOT EXPOSED** |
| check-in due | **no authority exists** | **NOT EXPOSED** |
| pending proposal | Coach-turn machinery only (`plan_confirmation` / `coach_confirmation`) — not a Today authority | **NOT EXPOSED** |
| plan change explanation | **no authority exists** | **NOT EXPOSED** |
| next best action | **no authority exists** beyond the deterministic `action` above | **NOT EXPOSED** |

---

## 11. Status vocabulary

`status` is `snapshot.primary_state`, re-exported verbatim. `docs/WORKOUT_STATE.md`
owns these names; PR3 defines none of its own.

| Value | Meaning |
|---|---|
| `scheduled_not_started` | A workout is scheduled today and is not complete |
| `completed` | Today's workout is proven complete |
| `rest_day` | The canonical schedule says today is a rest day |
| `no_plan` | No active plan exists — **not** a rest day |
| `needs_attention` | The plan exists but its schedule could not be read |
| `in_progress` | Only under `FITX_WORKOUT_SESSIONS_ENABLED`: a resumable session exists |

Held distinctions: `no plan != rest day`, `unavailable != empty`,
`not completed != incomplete data`.

---

## 12. Example — active workout

Sanitized test data, captured from the real endpoint.

```json
{ "today": {
  "date": "2026-07-23",
  "server_time": "2026-07-23T12:00:00Z",
  "status": "scheduled_not_started",
  "action": "start",
  "workout": {
    "schedule_state": "scheduled", "is_rest_day": false, "completed": false,
    "summary": { "focus": "Tum vucut", "duration_min": 45,
                 "estimated_calories": 320, "exercise_count": 2 },
    "session": null },
  "plan": { "exists": true, "created_at": "2026-07-01T08:00:00Z" },
  "daily_context": { "plan_lineage": "IFFkovLw3diUtHjuNdYMRzCa2Imf8dcZmVTQtxT1hMc",
                     "mutation_version": 0, "canonical_local_date": "2026-07-23" },
  "state": { "contract_version": 1, "today": "2026-07-23",
             "schedule_state": "scheduled", "execution_state": "no_execution",
             "plan_relationship": "indeterminate", "action": "start",
             "primary_state": "scheduled_not_started", "completed_today": false,
             "is_rest_day": false, "stale_previous_workout": false,
             "anomaly": null } } }
```

## 13. Example — rest day

```json
{ "today": {
  "date": "2026-07-23", "server_time": "2026-07-23T12:00:00Z",
  "status": "rest_day", "action": "none",
  "workout": { "schedule_state": "rest_day", "is_rest_day": true,
               "completed": false,
               "summary": { "focus": "-", "duration_min": 0,
                            "estimated_calories": 0, "exercise_count": 0 },
               "session": null },
  "plan": { "exists": true, "created_at": "2026-07-01T08:00:00Z" },
  "daily_context": { "plan_lineage": "FD7DWbNHMpRG2n77SAJYvpZ_zpd07G-XS-VPUpcSpJE",
                     "mutation_version": 0, "canonical_local_date": "2026-07-23" },
  "state": { "…": "primary_state: rest_day, schedule_state: rest_day, is_rest_day: true" } } }
```

## 14. Example — no plan

```json
{ "today": {
  "date": "2026-07-23", "server_time": "2026-07-23T12:00:00Z",
  "status": "no_plan", "action": "none",
  "workout": { "schedule_state": "no_plan", "is_rest_day": false,
               "completed": false, "summary": null, "session": null },
  "plan": { "exists": false, "created_at": null },
  "daily_context": { "plan_lineage": null, "mutation_version": null,
                     "canonical_local_date": "2026-07-23" },
  "state": { "…": "primary_state: no_plan, schedule_state: no_plan, is_rest_day: false" } } }
```

Note `is_rest_day: false` and `summary: null`. No plan is **not** a rest day, and
`summary: null` never means rest.

## 15. Example — completed

```json
{ "today": {
  "date": "2026-07-23", "server_time": "2026-07-23T12:00:00Z",
  "status": "completed", "action": "none",
  "workout": { "schedule_state": "scheduled", "is_rest_day": false,
               "completed": true,
               "summary": { "focus": "Tum vucut", "duration_min": 45,
                            "estimated_calories": 320, "exercise_count": 2 },
               "session": null },
  "plan": { "exists": true, "created_at": "2026-07-01T08:00:00Z" },
  "daily_context": { "plan_lineage": "kRwSSBZ5vpx1UZUFCUQBxcW1Z5w09vvqd8fI5qO3Prc",
                     "mutation_version": 0, "canonical_local_date": "2026-07-23" },
  "state": { "…": "execution_state: completed, plan_relationship: matches_scheduled" } } }
```

### Bonus example — unreadable schedule (degraded, but a *product* state)

```json
{ "today": {
  "status": "needs_attention", "action": "blocked",
  "workout": { "schedule_state": "schedule_unavailable", "is_rest_day": false,
               "completed": false, "summary": null, "session": null },
  "plan": { "exists": true, "created_at": "2026-07-01T08:00:00Z" },
  "state": { "anomaly": "schedule_unparseable", "…": "" } } }
```

An unparseable `plan_data` is a domain condition, not an infrastructure fault.
`_plan_content()` catches exactly the set `workout_state.queries._load_schedule`
catches (`ValueError`, `TypeError`), so the summary and the canonical schedule
state can never disagree about whether the plan was readable — and the honest
"your plan needs attention" survives instead of becoming a `503`.

---

## 16. Daily context identity

`daily_context = (plan_lineage, mutation_version, canonical_local_date)`.

- All three are **server-owned tokens read straight off the canonical plan row**
  plus the canonical clock. Nothing is hashed, minted, defaulted, or combined
  into a presentation key.
- `plan_lineage` is `secrets.token_urlsafe(32)` — opaque, non-guessable, not a
  database key, not derived from user data. It carries no sensitive content.
- **No internal integer primary key is exposed anywhere in the response**
  (asserted directly by
  `test_daily_context_carries_no_internal_database_identifier`).
- With no plan, lineage and version are `null`. Fabricating them would make a
  client believe in a plan the server does not have.
- No new table, no new persisted Today model, no migration.

`test_mutation_version_moves_with_the_canonical_plan_version` pins that a plan
mutation moves `mutation_version` while `plan_lineage` stays stable — which is
exactly the signal PR4 needs to invalidate a cached day.

---

## 17. Canonical date / timezone behaviour

- `app/timeutil` is the only clock. `app_today()` supplies the day;
  `app_now().astimezone(UTC)` supplies `server_time`. Because both come from
  one authority, they can never disagree, and `audit_clock()` freezes both.
- The projection performs **no** timezone conversion of its own and contains no
  `date.today()` — a source guard enforces this, and the guard is proven
  non-vacuous by mutating `day = app_today()` to `day = date.today()` and
  observing it trip.
- A client's date, timezone, locale or parameter cannot move Today:
  `?date=2026-01-01`, `?today=2026-01-01`, `X-Date:` and `X-Timezone:` are all
  inert, asserted byte-for-byte against an untampered baseline.
- Boundary regression:
  `test_late_utc_evening_reports_the_next_istanbul_day` freezes the clock to
  `2026-07-23 01:30 Istanbul` — `22:30Z on 22 July` — and asserts the response
  says **23 July**.
- `date == state["today"] == daily_context.canonical_local_date` is asserted, so
  the three can never drift.

---

## 18. Auth / ownership behaviour

| Case | Result |
|---|---|
| No `Authorization` header | `401`, JSON envelope `{code, message, retryable, request_id}`, `Cache-Control: no-store`, **no** `Location` header (never an HTML redirect) |
| Malformed header (`Basic …`) | `401` `AUTH_SESSION_EXPIRED` |
| Rejected/revoked credential | `401` `AUTH_SESSION_EXPIRED` via the shared envelope |
| Valid credential | Only that user's Today |
| `?user_id=`, `?user=`, `?username=`, `?owner_id=`, `?date=`, `?today=` | All inert — response identical to the untampered baseline |
| `X-User-Id:`, `X-Date:` headers | Inert |

Structurally: `build_today` accepts exactly one parameter (`user_id`, asserted
via `inspect.signature`), the route's source contains no `request.args`,
`get_json`, `form`, `headers`, `values` or `user_id=`, and
`view._require_mobile_auth is True`. Removing the decorator is the §26 mutation
proof and it fails 34 tests.

---

## 19. Error / degraded behaviour

| | |
|---|---|
| Strictness | `resolve_workout_state(..., strict_reads=True)` inside `coherent_read_snapshot()` |
| Failure type | Any exception in the composition → `TodayUnavailable` (deliberately distinct from every product state) |
| HTTP | `503` `TODAY_TEMPORARILY_UNAVAILABLE`, `retryable: true`, shared `/api/v1` error envelope |
| Never | Downgraded into an empty Today, a rest day, or a `no_plan` |
| Never | A `401` — a storage fault is not an authentication outcome, and a client reading it as one would discard a good session and send the user back to login |
| Leakage | None: no stack trace, no DB error, no ORM repr reaches the client. The log line carries an exception **type name** and a request id only — never a plan, a workout, an injury or an account identifier |

`test_a_canonical_read_failure_is_an_error_not_a_fabricated_empty_today` proves
this for a failure in `resolve_workout_state` **and** for a failure in
`get_active_plan`, asserting in both cases that the body is an error envelope
and contains no `no_plan` / `rest_day` state.

---

## 20. Adaptive Coaching behaviour with flags OFF

`FITX_ADAPTIVE_COACHING_ENABLED` and `FITX_COACH_PLAN_TOOLS_ENABLED` both
default `False` and are unchanged by PR3.
`test_today_is_correct_with_every_adaptive_coaching_flag_off` asserts a fully
correct Today under both flags off. The endpoint reads nothing Adaptive Coaching
owns: it does not touch `plan_mutation`, the mutation journal, coach turns, or
confirmation state. Turning those flags on changes nothing in this response.

`FITX_WORKOUT_SESSIONS_ENABLED` (also default `False`) is read **once** per
request via the same `current_app.config.get(...)` pattern
`/training/bootstrap` uses and passed into the resolver, so one response always
describes one contract version. OFF ⇒ `contract_version: 1` and
`workout.session: null`.

---

## 21. Explicitly excluded concepts

Asserted absent from the response body by
`test_no_speculative_or_unowned_concept_appears_in_the_contract`:

`readiness`, `recovery`, `strain`, `check_in`, `checkin`, `pending_proposal`,
`proposal`, `why_plan_changed`, `plan_change_reason`, `next_best_action`,
`recommendation`, `nutrition`, `calorie_goal`.

Each is excluded because **no canonical authority owns it**. Inventing any of
them at the API boundary is precisely the second-Today-authority failure mode
this PR exists to avoid. Pending-proposal state in particular exists only inside
Coach-turn machinery (`plan_confirmation` / `coach_confirmation`), which is not
a Today authority, so it is not surfaced here.

---

## 22. No-LLM proof

Two independent proofs:

1. **Static** — `test_no_provider_or_ai_module_is_reachable_from_the_today_read`
   walks the import graph of both modules and asserts no `openai`, `anthropic`,
   `bedrock`, `boto3`, `ai_`, `llm`, `coach` or provider-client module appears.
2. **Runtime** — `test_today_succeeds_while_every_provider_client_would_explode`
   replaces `extensions.openai_client` and `extensions.bedrock_client` with a
   `_Detonator` object that raises on **any** attribute access, then performs a
   real request and asserts `200` with correct canonical content.

**Exact provider-call count: 0.**

---

## 23. Web / mobile parity proof

`test_mobile_today_agrees_with_the_web_canonical_facts` is parametrized over
three persisted worlds (scheduled, rest day, completed). For each it performs
the mobile request, then **independently** calls the same canonical reads the
web surfaces use, on the same frozen day, and asserts:

```
payload["status"]                    == web_snapshot.primary_state
payload["action"]                    == web_snapshot.action
payload["workout"]["is_rest_day"]    == web_snapshot.is_rest_day
payload["workout"]["completed"]      == web_snapshot.completed_today
payload["workout"]["schedule_state"] == web_snapshot.schedule_state
payload["plan"]["exists"]            == (get_active_plan(user_id) is not None)
payload["date"]                      == web_snapshot.today.isoformat()
payload["state"]                     == web_snapshot.to_dict()      # the whole envelope
```

The last line is the strongest: the entire canonical envelope must match, not
just the fields mobile happens to mirror. Parity here is **structural** — both
surfaces are fed by the same resolver — and the test pins that it stays so.

---

## 24. Performance / query observations

Measured with a SQLAlchemy `before_cursor_execute` listener on a real request:

| State | Statements |
|---|---|
| Plan + completion | **4** |
| No plan | **4** |

The four are: the authenticated principal, the active plan, the execution
evidence (`workout_log`), and today's `PumpCheck`. One read per canonical
authority.

- **No N+1**: the count does not grow with plan content or exercise count.
- **No duplicate load**: the plan row is fetched once by `get_active_plan` and
  **passed into** the resolver rather than re-queried — asserted permanently by
  `test_today_costs_a_constant_bounded_number_of_queries`, which caps the budget
  at 5 statements and asserts `FROM training_plan` appears at most once, in both
  the plan and no-plan worlds.
- **No mutation-state recomputation**: lineage and version are read off the row
  already loaded.
- No write, flush, commit, lock, cache layer or HTTP call. No Redis added.
- `Cache-Control: no-store` from the shared blueprint policy — this is
  authenticated per-user state and is never publicly cacheable
  (`test_response_is_never_publicly_cacheable`).

---

## 25. Architecture guard

`tests/test_mobile_today_architecture.py` (20 tests). Guards operate on
`_code_only(path)` — the module's executable source with comments and docstrings
stripped via `ast.unparse` — so a prose explanation of a forbidden construct
cannot trip a guard, and the next author is never tempted to delete the
explanation instead of the construct.

| Guard | Asserts |
|---|---|
| delegation | `resolve_workout_state`, `get_active_plan`, `serialize_today_plan`, `app_today` are all called |
| no fitness reads | `app.models` / `app.extensions` not imported by the service; no `.query`, no `db.session` |
| no rest/completion inference | `is_rest_day` / `completed` come from `snapshot.*`; no `'dinlenme'` comparison, no marker/log inference |
| no date derivation | no `date.today`, no `datetime.now`, no `utcnow`, no second `ZoneInfo` |
| no client input | route source has no `request.args` / `get_json` / `form` / `headers` / `values` / `user_id=`; `build_today` takes exactly `["user_id"]` |
| thin route | exactly one `build_today` call; no state vocabulary in the route |
| no provider | import-graph guard + runtime detonator |
| parity | 3 parametrized worlds vs. independently computed canonical reads |
| feature gate | `MOBILE_AUTH_ENABLED=0` ⇒ **no** `/api/v1` rule registered at all |
| auth decorator | `view._require_mobile_auth is True` |
| single surface | `/api/v1` Today rules == exactly `{/api/v1/today, /api/v1/nutrition/diary/today}` — a third would mean a competing Today read |
| one blueprint | the endpoint belongs to `mobile_api` |
| read projection | no `db.Model`, `session.add/commit/flush`, `op.create_table`, `sa.Column`; no migration named `*today*` |
| production truth | no `fixture` / `sample` / `demo` / `placeholder` / `dummy` / `fallback` token, and no hard-coded plan **values** (`antrenman`, `dinlenme`, a `program` literal) in the production path |
| query budget | ≤ 5 statements, plan loaded ≤ once, in both the plan and no-plan worlds |

Route availability is additionally pinned in
`tests/test_mobile_auth_feature_gate.py`: `/api/v1/today` returns `404` when the
gate is off, and appears in the **exhaustive** approved-route allow-list when it
is on — which also proves PR3 registered no second, ungated `/api/v1` rule.

---

## 26. Mutation / non-vacuity proof

### Automated (permanent)

`test_the_delegation_guards_are_not_vacuous` applies three mutations to the
**real** service source and asserts each makes a guard raise `AssertionError`,
plus a positive control on the unmutated source:

| Mutation | Guard that trips |
|---|---|
| `'is_rest_day': snapshot.is_rest_day` → `today_plan['tip'] == 'dinlenme'` | delegation / rest-day inference |
| `'completed': snapshot.completed_today` → `'completed': completed` | delegation / completion inference |
| `day = app_today()` → `day = date.today()` | date derivation |

The guards are extracted into `assert_delegates_rest_and_completion(source)` and
`assert_derives_no_date(source)`, used by both the live guards and the
non-vacuity test, so the two can never drift apart.

### Manual (this session)

Representative mutation from the brief's list: **remove the auth decorator**.

```diff
 @bp.get("/today")
-@require_mobile_auth
 def today():
```

Result on `tests/test_mobile_today_api.py` + `tests/test_mobile_today_architecture.py`:

```
34 failed, 14 passed
```

Including `test_today_route_carries_the_shared_mobile_auth_decorator`, the whole
401 boundary, every ownership/tampering case, every canonical-state test, and
all three parity cases.

**Reverted completely** via `git checkout --`; the tree was verified clean
(`git status --short` empty, `require_mobile_auth` present twice in the file)
and the same two suites re-run: **48 passed**. No mutation artifact is committed.

---

## 27. Focused validation

All runs on this branch, exit code 0.

| Suite | Result |
|---|---|
| `test_mobile_today_api.py` + `test_mobile_today_architecture.py` | **48 passed** |
| `test_mobile_auth_feature_gate.py` | **11 passed** |
| `test_mobile_today_*` + `test_workout_state.py` + `test_workout_state_sessions.py` + `test_today_v2.py` | **163 passed** |
| `test_mobile_auth_api/service/models` + `test_mobile_credentials` + `test_mobile_nutrition_api` + `test_mobile_pump_check_api` | **180 passed** |
| `test_plan_mutation` + `test_plan_mutation_history` + `test_plan_v2` + `test_training_routes` + `test_training_planning` | **234 passed** |
| `test_migration_graph.py` | **10 passed** |

No PR3 regression appeared in any of them; nothing needed fixing outside PR3's
own files.

---

## 28. Full backend suite

The whole backend suite: **217 test modules**, run as 8 sequential batches of
28 (last 21) because a single `pytest -q` process exhausts memory on this
machine. Batching changes nothing about isolation - each module gets the same
fixtures either way - and every batch was run from this branch's working tree.

### First run - 2 failures, both real

| Batch | Modules | Result | Exit |
|---|---|---|---|
| 00 | 28 | 585 passed | 0 |
| 01 | 28 | 543 passed | 0 |
| 02 | 28 | 501 passed | 0 |
| 03 | 28 | 493 passed, 3 skipped | 0 |
| 04 | 28 | 556 passed, 4 skipped | 0 |
| 05 | 28 | 782 passed, 1 skipped | 0 |
| 06 | 28 | **2 failed**, 952 passed | **1** |
| 07 | 21 | 674 passed, 4 skipped | 0 |

```
FAILED tests/test_sprint12_daily_coach_discovery.py::test_mobile_api_publishes_only_auth_nutrition_and_pump_check
FAILED tests/test_sprint12_daily_coach_discovery.py::test_no_mobile_endpoint_serves_a_daily_coach_domain[today]
```

These were **not** flakes and not incidental breakage. They are Sprint 12 PR1's
discovery characterization tests, and they failed for exactly the reason PR1
wrote them:

- `test_mobile_api_publishes_only_auth_nutrition_and_pump_check` enumerates the
  mobile contract as an **exact set**, with a docstring saying the exactness is
  deliberate because "a subset assertion would not notice one arriving".
  `/api/v1/today` arrived.
- `test_no_mobile_endpoint_serves_a_daily_coach_domain[today]` asserted that the
  nutrition diary was the *only* `/api/v1` path containing "today", to prove no
  Today aggregate existed yet.

Both were true statements about the pre-PR3 world and false about the post-PR3
one. PR1's own header says these tests exist "so that a later PR that changes
one of these facts has to say so out loud". This is that PR, and this section is
saying so.

Worth noting how they were caught: the focused suites in section 27 all passed,
and so did every mobile, Today, workout-state and training suite. Only the full
run reached PR1's discovery module. That is the argument for this section
existing as a gate rather than a formality.

### 28.1 What was changed, and what was deliberately not

Fixed in `e1c53f6`, without weakening either assertion:

| Test | Change | Why it does not weaken the guard |
|---|---|---|
| exact-set | `/api/v1/today` added to the set | The set stays **exact**. Any *further* path still fails it. A note records why this one addition does not invalidate the finding: it publishes no new authority, it projects the canonical workout state the web app already serves |
| `[today]` case | Exception widened from a bare `!= "/api/v1/nutrition/diary/today"` to a named `_APPROVED_TODAY_PATHS` allow-list of exactly two | The case keeps its teeth and arguably gains some. It no longer proves "no Today aggregate exists" - it proves **no *second* one does**. `/api/v1/coach/today` or `/api/v1/training/today` appearing later still fails here, which is precisely PR3's one-authority rule |

Deliberately **not** done:

- The tests were **not** renamed, so the PR1 discovery report's F1 traceability
  row (`...-pr1-daily-coach-convergence-discovery.md:1298`) still resolves to
  them. A stale-sounding name is a smaller cost than a broken audit trail; the
  docstrings carry the correction.
- The tests were **not** deleted, skipped, `xfail`ed, or loosened to a subset
  assertion. Every one of those would have silently retired PR1's finding.
- The PR1 discovery report itself was **not** edited. It is a dated historical
  record of what was true on 2026-08-23; superseding it is this report's job.
- The other six domains (`training`, `workout`, `plan`, `progress`, `coach`,
  `checkin`) were left untouched and still pass - `/api/v1/today` contains none
  of them, which is itself a small piece of evidence that PR3 did not smuggle a
  domain onto mobile.

### Re-run - clean

Batch 06 re-run in full after the fix:

```
954 passed, 4851 warnings in 1226.55s (0:20:26)          exit 0
```

### Totals

| | |
|---|---|
| Test modules | **217** |
| **Passed** | **5088** |
| **Failed** | **0** |
| **Errors** | **0** |
| Skipped | 12 (pre-existing, unrelated to PR3) |
| Deselected | 0 |
| `xfail` / `xpass` | 0 |
| **Exit code** | **0** (every batch) |

PR3 contributes **52** of those tests: 31 in `test_mobile_today_api.py`, 20 in
`test_mobile_today_architecture.py`, and 1 added parametrization in
`test_mobile_auth_feature_gate.py`.

**No test outside PR3's own files fails, and no pre-existing test was disabled,
skipped, or relaxed.** The two assertions that changed are catalogued above in
full.

---

## 29. Static / migration validation

| Check | Result |
|---|---|
| `python -m compileall app tests` | exit 0 |
| `git diff --check origin/main` | clean, no whitespace errors |
| `git diff --stat origin/main -- migrations/` | **empty — no migration** |
| `git diff --name-only origin/main -- app/models.py` | **empty — no schema change** |
| Alembic heads | `['c2d3e4f5a6b7']`, count **1** |
| `tests/test_migration_graph.py` | 10 passed |
| Route registration | `/api/v1/today` present with the gate on, absent with it off; endpoint on `mobile_api` |
| Import sanity | both new modules import cleanly; the route module is registered from the single bottom import block in `mobile_api.py` |

---

## 30. Independent review

Run after implementation and focused validation, across the brief's 17
dimensions.

| # | Dimension | Finding |
|---|---|---|
| 1 | authority duplication | Clean. Every fact delegated; guards + non-vacuity proof |
| 2 | auth / ownership | Clean. Principal-only, no tamperable input, 401 envelope correct |
| 3 | timezone / date | Clean. One clock, boundary regression pinned, client input inert |
| 4 | no-plan vs rest | Clean. Distinct states, asserted; `summary: null` never means rest |
| 5 | completion | Clean. Canonical `PumpCheck`; past completion and execution evidence both proven not to make today complete |
| 6 | response contract quality | **P1-1** (fixed) + **P2-1** (accepted) |
| 7 | nullability / type stability | Clean. Key set identical in every state; nullability documented |
| 8 | provider budget | Clean. 0 calls, statically and at runtime |
| 9 | feature flags | Clean. No default changed; correct with AC flags off; sessions flag captured once |
| 10 | privacy / security | Clean. No internal PK, opaque lineage token, no plan/injury in logs, `no-store` |
| 11 | N+1 / performance | Clean. 4 constant queries, plan loaded once, now permanently guarded |
| 12 | caching hazards | Clean. `no-store`, no server-side cache, no ETag |
| 13 | web/mobile parity | Clean. Whole canonical envelope compared |
| 14 | Adaptive Coaching scope creep | Clean. Nothing AC-owned is touched |
| 15 | readiness / check-in invention | Clean. NOT EXPOSED, asserted |
| 16 | migration / schema creep | Clean. None |
| 17 | PR4 work started | Clean. No `axisai_mobile` change; no Flutter code |

---

## 31. P0 / P1 fixes

**P0: 0.**

**P1-1 — the summary carried localized web copy and a second rest-day signal.**
*(fixed in `0a26273`)*

`workout.summary` shipped `day` (`"Perşembe"`) and `kind`
(`"antrenman"` / `"dinlenme"`). Two problems:

1. §49 — localized web prose inside a machine contract, guaranteeing
   English/Turkish API divergence downstream;
2. worse, `kind` handed a client a second way to answer *"is today a rest
   day?"* — one that is not the canonical authority and would drift from it the
   moment the plan document and the resolver disagreed. That is exactly the
   "duplicate rest-day inference" §2 forbids, exported to every client.

Both were also pure redundancy: `date` and `schedule_state` already answer both
questions canonically. Removed. `focus` stays and is documented as plan
*content* (free text an author wrote, like an exercise name) to display, never
to branch on.

The same commit added the two review-driven guards (production-truth,
query-budget) so the findings cannot regress.

---

## 32. Accepted P2s

| ID | Finding | Why accepted |
|---|---|---|
| **P2-1** | The response uses a `{"today": {…}}` root, whereas `/api/v1/nutrition/diary/today` uses flat named top-level keys — a mild envelope inconsistency across the mobile surface. | Both shapes are stable, typed and documented, so neither harms PR4. The namespaced root leaves room for a PR5 sibling block (e.g. coaching) without renaming anything. Not worth churning a fully tested contract. Revisit if a third mobile surface settles the house style. |
| **P2-2** | `today.state` duplicates `status`, `action`, `workout.schedule_state`, `is_rest_day` and `completed`, so the response carries each canonical fact twice. | Deliberate: carrying the `/workout/status` envelope verbatim is what makes parity *structural* rather than merely asserted, and it gives a later client `execution_state` / `anomaly` / `plan_relationship` without a contract change. Ambiguity is resolved in documentation: the flattened projection is the integration target, `state` is forward-compatible passthrough. |
| **P2-3** | `summary.focus` is free text in the plan's authoring language (`"Tum vucut"`, `"-"` on a rest day), so it cannot be localized client-side. | It is plan **content**, in the same category as an exercise name, and the repository has no canonical focus taxonomy to map it onto. Inventing one at this boundary would be a new authority. Documented as display-only. |

---

## 33. Files changed

```
 CLAUDE.md                                    |   1 +
 app/blueprints/mobile_api.py                 |   1 +
 app/blueprints/mobile_today.py               |  51 ++++
 app/services/mobile_today.py                 | 225 ++++++++++++++
 docs/MOBILE_TODAY.md                         | 267 +++++++++++++++++
 tests/test_mobile_auth_feature_gate.py       |   4 +
 tests/test_mobile_today_api.py               | 508 +++++++++++++++++++++++++++
 tests/test_mobile_today_architecture.py      | 458 ++++++++++++++++++++++++
 tests/test_sprint12_daily_coach_discovery.py |  45 ++-
 9 files changed, 1552 insertions(+), 8 deletions(-)
```

`tests/test_sprint12_daily_coach_discovery.py` is the one pre-existing *test*
file whose assertions changed, and it changed by design: PR1 wrote those two
tests as an exact set precisely so that a later PR adding a mobile domain would
have to restate the finding out loud. §28 records how it was found and §28.1
what was changed.

Plus this report. **No file outside `app/`, `tests/`, `docs/` and `CLAUDE.md`
was touched.** The only edit to a pre-existing production file is one import
line appended to `app/blueprints/mobile_api.py`'s bottom import block, which is
how every other mobile product route is registered. No web route, template,
model, migration, flag default or `.env` was modified. `axisai_mobile` was not
touched.

---

## 34. Commits

| SHA | Message |
|---|---|
| `8e85dba` | `feat(api): add canonical mobile Today read projection` |
| `25df686` | `test: guard Today projection delegation, parity and zero-provider cost` |
| `5bc58db` | `test: cover /api/v1/today in the mobile approved-route gate` |
| `0a26273` | `fix(api): drop localized weekday and day-type from the Today summary` |
| `389826f` | `docs: record the canonical mobile Today contract` |
| `e1c53f6` | `test: record the canonical Today aggregate in the PR1 discovery baseline` |
| *(this report)* | `docs: record Sprint 12 PR3 validation` |

Contract tests were written and run red before the implementation; the guard,
gate, review-fix and documentation commits follow the real dependency order.

---

## 35. Final SHA

```
e1c53f6de72ee12fe2bb16d8298ed4dcf1540479
```

`e1c53f6` - `test: record the canonical Today aggregate in the PR1 discovery
baseline` - is the last code commit on the branch, and the SHA every number in
sections 27, 28 and 29 was measured at. This report is committed on top of it as
`docs: record Sprint 12 PR3 validation`; that commit adds no code, so the
validated tree is `e1c53f6`.

Branch `sprint12-pr3-canonical-mobile-today-api`, based on `origin/main`
`c72740a`, **6 commits ahead, 0 behind**. Working tree clean, no untracked
files. **Not pushed. No PR opened. Not merged. Not deployed.**

---

## 36. Final repository state

| | |
|---|---|
| Repository | `C:\Users\yusuf\develop\fitness-coach` |
| Worktree | `.worktrees/sprint12-pr3-canonical-mobile-today-api` |
| Branch | `sprint12-pr3-canonical-mobile-today-api` |
| Base SHA | `c72740a` |
| Final production SHA | `0a26273` (last commit touching `app/`) |
| Final HEAD | *(see §35)* |
| vs `origin/main` | 6 ahead, 0 behind |
| Working tree | clean |
| Untracked files | none |
| Migration head | `c2d3e4f5a6b7` (single head, no new migration) |
| Push status | **not pushed** |
| PR status | **no PR opened** |
| Merge status | **not merged** |
| Deploy status | **not deployed** |
| `MOBILE_AUTH_ENABLED` | unchanged, still default `False` — **not activated** |
| `AXISAI_NATIVE_AUTH_ENABLED` | **not modified** |
| `axisai_mobile` repository | **not touched** |

---

## 37. PR4 handoff contract

What Sprint 12 PR4 (Mobile Today Real-Data Convergence) may rely on. Full
reference: [`docs/MOBILE_TODAY.md`](../../MOBILE_TODAY.md).

**Endpoint.** `GET /api/v1/today`.

**Authentication.** Required: `Authorization: Bearer <opaque AxisAI access
credential>`, the same credential the rest of `/api/v1` uses. There is no user
parameter — the server resolves the owner from the credential. `401`
`AUTH_SESSION_EXPIRED` in the shared JSON envelope means re-authenticate; it is
never an HTML redirect. Note the endpoint is only reachable once
`MOBILE_AUTH_ENABLED` is turned on, which is a separate rollout step PR3 did not
perform.

**Status enum.** Branch on `today.status`:
`scheduled_not_started` · `completed` · `rest_day` · `no_plan` ·
`needs_attention` · `in_progress` (flag-gated). Treat an unknown member
additively — render a neutral state rather than crashing. Never collapse
`no_plan` into `rest_day`, and never treat `needs_attention` as either empty or
resting.

**Daily context.** Cache key: `(plan_lineage, mutation_version,
canonical_local_date)`. Same tuple ⇒ the cached day is still valid; a changed
`mutation_version` ⇒ the plan was mutated, discard; a changed
`canonical_local_date` ⇒ the server rolled to a new day. `plan_lineage` and
`mutation_version` are `null` when no plan exists — that is a valid cache key
meaning "no plan", not an error.

**Workout summary.** `today.workout.summary` is a bounded card:
`focus` (free text, display only), `duration_min`, `estimated_calories`,
`exercise_count`. It is **not** the exercise list; a screen needing exercises
fetches the existing workout surface. `summary: null` means there is no
publishable day — it **never** means rest day.

**Nullability.** Nullable: `workout.summary`, `workout.session`,
`plan.created_at`, `daily_context.plan_lineage`,
`daily_context.mutation_version`. Everything else is always present. The key set
is identical in every state, so PR4 can model one non-optional struct with those
five optional members.

**Empty state.** `status: no_plan`, `plan.exists: false`, `summary: null`,
lineage/version `null`, `is_rest_day: false`. Render "create a plan", never a
rest day and never a fabricated workout.

**Errors.** `503` `TODAY_TEMPORARILY_UNAVAILABLE` with `retryable: true` means
retry — **do not** log the user out, and **do not** render it as an empty or
resting Today. `401` means re-authenticate. `429` follows the shared mobile
throttling contract.

**Canonical date.** `today.date` is the server's Europe/Istanbul day and is
authoritative. Do **not** send a date, a timezone, or a device clock — there is
no parameter for it and the server would ignore it. Do **not** compute "today"
on the device and compare; use `date` / `canonical_local_date`.
`server_time` is for skew display only.

**Do not** implement rest-day, completion, or day-rollover logic client-side.
Every one of those answers is in this payload.

---

## 38. Final verdict

## READY TO SHIP

`GET /api/v1/today` exposes canonical Today state and does not create a second
Today authority.

**What that verdict rests on:**

- **Authority.** Every fact in the payload is delegated - the date to
  `app.timeutil`, the plan to `get_active_plan`, and rest-day, completion,
  schedule and action to `resolve_workout_state`. The service owns no `if` that
  decides a product fact. This is not asserted by inspection alone: three
  architecture guards forbid the alternative, and
  `test_the_delegation_guards_are_not_vacuous` mutates the real source three
  ways and proves each guard fails - so the guards cannot rot into decoration.
- **Parity.** The mobile payload is compared to the web resolver's own snapshot
  for the same user, same day, across three plan states, including the full
  `state` envelope. Mobile and web cannot disagree about today.
- **Truthfulness.** No plan, rest day, completed, and unavailable are four
  distinct observable states with distinct fields, each pinned by a test. A read
  failure fails closed to a 503 with a stable error code and no stack trace - it
  never becomes an empty product day. `summary: null` never means "rest".
- **Ownership.** The user id comes only from `g.mobile_user`; `build_today`'s
  signature is asserted to accept nothing else, and A-cannot-read-B is tested at
  the HTTP boundary. Removing `@require_mobile_auth` produced 34 failures - the
  auth guarantee is load-bearing, not incidental.
- **Blast radius.** No migration, no model change, no flag default changed, no
  `.env` touched, no web route or template modified, no AI/provider call (0,
  statically and at runtime), 4 constant queries now permanently budget-guarded.
  One import line is the only edit to a pre-existing production file.
- **Full suite green:** 5088 passed, 0 failed, 0 errors, exit 0.

**Conditions attached: none.** The three accepted P2s in section 32 are
documented design positions with no client-visible risk, not deferred work -
PR4 can build against this contract as written.

**Shipping still requires a human to push, review and merge**, which section 72
of the brief forbids me to do and I have not done. The branch sits local at
`e1c53f6`, 6 ahead of `origin/main`, unpushed, with no PR opened, nothing
merged, nothing deployed, and `MOBILE_AUTH_ENABLED` / `AXISAI_NATIVE_AUTH_ENABLED`
untouched at their existing defaults.
