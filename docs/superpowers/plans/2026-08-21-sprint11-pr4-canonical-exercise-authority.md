# Sprint 11 PR4 Canonical Exercise Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every newly generated and saved AxisAI training-plan exercise resolve to a stable server-owned identity with deterministic equipment enforcement.

**Architecture:** A version-controlled curated catalog and cached exact resolver become the sole exercise authority. PR3-valid provider names are canonicalized locally, a signed user-bound context carries PR2 equipment truth to save, and canonical Adaptive Coaching mutations reuse the same resolver while legacy plans remain readable.

**Tech Stack:** Python 3.14, Flask, Flask-SQLAlchemy, pytest, standard-library `dataclasses`/`unicodedata`/`hmac`/`hashlib`/`base64`/`json`, vanilla JavaScript, JSON assets.

**Spec:** `docs/superpowers/specs/2026-08-20-sprint11-pr4-canonical-exercise-authority-design.md`

## Global Constraints

- Base is `origin/main` `95fb0563e04479124d716ccfc7325f6642bf4d6c`; PR3 (`#223`) and PR2 (`#222`) are prerequisites.
- Provider-generated strings and ID-looking strings are never exercise authority.
- Resolution order is exact active ID, normalized canonical name, normalized declared alias, unresolved.
- Normalization is NFKC + trim + casefold + repeated-whitespace collapse + conservative hyphen normalization; no fuzzy matching, stemming, token deletion, embeddings, or LLM fallback.
- PR2 equipment contexts retain their UI meanings: `spor_salonu` = full gym, `ev` = bodyweight, `minimal` = bodyweight + dumbbell + resistance band.
- Unknown, ambiguous, inactive, fake-ID, and equipment-incompatible exercises fail closed.
- No provider-created or client-created catalog entries and no public exercise lookup endpoint.
- No automatic substitution and no medical injury inference.
- Maximum generation-layer provider calls stays `2`; maximum repair stays `1`, only for parse/truncation.
- Save verifies exercise authority before destructive delete; invalid save leaves the current plan unchanged.
- Existing name-only plans and logs remain readable; no destructive backfill and no WorkoutLog migration.
- Adaptive Coaching exactly-once journal and undo semantics remain unchanged.
- No provider/model change, push, pull request, merge, deploy, production flag, or PR5 implementation.

---

### Task 1: Canonical catalog and exact resolver

**Files:**
- Create: `app/services/exercise_catalog.py`
- Create: `app/services/training_assets/exercises.json`
- Create: `tests/test_sprint11_exercise_authority.py`

**Interfaces:**
- Consumes: PR2 tokens from `TrainingPreferences.ekipman`, `kardiyo_tipi`, and `kardiyo_gun`.
- Produces: `ExerciseDefinition`, `ExerciseContext`, `ExerciseCatalog`, `load_exercise_catalog()`, `normalize_exercise_lookup(value)`, `resolve_exercise(exercise_id=None, name=None, catalog=None)`, `is_exercise_compatible(exercise, context)`, and `compatible_exercises(context)`.

- [ ] **Step 1: Add failing catalog-shape and stable-identity tests**

Add tests that load the real JSON asset and prove IDs are unique and match
`^ex_[a-z0-9_]+$`, canonical names are non-empty and unique after normalization,
equipment/movement/region tokens belong to closed vocabularies, aliases are
unambiguous, every entry has an explicit boolean `active`, and representative
IDs exist independently of their display names:

```python
def test_catalog_has_stable_product_owned_identity():
    catalog = load_exercise_catalog()
    squat = catalog.by_id["ex_barbell_back_squat"]
    assert squat.canonical_name == "Barbell Back Squat"
    renamed = replace(squat, canonical_name="Back Squat")
    assert renamed.exercise_id == "ex_barbell_back_squat"


def test_real_catalog_has_no_normalized_alias_collision():
    catalog = load_exercise_catalog()
    assert len(catalog.by_lookup) == sum(
        1 + len(entry.aliases) for entry in catalog.exercises
    )
```

- [ ] **Step 2: Run the authority tests and verify RED**

Run: `python -m pytest -q tests/test_sprint11_exercise_authority.py`

Expected: collection fails because `app.services.exercise_catalog` does not exist.

