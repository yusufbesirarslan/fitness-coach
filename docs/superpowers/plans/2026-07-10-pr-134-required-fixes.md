# PR #134 Required Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all fifteen actionable findings from PR #134 with one independently tested and committed change per behavior area.

**Architecture:** Preserve current Flask blueprint and service boundaries. Add atomic database claims where correctness depends on concurrency, separate request admission from provider concurrency, centralize macro sanitation, and keep all external-service failures fail-closed or gracefully recoverable.

**Tech Stack:** Python 3.14, Flask 3.1, SQLAlchemy 2.0, Flask-Limiter 4.1, Redis sorted sets, cryptography Fernet, joserfc/Authlib, pytest.

## Global Constraints

- Work only in the isolated `codex/pr-134-fixes` worktree based on merged `origin/main`.
- Preserve Turkish UI copy and English code identifiers.
- Scope every database operation to the authenticated user's ID where applicable.
- Follow test-driven development: write and run a focused failing test before production code.
- Keep each task in a separate short commit and do not edit `FIXES_NEEDED.md`.
- Do not add a schema migration; existing columns and `User.user_metadata` are sufficient.
- Preserve existing HTTP status codes and translated errors unless this plan explicitly changes them.
- Never log tokens, JWT bodies, referral codes, or upstream credentials.
- Run the focused test file after each task; run the full suite after all tasks.

---

### Task 1: Atomically claim diary meals

**Files:**
- Modify: `app/blueprints/nutrition/diary.py`
- Test: `tests/test_nutrition_routes.py`

**Interfaces:**
- Consumes: `CustomMeal.id`, `CustomMeal.user_id`, `CustomMeal.is_logged`.
- Produces: exactly one canonical `MealLog` for a diary meal.

- [ ] **Step 1: Write the failing test**

Add `_claim_diary_meal` to the test import before it exists, then call it twice against the same row and assert results `1` then `0`. Keep the route-level single-ledger assertion as the integration check.

```python
def test_claim_diary_meal_is_single_use(auth_user, meal_id):
    assert _claim_diary_meal(meal_id, auth_user.id) == 1
    assert _claim_diary_meal(meal_id, auth_user.id) == 0

def test_diary_log_meal_claim_keeps_one_ledger_row(client, auth_user, meal_id):
    client.post(f"/api/diary/meal/{meal_id}/item", json={
        "food_name": "pirinç", "grams": 100,
        "per_100g": {"calories": 130, "protein": 2.7, "carbs": 28, "fat": 0.3},
    })
    assert client.post(f"/api/diary/meal/{meal_id}/log").status_code == 200
    assert client.post(f"/api/diary/meal/{meal_id}/log").status_code == 400
    assert MealLog.query.filter_by(user_id=auth_user.id, source="diary").count() == 1
```

- [ ] **Step 2: Run test to verify it fails for the missing guarded claim**

Run: `python -m pytest tests/test_nutrition_routes.py::test_diary_log_meal_claim_is_single_use -v`

Expected: collection fails because `_claim_diary_meal` does not exist.

- [ ] **Step 3: Implement the guarded update**

Add the helper and call it immediately before building the `MealLog`:

```python
def _claim_diary_meal(meal_id, user_id):
    return CustomMeal.query.filter_by(
        id=meal_id, user_id=user_id, is_logged=False,
    ).update({"is_logged": True}, synchronize_session=False)

claimed = _claim_diary_meal(meal_id, current_user.id)
if not claimed:
    db.session.rollback()
    return jsonify({"error": t("route.meal_already_logged")}), 400
```

