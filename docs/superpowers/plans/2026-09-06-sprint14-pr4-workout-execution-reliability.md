# Sprint 14 PR4 Workout Execution Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close P2-2 and S14-4/S14-7/S14-8 with one cross-transport workout identity authority, fixed-cardinality lifecycle telemetry, and deterministic PostgreSQL proof.

**Architecture:** Preserve native HMAC `workout_ref` re-resolution whenever the row has one. For a scheduled browser-started row without one, the native adapter delegates exercise membership to the existing transport-neutral `planned_exercise_identities()` authority. A small domain telemetry module owns the one metric name and closed event vocabulary, while canonical mutation services emit only committed semantic outcomes.

**Tech Stack:** Python 3.11-compatible Flask, SQLAlchemy, pytest, PostgreSQL 16, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-02-sprint14-pr1-product-engineering-discovery.md` plus `C:\Users\yusuf\OneDrive\Masaüstü\cf-sprint14-pr4.txt`

## Global Constraints

- Base SHA is `642202824acdbfefbb2e7cb45449325c1d7054b3`.
- Do not add a model, table, column, migration, flag, worker, queue, Redis/S3 authority, or inline CloudWatch call.
- Do not touch Flutter, browser JavaScript, production flag values, deployment, PR5, push, PR, or merge.
- Keep `FITX_WORKOUT_SESSIONS_ENABLED` and `RUNTIME_METRICS_ENABLED` defaults and values unchanged.
- Use one final local commit only.

---

### Task 1: Close P2-2 with the existing planned-workout authority

**Files:**
- Modify: `app/services/mobile_workout_sessions/service.py`
- Modify: `tests/test_mobile_workout_sessions_api.py`
- Modify: `tests/test_sprint14_workout_execution_contract.py`

**Interfaces:**
- Consumes: `workout_session.execution.planned_exercise_identities(row) -> tuple[str, ...]`
- Produces: native `checkpoint()` behavior that retains `_session_workout()` for rows with `workout_ref` and uses the planned-session authority only when `workout_ref is None`.

- [ ] **Step 1: Replace the accepted-debt test with a failing convergence test**

  Change `test_a_browser_started_session_is_adopted_but_cannot_be_checkpointed` into a test that starts through the browser authority, proves `workout_ref is None`, checkpoints through `/api/v1`, and asserts revision `1`, the submitted snapshot, one row, and unchanged null `workout_ref`.

- [ ] **Step 2: Add failing plan-drift and completion mirror tests**

  Add tests whose observable assertions are:

  ```python
  assert stale.status_code == 409
  assert stale.json["error"]["code"] == "TRAINING_SESSION_STALE"
  assert row.checkpoint_revision == 0
  assert row.checkpoint_data is None
  ```

  and web-start/native-complete plus native-start/web-complete each terminalize the same public session with exactly one `PumpCheck`, completion marker, XP/activity outcome, and no duplicate row.

- [ ] **Step 3: Run the new tests and verify RED**

  Run: `python -m pytest -q tests/test_mobile_workout_sessions_api.py -k "browser_started or plan_drift" tests/test_sprint14_workout_execution_contract.py -k "cross_transport or native_completion"`

  Expected: browser-start/native-checkpoint fails with `TRAINING_SESSION_STALE`; no test may fail from fixture or syntax errors.

- [ ] **Step 4: Implement the minimal server-owned fallback**

  Import `planned_exercise_identities` from `app.services.workout_session` and route native membership as:

  ```python
  def _session_exercise_ids(user_id, secret, row):
      if row.workout_ref:
          return allowed_exercise_ids(_session_workout(user_id, secret, row))
      return planned_exercise_identities(row)
  ```

  Pass that helper to `record_checkpoint`; do not synthesize or persist a reference.

- [ ] **Step 5: Run focused convergence tests and verify GREEN**

  Run the tests from Step 3 plus the existing native workout-reference drift and native-start/browser-checkpoint tests.

---

### Task 2: Add fixed-cardinality lifecycle telemetry at canonical outcomes

**Files:**
- Create: `app/services/workout_session/metrics.py`
- Modify: `app/services/workout_session/service.py`
- Modify: `app/services/workout_session/execution.py`
- Modify: `app/services/workout_completion/service.py`
- Create: `tests/test_workout_session_metrics.py`

**Interfaces:**
- Produces: `record_lifecycle_event(event: str) -> None`
- Metric: `WorkoutSessionLifecycle`
- Sole dimension: `{"Event": event}`
- Allowed values: `started`, `resumed`, `checkpointed`, `abandoned`, `completed`, `revision_conflict`.

- [ ] **Step 1: Write failing event, replay, privacy, disabled, and failure-safety tests**

  Tests must inspect `runtime_metrics._counters`/`build_metric_data`, not CloudWatch, and assert literal data such as:

  ```python
  assert datum == {
      "MetricName": "WorkoutSessionLifecycle",
      "Dimensions": [{"Name": "Event", "Value": "checkpointed"}],
      "Value": 1.0,
      "Unit": "Count",
  }
  ```

  Exercise real start/replay, resume, checkpoint/replay, abandon/replay, complete/replay, and stale revision behavior. Monkeypatch `runtime_metrics.increment` to raise and assert the original command result/state remains correct.

- [ ] **Step 2: Run the metrics module and verify RED**

  Run: `python -m pytest -q tests/test_workout_session_metrics.py`

  Expected: import or counter assertions fail because the lifecycle metric does not exist.

- [ ] **Step 3: Add the closed telemetry helper**

  Implement a frozen event set and a no-argument-for-dimensions API:

  ```python
  METRIC_NAME = "WorkoutSessionLifecycle"
  EVENTS = frozenset({
      "started", "resumed", "checkpointed", "abandoned", "completed",
      "revision_conflict",
  })

  def record_lifecycle_event(event):
      if event not in EVENTS:
          return
      try:
          runtime_metrics.increment(METRIC_NAME, dimensions={"Event": event})
      except Exception:
          pass
  ```

- [ ] **Step 4: Instrument only durable semantic outcomes**

  Emit `started`, `resumed`, and `abandoned` only from their created/accepted service branches; `checkpointed` only after `advance_checkpoint()` reports a winner; `completed` only after `complete_workout()` returns a newly created completion for a linked session. Emit `revision_conflict` at checkpoint rejection, completion preflight rejection, or locked completion rejection, ensuring any one command reaches exactly one site. Never emit on replay.

- [ ] **Step 5: Run metrics and lifecycle suites and verify GREEN**

  Run: `python -m pytest -q tests/test_workout_session_metrics.py tests/test_runtime_metrics.py tests/test_sprint14_workout_execution_contract.py tests/test_mobile_workout_sessions_api.py`

---

### Task 3: Add deterministic PostgreSQL cross-transport proof and CI selection

**Files:**
- Create: `tests/test_sprint14_workout_execution_pg.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the real PostgreSQL app/session factories and canonical start/checkpoint/completion services.
- Produces: an opt-in `pg_concurrency` module selected by the actual CI PostgreSQL job.