- [ ] **Step 3: Implement the catalog types, validation, and cached loader**

Use frozen dataclasses and immutable tuple/frozenset values:

```python
@dataclass(frozen=True)
class ExerciseDefinition:
    exercise_id: str
    canonical_name: str
    aliases: tuple[str, ...]
    equipment: frozenset[str]
    movement: str
    primary_region: str
    active: bool


@dataclass(frozen=True)
class ExerciseContext:
    equipment_context: str
    cardio_type: str = "yok"
    style: str = "general_fitness"
    catalog_version: int = 1


@dataclass(frozen=True)
class ExerciseCatalog:
    version: int
    exercises: tuple[ExerciseDefinition, ...]
    by_id: Mapping[str, ExerciseDefinition]
    by_lookup: Mapping[str, tuple[ExerciseDefinition, ...]]
```

`load_exercise_catalog()` uses `@lru_cache(maxsize=1)`, reads
`training_assets/exercises.json`, validates every field, rejects unknown keys and
collisions, and returns read-only indexes. `CatalogConfigurationError` is raised
for invalid product data; provider/client inputs never reach the loader.

- [ ] **Step 4: Populate the bounded catalog**

Add reviewed entries covering the exercise names in current tests and few-shots,
then the core official capability matrix:

- Powerlifting: squat, bench press, deadlift and bounded assistance.
- Bodybuilding: horizontal/vertical push and pull, squat/hinge/lunge, arms,
  shoulders, calves, and core using gym, dumbbell, band, and bodyweight variants.
- Calisthenics: push-up, pull-up/chin-up, dip, squat/lunge, bridge, plank,
  hollow-body, and bounded progressions that are distinct catalog entries.
- Functional/general: goblet squat, carries, kettlebell hinge, step-up, rows,
  presses, anti-extension, anti-rotation, and mobility movements.
- Cardio: running/walking/jump-rope/cycling/swimming entries aligned with the
  existing PR2 cardio tokens.

Every entry uses an explicit stable `ex_*` ID. Include aliases needed by current
fixtures (`Squat`, `Back Squat`, `Bench Press`, `Deadlift`, `Row`, `Push-up`,
`Goblet Squat`, `Curl`) without mapping distinct variants together.

- [ ] **Step 5: Add failing normalization and resolution tests**

```python
@pytest.mark.parametrize("raw", [
    "  BENCH   PRESS ", "Bench–Press", "Ｂｅｎｃｈ Press",
])
def test_safe_variants_resolve_to_bench_press(raw):
    assert resolve_exercise(name=raw).exercise_id == "ex_barbell_bench_press"


def test_semantically_distinct_name_is_not_fuzzy_matched():
    with pytest.raises(ExerciseUnresolved):
        resolve_exercise(name="Incline Benhc Press")


def test_unknown_supplied_id_does_not_fall_back_to_valid_name():
    with pytest.raises(ExerciseIdentityInvalid):
        resolve_exercise(exercise_id="ex_fake", name="Bench Press")
```

Use a constructed test catalog with one colliding normalized alias to prove
`ExerciseAmbiguous` fails closed rather than selecting the first entry.

- [ ] **Step 6: Implement exact resolution and context compatibility**

Implement domain exceptions `ExerciseResolutionError`, `ExerciseUnresolved`,
`ExerciseAmbiguous`, `ExerciseIdentityInvalid`, `ExerciseInactive`, and
`ExerciseIncompatible`. `resolve_exercise()` must prefer an explicitly supplied
ID and must not fall back to the name if that ID is invalid. `is_exercise_compatible()`
uses catalog equipment plus explicit cardio rules; it never parses the name.

Use these closed strength-equipment allowances:

```python
CONTEXT_EQUIPMENT = {
    "ev": frozenset({"bodyweight"}),
    "minimal": frozenset({"bodyweight", "dumbbell", "resistance_band"}),
    "spor_salonu": ALL_CATALOG_EQUIPMENT,
}
```

An exercise requiring multiple items is compatible only when all requirements
are available. Cardio modalities use explicit catalog/context rules so outdoor
running is not treated as a gym machine.

- [ ] **Step 7: Run catalog tests and commit**

Run: `python -m pytest -q tests/test_sprint11_exercise_authority.py`

