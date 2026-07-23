# Canonical Workout State (Sprint 7 PR1)

The single, server-side owner of the answer to **"what is the user's current
workout state, and what may they do about it?"** Before this PR the answer was
inferred piecemeal by three authorities that could disagree (see *Background*).
`app/services/workout_state/` now resolves one deterministic, serializable
snapshot from existing canonical data — **read-only**: no writes, no repair, no
XP/challenge/notification side effects, no AI/external calls.

> This PR establishes the *contract and read model* only. It does **not** change
> workout mutation flows, implement stale-session recovery, add plan↔log linkage,
> or redesign the workout UI. Those are later Sprint 7 PRs.

## Owner & layering

`app/services/workout_state/`

| File | Role | Purity |
|---|---|---|
| `models.py` | Frozen `WorkoutStateSnapshot` / `WorkoutStateInputs` + enum constants | pure data |
| `queries.py` | Read-only ORM adapters that gather trusted facts | impure (DB) |
| `resolver.py` | `resolve(inputs) -> snapshot` classification | **pure** |
| `__init__.py` | `resolve_workout_state(user_id, *, today=None)` orchestrator + fail-safe + safe logging | impure |

Dependency direction is one-way: `queries → resolver`. The resolver never
imports the ORM or Flask, so the whole state matrix is unit-testable without a DB
(`tests/test_workout_state.py`). The package **consumes** established contracts
(`app.timeutil`, `app.services.training_history`, the plan generator's
`WEEKDAYS`/`VALID_TIPS`) and copies none of their heuristics.

## Canonical inputs & source precedence

The resolver derives everything from three existing sources — no new persisted
state, no schema change:

1. **Schedule** — the newest `TrainingPlan` (`created_at desc`), whose
   `plan_data.program` is a 7-element list of `{gun, tip}`. Today's row is the
   one whose `gun` equals today's Istanbul weekday (`WEEKDAYS[weekday()]`).
   `tip == "dinlenme"` → rest; `tip ∈ {antrenman, kardiyo}` → workout. Any parse
   failure (bad JSON, not a 7-list, today's day/tip missing or unrecognized) →
   `schedule_unavailable` — **never a silent rest day**.
2. **Completion (canonical)** — today's `PumpCheck` row (Istanbul day via
   `utc_day_bounds`/`app_date_of` on `created_at`). This is exactly the signal
   `GET /workout/status` and `complete_workout`'s idempotency guard already use.
   The synthetic `WORKOUT_COMPLETION_MARKER` WorkoutLog row is **corroborating
   evidence only**, not independent proof.
3. **Execution evidence** — non-marker `WorkoutLog` rows for the Istanbul day,
   read through the training-history foundation (`fetch_workout_entries`). These
   prove *started/in-progress* work (e.g. exercises logged via the AI coach) —
   **never** completion.

**Plan ↔ performance association** is by *local date + schedule kind only*.
`WorkoutLog` carries no planned-workout identifier, so an identifier-level match
(spec scenario 15) is **not derivable** in the current schema — a documented gap
for a later PR, not a silently-wrong guess.

**Conflict handling (deterministic, safe):** PumpCheck stays canonical if it and
its marker disagree (`completion_marker_mismatch` anomaly logged, completion
still stands). Today is evaluated independently of yesterday's latest row (a
historical record never becomes today's workout). Multiple same-day rows are all
"today's evidence" (presence, never "newest row wins"). Malformed schedule →
`blocked` action, `needs_attention` state; data is preserved and only safe
operational metadata is logged.

## State dimensions

Distinct concepts are modelled separately — never collapsed into one overloaded
`completed` boolean.

