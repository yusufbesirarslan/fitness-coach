# Codebase Triage Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce and remediate the actionable 2026-08-14 and 2026-08-28 triage findings without changing accepted operational contracts.

**Architecture:** Keep each fix inside its current subsystem and pin the externally visible behavior with a focused regression test. Reuse RQ for production background work, use a daemon fallback only for maintenance when RQ is absent, and keep health/metrics paths read-only and non-gating where documented.

**Tech Stack:** Python 3, Flask, SQLAlchemy, Alembic/Flask-Migrate, pytest, RQ/Redis.

**Spec:** `docs/superpowers/specs/2026-08-28-codebase-triage-remediation-design.md`

## Global Constraints

- Base all work on `origin/main` in `fix/triage-2026-08-28`. The branch was
  written against `a6d6b2e` and has since been rebased onto the current
  `origin/main` `ad77426`, which is the base this PR must be reviewed against.
- Write and observe a failing regression test before each production change.
- Preserve `FITX_DB_UPGRADE_FAIL_OPEN`, single-worker AI gating, and informational deep-health semantics.
- Do not add infrastructure dependencies or reject legitimate punctuation/non-ASCII characters in names.
- Do not modify accepted-tradeoff findings unless reproduction contradicts the reports.

---

### Task 1: Verify the 2026-08-14 fixes

**Files:**
- Inspect: `app/blueprints/training.py`
- Inspect: `app/config.py`
- Inspect: `app/blueprints/coach.py`
- Inspect: `app/services/badges.py`
- Inspect: `app/services/ai_gate.py`
- Inspect: `app/services/mobile_auth.py`
- Inspect: `app/services/ai_coach.py`
- Test: existing focused tests only

**Interfaces:**
- Consumes: existing water logging, request validation, badge awarding, AI gates, refresh rotation, proxy configuration, and coach deadlines.
- Produces: a recorded pass/fail matrix in the final remediation report; no production code unless a focused test exposes a regression.

- [ ] **Step 1: Run the existing water, request-limit, and badge regressions**

Run:

```powershell
python -m pytest tests/test_training_routes.py -k "water" tests/test_hooks.py -k "request_body or payload" tests/test_challenges.py -k "badge" -v
```

Expected: the durable `WaterLog.quest_fired`, global request ceiling, `/chat` cap, and local badge-flush behaviors pass.

- [ ] **Step 2: Run the gate, mobile-refresh, proxy, and deadline regressions**

Run:

```powershell
python -m pytest tests/test_ai_gate.py tests/test_capacity_invariants.py -k "model_slot or deadline or ceiling" tests/test_mobile_auth_service.py -k "refresh" tests/test_health.py -k "proxyfix" tests/test_ai_coach.py -k "deadline or timeout" -v
```

Expected: bounded acquisition, network-outside-lock refresh, host/port distrust, and shared turn budget pass.

- [ ] **Step 3: Record findings without rewriting protected behavior**

Add each item and its focused-test evidence to the final report created in Task 11. If any regression fails for the reported reason, stop this task and insert a new red-green fix task immediately after Task 1.

---

### Task 2: Cover every user foreign key during erasure (F1)

**Files:**
- Modify: `app/cli.py`
- Modify: `tests/test_cascade_delete.py`

**Interfaces:**
- Consumes: `_USER_CHILD_MODELS`, `_purge_user(user)`, SQLAlchemy mapper metadata.
- Produces: `_USER_FK_MANUAL_CLEANUP: frozenset[tuple[str, str]]`, declaring nonstandard user-FK cleanup paths.

- [ ] **Step 1: Replace the name-only test with a full FK-target test**

Use mapper metadata and literal expectations:

