# Mobile Training PR4A Durable Plan Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one bearer-authenticated `POST /api/v1/training/plans` command that generates, validates, persists, and replays a user's first canonical Training plan with durable PostgreSQL-backed idempotency.

**Architecture:** A strict native DTO produces a versioned semantic fingerprint, then a durable generation-operation ledger and nonblocking PostgreSQL owner advisory lock serialize execution. The existing Training generator is extracted to return a typed canonical candidate; the native command stages that candidate for crash recovery, atomically inserts the existing `TrainingPlan` model and completes the operation, and responds through the PR2 native projector.

**Tech Stack:** Python 3.11, Flask, Flask-Limiter, Flask-SQLAlchemy, SQLAlchemy, Alembic/Flask-Migrate, PostgreSQL 16, SQLite unit tests, pytest.

**Spec:** `docs/superpowers/specs/2026-09-01-mobile-training-pr4a-idempotent-plan-generation-design.md`

## Global Constraints

- Work from `origin/main` SHA `2cfd008e1e8ab5f320a7bf810c485d0a4759a894` on branch `mobile-training-pr4a-idempotent-plan-generation`.
- Do not modify Flutter/mobile, Nutrition, Pump Check, Progress, Coach, Feed/social, Adaptive Coaching activation, workout-session/completion behavior, browser UI, rollout defaults, or unrelated deployment infrastructure.
- Use `require_mobile_auth` and authenticated `g.mobile_user`; never accept user identity in the body.
- Reuse canonical preference, capability, generator, repair, exercise, injury, persistence, current-plan, quota, and projection authorities.
- No production code is written before its focused test has been observed failing for the intended reason.
- No application transaction or row lock remains open during provider I/O.
- PostgreSQL is the concurrency authority; SQLite-only concurrency evidence is insufficient.
- The existing browser `/training-plan` and `/training-plan/save` contracts remain behaviorally compatible.
- New migrations are additive, create-all-first safe, and rollback-compatible with old code.
- Do not log bearer credentials, idempotency keys, fingerprints, request bodies, prompts, provider output, candidate plans, or raw exception text.

## File Structure

- `app/services/mobile_training_generation/contract.py` — exact request and key parsing plus semantic fingerprinting.
- `app/services/mobile_training_generation/errors.py` — bounded command-domain errors and retry metadata.
- `app/services/mobile_training_generation/locking.py` — nonblocking owner advisory lock and SQLite test fallback.
- `app/services/mobile_training_generation/store.py` — durable operation lookup, claim, stage, fail, and success transitions.
- `app/services/mobile_training_generation/service.py` — generate-and-persist orchestration over the above units.
- `app/services/mobile_training_generation/__init__.py` — narrow public API for the route and tests.
- `app/services/training_generation/service.py` — typed candidate extraction while preserving the browser wrapper.
- `app/services/premium.py` — transaction-neutral quota primitives reused by old wrappers and the native claim.
- `app/services/mobile_training.py` — expose the existing row projector as the one POST/GET projector.
- `app/blueprints/mobile_training.py` — POST transport adapter, provider-only limits, and typed error mapping.
- `app/models.py`, `app/cli.py` — durable operation model and user-child registration.
- `migrations/versions/d3e4f5a6b7c8_add_training_plan_generation_operations.py` — additive ledger schema.
- Focused tests listed in each task, plus `.github/workflows/ci.yml` for the PostgreSQL suite.

---

### Task 1: Strict Native Contract and Semantic Fingerprint

**Files:**
- Create: `app/services/mobile_training_generation/__init__.py`
- Create: `app/services/mobile_training_generation/contract.py`
- Create: `app/services/mobile_training_generation/errors.py`
- Create: `tests/test_mobile_training_generation_contract.py`

**Interfaces:**
- Produces: `parse_idempotency_key(raw: object) -> str`.
- Produces: `parse_native_request(raw: object) -> NativePlanRequest`.
- Produces: `NativePlanRequest.preferences: TrainingPreferences` and `NativePlanRequest.fingerprint: str`.
- Produces: bounded exceptions `InvalidPlanRequest`, `InvalidIdempotencyKey`, and `IdempotencyConflict`, all derived from `PlanGenerationCommandError`.
- Consumes: canonical choices and `parse_canonical_preferences` from `training_generation.preference_contract`, then `require_supported` from `training_generation.capability`.

- [ ] **Step 1: Write failing exact-schema and fingerprint tests**

Create tests that name the production change that would make them fail:

```python
CANONICAL = {
    "gun_sayisi": 3, "ekipman": "spor_salonu", "odak": "tum_vucut",
    "sure": 45, "kardiyo_tipi": "yok", "kardiyo_gun": 0,
    "kardiyo_sure": 20, "kardiyo_yogunluk": "orta",
    "antrenman_tarzi": "genel", "odak_hedef": "genel", "injuries": "",
}

def test_native_request_requires_the_exact_canonical_field_set():
    assert parse_native_request(CANONICAL).preferences.gun_sayisi == 3
    for bad in ({**CANONICAL, "legacy_goal": "bulk"},
                {key: value for key, value in CANONICAL.items() if key != "sure"}):
        with pytest.raises(InvalidPlanRequest):
            parse_native_request(bad)

def test_fingerprint_is_order_stable_and_semantically_sensitive():
    first = parse_native_request(CANONICAL).fingerprint
    reordered = parse_native_request(dict(reversed(list(CANONICAL.items())))).fingerprint
    changed = parse_native_request({**CANONICAL, "sure": 60}).fingerprint
    assert first == reordered
    assert first != changed
```

Also cover non-object JSON, booleans/numeric strings for integer fields,
unknown tokens, unsupported/conflicting combinations, 2,001-character injury
input, control characters, and valid/invalid key regex boundaries.

- [ ] **Step 2: Run the contract tests and observe the missing-module failure**

Run:

```powershell
python -m pytest tests/test_mobile_training_generation_contract.py -q
```

Expected: collection fails because `app.services.mobile_training_generation` does not exist.

- [ ] **Step 3: Implement the minimal exact DTO and fingerprint**

Use a frozen DTO and canonical JSON:

```python
REQUEST_FIELDS = frozenset({
    "gun_sayisi", "ekipman", "odak", "sure", "kardiyo_tipi",
    "kardiyo_gun", "kardiyo_sure", "kardiyo_yogunluk",
    "antrenman_tarzi", "odak_hedef", "injuries",
})
FINGERPRINT_DOMAIN = b"axisai:training-plan-generation:v1\0"
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

@dataclass(frozen=True)
class NativePlanRequest:
    preferences: TrainingPreferences
    normalized: dict[str, object]
    fingerprint: str

def _fingerprint(normalized):
    encoded = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(FINGERPRINT_DOMAIN + encoded).hexdigest()
```

Require all keys and JSON-native types before calling
`parse_canonical_preferences(raw, stored_injuries="")`. Convert
`PreferenceContractError` into `InvalidPlanRequest` only for malformed values;
allow the canonical unsupported/conflicting errors to retain their existing
codes for the HTTP mapper. Call `require_supported` before returning the DTO.

- [ ] **Step 4: Run contract tests to green**

Run:

```powershell
python -m pytest tests/test_mobile_training_generation_contract.py -q
```

Expected: all contract tests pass.

- [ ] **Step 5: Commit the contract**

```powershell
git add app/services/mobile_training_generation tests/test_mobile_training_generation_contract.py
git commit -m "feat(training): define native generation contract"
```

---

### Task 2: Extract the Canonical Typed Generation Candidate

**Files:**
- Modify: `app/services/training_generation/service.py`
- Create: `tests/test_mobile_training_generation_candidate.py`
- Modify: `tests/test_sprint11_training_generation_output.py`
- Modify: `tests/test_sprint12_pr2b_canonical_injury_annotation.py`

**Interfaces:**
- Produces: `GeneratedTrainingPlanCandidate` with `document`, `overall_score`, `exercise_context`, `injury_warnings`, `classification`, `risk_flags`, and `constraints_applied`.
- Produces: `generate_training_plan_candidate(user, last_session, preferences, chat_fn, language="tr", logger=None) -> GeneratedTrainingPlanCandidate`.
- Preserves: `generate_training_plan_payload(...) -> dict` and its browser response fields.
- Consumes: `TrainingPreferences` from Task 1 without reparsing or persisting injury metadata.

- [ ] **Step 1: Write failing candidate equivalence and side-effect tests**

Use the existing valid provider fixture and assert:

```python
def test_typed_candidate_runs_existing_authorities_in_canonical_order(...):
    candidate = generate_training_plan_candidate(
        user, session, preferences, chat_fn=provider)
    assert candidate.document["program"][0]["egzersizler"][0][
        "exercise_id"] == "ex_barbell_back_squat"
    assert candidate.exercise_context.equipment_context == "spor_salonu"
    assert provider.calls <= MAX_PROVIDER_COMPLETIONS

def test_candidate_does_not_persist_injuries_or_commit(...):
    generate_training_plan_candidate(...)
    assert refreshed_user.user_metadata == before
    assert commits == []
```