| Dimension | Field | Values |
|---|---|---|
| A. Schedule | `schedule_state` | `scheduled`, `rest_day`, `no_plan`, `schedule_unavailable` |
| B. Execution (today) | `execution_state` | `no_execution`, `in_progress`, `completed` |
| C. Plan relationship | `plan_relationship` | `matches_scheduled`, `unscheduled`, `unrelated_date`, `indeterminate` |
| D. Action eligibility | `action` | `start`, `resume`, `none`, `blocked` |
| E. Dominant state | `primary_state` | `rest_day`, `scheduled_not_started`, `in_progress`, `completed`, `unscheduled_in_progress`, `unscheduled_completed`, `no_plan`, `needs_attention` |

Diagnostics also exposed: `completed_today` (compat mirror of `/workout/status`),
`is_rest_day`, `stale_previous_workout` (kept as its **own** field so a prior-day
incomplete workout never contaminates *today*), `anomaly`, `today`,
`contract_version`.

### Dominant state (E) — the one field consumers should read

`primary_state` is the single deterministic answer so API/client consumers do not
recombine raw flags. `execution` outranks `schedule`; `unscheduled` variants are
chosen when execution exists on a rest/no-plan day (a rest day never erases an
unscheduled performed workout).

### Action (D) — why there is no separate `complete`

Actions are `start` (scheduled day, nothing logged), `resume` (real rows logged
today, not completed), `none` (completed / rest / no-plan with nothing to do), or
`blocked` (inconsistent schedule). There is **no** distinct `complete` action:
the client "complete" is a *mutation* (`POST /workout/complete`) reachable from an
in-progress state, and no started-session is persisted server-side to gate a
separate value. Adding one would be an unused state.

## Timezone

All day boundaries use `app.timeutil` (fixed Europe/Istanbul): `app_today`,
`app_date_of` (Istanbul day of a naive-UTC `created_at`), `utc_day_bounds` (query
window). No second date helper is introduced. A completion at 23:00 UTC that is
02:00 Istanbul the next day counts for the Istanbul day — verified by
`test_service_timezone_boundary_counts_as_today`.

## API contract

`GET /workout/status` is the single converged read path. The change is
**additive**:

```jsonc
{
  "completed": false,                 // preserved, back-compat (== state.completed_today)
  "state": {
    "contract_version": 1,
    "today": "2026-07-23",
    "schedule_state": "scheduled",
    "execution_state": "no_execution",
    "plan_relationship": "indeterminate",
    "action": "start",
    "primary_state": "scheduled_not_started",
    "completed_today": false,
    "is_rest_day": false,
    "stale_previous_workout": false,
    "anomaly": null
  }
}
```

`completed` keeps its exact previous value and meaning; existing consumers that
read only `completed` are unaffected. The route is thin — one
`resolve_workout_state(current_user.id)` call, no state logic, `@require_auth`
enforced, queries scoped to the current user.

## Anomaly handling

Domain conditions return stable states/anomalies; an unexpected read failure
fails **safe** to `needs_attention`/`blocked` (never a misleading rest/completed)
and logs only safe operational metadata — `request_id`, `user_id`, anomaly
category, exception class — via `[WORKOUT_STATE] anomaly …`. No stack traces,
SQL, health data or workout payloads are logged or returned to the client.

Anomaly categories: `schedule_unparseable`, `completion_marker_mismatch`,
`resolution_error`.

## Compatibility, migration, rollout

- **Migration:** none. Every fact derives from existing canonical data.
- **Feature flag:** none. The field is additive read-only and creates no
  competing authority; a flag would add no safety boundary here.
- **Rollback:** code revert only — no DB change required. Old `/workout/status`
  consumers keep working throughout.

## Consumers NOT converged in PR1 (documented, deferred)

- `static/training.js` (`renderHero`/`todayDay`) still infers rest/CTA
  client-side — Training/UIUX track converges later.
- `GET /api/progress/workout` is a history *aggregation*, not current-state; left
  as-is.
- No plan↔log identifier linkage (would require a schema change) — deferred.

## Explicit PR1 scope exclusions

No mutation-semantics change, no stale-session recovery/repair, no duplicate
cleanup, no XP/challenge/notification changes, no UI/copy/frontend changes, no
new persisted state, no feature-flag enablement, no deploy/push/merge.
