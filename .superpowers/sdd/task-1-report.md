# Task 1 Report: Pin the Pre-PR4 Coach Context and Provider Payloads

## Implementation

- Added `tests/test_adaptive_plan_context.py` as a characterization-only test module.
- Defined the exact UTF-8 `BASELINE_CONTEXT` produced by the current coach context builder when profile/trend and nudge inputs are empty and the remaining context sources return fixed sentinel values.
- Added `_stub_baseline_context_sources` to isolate the existing context assembly from database-backed source functions without changing runtime code.
- Characterized the current OpenAI message list, Bedrock plain system string, Bedrock cached content blocks, and identical provider context bytes.
- No production code was modified.

## Test Commands and Results

1. `python -m pytest tests/test_adaptive_plan_context.py::test_pre_pr4_context_bytes_are_characterized -v`
   - Result: `1 passed, 6 warnings in 7.40s`.
   - The planned golden context matched current runtime bytes; no fixture adjustment was needed.
2. `python -m pytest tests/test_adaptive_plan_context.py -v`
   - First provider run: `4 failed, 1 passed` because the fallback PowerShell patch transport had converted the newly appended `VERİSİ` literals to `VER?S?`. This was a test-file transport issue, not a runtime mismatch.
   - Second provider run after the first encoding correction: `4 failed, 1 passed` because the transport had double-decoded those same literals to `VERÄ°SÄ°`.
   - Final pre-commit run after forcing UTF-8 stdin for the patch engine: `5 passed, 6 warnings in 6.04s`.
3. Fresh post-commit `python -m pytest tests/test_adaptive_plan_context.py -v`
   - Result: `5 passed, 6 warnings in 6.36s`.

All warnings are existing `datetime.datetime.utcnow()` deprecation warnings from test/application infrastructure outside this task.

## Characterization Evidence

- `fetch_coach_context(auth_user.id, "question", "tr")` equals `BASELINE_CONTEXT` both as a Python string and as UTF-8 encoded bytes.
- OpenAI currently assembles two system messages followed by history and the current user question; its second system message is exactly `[KULLANICI VERİSİ]\n{BASELINE_CONTEXT}`.
- Bedrock without prompt caching currently concatenates the coach system prompt, two newlines, and exactly the same user-data context string.
- Bedrock with prompt caching currently returns two text blocks: the cached coach system prompt and the uncached user-data context string.
- The OpenAI, Bedrock plain, and Bedrock cached payloads embed identical context bytes.

## Files Changed

- `tests/test_adaptive_plan_context.py` — created, 122 lines.
- `.superpowers/sdd/task-1-report.md` — task handoff report; not part of the production/test commit.

## Commit

- `a6b9975 test: pin pre-PR4 coach context`

## Self-Review

- Compared the complete test file with the exact task brief; names, sentinel values, Turkish literals, payload shapes, and assertions match.
- Confirmed the staged commit contained only `tests/test_adaptive_plan_context.py`.
- Confirmed `git diff --name-only -- app` was empty before commit, so no production file changed.
- Ran `git diff --cached --check` before committing; it reported no whitespace errors.
- Tests assert real prompt/context assembly behavior. Monkeypatching is limited to deterministic source isolation and does not assert mock calls.

## Concerns

- No blocking implementation concerns.
- The Windows sandbox helper (`codex-windows-sandbox-setup.exe`) was unavailable during the task. The parent-provided direct `apply_patch` engine workaround was used, with explicit UTF-8 stdin after detecting and correcting transport corruption.
- Pytest emits six pre-existing `datetime.utcnow()` deprecation warnings.
- Git reports that LF may be converted to CRLF if it later rewrites the new test file; the committed content and runtime UTF-8 assertions are correct.
