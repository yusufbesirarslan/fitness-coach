# Sprint 12 PR2B — Independent Review

Canonical injury annotation ordering.

- **Reviewer:** Senior Code Reviewer (read-only)
- **Date:** 2026-08-24
- **Worktree:** `C:\Users\yusuf\develop\fitness-coach\.worktrees\sprint12-pr2b-canonical-injury-annotation-ordering`
- **Branch:** `sprint12-pr2b-canonical-injury-annotation-ordering`
- **Base:** `59cff9d54a059f9456f2b83a513784db21eef859` (`origin/main`)
- **HEAD:** still at base; working tree uncommitted (tracked edits + one untracked test module)
- **Plan:** `C:\Users\yusuf\OneDrive\Masaüstü\cf-sprint12-pr2b.txt`

This review inspected `git diff` against the base SHA plus the untracked proving tests. It did not mutate the index, HEAD, or branch, and it did not re-execute pytest.

---

### Strengths

The defect is closed on the production generate path, and the change stays inside the planned box.

1. **Correct new call order.** Generation is now:

   `extract` → `validate_generated_plan` (shape + semantics only) → `canonicalize_plan_exercises` (outside the parse/truncation repair boundary) → `annotate_injuries` → payload.

   That matches the plan’s preferred pipeline. Canonicalization was not pulled into repair. Annotation was pulled *out* of structural validation.

2. **Canonicalization remains the identity writer.** `canonicalize_plan_exercises` still overwrites `isim` with `resolved.canonical_name` and writes `exercise_id`. After that point the matcher sees catalog-owned display names, not provider aliases. The previously observed pair is still real in `app/services/training_assets/exercises.json`:

   `"Squat"` / `"Barbell Squat"` → `ex_barbell_back_squat` / `"Barbell Back Squat"`.

3. **Warn-only contract is intact.** `annotate_injuries` still only prepends `not` and appends to `injury_warnings`. It does not reject generation, delete/substitute exercises, or mutate `set` / `tekrar` / `dinlenme`. `injury_constraints.py` is untouched. No diagnosis, severity, readiness, or contraindication engine was added.

4. **Unknown/ambiguous still fail closed before persistence.** Canonical resolution still raises typed generation errors. Annotation cannot run on an unresolved name because canonicalize never returns. The new tests cover unresolved-before-annotate, HTTP non-persistence, and ambiguous-not-as-tiebreaker.

5. **Save validation was not weakened.** `validate_plan_for_save` still does structure → semantics → catalog identity/equipment. It does not call `annotate_injuries`. That is Model A from the plan (generation owns the overlay; save re-validates the already-canonical annotated payload and preserves `not`). Documented in `docs/TRAINING_GENERATOR.md`.

6. **Regression coverage is behavioral, not only line-order.** Alias-equivalent warning, alias-equivalent no-warning, deadlift survival, generate+save persistence, stored `plan_data` equality, provider-call count `== 1`, warn-only field stability, and an `exercise_id` gate on the matcher are all present in `tests/test_sprint12_pr2b_canonical_injury_annotation.py`. Existing HTTP characterization was updated so `"Conventional Deadlift"` warnings now assert the canonical name `"Barbell Deadlift"`.

7. **Scope stayed narrow.** No mobile, no `/api/v1/today`, no Adaptive Coaching redesign, no migration, no catalog expansion, no extra provider call, no new logging of injuries/plans.

---

### Issues

#### Critical (Must Fix) / P0

None.

#### Important (Should Fix) / P1

None confirmed against the production generate/save path.

The original P2-16 failure mode — two aliases of one catalog entry persisting different `not` text solely because of provider spelling — is not reachable after `canonicalize_plan_exercises` overwrites `isim` and `annotate_injuries` runs on that result.

#### Minor (Nice to Have) / P2

**P2-1 — Matcher is still canonical-*name* substring matching, not identity matching.**