Expected: all Task 1 tests pass.

Run: `git diff --check`

Commit:

```bash
git add app/services/exercise_catalog.py app/services/training_assets/exercises.json tests/test_sprint11_exercise_authority.py
git commit -m "feat(training): add canonical exercise catalog"
```

---

### Task 2: Equipment-filtered prompt vocabulary

**Files:**
- Modify: `app/services/training_generation/prompt_builder.py`
- Modify: `app/services/training_generation/service.py`
- Modify: `tests/test_sprint11_exercise_authority.py`
- Modify: `tests/test_sprint11_training_preference_contract.py`

**Interfaces:**
- Consumes: `ExerciseContext`, `compatible_exercises(context)` from Task 1.
- Produces: `canonical_exercise_vocabulary(context) -> tuple[str, ...]` and
  `build_training_prompt(..., exercise_vocabulary: Sequence[str] = ())`.

- [ ] **Step 1: Write failing vocabulary and prompt tests**

```python
def test_home_prompt_lists_bodyweight_but_not_barbell_exercises(...):
    prompt = captured_supported_prompt(ekipman="ev")
    assert "Push-Up" in prompt
    assert "Barbell Back Squat" not in prompt


def test_minimal_prompt_lists_dumbbell_and_band_but_not_machine(...):
    prompt = captured_supported_prompt(ekipman="minimal")
    assert "Goblet Squat" in prompt
    assert "Lat Pulldown" not in prompt


def test_prompt_vocabulary_is_bounded():
    names = canonical_exercise_vocabulary(
        ExerciseContext(equipment_context="spor_salonu")
    )
    assert len("\n".join(names)) <= 8000
```

- [ ] **Step 2: Run the focused prompt tests and verify RED**

Run: `python -m pytest -q tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_preference_contract.py -k "prompt or vocabulary"`

Expected: failures show the prompt has no canonical vocabulary block.

- [ ] **Step 3: Implement the bounded prompt block**

Add a keyword-only argument and a closed block:

```python
def build_training_prompt(
    features,
    preferences,
    classification,
    context,
    language="tr",
    *,
    exercise_vocabulary=(),
):
    vocabulary_block = "\n".join(f"- {name}" for name in exercise_vocabulary)
```

The prompt states that names must come from the list, while preserving the
server-authority warning that the output will still be resolved. Build the
context from canonical PR2 preferences before the provider call and pass the
deduplicated, sorted compatible canonical names. Do not include aliases, IDs,
equipment metadata, or the entire JSON asset.

- [ ] **Step 4: Run prompt/PR2 regressions and commit**

Run:

```bash
python -m pytest -q tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_preference_contract.py tests/test_prompt_builder.py
```

Expected: all pass; invalid PR2 requests still perform zero provider calls.

Commit:

```bash
git add app/services/training_generation/prompt_builder.py app/services/training_generation/service.py tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_preference_contract.py
git commit -m "feat(training): constrain provider exercise vocabulary"
```

---

### Task 3: Generation-time canonicalization and typed failures

**Files:**
- Create: `app/services/training_generation/exercise_resolution.py`
- Modify: `app/services/training_generation/output_errors.py`
- Modify: `app/services/training_generation/plan_schema.py`
- Modify: `app/services/training_generation/service.py`
- Modify: `app/services/training_generation/__init__.py`
- Modify: `app/blueprints/training.py`
- Modify: `locales/en.json`
- Modify: `locales/tr.json`
- Modify: `tests/test_sprint11_exercise_authority.py`
- Modify: `tests/test_sprint11_training_generation_output.py`

**Interfaces:**
- Consumes: Task 1 resolver and `ExerciseContext`; PR3
  `validate_generated_plan(plan, preferences, injuries)`.
- Produces: `canonicalize_plan_exercises(plan, context) -> dict` and typed
  `GenerationExerciseUnresolvedError`, `GenerationExerciseAmbiguousError`,
  `GenerationExerciseIdentityInvalidError`, and
  `GenerationExerciseIncompatibleError`.

- [ ] **Step 1: Add failing plan-canonicalization tests**

Start from `_valid_plan()` in `test_sprint11_training_generation_output.py` and
replace its free-form fixtures with resolvable aliases where success is expected.
Add explicit tests:

