# Sprint 9 PR3A Diary Mutation Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add owner-scoped, stale-write-safe mobile slot mutation and reconciliation-based hard delete for current-day canonical diary entries.

**Architecture:** Extend the canonical diary projection with a stateless domain-separated HMAC revision, then resolve opaque entry IDs within the authenticated user's current-day ledger and lock the selected `MealLog` row before checking `If-Match`. Strict transport parsing exposes only an absolute `set_slot` command; hard delete returns 204 and relies on the canonical diary read after ambiguous transport outcomes.

**Tech Stack:** Python 3.11, Flask, Flask-SQLAlchemy/SQLAlchemy, PostgreSQL 16 concurrency tests, pytest, stdlib HMAC/struct/base64.

## Global Constraints

- Backend only; do not modify Flutter.
- No database migration, tombstone, soft delete, mutation journal, or durable mutation idempotency state.
- Only absolute slot moves and hard delete are supported.
- `@require_mobile_auth`, existing `DiaryItemId`, `If-Match`, and the existing mobile error envelope are mandatory.
- Manual content edits, all provider edits, provider replacement, and provider/manual conversion remain explicitly unsupported.
- Do not push, open a PR, merge, deploy, begin PR3B, or begin PR4.
- Preserve legacy web route behavior and CSRF/auth semantics.

---

### Task 1: Baseline and legacy mutation characterization

**Files:**
- Modify: `tests/test_nutrition_routes.py`
- Modify: `tests/test_ownership.py`

**Interfaces:**
- Consumes: existing `/api/diary/item/<int:item_id>` web PATCH/DELETE routes.
- Produces: characterization evidence that the additive mobile API may not alter.

- [ ] **Step 1: Run the authoritative baseline before production changes**

Run: `python -m pytest -q`

Expected: the complete collected non-load suite passes; record exact count and duration.

- [ ] **Step 2: Add characterization tests for accepted branches and logged-row rejection**

Add focused assertions equivalent to:

```python
def test_legacy_diary_mutations_keep_web_contract(client, meal_id):
    item_id = _add_item(client, meal_id)
    updated = client.patch(
        f"/api/diary/item/{item_id}", json={"grams": 50})
    assert updated.status_code == 200
    assert set(updated.get_json()) == {
        "item_id", "grams", "calories", "protein", "carbs", "fat"}
    deleted = client.delete(f"/api/diary/item/{item_id}")
    assert deleted.status_code == 200
    assert deleted.get_json() == {"deleted": True}
```

Also pin missing/cross-user as 404, `is_logged` as 400, cookie auth plus CSRF conventions, and transaction persistence using the existing fixtures.

- [ ] **Step 3: Run characterization tests**

Run: `python -m pytest tests/test_nutrition_routes.py tests/test_ownership.py -q`

Expected: PASS without production changes.

- [ ] **Step 4: Commit characterization evidence**

```powershell
git add tests/test_nutrition_routes.py tests/test_ownership.py
git commit -m "test(api): characterize diary mutation behavior"
```

### Task 2: Canonical opaque entry revision

**Files:**
- Create: `app/services/mobile_nutrition/revision.py`
- Create: `tests/test_mobile_nutrition_revision.py`
- Modify: `app/services/mobile_nutrition/__init__.py`

**Interfaces:**
- Consumes: a typed row snapshot carrying every persisted `MealLog` field.
- Produces: `diary_entry_revision(secret, state) -> str` and `matches_diary_entry_revision(secret, state, token) -> bool`.

- [ ] **Step 1: Write failing pure codec tests**

Cover determinism, owner/row binding, every material field changing the token,
null distinct from zero, normalized negative zero, timezone-naive microseconds,
non-finite historical floats, URL-safe output, and domain separation from
`diary_entry_id`.

```python
def test_every_material_field_changes_revision(state, app):
    original = diary_entry_revision(app.config["SECRET_KEY"], state)
    for field, replacement in MATERIAL_CHANGES.items():
        assert diary_entry_revision(
            app.config["SECRET_KEY"], replace(state, **{field: replacement})
        ) != original
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_mobile_nutrition_revision.py -q`

Expected: FAIL because the revision module does not exist.

- [ ] **Step 3: Implement the typed canonical encoder and HMAC**

Use a frozen `DiaryEntryRevisionState` dataclass and explicit encoders:

```python
_SUBKEY_INFO = b"axisai/mobile-nutrition/diary-entry-revision/v1"

def diary_entry_revision(secret, state):
    digest = hmac.new(_subkey(secret), _canonical_state(state), hashlib.sha256)
    return base64.urlsafe_b64encode(digest.digest()[:18]).decode("ascii")

def matches_diary_entry_revision(secret, state, token):
    return isinstance(token, str) and bool(token) and hmac.compare_digest(
        diary_entry_revision(secret, state), token)
```

