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
| Final HEAD | **superseded** - see Latest main integration |
| Commits ahead of base | 12 (11 implementation + this report) |
| Position vs `origin/main` | **superseded** - rebased onto `7a5b2a7`; now 13 ahead, 0 behind (see Latest main integration) |
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
bundled data, not a table. No boot-time insert, no admin surface — deployment
requires no data step, and rollback is a code revert.

Updated after the latest-main integration: `migrations/versions/` now holds 37
files and `tests/test_migration_graph.py` pins the single head `c2d3e4f5a6b7`,
both of which come from `origin/main`'s `#224`. PR4 itself still adds no
migration. The reconciliation this paragraph used to defer is done — see
"Latest main integration and ship validation".

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
| Final HEAD | **superseded** - final code SHA `37d2829`, see Latest main integration |
| Commits ahead of `origin/main` | **superseded** - 13 ahead, 0 behind after the rebase onto `7a5b2a7` |
| Working tree state | clean |
| Untracked files | none outside the git-ignored `.superpowers/` workspace |
| Focused test totals | 883 passed, 0 failed (5m34s) |
| Full suite totals | 4836 passed, 11 skipped, 3 deselected, 0 failed (1h02m39s) |
| Migration validation | **superseded** - 37 revision files, single head `c2d3e4f5a6b7`, still no PR4 migration |
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

**16. Was a migration added?** No. After the latest-main integration: 37
revision files, single head `c2d3e4f5a6b7` (both from `origin/main`), no
`exercise_id` column on `WorkoutLog`, and no migration authored by PR4.

**17. How is catalog seeding handled?** There is none, because the catalog is
bundled data rather than a table. No rows, no boot-time insert, no admin
surface, no deployment data step.

**18. What remains for Sprint 11 PR5?** See "PR5 recommended scope" above:
catalog coverage first, then identity-keyed injury screening (P2-16), the Coach
wording decision (P2-11), the legacy logging gap, and proving PR4's authority
over main's new confirmation surface after the merge.

## Final verdict (pre-integration)

**READY TO SHIP** - earned at `c129606`, 12 ahead and 11 behind `origin/main`.
Superseded by the latest-main integration below, which re-proved it on the
integrated SHA rather than carrying it over.

## Latest main integration and ship validation

Added 2026-08-23. The verdict above was earned at `c129606`, when the branch was
12 ahead and **11 behind** `origin/main`. That verdict is superseded by this
section: it was re-proven on the integrated SHA, not carried over.

### Drift review before integrating

`origin/main` moved from `95fb056` to **`7a5b2a7`** — eleven commits. Ten of
them (`8160f32`, `403011d`, `3c754ab`, `f7d182c`, `9fae230`, `c5eb27d`,
`dfe62ad`, `118781b`, `d5fbe98`, `7a5b2a7`) touch only `README.md`,
`SECURITY.md` and `.github/` issue/PR templates — zero application impact
against every category the drift review screens for.

The eleventh, **`5d898b2`** (Adaptive Coaching S1 PR4, `#224`, "server-owned
training plan mutation confirmation"), is the only code commit and lands
squarely on PR4's surface: 36 files, `app/models.py`, the new
`coach_plan_policy/` and `plan_confirmation/` packages, a 376-line rewrite of
`coach_plan_tools/executor.py`, and a new Alembic revision `c2d3e4f5a6b7`.

| Category screened | Touched by main drift |
| --- | --- |
| Training generator | no |
| Plan validation (structural/semantic) | no |
| Plan save/persistence | no |
| Exercise domain | no |
| Migrations | **yes** — `c2d3e4f5a6b7`, new single head |
| Adaptive Coaching | **yes** — proposal → confirm → apply flow added |
| Workout logging/history | `workout_session/queries.py` (+12, read-only helper) |
| Shared utilities used by PR4 | `app/models.py`, `locales/*.json` |
| Test architecture | **yes** — `_ALL_PLAN_TOOL_DEFS`, migration-graph head pin |

Eight files are touched by both sides: `docs/ADAPTIVE_COACHING.md`,
`docs/handoff.md`, `locales/en.json`, `locales/tr.json`,
`tests/test_coach_plan_tools.py`, `tests/test_coach_plan_tools_architecture.py`,
`tests/test_migration_graph.py`, `tests/test_plan_mutation_architecture.py`.

### Integration strategy

**Rebase onto `origin/main` (`7a5b2a7`).** The branch is local-only
(`git ls-remote --heads origin 'sprint11-pr4*'` is empty), so no published
history is rewritten, and recent `main` is squash-merged and linear — a merge
commit here would be the anomaly. All twelve commits replayed; **git reported
zero conflicts**.

Zero textual conflicts is not the same as a correct integration, so the replay
was verified rather than assumed:

- the file set of PR4's delta is identical before and after
  (`95fb056..c129606` vs `7a5b2a7..7990b53`, 45 files both sides);
- 37 of those 45 file deltas are **byte-identical** pre/post rebase;
- for the 8 overlapping files, PR4's added/removed lines are unchanged (context
  shift only), and `main`'s added/removed lines all landed intact;