```python
def test_pr3_valid_aliases_become_canonical_ids():
    plan = _valid_plan(exercises=[_exercise("Back Squat")])
    canonical = canonicalize_plan_exercises(
        validate_generated_plan(plan, _prefs())[0],
        ExerciseContext("spor_salonu"),
    )
    ex = canonical["program"][0]["egzersizler"][0]
    assert ex["exercise_id"] == "ex_barbell_back_squat"
    assert ex["isim"] == "Barbell Back Squat"


def test_unresolved_provider_name_is_typed_and_not_repaired(...):
    response = valid_provider_plan_with("Invented Laser Row")
    with pytest.raises(GenerationExerciseUnresolvedError):
        generate_training_plan_payload(..., chat_fn=spy(response))
    assert spy.call_count == 1
```

Also prove duplicate aliases resolve to the same stable ID without duplicate
catalog lookup work, and an `ev` plan containing barbell squat fails with the
equipment-specific typed category.

- [ ] **Step 2: Run the new generation tests and verify RED**

Run: `python -m pytest -q tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_generation_output.py -k "exercise or canonical or unresolved or incompatible"`

Expected: failures show generated exercises still contain names only.

- [ ] **Step 3: Implement plan-level canonicalization**

`canonicalize_plan_exercises()` deep-copies or mutates only the already-new
validated candidate, deduplicates `(exercise_id, normalized_name)` references,
resolves them through the cached catalog, validates compatibility, and writes
only `exercise_id` plus the canonical `isim`; prescription fields are preserved.
It never adds catalog metadata to the plan.

Map domain exceptions to generation exceptions whose public bodies use closed
codes and existing `GenerationOutputError.to_body()` behavior. User-facing
messages are generic localized text and never echo provider exercise names.

- [ ] **Step 4: Integrate after PR3 validation without changing repair logic**

Keep `_parse_and_validate()` responsible for extraction and PR3 validation.
Immediately after it succeeds, canonicalize with the accepted `ExerciseContext`.
Do not place exercise exceptions inside either parse/truncation repair catch.
Add architecture assertions that the repair catches remain exactly
`ParseFailedError`/`TruncatedError` and the hard budget remains two calls.

- [ ] **Step 5: Run generation, route, and provider-budget suites**

Run:

```bash
python -m pytest -q tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_generation_output.py tests/test_training_generation.py tests/test_training_routes.py
```

Expected: canonical IDs appear on success; all typed failures and existing PR3
call-count tests pass.

- [ ] **Step 6: Commit generation authority**

```bash
git add app/services/training_generation app/blueprints/training.py locales/en.json locales/tr.json tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_generation_output.py tests/test_training_generation.py tests/test_training_routes.py
git commit -m "feat(training): resolve generated exercises canonically"
```

---

### Task 4: Signed save context, authoritative revalidation, and web clients

**Files:**
- Create: `app/services/training_generation/exercise_context_token.py`
- Modify: `app/services/training_generation/service.py`
- Modify: `app/services/training_generation/response_validator.py`
- Modify: `app/blueprints/training.py`
- Modify: `static/training.js`
- Modify: `static/plan_create.js`
- Modify: `tests/test_sprint11_exercise_authority.py`
- Modify: `tests/test_sprint11_training_generation_output.py`
- Modify: `tests/test_training_routes.py`
- Modify: `tests/test_plan_v2.py`
- Modify: `tests/test_training_ui.py`

**Interfaces:**
- Consumes: Task 3 canonicalizer; Flask `SECRET_KEY`; authenticated user ID.
- Produces: `sign_exercise_context(context, secret_key, user_id) -> str`,
  `verify_exercise_context(token, secret_key, user_id) -> ExerciseContext`, and
  `validate_plan_for_save(plan, exercise_context) -> dict` returning the
  canonical persisted document.

- [ ] **Step 1: Write failing token-integrity tests**

Use a domain-separated standard-library HMAC token; do not add dependencies:

```python
def test_context_token_round_trip_is_user_bound():
    context = ExerciseContext("minimal", cardio_type="yok", style="functional")
    token = sign_exercise_context(context, "secret", user_id=7)
    assert verify_exercise_context(token, "secret", user_id=7) == context
    with pytest.raises(ExerciseContextInvalid):
        verify_exercise_context(token, "secret", user_id=8)


@pytest.mark.parametrize("mutation", ["payload", "signature", "version"])
def test_context_token_tampering_fails_closed(mutation):
    ...
```