`annotate_injuries` requires a non-empty `exercise_id` string, then calls `find_contraindicated(ex["isim"], injuries)`. The ID is a presence gate, not the matching key. Plan §11 explicitly allows this (“canonical identity → canonical server-owned name → existing warning matcher”). On the generate path it is sufficient because canonicalize already replaced `isim`.

Residual: a future caller that supplies any non-empty `exercise_id` plus a raw alias in `isim` would recreate alias-dependent `neden` / `not` text without failing the gate. The architecture test only covers the no-ID case.

Accepted: do not build an injury-specific second catalog or movement ontology in this PR.

**P2-2 — Identity gate does not verify that `isim` matches the catalog name for that ID.**

Any non-empty string ID satisfies the `TypeError` check. Production canonicalize writes a real catalog ID and the matching name together, so this is not a current persist path. Stronger signature (canonical exercise object / ID looked up to catalog name before matching) was the plan’s “best” option and was not taken.

**P2-3 — Dead `injuries` argument on `validate_generated_plan`.**

The function still accepts `injuries=` and `_parse_and_validate` still forwards `preferences.injuries`, but the value is ignored and the function always returns `[]`. Documented as call-site compatibility. Footgun if a later caller assumes this helper still annotates.

**P2-4 — `TypeError` is not a typed generation error.**

A pipeline bug that reached annotation without IDs would miss `GenerationOutputError` handling in `app/blueprints/training.py` and surface as generic `CODE_GENERATION_UNAVAILABLE` 500. Fail-closed, but noisier than the exercise-authority errors. Only reachable if canonicalize is skipped or stops writing IDs.

**P2-5 — Adaptive Coaching still does not re-derive warnings (pre-existing, in scope to report only).**

`plan_mutation/document.py` `_apply_replace` inherits `not` onto the replacement slot. `_apply_add` writes name/sets/reps(/id) with no overlay. Mutation therefore cannot refresh injury notes after an exercise swap. PR2B did not touch this path and must not redesign it. Future work if Daily Coach starts editing plans that already carry durable `not` warnings.

**P2-6 — No exact-once assertion; discovery test pins the weaker matcher.**

Alias tests assert equal `not` text and `startswith(WARNING_PREFIX)`, not that the prefix appears once. Save correctly does *not* re-annotate, so generate+save does not double-prepend today.

`tests/test_sprint12_daily_coach_discovery.py` now requires `find_contraindicated(ex["isim"], injuries)` in `response_validator.py`. Updating the discovery pin to “P2-16 CLOSED” is right; locking the overlay to display-name matching will fail a later ID-keyed improvement.

**P2-7 — Docs slightly overclaim identity consumption.**

`docs/TRAINING_GENERATOR.md` says annotation runs “on the catalog-owned `exercise_id` + canonical `isim`.” Runtime matching consumes `isim` after an ID presence check. True on the generate path; not literally ID-keyed.

**P2-8 — Process residuals (not product defects).**

- Proving tests live in an **untracked** file. Any commit of only the tracked diff would ship the fix without the alias-equivalence suite.
- Plan §28 non-vacuity mutation proof is not recorded in the tree (expected if it was reverted; it is not evidenced here).
- Working tree is uncommitted; this review did not re-run the full backend suite.

---

### Recommendations

1. Commit the untracked proving module together with the service/docs/test edits. Do not land the reorder without `tests/test_sprint12_pr2b_canonical_injury_annotation.py`.
2. Accept P2-1/P2-2 as the §11 “acceptable” matcher. If a later PR identity-keys warnings, drop or rewrite the discovery assertion that the matcher must call `find_contraindicated(ex["isim"], injuries)`.
3. Optionally delete the ignored `injuries` argument (or stop forwarding it from `_parse_and_validate`) in a tiny follow-up so `validate_generated_plan` cannot be mistaken for the overlay.
4. Leave Adaptive Coaching mutation notes as a documented PR3/later concern: replace inherits `not`; add does not annotate.
5. Record the §28 mutation proof in the PR2B final report (what was inverted, which test failed, revert). This review could not see that evidence.
6. Keep historical `plan_data.not` as-is. No backfill.

