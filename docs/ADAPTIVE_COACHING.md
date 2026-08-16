# Adaptive Coaching — canonical training-plan mutation boundary

Adaptive Coaching Sprint 1, PR1 + PR2. Domain foundation only: **no route, no AI
tool, no feature flag.** PR2 adds the first schema of the track (one additive
migration) and still exposes nothing — the history it keeps is internal evidence,
not an API.

Read §§1-11 for the boundary PR1 established; §§12-18 for the durable versioning,
mutation journal, idempotent replay and undo PR2 adds on top of it.

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

**No provider I/O of any kind happens**, and no network call is made inside the
transaction.

> **PR2 supersedes the sequence above.** Exactly-once *is* now claimed, the plan
> version and the journal row commit inside the same transaction, and a no-op is
> durably recorded rather than silently dropped. See §16 for the current
> sequence and §15 for why a no-op must leave a trace.

### No-op semantics

When the requested state already holds — sets already equal, day moved onto
itself, prescription already matching — the plan document is **not rewritten**:
no churn, no regeneration, no fabricated progression, and the version does not
move. `PlanMutationResult.changed` stays `False`.

Since PR2 the *request* is still recorded, with `outcome = "no_op"` and identical
before/after snapshots. That row is not history of a change; it is history of an
answered request, and §15 explains why dropping it is unsafe.

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

**PR1 changed nothing here, on purpose, and PR2 keeps that decision** — including
on the undo path, where "put the fingerprint back too" is even more tempting and
even more wrong: it would let a reversal silently re-bless a session whose
planned workout no longer exists. Refreshing the fingerprint from here would make
the mutation service a second writer of workout-session state, breaking the
single-authority rule this domain exists to establish. Whether a mid-workout
mutation should instead be *refused* is a product question, and it belongs to the
confirmation/impact work listed in §11.

`TestHistoricalSafety.test_an_active_session_fingerprint_is_not_refreshed` pins
this: an ACTIVE session's fingerprint, status, version, `updated_at` and plan
pointer are byte-identical across a mutation followed by an undo.

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

---

# Sprint 1 PR2 — versioning, journal, replay, undo

## 12. The mutation envelope

Every call now carries a `MutationContext` alongside the typed command:

```python
apply_plan_mutation(user_id, ReplaceExerciseCommand(...),
                    MutationContext(idempotency_key="…", actor="ai_coach",
                                    reason="omuz ağrısı"))
undo_last_change(user_id, MutationContext(idempotency_key="…"))
```

The split is the point. The **command** is canonical intent — what should become
true of the plan. The **context** is everything about the request that must never
influence what the mutation does.

* `idempotency_key` — required, `[A-Za-z0-9._:-]{8,64}` (the shape the repo's
  existing `Idempotency-Key` contract already accepts), scoped to one user.
  Required rather than optional because PR1 shipped with no callers, so nothing
  is being migrated — and an optional key is a contract in which a future
  consumer can quietly opt out of replay protection and reintroduce the
  duplicate-`add` bug PR2 exists to close.
* `actor` — closed vocabulary `user | ai_coach | system`, server-supplied.
  **Never authorization.** A row saying `ai_coach` grants the AI Coach exactly
  nothing; ownership still comes from the authenticated session and the
  owner-scoped plan query.
* `reason` — optional, ≤200 characters, stored verbatim, never logged, never
  interpreted. Over the bound it is **refused, not truncated**: a silently
  shortened audit note is a different note the caller was never told about.

## 13. Plan version and plan lineage

`TrainingPlan` gains two columns.

`mutation_version` is the server-authoritative history position: **+1 on every
persisted state transition, including an undo; unchanged on a no-op, a validation
failure, a replay, a conflict or a failed commit.** It only ever counts up.
Restoring old *content* is what undo means; restoring an old *version number*
would make two different histories report the same position, and any optimistic
check later built on it would pass on stale state. Existing rows start at 0 —
"never mutated through this boundary" — and no historical events are fabricated
to justify a higher number.