- `git diff c129606..7990b53` contains **exactly** the eleven main commits'
  content and nothing else.

That check found the one thing the clean merge hid — see the next section.

### Conflicts and resolutions

No merge conflict was raised. Two defects were introduced *by* the silent
auto-merge and fixed in `37d2829`; both are test-side, and **no production
semantics were changed during integration**.

**1 — `tests/test_coach_plan_tools_architecture.py`: a main guard silently
narrowed.** `5d898b2` introduced `_ALL_PLAN_TOOL_DEFS`
(`PLAN_MUTATION_TOOL_DEFS + CONFIRMATION_TOOL_DEFS`) and widened its three
parametrized boundary guards onto it. PR4 had inserted a fourth guard
(`test_no_tool_property_names_a_catalog_owned_concept`, Task 5) into the middle
of that block, all four still on `PLAN_MUTATION_TOOL_DEFS`. The three-way merge
aligned PR4's inserted decorator against
`test_no_tool_offers_a_generic_mutation_language` and kept PR4's narrower
argument there — so main's new confirmation tools quietly stopped being checked
for a generic mutation language. Resolved by restoring `_ALL_PLAN_TOOL_DEFS`,
i.e. main's intent. Both sides are preserved and neither is weakened: PR4's own
catalog-owned guard merged onto `_ALL_PLAN_TOOL_DEFS`, so main's confirmation
tools are now covered by PR4's exercise-authority guard as well.

**2 — `tests/test_migration_graph.py`: a hard-coded baseline count.** PR4's
`test_pr4_canonical_exercise_authority_adds_no_migration` asserted
`len(revision_files) == 36`. Main's `c2d3e4f5a6b7` made it 37. PR4 still adds no
migration; only the baseline the tripwire counts against moved. Bumped to 37
with the reason recorded in the test. The assertion keeps exactly its previous
strength — a PR4-added migration still fails here first.

### Migration impact

**PR4 introduces no migration** (`git diff 7a5b2a7..HEAD -- migrations/` is
empty), and it changes no model (`-- app/models.py` is empty). The graph after
integration is main's:

```
flask --app starter db heads              →  c2d3e4f5a6b7 (head)   exit 0
flask --app starter db upgrade  (fresh)   →  base → c2d3e4f5a6b7   exit 0   (37 revisions)
flask --app starter db upgrade  (incr.)   →  c1d2e3f4a5b6 → c2d3e4f5a6b7   exit 0
```

Both the fresh and the incremental path apply cleanly, and
`tests/test_migration_graph.py` (10 passed) pins the single head
`c2d3e4f5a6b7`, chained off Sprint 10 PR4A's `c1d2e3f4a5b6`.

