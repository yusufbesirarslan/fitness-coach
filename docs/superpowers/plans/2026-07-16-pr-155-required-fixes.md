# PR #155 Required Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all fourteen findings in `docs/TRIAGE_FIXES.md` sequentially with regression coverage and independently reviewable commits.

**Architecture:** Preserve the existing Flask/SQLAlchemy boundaries. Decouple provider streaming with a queue, centralize validation and idempotency in focused helpers, keep PostgreSQL as the durable correctness layer, and harden request boundaries without changing public response contracts.

**Tech Stack:** Python 3.14, Flask 3.1, SQLAlchemy 2.0, Alembic/Flask-Migrate, Redis, Bedrock Anthropic streaming, pytest.

## Global Constraints

- Work only on `codex/pr-155-required-fixes`, based on merged `origin/main` at `03e6b36`.
- Preserve Turkish UI copy and English code identifiers.
- Scope every database lookup introduced here to the authenticated user's ID.
- Write and run a focused failing test before production code for every behavior change.
- Verify the focused subsystem and commit before starting the next task.
- Findings 1 and 2 share one production commit because the same boundary change fixes both.
- Keep provider exceptions, idempotency keys, credentials, and tokens out of logs and client payloads.
- Keep the idempotency migration additive and compatible with automatic fresh-database boot.
- Do not reply to or resolve GitHub threads; PR #155 has none.

---

### Task 1: Fix findings 1–2 by decoupling streaming inference

**Files:**
- Modify: `app/services/ai_stream.py`
- Test: `tests/test_ai_stream.py`

**Interfaces:**
- Produces: `_stream_bedrock_turn(messages_client, call_kwargs) -> iterator[dict]` with `delta`, `final`, and `exception` messages.
- Preserves: `stream_coach_answer` events and the Bedrock-to-OpenAI fallback rule.

- [ ] **Step 1: Write failing semaphore and slow-consumer tests**

Use a one-slot semaphore and fake Bedrock streams. Run the nested-tool reproduction in a helper thread with a one-second join so the old deadlock becomes an assertion failure.

    def test_stream_bedrock_releases_model_slot_before_tool_dispatch(monkeypatch):
        monkeypatch.setattr(ai_gate, "_model_slots", threading.BoundedSemaphore(1))
        monkeypatch.setattr(ai_coach, "_dispatch_coach_tool", nested_slot_tool)
        result = run_generator_in_thread(
            ai_stream._stream_bedrock(7, "foto", "", [], "tr"), timeout=1)
        assert result[-1]["type"] == "done"

    def test_slow_consumer_does_not_retain_model_slot(monkeypatch):
        monkeypatch.setattr(ai_gate, "_model_slots", threading.BoundedSemaphore(1))
        gen = ai_stream._stream_bedrock(7, "merhaba", "", [], "tr")
        assert next(gen) == {"type": "delta", "text": "Merhaba"}
        assert provider_finished.wait(timeout=1)
        assert ai_gate._model_slots.acquire(blocking=False)
        ai_gate._model_slots.release()

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_ai_stream.py -k "releases_model_slot or slow_consumer" -v`

Expected: the nested-tool helper times out and the slow-consumer acquire returns false because the current generator is suspended inside the model-slot context.

- [ ] **Step 3: Add the producer queue**

Add `queue` and `threading` imports and implement:

    def _stream_bedrock_turn(messages_client, call_kwargs):
        messages = queue.SimpleQueue()

        def produce():
            try:
                with model_concurrency_slot():
                    with messages_client.stream(**call_kwargs) as stream:
                        for text in stream.text_stream:
                            if text:
                                messages.put({"kind": "delta", "text": text})
                        messages.put({
                            "kind": "final",
                            "message": stream.get_final_message(),
                        })
            except Exception as exc:
                messages.put({"kind": "exception", "exception": exc})

        threading.Thread(target=produce, daemon=True).start()
        while True:
            message = messages.get()
            yield message
            if message["kind"] in {"final", "exception"}:
                return

Refactor each `_stream_bedrock` iteration to call the helper with `ai_coach.bedrock_client.messages`. Yield deltas in the request thread. Process the final message and dispatch tools only after the producer released its slot.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_ai_stream.py tests/test_ai_gate.py tests/test_coach_tools.py -q`

