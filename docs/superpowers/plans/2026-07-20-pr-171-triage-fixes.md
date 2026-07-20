# PR #171 Triage Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve every actionable bug and low-risk hygiene finding from PR #171 against the current Flask application while preserving intentional behavior and deferring non-bug structural rewrites.

**Architecture:** Keep operational invariants at application boot, route all workout-history reads through `app.services.training_history`, and enforce the AI turn budget at each provider call. Each behavior change gets a focused regression test before production code changes.

**Tech Stack:** Python 3.14, Flask, Flask-SQLAlchemy, pytest, Gunicorn, Anthropic Bedrock SDK.

## Global Constraints

- Work only inside `python_temellerii/flask`.
- Use Istanbul date helpers from `app.timeutil`; do not introduce `date.today()` for application day keys.
- Preserve current single-worker deployment semantics and fail closed only outside explicit development mode.
- Keep Turkish UI strings and English code identifiers.
- Do not change the intentional stream-disconnect quota policy.
- Do not mix the `ai_coach.py` or `social.py` structural splits into this bug-fix branch.

---

### Task 1: Enforce AI gate boot invariants (#1 and #2)

**Files:**
- Modify: `app/services/ai_gate.py`
- Modify: `app/__init__.py`
- Test: `tests/test_ai_gate.py`

**Interfaces:**
- Consumes: `FITX_WEB_WORKERS`, `FITX_WEB_THREADS`, `AI_MAX_CONCURRENCY`, and `SCRAPE_MAX_CONCURRENCY` environment-derived module values.
- Produces: `enforce_gate_invariants(app) -> None`, which raises `RuntimeError` in production and logs warnings in explicit development mode.

- [ ] **Step 1: Write failing production and development tests**

```python
def test_invalid_thread_reserve_is_fatal_in_production(app, monkeypatch):
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 5)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 3)
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    with pytest.raises(RuntimeError, match="thread reserve"):
        ai_gate.enforce_gate_invariants(app)


def test_multiple_workers_are_fatal_in_production(app, monkeypatch):
    app.config["FITX_IS_DEV"] = False
    monkeypatch.setattr(ai_gate, "WEB_WORKERS", 2)
    with pytest.raises(RuntimeError, match="single worker"):
        ai_gate.enforce_gate_invariants(app)


def test_invalid_gate_configuration_only_warns_in_development(app, monkeypatch, caplog):
    app.config["FITX_IS_DEV"] = True
    monkeypatch.setattr(ai_gate, "WEB_WORKERS", 2)
    monkeypatch.setattr(ai_gate, "AI_MAX_CONCURRENCY", 5)
    monkeypatch.setattr(ai_gate, "SCRAPE_MAX_CONCURRENCY", 3)
    monkeypatch.setattr(ai_gate, "WEB_THREADS", 8)
    ai_gate.enforce_gate_invariants(app)
    assert "single worker" in caplog.text
    assert "thread reserve" in caplog.text
```

- [ ] **Step 2: Run tests to verify RED**

Run: `python -m pytest tests/test_ai_gate.py -q`

Expected: FAIL because `enforce_gate_invariants` and `WEB_WORKERS` do not exist and the current function only warns.

- [ ] **Step 3: Implement the boot guard**

```python
WEB_WORKERS = max(1, int(os.getenv("FITX_WEB_WORKERS", "1")))


def enforce_gate_invariants(app):
    problems = []
    reserve = WEB_THREADS - (AI_MAX_CONCURRENCY + SCRAPE_MAX_CONCURRENCY)
    if reserve < THREAD_RESERVE_MIN:
        problems.append("AI/scrape gates violate the required thread reserve")
    if WEB_WORKERS != 1:
        problems.append("in-process gates require a single worker")
    if not problems:
        return
    message = "; ".join(problems)
    if app.config.get("FITX_IS_DEV", False):
        app.logger.warning("[AI-GATE] %s", message)
        return
    raise RuntimeError(message)
```

Set `app.config["FITX_IS_DEV"] = _is_dev` in `configure_app`, call `enforce_gate_invariants(app)` from the factory, and retain detailed values in the error text.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `python -m pytest tests/test_ai_gate.py tests/test_gunicorn_config.py -q`

Expected: PASS.

### Task 2: Correct sparse hydration averaging (#3)

**Files:**
- Modify: `app/services/analytics_engine.py`
- Test: `tests/test_analytics_engine.py`

**Interfaces:**
- Consumes: `WaterLog` rows in the inclusive seven-day Istanbul window.
- Produces: a seven-calendar-day average whenever at least one row exists.

- [ ] **Step 1: Write the sparse-logging regression test**