`lineage_id` is an opaque `secrets.token_urlsafe(32)` naming one plan *lineage*.
It exists because `POST /training-plan/save` replaces a plan by deleting every
row for the user and inserting a new one: the primary key never survives
regeneration, so it cannot identify "the same plan over time". A regenerated plan
gets a fresh lineage from the column default, which makes it **structurally
impossible** for an undo to restore an old plan's snapshot into a new one, no
matter how similar the day and exercise names look.

## 14. The mutation journal

`plan_mutation_record` — one row per accepted operation, append-oriented.

| Field | Note |
| --- | --- |
| `public_id` | opaque identity returned to callers; the sequential PK never leaves the service |
| `user_id`, `plan_lineage_id` | owner and lineage; every query filters on both |
| `operation_kind` | `mutation` (an original typed command) or `undo` (a compensating event) |
| `command_type` | stable snake_case identity, e.g. `replace_exercise`; never a class name or localized string |
| `command_fingerprint` | versioned semantic digest — the replay comparator |
| `actor`, `reason` | audit metadata from the envelope |
| `outcome` | `applied` or `no_op`; only `applied` is reversible |
| `before_version`, `after_version` | the exact transition |
| `before_snapshot`, `after_snapshot` | **the exact persisted `plan_data` text**, both sides |
| `before_fingerprint`, `after_fingerprint` | sha256 of those bytes; the undo precondition |
| `idempotency_key` | unique per `(user_id, key)` |
| `reverts_mutation_id` | which mutation an undo reverses; unique, so a change is reversed at most once |

Two decisions carry most of the weight.

**Snapshots are the exact text, not a projection.** §5 explains that PR1 preserves
plan fields this domain does not model; rebuilding a snapshot from `plan_facts`,
a DTO or an exercise list would silently drop them, so an undo would "restore" a
lossy plan. The snapshot is what was in the column, byte for byte.

**`plan_lineage_id` and `reverts_mutation_id` are soft references.** A hard FK to
`training_plan` would be cascade-deleted by the legacy replace path, destroying
audit history that must outlive the row it describes (the same reasoning as
`WorkoutSession.planned_training_plan_id`). A hard *self*-FK would make owner
purge order-dependent under SQLite's immediate FK checks, and both escape hatches
corrupt the trail: `ON DELETE CASCADE` destroys history, `ON DELETE SET NULL`
rewrites it. The uniqueness constraints — which are the invariants that actually
matter — need no foreign key.

The journal is **evidence, never authorization**. A matching `user_id` on a row
is a fact about the past, not a permission.

### The semantic fingerprint

Domain-separated and versioned: `axisai/training-plan-mutation/v1`. What is
hashed is an explicitly constructed payload — never `repr()`, dict iteration
order, a raw HTTP body, a prompt, or display text — canonicalized as
`json.dumps(…, sort_keys=True, separators=(",", ":"))` and sha256'd.

Normalization mirrors how `document.py` actually resolves a target, so the
fingerprint agrees with the mutation it protects:

* `day` / `target_day` — stripped (`_find_day` compares the stripped label
  exactly, so case is meaning).
* the **target** exercise of replace/remove/update — stripped and casefolded,
  because `_find_exercise_index` matches that way: `"bench press"` and
  `"Bench  Press "` address the same slot and must replay, not conflict.
* a **stored** name (`replacement`, and `exercise` on an add) — stripped only. It
  is written into the plan verbatim, so `"Machine Press"` and `"machine press"`
  are genuinely different intents.
* `reps` — stripped; `sets` — the integer, with `None` preserved as "field not
  supplied", which differs from supplying the value the plan already holds.

Changing which fields participate, or this normalization, requires a **new
version** rather than redefining what stored digests meant.

## 15. Idempotent replay

Three outcomes, decided durably:

| Same user, same key… | Result |
| --- | --- |
| …same semantic operation | **replay** — the original `outcome`, `plan_version`, `mutation_id` and stored after-snapshot, with `replayed=True`. Nothing is applied. |
| …different operation | **`IdempotencyConflict`** — fail closed. The command is not applied and no row is written. |
| different users, same key text | fully independent operations |

A replay returns **the state that call produced**, read from its stored
after-snapshot — not the plan as it stands now. A retry of an old key must not
report a later, unrelated change.