Remove the later `meal.is_logged = True`; keep the update and insert in the same commit.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_nutrition_routes.py -q`

- [ ] **Step 5: Commit**

```text
fix: atomically log diary meals
```

### Task 2: Bound model calls and fail fast at route admission

**Files:**
- Modify: `app/services/ai_gate.py`
- Modify: `app/services/ai.py`
- Test: `tests/test_ai_gate.py`

**Interfaces:**
- Produces: `model_concurrency_slot()` context manager.
- `AI_GATE_WAIT_SECONDS` defaults to `0`; `AI_MODEL_MAX_CONCURRENCY` defaults to `AI_MAX_CONCURRENCY`.

- [ ] **Step 1: Write failing concurrency tests**

Add tests asserting the default wait is zero and two threads cannot simultaneously enter a one-slot model context. Also assert a raised exception releases the slot.

```python
def test_route_gate_defaults_to_fail_fast():
    assert ai_gate.AI_GATE_WAIT_SECONDS == 0

def test_model_slot_releases_after_error(monkeypatch):
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(ai_gate, "_model_slots", sem)
    with pytest.raises(RuntimeError):
        with ai_gate.model_concurrency_slot():
            raise RuntimeError("boom")
    assert sem.acquire(blocking=False)
    sem.release()
```

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_ai_gate.py -v`

- [ ] **Step 3: Implement separate semaphores**

In `ai_gate.py`, add `AI_MODEL_MAX_CONCURRENCY`, `_model_slots`, and a context manager that acquires indefinitely inside admitted work and always releases. Change the route wait default from `10` to `0` and update the module documentation.

Wrap the full `_heavy_chat` provider-selection/fallback body and `_bedrock_validate_image` provider call with `model_concurrency_slot()` in `ai.py`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_ai_gate.py tests/test_ai_nutrition_llm.py -q`

- [ ] **Step 5: Commit**

```text
fix: bound concurrent model calls
```

### Task 3: Add default and social write limits

**Files:**
- Modify: `app/config.py`
- Modify: `app/extensions.py`
- Modify: `app/blueprints/social.py`
- Test: `tests/test_extensions.py`
- Test: `tests/test_social_routes.py`

**Interfaces:**
- Produces config constants `DEFAULT_RATELIMIT`, `FRIEND_REQUEST_RATELIMIT`, `CHAT_SEND_RATELIMIT`, `SUGGESTION_RATELIMIT`.

- [ ] **Step 1: Write failing limit tests**

Assert the limiter's default limits include `600 per hour` and, with limiter enabled, 21 friend requests from one user cause a 429 no later than the twenty-first allowed boundary.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_extensions.py tests/test_social_routes.py -k rate_limit -v`

- [ ] **Step 3: Implement limits**

```python
DEFAULT_RATELIMIT = os.getenv("DEFAULT_RATELIMIT", "600 per hour")
FRIEND_REQUEST_RATELIMIT = os.getenv("FRIEND_REQUEST_RATELIMIT", "20 per hour")
CHAT_SEND_RATELIMIT = os.getenv("CHAT_SEND_RATELIMIT", "60 per minute; 600 per hour")
SUGGESTION_RATELIMIT = os.getenv("SUGGESTION_RATELIMIT", "30 per hour")
```

Pass `default_limits=[DEFAULT_RATELIMIT]` to `Limiter`. Decorate the three social routes with their constants and `key_func=_user_or_ip_key`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_extensions.py tests/test_social_routes.py -q`

- [ ] **Step 5: Commit**

```text
fix: throttle authenticated writes
```

### Task 4: Reserve AI quota atomically

**Files:**
- Modify: `app/services/premium.py`
- Modify: `app/blueprints/coach.py`
- Test: `tests/test_premium_quota.py`
- Test: `tests/test_coach_routes.py`

**Interfaces:**
- Produces: `reserve_ai_quota(user, counter_key, limit) -> bool` and `refund_ai_quota(user, counter_key) -> None`.

- [ ] **Step 1: Write failing reservation tests**

Test that the first reservation succeeds, a second stale user object cannot reserve the last allowance, a failed plan response refunds, and a coach fallback refunds the chat reservation.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_premium_quota.py tests/test_coach_routes.py -k quota -v`

- [ ] **Step 3: Implement reservation/refund**

