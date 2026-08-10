# Sprint 9 PR2A Phase 2 and Idempotency Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a trustworthy PR2A baseline, correct inherited characterization fixtures without changing production behavior, and prove whether same-key/different-command detection is possible without a schema migration.

**Architecture:** This checkpoint plan changes no production code or schema. It first makes the inherited characterization suite accurately describe `origin/main@78e9d3c`, then executes the complete auditable six-batch baseline, and finally records persistent-idempotency evidence from the existing service, model, and migrations. If the schema is insufficient, execution stops with the exact migration checkpoint the user requested.

**Tech Stack:** Python 3.14, Flask, SQLAlchemy, Alembic/Flask-Migrate, pytest 9, PowerShell, Git.

## Global Constraints

- Work only in `C:\Users\yusuf\fitness-coach\.worktrees\sprint9-pr2a-mobile-food-discovery-logfood` on branch `sprint9-pr2a-mobile-food-discovery-logfood`.
- Preserve all inherited uncommitted work. Do not reset, restore, clean, stash, rebase, or delete it.
- Do not modify Flutter, push, open a PR, merge, deploy, or begin PR2B/PR3.
- Do not change production code or schema in this checkpoint plan.
- Search, serving, barcode, and menu remain discovery-only; `POST /api/v1/nutrition/logs` remains the sole future mobile write authority.
- No migration is approved. If persistent semantic conflict detection cannot be implemented safely with the existing schema, stop after producing the required evidence report.
- Use `python -m pytest -p no:cacheprovider --basetemp=instance/pytest-pr2a` because both the inherited `.pytest_cache` and the global pytest temp root have incompatible ACLs. The `instance/` path is repository-ignored; do not delete either inherited generated directory. Every pytest command below implicitly includes this explicit `--basetemp` argument.
- Report exact test files, passes, skips/deselections, and failures. Never claim PostgreSQL coverage that did not run.
- Do not commit until the complete Phase 2 baseline is trustworthy.

---

### Task 1: Correct and verify the inherited characterization fixtures

**Files:**
- Modify: `tests/test_food_discovery_characterization.py:126`
- Modify: `tests/test_food_discovery_characterization.py:304`

**Interfaces:**
- Consumes: unchanged legacy `_PORTION_WEIGHTS`, `parse_fatsecret_serving`, and `/api/diary/meal` request contract.
- Produces: a 24-case characterization suite that passes against unchanged production code and accurately pins legacy zero/estimate behavior.

- [ ] **Step 1: Confirm production remains untouched and retain the observed RED evidence**

Run:

```powershell
git diff -- app
python -m pytest -p no:cacheprovider tests/test_food_discovery_characterization.py -v
```

Expected: `git diff -- app` is empty; pytest reports exactly the two already-observed fixture failures: `30.0 != 0.0` for the `dilim` case and missing `meal_id` after the invalid `meal_type` request.

- [ ] **Step 2: Replace the matrix-recognized description with a genuinely unknown description**

Change only serving `b` in `test_food_get_servings_substitutes_a_matrix_estimate_for_unknown_mass`:

```python
{"serving_id": "b", "serving_description": "1 mystery portion",
 "metric_serving_amount": None, "metric_serving_unit": None,
 "calories": "0", "protein": "0", "carbohydrate": "0", "fat": "0"},
```

`mystery portion` contains none of the keys in `_PORTION_WEIGHTS`, so the legacy parser deterministically produces `0.0` without altering production behavior.

- [ ] **Step 3: Correct the actual legacy meal-creation field**

Change the setup request in `test_diary_builder_persists_unknown_mass_and_per_100g_as_zero` to:

```python
created = client.post("/api/diary/meal", json={"meal_name": "Kahvaltı"})
```

