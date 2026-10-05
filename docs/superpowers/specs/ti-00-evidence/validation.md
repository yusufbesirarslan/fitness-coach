# TI-00 validation evidence

Date: 2026-10-05. Backend production base:
`1a6ae1d871f734948623441c83de0afe6c4ee019`; mobile inspected/tested main:
`be7c65b5afffb99d9dae06e7c0d4ab9d8960829b`.
Final fetch of both origins succeeded; main SHAs were unchanged. Before commit,
each inspection worktree had divergence `0 0`; mobile remained clean. Original
feature branches were not changed.

## Backend current-contract checks

```sh
/Users/yusuf/develop/fitness-coach/.venv/bin/python -m pytest -q \
  tests/test_ti00_v1_checkpoint_characterization.py \
  tests/test_mobile_workout_sessions_architecture.py \
  tests/test_mobile_workout_sessions_api.py \
  tests/test_sprint14_workout_execution_contract.py \
  tests/test_training_execution_boundary.py \
  tests/test_lp13_no_start_after_completion.py \
  tests/test_lp13_completion_proof_fail_closed.py
```

Initial sandbox run: **282 passed, 2 skipped, 14 errors** in 334.63 s. All 14
errors were Playwright Chromium `BrowserType.launch: Target page, context or
browser has been closed` during fixture setup. Reran the affected browser module
outside sandbox with approved escalation:

```sh
/Users/yusuf/develop/fitness-coach/.venv/bin/python -m pytest -q \
  tests/test_training_execution_boundary.py
```

Result: **17 passed, 2 skipped**, 66.03 s. Two intentional obsolete-browser-global
tests remain skipped in repository code. Across the selected modules, after
replacing the sandbox browser result, **296 unique tests passed, 2 skipped**.
The browser runner uses hermetic intercepted Flask requests and mocked external
proof/storage; it does not write staging or AWS.

New characterization test rerun on its own after non-vacuity experiments:
**8 passed**, 4.45 s. No future V2 production behavior is asserted.

Non-vacuity experiments used `pytest_sessionstart` plugins in separate Python
processes; no production file was edited:

* Temporarily replace `_FINGERPRINT_DOMAIN` in memory with `wrong-domain\0`:
  frozen digest guard fails at fingerprint comparison (pytest exit 1).
* Temporarily allow `actual_rir` in `_SET_FIELDS` in memory: richer-set rejection
  guard fails because parser no longer raises (pytest exit 1).
* Fresh process restores normal imports; all eight tests pass.

## Flutter current behavior

Fresh detached worktree initially had no package metadata, so first `--no-pub`
invocation could not find its test dependency. `flutter pub get --offline`
completed successfully using cached dependencies; tracked files stayed clean.
The subsequent sandbox run could not bind its runner sockets (errno 1), so the
same focused command was rerun outside sandbox with approved escalation:

```sh
flutter test --no-pub \
  test/architecture/workout_session_execution_boundaries_test.dart \
  test/features/workout/domain \
  test/features/workout/data \
  test/features/workout/presentation/active_workout_set_results_test.dart \
  test/app/workout_execution_router_session_test.dart
```

Result: **145 passed** (`00:40 +145: All tests passed!`). Includes canonical-first
resume/restart, one writer, pending/ack separation, completion/refusal, account
fences, store races, repository/DTO checks and LP13 typed logging regression.
No Flutter source or test was edited in TI-00.

## Separate existing browser baseline failure

```sh
node --test tests/js/workout_checkpoint_client.test.js
```

Result: **22 passed, 1 failed**. Failure:
`an acknowledged checkpoint hydrates a separate page instance after reload`,
`tests/js/workout_checkpoint_client.test.js:550`. Actual:

```text
{index:0, weightKg:100, reps:6, done:true, isPR:false, repsExplicit:true}
```

Expected omits `repsExplicit:true`. `static/workout_draft.js:hydrateExercises`
sets the flag for non-null saved reps. `git diff origin/main --
static/workout_draft.js tests/js/workout_checkpoint_client.test.js app` is empty.
The failure therefore exists on the pinned main source, not from TI-00. Neither
production behavior nor test expectation was repaired in this PR. It is recorded
in the architecture's separate correctness findings and TI-01 browser merge gate.

## Other checks and limitations

* `git diff --check`: passed.
* `python -m compileall -q tests/test_ti00_v1_checkpoint_characterization.py`: passed.
* Final changes are architecture/evidence documentation and one characterization
  test module only. Existing production, browser, migration, flag, and mobile
  files remain untouched.
* No real PostgreSQL race run in TI-00; concurrency conclusions are code/existing
  test-source facts. Real PG races are mandatory TI-01/TI-05 gates, not claimed
  from SQLite. No migration run: TI-00 creates none.
* No physical iPhone, staging, AWS, plan provider, or rollout activation performed.
* No LP21/LP22/LP23 definitions found in either fetched main's docs; source was
  requested from owner. Exact mapping remains unverified and is a release
  integration blocker. The architecture defines gate behavior without inventing
  numbered assignments.
* No full backend suite or mobile release build claimed. Focused current-contract
  tests are appropriate to docs/characterization scope; future implementation
  has its separate comprehensive test and merge gates.

Local execution logs (not required to interpret this durable summary) were saved
under `/private/tmp/ti00-*`: backend-validation, browser-boundary-unsandboxed,
characterization-green, nonvacuity, nonvacuity-schema, browser-validation,
mobile-pub, mobile-validation and mobile-validation-unsandboxed.
