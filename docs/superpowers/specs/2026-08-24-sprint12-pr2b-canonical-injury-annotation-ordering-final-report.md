# Sprint 12 PR2B — Canonical Injury Annotation Ordering (Final Report)

- **Status:** recovered from an interrupted agent, completed, validated, reviewed.
- **Date:** 2026-08-24 (recovery pass 2026-08-25)
- **Repository:** `C:\Users\yusuf\develop\fitness-coach`
- **Worktree:** `.worktrees/sprint12-pr2b-canonical-injury-annotation-ordering`
- **Branch:** `sprint12-pr2b-canonical-injury-annotation-ordering`
- **Base SHA:** `59cff9d54a059f9456f2b83a513784db21eef859` (`origin/main`)
- **Closes:** Sprint 11 P2-16

---

## 1. Recovery summary

The previous agent hit its usage limit **after writing the implementation, the
proving tests and an independent review, but before committing anything and
before running a single test.**

State found at takeover:

| Item | Value |
|---|---|
| HEAD | `59cff9d` — identical to `origin/main` |
| Commits for PR2B | **none** |
| Ahead / behind origin/main | 0 / 0 |
| Staged files | none |
| Modified (tracked) | 6 files |
| Untracked | 2 files |
| Reflog | one `reset: moving to HEAD` entry, no lost commits |

All PR2B work existed only as an uncommitted working tree. Nothing was
discarded; the inherited implementation was validated and kept.

## 2. State inherited from the interrupted agent

**Inherited complete (kept unchanged):**

- `app/services/training_generation/service.py` — annotation moved after canonicalization
- `app/services/training_generation/response_validator.py` — annotation removed from `validate_generated_plan`, `exercise_id` gate added
- `app/services/training_generation/__init__.py` — module contract docstring
- `docs/TRAINING_GENERATOR.md` — "Injury annotation (warn-only, after identity)" section
- `tests/test_sprint12_pr2b_canonical_injury_annotation.py` (355 lines, 16 tests, untracked)
- `tests/test_sprint12_daily_coach_discovery.py` — P2-16 pin flipped open → closed
- `tests/test_training_routes.py` — warning name characterization updated
- `docs/superpowers/specs/2026-08-24-sprint12-pr2b-independent-review.md`

**Component classification at takeover:**

| # | Component | State at takeover |
|---|---|---|
| 1 | Old-call-order reproduction | COMPLETE (discovery test existed on main) |
| 2 | Canonicalization-before-annotation change | IMPLEMENTED BUT NOT VERIFIED |
| 3 | Annotation helper input contract | IMPLEMENTED BUT NOT VERIFIED |
| 4 | Canonical ID/name use | IMPLEMENTED BUT NOT VERIFIED |
| 5 | Alias-equivalence warning test | IMPLEMENTED BUT NOT VERIFIED |
| 6 | Alias-equivalence no-warning test | IMPLEMENTED BUT NOT VERIFIED |
| 7 | Persisted `not` consistency test | IMPLEMENTED BUT NOT VERIFIED |
| 8 | Unknown exercise fail-closed | IMPLEMENTED BUT NOT VERIFIED |
| 9 | Ambiguous exercise fail-closed | IMPLEMENTED BUT NOT VERIFIED |
| 10 | Save-time behavior regression | IMPLEMENTED BUT NOT VERIFIED |
| 11 | Provider-call-count regression | IMPLEMENTED BUT NOT VERIFIED |
| 12 | Architecture/order guard | IMPLEMENTED BUT NOT VERIFIED |
| 13 | Mutation / non-vacuity proof | **NOT STARTED** |
| 14 | Focused test run | **NOT STARTED** |
| 15 | Broader Sprint 11 regression run | **NOT STARTED** |
| 16 | Adaptive Coaching compatibility | **NOT STARTED** |
| 17 | Full backend suite | **NOT STARTED** |
| 18 | Compile / static validation | **NOT STARTED** |
| 19 | Migration-head validation | **NOT STARTED** |
| 20 | Independent review | COMPLETE (document present) |
| 21 | Final report | **NOT STARTED** |
| 22 | Final commits | **NOT STARTED** |

**What remained for this pass:** items 13–19, 21, 22 — i.e. every execution
gate plus all commits. No implementation work remained.

**Did this pass change the previous agent's implementation?** No. Not one
production line was altered. The implementation was verified semantically and
then proven by execution. All P2s raised by the inherited review were
re-confirmed as P2 and accepted rather than fixed, to keep the recovery
bounded.

