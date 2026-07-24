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
   are **recorded execution evidence only**: they prove a row *was written* today,
   **never** completion and **never** an active/resumable session (see
   *Execution evidence vs. active session*).

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
| B. Execution (today) | `execution_state` | `no_execution`, `execution_recorded`, `completed` |
| C. Plan relationship | `plan_relationship` | `matches_scheduled`, `unscheduled`, `unrelated_date`, `indeterminate` |
| D. Action eligibility | `action` | `start`, `none`, `blocked` |
| E. Dominant state | `primary_state` | `rest_day`, `scheduled_not_started`, `execution_recorded`, `completed`, `unscheduled_execution`, `unscheduled_completed`, `no_plan`, `needs_attention` |

Diagnostics also exposed: `completed_today` (compat mirror of `/workout/status`),
`is_rest_day`, `stale_previous_workout` (kept as its **own** field so a prior-day
incomplete workout never contaminates *today*), `anomaly`, `today`,
`contract_version`.

### Execution evidence vs. active session (Sprint 7 PR1 review Finding 2)

`execution_recorded` means **non-marker `WorkoutLog` rows exist for today** — that
is *recorded execution evidence* and nothing more. It deliberately does **not**
claim any of: an active session, an interrupted session, a resumable session, or
an interactive workout lifecycle. Two facts force this narrow reading:

- **No started-session is persisted.** The interactive workout session lives only
  in the browser (`static/training.js`, in-memory); the server stores no
  "started/paused" record it could trust to offer a *resume*.
- **These rows can be created outside any interactive flow.** The AI-coach
  `commit_workout_log` tool (`app/services/ai_coach.py:417`) writes a single
  non-marker `WorkoutLog` row with no `PumpCheck` and no marker. So a row's mere
  existence cannot imply "a workout is paused mid-session."

Therefore the contract exposes **no `resume` action** at all (see Action). When
evidence exists but completion is unproven, the action is `none` — the read model
neither fabricates a resumable session nor pretends nothing happened. Introducing
a real, resumable, persisted session lifecycle is explicit later-PR work; until
then `execution_recorded` is evidence, not lifecycle. This is proven by
`test_execution_evidence_never_completed_or_resumable`,
`test_no_scenario_ever_emits_resume` (exhaustive input sweep) and the adversarial
`test_service_ai_coach_logged_exercise_is_evidence_not_resumable`.

### Dominant state (E) — the one field consumers should read

`primary_state` is the single deterministic answer so API/client consumers do not
recombine raw flags. `execution` outranks `schedule`; `unscheduled` variants are
chosen when execution exists on a rest/no-plan day (a rest day never erases an
unscheduled performed workout). `execution_recorded` / `unscheduled_execution`
denote *evidence today, completion unconfirmed* — never "done", never "resumable".

### Action (D) — why there is no `resume` and no `complete`

Actions are exactly `start` (scheduled day, nothing logged), `none` (completed /
evidence-but-unconfirmed / rest / no-plan), or `blocked` (unavailable schedule).
Two client-side affordances are intentionally **absent** from the read model:

- **No `resume`.** Resuming presupposes trusted persisted evidence that a
  *resumable* session exists; none is persisted (recorded evidence is not a
  session — see above), so emitting `resume` would over-claim an active session
  the server cannot prove.
- **No `complete`.** The client "complete" is a *mutation*
  (`POST /workout/complete`); no started-session is persisted server-side to gate
  it as a read-model action. It remains available to the client regardless of the
  snapshot.

### Why a malformed/unavailable schedule cannot grant an unsafe action (Finding 4)

