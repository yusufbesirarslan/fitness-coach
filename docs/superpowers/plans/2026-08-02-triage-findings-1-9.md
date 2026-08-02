# PR #194 Findings #1-#9 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and verify the nine concrete security, reliability, and correctness fixes from PR #194 without performing finding #10's broad module refactors.

**Architecture:** Reuse the existing `_ai_slots` semaphore as the single admission budget for synchronous external work, with monotonic fail-fast acquisition and route-specific fallbacks. Refactor mobile refresh into snapshot/network/locked-persist phases, then apply local fixes for authentication disclosure, proxy trust, plan parsing/bounds, stale workout linkage, and one absolute coach-turn deadline.

**Tech Stack:** Python 3, Flask, SQLAlchemy, Werkzeug `ProxyFix`, `threading.BoundedSemaphore`, pytest, Cognito/OpenAI/Bedrock adapters.

## Global Constraints

- Work only in `C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9` on `fix/triage-findings-1-9`; do not modify or rebase `fix/triage-2026-07-19`.
- Findings #1-#9 are in scope. Finding #10 is documentation-only in this branch.
- No database lock or transaction may remain open during Cognito or other gated external I/O.
- Refresh phase two locks family and parent, compares the original family/version snapshot, and discards external tokens when revalidation fails.
- Every wait/deadline uses `time.monotonic()` and one absolute deadline per operation.
- Shared permits wrap only blocking work, release in `finally`, are never acquired under a database transaction, and preserve `FITX_WEB_THREADS - (AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY) >= 2`.
- Acquisition uses exactly `AI_GATE_WAIT_SECONDS` (default `0.0`); overload is mobile versioned 503 plus `Retry-After: 15`, food HTTP 200 empty results, and meal suggestion HTTP 200 `accepted_no_calc` with no `MealLog`.
- Nested acquisition order is `shared blocking gate -> model gate`, never the reverse.
- Unconfirmed login is publicly identical to invalid credentials; logs contain no credential, token, username, or raw Cognito detail.
- Day `sure_dk` is inclusive `0..1440`; exercise `set` is inclusive `1..100`, independently, before persistence.
- Previous-day linkage rejection performs no write, completion, abandonment, or reassignment.
- One coach deadline exists before the first provider call; OpenAI gets `timeout=min(30.0, remaining)` and is not invoked without positive budget.
- Follow red-green-refactor. Pytest uses `--basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp`.

---

### Task 1: Monotonic shared and model gate primitives (#3 foundation)

**Files:**
- Modify: `app/services/ai_gate.py:25-70`
- Test: `tests/test_ai_gate.py:1-65`

**Interfaces:**
- Produces `BlockingConcurrencyLimit`, `blocking_concurrency_slot(wait_seconds=None)`, and `_acquire_before_deadline(semaphore, wait_seconds, clock=None)`.
- Changes `model_concurrency_slot(wait_seconds=None)` from unbounded to bounded.

- [ ] **Step 1: Write failing fail-fast, monotonic, release, and nesting tests**

```python
def test_blocking_slot_fails_fast_when_full(monkeypatch):
    sem = threading.BoundedSemaphore(1)
    assert sem.acquire(blocking=False)
    monkeypatch.setattr(ai_gate, "_ai_slots", sem)
    with pytest.raises(ai_gate.BlockingConcurrencyLimit):
        with ai_gate.blocking_concurrency_slot(0):
            pytest.fail("must not enter")
    sem.release()

def test_shared_then_model_nesting_releases_both(monkeypatch):
    monkeypatch.setattr(ai_gate, "_ai_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(ai_gate, "_model_slots", threading.BoundedSemaphore(1))
    for _ in range(2):
        with ai_gate.blocking_concurrency_slot(0):
            with ai_gate.model_concurrency_slot(0):
                pass
```

Use a recording semaphore and injected clock values `10.0, 10.25` to assert a 1-second budget passes only `0.75` seconds to `acquire`. Replace the existing indefinitely blocking model-slot test with a bounded exception assertion.