---

### Assessment

**Ready to merge?** Yes

The bounded backend fix is correct. Alias-equivalent provider names that resolve to the same catalog exercise can no longer persist different injury warnings *because of spelling*, on the generate → save path. Warn-only semantics, fail-closed unknown/ambiguous resolution, save-time identity validation, provider budget, Adaptive Coaching, mobile, Today, and schema are unchanged.

Merge still depends on committing the untracked tests and on the implementation author’s already-required full-suite evidence. This independent review did not re-run pytest.

Accepted P2s (P2-1 through P2-8) do not reopen P2-16 on the production path.

---

### Checklist answers (plan §29 / §37)

**Can raw provider spelling still affect persisted warning content after resolution?**

No on the production generate/save path. `canonicalize_plan_exercises` replaces `isim` with the catalog canonical name before `annotate_injuries` runs; save re-canonicalizes identity and preserves that `not`. A raw alias without `exercise_id` cannot reach the matcher (`TypeError`). Residual only if a future caller passes a dummy ID with a stale `isim` (P2-1/P2-2).

**Is there a second injury annotation authority?**

No second writer of persisted `not` / `injury_warnings`. The only overlay call site is `generate_training_plan_payload` → `annotate_injuries` → `find_contraindicated`. `build_injury_directive` remains prompt-only (training generate + coach context). `banned_exercise_names` is unused outside its unit tests. Plan mutation does not annotate (P2-5).

**Did save-time validation change incorrectly?**

No. Save still refuses invalid/unresolved/incompatible exercises before delete/insert. It does not re-derive the overlay (Model A, documented). Client-supplied `not` was already echoed on save; this PR did not newly make warning copy client-owned.

**Did Adaptive Coaching get redesigned?**

No. `plan_mutation`, `coach_plan_tools`, and confirmation policy are untouched.

**Was a migration added?**

No. Diff does not touch `migrations/` or `app/models.py`. Historical rows are not rewritten.

**Are warnings still warn-only?**

Yes. Hits prepend `not` and populate `injury_warnings`. They do not reject, delete, substitute, or change load prescription. They do not claim a diagnosis.

**Old call order:** provider JSON → parse/structure/semantics (`validate_generated_plan` called `annotate_injuries` on raw `isim`) → `canonicalize_plan_exercises` → persist/return.

**New call order:** provider JSON → parse/structure/semantics (no overlay) → `canonicalize_plan_exercises` → `annotate_injuries` (requires `exercise_id`, matches canonical `isim`) → return; save re-validates canonical payload and preserves `not`.

**Which function now owns injury annotation?** `annotate_injuries` in `app/services/training_generation/response_validator.py`, invoked only from `generate_training_plan_payload` after canonicalization.

**What canonical input does it consume?** Non-empty `exercise_id` as a gate; matching input is `ex["isim"]` after canonicalize has written the catalog display name.

**Alias pair used:** `"Squat"` / `"Barbell Squat"` → `ex_barbell_back_squat` / `"Barbell Back Squat"` (shipped catalog, not a test fixture).

**Did Adaptive Coaching behavior change?** No.

**Did mobile / `/api/v1/today` change?** No.

**Provider call count:** no additional `complete()` / LLM path; annotation is local.

---

### Files reviewed

Tracked diff vs `59cff9d54a059f9456f2b83a513784db21eef859`:

- `app/services/training_generation/service.py`
- `app/services/training_generation/response_validator.py`
- `app/services/training_generation/__init__.py`
- `docs/TRAINING_GENERATOR.md`
- `tests/test_sprint12_daily_coach_discovery.py`
- `tests/test_training_routes.py`

Untracked (must be committed with the fix):

- `tests/test_sprint12_pr2b_canonical_injury_annotation.py`

Also read, unchanged and relevant: `exercise_resolution.py`, `injury_constraints.py`, `plan_mutation/document.py`, `app/blueprints/training.py`, `exercises.json` aliases for the squat pair.