```python
def test_purge_user_covers_every_foreign_key_to_user():
    from app.cli import _USER_CHILD_MODELS, _USER_FK_MANUAL_CLEANUP
    direct = set(_USER_CHILD_MODELS)
    missing = []
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        for column in mapper.columns:
            targets_user = any(
                fk.column.table.name == "user" for fk in column.foreign_keys
            )
            if not targets_user or cls.__name__ == "User":
                continue
            if column.name == "user_id" and cls in direct:
                continue
            if (cls.__name__, column.name) in _USER_FK_MANUAL_CLEANUP:
                continue
            missing.append(f"{cls.__name__}.{column.name}")
    assert missing == []
```

- [ ] **Step 2: Run the test and verify the correct failure**

Run: `python -m pytest tests/test_cascade_delete.py::test_purge_user_covers_every_foreign_key_to_user -v`

Expected: FAIL because `_USER_FK_MANUAL_CLEANUP` does not exist.

- [ ] **Step 3: Add the explicit manual-cleanup inventory**

Add beside `_USER_CHILD_MODELS`:

```python
_USER_FK_MANUAL_CLEANUP = frozenset({
    ("Friendship", "sender_id"),
    ("Friendship", "receiver_id"),
    ("Message", "sender_id"),
    ("Message", "receiver_id"),
    ("Notification", "actor_id"),
})
```

`User.referred_by_id` remains excluded because the parent `User` mapper is handled by the existing explicit `SET NULL` update. Do not add `FeedItem.ref_id`: it is polymorphic and not a foreign key.

- [ ] **Step 4: Verify focused and integration behavior**

Run: `python -m pytest tests/test_cascade_delete.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/cli.py tests/test_cascade_delete.py
git commit -m "test: guard all user erasure foreign keys"
```

---

### Task 3: Migrate fresh databases before seeding (F2)

**Files:**
- Modify: `app/db_init.py`
- Modify: `tests/test_db_init.py`

**Interfaces:**
- Consumes: `init_database(app)`, Flask-Migrate `stamp()`/`upgrade()`, seed/backfill helpers.
- Produces: `_FRESH_SCHEMA_STAMP = "aa11bb22cc33"` and the invariant `create_all -> stamp -> upgrade -> seeds/backfills -> lb_rebuild`.

- [ ] **Step 1: Add an ordering regression test**

Extend `test_fresh_init_stamps_trigger_predecessor_then_upgrades` so the patched `upgrade()` records whether `DailyQuest.query.count()` is zero:

```python
def record_upgrade(revision="head", **_kwargs):
    from app.models import DailyQuest
    calls.append(("upgrade", revision, DailyQuest.query.count()))

assert calls == [
    ("stamp", "aa11bb22cc33", True),
    ("upgrade", "head", 0),
]
```

Also add:

```python
def test_fresh_upgrade_failure_commits_no_seed_rows(monkeypatch):
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.delenv("FITX_DB_UPGRADE_FAIL_OPEN", raising=False)
    import flask_migrate
    from app import create_app
    from app.extensions import db
    commits = []
    monkeypatch.setattr(flask_migrate, "stamp", lambda **_kwargs: None)
    monkeypatch.setattr(
        flask_migrate, "upgrade",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("fresh migration failed")),
    )
    monkeypatch.setattr(db.session, "commit", lambda: commits.append("commit"))
    with pytest.raises(RuntimeError, match="fresh migration failed"):
        create_app()
    assert commits == []
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_db_init.py -k "fresh" -v`

Expected: FAIL because the current upgrade observes seeded rows.

- [ ] **Step 3: Move the fresh stamp/upgrade block immediately after `db.create_all()`**

Define and use:

```python
_FRESH_SCHEMA_STAMP = "aa11bb22cc33"
```

For `_has_alembic is False`, call `stamp(revision=_FRESH_SCHEMA_STAMP)` and `upgrade()` before the first `DailyQuest` query. Keep the existing fail-fast handler unchanged. Remove the old post-seed migration block.

- [ ] **Step 4: Verify boot behavior**

Run: `python -m pytest tests/test_db_init.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/db_init.py tests/test_db_init.py
git commit -m "fix: migrate fresh database before seeding"
```