`flask --app starter db check` **cannot be run authoritatively here**: no local
PostgreSQL is reachable and the repository's schema-drift guard is a
PostgreSQL-16 CI job. Run against SQLite it exits 1 with 80 autogenerate
operations — JSONB columns, `ondelete` cascades and index reflection that SQLite
cannot represent. That is an environment artifact, and it was proven to be one
rather than asserted: the identical command on **unmodified `origin/main`**
produces the *same* 80 operations, and after normalizing memory addresses the
two operation lists are equal element for element. **PR4 contributes zero schema
drift**, which follows independently from its touching neither `app/models.py`
nor `migrations/`.

### Canonical exercise authority re-proven on the integrated branch

| Invariant | Where it is enforced | Status |
| --- | --- | --- |
| One canonical authority | `app/services/exercise_catalog.py` + `training_assets/exercises.json`; the only `load_exercise_catalog()` callers are the generation resolver and `plan_mutation/document.py` | holds |
| Provider names are not identity | `_resolve_generated_name` resolves exact canonical name/declared alias only | holds |
| Stable IDs authoritative | `ID_PATTERN` + `catalog.by_id`; `canonicalize_plan_exercises` always overwrites `exercise_id` | holds |
| Exact names resolve | `normalize_exercise_lookup` (NFKC, hyphen unification, casefold, whitespace collapse) | holds |
| Aliases resolve deterministically | alias index built at load; a normalized collision is a `CatalogConfigurationError` at load time, so ambiguity cannot exist at runtime | holds |
| Unknown fails closed | `ExerciseUnresolved` → `GenerationExerciseUnresolvedError` / `InvalidMutation` | holds |
| Ambiguous fails closed | `len(matches) != 1` → `ExerciseAmbiguous`; `_find_exercise_index` refuses two matches rather than picking one | holds |
| No dynamic catalog entries | catalog module imports no `flask`/`sqlalchemy`/`app.*`; guarded by `test_architecture_catalog_never_persists` | holds |
| No fuzzy authorization | `FORBIDDEN_RESOLUTION_TECHNIQUES` scan over dynamically derived sources | holds |
| Client cannot override metadata | `_forbid_unknown` rebuilds a closed-key dict; `isim` is replaced by `resolved.canonical_name` unconditionally | holds |
| Equipment compatibility server-owned | `is_exercise_compatible` + `check_placement` against the HMAC-verified context | holds |

### PR3 invariants re-proven

`MAX_PROVIDER_COMPLETIONS == 2`, enforced by `_CompletionBudget.complete`, which
raises once `len(self.calls) >= max_calls` — a ceiling, not a convention.
Exactly one repair: a single `except (ParseFailedError, TruncatedError)` block
with one retry whose own handler re-raises. `SchemaInvalidError` and
`SemanticInvalidError` are logged `repair_eligible=0` and re-raised without a
provider call. Strict JSON extraction, structural validation and semantic
validation are unchanged. `canonicalize_plan_exercises` runs strictly **outside**
that try/except, so an exercise-authority failure can never be laundered into a
repairable parse failure — pinned by
`test_canonicalization_runs_after_the_full_try_except_not_inside_repair`.
Provider and model selection are untouched: PR4's diff against `7a5b2a7` does
not reach `ai_coach.py`, `ai_stream.py`, `bedrock_client.py` or `config.py`.

### PR2 invariants re-proven

Unknown program style raises `PreferenceContractError.invalid("UNKNOWN_PROGRAM_STYLE")`
and never becomes `general_fitness`. `parse_preferences` and `require_supported`
both run before `_CompletionBudget` is constructed, so a rejected request costs
**zero provider calls** structurally, not by assertion. Supported preferences
survive canonical normalization. 147 lines of PR4 additions to
`tests/test_sprint11_training_preference_contract.py` still pass.

### Save safety re-proven

There are exactly **three** paths that can write `TrainingPlan.plan_data`, and
all three are covered:

1. `POST /training-plan/save` — `resolve_save_exercise_context` (HMAC, bound to
   `user_id`) → `validate_plan_for_save` (structure → semantics → catalog
   identity → equipment compatibility) → **only then**
   `TrainingPlan.query…delete()`. The ordering is asserted from the AST by
   `test_architecture_save_validates_before_delete`. What is persisted is
   `canonical["program"]`, not the client's payload, and the `exercise_context`
   block is rebuilt from the *verified* context — a tampered display name is
   overwritten with `resolved.canonical_name`, and a fabricated `exercise_id` is
   re-resolved against the live catalog before it means anything.