Add a browser characterization proving `generate_training_plan_payload` still
returns the exact existing key set and still signs its context token. Retain the
existing alias resolution, equipment incompatibility, malformed output, repair,
and injury-order assertions.

- [ ] **Step 2: Run the candidate tests and observe the missing-symbol failure**

```powershell
python -m pytest tests/test_mobile_training_generation_candidate.py -q
```

Expected: import fails for `GeneratedTrainingPlanCandidate` or
`generate_training_plan_candidate`.

- [ ] **Step 3: Extract the candidate without duplicating the pipeline**

Move the body after browser parsing into the typed function. Keep provider
budget construction, parsing/repair, canonicalization, and injury annotation in
one implementation. Build the persisted document from the already canonical
candidate and server context:

```python
@dataclass(frozen=True)
class GeneratedTrainingPlanCandidate:
    document: dict
    overall_score: float
    exercise_context: ExerciseContext
    injury_warnings: list[dict]
    classification: ClassificationResult
    risk_flags: list[str]
    constraints_applied: list[str]

def _persistence_document(plan, context):
    return {
        "program": plan["program"],
        "haftalik_ozet": plan["haftalik_ozet"],
        "exercise_context": {
            "equipment_context": context.equipment_context,
            "cardio_type": context.cardio_type,
            "style": context.style,
            "catalog_version": context.catalog_version,
        },
    }
```

The browser wrapper remains responsible for parsing legacy-compatible request
data, optionally persisting posted injuries, and converting the candidate into
its old payload and signed token. The native function receives already parsed
preferences and performs no database write.

- [ ] **Step 4: Run focused generator tests**

```powershell
python -m pytest tests/test_mobile_training_generation_candidate.py tests/test_sprint11_training_generation_output.py tests/test_sprint12_pr2b_canonical_injury_annotation.py tests/test_training_generation.py -q
```

Expected: all pass with existing provider bounds and browser payload preserved.

- [ ] **Step 5: Commit the extraction**

```powershell
git add app/services/training_generation/service.py tests/test_mobile_training_generation_candidate.py tests/test_sprint11_training_generation_output.py tests/test_sprint12_pr2b_canonical_injury_annotation.py
git commit -m "refactor(training): expose canonical generation candidate"
```

---

### Task 3: Durable Operation Schema and Transaction-Neutral Quota

**Files:**
- Modify: `app/models.py`
- Modify: `app/cli.py`
- Modify: `app/services/premium.py`
- Create: `migrations/versions/d3e4f5a6b7c8_add_training_plan_generation_operations.py`
- Create: `tests/test_training_plan_generation_operation.py`
- Create: `tests/test_training_plan_generation_migration.py`
- Modify: `tests/test_premium_quota.py`
- Modify: `tests/test_cascade_delete.py`
- Modify: `tests/test_migration_graph.py`

**Interfaces:**
- Produces: `TrainingPlanGenerationOperation` and status constants.
- Produces: `reserve_ai_quota_in_transaction(user_id, counter_key, limit) -> str | None` where the return is the reserved ISO week.
- Produces: `refund_ai_quota_in_transaction(user_id, counter_key, reserved_week) -> None`.
- Preserves: existing `reserve_ai_quota`, `refund_ai_quota`, and `premium_ai_plan_gate` commit behavior.
- Migration parent: `c2d3e4f5a6b7`.

- [ ] **Step 1: Write failing model, quota, migration, and cascade tests**

Assert the schema contract explicitly:

```python
def test_generation_operation_has_owner_key_and_active_owner_uniqueness():
    table = TrainingPlanGenerationOperation.__table__
    assert _unique_columns(table, "uq_training_plan_generation_user_key") == (
        "user_id", "idempotency_key")
    active = next(index for index in table.indexes
                  if index.name == "uq_training_plan_generation_active_owner")
    assert active.unique is True

def test_transactional_quota_helper_does_not_commit(make_user, monkeypatch):
    week = premium.reserve_ai_quota_in_transaction(user.id, "training", 1)
    assert week == premium._week_key()
    db.session.rollback()
    assert premium.remaining_ai_plans(refreshed_user, "training") == 1
```

Migration tests must import the revision, verify `down_revision`, run `upgrade`
against a create-all-first schema and a migration-only empty schema, reflect all
columns/indexes, and run `downgrade` without affecting `training_plan`.

- [ ] **Step 2: Run the persistence tests and observe missing model/helper failures**