Under `with_for_update`, read `User.user_metadata` as a column, normalize the current week, and commit the increment before returning `True`. Return `False` without increment when used is at the limit. Refund under the same lock with `max(used - 1, 0)`.

Update `premium_ai_plan_gate` to reserve before calling the route and refund for non-200 responses or exceptions. Update `/ask` to reserve after validation and refund for `is_coach_error_fallback(answer)` or exceptions.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_premium_quota.py tests/test_coach_routes.py -q`

- [ ] **Step 5: Commit**

```text
fix: reserve AI quota atomically
```

### Task 5: Recover from corrupt wearable tokens

**Files:**
- Modify: `app/services/wearables/tokens.py`
- Modify: `app/blueprints/wearables.py`
- Test: `tests/test_wearables.py`

**Interfaces:**
- Corrupt ciphertext returns no connection and persists `status="reauth_required"`.

- [ ] **Step 1: Write failing tests**

Create a connection row with invalid Fernet ciphertext, call `get_wearable_connection`, and assert it returns `None` and persists the status. Assert `/api/wearables/status` exposes the status.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_wearables.py -k corrupt -v`

- [ ] **Step 3: Implement InvalidToken handling**

Import `InvalidToken`, catch it around both decryptions, set `row.status`, commit, and return `None`. Add `status` to the existing status payload.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_wearables.py -q`

- [ ] **Step 5: Commit**

```text
fix: require wearable reauthentication
```

### Task 6: Retry truncated training plans

**Files:**
- Modify: `app/services/training_generation/service.py`
- Test: `tests/test_training_generation.py`

**Interfaces:**
- First call uses 4,000 tokens; one retry uses 7,000 tokens and a compact-JSON suffix.

- [ ] **Step 1: Write failing retry tests**

Use a fake `chat_fn` that returns truncated JSON first and a valid seven-day plan second. Assert two calls and token limits `[4000, 7000]`. Add valid-first and invalid-twice cases.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_training_generation.py -k retry -v`

- [ ] **Step 3: Implement one retry**

Extract a `_request_and_validate(max_tokens, prompt)` local helper. Catch `(json.JSONDecodeError, PlanValidationError)` from the first attempt, log a warning without raw model output, and retry once with `prompt + "\nYanıtı kısa tut ve yalnızca eksiksiz JSON döndür."` and `max_tokens=7000`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_training_generation.py tests/test_premium_quota.py -q`

- [ ] **Step 5: Commit**

```text
fix: retry truncated training plans
```

### Task 7: Verify Cognito ID tokens

**Files:**
- Modify: `app/services/cognito_service.py`
- Replace: `app/services/cognito_idp.py`
- Test: `tests/test_cognito_idp.py`

**Interfaces:**
- `_decode_claims(id_token)` now verifies RS256, issuer, audience, expiry, token use, and subject.
- `cognito_idp.py` re-exports the canonical service API.

- [ ] **Step 1: Write failing cryptographic tests**

Generate an RSA key in the test, export a public JWK with a `kid`, sign a valid Cognito ID token, and monkeypatch the JWKS loader. Assert valid claims pass while a forged signature, wrong issuer, wrong audience, expired token, and `token_use="access"` return `{}` or cause `initiate_auth` to raise `CognitoServiceError`.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_cognito_idp.py -k "signature or issuer or audience or expired or token_use" -v`

- [ ] **Step 3: Implement verifier and cache**

Use `joserfc.jwt.decode` with `KeySet.import_key_set`, algorithms `{"RS256"}`, and `JWTClaimsRegistry` requiring exact `iss`, `aud`, `token_use`, `exp`, and `sub`. Fetch `https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json` with `requests.get(..., timeout=5)`, cache for six hours, and force one refresh on decode/key failure. Normalize only the existing four returned claims.