- [ ] **Step 5: Commit**

    fix: decouple streaming model slots

### Task 2: Fix finding 3 partial-stream accounting

**Files:**
- Modify: `app/services/ai_stream.py`
- Modify: `app/services/ai_pipeline.py`
- Modify: `app/blueprints/coach.py`
- Test: `tests/test_ai_stream.py`
- Test: `tests/test_ai_pipeline.py`
- Test: `tests/test_coach_routes.py`

**Interfaces:**
- Adds internal error fields `work_performed: bool` and `partial_text: str`.
- Preserves public SSE error data as a translated `message` only.

- [ ] **Step 1: Write failing interruption tests**

Test an error after a delta and an error after a tool with no delta. Both keep quota and avoid `record_ai_failure`. The delta case persists partial memory with `interrupted=True`. A pre-work error still refunds and records failure.

    def test_partial_stream_error_is_recorded_as_interruption(monkeypatch):
        monkeypatch.setattr(ai_stream, "stream_coach_answer", fake_delta_then_error)
        events = list(ai_pipeline.stream_answer(7, "soru"))
        assert events[-1]["work_performed"] is True
        record_turn.assert_called_once_with(
            conversation, "soru", "Kısmi yanıt", usage=None, interrupted=True)

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_ai_stream.py tests/test_ai_pipeline.py tests/test_coach_routes.py -k "partial_stream_error or keeps_quota" -v`

- [ ] **Step 3: Propagate work state**

In `ai_stream` emit:

    yield {
        "type": "error",
        "key": "coach.reply_failed",
        "work_performed": bool(parts or tools_ran),
        "partial_text": "".join(parts).strip(),
    }

In `ai_pipeline` persist non-empty `partial_text` with `interrupted=True` and forward `work_performed`. In the route, refund and record failure only when `work_performed` is false.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_ai_stream.py tests/test_ai_pipeline.py tests/test_coach_routes.py -q`

- [ ] **Step 5: Commit**

    fix: preserve partial stream work

### Task 3: Fix finding 4 AI meal-total sanitation

**Files:**
- Modify: `app/services/nutrition_pipeline.py`
- Modify: `app/blueprints/nutrition/meallog.py`
- Test: `tests/test_nutrition_pipeline.py`
- Test: `tests/test_nutrition_routes.py`

**Interfaces:**
- Produces: `sanitize_meal_total_macros(calories, protein, carbs, fat)`.

- [ ] **Step 1: Write failing sanitation tests**

    def test_meal_total_sanitizer_floors_non_finite_and_negative_values():
        assert np.sanitize_meal_total_macros(
            float("nan"), -2, 4, -1) == (0, 0, 4, 0)

    def test_meal_total_sanitizer_caps_and_preserves_ratios():
        assert np.sanitize_meal_total_macros(
            20000, 1000, 2000, 500) == (10000.0, 500.0, 1000.0, 250.0)

    def test_meal_total_sanitizer_caps_macro_without_high_calories():
        assert np.sanitize_meal_total_macros(
            1000, 5000, 0, 0) == (400.0, 2000.0, 0, 0)

Add a route test returning negative and excessive parseable AI JSON and assert the saved row equals the sanitized payload.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_nutrition_pipeline.py tests/test_nutrition_routes.py -k "meal_total_sanitizer or ai_meal_total" -v`

- [ ] **Step 3: Implement and call the helper**

Add `MAX_MEAL_TOTAL_KCAL = 10000.0`, `MAX_MEAL_TOTAL_MACRO_G = 2000.0`, and `MAX_MEAL_TOTAL_FAT_G = 1000.0`. Import `math`, define finite coercion, apply the strictest proportional ceiling, and reuse the existing hard Atwater correction:

    def _finite_nonnegative(value):
        value = _num(value)
        return value if math.isfinite(value) and value > 0 else 0

    def sanitize_meal_total_macros(calories, protein, carbs, fat):
        values = [
            _finite_nonnegative(value)
            for value in (calories, protein, carbs, fat)
        ]
        calories, protein, carbs, fat = values
        ratios = [1.0]
        if calories:
            ratios.append(MAX_MEAL_TOTAL_KCAL / calories)
        if protein:
            ratios.append(MAX_MEAL_TOTAL_MACRO_G / protein)
        if carbs:
            ratios.append(MAX_MEAL_TOTAL_MACRO_G / carbs)
        if fat:
            ratios.append(MAX_MEAL_TOTAL_FAT_G / fat)
        scale = min(1.0, *ratios)
        calories, protein, carbs, fat = [
            round(value * scale, 1) for value in values
        ]
        supported = 4.0 * protein + 4.0 * carbs + 9.0 * fat
        if calories and supported < calories * (1.0 - ATWATER_HARD_TOLERANCE):
            calories = round(supported, 1)
        return calories, protein, carbs, fat

Call it after parsing AI JSON and before constructing `MealLog`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_nutrition_pipeline.py tests/test_nutrition_routes.py -q`

