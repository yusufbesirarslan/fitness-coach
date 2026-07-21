# Task 3 Report: True Default-OFF Runtime Gate

## Status

Implementation, independent review, focused verification, and the scoped commit are complete.

## Commit

- `a10095c feat: gate adaptive coach context`

## Scope Implemented

- Added the sole `AI_ADAPTIVE_PLAN_CONTEXT` environment/config gate.
- The gate is strict opt-in and defaults to false (`"0"`).
- Added the sole enabled-only adapter call in `fetch_coach_context`.
- Injection occurs once after workout history and before supplements.
- The disabled branch has no adapter import, call, log, placeholder, or prompt block.
- Prompt-builder and provider code were not modified.
- The Task 2 adapter contract was not modified.

## TDD Evidence

### RED

Command:

```powershell
python -m pytest tests/test_adaptive_plan_context.py -k "flag or provider_parity or empty_history or user_scoped" -v
```

Result: **3 failed, 3 passed, 17 deselected**.

Expected failures:

1. `test_switching_flag_off_restores_exact_baseline`: enabled context did not contain the adaptive test block.
2. `test_flag_on_injects_once_after_workout_history`: adapter call list remained empty.
3. `test_empty_history_enabled_returns_complete_neutral_contract`: adaptive contract block was absent.

Expected existing/characterization passes:

- OFF exact-baseline/zero-activity test already passed because the pre-change path had no adaptive integration.
- User-scoping adapter coverage already passed from Task 2.
- The initial provider-parity assertion passed vacuously because it compared providers around the same baseline context without first proving adaptive content was present. Review caught this test weakness; the test was strengthened with `assert context.count("[ADAPTIVE TEST BLOCK]") == 1` and rerun successfully.

### First GREEN

After the minimal config, test-env, and shared context-builder integration changes:

```powershell
python -m pytest tests/test_adaptive_plan_context.py -v
```

Result: **23 passed, 45 warnings**.

This included all five original pre-PR4 baseline characterizations unchanged and byte-identical.

### Strengthened parity and fallback continuation GREEN

Added the explicit adaptive-presence parity assertion and the planner-failure continuation regression, then ran:

```powershell
python -m pytest tests/test_adaptive_plan_context.py -v
```

Result: **24 passed, 51 warnings**.

The fallback regression proves that planner failure yields the complete neutral JSON contract and preserves later supplement, nutrition, and friend-activity sections.

## Final Focused Regression Evidence

Command:

```powershell
python -m pytest tests/test_adaptive_plan_context.py tests/test_prompt_builder.py tests/test_ai_pipeline.py tests/test_ai_coach.py tests/test_ai_stream.py -q
```

Initial result: **134 passed, 352 warnings in 16.78s**; exit code 0.

Fresh pre-commit result after final file normalization: **134 passed, 352 warnings in 22.59s**; exit code 0.

No external service calls were observed.

## Files Changed

- `app/config.py`
  - Added strict default-OFF env constant.
  - Published the constant through `configure_app`.
- `app/services/context_builder.py`
  - Added one enabled-only local adapter import/call after workout history and before supplements.
- `tests/conftest.py`
  - Forced the gate to `0` before importing the app package for hermetic default-OFF tests.
- `tests/test_adaptive_plan_context.py`
  - Added OFF zero-cost/byte rollback tests.
  - Added enabled ordering, call-once, provider parity, neutral history, and user isolation coverage.
  - Added planner-fallback continuation coverage.
  - Strengthened provider parity to prove the adaptive block is present exactly once.

The pre-existing dirty changes in `.superpowers/sdd/task-1-report.md` and `.superpowers/sdd/task-2-report.md` were left untouched and will not be staged in the Task 3 commit.

## Self-Review