`action` is a **total function of `(schedule_state, execution_state)`** and its
very first branch is `schedule_state == schedule_unavailable → blocked`. That
guard runs *before* any branch that could emit `start`, so a plan that cannot be
parsed (bad JSON, not a 7-day list, today's day/tip missing or unrecognized) can
never yield `start` ("begin your workout") or any directed action — it yields
`blocked` + `needs_attention`, and the `schedule_unparseable` anomaly is logged
for repair. Crucially the schedule failure is mapped to `schedule_unavailable`
rather than silently defaulting to `rest_day`, so a broken plan is never
misread as "today is a rest day". `start` is reachable **only** from a
`scheduled`, valid workout day with no execution. This invariant is asserted for
every matrix row (`test_matrix_invariants`) and by the exhaustive
`test_no_scenario_ever_emits_resume` sweep, plus the DB-level
`test_service_malformed_plan_is_safe` / `test_api_status_malformed_plan_not_500`.

## Timezone

**Europe/Istanbul is the repository-wide canonical "day", not a local assumption
of this feature.** `app/timeutil.py` is the single documented source of every
day/hour boundary in the app (see the `app/timeutil.py` bullet in `CLAUDE.md`:
"TEK gün/saat kaynağı: sabit Europe/Istanbul … doğrudan `date.today()` KULLANMA").
This module hard-codes no timezone of its own — it calls the same
`app.timeutil` helpers every other subsystem uses (`app_today`, `app_date_of`
= Istanbul day of a naive-UTC `created_at`, `utc_day_bounds` = the UTC query
window). A completion at 23:00 UTC that is 02:00 Istanbul the next day counts for
the Istanbul day — verified by `test_service_timezone_boundary_counts_as_today`.
If the app's canonical zone ever changes, it changes in one place and this
contract follows automatically; there is no second definition to drift.

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

### All 11 `state` keys are intentionally public

`state` has a fixed, closed key set of exactly 11 fields — the five dimensions
(`schedule_state`, `execution_state`, `plan_relationship`, `action`,
`primary_state`), the diagnostics (`completed_today`, `is_rest_day`,
`stale_previous_workout`, `anomaly`), plus `contract_version` and `today`. Every
one is deliberately public API: `WorkoutStateSnapshot.to_dict()` is the single
projection, `primary_state` is the field most consumers should read, and the
individual dimensions are exposed for consumers that need finer detail. The
contract is **additive-only** — new keys may appear (consumers must ignore
unknown keys); existing keys and their closed enum vocabularies are stable and
`contract_version` gates any breaking evolution. The exact key set and every enum
vocabulary are asserted by `assert_valid_state_contract` in
`tests/test_workout_state.py`, reused by the `GET /workout/status` contract tests
in `tests/test_training_routes.py`, so an accidental field removal/addition or an
out-of-vocabulary value fails the suite. `anomaly` carries only a safe category
label (below) — never PII or health data.

## Completion authority — is a `PumpCheck` guaranteed for every completion? (Finding 3)

The contract treats **today's `PumpCheck` as the sole proof of completion**. That
is only safe if it matches what the app already calls "completed", so every
completion path was audited:

| Path | Writes today's `PumpCheck`? | Notes |
|---|---|---|
| Normal UI completion — `POST /workout/complete` (`training.py:216`) | **Yes** | `PumpCheck` + a paired `WORKOUT_COMPLETION_MARKER` `WorkoutLog`, one transaction. Its own idempotency guard (`training.py:173`) already uses "today's PumpCheck exists" as the definition of done. |
| AI-coach pump-check tool — `_tool_analyze_workout_photo` (`ai_coach.py:709`) | **Yes** | Same `PumpCheck` + marker pair, sharing the identical daily-idempotency ledger (`ai_coach.py:698`). |
| AI-coach exercise logging — `commit_workout_log` (`ai_coach.py:417`) | **No** | Writes a **non-marker** `WorkoutLog` only — deliberately *not* a completion. This is exactly the `execution_recorded` (evidence) case, not completion. |
| Manual / imported / wearable logs (e.g. `WearableWorkoutLog`, analytics inserts) | **No** | Raw exercise data, not a session-completion event. Surfaces as evidence, never completion. |
| Legacy quest-based "workout_logged" | **No** | The completion signal was migrated off the quest row to the PumpCheck (`training.py:169` "M3"), which is why the two `/workout/status` back-compat tests seed a PumpCheck. |

**Conclusion:** the two paths the product treats as "session completed" both write
a `PumpCheck`; every path that does **not** write one is genuinely *not* a
completion (it is at most execution evidence). So absence of a PumpCheck never
means "completed" — but, per Finding 2, it also never means "still in progress /
resumable". The three cases the model keeps strictly distinct:

- **Confirmed completion** — today's `PumpCheck` → `execution_state = completed`.
- **Recorded execution evidence** — non-marker `WorkoutLog`, no `PumpCheck` →
  `execution_state = execution_recorded`, `action = none` (not completed, not
  resumable).
- **Inconsistent / unconfirmed** — a completion **marker** with no `PumpCheck`
  (writer wrote one half only) → completion is **not** granted; the discrepancy is
  logged as `completion_marker_mismatch` and the state stays safe. Proven by
  `test_service_marker_without_pumpcheck_is_not_completion`.

The legacy `completed` field is preserved verbatim for back-compat and equals
`state.completed_today`; the richer three-way distinction lives in the additive
`state` object so no consumer is forced to conflate evidence with completion.

## Anomaly handling

Domain conditions return stable states/anomalies; an unexpected read failure
fails **safe** to `needs_attention`/`blocked` (never a misleading rest/completed)
and logs only safe operational metadata — `request_id`, `user_id`, anomaly
category, exception class — via `[WORKOUT_STATE] anomaly …`. No stack traces,
SQL, health data or workout payloads are logged or returned to the client.

Anomaly categories: `schedule_unparseable`, `completion_marker_mismatch`,
`resolution_error`.

### Why the one broad `except` is scoped, not "exception swallowing" (Finding 4)

There is exactly **one** catch-all, in the orchestrator
(`resolve_workout_state`), wrapping only the `load_inputs(...)` DB read:

- It is **narrowly scoped** to the impure I/O call — the pure `resolve(...)` is a
  total function and runs *outside* the guard, so a classification bug surfaces
  normally rather than being masked.
- It **does not silently continue**: it records the failure as a first-class
  `resolution_error` anomaly, logs safe metadata, and returns the deterministic
  fail-safe snapshot (`schedule_unavailable` / `needs_attention` / `blocked`,
  `completed_today=False`). A read outage therefore degrades to a *safe, visibly
  flagged* state — never a misleading "rest day" or "completed", and never a 500
  leaked to the user (`test_api_status_malformed_plan_not_500`).
- The only other catch-all guards the **logger call itself** (logging must never
  turn a read into a failure) and swallows deliberately, adding no behavior.

This is fail-safe degradation with an audit trail, not broad exception swallowing:
the operator sees `resolution_error`, and the user sees a safe, actionable state.
The `# noqa: BLE001` markers document each broad catch as intentional.

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

## Sprint 7 PR1 review — conditions closed

The four review conditions and where each is addressed:

1. **Real full-suite baseline** — a completed run at the exact base commit
   `d68186a` (`pytest -q -p no:cacheprovider`, same command as the final run),
   compared test-by-test, not inferred by subtraction. Counts in `docs/handoff.md`.
2. **`in_progress`/`resume` semantics** — the `in_progress` execution/primary
   states were renamed to `execution_recorded` (*recorded execution evidence*) and
   the `resume` action was **removed from the contract entirely**; the ambiguous
   "evidence but unconfirmed" case resolves to `action = none` (safe), never a
   fabricated resumable session (see *Execution evidence vs. active session*).
   Adversarial tests cover rows created outside the interactive flow.
3. **PumpCheck completion authority** — every completion path audited (see
   *Completion authority*); confirmed-completion, execution-evidence and
   inconsistent/unconfirmed completion are kept as three distinct, safe states.
4. **Strict API contract testing** — the `GET /workout/status` tests assert the
   exact top-level shape, preserved legacy fields, exact `completed` behavior,
   required state keys, closed enum vocabularies and no field removal
   (`assert_valid_state_contract`), instead of loose field checks. This section,
   *Timezone*, *All 11 `state` keys…* and *Why the one broad `except`…* answer the
   four report clarifications.