---

### Task 4: Move daily maintenance off request threads (F3)

**Files:**
- Modify: `app/jobs/__init__.py`
- Modify: `app/jobs/tasks.py`
- Modify: `app/hooks.py`
- Modify: `tests/test_jobs.py`
- Modify: `tests/test_hooks.py`

**Interfaces:**
- Produces: `dispatch_background(func, *args, **kwargs) -> dict`, which queues through RQ or starts one daemon thread; `run_daily_maintenance(now_iso: str) -> dict`.
- Consumes: `session_store.purge_expired()`, `mobile_auth.purge_expired(now)`, `notifications.purge_old(now)`.

- [ ] **Step 1: Add dispatch and hook regression tests**

Add to `tests/test_jobs.py`:

```python
def test_dispatch_background_uses_daemon_thread_without_queue(monkeypatch):
    monkeypatch.setattr(jobs, "get_queue", lambda: None)
    started = []
    class ThreadProbe:
        def __init__(self, *, target, args, kwargs, daemon):
            started.append((target, args, kwargs, daemon))
        def start(self):
            started.append("started")
    monkeypatch.setattr(jobs.threading, "Thread", ThreadProbe)
    result = jobs.dispatch_background(lambda: None)
    assert result == {"queued": False, "threaded": True}
    assert started[-1] == "started"
    assert started[0][3] is True
```

Replace the inline purge assertion in `tests/test_hooks.py` with an assertion that `dispatch_background(run_daily_maintenance, now.isoformat())` is called and patched purge functions remain untouched.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_jobs.py tests/test_hooks.py -k "background or purge" -v`

Expected: FAIL because dispatch/task interfaces do not exist and purges run inline.

- [ ] **Step 3: Implement daemon fallback and maintenance task**

In `app/jobs/__init__.py`, import `threading` and add a queue-first helper. On enqueue failure or no queue, start `threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True)` and return immediately. Log thread-start failures without running inline.

In `app/jobs/tasks.py`, parse the ISO datetime and run all three purges inside `_in_app_context`; isolate each purge with its own `try/except`, rollback, and warning so one failure does not skip the rest. Return literal counts/status keys.

In `maybe_weekly_rollover`, retain `_purge_throttle_passed(now)` and call the dispatcher with the top-level task rather than importing purge services.

- [ ] **Step 4: Verify focused behavior**

Run: `python -m pytest tests/test_jobs.py tests/test_hooks.py -k "background or purge or maintenance" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/jobs/__init__.py app/jobs/tasks.py app/hooks.py tests/test_jobs.py tests/test_hooks.py
git commit -m "fix: dispatch daily maintenance off request threads"
```

---

### Task 5: Accumulate streaming usage and finalize before summarization (F4, F5)

**Files:**
- Modify: `app/services/ai_stream.py`
- Modify: `app/services/ai_pipeline.py`
- Modify: `tests/test_ai_stream.py`
- Modify: `tests/test_ai_pipeline.py`

**Interfaces:**
- Produces: `_add_usage(total, current) -> dict | None` with `prompt_tokens` and `completion_tokens` sums.
- Preserves: SSE event contract and once-only deferred summarization.

- [ ] **Step 1: Add literal multi-round usage tests**

Add a unit test for:

```python
assert ai_stream._add_usage(
    {"prompt_tokens": 10, "completion_tokens": 2},
    {"prompt_tokens": 7, "completion_tokens": 5},
) == {"prompt_tokens": 17, "completion_tokens": 7}
assert ai_stream._add_usage(None, None) is None
```

Add a stream test whose first `tool_use` final reports `10/2` and final answer reports `7/5`; assert the emitted `done.usage` is `17/7`.

- [ ] **Step 2: Add the lifecycle-order test**

Construct `gen = ai_pipeline.stream_answer(...)`, consume through the `done` event, and assert the deferred callback has not run. Then call `gen.close()` and assert it ran exactly once; a second close must not add another call.

- [ ] **Step 3: Verify RED**

Run: `python -m pytest tests/test_ai_stream.py tests/test_ai_pipeline.py -k "usage or summarize or deferred" -v`

Expected: FAIL because usage is overwritten and summarization runs before `done`.

- [ ] **Step 4: Implement accumulation and close-time callback**

Add:

```python
def _add_usage(total, current):
    if not current:
        return total
    total = dict(total or {})
    for key in ("prompt_tokens", "completion_tokens"):
        total[key] = int(total.get(key) or 0) + int(current.get(key) or 0)
    return total