**The database is the final arbiter.** No process memory, no Redis, no timestamp
window. Two layers settle a same-key race, in this order. Usually the row lock
does it: the loser blocks on `SELECT … FOR UPDATE`, and by the time it runs its
second look the winner's row is committed, so it replays without any exception.
When both contenders get past their pre-flight check before either commits — no
lock held, a backend without row locks, or a future caller that resolves the plan
differently — both reach the INSERT and `uq_plan_mutation_user_key` picks one;
the loser rolls back its own half-applied mutation, re-reads the winner's row and
replays it. The constraint is what makes the guarantee independent of the lock,
which is why it is the arbiter of record even though the lock normally gets there
first. The `add_exercise` retry — the one command that is not naturally
convergent — is the concrete bug this closes.

Both layers are pinned separately, because a test can only see one at a time:
`TestDatabaseArbitration` (tests/test_plan_mutation_history.py) forces the
constraint path deterministically, and the PostgreSQL races
(tests/test_plan_mutation_history_pg.py) prove the outcome under real
concurrency. A green PostgreSQL run on its own does **not** demonstrate that the
constraint fired.

**A validation failure consumes nothing.** The command is rejected before any row
is inserted, so a corrected retry under the same key succeeds instead of being
refused as a conflict or, worse, replaying a mutation that never happened.

**A no-op is recorded anyway**, and this is the non-obvious one. Sets are 3. A
no-op "set them to 3" is accepted under key K. The user then really changes them
to 4. A retransmitted K arrives. If the no-op left no durable trace, K looks
fresh and quietly drags the plan back to 3 — a mutation nobody asked for,
produced by a *retry*. The row makes the retry a replay instead.

## 16. `undo_last_change`

The one canonical reversal. There is **no redo, no `rollback_to_version`, no
`restore_snapshot`**: an operation that can restore an arbitrary past state is a
plan-writing API with extra steps, and this boundary exists precisely so plan
writes stay typed and narrow.

```
validate the envelope
→ durable replay check
→ resolve the caller's OWN active plan (SELECT … FOR UPDATE, populate_existing)
→ re-check the operation key under the lock
→ select the newest still-effective reversible mutation of THIS lineage
→ verify current bytes are exactly what that mutation produced
→ restore its stored before-snapshot; version + 1
→ append the undo row pointing at the reverted mutation
→ ONE commit
```

Selection filters, each carrying an invariant: same lineage (§13), `operation_kind
= mutation` (an undo is not something to undo), `outcome = applied` (a no-op
produced no transition), and not already reverted (which is what lets a second
undo reach further back). Ordering is by primary key, not `created_at` — two rows
in one millisecond must still have one deterministic "latest".

**The precondition is bytes, not version.** After two undos the plan sits at a
much higher version than the mutation being reversed, which is correct, so a
version equality check would break multi-level undo. Comparing
`snapshot_fingerprint(plan_data)` against the target's `after_fingerprint` is the
honest check, and a mismatch means an out-of-band writer touched the plan →
`UndoConflict`, fail closed. Restoring an old snapshot on top of that would
silently destroy the other writer's work. There is no "close enough", no name
similarity, no partial match.

Restoration is the **exact stored bytes**, never an inferred inverse command and
never a model call. An inverse of "replace X with Y" looks obvious and is wrong
the moment the plan holds fields this domain does not model.

Undo is itself idempotent: the same key retried replays the original undo rather
than walking a second change back. Two *different* undo keys racing for one
target produce one winner and one deterministic refusal —
`uq_plan_mutation_reverts` decides.

Domain errors: `IdempotencyConflict`, `UndoUnavailable` (nothing reversible on
this lineage), `UndoConflict` (a precondition failed), `PlanStateConflict` (the
authoritative row moved between the locked read and the write). They are internal
outcomes, deliberately **not** HTTP codes — there is no transport layer to map
them onto yet.

## 17. Schema and migration

One migration, `b3c4d5e6f7a8`, a single new head off `f0a1b2c3d4e5`. Additive
only: two columns on `training_plan` and one new table. Nothing is dropped,
renamed or rewritten, and no existing value is edited (CLAUDE.md A2).