---

## 3. The defect and the fix

**Root cause.** `validate_generated_plan` called `annotate_injuries` on the
structurally-validated but *not yet canonicalized* plan. Catalog resolution
ran afterwards. The injury matcher therefore keyed off the provider's raw
exercise spelling, and `canonicalize_plan_exercises` then overwrote `isim`
with the catalog name — leaving a warning (or the absence of one) that had
been decided by a string the catalog does not own. Because the warning is
written into `not`, and `not` is a persisted schema key, the spelling-dependent
outcome reached `plan_data`.

**Old call order**

```
provider JSON
  -> extract_plan_object
  -> validate_generated_plan
       -> validate_plan_structure
       -> validate_plan_semantics
       -> annotate_injuries(raw provider "isim")     <-- warning decided here
  -> canonicalize_plan_exercises                      <-- identity assigned here
  -> payload / persistence
```

**New call order**

```
provider JSON
  -> extract_plan_object
  -> validate_generated_plan          (shape + semantics only, no overlay)
  -> canonicalize_plan_exercises      (writes exercise_id, rewrites isim)
  -> annotate_injuries                (requires exercise_id; matches canonical isim)
  -> payload
  -> validate_plan_for_save           (re-validates canonical payload, preserves `not`)
  -> persistence
```

Canonicalization stays strictly outside the parse/truncation repair
`try/except`, exactly as Sprint 11 PR4 required; annotation was placed after
it, in the same non-repairable region.

**Which function owns injury annotation now?**
`annotate_injuries` in `app/services/training_generation/response_validator.py`,
invoked from exactly one place: `generate_training_plan_payload`, after
`canonicalize_plan_exercises`.

**What canonical input does it consume?** A non-empty catalog `exercise_id` as
a hard gate (`TypeError` otherwise), then the canonical display name in
`isim` that `canonicalize_plan_exercises` has already written.

**Can raw provider spelling affect persisted warning content?** No, on the
production generate → save path. An entry without `exercise_id` cannot reach
the matcher at all, and every entry that has one has had `isim` replaced by
the catalog name. Save does not re-derive the overlay.

**Canonical authority used:** `app/services/training_generation/exercise_resolution.py`
over `app/services/exercise_catalog` / `app/services/training_assets/exercises.json`.
PR2B added no second authority.

---

## 4. Alias-equivalence proof

**Alias pair (verified against the current catalog on `origin/main`, not a fixture):**

| Provider spelling | Canonical ID | Canonical name |
|---|---|---|
| `Squat` | `ex_barbell_back_squat` | `Barbell Back Squat` |
| `Barbell Squat` | `ex_barbell_back_squat` | `Barbell Back Squat` |

Both aliases resolve to the same canonical ID **and** the same canonical name.

**Why the pair is non-vacuous.** Under the old ordering the two spellings
diverged in the matcher itself:

```
find_contraindicated("Squat",             "menisküs") -> None            (NO warning)
find_contraindicated("Barbell Squat",     "menisküs") -> "barbell squat" (warning)
find_contraindicated("Barbell Back Squat","menisküs") -> "back squat"    (canonical)
```

So the old order gave one alias no warning at all and the other a warning with
a *different* `neden`. After the fix both produce `back squat`.

**Warning-equivalence result:** PASS —
`test_alias_equivalent_exercises_receive_equivalent_injury_warnings`.
Same injury input (`menisküs`) + either alias yields the same `exercise_id`,
the same `isim`, byte-identical `not`, and identical `injury_warnings` entries.

**No-warning equivalence result:** PASS —
`test_alias_equivalent_exercises_remain_warning_free_when_injury_is_irrelevant`.
With `bilek sakatlığı` (irrelevant to a back squat) both aliases stay
warning-free and `injury_warnings == []`. Canonicalization did not degrade
into "always warn".

**Persisted `not` proof:** PASS, at two boundaries.

- `test_alias_equivalent_warnings_persist_through_canonical_save` — the
  document returned by `validate_plan_for_save` is identical across aliases for
  `exercise_id`, `isim`, `not`, `set`, `tekrar`, `dinlenme`, and the warning
  prefix occurs exactly once.
- `test_alias_equivalent_warnings_persist_into_stored_plan_data` — end-to-end
  over HTTP (`POST /training-plan` then `POST /training-plan/save`), reading
  the actual `TrainingPlan.plan_data` back out of the database. Stored `not`
  text is equal across aliases.

---

## 5. Semantics preserved