2. `plan_mutation/service.py:295` (apply) — `after_snapshot` comes from
   `apply_command`, which runs PR4's `_exercise_authority`,
   `_resolve_placeable_exercise` and `check_placement`.
3. `plan_mutation/service.py:407` (undo) — restores the journal's
   `before_snapshot` byte for byte. No new identity is authored.

`app/blueprints/nutrition/plan.py` writes `NutritionPlan`, a different model.
No fourth path exists. Catalog failure codes are deliberately collapsed at the
save boundary so the client cannot use it as a catalog oracle.

### Main's new confirmation surface is under PR4's authority

This was the integration's real risk: `5d898b2` turned a plan mutation into
*propose now, apply later*, and a durable proposal that stored a pre-computed
document would have escaped the catalog. It does not. `encode_command` stores
only the bounded typed-command fields (`day`, `exercise`, `replacement`,
`sets`, `reps`) — never a plan document. Every execution path re-enters the
authority:

- `preview_command` → `apply_command` (PR4 authority);
- `session_impact_facts` → `apply_command` (PR4 authority), read-only;
- `_apply_confirmed` → `apply_plan_mutation` → `apply_command` (PR4 authority).

So an entry retired from the catalog between proposal and confirmation fails at
confirmation. `binding_matches` additionally requires lineage, mutation version
and `snapshot_fingerprint` to be unchanged, and `snapshot_fingerprint` covers
the stored `exercise_context` block — a context change makes the proposal
`STALE` rather than applicable. `_resolved_replay_result` returns journal
evidence and performs no mutation call.

Adaptive Coaching undo semantics are unchanged: PR4's diff against `7a5b2a7`
touches only `document.py`, `errors.py` and `validation.py` inside
`plan_mutation/`, and reaches neither `journal.py` nor `service.py`.

### No duplicate exercise authority

`app/services/training_generation/exercise_knowledge_base.py` remains deleted.
No module in the integrated tree holds a second exercise identity map: main's
new `coach_plan_policy/`, `plan_confirmation/`, `proposals.py` and `results.py`
carry exercise *names* as command fields and presentation strings, never a
catalog. The remaining hardcoded exercise-name lists are
`app/services/injury_constraints.py` (P2-16, below) and the movement-pattern
names in `contradiction_engine` / `feature_extractor` / `rule_engine` /
`scoring_engine`, which are `MOVEMENT_VOCABULARY` values, not exercise
identities. `WorkoutLog` stays name-only — the explicitly documented,
namespace-free scope boundary from the design's §9, not a second authority.

### Architecture mutation battery (non-vacuity, integrated SHA)

Re-proven at `37d2829`. The driver is preserved this time
(`scratchpad/mutation_battery.py`) and each case is reproducible from the table
below: it backs up the file, applies the mutation, runs the guard, restores in a
`finally`, and asserts `git status` is clean at the end (it was).

| # | Mutation | Guard required to fire | Result |
| --- | --- | --- | --- |
| 1 | Delete the `canonicalize_plan_exercises(...)` call site in `service.py` | `test_canonicalization_runs_after_the_full_try_except_not_inside_repair`, `test_generated_exercise_id_is_accepted_by_save` | 2 failed → **detected** |
| 2 | `_resolve_generated_name` fabricates an `ExerciseDefinition` instead of raising | `test_semantically_distinct_name_is_not_fuzzy_matched`, `test_http_typed_exercise_unresolved` | 1 failed → **detected** |
| 3 | `difflib.get_close_matches` fallback added to the resolver | `test_no_legacy_exercise_kb_or_fuzzy_persistence_path` | 1 failed → **detected** |
| 4 | Move `TrainingPlan…delete()` above the validation block | `test_architecture_save_validates_before_delete` | 1 failed → **detected** |
| 5 | Add a **new** module `training_generation/exercise_matcher.py` with a fuzzy `match()` | `test_no_legacy_exercise_kb_or_fuzzy_persistence_path` | 1 failed → **detected** |
| 6 | Drop the `is_exercise_compatible` gate in `canonicalize_plan_exercises` | `test_equipment_incompatible_generated_exercise_is_typed`, `test_generation_pipeline_rejects_incompatible_equipment` | 2 failed → **detected** |