```powershell
python -m pytest tests/test_training_plan_generation_operation.py tests/test_training_plan_generation_migration.py tests/test_premium_quota.py tests/test_cascade_delete.py tests/test_migration_graph.py -q
```

Expected: failures identify the absent model, revision, and quota helpers.

- [ ] **Step 3: Add the model and user-child registration**

Use bounded columns and portable partial indexes:

```python
class TrainingPlanGenerationOperation(db.Model):
    __tablename__ = "training_plan_generation_operation"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    idempotency_key = db.Column(db.String(64), nullable=False)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    attempt_count = db.Column(db.Integer, nullable=False, default=1, server_default="1")
    candidate_plan_data = db.Column(db.Text)
    candidate_score = db.Column(db.Float)
    training_plan_id = db.Column(db.Integer)
    plan_lineage_id = db.Column(db.String(64))
    quota_reserved = db.Column(db.Boolean, nullable=False, default=False, server_default=text("false"))
    quota_week = db.Column(db.String(8))
    error_code = db.Column(db.String(64))
    error_http_status = db.Column(db.Integer)
    error_retryable = db.Column(db.Boolean)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime)
```

Declare unique `(user_id, idempotency_key)`, owner/status lookup, and unique
owner partial index where `status IN ('IN_PROGRESS', 'GENERATED')`, with both
`sqlite_where` and `postgresql_where`. Add the model before `TrainingPlan` in
`app/cli.py::_user_child_models()` so purge ordering is explicit.

- [ ] **Step 4: Extract no-commit quota primitives and preserve wrappers**

Factor the locked metadata update out of the current functions. The old public
wrappers call the helper and then commit. The native command can instead add its
operation row and commit quota plus claim atomically. Preserve premium users as
uncharged and preserve week-rollover/refund behavior.

- [ ] **Step 5: Add the additive idempotent migration**

Create revision `d3e4f5a6b7c8` with `down_revision = "c2d3e4f5a6b7"`.
Guard table creation with `sa.inspect(op.get_bind()).has_table(...)`; after a
create-all-first boot, reflect and verify required columns/indexes instead of
blindly creating them. Downgrade drops only the new table.

- [ ] **Step 6: Run persistence tests to green**

```powershell
python -m pytest tests/test_training_plan_generation_operation.py tests/test_training_plan_generation_migration.py tests/test_premium_quota.py tests/test_cascade_delete.py tests/test_migration_graph.py -q
```

- [ ] **Step 7: Commit durable infrastructure**

```powershell
git add app/models.py app/cli.py app/services/premium.py migrations/versions/d3e4f5a6b7c8_add_training_plan_generation_operations.py tests/test_training_plan_generation_operation.py tests/test_training_plan_generation_migration.py tests/test_premium_quota.py tests/test_cascade_delete.py tests/test_migration_graph.py
git commit -m "feat(training): add durable generation operation"
```

---

### Task 4: Advisory Lock and Generate-and-Persist State Machine

**Files:**
- Create: `app/services/mobile_training_generation/locking.py`
- Create: `app/services/mobile_training_generation/store.py`
- Create: `app/services/mobile_training_generation/service.py`
- Modify: `app/services/mobile_training_generation/__init__.py`
- Create: `tests/test_mobile_training_generation_service.py`

**Interfaces:**
- Produces: `try_owner_lock(user_id: int)` context manager yielding `True` when acquired and `False` without waiting.
- Produces: `GenerationCommandResult(plan: TrainingPlan, replayed: bool)`.
- Produces: `generate_and_persist(user, request, key, *, chat_fn, provider_guard, logger=None) -> GenerationCommandResult`.
- Produces errors: `GenerationInProgress`, `ExistingPlanRefused`, `GenerationPrerequisiteMissing`, `GenerationPersistenceUnavailable`, and `StoredGenerationFailure`.
- Consumes: Task 1 DTO, Task 2 candidate, Task 3 operation/quota model, and canonical `get_active_plan`.

- [ ] **Step 1: Write failing state-machine tests with provider detonators**

Cover one behavior per test:

```python
def test_succeeded_same_key_replays_without_provider(...):
    first = generate_and_persist(..., chat_fn=counting_provider)
    second = generate_and_persist(..., chat_fn=detonator)
    assert second.replayed is True
    assert second.plan.id == first.plan.id
    assert TrainingPlan.query.count() == 1

def test_same_key_different_fingerprint_conflicts_before_provider(...):
    generate_and_persist(..., request=first_request, chat_fn=provider)
    with pytest.raises(IdempotencyConflict):
        generate_and_persist(..., request=changed_request, chat_fn=detonator)

def test_generated_state_persists_without_regeneration_after_crash(...):
    operation = staged_operation(validated_candidate)
    result = generate_and_persist(..., chat_fn=detonator)
    assert result.plan.plan_data == operation_candidate_json
```