- [ ] **Step 2: Run `tests/test_ai_gate.py` and verify RED**

```powershell
python -m pytest tests/test_ai_gate.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
```

Expected: missing interfaces and the current unbounded model acquire fail the new assertions.

- [ ] **Step 3: Implement the minimal gate primitive**

```python
class BlockingConcurrencyLimit(RuntimeError):
    pass

def _acquire_before_deadline(semaphore, wait_seconds, *, clock=None):
    monotonic = clock or time.monotonic
    deadline = monotonic() + max(0.0, wait_seconds)
    return semaphore.acquire(timeout=max(0.0, deadline - monotonic()))

@contextmanager
def blocking_concurrency_slot(wait_seconds=None):
    wait = AI_GATE_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if not _acquire_before_deadline(_ai_slots, wait):
        raise BlockingConcurrencyLimit("shared blocking capacity exhausted")
    try:
        yield
    finally:
        _ai_slots.release()
```

Apply the same shape to `_model_slots`. Do not change the reserve invariant calculation.

- [ ] **Step 4: Re-run gate tests and commit GREEN**

```powershell
python -m pytest tests/test_ai_gate.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
git add app/services/ai_gate.py tests/test_ai_gate.py
git commit -m "fix: bound shared model concurrency waits"
```

### Task 2: Mobile admission and indistinguishable login (#2, #4)

**Files:**
- Modify: `app/services/mobile_auth.py:39-60,155-220`
- Modify: `app/blueprints/mobile_api.py:78-92`
- Modify: `app/mobile_auth_middleware.py:39-50`
- Test: `tests/test_mobile_auth_service.py:62-127`
- Test: `tests/test_mobile_auth_api.py:41-115`

**Interfaces:**
- `MobileAuthFailure` and `_failure` gain optional `retry_after`.
- Mobile login consumes Task 1's shared gate and translates saturation to the existing mobile envelope.

- [ ] **Step 1: Write failing enumeration and safe-log tests**

```python
with pytest.raises(MobileAuthFailure) as caught:
    mobile_auth.login("unconfirmed@example.test", "do-not-log-password")
assert (caught.value.code, caught.value.status, caught.value.retryable) == (
    "AUTH_INVALID_CREDENTIALS", 401, False)
assert "do-not-log-password" not in caplog.text
assert "unconfirmed@example.test" not in caplog.text
assert "raw cognito detail" not in caplog.text
assert "unconfirmed_account" in caplog.text
```

At API level compare the full error objects for unconfirmed and invalid credentials after replacing only their generated request ids.

- [ ] **Step 2: Write failing mobile saturation tests**

Saturate `_ai_slots`; assert login returns `AUTH_TEMPORARILY_UNAVAILABLE`, HTTP 503, `retryable: true`, `Retry-After: 15`, and neither `authenticate` nor conditional provider refresh is called.

- [ ] **Step 3: Run the two mobile modules and verify RED**

```powershell
python -m pytest tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
```

- [ ] **Step 4: Implement gated login, generic public failure, and retry header**

Hold one shared permit around authenticate/validate/optional provider refresh, exit before `_resolve_user` and ORM writes, and map saturation to:

```python
raise _failure("AUTH_TEMPORARILY_UNAVAILABLE", 503, True,
               "blocking_capacity_exhausted", retry_after=15)
```

Map `UserNotConfirmedException` to a safe internal `unconfirmed_account` event followed by `AUTH_INVALID_CREDENTIALS`/401. Pass `exc.retry_after` from `_run_issuance` into `mobile_error` and remove the unused verification-required safe message.

- [ ] **Step 5: Re-run mobile tests and commit GREEN**

```powershell
python -m pytest tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
git add app/services/mobile_auth.py app/blueprints/mobile_api.py app/mobile_auth_middleware.py tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py
git commit -m "fix: gate mobile login and hide verification state"
```

