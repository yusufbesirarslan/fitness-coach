# Adaptive Coaching — canonical training-plan mutation boundary

Adaptive Coaching Sprint 1 PR1. Domain foundation only: **no route, no AI tool,
no feature flag, no migration.**

The principle this document exists to fix in place:

> AI may request a training-plan change.
> AI never owns training-plan persistence.
> The canonical backend mutation domain decides whether and how that change is allowed.

---

## 1. Objective

Give the backend one deterministic, typed, server-authoritative way to change a
*part* of a user's training plan, so that when the AI Coach is eventually allowed
to act on plans it cannot:

rewrite a whole plan for a one-field request · touch ORM entities or rows
directly · mutate another user's plan · change unrelated exercises or days ·
rewrite completed history · bypass canonical validation · become a second plan
authority · perform arbitrary JSON/database patches · use plan regeneration as a
substitute for a targeted change.

PR1 owns the boundary. It does **not** connect the AI Coach to it.

---

## 2. Canonical training-plan authority

| Question | Answer (verified in this repo, not assumed) |
| --- | --- |
| What persists a plan? | `TrainingPlan` (`app/models.py`) — `user_id` FK (CASCADE), `plan_data` JSON **text**, `score`, `created_at`. |
| Which row is active? | The newest by `created_at` — `get_active_plan()` in `app/services/today_facts.py`. Every reader (`plan_facts`, `workout_state`, `workout_session`, `pump_checks`) reuses that same rule. |
| One authority? | Yes. There is exactly one active-plan selector and, before this PR, exactly one writer. |
| Ownership | The `user_id` column. Nothing else confers plan ownership. |

`plan_data` shape (both accepted everywhere):

```jsonc
{"program": [ /* 7 days */ ]}        // or a bare list of the same days
// day
{"gun": "Pazartesi", "tip": "antrenman", "odak": "İtiş",
 "sure_dk": 45, "tahmini_kalori": 320, "egzersizler": [ /* … */ ]}
// exercise
{"isim": "Bench Press", "set": 3, "tekrar": "8-12",
 "dinlenme": "90 sn", "not": ""}
```

**Identity.** A training day is identified by `gun` — a canonical Turkish
weekday, validated unique across exactly seven days. An exercise is identified by
`isim` **only: there are no exercise IDs anywhere in the plan.** PR1 does not
invent a production exercise catalog to fix that (see §7).

---

## 3. The mutation authority

`app/services/plan_mutation/` — the single boundary through which all future
adaptive-training writes flow.

```
consumer (AI / API / web)
        ↓
plan mutation service          app/services/plan_mutation/
        ↓
canonical training domain / persistence
```

The arrow never reverses. The package imports no AI/provider SDK, no prompt
construction, no blueprint, no UI state — enforced by
`tests/test_plan_mutation_architecture.py`, which also asserts that no Coach
module imports the mutation domain yet.

| File | Role |
| --- | --- |
| `commands.py` | The typed contract. Frozen dataclasses, one per operation. |
| `document.py` | **Pure** targeted-mutation engine — no ORM, no Flask, no I/O. |
| `validation.py` | Canonical bounds, reused from the generator's validator. |
| `service.py` | Transaction + ownership owner. The only writer. |
| `errors.py` | Internal domain outcomes (not public API error codes). |
| `__init__.py` | Public API. |

This mirrors the pure/impure split already used by `workout_state`,
`workout_session` and `workout_completion`.

---

## 4. Supported operations (PR1)

| Command | Semantics |
| --- | --- |
| `ReplaceExerciseCommand` | Replace one named exercise in one named day. Position, rest, notes and unmodelled fields are **inherited** unless `sets`/`reps` are explicitly overridden. |
| `AddExerciseCommand` | Append one exercise to one named day. `sets` **and** `reps` are required — no invented prescription. Rejected on a rest day. |
| `RemoveExerciseCommand` | Remove exactly one identified exercise. Rejected if it would empty a training day. |
| `UpdateExercisePrescriptionCommand` | Change `sets` and/or `reps` only. |
| `MoveTrainingDayCommand` | Exchange two weekday slots' *content*; each entry keeps its own `gun`, so the calendar is never renamed or reordered. |