```

Replace `usage = _usage_of(final) or usage` with `_add_usage`. In `stream_answer`, remove the pre-yield callback and run it once in `finally` only after a terminal `done` has been yielded or the generator is closed after yielding it. Preserve disconnect persistence.

- [ ] **Step 5: Verify AI stream suites**

Run: `python -m pytest tests/test_ai_stream.py tests/test_ai_pipeline.py tests/test_jobs.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/services/ai_stream.py app/services/ai_pipeline.py tests/test_ai_stream.py tests/test_ai_pipeline.py
git commit -m "fix: preserve streaming usage and terminal latency"
```

---

### Task 6: Reject only unsafe control characters in full names (F6)

**Files:**
- Modify: `app/services/validators.py`
- Modify: `app/blueprints/profile.py`
- Modify: `tests/test_profile_routes.py`

**Interfaces:**
- Produces: `validate_full_name(value: str) -> bool`.
- Preserves: apostrophes, ampersands, quotes, angle brackets, and Unicode letters; Jinja/JS escaping remains the output defense.

- [ ] **Step 1: Add input contract tests**

```python
def test_edit_profile_rejects_control_characters_in_full_name(client, auth_user):
    assert client.post("/edit-profile", json={
        "username": "testuser", "full_name": "Safe\u0000Name"
    }).status_code == 400

def test_edit_profile_allows_legitimate_name_punctuation(client, auth_user):
    value = "O'Connor & Sons <TR>"
    response = client.post("/edit-profile", json={
        "username": "testuser", "full_name": value
    })
    assert response.status_code == 200
    assert _fresh_user(auth_user.id).full_name == value
```

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_profile_routes.py -k "full_name" -v`

Expected: the control-character case FAILS while punctuation passes.

- [ ] **Step 3: Implement the narrow validator**

Use `unicodedata.category(char).startswith("C")` to reject control/format/surrogate/private-use/unassigned characters, while allowing normal whitespace after the route's existing `.strip()`. Call it after the length check and return the existing localized 400 shape.

- [ ] **Step 4: Verify profile behavior**

Run: `python -m pytest tests/test_profile_routes.py tests/test_profile_ui.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/validators.py app/blueprints/profile.py tests/test_profile_routes.py
git commit -m "fix: reject control characters in profile names"
```

---

### Task 7: Cache FatSecret reachability outside deep-health requests (F7)

**Files:**
- Modify: `app/jobs/__init__.py`
- Modify: `app/jobs/tasks.py`
- Modify: `app/__init__.py`
- Modify: `tests/test_jobs.py`
- Modify: `tests/test_extensions.py`
- Modify: `tests/test_health.py`

**Interfaces:**
- Produces: `record_fatsecret_proxy_status(status, sampled_at=None)` and `fatsecret_proxy_status(max_age_seconds=900)` returning `"ok" | "error" | "stale" | "unknown"`; `sample_fatsecret_proxy()` task.
- Consumes: Redis when configured, with a process-local timestamped fallback.

- [ ] **Step 1: Add cache and no-outbound health tests**