### Task 3: Two-phase mobile refresh and same-version race (#1, #2 refresh)

**Files:**
- Modify: `app/services/mobile_auth.py:339-550`
- Test: `tests/test_mobile_auth_service.py:164-492`

**Interfaces:**
- Add frozen internal `_RefreshSnapshot` and `_RenewedProviderTokens` dataclasses.
- Add snapshot, out-of-transaction renewal, and locked persist helpers used by `refresh`.

- [ ] **Step 1: Write a failing transaction-boundary test**

Force provider renewal and make the Cognito mock assert `db.session.in_transaction() is False`. Current code must fail because it calls Cognito after `with_for_update()`.

- [ ] **Step 2: Write the failing real two-request race**

Use two threads with separate app contexts and a `threading.Barrier(2)` in the provider mock so both snapshot family version 1. Return distinguishable tokens. Assert exactly one generation-2 refresh child/access row, family version 2, winner provider token retained, no loser overwrite/partial rows, and loser follows replay-safe or existing typed stale/conflict behavior.

- [ ] **Step 3: Run the two node ids and verify RED**

Use `pytest -vv` for the boundary and race node ids. Record the expected in-transaction assertion/race failure before production edits.

- [ ] **Step 4: Implement snapshot and end phase-one transaction**

Copy scalar family/parent ids, public id, original version, generation/state, expiries, encrypted provider token, username, and subject into the frozen snapshot. Explicitly rollback/end the session before returning it. Keep consumed-parent replay as a no-network early path.

- [ ] **Step 5: Implement gated provider renewal outside the transaction**

Only when the snapshot requires coverage, enter `blocking_concurrency_slot`, decrypt/call/validate/encrypt, release in `finally`, and return prepared values. Confirm no active SQLAlchemy transaction immediately before Cognito. Translate saturation to the mobile 503 with retry-after 15.

- [ ] **Step 6: Implement locked phase-two revalidation**

Lock family and parent, repeat hash/revocation/expiry/consumption/generation checks, compare family id/version with the snapshot, and apply prepared tokens only while current. Preserve the optimistic version update. A consumed parent replays the committed child; any other stale snapshot returns the existing conflict contract. Revalidate before definitive-failure revocation so a stale loser cannot revoke a winner.

- [ ] **Step 7: Run all refresh/API/migration tests and commit GREEN**

```powershell
python -m pytest tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py tests/test_mobile_auth_migration.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
git add app/services/mobile_auth.py tests/test_mobile_auth_service.py
git commit -m "fix: rotate mobile refresh tokens in two phases"
```

### Task 4: Gate food and suggestion work without DB-held waits (#3 routes)

**Files:**
- Modify: `app/blueprints/food.py:22-50`
- Modify: `app/blueprints/social.py:881-1033`
- Test: `tests/test_food_routes.py`
- Test: `tests/test_social_routes.py:288-470`
- Test: `tests/test_ai_gate.py`

**Interfaces:**
- Consume `blocking_concurrency_slot`/`BlockingConcurrencyLimit`.
- Split suggestion calculation from ORM persistence with `_calculate_meal_suggestion(snapshot)` and `_persist_meal_suggestion(snapshot, nutrients)`.

- [ ] **Step 1: Write failing food saturation/cache tests**

Saturate `_ai_slots`. A cache miss must return HTTP 200 `{"results": []}` without calling `_coach_search_food`; a cache hit must still return cached data, proving no permit is acquired for local work.

- [ ] **Step 2: Write failing suggestion saturation/no-write tests**

For an uncached accepted meal suggestion under saturation, assert HTTP 200 `accepted_no_calc`, accepted message state, no `MealLog`, and no FatSecret/LLM call. Under the same saturation, decline and workout acceptance must succeed. Instrument gate entry and external helpers to assert `db.session.in_transaction() is False`.

- [ ] **Step 3: Run food/social tests and verify RED**

```powershell
python -m pytest tests/test_food_routes.py tests/test_social_routes.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
```