Also test: no-plan success, existing-plan refusal before provider, failed
generation leaves no plan, provider failure stores bounded `FAILED` metadata and
refunds quota, failed replay detonates provider, missing session consumes no key,
staging failure returns no success, final insert failure leaves `GENERATED`,
final current-plan race refuses replacement, success clears candidate, and no
WorkoutSession/WorkoutLog/PumpCheck/XP/quest records are created.

- [ ] **Step 2: Run service tests and observe missing state-machine failures**

```powershell
python -m pytest tests/test_mobile_training_generation_service.py -q
```

- [ ] **Step 3: Implement the owner advisory lock**

Derive a signed 64-bit PostgreSQL advisory key from a domain-separated SHA-256
digest of authenticated `user_id`. Open a dedicated autocommit connection and
call `pg_try_advisory_lock`; always call `pg_advisory_unlock` in `finally` before
closing. Do not include the key or user ID in logs. For SQLite unit tests, use a
process-local nonblocking owner lock only as a compatibility harness; model
uniqueness remains the durable final arbiter, and PostgreSQL races are Task 6.

- [ ] **Step 4: Implement durable store transitions**

Keep database mutation helpers explicit:

```python
def find_by_key(user_id, key): ...
def find_active_for_owner(user_id): ...
def claim(user_id, key, fingerprint) -> TrainingPlanGenerationOperation: ...
def stage_candidate(operation_id, document_json, score) -> None: ...
def record_failure(operation_id, error) -> None: ...
def commit_plan(operation_id, user_id) -> TrainingPlan: ...
```

Every query scopes by authenticated owner. `claim` inserts the operation and
reserves quota in one commit. `stage_candidate` validates the maximum serialized
size before storing. `commit_plan` locks the operation, accepts only
`GENERATED`, re-checks `get_active_plan`, inserts `TrainingPlan`, flushes its
identity, changes the operation to `SUCCEEDED`, clears candidate/error fields,
and commits once. Roll back on every exception.

- [ ] **Step 5: Implement orchestration and typed failure replay**

Order the service exactly as the design specifies: durable replay/conflict
preflight, nonblocking owner lock, second preflight, existing-plan and active-op
checks, claim/resume, provider guard only for `IN_PROGRESS`, stage, persist, and
return. Translate canonical generation failures into bounded stored metadata;
never store or expose `str(provider_exception)`.

- [ ] **Step 6: Run service and generator suites to green**

```powershell
python -m pytest tests/test_mobile_training_generation_service.py tests/test_mobile_training_generation_contract.py tests/test_mobile_training_generation_candidate.py tests/test_training_generation.py tests/test_premium_quota.py -q
```

- [ ] **Step 7: Commit the command authority**

```powershell
git add app/services/mobile_training_generation tests/test_mobile_training_generation_service.py
git commit -m "feat(training): persist idempotent first-plan commands"
```

---

### Task 5: Bearer-Only POST Route and Projection Convergence

**Files:**
- Modify: `app/services/mobile_training.py`
- Modify: `app/blueprints/mobile_training.py`
- Modify: `tests/test_mobile_training_api.py`
- Modify: `tests/test_mobile_training_architecture.py`
- Create: `tests/test_mobile_training_generation_api.py`

**Interfaces:**
- Produces: `mobile_training.project_current_plan(plan, user_id, secret, *, sessions_enabled=False) -> dict`, used by POST and GET.
- Produces: `POST /api/v1/training/plans` with required bearer and idempotency headers.
- Produces: `201 {"plan": ...}` plus `Idempotency-Replayed: false|true`.
- Consumes: `generate_and_persist`, `_heavy_complete`, mobile error envelope, `AI_RATELIMIT`, `BEDROCK_RATELIMIT`, Flask-Limiter block contexts, and `blocking_concurrency_slot`.

- [ ] **Step 1: Write failing HTTP and convergence tests**

Add literal contract assertions for:

```python
def test_post_success_equals_immediate_get_current(...):
    created = client.post(POST_PATH, json=CANONICAL, headers=headers)
    current = client.get(CURRENT_PLAN_PATH, headers=auth_headers)
    assert created.status_code == 201
    assert created.json == current.json
    assert created.headers["Idempotency-Replayed"] == "false"

def test_response_loss_retry_is_exact_replay_without_provider(...):
    first = client.post(...)
    install_provider_detonator()
    replay = client.post(...)
    assert replay.status_code == 201
    assert replay.json == first.json
    assert replay.headers["Idempotency-Replayed"] == "true"
```