The token payload is canonical compact JSON containing only `v`, `uid`, `eq`,
`cardio`, `style`, and `catalog`. Encode payload and SHA-256 HMAC with unpadded
URL-safe base64 and compare signatures with `hmac.compare_digest`.

- [ ] **Step 2: Run token tests and verify RED**

Run: `python -m pytest -q tests/test_sprint11_exercise_authority.py -k token`

Expected: module/function import failure.

- [ ] **Step 3: Implement token signing and verification**

Reject non-string/oversized/malformed tokens, unknown payload keys or versions,
wrong user, unknown equipment/cardio/style tokens, catalog-version mismatch,
and invalid signatures with one `ExerciseContextInvalid` exception. Never log
the token or decoded payload.

- [ ] **Step 4: Add failing save-authority tests**

Cover the full route boundary:

```python
def test_valid_id_with_tampered_name_persists_catalog_name(client, auth_user):
    token = signed_context(auth_user.id, equipment="spor_salonu")
    plan = canonical_plan()
    plan[0]["egzersizler"][0].update(
        exercise_id="ex_barbell_bench_press", isim="Magic Chest Exercise"
    )
    assert client.post("/training-plan/save", json={
        "plan": plan, "score": 8, "exercise_context_token": token,
    }).status_code == 200
    assert persisted_exercise()["isim"] == "Barbell Bench Press"


def test_invalid_exercise_save_does_not_delete_existing_plan(...):
    before = existing.plan_data
    response = post_plan_with(exercise_id="ex_fake")
    assert response.status_code == 422
    assert db.session.get(TrainingPlan, existing.id).plan_data == before
```

Also test missing/forged/wrong-user token; unknown/inactive ID; unresolved
name-only item; valid declared name-only alias; home/barbell incompatibility;
client-supplied equipment metadata; and a delete spy proving all token,
resolution, equipment, structural, and semantic checks happen first.

- [ ] **Step 5: Implement canonical save construction**

Change the route to read `exercise_context_token`, verify it with
`current_app.config["SECRET_KEY"]` and `current_user.id`, then call:

```python
validated_document = validate_plan_for_save(plan, exercise_context)
TrainingPlan.query.filter_by(user_id=current_user.id).delete()
new_plan = TrainingPlan(
    user_id=current_user.id,
    plan_data=json.dumps(validated_document, ensure_ascii=False),
    score=score,
)
```

`validate_plan_for_save()` accepts provider-style names or canonical ID/name
pairs, reruns structure/semantics, resolves all exercises, and returns an object
with `program`, derived/preserved `haftalik_ozet`, and server-created:

```json
{"exercise_context":{"equipment_context":"minimal","cardio_type":"yok","style":"functional","catalog_version":1}}
```

The function ignores/rejects client-authored authority keys and uses only the
verified `ExerciseContext`. A valid ID always replaces the supplied display name
with the current canonical name.

Extend structural validation with an explicit
`allow_exercise_id: bool = False` parameter. Provider generation keeps the
default and therefore rejects provider-authored IDs; save opts in so
`exercise_id` is an optional input that is then required/created by canonical
resolution. Incoming `exercise_context` and other authority keys are rejected;
the persisted context is built only from the verified token.

- [ ] **Step 6: Emit the signed token from generation without leaking context**

Add an optional keyword-only
`context_token_factory: Callable[[ExerciseContext], str] | None = None` to
`generate_training_plan_payload()`. The route supplies a closure bound to the
authenticated user and `SECRET_KEY`; successful HTTP payloads include
`exercise_context_token`. Unit callers that do not need transport signing may
omit the factory, but the route may never return a successful candidate without
the token.

- [ ] **Step 7: Update both web clients**

Legacy `static/training.js` stores `data.exercise_context_token` next to
`currentPlan/currentScore` and posts it as `exercise_context_token`. Plan v2
`static/plan_create.js` forwards the token from generate directly into save.
Neither client parses, displays, edits, or persists the token outside the
in-memory candidate flow.