- [ ] **Step 4: Gate only a food cache miss**

```python
try:
    with blocking_concurrency_slot():
        results = _coach_search_food(q)
except BlockingConcurrencyLimit:
    current_app.logger.warning("food_search event=blocking_capacity_exhausted")
    results = []
return jsonify({"results": results})
```

- [ ] **Step 5: Isolate suggestion calculation from persistence**

Read/validate and copy ids, body, type, and safe sender display data; end the read transaction. Parse/cache locally. Gate only uncached FatSecret/LLM calculation and release before a new conditional-update transaction. On saturation use `nutrients=None`. Re-open the short transaction, preserve the single-winner conditional update, create the reply, and add `MealLog` only for positive calculated nutrients.

- [ ] **Step 6: Add a reserve/nesting saturation test**

Occupy all shared slots with shared->model workers. Start food, suggestion, and mobile attempts; assert all return immediately via their own contracts, providers are untouched, and two cheap synthetic workers execute. Release holders and prove subsequent shared->model acquisition succeeds without leakage/deadlock.

- [ ] **Step 7: Run affected tests and commit GREEN**

```powershell
python -m pytest tests/test_ai_gate.py tests/test_food_routes.py tests/test_social_routes.py tests/test_mobile_auth_api.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
git add app/blueprints/food.py app/blueprints/social.py tests/test_food_routes.py tests/test_social_routes.py tests/test_ai_gate.py
git commit -m "fix: share blocking capacity across fallback routes"
```

### Task 5: Narrow ProxyFix trust (#5)

**Files:**
- Modify: `app/config.py:298`
- Test: `tests/test_health.py`

**Interfaces:** `ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0)`.

- [ ] **Step 1: Write the failing hostile-host regression**

Expose `request.host`, `remote_addr`, `scheme`, and an external URL in a test route. Send direct `Host: fitness.test`, trusted forwarded-for/proto, and hostile `X-Forwarded-Host: evil.test`, `X-Forwarded-Port: 4444`. Assert host/external URL retain `fitness.test`; address and HTTPS still use one trusted proxy hop.

- [ ] **Step 2: Run the new node and verify RED**

Expected: current config trusts `evil.test:4444`.

- [ ] **Step 3: Set host/port trust to zero, verify, and commit**

```powershell
python -m pytest tests/test_health.py tests/test_extensions.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
git add app/config.py tests/test_health.py
git commit -m "fix: stop trusting forwarded host and port"
```

### Task 6: Accept wrapped plan facts (#6)

**Files:**
- Modify: `app/services/plan_facts.py:97-114`
- Test: `tests/test_plan_v2.py:144-180`

**Interfaces:** `_parse_plan_days` unwraps dict `program` before requiring a non-empty list.

- [ ] **Step 1: Write failing wrapped/malformed tests**

```python
def test_facts_accepts_program_wrapper(app, make_user):
    from app.services.plan_facts import gather_plan_facts
    user = make_user("wrapped_valid", profile_complete=True)
    _seed_plan(user.id, json.dumps({"program": json.loads(_VALID_PLAN)}))
    facts = gather_plan_facts(user.id)
    assert facts.has_active_plan is True and facts.parse_ok is True

@pytest.mark.parametrize("payload", [{}, {"program": {}}, {"program": []}])
def test_bad_wrapper_remains_partial(app, make_user, payload):
    from app.services.plan_facts import gather_plan_facts
    user = make_user("wrapped_partial")
    _seed_plan(user.id, json.dumps(payload))
    facts = gather_plan_facts(user.id)
    assert facts.has_active_plan is True and facts.parse_ok is False
```

- [ ] **Step 2: Verify RED, implement minimal unwrap, verify GREEN**

```python
if isinstance(parsed, dict):
    parsed = parsed.get("program")
if not isinstance(parsed, list) or not parsed:
    return False, ()
```

Run `tests/test_plan_v2.py` plus workout-state/session plan parser modules.