- [ ] **Step 5: Commit**

    fix: sanitize AI meal totals

### Task 4: Fix finding 5 weight range validation

**Files:**
- Modify: `app/blueprints/tracking.py`
- Modify: `locales/tr.json`
- Modify: `locales/en.json`
- Test: `tests/test_tracking_routes.py`

**Interfaces:**
- Produces: `_parse_weight(value) -> tuple[float | None, str | None]`.

- [ ] **Step 1: Write failing route tests**

Parametrize `/log`, `/checkin`, and `/update-weight` with `-5`, `19.9`, `500.1`, `5000`, `NaN`, and `Infinity`. Assert 400 and no model or profile mutation.

    @pytest.mark.parametrize("path", ["/log", "/checkin", "/update-weight"])
    @pytest.mark.parametrize("weight", [-5, 19.9, 500.1, 5000, "NaN", "Infinity"])
    def test_weight_routes_reject_out_of_range_values(
            client, auth_user, path, weight):
        assert client.post(path, json={"weight": weight}).status_code == 400

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_tracking_routes.py -k weight_routes_reject -v`

- [ ] **Step 3: Centralize parsing**

    def _parse_weight(value):
        if value in (None, ""):
            return None, "route.weight_required"
        try:
            weight = float(value)
        except (TypeError, ValueError):
            return None, "route.weight_numeric"
        if not math.isfinite(weight) or not 20 <= weight <= 500:
            return None, "route.weight_range"
        return weight, None

Use the helper in all three routes and add `route.weight_range` translations.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_tracking_routes.py tests/test_calculations.py -q`

- [ ] **Step 5: Commit**

    fix: validate submitted weight range

### Task 5: Fix finding 6 summary-note budgeting

**Files:**
- Modify: `app/services/memory_manager.py`
- Test: `tests/test_memory_manager.py`

**Interfaces:**
- Preserves: `build_context_window(conversation, budget=None) -> list[dict]`.

- [ ] **Step 1: Write the boundary regression test**

    def test_summary_header_counts_toward_context_budget(app, conversation):
        conversation.summary = "x" * 80
        window = memory_manager.build_context_window(conversation, budget=25)
        assert sum(
            memory_manager.estimate_tokens(message["content"])
            for message in window
        ) <= 25

Also assert no recent row is forced in when the complete note consumes the budget.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_memory_manager.py -k summary_header_counts -v`

- [ ] **Step 3: Budget the note before rows**

Construct the full note before the row loop, truncate it to `budget * CHARS_PER_TOKEN`, seed `used` from the complete note, and force-truncate a newest message only when `budget - used` is positive.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_memory_manager.py -q`

- [ ] **Step 5: Commit**

    fix: budget conversation summary header

### Task 6: Fix finding 7 stream-refund week guards

**Files:**
- Modify: `app/services/premium.py`
- Modify: `app/blueprints/coach.py`
- Test: `tests/test_premium_quota.py`
- Test: `tests/test_coach_routes.py`

**Interfaces:**
- Produces: `reservation_week(user, counter_key) -> str | None`.
- Extends: `refund_ai_quota(user, counter_key, reserved_week=None)`.

- [ ] **Step 1: Write the rollover failure test**

Reserve chat quota in week A, replace stored metadata with week B and count 1, refund with captured week A, and assert week B stays 1. Add a route test proving `_refund_chat_quota` receives the captured value.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_premium_quota.py tests/test_coach_routes.py -k "reservation_week or rollover" -v`

- [ ] **Step 3: Pass the explicit week**

    def reservation_week(user, counter_key):
        return (
            getattr(user, _RESERVATION_WEEKS_ATTR, {}) or {}
        ).get(counter_key)

    def refund_ai_quota(user, counter_key, reserved_week=None):
        if reserved_week is None:
            reserved_week = reservation_week(user, counter_key)

Capture the week immediately after stream reservation and pass it through `_refund_chat_quota(user_id, reserved_week)`.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_premium_quota.py tests/test_coach_routes.py -q`