One caveat that "additive" does not cover, so it is stated rather than implied:
`lineage_id` lands `NOT NULL` with **no server default** (the value has to be
unique per row, so there is no constant a default could supply). Pre-PR2 code
does not name the column, so its `INSERT` into `training_plan` — the one in
`POST /training-plan/save` — would violate the constraint. That does not make a
code rollback *more* dangerous than this repository already treats it: rolling
code back past any new revision leaves `alembic_version` naming a revision the
old tree does not contain, and boot upgrade is fatal (CLAUDE.md A2), so the
container refuses to start either way. It does mean the documented escape hatch
of running old code against the migrated schema (`FITX_DB_AUTO_UPGRADE=0` /
`FITX_DB_UPGRADE_FAIL_OPEN=1`) is not available for this revision. Making the
column nullable and tightening it in a later contract migration is the standard
two-step if that hatch is ever needed.

`lineage_id` is backfilled with a fresh random token **per row** — one shared
constant would be far easier to write and would make two users' plans look like a
single lineage. `mutation_version` starts at 0 for everything that already
exists. **No historical mutation events are fabricated**: the application never
recorded them, so any row written here would be fiction, and an undo standing on
fiction would restore a snapshot that was never real.

The revision follows a994f9bed783's verify-or-create shape, because the fresh-DB
boot path runs `db.create_all()` before stamping and upgrading: it creates only
what is missing, and raises rather than reporting success against a table that
shares the name but not the shape.

`lineage_id`'s uniqueness is a unique **index**, not a table constraint (the
convention `WorkoutSession.public_id` already follows), because an index can be
added to a live table on both PostgreSQL and SQLite — so the invariant holds on
both rather than on production only. Only the NOT NULL tightening needs SQLite's
batch rebuild, and it is guarded to run solely when the reflected column is
actually nullable, so the create_all boot path never pays for one.
`tests/test_migration_graph.py` runs the revision upgrade → downgrade → upgrade
against a deployed pre-column schema and against a create_all-built one.

## 18. Explicitly NOT in PR2

No AI Coach tool, LLM function calling, prompt change, intent classification or
tool schema · no confirmation UX or impact classification · no public, mobile or
AI-facing API — **no history endpoint, no journal serialization of any kind** ·
no history UI · no Flutter change · no redo, no arbitrary rollback, no plan diff
visualization · no proactive coaching, fatigue/plateau/adherence detection or
deload · no Pump Check or Weekly Check-in driven mutation · no nutrition/calorie/
macro mutation · no feature flag, no deployment.

`POST /training-plan/save` is **not** migrated onto the journal. It replaces a
plan wholesale; recording it as a targeted mutation would put a snapshot in the
journal that an undo could later restore over a plan the user deliberately
regenerated.

### Handoff to Sprint 1 PR3

PR3 owns the AI-facing surface: tool registration, intent → typed command,
confirmation UX, and whatever transport carries the result. Four notes:

1. The result contract is already shaped for it — `outcome`, `plan_version`,
   `mutation_id` (opaque), `replayed`. Map those onto a response; do not add raw
   database identifiers.
2. The operation key is the caller's to mint, and it must be **stable across the
   retry of one logical user request**. A key generated per HTTP attempt provides
   no protection at all.
3. `actor="ai_coach"` is metadata. Authorization stays with the authenticated
   session; do not let a tool argument choose it.
4. The four domain errors in §16 need an HTTP/tool mapping. Deliberately
   unassigned here so the transport layer owns its own vocabulary.

### What a later tool/API layer will need to observe

This domain introduces **no logging of user content and no metrics** — it has no
endpoint and no provider interaction, so any metric would be speculative. The
package has no logger at all, and an architecture test enforces that: snapshots,
command payloads, reason text, exercise lists, operation keys and fingerprints
are exactly the material that must not reach a log line.

When a transport layer lands it will want: mutation-outcome counts by command
type and error kind, replay and conflict rates (a rising conflict rate means
callers are minting keys wrong), undo rate, and rejection rates for ambiguous
targets (a rising rate is the signal that name-only identity has become the
bottleneck). None of that may carry plan payloads, exercise text from user input,
reasons, keys, prompts, tokens, or user IDs as metric dimensions.

---

# Sprint 1 PR3 — the AI Coach's plan-editing tools

PR1 built the boundary. PR2 gave it durable history, versioning and undo. Both
shipped with **no caller**. PR3 is the caller, and it is the first time in this
codebase that a language model can cause a durable write to user data.