```python
def test_sparse_hydration_uses_full_seven_day_window(make_user):
    user = make_user("sparsewater", last_login=date.today())
    today = app_today()
    for days_ago in (0, 6):
        db.session.add(WaterLog(
            user_id=user.id,
            count=8,
            date_key=(today - timedelta(days=days_ago)).isoformat(),
        ))
    db.session.commit()
    assert "NUDGE_LOW_HYDRATION" in _nudge_types(user)
```

- [ ] **Step 2: Run test to verify RED**

Run: `python -m pytest tests/test_analytics_engine.py::test_sparse_hydration_uses_full_seven_day_window -q`

Expected: FAIL because the current average is `16 / 2 == 8`.

- [ ] **Step 3: Implement the seven-day denominator**

```python
window_days = 7
avg_cups = sum(row.count or 0 for row in rows) / window_days
```

Use `window_days` in both localized nudge messages, and update the existing adequate-water test to insert seven days of eight cups.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `python -m pytest tests/test_analytics_engine.py -q`

Expected: PASS.

### Task 3: Complete canonical WorkoutLog reader migration (#6)

**Files:**
- Modify: `app/services/analytics_engine.py`
- Modify: `app/blueprints/tracking.py`
- Test: `tests/test_analytics_engine.py`
- Test: `tests/test_progress_api.py`

**Interfaces:**
- Consumes: `fetch_workout_entries(user_id, start_day, end_day, include_markers=True)`.
- Produces: unchanged missing-log, heatmap, and insight responses without direct `WorkoutLog` history queries in these consumers.

- [ ] **Step 1: Add tests proving canonical reader behavior**

```python
def test_completion_marker_counts_as_recent_workout(make_user):
    user = make_user("recentmarker", last_login=date.today())
    db.session.add(WorkoutLog(
        user_id=user.id,
        exercise_name=WORKOUT_COMPLETION_MARKER,
        created_at=datetime.utcnow(),
    ))
    db.session.commit()
    assert "NUDGE_NO_WORKOUT" not in _nudge_types(user)
```

Extend progress endpoint tests so a marker-only workout day appears once in both heatmap and insight session counts.

- [ ] **Step 2: Run tests to establish current behavior**

Run: `python -m pytest tests/test_analytics_engine.py tests/test_progress_api.py -q`

Expected: behavior tests may pass; add a source-boundary assertion that fails while either module still contains a direct `WorkoutLog.query` or `db.session.query(WorkoutLog...)` reader.

- [ ] **Step 3: Route all three readers through training_history**

```python
entries = fetch_workout_entries(
    user.id,
    app_date_of(cutoff),
    app_today(),
    include_markers=True,
)
recent_workout = next((entry for entry in entries if entry.created_at >= cutoff), None)
```

For heatmap and insights, fetch the date window with `include_markers=True` and deduplicate `entry.performed_on.isoformat()`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `python -m pytest tests/test_analytics_engine.py tests/test_progress_api.py tests/test_training_history.py -q`

Expected: PASS.

### Task 4: Enforce a Bedrock per-turn wall-clock budget (#7)

**Files:**
- Modify: `app/config.py`
- Modify: `app/extensions.py`
- Modify: `.env.example`
- Modify: `app/services/ai_coach.py`
- Modify: `app/services/ai_stream.py`
- Test: `tests/test_ai_coach.py`
- Test: `tests/test_ai_stream.py`
- Test: `tests/test_env_example.py`

**Interfaces:**
- Consumes: `AI_COACH_TURN_TIMEOUT_SECONDS` (default `90`) and `time.monotonic()`.
- Produces: a shared remaining-budget helper and a per-call Anthropic `timeout` no larger than either the existing 60-second call cap or the remaining turn budget.

- [ ] **Step 1: Write failing blocking and streaming deadline tests**

```python
def test_bedrock_tool_loop_stops_when_turn_budget_expires(...):
    clock = iter([0.0, 0.0, 91.0])
    monkeypatch.setattr(ai_coach.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(ai_coach, "AI_COACH_TURN_TIMEOUT_SECONDS", 90.0)
    # First response requests a tool; no second provider call is allowed.
    assert len(calls) == 1


def test_stream_bedrock_passes_remaining_turn_timeout(...):
    monkeypatch.setattr(ai_stream.time, "monotonic", scripted_clock)
    events = list(ai_stream._stream_bedrock(...))
    assert fake.calls[0]["timeout"] <= 90.0
```

- [ ] **Step 2: Run tests to verify RED**

Run: `python -m pytest tests/test_ai_coach.py tests/test_ai_stream.py -q`

Expected: FAIL because calls have no turn deadline or timeout override.

- [ ] **Step 3: Implement the deadline**

```python
AI_COACH_TURN_TIMEOUT_SECONDS = float(
    os.getenv("AI_COACH_TURN_TIMEOUT_SECONDS", "90")
)


def _remaining_turn_seconds(deadline):
    return max(0.0, deadline - time.monotonic())
```