- Gate name is exactly `AI_ADAPTIVE_PLAN_CONTEXT`; no alternate gate exists.
- Env parsing is strict (`== "1"`) and defaults false.
- `current_app.config.get(..., False)` also remains safe if configuration is absent.
- OFF path performs no adaptive module import, adapter work, log, or prompt mutation.
- Enabled adapter call is shared in `context_builder`, so provider-specific branches were unnecessary and remain untouched.
- Adapter result is appended exactly once in the required order.
- Task 2 public adapter interface remains unchanged.
- No `else`, disabled log, placeholder, alternate serializer, or provider-specific logic was added.
- `git diff --check` passed after adding the final newline.
- Only the four required implementation/test files are intended for staging.

## Independent Review

A read-only reviewer inspected the four-file scoped diff against `6d24339` and the Task 3 brief.

- Critical issues: none.
- Important issues: none.
- Minor issues: none.
- Verdict: ready.
- Optional, non-blocking suggestion: add an isolated config parser test for absent/`"1"` values. This was not added because the exact implementation is already specified by the brief and exercised through the default-OFF and enabled integration suite.

## Concerns

- The focused suite emits pre-existing Python 3.14 `datetime.utcnow()` and SQLAlchemy-related deprecation warnings (352 total); there were no failures and these warnings are outside Task 3 scope.
- The dedicated filesystem patch helper could not launch because `codex-windows-sandbox-setup.exe` was unavailable. Guarded, exact text edits were therefore applied outside that broken helper and validated through syntax checks, scoped diffs, `git diff --check`, and the requested test suites.
## Review Fix: Prove the Absent-Environment Default

Review-fix commit: `4a1f1cf test: prove adaptive context defaults off`
The controller review identified that the original tests forced `AI_ADAPTIVE_PLAN_CONTEXT="0"` in `tests/conftest.py` or set Flask config directly, so they did not catch a regression in the `os.getenv` fallback itself.

### Test Added

`test_adaptive_context_config_is_off_when_unset_or_zero` runs `app/config.py` in an isolated subprocess for two cases: the variable is absent, and it is explicitly `"0"`. The subprocess:

- uses Python `-I` isolated mode;
- removes `PYTHONPATH` and `AI_ADAPTIVE_PLAN_CONTEXT` from its inherited environment;
- runs from a pytest temporary directory;
- replaces `dotenv.load_dotenv` with a no-op module before `runpy.run_path`, preventing any developer `.env` from influencing the probe;
- leaves the already-imported shared `app.config` module untouched.

No runtime code was changed by this fix.

### Mutation RED Evidence

The runtime fallback was temporarily changed from `"0"` to `"1"` inside a guarded try/finally, the narrow test was run, and the original file was restored byte-for-byte before the command returned.

```powershell
python -m pytest tests/test_adaptive_plan_context.py -k "adaptive_context_config_is_off_when_unset_or_zero" -v
```

Result against the temporary mutant: **1 failed, 1 passed, 24 deselected in 5.34s**.

- Absent environment: failed because the loaded constant was `True`.
- Explicit `"0"`: passed.

This demonstrates that the new test detects the exact reviewed regression.

### Restored GREEN Evidence

After confirming `app/config.py` was restored to `os.getenv("AI_ADAPTIVE_PLAN_CONTEXT", "0") == "1"`, the same command produced:

**2 passed, 24 deselected in 6.45s**.

Full adaptive test file:

```powershell
python -m pytest tests/test_adaptive_plan_context.py -v
```

Result: **26 passed, 51 warnings in 10.52s**.

Focused Coach/pipeline regression:

```powershell
python -m pytest tests/test_adaptive_plan_context.py tests/test_prompt_builder.py tests/test_ai_pipeline.py tests/test_ai_coach.py tests/test_ai_stream.py -q
```

Result: **136 passed, 352 warnings in 22.37s**.

### Review-Fix Self-Review

- The test exercises the real `app/config.py` source in a new interpreter.
- It proves both absent-env default OFF and explicit `"0"` OFF.
- It does not mutate the parent process environment or shared imported config module.
- It cannot load the repository/developer `.env` through `python-dotenv`.
- The temporary mutation was restored, and `git diff -- app/config.py` is empty.