Test cache round-trips with and without Redis. In the deep-health test, patch `requests.get` to raise `AssertionError("request path made outbound call")`, record `"ok"`, request `/health?deep=1`, and assert `fatsecret_proxy == "ok"`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_jobs.py tests/test_extensions.py tests/test_health.py -k "fatsecret or outbound" -v`

Expected: FAIL because deep-health calls `requests.get` directly.

- [ ] **Step 3: Implement sampler-owned cache**

Store JSON containing only `status` and UTC epoch under `fitx:fatsecret-proxy:status` with a bounded TTL; mirror it in a process-local dict for Redis-less operation. The task performs the existing URL/timeout/status logic and records the sample. Invoke it from `run_daily_maintenance` after purges, with failure isolated.

Replace request-time HTTP logic in `app/__init__.py` with a read from `fatsecret_proxy_status()`. Preserve `"unconfigured"` when the base URL is empty; map stale/absent samples to `"unknown"`; keep the field non-gating.

- [ ] **Step 4: Verify health and jobs behavior**

Run: `python -m pytest tests/test_jobs.py tests/test_extensions.py tests/test_health.py -v`

Expected: PASS with zero request-time network calls.

- [ ] **Step 5: Commit**

```powershell
git add app/jobs/__init__.py app/jobs/tasks.py app/__init__.py tests/test_jobs.py tests/test_extensions.py tests/test_health.py
git commit -m "fix: serve cached provider health samples"
```

---

### Task 8: Correct the runtime thread-reserve gauge (F8)

**Files:**
- Modify: `app/services/ai_gate.py`
- Modify: `tests/test_ai_gate.py`

**Interfaces:**
- Consumes: `_active_counts` for `ai`, `model`, and `scrape`.
- Produces: `thread_reserve = WEB_THREADS - ai_active - scrape_active - max(0, model_active - ai_active)`.

- [ ] **Step 1: Add the excess-model-activity regression**

Patch counts to `ai=1`, `model=3`, `scrape=1`, and `WEB_THREADS=8`; assert reserve is `4`, not `6`.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_ai_gate.py -k "capacity_snapshot or reserve" -v`

Expected: FAIL with the current over-reported reserve.

- [ ] **Step 3: Implement the ceiling-equivalent calculation**

Compute `outside_route_models = max(0, model_active - ai_active)` and subtract it. Clamp at zero if the existing function already clamps other values.

- [ ] **Step 4: Verify gate tests**

Run: `python -m pytest tests/test_ai_gate.py tests/test_capacity_invariants.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/ai_gate.py tests/test_ai_gate.py
git commit -m "fix: account for standalone model slots in reserve gauge"
```

---

### Task 9: Avoid sparse hydration false positives (F9)

**Files:**
- Modify: `app/services/analytics_engine.py`
- Modify: `tests/test_analytics_engine.py`

**Interfaces:**
- Produces: hydration nudges only with at least three tracked days; average is `sum(count) / len(rows)`.

- [ ] **Step 1: Correct the sparse-data expectations**

Change the two-day, eight-cup test to expect no nudge. Keep the three-day, three-cup test expecting a nudge, and add a three-day, eight-cup test expecting silence.

- [ ] **Step 2: Verify RED**

Run: `python -m pytest tests/test_analytics_engine.py -k "hydration" -v`

Expected: FAIL for adequate sparse hydration.

- [ ] **Step 3: Implement observation-aware averaging**

Use:

```python
if len(rows) < 3:
    return
days = len(rows)
avg_cups = sum(r.count or 0 for r in rows) / days
```

Update nudge copy to say “tracked days” rather than implying seven fully observed days.

- [ ] **Step 4: Verify analytics**

Run: `python -m pytest tests/test_analytics_engine.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add app/services/analytics_engine.py tests/test_analytics_engine.py
git commit -m "fix: suppress sparse hydration false positives"
```

---

### Task 10: Observe confirmation cleanup and refund only failures (F10, F11)

**Files:**
- Modify: `app/services/plan_confirmation/service.py`
- Modify: `app/services/premium.py`
- Modify: `tests/test_plan_confirmation.py`
- Modify: `tests/test_premium_quota.py`