- [ ] **Step 4: Run the two corrected cases and the entire characterization file**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_food_discovery_characterization.py::test_food_get_servings_substitutes_a_matrix_estimate_for_unknown_mass tests/test_food_discovery_characterization.py::test_diary_builder_persists_unknown_mass_and_per_100g_as_zero -v
python -m pytest -p no:cacheprovider tests/test_food_discovery_characterization.py -v
```

Expected: both focused cases pass; the full file reports `24 passed` with no network calls and no production diff.

- [ ] **Step 5: Audit fixture isolation and the characterization/new-contract boundary**

Run:

```powershell
rg -n "_fs_get|_get_fatsecret_token|db\.session|client\.(get|post)|require_mobile_auth|/api/v1" tests/test_food_discovery_characterization.py
git diff -- tests/test_food_discovery_characterization.py
```

Confirm that every provider call is monkeypatched, persistence is cleaned by the app fixture, legacy browser auth/CSRF is exercised through `AutoOriginClient`, and the file contains no `/api/v1` mobile-contract assertion.

---

### Task 2: Execute and record the complete Phase 2 baseline

**Files:**
- Read: `batches.json`
- Read: `pytest.ini`
- Read: all 151 baseline `tests/**/test_*.py` files selected by `batches.json`
- Modify: none during test execution

**Interfaces:**
- Consumes: the corrected green characterization suite and the six complete/non-overlapping batch lists.
- Produces: exact auditable baseline counts and a trustworthy point from which production extraction may begin after the idempotency checkpoint.

- [ ] **Step 1: Prove batch completeness, uniqueness, and exact repository coverage**

Run:

```powershell
$j = Get-Content -Raw batches.json | ConvertFrom-Json
$all = @($j | ForEach-Object { $_ })
$repo = @(rg --files tests -g 'test_*.py' | ForEach-Object { $_.Replace('\', '/') } | Where-Object { $_ -ne 'tests/test_food_discovery_characterization.py' })
"batches=$($j.Count) total=$($all.Count) unique=$((@($all | Sort-Object -Unique)).Count)"
"repo=$($repo.Count) missing=$((@($repo | Where-Object { $_ -notin $all })).Count) unknown=$((@($all | Where-Object { $_ -notin $repo })).Count)"
```

Expected: `batches=6 total=151 unique=151` and `repo=151 missing=0 unknown=0`.

- [ ] **Step 2: Run baseline batch 1 and record its terminal summary**

Run:

```powershell
$batch = (Get-Content -Raw batches.json | ConvertFrom-Json)[0]
& python -m pytest -p no:cacheprovider $batch -q
```

Expected: exit code 0. Record exact passed/skipped/deselected counts and the 26 file names from `batches.json[0]`.

- [ ] **Step 3: Run baseline batch 2 and record its terminal summary**

Run:

```powershell
$batch = (Get-Content -Raw batches.json | ConvertFrom-Json)[1]
& python -m pytest -p no:cacheprovider $batch -q
```

Expected: exit code 0. Record exact counts and the 26 selected files.

- [ ] **Step 4: Run baseline batch 3 and record its terminal summary**

Run:

```powershell
$batch = (Get-Content -Raw batches.json | ConvertFrom-Json)[2]
& python -m pytest -p no:cacheprovider $batch -q
```

Expected: exit code 0. Record exact counts and the 26 selected files.

- [ ] **Step 5: Run baseline batch 4 and record its terminal summary**

Run:

```powershell
$batch = (Get-Content -Raw batches.json | ConvertFrom-Json)[3]
& python -m pytest -p no:cacheprovider $batch -q
```

Expected: exit code 0. Record exact counts and the 26 selected files.

- [ ] **Step 6: Run baseline batch 5 and record its terminal summary**

Run:

```powershell
$batch = (Get-Content -Raw batches.json | ConvertFrom-Json)[4]
& python -m pytest -p no:cacheprovider $batch -q
```

Expected: exit code 0. Record exact counts and the 26 selected files.

- [ ] **Step 7: Run baseline batch 6 and record its terminal summary**

Run:

```powershell
$batch = (Get-Content -Raw batches.json | ConvertFrom-Json)[5]
& python -m pytest -p no:cacheprovider $batch -q
```

Expected: exit code 0. Record exact counts and the 21 selected files.

- [ ] **Step 8: Run the focused authoritative baseline suites**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_mobile_auth_api.py tests/test_mobile_auth_feature_gate.py tests/test_mobile_auth_service.py tests/test_mobile_nutrition_api.py tests/test_fatsecret.py tests/test_fatsecret_lookup.py tests/test_food_routes.py tests/test_food_relevance.py tests/test_barcode.py tests/test_barcode_workflow.py tests/test_nutrition_routes.py tests/test_nutrition_pipeline.py tests/test_timeutil.py tests/test_ownership.py tests/test_migration_graph.py tests/test_sprint6_migration_golden.py tests/test_menu_fetch.py tests/test_menu_routes.py tests/test_menu_url_cache.py -q
```

Expected: exit code 0. Record the exact summary. This focused run intentionally overlaps the complete batches and is reported separately rather than added to the unique baseline total.

- [ ] **Step 9: Check PostgreSQL concurrency availability without fabricating coverage**

Run:

```powershell
if ($env:FITX_PG_CONCURRENCY_TEST -eq '1' -and $env:PG_TEST_DATABASE_URL) {
  python -m pytest -p no:cacheprovider -m pg_concurrency tests/test_mobile_auth_pg.py tests/test_workout_completion_pg.py tests/test_workout_session_pg.py -q
} else {
  Write-Output 'POSTGRESQL CONCURRENCY NOT RUN: FITX_PG_CONCURRENCY_TEST/PG_TEST_DATABASE_URL unavailable'
}
```

Expected: either exact PostgreSQL results or the explicit not-run line; never infer a pass.

- [ ] **Step 10: Inspect the complete baseline diff before the first commit**

Run:

```powershell
git status --short
git diff -- tests/test_food_discovery_characterization.py
git diff --check
git diff --cached --check
```

Expected: only inherited `batches.json`, the corrected characterization test, and approved spec/plan documents are untracked; no production file is modified and both diff checks are clean.

- [ ] **Step 11: Commit only the verified characterization correction**

Run:

```powershell
git add tests/test_food_discovery_characterization.py
git diff --cached --check
git diff --cached
git commit -m "test(api): characterize legacy food discovery and writes"
```

Expected: the first PR2A commit contains only the verified characterization file. `batches.json`, the design spec, and this plan remain uncommitted for the evidence checkpoint.

---

### Task 3: Prove whether migration-free semantic idempotency is possible

**Files:**
- Modify: `tests/test_food_discovery_characterization.py`
- Read: `app/services/meal_idempotency.py`
- Read: `app/models.py`
- Read: `app/blueprints/nutrition/meallog.py`
- Read: `app/blueprints/food.py`
- Read: `app/services/barcode.py`
- Read: `migrations/versions/*.py`
- Create: `docs/superpowers/reports/2026-08-10-sprint9-pr2a-idempotency-evidence.md`

**Interfaces:**
- Consumes: `read_idempotency_key()`, `find_existing(user_id, key)`, `commit_once(entry, key)`, the `MealLog` columns and unique `(user_id, idempotency_key)` constraint.
- Produces: executable characterization of current same-key/different-payload behavior and a decision-grade migration checkpoint. It does not produce a schema or production implementation.

- [ ] **Step 1: Add an explicit legacy characterization for semantic mismatch replay**

Append this test beside the existing `commit_once` characterization tests:

```python
def test_commit_once_legacy_replays_same_key_even_when_payload_differs(
        app, make_user):
    user = make_user("idem-semantic-mismatch")
    first = MealLog(
        user_id=user.id, ogun="Kahvaltı", yemekler="Yulaf",
        kalori=100.0, protein=5.0, karb=15.0, yag=2.0,
        tarih="2026-08-09")
    winner, created = meal_idempotency.commit_once(first, "semantic-key-01")

    different = MealLog(
        user_id=user.id, ogun="Akşam", yemekler="Somon",
        kalori=500.0, protein=45.0, karb=0.0, yag=30.0,
        tarih="2026-08-09")
    replay, created_again = meal_idempotency.commit_once(
        different, "semantic-key-01")

    assert created is True
    assert created_again is False
    assert replay.id == winner.id
    assert replay.ogun == "Kahvaltı"
    assert replay.yemekler == "Yulaf"
    assert MealLog.query.filter_by(user_id=user.id).count() == 1
```

This is characterization, not desired mobile behavior: it proves the current service binds only user and key.

- [ ] **Step 2: Run the new characterization and the full file**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_food_discovery_characterization.py::test_commit_once_legacy_replays_same_key_even_when_payload_differs -v
python -m pytest -p no:cacheprovider tests/test_food_discovery_characterization.py -q
```

Expected: the new test passes immediately against unchanged production code; the full characterization file reports 25 passing cases.

- [ ] **Step 3: Inventory every persisted idempotency/metadata field and caller**

Run:

```powershell
rg -n -C 8 "class MealLog|idempotency_key|photo_key|source\s*=|commit_once\(|find_existing\(" app migrations tests
rg -n "JSON|metadata|payload|command|fingerprint|request_hash" app/models.py app/services/meal_idempotency.py migrations/versions
```

Record exact definitions and callers. Verify whether anything beyond `(user_id, idempotency_key)` stores request semantics or response metadata.

- [ ] **Step 4: Prove whether a canonical command is reconstructible from `MealLog`**

Compare the future canonical commands with persisted columns:

```text
provider_backed command: kind, provider, food_id, serving_id, quantity, slot, discovery_source
manual command: kind, description, slot, energy_kcal, protein_g, carbohydrate_g, fat_g
MealLog row: user_id, ogun, yemekler, kalori, protein, karb, yag, tarih, source,
             idempotency_key, photo_key, created_at
```

Document at least these collision proofs:

```text
1. Two different provider food IDs can have the same display name, serving
   description, scaled macros, slot, source, day, and timestamp resolution.
2. Different serving ID/quantity combinations can yield the same final scaled
   nutrition; neither provider ID nor serving ID is persisted.
3. A manual snapshot can reproduce the same description and final nutrition as
   a provider result; only a bounded source may differ, and source cannot recover
   provider identity or quantity.
4. `yemekler` is a display string, not a lossless canonical command encoding.
```

The mapping is reconstructible only if every semantic command field can be
recovered uniquely after process restart. Any collision proves it cannot serve
as a persistent fingerprint.

- [ ] **Step 5: Evaluate existing general-purpose fields for semantic fitness**

For each candidate, record acceptance or rejection:

```text
source: bounded provenance token; cannot store a hash without breaking diary semantics.
photo_key: S3 object identity with URL-generation behavior; unrelated and nullable for photo use.
yemekler: user-visible description; overloading it leaks implementation data and breaks compatibility.
created_at/tarih: server-owned temporal data; non-unique and not request semantics.
nutrition columns: lossy scaled result; collisions are expected.
idempotency_key: client-provided replay key; encoding a server hash into it changes the public contract.
```

Do not accept a field merely because it can technically hold characters.

- [ ] **Step 6: Evaluate mobile-boundary-only, migration-free conflict detection**

Record why each mechanism is or is not durable and race-safe:

```text
process-local dict/cache: lost on restart and inconsistent across workers;
Redis-only mapping: not the canonical DB transaction winner and may expire/fail independently;
pre-insert compare against MealLog: safe only if the full canonical command is reconstructible;
description/source encoding: corrupts product semantics and can expose hashes;
client-key rewriting: cannot detect reuse of the original key and breaks existing validation/replay;
new table/column: persistent and transactional, but is a migration and therefore requires approval.
```

The accepted solution must survive restart, multiple Gunicorn workers, retries,
and the uniqueness-race loser path in `commit_once`.

- [ ] **Step 7: Write the decision-grade evidence report**

Create `docs/superpowers/reports/2026-08-10-sprint9-pr2a-idempotency-evidence.md` with these exact sections:

```markdown
# Sprint 9 PR2A Idempotency Evidence

## Executive finding
## Existing service behavior
## Existing MealLog schema and constraints
## Persisted semantic metadata inventory
## Canonical-command reconstruction proof
## Migration-free alternatives evaluated
## Concurrency and restart analysis
## Schema sufficiency verdict
## Proposed migration (not implemented)
## Legacy writer impact
## Existing-row null semantics
## Replay and conflict behavior
## Rollback
## Alembic and schema-drift impact
## PostgreSQL concurrency implications
## Approval checkpoint
```

If the schema is insufficient, the proposed-migration section must name the
exact nullable column type, length, index/constraint choice, application read and
write behavior, downgrade operation, and why no existing field is semantically
valid. It must explicitly state that no migration file or model edit has been
made.

- [ ] **Step 8: Re-run evidence tests and inspect every pending change**

Run:

```powershell
python -m pytest -p no:cacheprovider tests/test_food_discovery_characterization.py -q
git status --short
git diff --check
git diff --cached --check
rg -n "TBD|TODO|implement later|appropriate error|similar to" docs/superpowers/reports/2026-08-10-sprint9-pr2a-idempotency-evidence.md
```

Expected: 25 characterization cases pass; diff checks are clean; the evidence
report has no placeholder language.

- [ ] **Step 9: Commit the evidence and planning documents without schema changes**

Run:

```powershell
git add tests/test_food_discovery_characterization.py docs/superpowers/specs/2026-08-10-sprint9-pr2a-mobile-food-discovery-logfood-design.md docs/superpowers/plans/2026-08-10-sprint9-pr2a-phase2-idempotency-evidence.md docs/superpowers/reports/2026-08-10-sprint9-pr2a-idempotency-evidence.md
git diff --cached --check
git diff --cached
git commit -m "docs(api): record PR2A design and idempotency evidence"
```

Expected: the commit contains the additional legacy characterization plus the
approved design, this plan, and the evidence report. It contains no `app/models.py`,
`migrations/`, or production-service change. `batches.json` remains untracked.

- [ ] **Step 10: Stop at the migration approval checkpoint**

Report the baseline batch counts, focused-suite counts, PostgreSQL availability,
the exact schema-sufficiency verdict, alternatives rejected, and the proposed
migration details if one is necessary. Do not create a migration, edit the
model, start discovery extraction, or implement LogFood until the user responds.

---

## Plan completion condition

This plan is complete when Phase 2 is fully green and auditable, characterization
truth is committed, and the idempotency evidence checkpoint is delivered with no
production or schema edits. The next plan begins only after the user decides the
migration question; it will cover normalized discovery extraction, mobile
search/serving/barcode routes, canonical provider/manual LogFood, menu security,
architecture guards, final validation, and documentation.