Cover valid/missing/invalid bearer, browser-cookie rejection, invalid/missing
key, unknown field, malformed canonical value, unsupported combination,
oversized input, provider unavailable/timeout/output categories, existing-plan
409, operation-in-progress 409 with `Retry-After`, fingerprint conflict 409,
rate-limit envelope, persistence failure, cross-user ownership, raw-ID absence,
POST-to-GET equality, and no-store headers.

Instrument SQL in one success, replay, and failure test. Pin a bounded statement
count, assert replay performs no provider query/work, assert owner/key lookups use
the indexed predicates, and compare plans with one versus many repeated
exercises to catch an exercise-level N+1.

- [ ] **Step 2: Run API tests and observe POST 405/404 failures**

```powershell
python -m pytest tests/test_mobile_training_generation_api.py -q
```

- [ ] **Step 3: Expose the shared PR2 row projector**

Rename `_project_plan` to a public row-level helper only if needed internally,
then add `project_current_plan` as the shared supported boundary that performs
the existing row projection plus canonical current-workout selection.
`build_current_plan` queries `get_active_plan` and delegates to this helper;
POST delegates with its just-committed row. Do not fork serialization.

- [ ] **Step 4: Add provider-only limit context and POST route**

Implement a route-local context manager used only when the service invokes its
candidate factory:

```python
@contextmanager
def _native_generation_provider_guard():
    with limiter.limit(AI_RATELIMIT, key_func=lambda: str(g.mobile_user.id)):
        with limiter.limit(BEDROCK_RATELIMIT,
                           key_func=lambda: str(g.mobile_user.id)):
            with blocking_concurrency_slot():
                yield
```

Map `RateLimitExceeded` to a typed mobile 429 and
`BlockingConcurrencyLimit` to a typed 503 with `Retry-After`. Because the
service enters this context only for `IN_PROGRESS`, completed/failed/staged
replays and conflicts do not consume provider limits or AI capacity.

Add the route:

```python
@bp.post("/training/plans")
@require_mobile_auth
def create_training_plan():
    # parse body/key, call command with g.mobile_user, return shared projection
```

Use `request.get_json(silent=True)` only to obtain raw JSON; all authority and
strictness lives in Task 1. Map every domain/canonical exception to the existing
`mobile_error` envelope. Never return raw exception messages.

- [ ] **Step 5: Add Today convergence and side-effect tests**

In the API suite, prove:

```text
GET /api/v1/today -> no_plan
POST /api/v1/training/plans -> 201
GET /api/v1/today -> canonical planned state
```

Count relevant tables before/after to prove no workout session, completion,
WorkoutLog, PumpCheck, XP, quest, Progress, Coach, Feed, or Nutrition write.

- [ ] **Step 6: Run native API/read/Today/auth suites**

```powershell
python -m pytest tests/test_mobile_training_generation_api.py tests/test_mobile_training_api.py tests/test_mobile_training_architecture.py tests/test_mobile_today.py tests/test_mobile_auth_feature_gate.py -q
```

- [ ] **Step 7: Commit the native route**

```powershell
git add app/services/mobile_training.py app/blueprints/mobile_training.py tests/test_mobile_training_generation_api.py tests/test_mobile_training_api.py tests/test_mobile_training_architecture.py
git commit -m "feat(training): add native plan generation endpoint"
```

---

### Task 6: PostgreSQL Concurrency Proof and Architecture Guards

**Files:**
- Create: `tests/test_mobile_training_generation_pg.py`
- Modify: `tests/test_mobile_training_architecture.py`
- Modify: `.github/workflows/ci.yml`
- Create: `docs/mobile/training-plan-generation-command.md`

**Interfaces:**
- Produces: opt-in `pg_concurrency` tests using `FITX_PG_CONCURRENCY_TEST=1` and disposable `PG_TEST_DATABASE_URL`.
- Produces: CI inclusion of the new PostgreSQL suite.
- Produces: final public contract and failure/replay documentation.

- [ ] **Step 1: Write the PostgreSQL race tests and CI tripwire first**

Build a disposable Flask/SQLAlchemy app following
`tests/test_mobile_log_food_pg.py`, but coordinate provider entry with barriers
and events rather than sleeps. Required tests:

```python
def test_same_owner_key_fingerprint_runs_one_provider_execution(...): ...
def test_same_owner_key_different_fingerprint_conflicts_without_second_call(...): ...
def test_different_owners_same_key_are_independent(...): ...
def test_same_owner_different_keys_cannot_create_two_plans(...): ...
def test_successful_replay_detonates_provider_and_keeps_one_plan(...): ...
def test_generated_crash_recovery_persists_without_provider(...): ...
```

The first provider blocks on an `Event`; the duplicate starts only after the
first has recorded entry. Assert the duplicate returns in-progress rather than
waiting. Assert exact provider counts, operation counts/statuses, plan counts,
ownership, fingerprints, lineage, and mutation version.

Add an architecture test that fails until `.github/workflows/ci.yml` lists
`tests/test_mobile_training_generation_pg.py` in the PostgreSQL concurrency job.

- [ ] **Step 2: Run the new PG module without opt-in and observe a clean skip**

```powershell
python -m pytest tests/test_mobile_training_generation_pg.py -q
```

Expected: module skipped because `FITX_PG_CONCURRENCY_TEST` is not set.

- [ ] **Step 3: Add the suite to CI and architecture guards**

Extend guards to assert bearer decoration, no browser blueprint import,
canonical generator/capability/exercise/injury/projector imports, durable model
and migration, no provider call on replay, no delete/replace path, and unchanged
auth/session default declarations. Add source/log-capture guards proving keys,
fingerprints, body values, candidate JSON, raw provider errors, and database IDs
are absent from logs and responses. Update the CI `mobile-pg-concurrency`
command with the new test file.

- [ ] **Step 4: Document the exact command contract**

Document request keys/types, `Idempotency-Key`, 201 response/replay header,
status machine, failure table, retry-with-same-key ambiguity resolution,
new-key-after-definitive-retryable-failure rule, crash boundaries, existing-plan
protection, and POST/GET/Today convergence. State the unavoidable external-call
boundary: a worker crash during an un-idempotent provider request may safely
re-run inference, but cannot create a partial or duplicate canonical plan.

- [ ] **Step 5: Run PostgreSQL races locally when a disposable URL is available**

```powershell
$env:FITX_PG_CONCURRENCY_TEST='1'
if (-not $env:PG_TEST_DATABASE_URL) { throw 'Set PG_TEST_DATABASE_URL to the disposable PostgreSQL 16 test database.' }
python -m pytest -m pg_concurrency -q tests/test_mobile_training_generation_pg.py
```

If no disposable PostgreSQL service is available locally, record this as a
blocking validation gap and do not claim PostgreSQL proof until exact-HEAD CI
runs the configured PostgreSQL 16 job.

- [ ] **Step 6: Run architecture and documentation-adjacent tests**

```powershell
python -m pytest tests/test_mobile_training_architecture.py tests/test_migration_graph.py -q
git diff --check
```

- [ ] **Step 7: Commit concurrency proof and docs**

```powershell
git add tests/test_mobile_training_generation_pg.py tests/test_mobile_training_architecture.py .github/workflows/ci.yml docs/mobile/training-plan-generation-command.md
git commit -m "test(training): prove generation concurrency safety"
```

---

### Task 7: Full Verification, Adversarial Review, Push, and PR

**Files:**
- Modify only files required to fix verified PR4A defects.
- Create: `docs/superpowers/reports/2026-09-01-mobile-training-pr4a-independent-review.md`

**Interfaces:**
- Produces: fresh validation evidence, P0–P3 independent review, pushed branch, PR, and exact-HEAD CI evidence.
- Consumes: all prior task deliverables and the original PR4A acceptance matrix.

- [ ] **Step 1: Run compile and static validation**

```powershell
python -m compileall -q app tests
git diff --check origin/main...HEAD
python -m pytest tests/test_training_plan_generation_migration.py tests/test_migration_graph.py -q
```

Run the repository's configured formatter/linter if one is present at execution
time; do not introduce a new formatter configuration in PR4A.

- [ ] **Step 2: Validate migration upgrade and schema drift on PostgreSQL**

Against a disposable PostgreSQL database:

```powershell
if (-not $env:PG_TEST_DATABASE_URL) { throw 'Set PG_TEST_DATABASE_URL to the disposable PostgreSQL 16 test database.' }
$env:DATABASE_URL=$env:PG_TEST_DATABASE_URL
$env:FITX_SKIP_DB_INIT='1'
$env:FLASK_ENV='development'
python -m flask --app starter db upgrade
python -m flask --app starter db check
```