**Warn-only:** yes. `annotate_injuries` only prepends `not` and appends to
`injury_warnings`. It cannot reject, delete, or substitute an exercise, and it
does not touch `set` / `tekrar` / `dinlenme`. `injury_warnings` has no consumer
that blocks — it is a response payload field and nothing else.
`injury_constraints.py` is untouched; no diagnosis, severity, readiness, or
contraindication engine was added.

**Unknown exercises:** unchanged, fail closed. `canonicalize_plan_exercises`
raises `GenerationExerciseUnresolvedError` before annotation can run, so an
unknown name is never annotated and never persisted. Covered by
`test_unresolved_exercise_fails_closed_before_injury_annotation` and, at the
HTTP boundary, `test_unresolved_exercise_with_injuries_does_not_persist`
(500, zero `TrainingPlan` rows).

**Ambiguous exercises:** unchanged, fail closed with
`GenerationExerciseAmbiguousError`. Injury input is not used as a
disambiguation tiebreaker — covered by
`test_ambiguous_exercise_remains_fail_closed_and_is_not_an_injury_tiebreaker`.

**Save behavior:** unchanged. `validate_plan_for_save` still runs
structure → semantics → catalog identity/equipment against the *verified*
exercise context, and does **not** call `annotate_injuries` (Model A:
generation owns the overlay, save re-validates the already-canonical annotated
payload and preserves `not`). Guarded by
`test_save_does_not_re_derive_injury_annotation`.

**Provider-call budget:** unchanged — **0 additional provider/LLM calls.**
Annotation is deterministic and local. No repair call, no second generation
call, no injury-classification AI call.
`test_injury_annotation_does_not_add_provider_calls` asserts exactly one
`chat_fn` invocation on the annotated path.

**Adaptive Coaching compatibility:** not on the changed path, so unchanged.
`app/services/plan_mutation/` and `app/services/coach_plan_tools/` deliberately
do not call `response_validator` (their own docstrings state the generator's
validator "cannot be called directly here"). The only cross-module imports of
`response_validator` elsewhere in `app/` are the unchanged `VALID_TIPS` /
`WEEKDAYS` constants used by `workout_session` and `workout_state`. No
Adaptive Coaching flag, policy, or mutation path was touched.

**Historical data:** not rewritten. No backfill, no reprocessing. Rows written
under the old ordering keep whatever `not` they have. PR2B governs newly
generated plans; the save path naturally re-canonicalizes identity on any plan
a client re-saves, but does not re-derive warnings.

**Migration state:** none added. `migrations/` and `app/models.py` are
untouched; `git status` on both is empty. Single Alembic head confirmed:
`c2d3e4f5a6b7`.

**Out of scope and untouched:** mobile (`axisai_mobile`), `GET /api/v1/today`,
Adaptive Coaching redesign, exercise substitution, fuzzy matching, new catalog
authority, readiness scoring, injury taxonomy.

---

## 6. Validation evidence

All runs on the final production SHA `5c42451`.

### Focused

| Suite | Result |
|---|---|
| `tests/test_sprint12_pr2b_canonical_injury_annotation.py` | **16 passed** |

### Broader regression

| Suite | Result |
|---|---|
| `test_sprint12_daily_coach_discovery.py`, `test_training_routes.py`, `test_training_generation.py`, `test_workout_convergence.py` | **130 passed** |
| `test_sprint11_exercise_authority.py`, `test_sprint11_training_generation_output.py`, `test_sprint11_training_preference_contract.py` | **376 passed** |

### Static / repository validation

| Check | Result |
|---|---|
| `python -m compileall app tests` | exit 0 |
| `git diff --check` | clean, no whitespace errors |
| `git status -- migrations/ app/models.py` | empty |
| Alembic heads | `['c2d3e4f5a6b7']`, count 1 |

### Full backend suite

Run on the final production SHA `5c42451`, after the mutation experiment was
reverted and the tree verified clean. A single `pytest -q` over `tests/` is
known to exceed the practical run window in this repository, so the suite was
run as **8 sequential batches of 29 `tests/test_*.py` files** (215 modules
total, 12 in the last batch). `pytest.ini` `addopts` (`-m "not load"`) applied
to every batch.

| Batch | Files | Result | Time | Exit |
|---|---|---|---|---|
| 1 | 29 | 609 passed | 7:53 | 0 |
| 2 | 29 | 598 passed | 7:25 | 0 |
| 3 | 29 | 457 passed | 9:17 | 0 |
| 4 | 29 | 529 passed, 3 skipped | 6:09 | 0 |
| 5 | 29 | 566 passed, 5 skipped | 5:46 | 0 |
| 6 | 29 | 807 passed | 5:36 | 0 |
| 7 | 29 | 1037 passed | 9:48 | 0 |
| 8 | 12 | 434 passed, 4 skipped | 5:12 | 0 |