- [ ] **Step 1: Write PostgreSQL tests for the five required invariants**

  Add tests for browser/native concurrent start convergence; different same-base checkpoints (one winner/one conflict/R1/exact whole snapshot); duplicate command delivery (one write/one replay/R1); completion-first ordering (terminal then checkpoint refusal); checkpoint-first ordering (R1 then stale completion refusal with zero artifacts). Synchronize with `threading.Barrier` and `threading.Event`, never sleeps.

- [ ] **Step 2: Add the new module to CI**

  Append `tests/test_sprint14_workout_execution_pg.py` to the `PostgreSQL concurrency` job's explicit pytest file list.

- [ ] **Step 3: Run locally when PostgreSQL is available**

  Run: `$env:FITX_PG_CONCURRENCY_TEST='1'; $env:PG_TEST_DATABASE_URL='postgresql://postgres:postgres@localhost:5432/fitx_mobile_race'; python -m pytest -q tests/test_sprint14_workout_execution_pg.py`

  If no local server is reachable, run without the variables to prove clean collection/skip and record that exact-SHA CI PostgreSQL remains mandatory.

- [ ] **Step 4: Execute mutations M1-M4 against the PG/convergence tests**

  Temporarily remove each load-bearing guard, run its exact test to observe RED, and restore the production file byte-for-byte: checkpoint base predicate; locked completion revision comparison; active-owner uniqueness handling; browser-row fallback.

---

### Task 4: Document evidence, execute M5-M8, and run final gates

**Files:**
- Modify: `app/feature_flags.py`
- Modify: `docs/OBSERVABILITY.md`
- Modify: `docs/superpowers/specs/2026-09-02-sprint14-pr1-product-engineering-discovery.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- Produces: exact operator-visible metric contract, P2/S14 status, CI proof location, and PR5 boundary without changing any flag value.

- [ ] **Step 1: Update bounded documentation**

  Replace the flag record's obsolete “No session-lifecycle metric exists” statement with the exact metric name, `Event` vocabulary, runtime-metrics prerequisite, and privacy guarantee. Add the smallest matching section/query to `docs/OBSERVABILITY.md`. Append PR4 evidence to the sprint spec and a concise current-truth entry to `CLAUDE.md`; preserve prior history.

- [ ] **Step 2: Execute metric mutations M5-M8**

  Prove replay-as-transition, identity dimension, and missing conflict emission each make the relevant test RED. Monkeypatching `runtime_metrics.increment` to raise must keep every execution result GREEN. Restore all mutations byte-for-byte.

- [ ] **Step 3: Run focused, cross-transport, metrics, flag, migration, and static gates**

  Run the exact suites named by the PR4 contract; run `python -m py_compile` for changed Python files, `flask --app starter db heads`, migration/feature/dependency guards, and `git diff --check`.

- [ ] **Step 4: Run the authoritative full regression**

  Run the repository's Windows-compatible pytest partition and report passed, failed, skipped, deselected, and platform exclusions. Run JavaScript tests only if a JavaScript file changed; PR4 should not change one.

- [ ] **Step 5: Re-fetch and review scope**

  Re-fetch `origin/main`, calculate drift/ahead/behind, inspect `git diff --stat` and `git diff --name-only`, answer the 20 self-review questions, and verify no forbidden scope entered the diff.

- [ ] **Step 6: Create one local commit**

  Stage only PR4 files and commit with `fix(training): harden workout execution reliability`. Do not push, open a PR, merge, or approve deployment.