Also upgrade once from revision `c2d3e4f5a6b7` to head and once through the
repository's create-all-first boot path. Verify downgrade of only the new
revision in a disposable database.

- [ ] **Step 3: Run the focused regression matrix**

```powershell
python -m pytest -q \
  tests/test_mobile_training_generation_contract.py \
  tests/test_mobile_training_generation_candidate.py \
  tests/test_training_plan_generation_operation.py \
  tests/test_training_plan_generation_migration.py \
  tests/test_mobile_training_generation_service.py \
  tests/test_mobile_training_generation_api.py \
  tests/test_mobile_training_api.py \
  tests/test_mobile_training_architecture.py \
  tests/test_training_generation.py \
  tests/test_sprint11_training_generation_output.py \
  tests/test_sprint12_pr2b_canonical_injury_annotation.py \
  tests/test_premium_quota.py \
  tests/test_mobile_today.py \
  tests/test_mobile_auth_feature_gate.py
```

On PowerShell, replace the backslash continuations with backticks or pass the
paths on one line. Record exact passed/failed/skipped counts.

- [ ] **Step 4: Run the complete PostgreSQL concurrency job command**

Use the exact `.github/workflows/ci.yml` command with PostgreSQL 16 and include
`tests/test_mobile_training_generation_pg.py`. Record exact counts and prove the
new module did not skip.

- [ ] **Step 5: Run the full default backend regression**

```powershell
python -m pytest -q
```

Record exact passed/failed/skipped counts from this fresh HEAD.

- [ ] **Step 6: Request independent adversarial code review**

Use the `superpowers:requesting-code-review` skill with:

```text
BASE_SHA=2cfd008e1e8ab5f320a7bf810c485d0a4759a894
HEAD_SHA is captured by running `git rev-parse HEAD` immediately before review
REQUIREMENTS=the approved PR4A design and original mc-pr4a.txt acceptance matrix
```

The reviewer must attack double provider execution, retry races, non-durable
idempotency, fingerprint ambiguity, cross-user collision, plan overwrite,
partial persistence, commit-before-response recovery, provider leakage, browser
controller dependency, raw ID leakage, duplicate canonical logic, state
corruption, unbounded repair, transaction/pool bugs, quota double-charge,
Sprint 13 overlap, and Pump Check overlap. Classify P0/P1/P2/P3 in the report.

- [ ] **Step 7: Fix every P0/P1 through TDD and re-run affected plus full tests**

For each valid issue, write a failing regression test, observe it fail, make the
minimal fix, run the focused test to green, then repeat Steps 1–5. Do not proceed
with unresolved P0 or P1 findings.

- [ ] **Step 8: Commit the review report and verify the final tree**

```powershell
git add docs/superpowers/reports/2026-09-01-mobile-training-pr4a-independent-review.md
git commit -m "docs: record PR4A independent review"
git status --short --branch
git log --oneline origin/main..HEAD
git diff --check origin/main...HEAD
```

The tree must be clean and the branch must be based on the recorded current
`origin/main`; fetch again and report ahead/behind before push. If main advanced,
rebase only after reviewing overlap and rerun all verification on the rebased
HEAD.

- [ ] **Step 9: Push and create the PR**

```powershell
git push -u origin mobile-training-pr4a-idempotent-plan-generation
gh pr create --base main --head mobile-training-pr4a-idempotent-plan-generation --title "feat(training): add idempotent native plan generation" --body "Problem: Native Training has canonical reads but no retry-safe first-plan write; the legacy generate/save pair is unsafe for mobile ambiguity.`n`nSolution: Add one bearer-authenticated durable idempotent generate-and-persist command that reuses canonical Training authorities.`n`nGuarantees: strict preferences; canonical generator/exercise/injury validation; atomic first-plan persistence; no silent replacement; durable replay with no duplicate provider call; POST/GET/Today convergence; no workout-session side effects.`n`nExcluded: Flutter UI, plan editing, workout sessions/completion, Pump Check, Progress, Coach, and auth rollout changes.`n`nValidation: see the exact fresh commands and counts appended before PR creation."
```

The description must include Problem, Solution, Guarantees, Excluded, and exact
fresh Validation results from the original instruction.

- [ ] **Step 10: Verify exact-HEAD CI without merging**

Capture the pushed SHA, verify the PR head equals it, and inspect every required
job. Do not merge. If any exact-HEAD job fails, diagnose with
`superpowers:systematic-debugging`, fix through TDD, repush, and wait for the new
exact HEAD. The final report follows sections A–R from `mc-pr4a.txt` exactly.