**Totals**

| | |
|---|---|
| passed | **5037** |
| failed | **0** |
| skipped | 12 |
| deselected | 0 |
| errors | 0 |
| exit code | **0** (every batch) |

No `FAILED` or `ERROR` line appears anywhere in the run log. Opt-in
`pg_concurrency` tests were not run (they require
`FITX_PG_CONCURRENCY_TEST=1` plus a live `PG_TEST_DATABASE_URL`, and are not a
required gate for this PR — PR2B adds no concurrency surface).

---

## 7. Architecture guard and non-vacuity proof

**Guards present:**

- `test_generation_annotates_after_canonical_exercise_resolution` — AST line
  ordering inside `generate_training_plan_payload`: every
  `canonicalize_plan_exercises` call precedes every `annotate_injuries` call;
  additionally asserts neither appears inside the parse/truncation repair block.
- `test_annotate_injuries_is_not_called_from_structural_validation` — AST proof
  that `validate_generated_plan` no longer calls the overlay.
- `test_raw_provider_name_cannot_reach_the_warning_matcher` — behavioral: a
  structurally-valid but un-canonicalized plan has no `exercise_id`, annotation
  raises, and `not` is left untouched.
- `test_persisted_not_key_remains_the_warning_channel` — pins `not` as the
  persisted warning channel and the `NOTE_MAX` bound.
- `test_injury_annotation_follows_canonical_exercise_resolution`
  (Sprint 12 discovery module) — the P2-16 pin, flipped from open to closed.

**Non-vacuity proof (executed this pass — it had never been run).**

The old ordering was temporarily reintroduced by reverting the production
commit (`git revert --no-commit f66daaf`), the guards were run, and the
mutation was then fully reverted with `git reset --hard HEAD`.

Result: **8 failed, 9 passed.** The failures were not only the AST guards —
the behavioral tests failed too:

```
FAILED test_alias_equivalent_exercises_receive_equivalent_injury_warnings
FAILED test_existing_deadlift_warning_survives_canonicalization
FAILED test_alias_equivalent_warnings_persist_through_canonical_save
FAILED test_alias_equivalent_warnings_persist_into_stored_plan_data
FAILED test_raw_provider_name_cannot_reach_the_warning_matcher
FAILED test_annotate_injuries_is_not_called_from_structural_validation
FAILED test_generation_annotates_after_canonical_exercise_resolution
FAILED test_injury_annotation_follows_canonical_exercise_resolution
```

Headline failure — the P2-16 defect itself, reproduced:

```
>   assert ex["not"].startswith(WARNING_PREFIX)
E   AssertionError: assert False
E    + where False = 'kontrollü'.startswith('⚠️ SAKATLIK RİSKİ')
```

Under the old ordering, alias `"Squat"` with `menisküs` kept its untouched
provider note (`kontrollü`) — **no injury warning at all** — while
`"Barbell Squat"` received one. That is exactly the alias-dependent persisted
state PR2B closes.

No mutation artifact was committed. Working tree verified clean afterwards and
the new ordering confirmed restored.

---

## 8. Independent review

Two reviews exist and agree.

1. The interrupted agent's review,
   `docs/superpowers/specs/2026-08-24-sprint12-pr2b-independent-review.md`
   (kept in place, not duplicated).
2. This pass's fresh review of `origin/main...HEAD`, re-checking each risk:

| Risk | Finding |
|---|---|
| Raw provider spelling still influencing warnings | Not reachable — `exercise_id` gate + canonical `isim` |
| Second warning authority | None — one call site, one writer of `not` / `injury_warnings` |
| Annotation before canonicalization on an alternate path | None — one generation entry point (`blueprints/training.py:200`), two canonicalization sites (generate, save), annotation only after the generate one |
| Warning applied twice | No — save does not re-annotate; prefix count asserted `== 1` |
| Warning lost after canonicalization | No — annotation now runs *after* the rewrite, so it cannot be overwritten |
| Alias-specific persisted state | Closed, proven at helper, save, and HTTP/`plan_data` boundaries |
| Unknown / ambiguous bypass | Closed — canonicalization raises first |
| Save-path inconsistency | None — save unchanged |
| Provider-call regression | None — asserted `== 1` |
| Medical-scope expansion | None — `injury_constraints.py` untouched |
| Unrelated changes | None — 7 files, all in scope |

