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

## Sprint 7 PR2 — canonical completion mutation (write side)

PR1 above is the read model. **PR2 makes the *write* side canonical.** The single
owner of confirmed-workout-completion writes is `app/services/workout_completion/`
(`complete_workout(CompleteWorkoutCommand) -> CompletionResult`). Both production
completion writers delegate to it and keep only their own transport/validation/
media/response concerns — neither implements completion semantics itself:

- `POST /workout/complete` (`training.py`) — UI completion.
- AI-coach `_tool_analyze_gym_photo` (`ai_coach.py`) — photo-verified completion.

**Completion identity / product invariant.** One confirmed completion per
user-local (Istanbul) day, enforced at the persistence boundary by the existing
`uq_pump_check_day` UNIQUE `(user_id, date_key)`. `date_key = app_today()`
(`app.timeutil`, the one timezone source). This matches PR1's `completed_today`
(today's `PumpCheck` bucketed by `created_at` into the Istanbul day), so the read
model and the mutation never disagree.

**Idempotency & concurrency.** A read-only preflight (`already_completed_today`)
short-circuits obvious replays *before* any expensive provider work (Bedrock
vision / S3 upload) — a cost optimization only. The **unique constraint is the
sole concurrency-safe atomic claim**: the sequential replay and the concurrent
race-loser both return `ALREADY_COMPLETED` (a normal outcome — never HTTP 500,
never duplicate artifacts). Only a *verified* `uq_pump_check_day` violation is
classified as the replay outcome; any other `IntegrityError` rolls back and
surfaces as an internal error.

**Atomicity — side-effect classification.**

- *Required & atomic* (failure ⇒ whole completion rolls back): `PumpCheck`, the
  paired `WORKOUT_COMPLETION_MARKER` `WorkoutLog`, quest progress, XP, the
  `Activity` row, and friend share-`Message`s. A marker is therefore never
  produced without its `PumpCheck` (the exact inconsistency PR1's
  `completion_marker_mismatch` guards against on the read side).
- *Best-effort, in-transaction, savepoint-isolated* (failure never blocks
  completion): challenge progress + challenge-completion badge/notification/feed
  via `record_event` (its existing self-swallowing contract).
- *Response enrichment / external* (not a DB completion mutation): presigned
  pump-image URL and the Redis leaderboard `after_commit` sync. No completion
  state lives in an async job.

No remote (Bedrock/S3/network) call runs inside the mutation transaction — entry
paths validate and upload first, then invoke the mutation.

**Evidence-only writers stay separate.** AI-coach exercise logging
(`_tool_confirm_and_commit_workout_log`), `fitx_mcp` logging (a separate process
writing raw-SQL non-marker rows), manual/imported and wearable logs never call
the completion mutation and never create a `PumpCheck`/marker/completion-XP/
challenge/notification. They remain `execution_recorded` on the read side.

**Migration:** none — `uq_pump_check_day` already provides the atomic claim.
**Feature flag:** none — a single mutation authority; a flag would create a
competing one. **Rollback:** code revert only (no schema change).

## Sprint 7 PR3 — persisted workout-session lifecycle, safe resume, abandonment & stale recovery

PR1 is the read model; PR2 is the canonical completion write. **PR3 adds the one
thing the server previously could not answer: whether a workout *session* was
started, is active, is safely resumable, was completed/abandoned, or has gone
stale.** Before PR3 the only "session" was the ephemeral in-memory `_session` in
`static/training.js` (lost on refresh); `localStorage` held only a paint-cache
flag. PR3 gives the server durable, owned session truth **without** touching the
UI, `TrainingPlan` storage, or set logging.

Owner: `app/services/workout_session/` — same pure/impure split as PR1/PR2
(`models.py` = frozen commands/results + outcome enum + **pure** classification,
no ORM/Flask; `queries.py` = impure DB reads/writes; `service.py` = transaction
ownership; `__init__.py` = public API).

### `WorkoutSession` model & the active-owner invariant

`app/models.py::WorkoutSession` — integer PK `id` (never exposed) + opaque
`public_id` (`secrets.token_urlsafe`, unique) for **all** API exposure. Fields:
`user_id` (FK, CASCADE, indexed), `status` (`active|completed|abandoned` +
`CheckConstraint`), `workout_date` (ISO Istanbul **start** day — context, not
identity), `weekday_slot`, `source` (`scheduled|unscheduled`), `planned_training_plan_id`
(**plain Integer soft reference, intentionally NOT a hard FK** so the session
survives plan deletion/regeneration and we can *detect* a now-missing plan),
`plan_fingerprint` (versioned; below), `started_at`, `last_activity_at`,
`completed_at?`, `abandoned_at?`, `terminal_reason?`, `version` (transition
version), `created_at`, `updated_at`.

**Active-session uniqueness invariant (DB-level, the single atomic claim):**
partial unique index `uq_workout_session_active_owner` on `user_id WHERE
status='active'` (`sqlite_where`/`postgresql_where` — supported on SQLite ≥3.8 and
PostgreSQL). At most one ACTIVE session per user, enforced by the database, exactly
mirroring PR2's reliance on `uq_pump_check_day`. A terminal row does not occupy the
active slot, so a new session is always startable afterwards.
`is_active_session_owner_violation(exc)` classifies that specific `IntegrityError`;
any *other* integrity error re-raises (fail-closed).

### Lifecycle state machine (small, explicit)

States `ACTIVE / COMPLETED / ABANDONED`. **Stale is a *derived* condition of an
ACTIVE session, never a persisted status.** Transitions: none→ACTIVE (start);
ACTIVE→ACTIVE (idempotent resume/checkpoint); ACTIVE→COMPLETED (only via the PR2
completion authority); ACTIVE→ABANDONED (explicit). Terminal→terminal is immutable.
No PAUSED. **Reads never mutate.**

### Public operations & outcomes

`start_session`, `get_current_session`, `read_session_for_state` (the read model
the flag-ON resolver consumes), `resume_session`, `checkpoint_session`,
`abandon_session`, `resolve_for_completion`, `complete_session`. Outcome enum:
`CREATED, EXISTING_ACTIVE, RESUMED, CHECKPOINTED, ABANDONED, COMPLETED,
ALREADY_COMPLETED, ALREADY_ABANDONED, STALE_SESSION_REQUIRES_RESOLUTION, CONFLICT,
NOT_FOUND, INVALID_TRANSITION`.

- **`start_session`** — derives user + Istanbul date + plan snapshot/fingerprint
  **server-side** (no client body trusted); inserts ACTIVE. On the partial-index
  `IntegrityError` it loads the existing active session → same intended workout ⇒
  idempotent `EXISTING_ACTIVE`, different ⇒ `CONFLICT`. Replay-safe after a client
  timeout.
- **`resume_session`** — normal `RESUMED` **only** for an owned, ACTIVE, same-day
  session whose relationship is `matching_current_plan` (or `unscheduled`). Any
  mismatch/stale case ⇒ `STALE_SESSION_REQUIRES_RESOLUTION`: the session is
  **preserved**, no unsafe normal resume is exposed, an explicit recovery choice is
  required. A terminal session yields `ALREADY_COMPLETED`/`ALREADY_ABANDONED`
  (never re-attached).

### Heartbeat replay-idempotency (correction #2)

`checkpoint_session` is a **lock-free conditional `UPDATE`**:
`UPDATE workout_session SET last_activity_at=now WHERE public_id=? AND user_id=?
AND status='active' AND last_activity_at < (now - HEARTBEAT_COALESCE_SECONDS)`.
It touches **only** `last_activity_at` — never identity/start/ownership/status —
and coalesces (no-op → `CHECKPOINTED`) within the bounded interval
(`HEARTBEAT_COALESCE_SECONDS = 30`). No client optimistic version, no progress
blob. A retried heartbeat never returns a false conflict. The row-lock /
`version` bump are reserved for **terminal** transitions only.

### Session↔plan relationship & the versioned fingerprint (correction #4)

On `get_current`/`resume` the current newest `TrainingPlan` is loaded and the
fingerprint for the session's `weekday_slot` is **recomputed server-side** and
compared — weekday text is never trusted alone. `plan_fingerprint` is stored as
`v1:<sha256hex>` over the ordered, casefolded exercise names of the slot
(order-preserving, never client-supplied, never the plan itself).
`fingerprints_match(stored, current)` returns `None` on an algorithm/**version
mismatch** ⇒ the relationship classifies as `indeterminate` (safe), **never a
silent match**. Relationship vocabulary: `matching_current_plan`,
`plan_regenerated`, `plan_missing`, `schedule_slot_changed`, `unscheduled`,
`indeterminate`; temporal `previous_day`; `lifecycle_inconsistent` when a same-day
`PumpCheck` completion coexists with a still-ACTIVE session.

### No inactivity-based staleness (correction #6)

Stale derives **only** from concrete lifecycle/relationship evidence (previous
local day, plan missing/regenerated/replaced, schedule-slot changed,
lifecycle/completion inconsistency, indeterminate relationship). A same-day
session with no recent heartbeat is still `matching_current_plan`/resumable —
`last_activity_at` is heartbeat/observability only and **never gates resume**.

### Flag-conditional read contract (correction #1)

The contract version is **not a static bump**. With `FITX_WORKOUT_SESSIONS_ENABLED`
**OFF**, `resolve_workout_state` returns the **exact PR1 `contract_version=1`
snapshot** — identical key set, identical enum vocabulary (`action` stays
`{start,none,blocked}`), no `session` keys, `resume`/`in_progress` never emitted.
With it **ON**, the read model loads session truth read-only and returns the
additive `contract_version=2` snapshot: two new keys (`session_state`, `session`),
the additive `action=resume` and `execution_state/primary_state=in_progress`
values — producible **only** from a persisted, *eligible* ACTIVE session, never
from evidence-only `WorkoutLog`, and `start` is never emitted while a conflicting
ACTIVE session exists. Pure projection: `resolver.enrich_with_session(base,
facts)` — folds session facts into the v1 base without mutating it (the OFF path
that skips enrichment stays byte-identical). Both modes have strict snapshot
tests (`tests/test_workout_state.py`, `tests/test_workout_state_sessions.py`).

`v2` `session` object: `{public_id, status, resumable, relationship, stale_reason,
workout_date}`. `session_state` vocabulary: `none, active_resumable,
active_blocked, completed, abandoned, execution_without_session, inconsistent`.

### Completion↔session reconciliation & fixed lock order (correction #3)

`CompleteWorkoutCommand` gains an additive `session_id: Optional[int] = None`;
`session_id is None` is the **unchanged legacy path** (no synthetic session
fabricated, legacy completion without a session stays valid). When present, the
completion authority (`app/services/workout_completion/service.py`) owns the
ACTIVE→COMPLETED transition **inside its single transaction**:

- **Fixed lock order** (documented + tested, both paths, so create and
  reconciliation can never deadlock): the session row is locked **first**
  (`SELECT … FOR UPDATE` on PG; the enclosing txn suffices on SQLite —
  `lock_session_for_completion`), *then* the PumpCheck/completion artifacts.
- **Fresh completion (`CREATED`):** create the PR2 artifacts, then — before the
  single `commit()` — terminalize the session (`mark_session_completed`:
  conditional on `status='active'`, sets `completed_at`, bumps `version`). One
  atomic unit.
- **Existing confirmed completion / reconciliation:** every path that ends the day
  completed **still** terminalizes the owned matching ACTIVE session, with **no**
  duplicated PumpCheck/marker/XP/quest/challenge/activity/notification —
  (a) the preflight-detected replay reconciles the already-locked session;
  (b) the `uq_pump_check_day` race-loser re-locks and terminalizes in a fresh,
  artifact-free transaction (`_reconcile_session_after_race`);
  (c) a duplicate session completion is a deterministic no-op.
- An explicitly **ABANDONED** session cannot be completed ⇒ `SessionCompletionConflict`,
  rolled back with no artifacts.

Invariants held: a matching session is **never** left permanently ACTIVE after its
day is completed; COMPLETED is **never** written without PumpCheck authority;
unique-conflict handling never silently drops the terminalization. Real Postgres
two-contender proof: `tests/test_workout_session_pg.py` (opt-in, `pg_concurrency`
marker + env-gated) — **executed 2026-07-25 on a disposable `postgres:16` (16.14),
all 3 tests passed** (concurrent-start, complete-vs-abandon, concurrent-completion
reconciliation); see `docs/handoff.md` for the full execution record.

### API routes

All `@require_auth`, `current_user.id` server-side, bounded payloads, stable error
envelope, no client-supplied `user_id`/`status`/timestamps/`version`:
`POST /workout/session/start`, `GET /workout/session/current`,
`POST /workout/session/<public_id>/resume|checkpoint|abandon`, and the extended
`POST /workout/complete` (optionally accepts the session `public_id` →
ownership-resolve → internal id → `CompleteWorkoutCommand.session_id`; absent ⇒
legacy). The session routes are gated by the flag: **OFF ⇒ 404 (inert)**, so
enabling PR1/PR2 behavior stays byte-identical. The flag is a rollout/presentation
gate, **not** an authorization gate — `@require_auth` and server-side ownership
always hold. HTTP status map: `CREATED`→201, `*_ACTIVE`/`RESUMED`/`CHECKPOINTED`/
`ABANDONED`/`COMPLETED`/`ALREADY_*`→200, `STALE…`/`CONFLICT`/`INVALID_TRANSITION`
→409, `NOT_FOUND`→404.

### Migration — fail-closed, verify-or-create (correction #5)

`migrations/versions/a994f9bed783_add_workout_session_sprint_7_pr3.py`
(down_revision `bb88cc99dd00`; single new head). Because the repo boot order is
`create_all` → stamp → upgrade, the migration is **expand-only and
verify-or-create**: it does **not** blanket-skip when `workout_session` already
exists — it inspects and creates each missing required object (table, all required
columns, the status `CheckConstraint`, `public_id` uniqueness, the active-owner
partial unique index, supporting indexes). If an existing table is present but
**incompatible** (missing required columns) it raises `RuntimeError` rather than
reporting a successful upgrade with an incomplete invariant. Downgrade drops the
indexes + table. Tested (`tests/test_workout_session.py`): fresh DB creates the
full table, verify-or-create is idempotent, fail-closed on an incompatible table,
downgrade removes it.

### Feature flag, rollout & rollback

`FITX_WORKOUT_SESSIONS_ENABLED` (default `False`, `app/config.py`, single canonical
owner). **OFF:** session-write routes are inert (404) **and** the resolver emits
the exact PR1 `contract_version=1` vocabulary — PR1/PR2 behavior byte-for-byte.
**ON:** full lifecycle + `contract_version=2`. Disabling after sessions already
exist is safe: persisted sessions are simply **ignored** by the read contract,
never deleted. Do **not** enable in prod as part of this PR. Removal criteria:
once the UI consumer ships and the lifecycle has soaked, the flag and its OFF
branch can be retired.

### Deferred / out of scope (recorded)

Stable plan-day identifier linkage (session↔plan is a soft reference only; no
set-level restoration — checkpoint is a lifecycle heartbeat, documented gap),
workout UI/nav redesign, offline sync, `TrainingPlan` schema redesign, historical
`WorkoutLog` backfill, automated destructive stale cleanup, Sprint 7 PR4.

## Sprint 7 PR4 — consumer convergence

PR4 keeps PR1 as the read authority, PR2 as the confirmed-completion authority,
and PR3 as the persisted-session authority. It adds no schema, migration, new
feature flag, or competing workout-state resolver.

### Coherent Training bootstrap

`GET /training/bootstrap` is authenticated and user-scoped. It returns one
`Cache-Control: private, no-store` snapshot containing the shared `workout`
envelope, the active `plan` envelope, and the server-selected `today_plan`. The
route resolves the Istanbul date and session flag once, loads the newest
`TrainingPlan` once, and passes that same plan object and date into strict
canonical resolution. No-plan, no-session, active-session, completed, and
unavailable remain distinct; absence never fabricates a resumable session.

Every required authoritative read and public serialization must succeed before
the response is emitted. A plan-read, strict session-read, canonical-resolution,
malformed-plan, or bounded-serialization failure returns only the localized
generic error and `code=bootstrap_unavailable` with HTTP 500. It returns no
partial plan/workout combination, raw exception, database identifier, or
sensitive payload.

`today_plan` contains only `gun`, `tip`, `odak`, `sure_dk` (0..1440),
`tahmini_kalori` (0..900), and at most 50 exercises. Exercise keys are `isim`,
`set` (1..100), `tekrar`, `dinlenme`, and `not`, with bounded strings. The
full-week projection is closed and key ordering deterministic; unknown/internal
fields, IDs, other-day metadata, and generation metadata are dropped. Malformed
schedules cannot be presented as rest or startable content. Both persisted
shapes remain compatible: the legacy top-level seven-day list and wrapped
`{ "program": [...] }`.

### Consumer ownership matrix

| Consumer | Canonical fields consumed | Canonical source | Refresh trigger | Mutation ownership | Failure/fallback | No independent reconstruction |
|---|---|---|---|---|---|---|
| Training | `completed`, complete `workout.state`, `session`, `plan`, `today_plan` | ordered `GET /training/bootstrap`; PR1/PR3 services behind it | initial load, focus/visibility return, mutation settlement | PR2 completion and PR3 session routes; client only orchestrates | fail-closed blocked UI; no cached truth | no completion, active session, workout date, current plan, or mutation success from local date, localStorage, DOM, or POST response |
| Progress | additive `current.completed` and `current.state`; historical `days`/`totals` stay historical | shared canonical envelope in `/api/progress/workout` | each request/navigation refresh | none | historical aggregates may render; current state is not guessed | no current state from history rows |
| Barcode | `completed_today` | one canonical resolver call in barcode service | each barcode context build | none | safe canonical unavailable/false; nutrition context remains | no marker/log completion inference or session/date/plan/mutation reconstruction |
| Coach | compact canonical current-state projection plus unchanged history | one canonical resolver call in context builder | each context build/request | none | history remains; unavailable canonical state is honest | no current state or mutation success from history/prompt-local rules |

Historical heatmap, streak, analytics, and detailed set/rep views retain only
their historical or page-ephemeral ownership; they are not alternative
current-workout authorities.

### Refresh, mutation, and heartbeat lifecycle

The Training controller uses a monotonic generation and `AbortController`: a
superseded response cannot apply state or clear a newer request. Mutations are
single-flight, their transport result is never painted as canonical success, and
every settled mutation triggers a newly ordered authoritative bootstrap. Repeated
initialization and destruction are idempotent; `pagehide`/navigation removes
listeners, aborts controllers, and stops timers.

For an active v2 session, exactly one visible-page 60-second heartbeat may exist.
There is no heartbeat for v1, inactive, blocked, completed, logged-out, hidden, or
torn-down state. Checkpoints are single-flight, failures cannot mutate canonical
state, and terminal/authentication failures stop the timer and request at most
one ordered refresh. There is no immediate retry loop or high-frequency polling;
PR3's server-side 30-second coalescing remains unchanged.

### Validation, limitations, rollout, and rollback

The frozen PR4 base is `1e76e1374d1a8a61b4a44ceb1a763e9c2758061c` (Sprint 7 PR1/PR2/PR3
are merged ancestors); branch `sprint7-pr4-workout-state-convergence`, isolated worktree
`.worktrees/sprint7-pr4-workout-state-convergence`. At closeout (2026-07-26) `origin/main`
had advanced past this base to `cab7c27` (UIUX Sprint 1 PR3, #189), which overlaps
`app/blueprints/training.py`, `CLAUDE.md`, `docs/handoff.md`, and `tests/test_training_ui.py`; the base
was deliberately **not** rebased so this workout-state PR does not absorb that unrelated UIUX
work (see docs/handoff.md "Sprint 7 PR4"). The reconciled suite
collected 2,581 selected nodes (2,584 unfiltered; three load-marked deselected),
SHA-256 `40ab2557b2c0bf51aa0f95d3a8e2bebd648e196e22823f762ef2e481bb37ce45`.
Disjoint partitions executed every selected node: 2,577 passed, four opt-in
PostgreSQL tests skipped, zero failures/errors, all exit 0. Exact proof is in
`docs/frontend-readiness/sprint-7-pr4/test-partition.json`.

<!-- PR4_BROWSER_RESULT_START -->
The final hermetic WSL/Linux Playwright/Chromium run passed all 52/52 cells
(zero failed/blocked), exit 0; runner duration 229.196 seconds and shell duration
266.2 seconds. The 48 English/Turkish matrix cells covered no-plan, active, and
completed states at 320x720, 360x800, 375x812, 390x844, 430x932, 768x1024,
1024x900, and 1440x900. Special cases covered fail-closed bootstrap, ordered
stale/mutation behavior, repeated refresh, direct load/refresh, back/forward,
breakpoint transitions, Progress/barcode/Coach consistency, and real/controller
heartbeat lifecycle.

The artifact records zero unexpected same-origin 5xx, zero PR4 console errors,
zero hard failed requests, and zero analytics-event requests. Matrix traffic was
96 bootstrap requests—exactly two per cell (initial load plus the audit identity
probe)—with no on-load mutations. The active-session heartbeat had exactly one
60-second timer, one forced checkpoint, and zero timers after teardown. Canonical
identity checks passed; no duplicate bootstrap, mutation, heartbeat, or analytics
event was observed. Authority scans confirmed no localStorage workout authority
and no client-local-date authority. Exact requests, responses, errors, identities,
timer counts, and interactions are in
`docs/frontend-readiness/sprint-7-pr4/browser-validation.json`.
<!-- PR4_BROWSER_RESULT_END -->

Remaining legacy debt: set/rep/timer progress is page-memory-only; historical
heatmap/streak/analytics retain their existing models; plan-to-log identifier
linkage and durable per-set checkpoint data remain outside PR4. Roll out with the
existing session flag OFF; enable it only after a clean non-production browser
gate. Rollback is flag OFF for PR3 lifecycle calls plus a PR4 code revert; no
database rollback is needed.