The one-line rule stays exactly what PR1 wrote: **an AI may *request* a plan
change; it never owns plan persistence.** Everything below is the machinery that
makes that true when a probabilistic system is the one asking.

## 19. Where the authority actually sits

```
provider (Bedrock / OpenAI)
        |  a tool call - text, untrusted
app/services/ai_coach.py                 <- authenticated user_id lives here
        |
app/services/coach_plan_tools/           <- the trusted execution context
        |
app/services/plan_mutation/              <- PR1/PR2: owns the plan + the transaction
```

`ai_coach` does **not** import `plan_mutation`; `coach_plan_tools` does not
import `ai_coach`. Both directions are enforced structurally
(`tests/test_plan_mutation_architecture.py`,
`tests/test_coach_plan_tools_architecture.py`), and PR1's
`test_no_coach_module_imports_the_mutation_domain_yet` was *narrowed* rather
than deleted: it now asserts that every Coach module still reaches the domain
only through the bridge.

| Module | Owns |
|---|---|
| `schemas.py` | the six LLM-facing tool definitions; bounds imported from the domain |
| `parser.py` | model arguments to a typed PR1 command, or a refusal |
| `identity.py` | the server-minted operation key |
| `results.py` | the bounded result and error vocabulary |
| `executor.py` | flag, turn, budget, actor, the domain call, transaction settle |

## 20. Six narrow tools, never a generic one

`replace_training_plan_exercise`, `add_training_plan_exercise`,
`remove_training_plan_exercise`, `update_training_plan_prescription`,
`move_training_plan_day`, `undo_last_training_plan_change`.

There is deliberately no `mutate_training_plan(operation, payload)`, no JSON
Patch, no `set_plan_field`. A generic tool would hand the model an arbitrary
mutation language *above* the typed boundary and make PR1's whole contract
decorative. An architecture test rejects a property named `operation`, `payload`,
`patch`, `path`, `field`, `value` or `sql` anywhere in a schema.

**A property exists only if it carries user intent.** `user_id`, `plan_id`,
`lineage_id`, `mutation_version`, `mutation_id`, `actor`, `reason`, the
operation key, both snapshots and every fingerprint appear in no schema, and a
test walks the published schemas to prove it. Every bound (`MIN_SETS`,
`MAX_SETS`, `MAX_EXERCISE_NAME_CHARS`, `MAX_REPS_CHARS`, `WEEKDAYS`) is imported
from `plan_mutation.validation` — the schema is UX that steers the model, and
the domain remains the only authority that accepts or rejects.

## 21. The parser is the boundary; the schema is a hint

A provider may ignore a JSON schema. `parser.py` therefore refuses, never
coerces:

* an **unknown property** — named in the error, not silently dropped. Dropping
  `{"day": "Cuma", "sets": 4}` on a remove call would execute a different
  request than the model expressed and nobody would learn about it;
* a **missing required property** — no defaulting. The plan *generator* fills
  gaps because a slightly-wrong plan beats no plan; a mutation that invents
  "3 sets" is a fabricated instruction;
* a **wrong type** — including `bool`, which is an `int` subclass and would
  otherwise arrive as "1 set";
* an **empty string** — a target nobody named.

`build_command` constructs one known frozen dataclass per branch, by keyword.
No dynamic dispatch on a model string, no `**arguments` splat — both are
AST-enforced. The model cannot reach a command type this function does not name.

An argument refusal spends **no operation identity**, so a corrected retry in
the same turn is a fresh operation rather than a conflict.

## 22. Operation identity, and the honest limit of it

```
aic:{request_id}:{sha256(domain \0 turn \0 command_type \0 semantic_fingerprint)[:16]}
```

* **turn** — `g.request_id`, the repository's existing server-generated
  per-request id. It cannot be injected by a client, it is already the log and
  SSE correlation id, and it survives everything one turn has to survive: a
  provider retry, a Bedrock-to-OpenAI fallback, and every tool-continuation
  round. A second turn-identity system would have been a competing source of
  truth with no evidence one was needed.
