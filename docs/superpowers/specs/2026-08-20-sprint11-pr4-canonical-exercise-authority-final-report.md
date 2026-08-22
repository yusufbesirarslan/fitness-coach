# Sprint 11 PR4 — Canonical Exercise Authority — Final Report

Date: 2026-08-22
Status: final validation complete
Repo: `yusufbesirarslan/fitness-coach`

## Executive verdict

**READY TO SHIP.** AxisAI no longer lets a provider, a client, or the AI Coach
decide what an exercise is. A server-owned, versioned catalog is the sole
authority; identity is a stable `exercise_id`, and the display name is
presentation only. Both doors that can write a training plan — the save route
and the plan-mutation engine — resolve every exercise against that catalog
under a server-verified equipment context, exactly and never approximately, and
fail closed on anything they cannot authorize. Legacy name-only plans keep
working unchanged and are never silently upgraded. PR3's provider-call and
repair ceilings are untouched, and no migration ships.

## Base / branch / HEAD

| | |
| --- | --- |
| Worktree | `C:\Users\yusuf\fitness-coach\.worktrees\sprint11-pr4-canonical-exercise-authority` |
| Branch | `sprint11-pr4-canonical-exercise-authority` (local only, never pushed) |
| Base | `95fb056` — PR3, `#223` |
| Last implementation commit | `ba4a7ea` |
| Final HEAD | this report's commit, on top of `ba4a7ea` |
| Commits ahead of base | 12 (11 implementation + this report) |
| Position vs `origin/main` | 12 ahead, 11 behind — no rebase, no merge |
| Diff vs base | 45 files, +7939 / −217 (including this report) |

## PR3 prerequisites verified

Checked on this branch rather than assumed from PR3's report:

- `MAX_PROVIDER_COMPLETIONS = 2`, `PRIMARY_MAX_TOKENS = 4000`,
  `REPAIR_MAX_TOKENS = 7000` — all unchanged (`plan_schema.py:58-60`).
- Exactly two `budget.complete(...)` call sites in the generation service.
- Repair remains eligible only for parse/truncation; semantic misses still do
  not re-enter repair.
- Save-time re-validation still runs before any delete.
- The exercise catalog PR3 recorded as absent is what this PR adds.

## Pre-PR4 map

Before this PR, an exercise was a free string. The provider invented `isim`
values, structural validation bounded their length, semantic validation counted
days and training slots, and nothing checked that a named movement existed, was
possible with the user's equipment, or meant the same thing twice. A second,
unwired opinion sat beside it: `exercise_knowledge_base.py`'s `EXERCISE_KB`,
57 lines of hand-written risk/difficulty/progression metadata that nothing
imported. The only live symbol in that file was `REQUIRED_MOVEMENT_COVERAGE`,
eight prompt-directive labels.

## The exercise authority

`app/services/exercise_catalog.py` — a server-owned, bundled, versioned catalog
loaded through an `@lru_cache`d `load_exercise_catalog()`, reading the reviewed
asset `app/services/training_assets/exercises.json`. It is the only thing in the
system that can say what an exercise is. Neither the provider, nor the client,
nor the AI Coach can add to it, and nothing writes to it at runtime.

Everything it returns is immutable: `ExerciseDefinition` is a frozen dataclass,
`equipment` is a `frozenset`, and both indexes are `MappingProxyType`. A
malformed asset raises `CatalogConfigurationError` at first use rather than
degrading.

Completeness here is an enumeration, not a claim. Exactly three statements
write `TrainingPlan.plan_data`:

- `app/blueprints/training.py:265` — the save route (Task 4)
- `app/services/plan_mutation/service.py:295` — apply (Task 5)
- `app/services/plan_mutation/service.py:407` — undo (restores previously
  validated bytes)

`app/blueprints/nutrition/plan.py:38` writes `NutritionPlan`, a different table.
There is no fourth door.

## Stable identity

`exercise_id`, matching `^ex_[a-z0-9_]+$` (`exercise_catalog.py:16`). The
display name `isim` is presentation only and is always overwritten with the
catalog's `canonical_name` on both write doors, so a valid ID paired with a
fabricated display name persists the catalog's name, not the supplied one —
demonstrated, not asserted: an entry submitted as
`{"isim": "Totally Invented Movement", "exercise_id": "ex_barbell_back_squat"}`
persists as `Barbell Back Squat`.

Renaming a catalog entry's display name does not change its ID, and does not
rewrite anything already logged.