Create one deadline before each Bedrock tool loop. Before every provider round, return/emit the localized tool fallback if no budget remains; otherwise pass `timeout=min(BEDROCK_CALL_TIMEOUT_SECONDS, remaining)` to `messages.create` and `messages.stream`. Document the turn-budget env override in `.env.example`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `python -m pytest tests/test_ai_coach.py tests/test_ai_stream.py tests/test_env_example.py -q`

Expected: PASS.

### Task 5: Remove dead quota counter APIs (#8)

**Files:**
- Modify: `app/services/premium.py`
- Modify: `tests/test_premium_quota.py`
- Modify: `tests/test_concurrency_staleness.py`

**Interfaces:**
- Consumes: live `reserve_ai_quota(user, counter_key, limit)` API.
- Produces: one documented counter mutation path with no `record_ai_chat`, `record_ai_plan_generation`, or `_record_counter` trap.

- [ ] **Step 1: Rewrite tests against the live reservation API**

```python
def test_remaining_and_reserve_per_kind(make_user):
    user = make_user("kotauser")
    assert premium.reserve_ai_quota(user, "training", 1) is True
    assert premium.remaining_ai_plans(user, "training") == 0


def test_reserve_ai_quota_reads_fresh_metadata_under_lock(app, make_user):
    # Simulate DB count=1 while identity-map metadata is stale, then reserve with limit=3.
    assert reserve_ai_quota(user, "nutrition", 3) is True
    assert fresh.user_metadata["ai_plan_quota"]["nutrition"] == 2
```

- [ ] **Step 2: Run tests to verify the replacement path is covered**

Run: `python -m pytest tests/test_premium_quota.py tests/test_concurrency_staleness.py -q`

Expected: PASS after test-only migration; production deletion is still pending.

- [ ] **Step 3: Delete dead functions and document the sole writer**

Remove `_record_counter`, `record_ai_plan_generation`, and `record_ai_chat`. Add a module comment stating that reservations are the sole increment path and failures refund through `refund_ai_quota`.

- [ ] **Step 4: Run tests and a call-site scan**

Run: `python -m pytest tests/test_premium_quota.py tests/test_concurrency_staleness.py -q`

Run: `rg -n "record_ai_chat|record_ai_plan_generation|_record_counter" app tests`

Expected: tests PASS and scan returns no matches.

### Task 6: Escape SQL LIKE wildcards in friend search (#9)

**Files:**
- Modify: `app/blueprints/social.py`
- Test: `tests/test_social_routes.py`

**Interfaces:**
- Consumes: raw query text from `/friends/search?q=...`.
- Produces: literal case-insensitive substring matching for `%`, `_`, and `\`.

- [ ] **Step 1: Write the literal wildcard regression test**

```python
def test_friends_search_treats_like_wildcards_literally(client, auth_user, make_user):
    make_user("fit_100%")
    make_user("fitX100abc")
    users = client.get(
        "/friends/search", query_string={"q": "fit_100%"}
    ).get_json()["users"]
    assert [user["username"] for user in users] == ["fit_100%"]
```

- [ ] **Step 2: Run test to verify RED**

Run: `python -m pytest tests/test_social_routes.py::test_friends_search_treats_like_wildcards_literally -q`

Expected: FAIL because `_` and `%` currently broaden the SQL pattern.

- [ ] **Step 3: Escape the pattern**

```python
escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
User.username.ilike(f"%{escaped_q}%", escape="\\")
```

- [ ] **Step 4: Run tests to verify GREEN**

Run: `python -m pytest tests/test_social_routes.py -q`

Expected: PASS.

### Task 7: Verify dispositions and complete the branch

**Files:**
- Review: all modified files
- Do not modify for #4, #5, or #10.

**Interfaces:**
- Consumes: all six independently green fixes.
- Produces: a verified branch plus a disposition for every PR #171 item.

- [ ] **Step 1: Run focused regression suites**

Run: `python -m pytest tests/test_ai_gate.py tests/test_gunicorn_config.py tests/test_analytics_engine.py tests/test_progress_api.py tests/test_training_history.py tests/test_ai_coach.py tests/test_ai_stream.py tests/test_env_example.py tests/test_premium_quota.py tests/test_concurrency_staleness.py tests/test_social_routes.py -q`

Expected: PASS.

- [ ] **Step 2: Run the complete test suite**

Run: `python -m pytest -q`

Expected: all tests pass; the baseline was `1893 passed, 3 deselected`.

- [ ] **Step 3: Inspect the final diff and scope**

Run: `git diff --check`

Run: `git status --short`

Run: `git diff --stat origin/main...HEAD`

Expected: only planned Flask code, tests, environment documentation, and this plan are changed.

- [ ] **Step 4: Record report dispositions**

Document in the handoff: #1/#2/#3/#6/#7/#8/#9 fixed; #4/#5 intentionally deferred as standalone structural refactors; #10 unchanged because quota-on-disconnect is an explicit policy.