- [ ] **Step 5: Commit**

    fix: preserve stream quota week

### Task 7: Fix finding 8 meal-write idempotency

**Files:**
- Create: `app/services/meal_idempotency.py`
- Create: `migrations/versions/ee55ff66aa77_add_meal_log_idempotency.py`
- Modify: `app/models.py`
- Modify: `app/blueprints/nutrition/meallog.py`
- Modify: `app/blueprints/nutrition/diary.py`
- Modify: `app/blueprints/food.py`
- Modify: `app/services/barcode.py`
- Modify: `static/nutrition.js`
- Modify: `static/coach_widget.js`
- Test: `tests/test_nutrition_routes.py`
- Test: `tests/test_barcode_workflow.py`
- Test: `tests/test_migration_graph.py`

**Interfaces:**
- Produces: `read_idempotency_key()`, `find_existing(user_id, key)`, and `commit_once(entry, key)`.
- Adds: nullable `MealLog.idempotency_key` and `uq_meal_log_user_idempotency`.

- [ ] **Step 1: Write failing duplicate tests**

Use the same key twice for `/meal-log`, quick-add, and barcode. Assert one row. For `/meal-log` assert one AI call. Use the same key for two users and assert user isolation. Simulate a commit uniqueness race and assert the winner row is returned.

    headers = {
        "Idempotency-Key": "018f47d2-a2c7-7f52-a5b0-123456789abc"
    }
    first = client.post("/meal-log", json=payload, headers=headers)
    second = client.post("/meal-log", json=payload, headers=headers)
    assert first.status_code == second.status_code == 200
    assert MealLog.query.filter_by(user_id=auth_user.id).count() == 1
    assert chat_calls == 1

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_nutrition_routes.py tests/test_barcode_workflow.py -k idempotency -v`

- [ ] **Step 3: Add model and expand-only migration**

Revision `ee55ff66aa77` uses down revision `dd44ee55ff66` and batch operations:

    with op.batch_alter_table("meal_log") as batch_op:
        batch_op.add_column(sa.Column(
            "idempotency_key", sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint(
            "uq_meal_log_user_idempotency",
            ["user_id", "idempotency_key"],
        )

The downgrade drops the constraint before the column. Add the same column and unique constraint to `MealLog.__table_args__`.

- [ ] **Step 4: Implement durable replay helpers**

Validate keys with `^[A-Za-z0-9._:-]{8,64}$`. Query before AI work. Assign the key to new rows. On `IntegrityError`, roll back and re-query by user ID and key.

    def commit_once(entry, key):
        entry.idempotency_key = key
        db.session.add(entry)
        try:
            db.session.commit()
            return entry, True
        except IntegrityError:
            db.session.rollback()
            existing = find_existing(entry.user_id, key)
            if existing is None:
                raise
            return existing, False

Award quests only when `created` is true. Replay existing nutrient data without repeating AI or ledger writes.

- [ ] **Step 5: Add frontend keys**

Add a browser helper using `crypto.randomUUID()` with a timestamp/random fallback. Generate once at the start of each maintained meal-write action and send it as `Idempotency-Key`. Keep existing button disabling.

- [ ] **Step 6: Verify migration and green behavior**

Run: `python -m pytest tests/test_nutrition_routes.py tests/test_barcode_workflow.py tests/test_migration_graph.py tests/test_db_init.py -q`

- [ ] **Step 7: Commit**

    fix: make meal writes idempotent

### Task 8: Fix finding 9 leaderboard rebuild batching

**Files:**
- Modify: `app/services/gamification.py`
- Modify: `tests/test_leaderboard_redis.py`

**Interfaces:**
- Produces: `_iter_leaderboard_users(batch_size=500)`.

- [ ] **Step 1: Write a multi-batch test**

Set batch size to two, create five users, rebuild, assert all IDs and scores occur in both sets, and assert at least three pipeline executions.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_leaderboard_redis.py -k rebuild_batches -v`