### Findings

- **P0: 0**
- **P1: 0**
- **P2: 8 — all accepted, none reopening P2-16 on the production path**

| ID | Accepted P2 |
|---|---|
| P2-1 | Matcher is canonical-*name* substring matching; `exercise_id` is a presence gate, not the matching key. Explicitly the allowed design. A future caller passing a dummy ID plus a stale `isim` would not trip the gate. |
| P2-2 | The gate does not verify `isim` matches the catalog name for that ID. Production `canonicalize_plan_exercises` always writes both together. |
| P2-3 | `validate_generated_plan` still accepts an ignored `injuries=` kwarg and `_parse_and_validate` still forwards it. Dead but harmless; removing it would churn ~20 existing call sites for no behavioral gain. |
| P2-4 | The identity gate raises `TypeError`, not a typed `GenerationOutputError`, so a hypothetical pipeline bug would surface as a generic 500. Fail-closed either way; only reachable if canonicalization is skipped. |
| P2-5 | Pre-existing: Adaptive Coaching `_apply_replace` inherits `not` onto the replacement slot and `_apply_add` writes none. Mutation cannot refresh injury notes after a swap. PR2B did not touch this and must not redesign it. |
| P2-6 | The Sprint 12 discovery pin now requires `find_contraindicated(ex["isim"], injuries)`; a later ID-keyed matcher will need that assertion rewritten. |
| P2-7 | `docs/TRAINING_GENERATOR.md` reads slightly stronger than runtime — matching consumes `isim` after an ID presence check rather than being literally ID-keyed. |
| P2-8 | Process residuals from the interruption (untracked tests, missing mutation proof, no commits) — **all resolved by this pass.** |

---

## 9. Files, commits, verdict

**Files changed vs `origin/main` (7):**

```
app/services/training_generation/__init__.py            |   4 +-
app/services/training_generation/response_validator.py  |  22 +-
app/services/training_generation/service.py             |  22 +-
docs/TRAINING_GENERATOR.md                              |  12 +
tests/test_sprint12_daily_coach_discovery.py            |  41 +-
tests/test_sprint12_pr2b_canonical_injury_annotation.py | 355 +++++++++++++
tests/test_training_routes.py                           |   2 +-
```

**Commits:**

| SHA | Message |
|---|---|
| `f66daaf` | `fix(training): canonicalize exercises before injury annotation` |
| `5c42451` | `test(training): guard canonical injury warning equivalence` |
| _(this commit)_ | `docs(sprint12): record PR2B validation` |

The two inherited-work commits were authored fresh in this pass (the previous
agent left everything uncommitted), so nothing was squashed or rewritten.
The docs commit adds this report plus the inherited independent review; it
touches no production or test code, so the full-suite result above still
describes the final production SHA.

**Final HEAD:** see the docs commit above — this report is the last commit on
the branch.
**Working tree:** clean
**Verdict:** **READY TO SHIP**

Implementation complete, all required validation gates executed and green
(focused 16, regression 130 + 376, full suite 5037 passed / 0 failed),
architecture guards green and proven non-vacuous, P0 = 0, P1 = 0, working tree
clean.

Not pushed. No PR opened. No merge. No deploy.

---

## 10. Recommended Sprint 12 PR3 scope

PR2B closed the *ordering* defect. The natural next bounded increment follows
the PR1 discovery
(`2026-08-23-sprint12-pr1-daily-coach-convergence-discovery.md`), whose
sequence is PR2A → PR2B → PR3 → PR4 → PR5.

Recommended PR3 scope:

1. Close **P2-5**, the one residual this PR surfaced and deliberately did not
   fix: Adaptive Coaching plan mutation inherits `not` on replace and writes
   none on add, so an injury warning can survive onto an exercise it was never
   computed for, or vanish when one is added. Re-derive the warn-only overlay
   from canonical identity inside `plan_mutation` after a mutation is applied,
   reusing `annotate_injuries` rather than creating a second authority.
2. Keep it warn-only and warn-only-shaped — no rejection of mutations on
   injury grounds, no substitution, no new provider calls.
3. Do **not** bundle `GET /api/v1/today`; that remains its own PR.

Explicitly still out of scope for PR3: mobile changes, `MOBILE_AUTH_ENABLED`,
Adaptive Coaching flag changes, historical backfill, injury taxonomy, and any
medical/contraindication engine.