Replace duplicate implementation with imports from `cognito_service` and an explicit `__all__`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_cognito_idp.py tests/test_auth.py -q`

- [ ] **Step 5: Commit**

```text
fix: verify Cognito ID tokens
```

### Task 8: Sanitize negative and Atwater-invalid macros

**Files:**
- Modify: `nutrition_pipeline.py`
- Test: `tests/test_nutrition_pipeline.py`
- Test: `tests/test_nutrition_routes.py`

**Interfaces:**
- `clamp_serving_macros` always returns non-negative values and corrects declared calories above supported macro energy.

- [ ] **Step 1: Write failing sanitation tests**

```python
def test_clamp_floors_negative_values():
    assert clamp_serving_macros(-10, -2, 4, -1) == (0, 0, 4, 0)

def test_clamp_corrects_atwater_only_violation():
    assert clamp_serving_macros(2000, 0, 0, 0) == (0.0, 0, 0, 0)
```

Add route tests showing negative override and quick-add plan macros produce 200 responses with zero floors rather than database errors.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_nutrition_pipeline.py tests/test_nutrition_routes.py -k "negative or atwater" -v`

- [ ] **Step 3: Implement canonical sanitation**

Coerce each input through `_num` and `max(value, 0)`. Apply existing absolute-cap scaling. Re-check the result; when `calories_exceed_macro_energy` remains, set calories to `round(4*protein + 4*carbs + 9*fat, 1)`. Return sanitized values even when no absolute ratio exists.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_nutrition_pipeline.py tests/test_nutrition_routes.py -q`

- [ ] **Step 5: Commit**

```text
fix: sanitize serving macros
```

### Task 9: Batch FatSecret serving-weight estimation

**Files:**
- Modify: `app/services/fatsecret.py`
- Test: `tests/test_fatsecret.py`

**Interfaces:**
- `_food_search_fatsecret` calls `_estimate_serving_weights_llm` at most once per search.

- [ ] **Step 1: Write failing batch test**

Return several per-serving FatSecret foods, record estimator calls, and assert one call containing all unique names in result order. Assert scaled output and fallback caching semantics.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_fatsecret.py -k batch_serving -v`

- [ ] **Step 3: Refactor into parse then build phases**

Parse eligible foods into `(food, parsed, macros, serving_text, is_serving)` records. Collect ordered unique per-serving names and make one estimator call with `return_fallbacks=True`. Reuse the result dictionaries in the existing result-building loop without changing ordering, validation, or cache rules.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_fatsecret.py tests/test_fatsecret_lookup.py -q`

- [ ] **Step 5: Commit**

```text
perf: batch serving weight estimates
```

### Task 10: Make Redis leaderboard ties deterministic

**Files:**
- Modify: `app/services/gamification.py`
- Modify: `tests/test_leaderboard_redis.py`

**Interfaces:**
- Global Redis order and off-list rank match PostgreSQL: score descending, numeric user ID ascending.

- [ ] **Step 1: Make FakeRedis reproduce real Redis ties and add failing test**

Change the fake's equal-score order to reverse lexicographic member order. Add tied IDs including `2` and `10`, assert numeric ascending order and exact rank for a tied user outside top N.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_leaderboard_redis.py -k tie -v`

- [ ] **Step 3: Implement application tie resolution**

Fetch global candidates with scores, fetch all members at the cutoff score, combine and sort `key=lambda item: (-score, int(member))`, then slice. Calculate off-list rank as strictly-higher count plus equal-score IDs lower than the current ID plus one. Keep the existing friends sort.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_leaderboard_redis.py tests/test_gamification_routes.py -q`

- [ ] **Step 5: Commit**

```text
fix: stabilize Redis leaderboard ties
```

### Task 11: Purge all newer child tables

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_remaining_work.py`

**Interfaces:**
- `_purge_user` deletes pump-check children before pump checks and wearable rows before users.

- [ ] **Step 1: Write failing CLI test**