- [ ] **Step 3: Implement keyset batches**

    def _iter_leaderboard_users(batch_size=500):
        last_id = 0
        while True:
            batch = (
                User.query.filter(User.id > last_id)
                .order_by(User.id)
                .limit(batch_size)
                .all()
            )
            if not batch:
                return
            yield batch
            last_id = batch[-1].id

Delete both Redis sets once, create and execute a fresh pipeline per batch, then fetch the next batch.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_leaderboard_redis.py tests/test_gamification_routes.py -q`

- [ ] **Step 5: Commit**

    perf: batch leaderboard rebuilds

### Task 9: Fix finding 10 client-controlled diary dates

**Files:**
- Modify: `app/blueprints/nutrition/diary.py`
- Test: `tests/test_nutrition_routes.py`

**Interfaces:**
- Preserves response fields while deriving `CustomMeal.date_key` from `day_key()`.

- [ ] **Step 1: Write the failing date test**

    def test_diary_create_meal_ignores_client_date(client, auth_user):
        body = client.post("/api/diary/meal", json={
            "meal_name": "Kahvaltı",
            "date_key": "2099-01-01",
        }).get_json()
        meal = db.session.get(CustomMeal, body["meal_id"])
        assert meal.date_key == day_key()

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_nutrition_routes.py -k ignores_client_date -v`

- [ ] **Step 3: Derive the date**

Replace the request lookup with `date_key = day_key()` and keep both existence queries scoped to that value.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_nutrition_routes.py -q`

- [ ] **Step 5: Commit**

    fix: derive diary date server-side

### Task 10: Fix finding 11 duplicate finalization

**Files:**
- Modify: `app/services/ai_coach.py`
- Test: `tests/test_ai_coach.py`
- Test: `tests/test_ai_pipeline.py`

**Interfaces:**
- Makes `ai_pipeline.generate_answer` the only blocking `finalize_reply` caller.

- [ ] **Step 1: Write single-call and history tests**

Patch `response_formatter.finalize_reply` with a counter, call `generate_answer`, and assert one call. Exercise `_run_coach_conversation` with a provider fallback and assert legacy session history is unchanged.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_ai_coach.py tests/test_ai_pipeline.py -k "finalize_once or fallback_history" -v`

- [ ] **Step 3: Remove inner finalization**

Replace the inner tuple assignment with:

    is_error_fallback = is_coach_error_fallback(final_text)

Return raw `final_text` and keep the pipeline's current finalization and moderation order.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_ai_coach.py tests/test_ai_pipeline.py tests/test_coach_routes.py -q`

- [ ] **Step 5: Commit**

    fix: finalize coach replies once

### Task 11: Fix finding 12 blocking AI metrics

**Files:**
- Modify: `app/services/ai_pipeline.py`
- Test: `tests/test_ai_pipeline.py`
- Test: `tests/test_ai_metrics.py`

**Interfaces:**
- Extends: `_emit_metrics(mode, is_error=False, usage=None)`.

- [ ] **Step 1: Write blocking metric tests**

Patch `ai_metrics.increment`. Assert a normal blocking answer emits `AITurn` with `mode=blocking`, a fallback emits `AIErrors`, and an exception emits `AIErrors` before being re-raised.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_ai_pipeline.py tests/test_ai_metrics.py -k blocking -v`

- [ ] **Step 3: Parameterize metrics**

    def _emit_metrics(mode, is_error=False, usage=None):
        dimensions = {"mode": mode}
        ai_metrics.increment(
            "AIErrors" if is_error else "AITurn",
            dimensions=dimensions,
        )
        if usage:
            ai_metrics.record_tokens(
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                dimensions=dimensions,
            )

Wrap the blocking provider/finalization body in `try/except`; emit a blocking error and re-raise on exception. Emit after fallback classification on normal return. Pass `mode="stream"` from stream calls.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_ai_pipeline.py tests/test_ai_metrics.py tests/test_observability.py -q`

- [ ] **Step 5: Commit**

    feat: emit blocking AI metrics

### Task 12: Fix finding 13 exact CSRF origins