**6 / 6 mutations detected.** Case 5 is the one that matters most for §14's
"must derive files dynamically": the guard's file set comes from
`GENERATION_PACKAGE.rglob("*.py")` with a ten-module floor
(`REQUIRED_SCANNED_MODULES`), so a brand-new module cannot evade it and a glob
that stopped matching cannot make it vacuous.

An honest note on method: case 1 initially reported *not detected* because its
first two targets (`test_pr3_valid_aliases_become_canonical_ids`,
`test_duplicate_exercise_references_resolve_to_the_same_stable_id`) call
`canonicalize_plan_exercises` **directly** and therefore never traverse the call
site the mutation removes. That was a bad target choice, not a vacuous guard;
retargeting to the two tests that do traverse it made the mutation fire. It is
recorded here because a battery that quietly re-picks its targets until they go
green proves nothing.

### Focused validation

27 suites covering PR2, PR3, PR4 and every adjacent system the drift review
flagged — preference contract, capability matrix and zero-provider-call
rejections; parser/schema, semantic validation, repair bounds, truncation,
provider-call bounds and save-time validation; the exercise catalog, stable IDs,
aliases, normalization, the resolver, unknown/ambiguous failure, equipment
compatibility, legacy compatibility, save-time exercise authority and the
architecture guards; plus Adaptive Coaching (including main's new
confirmation/policy suites), workout logging/history, plan-mutation history and
the migration graph.

```
python -m pytest -q <27 suites>
  1 failed, 1143 passed, 3514 warnings in 392.33s (0:06:32)
```

The single failure was
`test_migration_graph.py::test_pr4_canonical_exercise_authority_adds_no_migration`
— the hard-coded `36` described above. It is a genuine latest-main integration
regression, reproduced independently, and fixed in `37d2829` rather than
deleted. After the fix, `tests/test_migration_graph.py` is 10 passed.

### Full suite

```
python -m pytest -q            # pytest.ini: addopts = -m "not load"
  4900 passed, 12 skipped, 3 deselected, 0 failed, 0 errors, 16352 warnings in 1727.46s (0:28:47)
  exit code 0
```

Zero failures and zero errors. Against the pre-integration baseline (4836
passed / 11 skipped / 3 deselected at `ba4a7ea`) the suite gained **64 tests**
and one skip - main's `#224` confirmation suites
(`test_coach_plan_confirmation.py`, `test_coach_plan_policy.py`,
`test_plan_confirmation.py`, `test_plan_confirmation_parser.py`,
`test_plan_confirmation_pg.py`), whose PostgreSQL race case skips without
`FITX_PG_CONCURRENCY_TEST`. The 3 deselections are `pytest.ini`'s
`addopts = -m "not load"`, so this run is the authoritative full non-load
suite. Warnings are the repository's existing `datetime.utcnow()` noise.

### Exact-SHA validation

Everything below was rerun on the committed final SHA **`37d2829`**, after
the integration fixes, with a clean working tree.

```
git rev-parse HEAD                    -> 37d2829b195a219ef47ebc2110242c4f3a4e54ad
git status --porcelain -uall          -> clean
git rev-list --left-right --count       -> 0 behind, 13 ahead of origin/main

python -m compileall -q app tests      -> exit 0

python -m pytest -q <27 focused suites>
  1144 passed, 0 failed, 3514 warnings in 383.33s (0:06:23)   exit 0
    - PR2 zero-provider-call guards ....... included
    - PR3 repair / provider-call bounds ... included
    - PR4 authority + resolver suite ...... included
    - invalid-save / no-mutation tests .... included
    - architecture guard tests ............ included

architecture mutation battery          -> 6/6 detected, tree clean

flask --app starter db heads           -> c2d3e4f5a6b7 (head)      exit 0
flask --app starter db upgrade (fresh) -> 37 revisions applied     exit 0
flask --app starter db current         -> c2d3e4f5a6b7 (head)
flask --app starter db check           -> PostgreSQL CI gate; see Migration impact

git diff --check                       -> clean (exit 0)
git diff --check origin/main...HEAD    -> 7 lines / 4 findings (exit 2)
conflict-marker scan (tracked files)   -> none
```