Not in the contract: arbitrary dicts, ORM field assignment, JSON Patch, or any
generic "set field X". `apply_plan_mutation` refuses anything that is not one of
the five command types — **before it reads a row**.

`UpdateExercisePrescriptionCommand` is scoped to `sets`/`reps` deliberately.
`dinlenme` and `not` are persisted but only length-bounded, and RIR/RPE do not
exist in the plan shape at all — so none of them has the canonical validation
semantics §9D requires.

**An added exercise carries no `dinlenme` (rest) key.** The generator has a
canonical default for it ("60-90 sn"), but that default exists to fill gaps in
*LLM output*; writing it here would state a rest prescription the caller never
gave, and it would be indistinguishable afterwards from one that was actually
prescribed. Every reader already treats a missing `dinlenme` as neutral
(`plan_facts` projects it to an empty string; the workout-session fingerprint
reads only `isim`), so the honest empty value costs nothing. A caller that wants
a rest prescription should get a command field for it — which is an additive
change to the contract, not a default.

---

## 5. The targeted-mutation invariant

A mutation changes only what the command explicitly owns.

The plan is one JSON text column, so persisting *anything* rewrites the whole
column. The invariant therefore holds one level up: `document.apply_command`
deep-copies the parsed document, reaches exactly one node, and mutates that node
**in place**. Untouched days and exercises are the same objects that were parsed,
re-serialized in the same order with the same keys — including keys this PR knows
nothing about. Nothing is rebuilt from a projection, which is what would silently
drop an unknown field.

Explicitly *not* touched by any mutation: other exercises · other days · weekly
frequency · plan goal · `haftalik_ozet` and other derived plan-level values ·
progression history · calories/macros · completed `WorkoutLog` rows · workout
completion state.

Derived plan-level summaries are left exactly as the generator wrote them.
Recomputing a weekly summary from one exercise swap would be this boundary
inventing planning authority it does not have.

---

## 6. Ownership and authorization

`apply_plan_mutation(user_id, command)`. `user_id` **must** come from the
authenticated server context.

No command carries a `user_id`, a plan id, or any other ownership hint, so
authority can never arrive from a request body, a tool argument, or model output.
The service resolves the caller's *own* active plan by scoping the query to
`user_id`, which makes cross-user mutation structurally impossible rather than
merely checked.

A user with no plan and a user naming someone else's plan both reach
`PlanNotFound`. Plan existence never leaks across users. No second
authentication mechanism is introduced.

---

## 7. Validation

Bounds are **reused, not redefined**. `validation.py` takes its vocabulary and
limits from `training_generation/response_validator.py`
(`WEEKDAYS`, `VALID_TIPS`, sets 1–100, reps ≤ 40 chars, name ≤ 120 chars), and
`tests/test_plan_mutation.py` pins the two together so they cannot drift.

The *posture* differs on purpose: the generator **clamps** LLM output because a
slightly-wrong number from a model is better repaired than rejected; the mutation
boundary **rejects**, because a caller asking for 999 sets has made an error and
silently storing 100 would report success for a request that did not happen.

Enforced: plan is parseable and mutable · plan belongs to the user · day exists ·
exercise exists where required · the exercise match is unambiguous · replacement/
addition is structurally valid · sets and reps within canonical bounds · empty and
malformed commands fail deterministically · unknown fields are never accepted from
a command · protected plan fields cannot be mass-assigned (there is no field-name
input at all) · completed history is unreachable through this boundary · the
resulting plan stays structurally valid.

### Known identity limitation

Exercise identity is the name. Matching is case- and whitespace-insensitive, and
**a name that matches more than once in the target day is refused**
(`AmbiguousExerciseTarget`) rather than resolved by position. That is the honest
consequence of name-only identity: the caller did not identify a unique thing, so
the boundary refuses to guess. Building an exercise catalog with stable IDs is
out of scope for PR1 and is the correct long-term fix.

---

## 8. Transaction semantics