- [ ] **Step 3: Commit the parser fix**

```powershell
git add app/services/plan_facts.py tests/test_plan_v2.py
git commit -m "fix: parse wrapped plan facts"
```

### Task 7: Align generated plan bounds before persistence (#7)

**Files:**
- Modify: `app/services/training_generation/response_validator.py:35-85`
- Test: `tests/test_training_generation.py`
- Test: `tests/test_workout_convergence.py`

**Interfaces:** `sure_dk` clamps to `0..1440`; `set` clamps to `1..100`, independently; valid zero is not replaced by a truthiness default.

- [ ] **Step 1: Write failing independent boundary tests**

```python
@pytest.mark.parametrize("raw, expected", [(-1, 0), (0, 0), (1440, 1440), (1441, 1440)])
def test_sure_dk_bounds(raw, expected):
    preferences = TrainingPreferences(gun_sayisi=1, sure=45)
    generated = _valid_generated_plan()
    generated["program"][0]["sure_dk"] = raw
    generated["program"][0]["egzersizler"][0]["set"] = 3
    validated, _ = validate_generated_plan(generated, preferences)
    assert validated["program"][0]["sure_dk"] == expected
    assert validated["program"][0]["egzersizler"][0]["set"] == 3

@pytest.mark.parametrize("raw, expected", [(0, 1), (1, 1), (100, 100), (101, 100)])
def test_set_bounds(raw, expected):
    preferences = TrainingPreferences(gun_sayisi=1, sure=45)
    generated = _valid_generated_plan()
    generated["program"][0]["sure_dk"] = 45
    generated["program"][0]["egzersizler"][0]["set"] = raw
    validated, _ = validate_generated_plan(generated, preferences)
    assert validated["program"][0]["sure_dk"] == 45
    assert validated["program"][0]["egzersizler"][0]["set"] == expected
```

Feed each validated result to `serialize_plan` and assert no serialization failure.

- [ ] **Step 2: Run tests and verify RED**

Expected: negative/oversized values pass through and `sure_dk=0` is replaced by the default.

- [ ] **Step 3: Implement explicit clamps and verify GREEN**

```python
def _bounded_int(value, default, low, high):
    parsed = _to_int(value, default)
    return max(low, min(high, parsed))

day["sure_dk"] = _bounded_int(day.get("sure_dk"), default_duration, 0, 1440)
ex["set"] = _bounded_int(ex.get("set"), 1, 1, 100)
```

Run training-generation, workout-state serialization, and bootstrap tests.

- [ ] **Step 4: Commit the bounds fix**

```powershell
git add app/services/training_generation/response_validator.py tests/test_training_generation.py tests/test_workout_convergence.py
git commit -m "fix: bound generated workout fields"
```

### Task 8: Reject previous-day completion linkage read-only (#8)

**Files:**
- Modify: `app/services/workout_session/service.py:271-310`
- Modify: call site in `app/blueprints/training.py`
- Test: `tests/test_workout_session.py:390-445`
- Test: `tests/test_workout_completion.py`

**Interfaces:** `resolve_for_completion(user_id, public_id, today)` returns `STALE_SESSION_REQUIRES_RESOLUTION` when stored ISO date differs from `today.isoformat()`.

- [ ] **Step 1: Write failing service and route no-mutation tests**

Seed yesterday's ACTIVE session; snapshot status/date/public id and all session/completion artifact counts. Resolve and submit today's completion. After `expire_all`, assert stale outcome, unchanged old row, no new/reassigned session, and no PumpCheck/marker/XP/activity persistence.

- [ ] **Step 2: Run focused tests and verify RED**

Expected: resolver returns yesterday's id and the completion path marks it completed.

- [ ] **Step 3: Thread authoritative day into resolver and verify GREEN**

Compare stored ISO date before returning an id. Use the caller's already-computed day; do not independently call `app_today()` inside the resolver.

```powershell
python -m pytest tests/test_workout_session.py tests/test_workout_completion.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
```