### Fresh independent integration review of `origin/main...HEAD`

One new review, scoped to latest-main interaction rather than re-reviewing
already-accepted PR4 internals.

**P0: 0. P1: 0.**

Two defects were found, both introduced by the auto-merge and both **fixed** in
`37d2829` (they are not carried as accepted P2s):

| ID | Finding | Severity | Disposition |
| --- | --- | --- | --- |
| I-1 | `test_no_tool_offers_a_generic_mutation_language` silently narrowed from main's `_ALL_PLAN_TOOL_DEFS` back to `PLAN_MUTATION_TOOL_DEFS`, dropping main's new confirmation tools from that guard | P2 (test coverage; no production behaviour changed, and the widened guard passes) | fixed |
| I-2 | `test_pr4_canonical_exercise_authority_adds_no_migration` pinned `len(revision_files) == 36`; main's `c2d3e4f5a6b7` made it 37 | P2 (brittle baseline literal) | fixed |

Screened and clean: duplicate exercise authority, unstable exercise identity,
alias ambiguity, unsafe normalization, fuzzy authorization, unknown-name bypass,
equipment-compatibility bypass, client tampering, save-validation bypass,
destructive save ordering, provider-call amplification, repair-loop regression,
migration drift, legacy-plan breakage, workout-logging breakage, Adaptive
Coaching coupling, and N+1 resolution.

**P2-16 disposition — unchanged, still routed to PR5.** The escalation test was
run rather than assumed. `injury_constraints.py` is touched by neither side of
the integration. `find_contraindicated(ex["isim"], injuries)` still runs in
`response_validator.py:224`, i.e. before `canonicalize_plan_exercises` in
`service.py`, so aliases of the same lift can still attach different warnings.
It stays P2 because `injury_warnings` has exactly three references in the whole
tree — two assignments in `service.py` and one payload key — and gates nothing;
main's new confirmation surface never invokes injury screening at all, so the
integration does not amplify it. Per the brief it is not pulled into PR4.

The thirteen previously accepted P2s are unchanged.

### `git diff --check`

Not clean, and reported exactly rather than silently "fixed".

```
git diff --check origin/main...HEAD          → exit 2, 7 lines / 4 findings
  docs/superpowers/plans/…-authority.md:814   new blank line at EOF
  docs/superpowers/specs/…-design.md:3        trailing whitespace   "Date: 2026-08-20  "
  docs/superpowers/specs/…-design.md:4        trailing whitespace   "Status: approved design; implementation pending  "
  docs/superpowers/specs/…-design.md:5        trailing whitespace   "Repository: `yusufbesirarslan/fitness-coach`  "
git diff --check                (worktree)   → clean
```

One clarification to the previous disclosure. `git diff --check` prints **7
lines**, which is the number the earlier report and the brief quote — but those
7 lines describe **4 findings**, because each trailing-whitespace finding is
followed by the offending line itself. And only **3** of the 4 are Markdown hard
breaks; the fourth is a trailing blank line at end of file, which renders
identically either way, so "seven Markdown hard-break lines" overstates both the
count and the kind. All four are in design/plan documentation; no production,
test, template, locale or static file is flagged.

**CI does not gate on this.** `.github/workflows/ci.yml` has exactly three jobs
— `pytest`, `schema-drift guard` (`flask db upgrade` + `flask db check`) and
`PostgreSQL concurrency` — and `git diff --check` appears nowhere in `.github/`.
Per the brief's §4 condition, the hard breaks are therefore left intact and
disclosed rather than rewritten to `<br>`: stripping the three would collapse
`Date:` / `Status:` / `Repository:` onto one rendered line.