```
validate command type
→ resolve the caller's OWN active plan (SELECT … FOR UPDATE)
→ parse authoritative current state
→ apply the targeted mutation to a copy
→ no-op? return without writing
→ serialize, assign, flush, commit
```

Every failure rolls back; there is no partial logical mutation. The copy is taken
up front and all validation runs against it, so a command that fails half-way
never reaches the ORM row.

`with_for_update()` is the repository's established PostgreSQL-safe pattern (a
harmless no-op on SQLite) and prevents the obvious lost update when two mutations
race on one plan. No speculative distributed locking is introduced.

**No provider I/O of any kind happens in PR1**, and no network call is made
inside the transaction.

**Exactly-once is not claimed.** A caller retrying after a lost response can apply
the same change twice; for replace/update/move that converges on the desired
state, and for `add` it appends a second entry. The persistent mutation journal
that would make this exactly-once is deliberately deferred (see §11).

### No-op semantics

When the requested state already holds — sets already equal, day moved onto
itself, prescription already matching — the service returns
`PlanMutationResult(changed=False, …)` and **does not write**. No churn, no
regeneration, no fabricated progression, no history row.

---

## 9. Completed history is untouched — and why that is structural

`WorkoutLog` stores `exercise_name`, `sets`, `reps`, `weight_kg` and `volume` as
its **own columns**, snapshotted when the set was recorded. Nothing about history
is derived from `plan_data`.

So a plan mutation *cannot* make a past workout appear to have used a new
exercise or prescription: there is no code path from the plan to a historical
row. This is a property of the schema, not of care taken in this PR — which is
the strongest form the brief's §15 requirement can take. It is covered by
`TestHistoricalSafety`, including that a mutation creates no `WorkoutLog` or
`PumpCheck` rows at all.

---

## 10. Known interaction: workout sessions

`workout_session` fingerprints a scheduled day as `v1:sha256(ordered exercise
names)` (Sprint 7 PR3). Mutating today's exercises therefore changes that
fingerprint, and a linked ACTIVE session classifies as
`plan_regenerated_or_replaced` → stale, which the existing UX resolves by asking
the user.

**PR1 changes nothing here, on purpose.** That behaviour is already correct and
fail-safe, and "refresh the session fingerprint" would make the mutation service
a second writer of workout-session state — breaking the single-authority rule
this PR exists to establish. Whether a mid-workout mutation should instead be
refused is a product question, and it belongs to the confirmation/impact work
listed in §11.

---

## 11. Explicitly NOT in PR1

No AI Coach tool registration, tool execution, intent parsing or AI-generated
mutation arguments · no plan versioning, mutation history, audit trail, undo or
rollback · no impact classification or confirmation UX · no proactive coaching,
plateau/fatigue/adherence detection or automatic deloads · no full-program
regeneration · no nutrition-plan or calorie/macro mutation · no Pump Check or
Weekly Check-in → plan mutation · no mobile/Flutter change · no notifications ·
no route, no feature flag, no schema change, no migration, no production
activation.

No existing caller was migrated to this service. `POST /training-plan/save` keeps
its whole-plan replace semantics, and the Training Generator is unchanged.

### Handoff to Sprint 1 PR2

PR2 owns plan version identity, mutation history with actor/reason metadata,
before/after representation, `undo_last_change` and safe rollback. Three notes
for whoever picks it up:

1. `PlanMutationResult.changed` already distinguishes an applied mutation from a
   no-op; a journal should record only the former.
2. Idempotency keys / replay protection belong there, not here — see the
   exactly-once note in §8.
3. Versioning will interact with the workout-session fingerprint in §10; decide
   the two together.

### What a later tool/API layer will need to observe

PR1 introduces **no logging of user content and no metrics** — it has no endpoint
and no provider interaction, so any metric would be speculative. When a transport
layer lands it will want: mutation-outcome counts by command type and error kind,
and rejection rates for ambiguous targets (a rising rate is the signal that
name-only identity has become the bottleneck). None of that may carry plan
payloads, exercise text from user input, prompts, tokens, or user IDs as metric
dimensions.