Create `PumpCheckLike`, `PumpCheckComment`, `UserWearableConnection`, `WearableSleepLog`, `WearableActivityLog`, and `WearableWorkoutLog` rows for the victim. Invoke cleanup and assert every model count is zero.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_remaining_work.py -k newer_dependents -v`

- [ ] **Step 3: Extend explicit deletion order**

Import the six models. Delete likes/comments filtered by `user_id` before deleting pump checks; delete wearable logs and connections in the main user-scoped loop before deleting the user.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_remaining_work.py tests/test_cascade_delete.py -q`

- [ ] **Step 5: Commit**

```text
fix: purge wearable and pump rows
```

### Task 12: Defer Cognito referral rewards

**Files:**
- Modify: `app/blueprints/auth.py`
- Test: `tests/test_auth.py`
- Test: `tests/test_referral.py`

**Interfaces:**
- Cognito registration stores `pending_referral_code`; verification consumes and clears it.
- Local registration behavior stays immediate.

- [ ] **Step 1: Write failing Cognito referral flow test**

Enable Cognito with monkeypatches, register using a real referrer code, assert neither user receives XP and metadata contains the pending code. Verify the account, assert both receive exactly 75 XP, the relationship is set, and the pending key is removed.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_auth.py tests/test_referral.py -k cognito_referral -v`

- [ ] **Step 3: Persist and consume pending referral**

At Cognito user creation, merge `{"pending_referral_code": ref_code}` into metadata only when non-empty and do not call `consume_referral`. In `verify_confirm`, after successful Cognito confirmation, load the local user by username, consume the pending code, reload metadata, remove the pending key with `flag_modified`, commit, and return `referred=bool(referrer)`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_auth.py tests/test_referral.py tests/test_remaining_work.py -q`

- [ ] **Step 5: Commit**

```text
fix: defer Cognito referral rewards
```

### Task 13: Allow-list WHOOP proxy parameters

**Files:**
- Modify: `app/blueprints/wearables.py`
- Test: `tests/test_wearables.py`

**Interfaces:**
- Profile/body forward no query parameters.
- Recovery/sleep/workout forward only `start`, `end`, and validated `limit` from 1 to 25.

- [ ] **Step 1: Write failing proxy parameter tests**

Monkeypatch the WHOOP adapter, call each resource with allowed and injected parameters, and assert the captured params omit arbitrary keys. Assert `limit=0`, `limit=26`, and non-integer limits return 400.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_wearables.py -k whoop_resource_params -v`

- [ ] **Step 3: Implement resource-specific filtering**

Define an immutable mapping with empty sets for profile/body and `{"start", "end", "limit"}` for recovery/sleep/workout. Copy only allowed keys. Reject invalid limits; truncate or reject start/end strings longer than 64 characters without echoing them.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_wearables.py -q`

- [ ] **Step 5: Commit**

```text
fix: filter WHOOP query parameters
```

### Task 14: Whole-branch verification

**Files:**
- Review: all files changed since `f576000`.

- [ ] **Step 1: Run affected suites together**

Run:

```text
python -m pytest tests/test_ai_gate.py tests/test_ai_nutrition_llm.py tests/test_extensions.py tests/test_social_routes.py tests/test_premium_quota.py tests/test_coach_routes.py tests/test_wearables.py tests/test_training_generation.py tests/test_cognito_idp.py tests/test_auth.py tests/test_nutrition_pipeline.py tests/test_nutrition_routes.py tests/test_fatsecret.py tests/test_fatsecret_lookup.py tests/test_leaderboard_redis.py tests/test_gamification_routes.py tests/test_remaining_work.py tests/test_cascade_delete.py -q
```

- [ ] **Step 2: Run the full suite**

Run: `python -m pytest -q`

Expected: at least the 1,127 baseline tests plus all new tests pass.

- [ ] **Step 3: Inspect repository health**

Run: `git diff --check origin/main...HEAD`, `git status --short`, and `git log --oneline origin/main..HEAD`.

- [ ] **Step 4: Request final code review**

Generate a review package from merge base to HEAD and dispatch the final reviewer. Resolve every Critical or Important finding, rerun covering tests, and repeat review until approved.