Encode fixed-order typed fields with length-prefixed UTF-8, signed 64-bit
integers, normalized IEEE-754 binary64, explicit null tags, and fixed
microsecond datetime text. Do not use `repr` or dictionary JSON.

- [ ] **Step 4: Verify GREEN**

Run: `python -m pytest tests/test_mobile_nutrition_revision.py -q`

Expected: PASS.

- [ ] **Step 5: Commit revision codec**

```powershell
git add app/services/mobile_nutrition/revision.py app/services/mobile_nutrition/__init__.py tests/test_mobile_nutrition_revision.py
git commit -m "feat(api): add diary entry revisions"
```

### Task 3: Add revision to the canonical serializer

**Files:**
- Modify: `app/services/mobile_nutrition/queries.py`
- Modify: `app/services/mobile_nutrition/serialization.py`
- Modify: `app/services/mobile_nutrition/__init__.py`
- Modify: `app/services/mobile_log_food/service.py`
- Modify: `tests/test_mobile_nutrition_api.py`
- Modify: `tests/test_mobile_log_food_api.py`

**Interfaces:**
- Consumes: `DiaryEntryRevisionState` and the existing canonical `logged_meal` serializer.
- Produces: additive `meal["revision"]` in diary reads and LogFood responses.

- [ ] **Step 1: Write failing read and LogFood response tests**

```python
def test_every_entry_carries_an_opaque_revision(raw_client, as_mobile, mobile_user):
    row = log_meal(mobile_user)
    meal = read_diary(raw_client, as_mobile(mobile_user)).json["meals"][0]
    assert isinstance(meal["revision"], str) and meal["revision"]
    assert meal["revision"] != str(row.id)
```

Pin stability across unchanged reads and equality between a LogFood response
and the next canonical diary read.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_mobile_nutrition_api.py tests/test_mobile_log_food_api.py -q`

Expected: FAIL because `revision` is absent.

- [ ] **Step 3: Extend frozen query values and the single serializer**

Make `LedgerEntry` carry all fields needed by `DiaryEntryRevisionState`. Change
the serializer interface to accept both identity and revision callables:

```python
def logged_meal(entry, entry_id_for, revision_for):
    return {
        "id": entry_id_for(entry.entry_id),
        "revision": revision_for(entry),
        # existing canonical fields unchanged
    }
```

Adapt `build_diary_day` and `response_meal`; do not create a mutation-only DTO.

- [ ] **Step 4: Verify GREEN and query-count stability**

Run: `python -m pytest tests/test_mobile_nutrition_api.py tests/test_mobile_log_food_api.py -q`

Expected: PASS, including the existing constant query-count test.

- [ ] **Step 5: Commit serializer change**

```powershell
git add app/services/mobile_nutrition app/services/mobile_log_food/service.py tests/test_mobile_nutrition_api.py tests/test_mobile_log_food_api.py
git commit -m "feat(api): publish diary mutation revisions"
```

### Task 4: Strict precondition and slot-command parsing

**Files:**
- Create: `app/services/mobile_diary_mutation/commands.py`
- Create: `app/services/mobile_diary_mutation/preconditions.py`
- Create: `app/services/mobile_diary_mutation/__init__.py`
- Create: `tests/test_mobile_diary_mutation_parsing.py`

**Interfaces:**
- Produces: `SetSlotCommand(slot: str)`, `parse_mutation_command(data)`, and `parse_if_match(value) -> str`.

- [ ] **Step 1: Write failing strict parser tests**

Accept only `{operation: set_slot, slot: <canonical>}`. Reject missing/extra
fields, description, nutrition, quantity, serving, provider, food, user, day,
timestamp, source, fingerprint, and body revision. Accept only exactly one
strong quoted `If-Match`; reject missing distinctly from malformed, weak,
wildcard, list, unquoted, empty, whitespace, and overlong tokens.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_mobile_diary_mutation_parsing.py -q`

Expected: FAIL because the package is absent.

- [ ] **Step 3: Implement minimal typed parsers**

```python
@dataclass(frozen=True)
class SetSlotCommand:
    slot: str

def parse_mutation_command(data):
    if not isinstance(data, dict) or set(data) != {"operation", "slot"}:
        raise InvalidDiaryMutation
    if data["operation"] != "set_slot" or data["slot"] not in SLOT_LABELS:
        raise InvalidDiaryMutation
    return SetSlotCommand(data["slot"])
```

