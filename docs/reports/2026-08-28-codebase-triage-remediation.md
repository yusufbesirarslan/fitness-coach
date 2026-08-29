# 2026-08-28 Codebase Triage Remediation

## Outcome

The actionable findings in `NEEDED_FIXES_2026-08-14.md` and
`docs/TRIAGE_2026-08-28.md` were reproduced or re-verified. The work was written
against `origin/main` `a6d6b2e` and has since been rebased onto the current
`origin/main` **`ad774266e02a509ef41ffe64538ebfb68296ef5f`**, which is the base
this PR must be reviewed against. F1-F11 are covered by regression tests and
fixed. The 2026-08-14 structural god-module note remains an accepted refactoring
observation because it is not a discrete correctness or security defect.

A subsequent review returned NO-GO on three defects that this branch itself
introduced. They are remediated in "Post-review remediation" below. Whether
every actionable finding is closed is a determination for the final review on
the exact pushed head, not a claim this report makes in advance.

## 2026-08-14 disposition

| Finding | Disposition | Evidence |
| --- | --- | --- |
| 1. Weekly water could be completed by same-day toggles | Verified already fixed | Five `test_training_routes.py` water regressions pass, including zero-toggle replay and genuinely-new-day behavior. |
| 2. `/chat` and global request bodies lacked caps | Verified already fixed | Chat rejects over-cap input, accepts the boundary, and the global body ceiling/JSON 413 tests pass. |
| 3. Deferred badge flush could poison the outer transaction | Verified already fixed | Badge deduplication and nested-savepoint rollback tests pass. |
| 4. Ungated model slots could consume the thread reserve | Verified already fixed; telemetry strengthened by F8 | Model-slot deadline/capacity regressions pass; runtime reserve now also counts excess model activity. |
| 5. Mobile refresh held a row lock during Cognito I/O | Verified already fixed | `test_refresh_provider_renewal_runs_without_database_transaction` passes. |
| 6. ProxyFix trusted forwarded host/port | Verified already fixed | `test_proxyfix_ignores_forwarded_host_and_port_but_trusts_for_and_proto` passes. |
| 7. Coach turns lacked a shared deadline | Verified already fixed | Nine OpenAI/Bedrock deadline and timeout regressions pass. |
| 8. Large service/blueprint modules | Accepted as documented | Structural refactoring is intentionally outside this defect-remediation change. |

Fresh verification for this matrix: **30 passed, 302 deselected**.

## 2026-08-28 disposition

| ID | Disposition | Implementation and regression coverage |
| --- | --- | --- |
| F1 | Fixed | `app/cli.py` declares manual cleanup for nonstandard user FKs; `test_purge_user_covers_every_foreign_key_to_user` enumerates mapper metadata rather than column names alone. |
| F2 | Fixed | `app/db_init.py` stamps/upgrades a fresh schema before seed writes. DB-init tests prove upgrade sees zero quest rows and failed upgrade commits no seeds. |
| F3 | Fixed | Daily purges are dispatched through RQ or a daemon fallback, never inline on the request thread. Coverage is described precisely in "Post-review remediation": the original throttle test was vacuous and there was no direct `run_daily_maintenance` failure-isolation test. Both now exist and are mutation-proven. |
| F4 | Fixed | `app/services/ai_stream.py` accumulates usage across every Bedrock tool-loop call. Stream tests assert summed prompt/completion tokens. |
| F5 | Fixed | Worker-less summarization runs in generator finalization after the terminal `done` frame, including immediate `close()`, and runs once. |
| F6 | Fixed | Full names reject Unicode control/invisible categories while preserving legitimate punctuation and Unicode. Profile route regressions cover NUL rejection and `O'Connor & Sons <TR>`. |
| F7 | Fixed | FatSecret reachability is sampled in background maintenance/worker paths and cached with a bounded timestamp/TTL. Deep health performs no outbound HTTP and reports absent/stale samples as non-gating `unknown`. The probe does not follow redirects, and a sampling failure cannot end the worker heartbeat daemon (see "Post-review remediation"). |
| F8 | Fixed | `ThreadReserve` subtracts `max(0, model_active - ai_active)` so out-of-route model work cannot inflate reserve. |
| F9 | Fixed | Hydration nudges require at least three tracked days and average over observed rows; sparse and adequate tracked-day cases are covered. |
| F10 | Fixed | Best-effort plan-confirmation cleanup remains non-fatal but logs failures at DEBUG with traceback context. |
| F11 | Fixed | Premium plan quota refunds only HTTP failures (`>= 400`); the status matrix covers 200, 201, 204, 302, 400, and 500. |

The source report's accepted-tradeoff section remains unchanged: single-worker
in-process concurrency, fail-open migration policy, Redis-less local fallbacks,
and informational worker/FatSecret health remain explicit operational contracts.

## Post-review remediation

Review of the rebased branch returned a valid NO-GO on three defects. Each fix
below is mutation-proven: the production change was reverted and the new test
was observed failing before being restored.

### 1. The maintenance throttle test was vacuous

`tests/test_hooks.py::test_purge_skipped_while_throttle_held` patched
`session_store.purge_expired` and asserted it was never called. That assertion
stopped meaning anything once `maybe_weekly_rollover` was changed to call
`dispatch_background` instead of purging directly: `purge_expired` is not
reached on either branch, so the test passed for **both** throttle outcomes and
proved nothing.

The test now observes the real seam — `jobs.dispatch_background` — and asserts
nothing is dispatched while the throttle is held.

- Mutation: forcing `_purge_throttle_passed` to `True` fails the test with
  `assert [(<function run_daily_maintenance...>, ...)] == []`. Under the old
  test the same mutation still passed.