- [ ] **Step 4: Commit the stale-link fix**

```powershell
git add app/services/workout_session/service.py app/blueprints/training.py tests/test_workout_session.py tests/test_workout_completion.py
git commit -m "fix: reject stale workout completion linkage"
```

### Task 9: Share one absolute coach-turn deadline (#9)

**Files:**
- Modify: `app/services/ai_coach.py:932-1145`
- Test: `tests/test_ai_coach.py:397-525`

**Interfaces:** OpenAI/Bedrock helpers accept optional `deadline`; orchestration creates one before provider selection; every OpenAI call gets `timeout=min(30.0, remaining)`.

- [ ] **Step 1: Write failing budget/fallback tests**

With a controlled monotonic clock, exhaust the budget in Bedrock and assert OpenAI call count is zero. With 7.5 seconds remaining, assert `openai_create.call_args.kwargs["timeout"] == pytest.approx(7.5)`. Add a multi-round OpenAI test proving timeouts decrease from one deadline.

- [ ] **Step 2: Run coach tests and verify RED**

Expected: fallback resets the budget and OpenAI calls omit per-call timeout.

- [ ] **Step 3: Create and propagate one deadline**

```python
deadline = _coach_turn_deadline()
return _run_coach_conversation_bedrock(
    user_id, question, context, history, language, deadline=deadline)
```

Direct helper tests may default `deadline` once; normal orchestration always supplies it.

- [ ] **Step 4: Guard every retry/fallback and pass timeout**

```python
remaining = _remaining_coach_turn_seconds(deadline)
if remaining <= 0:
    return _tool_error_response(language)
response = openai_client.chat.completions.create(
    model=OPENAI_MODEL, messages=messages, tools=COACH_TOOLS,
    tool_choice="auto", max_tokens=700, temperature=0.6,
    timeout=min(30.0, remaining))
```

Do not catch deadline exhaustion and invoke another provider. Preserve localized fallback text.

- [ ] **Step 5: Verify and commit GREEN**

```powershell
python -m pytest tests/test_ai_coach.py tests/test_ai_gate.py tests/test_coach_routes.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
git add app/services/ai_coach.py tests/test_ai_coach.py
git commit -m "fix: enforce one coach turn deadline"
```

### Task 10: Focused and full verification

**Files:** Verify all changed files; no finding #10 production changes.

**Interfaces:** No new production interface.

- [ ] **Step 1: Run the focused regression set**

```powershell
python -m pytest tests/test_ai_gate.py tests/test_mobile_auth_service.py tests/test_mobile_auth_api.py tests/test_mobile_auth_migration.py tests/test_food_routes.py tests/test_social_routes.py tests/test_health.py tests/test_extensions.py tests/test_plan_v2.py tests/test_training_generation.py tests/test_workout_session.py tests/test_workout_completion.py tests/test_ai_coach.py tests/test_coach_routes.py -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
```

- [ ] **Step 2: Inspect diff and static state**

```powershell
git diff --check
git status --short
```

Check for secrets/raw Cognito details, reversed semaphore order, network calls inside transactions, broad refactors, and finding #10 scope leakage.

- [ ] **Step 3: Run the full suite**

```powershell
python -m pytest -q --basetemp=C:\Users\yusuf\fitness-coach\.worktrees\triage-findings-1-9\.pytest_cache\codex-basetemp
```

Expected: prior baseline `2742 passed, 5 skipped, 3 deselected` plus new regressions, zero setup errors, zero failures.

- [ ] **Step 4: Confirm branch history and clean worktree**

```powershell
git log --oneline --decorate origin/main..HEAD
git status --short --branch
```

- [ ] **Step 5: Prepare final report**

Report each finding's behavior, exact test counts, deviations, branch/commit state, and finding #10's separate sequence: `ai_coach.py` provider/tool extraction, then `social.py` domain split, then `tracking.py` domain split, each behavior-preserving after #1-#9.