* **semantic** — the domain's own `semantic_fingerprint` / `undo_fingerprint`.
  Reusing PR2's comparator means the key and the replay check can never
  disagree about what "the same mutation" is. **Raw model JSON is not hashed**:
  whitespace, key order or one echoed field would each mint a fresh key for one
  intent.

There is **no occurrence counter**. Adding one would make the second delivery of
one mutation a *new* operation — the duplicate `add` and double `undo` this
mechanism exists to prevent. Distinct commands separate themselves: different
intent, different fingerprint, different key.

**Scope, stated plainly:** this guarantees convergence *within one server Coach
turn*. It is not transport-level exactly-once. A new HTTP request carries a new
`request_id` and is a new intent — which is correct: a user who sends "add cable
flies" in two messages has asked twice. No durable client-supplied chat-request
identity exists in this architecture, and inventing fragile state to claim a
broader guarantee would be a promise the system cannot keep.

## 23. The per-turn mutation budget

`MAX_PLAN_OPERATIONS_PER_TURN = 2`, request-scoped on `g`, reset by
`ai_coach._begin_coach_turn()` so blocking and streaming share one definition of
a turn. A test pins it strictly below `_COACH_TOOL_LOOP_CAP` (5) — a budget at
or above the loop cap is decorative.

The loop cap bounds how many times the model may call *any* tool. It was never
sized as a limit on durable writes, and a model that misreads one message as
five edits would spend it happily. Two is what an honest single message asks
for; more is a plan redesign, which must not be decomposed into a mutation
swarm.

**A repeated operation key costs nothing.** If a repeat consumed budget, the
second delivery of one mutation could be refused as "too many changes" — and the
model would tell the user their edit was rejected when it had in fact been
applied. That is the worst available answer, so replay is free.

The budget refusal never rolls back earlier edits: punishing the user for the
model's behaviour would be a second bug on top of the first.

## 24. Actor, reason, authorization

`actor="ai_coach"` is set by the server, appears in no schema, and is **audit
metadata, not authorization**. The mutation is scoped to the caller's own plan
either way — PR1 made cross-user mutation structurally inexpressible.

`reason` is `None`. Every candidate was worse: the raw user message is untrusted
input in a durable audit column, model-authored text is unverified narration of
what the model *believes* it did, and a fixed "AI Coach" string says nothing the
`actor` column does not. An AST test pins both values at the single
`MutationContext(...)` construction site.

## 25. What the model is told back

A tool result is the next model call's input. It is billed, and whatever is in
it can be paraphrased to the user. So the payload is small and closed:

```json
{"status": "applied|no_op|replayed",
 "operation": "replace_exercise",
 "plan_version": 3,
 "change": {"day": "Pazartesi", "exercise": "...", "replacement": "..."},
 "summary": "<one deterministic Turkish sentence>",
 "note": "<server guidance, when there is any>"}
```

Errors are `{"status": "error", "error": "<CODE>", "message": "...",
"detail"?: "..."}` with sixteen codes and a server-authored recovery
instruction attached to each — the recovery policy travels with the failure it
describes instead of competing with the system prompt. Codes are mapped by
exception **class**, so a domain wording change is not a contract change.

Never crosses: both snapshots, the plan document, `lineage_id`, any row id, the
operation key, any fingerprint, SQL, or a raw exception message. `mutation_id`
is withheld too — the model cannot act on it, and an identifier in the context
window is an identifier that can be echoed to the user.

`replayed` is distinct from `applied` on purpose: the model must be able to say
"that is already done" instead of narrating a second change that never happened.
A `no_op` says so explicitly and instructs the model not to claim a change.

**Context freshness.** The plan block in the prompt was built before the tools
ran, so a committed mutation makes it stale. Every applied result carries a note
saying which of the two sources is current. Re-rendering the block mid-turn was
rejected: it would put a second description of the plan in the window and would
be stale again after the next call.

## 26. Provider parity, deadlines and transactions

One canonical definition list feeds both provider formats
(`_coach_tool_defs_for_call` then `_to_openai_tool` / `_to_anthropic_tool`), so
the two schemas cannot drift; a test compares them field for field. The
Anthropic cache breakpoint still lands on the last tool, so the order stays
stable for a given flag state.