**Interfaces:**
- Preserves: `_settle()` best-effort semantics.
- Changes: successful HTTP status range is `200 <= status < 400`; only `>= 400` refunds.

- [ ] **Step 1: Add cleanup logging test**

Patch `db.session.rollback` to raise `RuntimeError("cleanup failed")`, invoke `_settle()` under an app context with `caplog` at DEBUG, assert no exception escapes and `cleanup failed` is logged without sensitive payload data.

- [ ] **Step 2: Add quota status matrix**

Parametrize wrapped routes returning `200`, `201`, `204`, `302`, `400`, and `500`; spy on `refund_ai_quota` and assert calls occur only for `400` and `500`.

- [ ] **Step 3: Verify RED**

Run: `python -m pytest tests/test_plan_confirmation.py tests/test_premium_quota.py -k "settle or status or refund" -v`

Expected: cleanup produces no log and 201/204/302 incorrectly refund.

- [ ] **Step 4: Implement the narrow changes**

Change `_settle()` to `except Exception: current_app.logger.debug(..., exc_info=True)` and import `current_app`. Change the gate condition from `resp.status_code != 200` to `resp.status_code >= 400`.

- [ ] **Step 5: Verify service suites**

Run: `python -m pytest tests/test_plan_confirmation.py tests/test_premium_quota.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add app/services/plan_confirmation/service.py app/services/premium.py tests/test_plan_confirmation.py tests/test_premium_quota.py
git commit -m "fix: clarify cleanup and quota failure semantics"
```

---

### Task 11: Final verification and remediation report

**Files:**
- Create: `docs/reports/2026-08-28-codebase-triage-remediation.md`
- Inspect: `.github/workflows/*.yml`
- Inspect: `git diff origin/main...HEAD`

**Interfaces:**
- Consumes: all task commits and fresh command output.
- Produces: a source-backed finding disposition and verification record.

- [ ] **Step 1: Re-run the 2026-08-14 focused matrix**

Run the two commands from Task 1 and record exact test counts.

- [ ] **Step 2: Run every touched subsystem together**

```powershell
python -m pytest tests/test_cascade_delete.py tests/test_db_init.py tests/test_hooks.py tests/test_jobs.py tests/test_ai_stream.py tests/test_ai_pipeline.py tests/test_profile_routes.py tests/test_health.py tests/test_extensions.py tests/test_ai_gate.py tests/test_capacity_invariants.py tests/test_analytics_engine.py tests/test_plan_confirmation.py tests/test_premium_quota.py -v
```

Expected: PASS.

- [ ] **Step 3: Run repository static/CI checks**

Inspect workflow commands with `rg -n "pytest|ruff|flake8|mypy|compileall" .github/workflows`. Run every applicable non-deploy check exactly as configured. At minimum run:

```powershell
python -m compileall -q app tests
git diff --check origin/main...HEAD
```

- [ ] **Step 4: Run the full default suite**

Run: `python -m pytest`

Expected: all selected tests pass. If the two baseline `test_adaptive_plan_context.py` setup errors remain, reproduce them on untouched `origin/main`, document them as pre-existing, and do not claim a fully green suite.

- [ ] **Step 5: Write the remediation report**

Include a table for every 2026-08-14 and F1–F11 item with disposition (`fixed`, `verified already fixed`, `accepted as documented`), files changed, regression test names, and verification output. Record the baseline failure comparison explicitly.

- [ ] **Step 6: Verify and commit the report**

Run:

```powershell
git diff --check
git status --short
```

Then:

```powershell
git add docs/reports/2026-08-28-codebase-triage-remediation.md
git commit -m "docs: record codebase triage remediation"
```

- [ ] **Step 7: Review the complete branch**

Run `git log --oneline origin/main..HEAD`, `git diff --stat origin/main...HEAD`, and inspect the complete diff for unrelated changes, secrets, generated artifacts, and missing tests.