The precondition parser returns the inside of one strong quoted validator and
uses separate `MissingPrecondition` and `InvalidPrecondition` exceptions.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_mobile_diary_mutation_parsing.py -q`

```powershell
git add app/services/mobile_diary_mutation tests/test_mobile_diary_mutation_parsing.py
git commit -m "feat(api): define strict diary mutation commands"
```

### Task 5: Locked slot mutation service and mobile PATCH route

**Files:**
- Create: `app/services/mobile_diary_mutation/service.py`
- Modify: `app/blueprints/mobile_nutrition.py`
- Modify: `app/services/mobile_nutrition/queries.py`
- Create: `tests/test_mobile_diary_mutation_api.py`

**Interfaces:**
- Consumes: authenticated user ID, current server day, opaque DiaryItemId, parsed revision, and `SetSlotCommand`.
- Produces: `set_slot(...) -> MealLog`; raises `EntryNotFound` or `StaleDiaryEntry`.

- [ ] **Step 1: Write failing PATCH contract tests**

Cover Bearer required, cookie-only unauthorized, own/cross-user/malformed ID,
428 missing precondition, 400 malformed precondition, 412 stale, correct update,
same ID/new revision, old revision rejection, returned revision reuse,
same-slot idempotent success, invalid slot, all protected/unsupported fields,
current-day-only scope, diary reflection, unchanged totals, no sensitive logs,
and storage failure normalization.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_mobile_diary_mutation_api.py -q`

Expected: FAIL with route not found.

- [ ] **Step 3: Implement bounded identity resolution and locked mutation**

Resolve only IDs from `MealLog(user_id, tarih)`; then lock the selected row:

```python
row = (MealLog.query.filter_by(
    id=entry_id, user_id=user_id, tarih=day_key_value)
    .with_for_update().one_or_none())
if row is None:
    raise EntryNotFound
if not matches_diary_entry_revision(secret, state_from_row(row), revision):
    raise StaleDiaryEntry
row.ogun = SLOT_LABELS[command.slot]
db.session.commit()
return row
```

Revalidate under the lock. No provider I/O, mass assignment, or client day.

- [ ] **Step 4: Add the PATCH handler and bounded error mapping**

Use `@require_mobile_auth`, `parse_if_match`, strict JSON parsing, the existing
`mobile_error`, and the canonical response serializer. Roll back on unexpected
errors and log only event, exception type, and request ID.

- [ ] **Step 5: Verify GREEN and regressions**

Run: `python -m pytest tests/test_mobile_diary_mutation_api.py tests/test_mobile_nutrition_api.py tests/test_mobile_auth_feature_gate.py -q`

Expected: PASS.

- [ ] **Step 6: Commit slot mutation**

```powershell
git add app/blueprints/mobile_nutrition.py app/services/mobile_diary_mutation app/services/mobile_nutrition/queries.py tests/test_mobile_diary_mutation_api.py
git commit -m "feat(api): add stale-safe diary slot mutation"
```

### Task 6: Reconciliation-based hard delete

**Files:**
- Modify: `app/services/mobile_diary_mutation/service.py`
- Modify: `app/blueprints/mobile_nutrition.py`
- Modify: `tests/test_mobile_diary_mutation_api.py`

**Interfaces:**
- Produces: `delete_entry(...) -> None`, confirmed `204`, private `404` after absence.

- [ ] **Step 1: Write failing delete tests**

Cover own delete, stale revision, missing/malformed precondition, cross-user and
invalid ID, body rejection, diary absence, authoritative total reduction,
second delete `404`, and lost-response reconciliation through the diary GET.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_mobile_diary_mutation_api.py -q`

Expected: DELETE route is absent.

- [ ] **Step 3: Implement locked delete and route**

Reuse the exact resolution/lock/revision boundary from slot mutation, then:

```python
db.session.delete(row)
db.session.commit()
```

Return empty `204`. Do not make missing tokens universally successful.

- [ ] **Step 4: Verify GREEN and commit**

Run: `python -m pytest tests/test_mobile_diary_mutation_api.py tests/test_mobile_nutrition_api.py -q`

```powershell
git add app/blueprints/mobile_nutrition.py app/services/mobile_diary_mutation/service.py tests/test_mobile_diary_mutation_api.py
git commit -m "feat(api): add reliable mobile diary delete"
```

### Task 7: Deterministic PostgreSQL race closure

**Files:**
- Create: `tests/test_mobile_diary_mutation_pg.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: mutation service with independent Flask-SQLAlchemy sessions.
- Produces: deterministic slot/slot, slot/delete, delete/delete, and fresh revision race evidence.