**Files:**
- Modify: `app/hooks.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Produces: `_origin_tuple(url) -> tuple[str, str, int] | None`.

- [ ] **Step 1: Write origin matrix tests**

Add token-bearing requests for `Origin: null`, HTTPS-to-HTTP mismatch, port mismatch, valid explicit default port, and valid same origin. Assert the first three return 403 and the last two reach the route.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_hooks.py -k "origin_null or scheme_mismatch or port_mismatch or default_port" -v`

- [ ] **Step 3: Normalize exact origins**

    def _origin_tuple(value):
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return None
        return parsed.scheme, parsed.hostname.lower(), port

Compare the header tuple with `_origin_tuple(request.host_url)` for Origin and Referer. A null or malformed tuple fails closed.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_hooks.py tests/test_auth.py tests/test_notification_routes.py -q`

- [ ] **Step 5: Commit**

    fix: enforce exact CSRF origins

### Task 13: Fix finding 14 deep-health allowlisting

**Files:**
- Modify: `app/__init__.py`
- Modify: `.env.example`
- Modify: `docs/DEPLOYMENT.md`
- Test: `tests/test_health.py`

**Interfaces:**
- Adds: `DEEP_HEALTH_TRUSTED_CIDRS`.
- Preserves public shallow `/health` behavior.

- [ ] **Step 1: Write allowlist tests**

Set the allowlist to `172.17.0.1/32`. Assert that address receives deep fields while `10.0.0.8`, `192.168.1.20`, and `172.17.0.2` receive shallow fields. Keep loopback and spoofed-forwarding coverage.

- [ ] **Step 2: Verify red**

Run: `python -m pytest tests/test_health.py -k "configured_gateway or other_private" -v`

- [ ] **Step 3: Parse explicit networks**

    def _deep_health_networks():
        raw = os.getenv(
            "DEEP_HEALTH_TRUSTED_CIDRS",
            "172.17.0.1/32",
        )
        networks = []
        for value in raw.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                networks.append(ipaddress.ip_network(value, strict=False))
            except ValueError:
                continue
        return tuple(networks)

    def _deep_health_allowed():
        try:
            addr = ipaddress.ip_address(request.remote_addr or "")
        except ValueError:
            return False
        return addr.is_loopback or any(
            addr in network for network in _deep_health_networks()
        )

Document comma-separated CIDRs and the Docker gateway requirement without widening the default to an RFC1918 range.

- [ ] **Step 4: Verify green**

Run: `python -m pytest tests/test_health.py tests/test_deploy_workflow.py -q`

- [ ] **Step 5: Commit**

    fix: restrict deep health networks

### Task 14: Whole-branch verification and review

**Files:**
- Review: every file changed in `origin/main...HEAD`.

**Interfaces:**
- Produces: one verified branch with the design commit and thirteen implementation commits.

- [ ] **Step 1: Run all affected suites together**

Run:

    python -m pytest tests/test_ai_stream.py tests/test_ai_gate.py tests/test_coach_tools.py tests/test_ai_pipeline.py tests/test_coach_routes.py tests/test_nutrition_pipeline.py tests/test_nutrition_routes.py tests/test_tracking_routes.py tests/test_calculations.py tests/test_memory_manager.py tests/test_premium_quota.py tests/test_barcode_workflow.py tests/test_migration_graph.py tests/test_db_init.py tests/test_leaderboard_redis.py tests/test_gamification_routes.py tests/test_ai_coach.py tests/test_ai_metrics.py tests/test_observability.py tests/test_hooks.py tests/test_auth.py tests/test_notification_routes.py tests/test_health.py tests/test_deploy_workflow.py -q

Expected: exit 0 with zero failures.

- [ ] **Step 2: Run the complete non-load suite**

Run: `python -m pytest -q`

Expected: exit 0 with zero failures; existing warning output may remain unchanged.

- [ ] **Step 3: Verify migration and repository integrity**

Run:

    $env:FITX_SKIP_DB_INIT='1'
    $env:FLASK_ENV='development'
    python -m flask --app starter db heads
    git diff --check origin/main...HEAD
    git status --short
    git log --oneline origin/main..HEAD

Expected: one Alembic head at `ee55ff66aa77`, no whitespace errors, a clean working tree, and the planned commit sequence.

- [ ] **Step 4: Review the final diff against the audit**

For each numbered section in `docs/TRIAGE_FIXES.md`, point to its regression test and production change. Any critical or important review finding must receive a new failing test and fresh verification before completion is reported.