The absolute turn deadline (`AI_COACH_TURN_TIMEOUT_SECONDS`) and the B-rule are
unchanged. The B-rule now guards a **durable write**: once a plan tool has run,
Bedrock never falls back to OpenAI, because the second provider would re-run the
turn against a plan that has already changed and has no way to know it. The turn
degrades to a soft error and **the mutation stays committed** — a failed
continuation is not a reason to reverse a change the user asked for.

`executor._settle_transaction()` guarantees no transaction is held across the
provider call that follows a tool. It rolls back only a *provably read-only*
residual (in a transaction, nothing new/dirty/deleted); anything pending is left
to its owner, because a blanket rollback here would be a second uninvited
transaction authority.

This is also why PR2's known Minor was closed in this PR: `service.py`'s
`IntegrityError` arbitration branch re-read the journal after its rollback and
left that read open. With no caller that was a documented residual; with a
blocking provider call immediately downstream it is a transaction held across
network I/O. `_arbitrated_result` now closes it in a `finally`, at the layer
that owns the transaction.

## 27. Undo

Undo is a dedicated, argument-less tool. There is no `undo(version)`, no
`rollback_to`, no redo — PR2's `undo_last_change` is the one canonical inverse
and its precondition is bytes, not a version number.

`undo_fingerprint()` is constant, so every undo in one turn *is* the same
operation: a duplicated delivery replays the first undo instead of reaching back
and reversing a second, earlier change. Arguments are refused rather than
ignored, so a model that thinks it can choose *which* change to undo finds out
that it cannot.

The prompt states the negative case explicitly: a tool error, the model's own
uncertainty, or a user who sounds hesitant are **not** reasons to undo.

## 28. The rollout flag

`AI_COACH_PLAN_MUTATION_TOOLS_ENABLED`, registered in the canonical
`app/feature_flags.py::ROLLOUT_FLAGS` (default OFF, `staging_only`, read through
`current_app.config`, fail-closed on anything unexpected). It gates **both**
halves: OFF removes the tools from both provider schemas *and* refuses
execution, so a model that remembers a name from an earlier turn still cannot
reach the domain. OFF also emits no policy block — a prompt must not describe a
capability the model cannot use.

Rollback stops new AI writes; it does not revert mutations already applied.
Those are ordinary versioned plan history and the user can undo them.
`docs/ROLLOUT.md` §3 carries the journal query an operator reviews during the
observation window: the abort signals here are individual events, not rates.

## 29. Observability

One PII-free line per tool call:

```
[COACH][PLAN_TOOL] request_id=... tool=<name> outcome=<status|ERROR_CODE>
```

Both interpolated values come from closed server-owned vocabularies, and an AST
test pins *which expressions* may be logged — checking the arguments rather than
the format string, because the format is a server constant and the arguments are
where user or model data would enter. No arguments, plan text, summary, reason,
operation key or fingerprint is ever logged.

The unexpected-error path logs the exception's **class**, not the exception, and
the same test rejects `exc_info=`/`extra=` outright. A traceback is not
deliberately audit material, but it carries the exception's own message — and
the realistic unexpected failure here is a SQLAlchemy `StatementError`, whose
message embeds the statement and its bound parameters, i.e. the whole plan
document. For the same reason `IdempotencyConflict` is raised `from None`: it is
resolved inside an `except IntegrityError` handler, and implicit chaining would
hang the driver's error off an ordinary domain outcome.

The authoritative record of what changed remains the `PlanMutationRecord`
journal, queryable by user and lineage. No metric is emitted: the honest
signals for this capability (did the AI change something the user did not ask
for?) are not countable, and a counter that implied they were would be worse
than none.

## 30. Explicitly NOT in PR3

No impact classifier or confirmation/preview UX · no mobile mutation UI or
Flutter change · no public/mobile REST mutation API and still no history
endpoint · no redo, `rollback_to_version`, restore-by-id or plan diff view · no
full-plan regeneration, frequency/goal/split mutation or deload through these
tools · no proactive or scheduled editing · no plateau/fatigue/adherence
detection · no Pump Check or Weekly Check-in driven mutation · no nutrition
mutation · no push notification · no migration (Alembic head unchanged at
`b3c4d5e6f7a8`) · no deployment and no production flag activation.

`POST /training-plan/save` is still a separate, untouched whole-plan path.