- [ ] **Step 1: Write PostgreSQL barrier tests**

Create a disposable PostgreSQL fixture matching `test_mobile_log_food_pg.py`.
Have two daemon threads begin with the same revision and synchronize before
calling the real service. Assert:

```python
assert sorted(outcome.kind for outcome in outcomes) == ["ok", "stale"]
assert MealLog.query.one().ogun in {"Öğle", "Akşam"}
```

For update/delete allow exactly one winning mutation and a loser of stale or
private missing according to winner order; assert no resurrection. For
delete/delete assert one success, one missing, and zero rows. Add independent
cross-user operations and fresh-revision reuse.

- [ ] **Step 2: Verify RED against any lost-update gap**

Run with the already configured disposable `PG_TEST_DATABASE_URL`:
`$env:FITX_PG_CONCURRENCY_TEST='1'; python -m pytest tests/test_mobile_diary_mutation_pg.py -q`

Expected before complete locking: at least one new concurrency assertion fails.

- [ ] **Step 3: Make the minimal transaction-boundary corrections**

Keep `FOR UPDATE`, owner/day filters, and revision recheck in one transaction.
Do not add process locks or external calls.

- [ ] **Step 4: Verify GREEN and add CI coverage**

Add `tests/test_mobile_diary_mutation_pg.py` to the existing
`mobile-pg-concurrency` command. Run all three mobile PG suites.

- [ ] **Step 5: Commit concurrency closure**

```powershell
git add tests/test_mobile_diary_mutation_pg.py .github/workflows/ci.yml app/services/mobile_diary_mutation/service.py
git commit -m "test(api): lock diary mutation concurrency"
```

### Task 8: Architecture guards and contract documentation

**Files:**
- Create: `tests/test_mobile_diary_mutation_architecture.py`
- Modify: `docs/MOBILE_NUTRITION.md`
- Modify: `docs/handoff.md`

**Interfaces:**
- Produces: executable invariants and complete PR3B backend contract.

- [ ] **Step 1: Write failing non-vacuous architecture guards**

Assert target files exist before scanning. Pin mobile auth decorators, route
paths, `If-Match`, no generic `setattr`, no client user/day/timestamp fields,
distinct revision/identity domains, canonical serializer reuse, no heuristic
provider scaling, unchanged legacy route source, no migration file, and no
Flutter path in the backend diff.

- [ ] **Step 2: Verify RED, then add documentation**

Document the capability matrix, routes, exact headers/bodies/responses, error
codes/retryability, revision opacity, same-slot behavior, hard-delete ambiguity,
canonical reconciliation, concurrency, legacy compatibility, query/lock model,
provenance limitations, no-migration decision, and PR3B responsibilities.

- [ ] **Step 3: Verify focused documentation/guard suite**

Run: `python -m pytest tests/test_mobile_diary_mutation_architecture.py tests/test_mobile_diary_mutation_api.py tests/test_mobile_nutrition_api.py -q`

Expected: PASS.

- [ ] **Step 4: Commit docs and guards**

```powershell
git add tests/test_mobile_diary_mutation_architecture.py docs/MOBILE_NUTRITION.md docs/handoff.md
git commit -m "docs(api): document diary mutation contract"
```

### Task 9: Final validation and readiness audit

**Files:**
- Modify only if a validation failure proves an in-scope defect; every fix requires a new failing regression test first.

**Interfaces:**
- Produces: exact evidence for the required 28-section final report.

- [ ] **Step 1: Run focused suites**

Run mobile auth, diary read, LogFood, idempotency, mutation, ownership/security,
legacy nutrition, time utilities, migrations/schema, and architecture guards.

- [ ] **Step 2: Run PostgreSQL concurrency and schema drift**

Use the locally available disposable PostgreSQL service if reachable. Run all
mobile PG concurrency tests and `flask --app starter db check` against an
Alembic-upgraded disposable database. If unavailable, report the local skip and
retain CI wiring as a review condition; do not claim local PG success.

- [ ] **Step 3: Run the full backend suite**

Run: `python -m pytest -q`

Expected: all collected non-load tests pass.

- [ ] **Step 4: Audit diff and repository state**

Run `git diff --check origin/main...HEAD`, inspect `git status --short`, list
changed files and commits, scan for credentials/tokens and Flutter paths, verify
no migration was added, and confirm no push/PR/merge/deploy occurred.

- [ ] **Step 5: Apply verification-before-completion and report**

Report exact baseline-to-final counts, PostgreSQL evidence, query/lock findings,
files, commits, risks, PR3B readiness, and one verdict from the task contract.