### Final repository state after integration

| Field | Value |
| --- | --- |
| Repository | `yusufbesirarslan/fitness-coach` |
| Worktree | `C:\Users\yusuf\fitness-coach\.worktrees\sprint11-pr4-canonical-exercise-authority` |
| Branch | `sprint11-pr4-canonical-exercise-authority` (local only) |
| Previous local HEAD | `c129606` (12 ahead, 11 behind) |
| Integrated `origin/main` | `7a5b2a7cd4dacd782f7932760e151037ee1b4662` |
| Integration strategy | rebase onto `origin/main`, zero conflicts |
| Rebased HEAD | `7990b53` |
| Integration fix commit | `37d2829` |
| **Final HEAD** | **``37d2829b195a219ef47ebc2110242c4f3a4e54ad` - the final **code** SHA, where every result above was produced. Everything committed above it is this report; `git diff --stat 37d2829..HEAD` touches this file and nothing else.`** |
| Position vs `origin/main` | **13 ahead, 0 behind** |
| Diff vs `origin/main` | 45 files, +7953 / −217 |
| Working tree | clean (`-uall`) |
| Backup of pre-integration HEAD | tag `pr4-preintegration-backup` → `c129606` |
| Alembic | single head `c2d3e4f5a6b7`, 37 revisions, PR4 adds none |
| Focused suites | 1144 passed, 0 failed (6m23s) |
| Full suite | 4900 passed, 12 skipped, 3 deselected, 0 failed, 0 errors, 16352 warnings in 1727.46s (0:28:47) |
| Mutation battery | 6 / 6 detected |
| `compileall` | exit 0 |
| P0 | 0 |
| P1 | 0 |
| P2 | 13 accepted (unchanged) + 2 integration defects found and fixed |
| Provider-call max | 2 (unchanged) |
| Repair max | 1 (unchanged) |
| Push status | not pushed |
| PR status | none opened |
| Merge status | not merged |
| Deploy status | not deployed; no flag changed |
| PR5 | not started |

### Answers to the latest-main integration questions

**1. Latest integrated `origin/main` SHA?** `7a5b2a7cd4dacd782f7932760e151037ee1b4662`.

**2. Final PR4 HEAD SHA?** ``37d2829b195a219ef47ebc2110242c4f3a4e54ad` - the final **code** SHA, where every result above was produced. Everything committed above it is this report; `git diff --stat 37d2829..HEAD` touches this file and nothing else.`.

**3. Ahead/behind?** 13 ahead, 0 behind.

**4. Integration strategy?** Rebase onto `origin/main`. The branch is local-only
so nothing published was rewritten, and recent `main` is squash-merged linear.

**5. Conflicts?** None raised by git. Verification of the replay found two
defects the clean merge hid; both fixed in `37d2829`.

**6. Were production semantics changed during conflict resolution?** No. Both
fixes are in test files; no production file was touched during integration.

**7. Canonical exercise authority?** `app/services/exercise_catalog.py` over the
version-controlled asset `app/services/training_assets/exercises.json`. One
logical domain; `exercise_knowledge_base.py` stays deleted and no equivalent
returned.

**8. Can arbitrary provider exercise names persist?** No. Only an exact
normalized canonical name or a declared alias resolves, and the persisted `isim`
is always overwritten with `resolved.canonical_name`.

**9. Can fuzzy matching authorize persistence?** No. No fuzzy library or
technique exists in the derived source set, and the guard fires when one is
introduced (battery cases 3 and 5).

**10. What happens to unknown exercises?** Fail closed —
`GenerationExerciseUnresolvedError` on generation/save, `InvalidMutation` on the
Coach path. No substitution, no repair, no dynamic catalog entry.

**11. How is equipment compatibility enforced?** Server-side only, by
`is_exercise_compatible` plus `check_placement`, against the HMAC-signed
`ExerciseContext` bound to the user id. The client supplies a token, never a
context.