## Catalog coverage

Version 1, **73 exercises**, all active, carrying 60 declared aliases for
**133 unique normalized lookup keys**. 68 are resistance entries and 5 are
cardio modalities.

| Context | Compatible entries |
| --- | --- |
| `spor_salonu` | 68 |
| `minimal` | 40 |
| `ev` | 20 |
| cardio `kosu` / `yuruyus` / `ip_atlama` / `bisiklet` / `yuzme` | 1 each |
| cardio `karisik` | 5 |

Every number above was re-derived from `load_exercise_catalog()` during review,
not copied from an implementer's report.

## Names, aliases, normalization

`normalize_exercise_lookup` canonicalizes safe spelling variants only: NFKC,
unicode dash variants folded to ASCII `-`, casefold, whitespace collapse. It
never stems, never deletes tokens, never scores similarity.

Verified at the boundary in both directions. These collapse to one key:
`bench  press`, `BENCH PRESS`, and `Push‑Up` / `Push–Up` / `Push—Up` (non-breaking
hyphen, en dash, em dash) and the NFKC fullwidth `Ｐush-Up`. These stay distinct:
`Incline Bench Press`, `Pushup`, `Push Up`, `Barbell Rows`.

A normalized collision between two entries is rejected at *load* time
(`CatalogConfigurationError`), so two entries can never claim one lookup key.
Confirmed empirically across all 73 entries and 60 aliases: 133 distinct keys,
zero collisions.

## Resolver and failure modes

Exact, and in this order:

1. **A supplied `exercise_id` wins outright** — it must match `ID_PATTERN`, must
   exist, and must be active. There is **no fallback to the name** if it fails.