- [ ] **Step 8: Run save/client regressions and commit**

Run:

```bash
python -m pytest -q tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_generation_output.py tests/test_training_routes.py tests/test_plan_v2.py tests/test_training_ui.py
```

Expected: valid candidate saves succeed; every tamper case returns typed 422
without deletion; client source guards confirm token forwarding.

Commit:

```bash
git add app/services/training_generation app/blueprints/training.py static/training.js static/plan_create.js tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_generation_output.py tests/test_training_routes.py tests/test_plan_v2.py tests/test_training_ui.py
git commit -m "feat(training): enforce canonical exercises on save"
```

---

### Task 5: Canonical Adaptive Coaching mutations without undo changes

**Files:**
- Modify: `app/services/plan_mutation/document.py`
- Modify: `app/services/plan_mutation/validation.py`
- Modify: `tests/test_plan_mutation.py`
- Modify: `tests/test_plan_mutation_history.py`
- Modify: `tests/test_coach_plan_tools.py`
- Modify: `tests/test_plan_mutation_architecture.py`
- Modify: `tests/test_coach_plan_tools_architecture.py`

**Interfaces:**
- Consumes: persisted `exercise_context`, Task 1 resolver/compatibility, existing
  `apply_command(document, command) -> (document, changed)`.
- Produces: canonical target matching and replacement/add canonicalization for
  new plans while preserving the existing public mutation-service interfaces.

- [ ] **Step 1: Add failing canonical mutation tests**

```python
def test_replace_on_canonical_plan_preserves_slot_and_writes_identity():
    document = canonical_document(equipment_context="minimal")
    mutated, changed = apply_command(
        document,
        ReplaceExerciseCommand(
            day="Pazartesi", exercise="Row", replacement="DB Row"
        ),
    )
    ex = mutated["program"][0]["egzersizler"][0]
    assert changed is True
    assert ex["exercise_id"] == "ex_one_arm_dumbbell_row"
    assert ex["isim"] == "One-Arm Dumbbell Row"


def test_canonical_plan_rejects_incompatible_coach_replacement():
    with pytest.raises(InvalidMutation):
        apply_command(
            canonical_document(equipment_context="ev"),
            ReplaceExerciseCommand(
                day="Pazartesi",
                exercise="Push-Up",
                replacement="Barbell Back Squat",
            ),
        )
```

Also prove canonical add resolves aliases, target lookup uses stable identity
when the command names an alias, fake/unresolved replacement fails, and a
name-only legacy document keeps the existing casefold behavior.

- [ ] **Step 2: Run mutation tests and verify RED**

Run: `python -m pytest -q tests/test_plan_mutation.py -k "canonical or legacy"`

Expected: canonical replacements still write free-form names and no IDs.

- [ ] **Step 3: Integrate the shared resolver at the pure document boundary**

Add `FIELD_EXERCISE_ID = "exercise_id"` and a helper that validates the
persisted context into `ExerciseContext`. When context exists:

- resolve a command target name and match the one entry with that stable ID;
- resolve add/replace names through declared aliases;
- reject unresolved/inactive/incompatible replacements as `InvalidMutation`;
- write catalog `exercise_id` and canonical `isim` while preserving position,
  prescription, rest, notes, and unrelated fields.

When context is absent, retain the existing legacy name-only matching and
writing behavior exactly. Do not change command dataclasses, operation keys,
journal records, transaction order, mutation versions, or snapshots.

- [ ] **Step 4: Prove journal, replay, and undo invariants**

Add/adjust tests showing:

- applied canonical mutation increments version once and journals one record;
- same operation key replays without a second mutation;
- undo restores the exact canonical before-snapshot including exercise IDs and
  context;
- a rejected resolution creates no journal row and changes no plan bytes;
- legacy mutation history remains readable.

- [ ] **Step 5: Run Adaptive Coaching regressions and commit**

Run:

```bash
python -m pytest -q tests/test_plan_mutation.py tests/test_plan_mutation_history.py tests/test_coach_plan_tools.py tests/test_plan_mutation_architecture.py tests/test_coach_plan_tools_architecture.py
```

Expected: all pass with existing exactly-once/undo semantics unchanged.

Commit:

```bash
git add app/services/plan_mutation tests/test_plan_mutation.py tests/test_plan_mutation_history.py tests/test_coach_plan_tools.py tests/test_plan_mutation_architecture.py tests/test_coach_plan_tools_architecture.py
git commit -m "feat(training): enforce exercise authority in plan mutations"
```

---

### Task 6: Remove duplicate authority, prove compatibility/performance, and document the domain

**Files:**
- Create: `app/services/training_generation/movement_coverage.py`
- Delete: `app/services/training_generation/exercise_knowledge_base.py`
- Modify: `app/services/training_generation/program_generator.py`
- Modify: `tests/test_sprint11_exercise_authority.py`
- Modify: `tests/test_training_history.py`
- Modify: `tests/test_workout_session.py`
- Modify: `tests/test_workout_state.py`
- Modify: `tests/test_adaptive_plan_context.py`
- Modify: `tests/test_migration_graph.py`
- Modify: `docs/TRAINING_GENERATOR.md`
- Modify: `docs/ADAPTIVE_COACHING.md`
- Modify: `docs/handoff.md`

**Interfaces:**
- Consumes: all prior task contracts.
- Produces: one documented exercise authority, explicit legacy gaps, and
  architecture/performance evidence.

- [ ] **Step 1: Write failing duplicate-authority and performance guards**

Add source/AST guards proving:

```python
def test_no_legacy_exercise_kb_or_fuzzy_persistence_path():
    assert not Path("app/services/training_generation/exercise_knowledge_base.py").exists()
    source = production_exercise_sources()
    for forbidden in ("levenshtein", "fuzzy", "difflib", "rapidfuzz"):
        assert forbidden not in source.casefold()


def test_representative_plan_resolution_executes_no_sql(app, query_counter):
    canonicalize_plan_exercises(many_exercise_plan(), gym_context())
    assert query_counter.count == 0
```

Guard that provider schemas do not accept `exercise_id`, catalog code has no
`db.session.add`, save validation appears before `.delete()`, provider budget
constants remain `2` and repair `1`, and generation does not import mutation
journal/undo modules.

- [ ] **Step 2: Run architecture guards and verify RED**

Run: `python -m pytest -q tests/test_sprint11_exercise_authority.py -k "legacy_exercise_kb or architecture or sql"`

Expected: the legacy KB file still exists.

- [ ] **Step 3: Separate movement coverage and delete the old KB**

Move only `REQUIRED_MOVEMENT_COVERAGE` into
`training_generation/movement_coverage.py`, update `program_generator.py`, and
delete `exercise_knowledge_base.py`. Do not copy its risk/difficulty/progression
data into the new authority; those fields are untrusted and outside PR4.

- [ ] **Step 4: Add legacy compatibility coverage**

Prove existing list and wrapped name-only plans still load through presenter,
workout state, workout-session fingerprinting, Adaptive Coaching context, and
training history. Prove ambiguous legacy names are not backfilled or assigned a
fabricated ID. Keep `WorkoutLog.exercise_name` and migration baseline unchanged.

- [ ] **Step 5: Update canonical documentation**

Expand `docs/TRAINING_GENERATOR.md` with catalog ownership, stable ID format,
catalog count/coverage, alias and normalization rules, exact resolver hierarchy,
equipment vocabulary/context map, no-substitution policy, signed save context,
typed failures, zero-query behavior, no-migration deployment, legacy logging
gap, and provider-call invariants.

Update `docs/ADAPTIVE_COACHING.md` with canonical-plan resolution and the
unchanged legacy/undo boundary. Add a concise Sprint 11 PR4 handoff section to
`docs/handoff.md`; do not duplicate the full architecture.

- [ ] **Step 6: Run compatibility, migration, and architecture suites**

Run:

```bash
python -m pytest -q tests/test_sprint11_exercise_authority.py tests/test_training_history.py tests/test_workout_session.py tests/test_workout_state.py tests/test_adaptive_plan_context.py tests/test_migration_graph.py tests/test_plan_mutation_architecture.py tests/test_coach_plan_tools_architecture.py
```

Expected: all pass; migration graph remains single-head and unchanged by PR4.

- [ ] **Step 7: Commit compatibility and documentation**