**12. Can a client-tampered exercise payload bypass save validation?** No. Keys
are closed and the dict is rebuilt; a fabricated `exercise_id` is re-resolved;
a fabricated display name is discarded; the equipment context comes from the
verified token.

**13. Can an invalid save alter the current plan?** No. Every validator runs
before `TrainingPlan.query…delete()`, asserted from the AST and covered by
`test_invalid_exercise_save_does_not_delete_existing_plan`.

**14. Can any persistence path bypass canonical validation?** No. Exactly three
paths write `TrainingPlan.plan_data` — save route, mutation apply, undo restore
— and all three are accounted for. Main's new propose→confirm flow re-enters
`apply_command` at execution time; it stores typed command fields, never a
document.

**15. Final maximum provider-call count?** 2, unchanged.

**16. Final maximum repair count?** 1, unchanged, parse/truncation only.

**17. Did provider/model selection change?** No. PR4 does not touch
`ai_coach.py`, `ai_stream.py`, `bedrock_client.py` or `config.py`.

**18. Did Adaptive Coaching undo semantics change?** No. PR4 reaches neither
`plan_mutation/journal.py` nor `plan_mutation/service.py`; undo still restores
the prior snapshot byte for byte.

**19. Are legacy plans readable?** Yes. Legacy name-only documents keep loading
through the presenter, are never silently upgraded, and an ambiguous legacy name
is refused rather than backfilled.

**20. Is the migration graph clean?** Yes. Single head `c2d3e4f5a6b7`, 37
revisions, both fresh and incremental upgrade paths exit 0, and PR4 adds no
migration. `flask db check` is a PostgreSQL CI gate; under SQLite it emits the
same 80 reflection artifacts on unmodified `origin/main`, so PR4's schema drift
is provably zero.

**21. P0 / P1 / P2 counts?** P0 = 0, P1 = 0. P2 = 13 accepted (unchanged), plus
2 integration defects found and fixed.

**22. What happened to P2-16?** Unchanged and still routed to PR5. Verified
warn-only after integration; `injury_warnings` gates nothing and main's
confirmation surface never invokes injury screening.

**23. Is `git diff --check` clean?** No — it exits 2 with 7 output lines
describing 4 findings, all in two design/plan Markdown documents: 3 intentional
hard breaks plus 1 blank line at EOF. CI does not run `git diff --check`
anywhere, so per the brief they are disclosed rather than rewritten. The
worktree itself is clean.

**24. What remains for PR5?** Unchanged from the previous report — catalog
coverage, identity-keyed injury screening (P2-16), the Coach alias-wording
product decision (P2-11), and the legacy `WorkoutLog` identity gap. The one item
that section listed as pending is now **done**: PR4's authority over main's new
confirmation surface is proven above.

## Final verdict after latest-main integration

**READY TO SHIP.**

Every gate in the acceptance criteria is met on the integrated SHA:
integrated with latest `origin/main` (`7a5b2a7`) at 0 behind; no unresolved
merge conflict; full suite green (4900 passed, 0 failed); focused suites green
(1144 passed, 0 failed); exact-SHA critical validation green; P0 = 0; P1 = 0;
one canonical exercise authority; arbitrary provider names cannot persist;
unknown and ambiguous exercises fail closed; equipment compatibility is
enforced server-side under a signed context; no save-path bypass; an invalid
save cannot mutate the current plan; PR2's pre-provider guards are preserved;
PR3's provider-call ceiling stays 2 and its repair ceiling stays 1; the
migration graph is a single head with PR4 adding nothing; legacy plans remain
readable; and Adaptive Coaching undo semantics are unchanged.

The only non-green signal is `git diff --check` on the branch range, which is
disclosed above: 4 findings across 7 output lines, all in two design/plan
Markdown documents, and not gated by any CI job. It is a documentation-formatting disclosure, not a
ship blocker.

The branch remains local: not pushed, no pull request, not merged, not
deployed, no feature flag changed, PR5 not started.