2. **Otherwise the name is looked up exactly**, after normalization. No match →
   `ExerciseUnresolved`. More than one → `ExerciseAmbiguous` (never "first
   hit"). Inactive → `ExerciseInactive`.

The catalog-level `ExerciseAmbiguous` branch is defence in depth rather than a
live path: the loader already refuses a colliding asset, so every lookup bucket
holds exactly one entry. It is kept because the cost of the check is nothing and
the cost of "first hit" would be a silently wrong exercise. Ambiguity that *is*
reachable lives at the mutation door — two entries with the same name in one day
— and raises `AmbiguousExerciseTarget` rather than guessing.

The no-fallback property is the one most worth proving, because a fallback
would make a forged ID free. Six variants were submitted, each beside a display
name that resolves perfectly on its own — nonexistent ID, wrong-case ID, empty
string, numeric, and the degenerate `ex_` — and all six were refused as
`GenerationExerciseIdentityInvalidError`. (`exercise_id: null` is accepted and
falls back to the name; `null` means no claim was made, identical to the key
being absent.) A *name* shaped like an ID is refused too, rather than falling
through as an unknown exercise.

There is no stemming, token deletion, nearest match, edit distance, embedding,
`difflib`, `rapidfuzz`, substring fallback, or LLM adjudication anywhere in the
resolution path. Of 14 near-miss spellings driven at a real mutation, 12 were
refused; the two that resolved were a *declared alias* and the documented
whitespace collapse.

## Equipment compatibility

`is_exercise_compatible(exercise, context)` runs against the *server-verified*
context, never a client-asserted one. Contexts are closed: `spor_salonu` = full
gym, `ev` = bodyweight, `minimal` = bodyweight + dumbbell + resistance_band.
Cardio is gated separately by `cardio_type` rather than by `equipment_context`,
because a home user who runs outdoors is a real product case.

That carve-out is only sound while a cardio movement can land on a `kardiyo`
day and nowhere else. Both write doors therefore enforce placement through one
shared `check_placement`, reused rather than copied and pinned to a single
definition by an architecture guard. Without it, `ekipman="ev"` could persist
Swimming inside a strength day — reproduced end to end as P1-3 before the fix,
refused across all 15 equipment × cardio-entry cells after it, and refused at
the mutation door as well.

Tampering cannot widen compatibility: a barbell exercise submitted into an `ev`
plan with a forged `equipment: ["bodyweight"]` field is refused.

## Substitutions

None, deliberately. Nothing anywhere swaps one exercise for another. An
unresolvable or incompatible reference fails the operation with a typed error;
it is never quietly replaced with a "close enough" catalog entry. This matches
the design spec's non-goal and avoids silently changing user intent.

## Injury boundary

PR3's report recommended injury enforcement as PR4 scope. **PR4's design
narrowed that deliberately** (§8 and non-goal "No medical injury inference"),
and this PR complies: the catalog carries no risk, difficulty, or
contraindication metadata, and the deleted `EXERCISE_KB`'s `risk` /
`difficulty` / `progression` fields were **not** ported into it. They were
unreviewed opinion, and promoting them to catalog truth would have made AxisAI
appear to hold medical knowledge it does not have.

Injury screening therefore remains the pre-existing warn-only overlay
(`annotate_injuries`), which appends a note and returns warnings but never
blocks persistence and never substitutes.

**Disclosed defect, P2-16 (new, found during this review).** The overlay runs
*before* canonicalization and keys on the provider-supplied `isim` via substring
matching. Because canonicalization then rewrites `isim` to the catalog name,
three aliases of one entry produce byte-identical stored plans that differ in
whether a warning is attached:

| Provider wrote | Warnings | Stored name | Note attached |
| --- | --- | --- | --- |
| `Squat` | 0 | Barbell Back Squat | no |
| `Barbell Squat` | 1 | Barbell Back Squat | yes |
| `Barbell Back Squat` | 1 | Barbell Back Squat | yes |

This is not a safety regression — the overlay always keyed on the raw provider
name, so the same alias evasion existed before PR4, and it never gated
persistence. What PR4 changed is that the inconsistency is now *visible*: the
stored plan can show a name the injury engine would flag, carrying no warning.
Classified P2 because it violates no ship criterion (not authority, persistence,
migration, performance, or compatibility) and both fixes — running the overlay
after canonicalization, or keying injury screening on `exercise_id`/`movement`
instead of name substrings — are real design changes belonging to PR5.

## Generated-plan contract

The provider's vocabulary is constrained in the prompt (Task 2), and the
provider is never allowed to author identity: `validate_plan_structure` takes a
keyword-only `allow_exercise_id: bool = False`, the generation call site leaves
it at the default, and only the save call site opts in. Exercise entries are
validated against a **closed key set** (`isim`, `set`, `tekrar`, `dinlenme`,
`not`, plus `exercise_id` on save only) and rebuilt by projection, so unknown
keys are refused rather than carried.

Canonicalization runs exactly once, on the final accepted candidate, strictly
*outside* the repair try/except (`service.py:282`) — verified by source index,
not by reading. An exercise-authority failure can never be caught by, or looped
back into, the repair path.

## Signed save context and tampering

The accepted equipment context reaches save inside an HMAC-signed, user-bound
`exercise_context_token` (stdlib `hmac`/`hashlib`/`base64`, domain-separated,
compared with `hmac.compare_digest`). The context is never echoed in the clear,
so a client cannot read what it is asserting, let alone edit it. A missing,
malformed, forged, or wrong-user token returns 422
`TRAINING_PLAN_SAVE_CONTEXT_INVALID`.

`validate_plan_for_save` runs to completion *before* the route reaches
`TrainingPlan.query.filter_by(...).delete()`, so a rejected save cannot alter
the current plan — verified by reproduction, not by reading: the probe saves a
valid plan, posts an invalid one, and the stored document is byte-identical
afterward. An architecture guard pins the ordering by AST line number.

The four exercise-authority failures collapse into one `SaveExerciseInvalidError`
(422) on purpose — letting a client distinguish "no such exercise" from
"retired" from "not allowed by your equipment" would make the endpoint a
catalog oracle.

Save also persists the verified context *inside* the stored document under
`exercise_context`, which is how the mutation engine later learns the plan's
equipment truth without any signature change. A clean end-to-end save of the
alias `"Squat"` stores
`{"isim": "Barbell Back Squat", "exercise_id": "ex_barbell_back_squat", ...}`
with document keys exactly `{program, exercise_context}`.

## Legacy plans, Adaptive Coaching, and logging

**Legacy plans.** A pre-PR4 row is a bare JSON list with no `exercise_id` and no
`exercise_context`. Every reader still accepts both that and the wrapped
`{"program": [...]}` shape, and such plans are never silently upgraded. Coverage
now spans the presenter, workout state and its bounded public projection,
`/workout/status`, `/training/bootstrap`, workout-session fingerprinting,
training history, and the Adaptive Coaching contract. A legacy plan that is
mutated stays legacy and never acquires an ID; an ambiguous legacy name is
refused rather than backfilled.

**Mutation boundary.** `apply_command` resolves the document's authority once,
before command dispatch. On a canonical plan the target slot is found by stable
`exercise_id` rather than by wording, and an add or replace writes `exercise_id`
and the canonical `isim` together — closing P1-4, where a replace moved the name
and left the ID pointing at the exercise that used to be there. Fail-closed in
both directions: a canonical plan with an unreadable context is refused rather
than downgraded to name matching, and a document carrying `exercise_id` with no
context block is refused too.

Every `ExerciseResolutionError` is translated to `InvalidMutation` before it
leaves the pure engine; no raw `ValueError` reaches `service.py`, where it would
have surfaced as a 500. No refusal message leaks an exercise id, canonical name,
alias list, or the context block.

**Adaptive Coaching.** Unchanged. Command dataclasses, operation keys, journal
records, transaction order, mutation versions and snapshot format are all
untouched. Snapshots are the full serialized document, so `exercise_context` and
`exercise_id` ride along in undo for free. `undo_last_change` restores
`target.before_snapshot` byte for byte, calls only `parse_plan_document`, and
never re-resolves — undo returns the user to their own prior state rather than
making a new authorization decision.

**Logging gap, deliberate and unfixed.** `WorkoutLog.exercise_name` is a
`String(120)`, so historical logs identify exercises by name, cannot be joined
to catalog identity, and renaming a catalog entry does not retroactively rename
what is already logged. PR4 adds no backfill and no column. Pinned by test so it
is a known state rather than a surprise.

## Provider call upper bound

Unchanged at 2, enforced by `_CompletionBudget`, which raises
`GenerationUnavailableError` on a third attempt. PR4 adds no provider call:
canonicalization is pure catalog work on an already-accepted candidate. Repair
remains exactly 1, still only for parse/truncation. Provider, model id,
temperature and token budgets are untouched.

## Performance

Zero database queries and one catalog load per canonicalization pass — proven by
an engine `before_cursor_execute` listener rather than a mocked session, over a
representative 27-reference week. Within a pass, each *distinct* normalized
spelling is resolved once: 5 references across 3 entries and 4 spellings produce
1 catalog load and 4 resolutions, not 5. The catalog loader itself is
`@lru_cache`d process-wide.

## Migration and seeding strategy

No migration, and no seeding, because there is nothing to seed. The catalog is
bundled data, not a table: `migrations/versions/` still holds 36 files and
`tests/test_migration_graph.py` still pins the single head `c1d2e3f4a5b6` on
this branch. No boot-time insert, no admin surface — deployment requires no data
step, and rollback is a code revert.

Disclosure: `origin/main` has since moved its head to `c2d3e4f5a6b7` via PR
`#224`, which this branch is not based on. Reconciling the two is the merge's
job, not PR4's.

## Security / privacy

- Signed, user-bound save context; never echoed in the clear.
- A declared `exercise_id` is treated as a *claim* and re-resolved against the
  live catalog on every submission, so a retired entry stops being savable the
  moment it is retired.
- Closed key set on exercise entries; unknown client keys refused, not stored.
- Error messages carry codes, never the token, the payload, or an exercise name.
  The save-boundary collapse prevents catalog enumeration.
- All plan queries remain scoped to the authenticated user; no command carries a
  `user_id`.
- The context-token module is stdlib-only and knows nothing about HTTP, Flask,
  or logging — pinned by guard.

## Tests, guards, and full validation

New suite `tests/test_sprint11_exercise_authority.py` — 63 test functions,
**168 collected cases** after parametrization — plus additions
to the presenter, workout-state, workout-session, training-history,
adaptive-plan-context, migration-graph, plan-mutation and coach-plan-tools
suites.

Architecture guards worth naming, because each one had to be made to bite:

- **No second authority / no fuzzy path** — scans the *executable text* (comments
  and docstrings stripped by `tokenize`, string literals kept) of every module
  in `exercise_catalog.py`, `training_generation/` and `plan_mutation/`, with the
  file set **derived from the package directories** so a brand-new module cannot
  escape it, and a ten-module floor so a glob that stopped matching cannot turn
  the guard into a test that reads nothing.
- **Zero SQL** — counts statements actually executed against the engine, and
  ships with a self-test proving the counter is not inert.
- **Provider never authors identity** — pins the keyword-only default and both
  call sites individually.
- **Catalog never persists**, **save validates before delete**, **provider budget
  is 2 with 1 repair**, **generation never imports the mutation journal**.

**Full validation at `ba4a7ea`:**

```
python -m compileall -q app tests
  exit 0

python -m pytest -q  (full non-load suite)
  4836 passed, 11 skipped, 3 deselected, 16160 warnings in 3759.82s (1:02:39)

python -m pytest -q  (15 focused suites, Task 7 Step 1)
  883 passed, 2760 warnings in 334.80s (0:05:34)

git diff --check                       (working tree)
  clean

git diff --check origin/main...HEAD    (whole branch)
  7 lines, all in two markdown documents:
    docs/superpowers/specs/…-design.md      3 × trailing whitespace
    docs/superpowers/plans/…-authority.md   1 × blank line at EOF
```

Those seven lines are disclosed rather than silently "fixed". They come from the
design and plan documents committed in `43247f5` / `6daa2f8`, and the trailing
whitespace is markdown hard-break syntax on three consecutive header lines
(`Date:`, `Status:`, `Repository:`) — stripping it would collapse them onto one
rendered line. No production, test, template, locale or static file is flagged.

Zero failures and zero errors in both runs. The 11 skips and 3 deselections are
pre-existing and unrelated to PR4; the warnings are the repository's existing
`datetime.utcnow()` deprecation noise.

**Controller's independent neuter battery over the Task 6 guards** — because the
Task 6 implementer's own driver was not left on disk, its "24 of 24 fired" claim
could not be re-run and was not taken on faith. Six cases were rewritten from
scratch, each breaking a real production line and requiring the guard to fail:

| Neuter | Result |
| --- | --- |
| `import difflib` into `exercise_catalog.py` | detected |
| `rapidfuzz` inside a **brand-new** module dropped into the package | detected |
| an executed `SELECT` inside canonicalization | detected, printing the SQL |
| `MAX_PROVIDER_COMPLETIONS` 2 → 3 | detected (`assert 3 == 2`) |
| `.delete()` moved above validation in the save route | detected (`assert 253 < 246`) |
| the query-counter listener unwired | detected (`assert 0 == 1`) |

Six run, six detected, zero survivors. The second is the load-bearing one: it
proves the scan's file set is genuinely derived from the package directories,
so a plausible second resolver cannot be added in a new file and walk past a
hand-maintained list.

## Independent review and P0/P1/P2 disposition

Every task received an independent review, plus fix rounds where needed. Three
consecutive Task 5 reviewers and the Task 6 implementer died on the same API
session limit; where a subagent could not be re-dispatched, the controller
performed the review directly and mitigated its own bias the only way that
works — by making every claim an experiment rather than a reading. That produced
a 10-trace attack probe and an 11-case neuter battery at Task 5 (11 effective
neuters, 11 detected, zero survivors), an independent 6-case neuter battery over
the Task 6 guards, and a fresh 13-attack trace at Task 7.

**P0: 0. P1: 0.**

Closed during the PR: P1-1 (non-ASCII token segment surfaced as a 500), P1-3
(cardio placement bypassed the equipment gate), P1-4 (replace moved the display
name and left the ID pointing at the previous exercise).

Sixteen P2s were raised across the PR. Three are closed: **P2-1** and **P2-3**
were tests that could not fail, fixed in the round that found them, because a
test that cannot fail is worse than no test; **P2-8** (three false claims in
`docs/TRAINING_GENERATOR.md`) was closed by Task 6 and verified line by line.

The remaining thirteen are accepted, each with a stated reason:

| ID | Finding | Why accepted |
| --- | --- | --- |
| P2-2 | `/training-plan/active` echoes the context to the user who owns it | not a leak — the owner may see their own context |
| P2-4 | non-canonical base64 still verifies | the HMAC is what binds; encoding slack changes nothing |
| P2-5 | the save token has no expiry | an integrity device, not a grant of access |
| P2-6 | a non-dict request body yields a 500 | pre-existing, predates PR4 |
| P2-7 | MCP coach context growth | outside the exercise-authority boundary |
| P2-9 | a legacy-shape session test does not discriminate the shape its name claims | pre-existing test-quality work outside PR4's scope |
| P2-10 | a `kardiyo` day under `ekipman="ev"` accepts Swimming | the deliberate PR1/PR2 carve-out — the home user who trains outdoors. Narrowing it is a catalog-data decision, not an authority defect |
| P2-11 | Coach narrates the caller's alias while the plan stores the canonical name | needs a product decision, not a fix |
| P2-12 | `test_status_endpoint_never_emits_exercise_id` is a weak guard | forward tripwire; the real evidence is the presenter/serialization tests. Disclosed by its own author |
| P2-13 | the zero-SQL guard pins `== 27` references | the count is what stops "zero SQL" being cheap on a shrunken plan; fails loudly, one-line fix |
| P2-14 | `executable_text()` skips a bare string statement mid-function | unreachable by construction; the alternative has a demonstrated worse failure mode |
| P2-15 | `REQUIRED_MOVEMENT_COVERAGE` and `MOVEMENT_VOCABULARY` still diverge | documented rather than unified; reconciling would change the provider prompt |
| P2-16 | injury overlay runs before canonicalization (see Injury boundary) | warn-only, pre-existing evasion, both fixes are PR5 design work |

## Files and commits

45 files changed, +7939 / −217 against `95fb056` (including this report).

| SHA | Message |
| --- | --- |
| `43247f5` | docs(training): design canonical exercise authority |
| `6daa2f8` | docs(training): plan canonical exercise authority |
| `9b34ea5` | feat(training): add canonical exercise catalog |
| `9df8564` | feat(training): constrain provider exercise vocabulary |
| `9176822` | feat(training): resolve generated exercises canonically |
| `8668661` | feat(training): enforce canonical exercises on save |
| `664b633` | fix(training): close save-context and cardio placement gaps |
| `86213df` | feat(training): enforce exercise authority in plan mutations |
| `9eaa065` | fix(training): make cardio placement rule public and close identity gap |
| `3afd5fb` | test(training): guard identity writes, not identity mentions |
| `ba4a7ea` | docs(training): define canonical exercise domain |
| *this report* | feat(training): complete canonical exercise authority |

## Final repository state

| Field | Value |
| --- | --- |
| Repository | `yusufbesirarslan/fitness-coach` |
| Worktree | `C:\Users\yusuf\fitness-coach\.worktrees\sprint11-pr4-canonical-exercise-authority` |
| Branch | `sprint11-pr4-canonical-exercise-authority` |
| Base SHA | `95fb0563e04479124d716ccfc7325f6642bf4d6c` (PR3, `#223`) |
| Task 1 commit | `9b34ea5` |
| Task 2 commit | `9df8564` |
| Task 3 commit | `9176822` |
| Task 4 commits | `8668661`, fix round `664b633` |
| Task 5 commits | `86213df`, fix round `9eaa065`, controller guard rewrite `3afd5fb` |
| Task 6 commit | `ba4a7ea` |
| Task 7 commit | this report |
| Final HEAD | this report's commit (SHA recorded in the ship handoff) |
| Commits ahead of `origin/main` | 12 ahead, 11 behind (never rebased) |
| Working tree state | clean |
| Untracked files | none outside the git-ignored `.superpowers/` workspace |
| Focused test totals | 883 passed, 0 failed (5m34s) |
| Full suite totals | 4836 passed, 11 skipped, 3 deselected, 0 failed (1h02m39s) |
| Migration validation | 36 revision files, single head `c1d2e3f4a5b6`, no PR4 migration |
| P0 | 0 |
| P1 | 0 |
| Accepted P2 | 13 (P2-1, P2-3, P2-8 closed) |
| Provider-call max | 2 (unchanged) |
| Repair max | 1 (unchanged) |
| Push status | not pushed |
| PR status | no pull request opened |
| Merge status | not merged |
| Deploy status | not deployed; no production flag changed |

## Remaining gaps

- Catalog coverage is 73 entries; fail-closed refusals scale with coverage gaps
  the moment real canonical plans exist.
- The legacy logging gap (`WorkoutLog.exercise_name`).
- P2-11 wording drift and P2-16 injury-overlay ordering.
- The confirmation surface added to `origin/main` by PR `#224` has never been
  exercised against PR4's authority.

## PR5 recommended scope

1. **Catalog coverage — the headline rollout risk.** Every plan that exists
   today is legacy; the canonical path only carries load once users generate
   through the Task 4 boundary, and at that moment "the exercise the user asked
   for is not in the catalog" becomes a user-visible refusal whose only remedy
   is growing the catalog. This is the intended fail-closed posture, but it
   converts a coverage gap into a refusal rate nothing in PR4 can predict.
2. **Injury screening keyed on identity, not name substrings (P2-16).** The
   natural home for the enforcement PR3 originally wanted, now that stable IDs
   and a `movement` vocabulary exist — without inventing medical claims.
3. **Coach wording drift (P2-11).** A product decision about whether the Coach
   echoes the user's alias or the catalog's name.
4. **The legacy logging gap** — joining history to catalog identity.
5. **Prove PR4's authority covers main's new confirmation surface** after the
   merge (`coach_plan_policy/`, `plan_confirmation/`, +376 lines in
   `coach_plan_tools/executor.py`). It reaches the plan through
   `apply_plan_mutation` → `apply_command`, so it should be covered
   automatically — but this branch never saw that code, so it should be proven
   rather than assumed.

## Answers to the required final questions

**1. Canonical exercise authority?** `app/services/exercise_catalog.py`, a
server-owned bundled versioned catalog (version 1, 73 entries) behind an
`@lru_cache`d loader. Nothing writes to it at runtime.

**2. Stable identity?** `exercise_id` (`^ex_[a-z0-9_]+$`). `isim` is presentation
only and is always overwritten with the catalog's `canonical_name`.

**3. Can arbitrary model-generated names persist?** No. Three independent
reasons, each sufficient: the prompt constrains the vocabulary; generation
canonicalizes the accepted candidate and fails closed on anything unresolvable;
save re-resolves everything against the *verified* context. `exercise_id` is not
an accepted generation key at all.

**4. How are aliases resolved?** Exactly, never approximately — a normalized
lookup against canonical names plus declared aliases. Normalized collisions are
rejected at catalog load time, so ambiguity is a review-time failure rather than
a runtime coin flip.

**5. Does fuzzy matching authorize persistence?** No. No stemming, token
deletion, nearest match, edit distance, embedding, `difflib`, `rapidfuzz`,
substring fallback, or LLM adjudication anywhere in the resolution path, pinned
by a source-level guard over a derived file set. (Substring matching does exist
in the warn-only injury overlay, which authorizes nothing and cannot persist an
exercise.)

**6. What happens to unknown exercises?** The whole operation fails closed with a
typed error and nothing partial is written — `GenerationExerciseUnresolvedError`
and siblings at generation, one collapsed `SaveExerciseInvalidError` (422) at
save, `InvalidMutation` with no journal row at the mutation door.

**7. How is equipment compatibility enforced?** `is_exercise_compatible` against
the server-verified context, never a client-asserted one, with closed context
definitions; cardio gated separately by `cardio_type`, made sound by the shared
`check_placement` rule enforced at both write doors.

**8. Are substitutions implemented?** No, deliberately. Nothing swaps one
exercise for another.

**9. Can client-tampered data bypass save validation?** No. HMAC-signed,
user-bound context token; closed key set on entries; a declared `exercise_id` is
re-resolved as a claim on every submission; forged/missing/wrong-user tokens
return 422.

**10. Can an invalid exercise save alter the current plan?** No.
`validate_plan_for_save` completes before the route reaches `.delete()` —
reproduced, and pinned by an AST ordering guard.

**11. Are legacy plans readable?** Yes, in both stored shapes, across every
reader, and they are never silently upgraded. The dangerous direction is closed
too: a canonical plan with an unreadable context is refused, not degraded.

**12. Did provider-call max change from 2?** No. Still 2, enforced by
`_CompletionBudget`; PR4 adds no provider call.

**13. Did repair max change from 1?** No. Still 1, still parse/truncation only,
and canonicalization runs strictly outside the repair boundary.

**14. Did provider/model selection change?** No. No provider, model id,
temperature, or token budget was touched.

**15. Did Adaptive Coaching undo semantics change?** No. Command dataclasses,
operation keys, journal records, transaction order, mutation versions and
snapshot format are untouched; undo restores the prior snapshot byte for byte
and never re-resolves.

**16. Was a migration added?** No. 36 revision files, single head
`c1d2e3f4a5b6`, no `exercise_id` column on `WorkoutLog`.

**17. How is catalog seeding handled?** There is none, because the catalog is
bundled data rather than a table. No rows, no boot-time insert, no admin
surface, no deployment data step.

**18. What remains for Sprint 11 PR5?** See "PR5 recommended scope" above:
catalog coverage first, then identity-keyed injury screening (P2-16), the Coach
wording decision (P2-11), the legacy logging gap, and proving PR4's authority
over main's new confirmation surface after the merge.

## Final verdict

**READY TO SHIP.**