- No production behaviour was changed: the corrected test exposed no defect in
  `maybe_weekly_rollover` itself.

`run_daily_maintenance` also had no direct coverage. It now has:

- every intended operation executes, in order
  (`sessions`, `mobile_auth`, `notifications`, `fatsecret_proxy`);
- one failing operation is isolated and recorded as `"error"` while every later
  operation still runs — parameterised over all four positions, so isolation is
  proven for the first and last operation alike;
- `db.session.rollback()` occurs exactly once, for the failed operation.

### 2. The #251 worker-health contract was at risk

PR #253 added a FatSecret sample to the worker heartbeat loop. The loop is a
bare `while` in a daemon thread, and the sample was unguarded: an unexpected
exception would not skip one probe, it would end the thread, so every **future**
heartbeat write was lost. The worker would then read dead in `/health?deep=1`
while still consuming jobs.

Smallest compatible hardening, with no redesign of `worker.py` or the queue:

- `sample_fatsecret_proxy` is called through `worker._sample_fatsecret_safely()`,
  which swallows and logs any exception.
- `worker._heartbeat_tick()` writes the heartbeat **first** and independently of
  the optional sample, and returns the next probe timestamp. A failing sample
  still advances that timestamp, so a dead proxy is retried on its interval
  rather than on every tick.
- `requests.get(..., allow_redirects=False)` — a 3xx must classify *this* proxy,
  not whatever the hop points at.

Tests assert that three consecutive ticks with a raising sampler still write
three heartbeats; that a failing sample does not cause a retry storm; that the
heartbeat is written before and independently of the sample; that the probe
passes `allow_redirects=False` and `timeout=3`; and that the protocol is
unchanged — key `fitx:worker:alive`, value `"1"`, TTL `WORKER_HEARTBEAT_TTL`,
and **no other key written**. No new heartbeat protocol is introduced.

### 3. The no-queue fallback built a second application

`dispatch_background` started the daemon thread with `target=func`, so the
thread had no Flask app context. `tasks._in_app_context` then took its *worker*
branch inside the web process: a second `create_app()` with its own SQLAlchemy
engine and connection pool, plus a process-wide `os.environ.setdefault(
"FITX_SKIP_DB_INIT", "1")` that silently disables boot migrations for the real
application. Reverting the fix reproduces this directly — the test run emits
`FITX_SKIP_DB_INIT=1 prod'da AKTIF` from `app/config.py`.

The fallback now follows the existing `ai_pipeline._deferred_summarize` pattern:
`_bind_current_app()` captures `current_app._get_current_object()` while a
context still exists and pushes **that** app inside the thread, then removes the
thread-scoped session so the daemon does not leak its checked-out connection.
When there is no context to inherit (worker/CLI), the task body sets up its own
as before.

Tests assert the daemon runs under the *same* app object, that dispatching the
real `run_daily_maintenance` leaves `tasks._worker_app` `None` with
`app.create_app` patched to raise, and that `FITX_SKIP_DB_INIT` is absent from
`os.environ` afterwards.

### 4. Stale docstring

`premium_ai_plan_gate` still documented refunding on "exception or non-200"
after F11 moved the gate to `>= 400`. The docstring now states the real rule:
2xx/3xx burn the quota, `>= 400` refunds.

## Verification

All figures below were produced on the final head of this branch, after the
post-review remediation, against base `ad774266e02a509ef41ffe64538ebfb68296ef5f`.

| Check | Result |
| --- | --- |
| Full `python -m pytest` (220 files, 8 batches) | **5,487 passed, 12 skipped, 5 deselected** |
| Focused remediation suite (hooks, jobs, worker healthcheck, health, compose, Redis) | **146 passed** |
| Production deploy script tests, trusted-ownership worktree | **343 passed, 5 deselected** |
| `python -m compileall -q app tests worker.py` | Passed |
| `git diff --check` against current `main` | Clean |

The full suite is run in eight batches because a single unrestricted run
contends for the shared Windows user temp root; each batch uses its own
`--basetemp`. Batch totals: 675, 892, 498, 414, 506, 823, 779, 900. There were
no failures and no errors in any batch.

The focused suite covers the subsystems this branch touches: rollover/maintenance
hooks, the background job layer, the worker heartbeat contract, deep health,
Compose configuration, and Redis security. Deprecation warnings for naive
`datetime.utcnow()` calls remain pre-existing cleanup debt and did not fail
verification.

The deploy-script tests are run from a separate worktree owned by the invoking
user. The primary worktree is owned by a sandbox account, and those tests assert
file ownership and permission semantics, so failures observed there are not
trustworthy. Nothing in this branch modifies `scripts/production_deploy.sh`,
`tests/test_production_deploy_script.py`, `tests/test_deploy_control.py`, or
`tests/test_deploy_workflow.py`.

### Mutation checks

Each production change was reverted and the corresponding test observed failing
before the change was restored.

| Mutation | Expected failure | Observed |
| --- | --- | --- |
| `_purge_throttle_passed` forced to `True` | corrected throttle test fails | Yes — `assert [(run_daily_maintenance, ...)] == []` |
| Sampler guard removed from `worker._sample_fatsecret_safely` | heartbeat-survival and throttle-advance tests fail | Yes |
| `allow_redirects=False` removed from the probe | redirect test fails | Yes |
| `dispatch_background` restored to `target=func` | all three fallback tests fail | Yes, and the run emitted `FITX_SKIP_DB_INIT=1 prod'da AKTIF` |

Under the *old* throttle test the first mutation still passed, which is the
evidence that the test was vacuous.