```bash
git add app/services/training_generation tests/test_sprint11_exercise_authority.py tests/test_training_history.py tests/test_workout_session.py tests/test_workout_state.py tests/test_adaptive_plan_context.py tests/test_migration_graph.py docs/TRAINING_GENERATOR.md docs/ADAPTIVE_COACHING.md docs/handoff.md
git commit -m "docs(training): define canonical exercise domain"
```

---

### Task 7: Full validation, independent review, fixes, and final report

**Files:**
- Create: `docs/superpowers/specs/2026-08-20-sprint11-pr4-canonical-exercise-authority-final-report.md`
- Modify: any PR4 file required to fix confirmed review findings.

**Interfaces:**
- Consumes: completed Tasks 1–6 and the acceptance criteria in the design spec.
- Produces: reproducible validation evidence, P0/P1/P2 review disposition, exact
  PR5 recommendation, and one permitted final verdict.

- [ ] **Step 1: Run static and focused validation**

Run:

```bash
python -m compileall -q app tests
python -m pytest -q tests/test_sprint11_exercise_authority.py tests/test_sprint11_training_preference_contract.py tests/test_sprint11_training_generation_output.py tests/test_training_generation.py tests/test_training_routes.py tests/test_plan_mutation.py tests/test_plan_mutation_history.py tests/test_coach_plan_tools.py tests/test_training_history.py tests/test_workout_session.py tests/test_workout_state.py tests/test_adaptive_plan_context.py tests/test_migration_graph.py tests/test_plan_v2.py tests/test_training_ui.py
git diff --check
```

Record exact pass/fail counts, duration, warnings, and HEAD SHA. Fix any PR4
regression before continuing.

- [ ] **Step 2: Run the full non-load suite**

Run: `python -m pytest -q`

Expected: zero failures/errors. Record exact passed/skipped/deselected counts,
duration, and any baseline-only environmental flake. Re-run a failure in
isolation only to diagnose it; do not hide a PR4-caused failure as legacy.

- [ ] **Step 3: Perform a fresh independent review**

Review the complete diff and trace these attacks end to end:

1. duplicate catalog or legacy KB;
2. normalized alias collision and Unicode/hyphen over-normalization;
3. fuzzy/nearest matching or provider/client catalog insertion;
4. fake/inactive ID with valid name fallback;
5. valid ID plus tampered display/equipment metadata;
6. missing/forged/wrong-user signed context;
7. home/minimal equipment bypass, including cardio exceptions;
8. resolution after destructive delete;
9. Adaptive Coaching add/replace bypass or changed undo/replay behavior;
10. catalog N+1/database queries;
11. provider call/repair/model drift;
12. legacy plan/log/session/history breakage;
13. migration graph drift and accidental PR5 scope.

Classify each confirmed finding as P0/P1/P2 with file/line evidence. Fix all
P0/P1 findings, add regression tests, rerun the affected focused suites, and
record accepted P2 rationale. If any P0/P1 remains, the verdict is `NOT READY`.

- [ ] **Step 4: Write the required final report**

Create the exact required file and include every section from the task:
executive verdict; repository/base/branch/HEAD; PR3 verification; pre-PR4 map;
chosen authority; stable IDs; catalog coverage; names/aliases/normalization;
resolver and failures; equipment; substitutions; injury boundary; generated
contract; signed save and tampering; legacy/Adaptive/logging; provider calls;
performance; migration/seed strategy; security/privacy; tests/guards/full
validation; independent review and P0/P1/P2 dispositions; files/commits/final
state; remaining gaps; exact PR5 recommendation; explicit answers to all final
questions; and exactly one final verdict.

- [ ] **Step 5: Verify report claims against repository evidence**

Run:

```bash
git status --short --branch
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
python -m pytest -q tests/test_sprint11_exercise_authority.py
```

Every numeric claim in the report must match captured command output. Search the
report for unfinished markers, placeholder verdicts, or claims of push/merge/
deployment and correct them.

- [ ] **Step 6: Commit the final report and any final fixes**

```bash
git add app static locales tests docs
git commit -m "feat(training): complete canonical exercise authority"
```

Then rerun `git status --short --branch` and `git diff --check origin/main...HEAD`.
Stop with a clean worktree. Do not push, open a PR, merge, deploy, or begin PR5.

