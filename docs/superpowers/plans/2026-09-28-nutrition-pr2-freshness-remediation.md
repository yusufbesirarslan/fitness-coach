# Nutrition PR2 freshness remediation

Execute inline on the existing PR2 worktree. The owner's task.txt is the binding
specification; its execute instruction authorizes this bounded remediation and
local commit without a new approval handoff. No remote writes.

## Baseline gate

Starting HEAD: 648f94568cf37b74a79c1ac6ff482758d1ccb509.
Fetched main: f4b5fcd832b3c2c202d3334db58fa1d537e1fdcf.
Exact-main CI: 36376686878, completed/success.
Intervening commit #357 changes Coach/Progress, including shared Coach locale
keys. Inspection found no semantic Nutrition overlap.

## Steps and verification

1. Add real Chromium/Flask tests in
   `tests/test_nutrition_vnext_pr2_freshness_browser.py`. Seed one MealLog with
   200 kcal and one staging CustomMeal with 525 kcal; click the real Diary log
   control, assert the server commits and visible Today moves to two meals,
   725 kcal and 40/80/17 macros. Capture initial server truth, POST response,
   browser requests and visible Today/Diary/History before assertions. Exercise
   History open and closed, EN/390 and TR/1366. Run against unchanged HEAD and
   retain the expected assertion failures in evidence.
2. Reuse `loadTodayData()` after successful `logDiaryMeal()`; retain `loadDiary()`.
   If open History is stale, call existing `loadMealHistory()` only while Today
   and History are visible. Keep navigation and disclosures unchanged. Check
   response status before considering a mutation or canonical read successful.
   On Today refresh failure retain last confirmed DOM and show an existing
   localized unavailable/error toast; never render an error object as zero.
3. Run freshness tests to green. Add scoped, in-memory browser script mutations
   for missing Today read, stale canonical response, count-only DOM patch,
   missing visible History read and eager closed History read. Assert the
   normal behavioral tests fail; reuse the existing IA mutation for Diary tab.
4. Reconcile onto the exact green main before final validation using an explicit
   stash of this remediation, rebase existing PR2 commit, then restore the stash.
   Record old/new HEAD and range-diff original versus rebased PR2 semantics.
5. Run dedicated PR2 contracts/browser/non-vacuity, relevant Nutrition/Diary/
   History/plan/legacy/i18n suites, JS rendering, node syntax and diff whitespace
   checks. Run the repository test command and report every remaining failure.
   Recheck exact-main SHA/CI before local commit.
6. Request one fresh-context whole-branch review, per executing-plans and
   requesting-code-review skills. Resolve blocking findings with regression
   evidence. Commit a separate bounded remediation on the same branch and
   report the owner's 19 required items, with no push, PR, merge or deploy.

## Review focus

Committed meal versus failed mutation; HTTP versus network read failure;
History closed at commit and opened later; History open beneath hidden Plan;
canonical aggregate assertions independent of submitted form values.

## Ledger

- Setup: authoritative branch and clean starting worktree confirmed; main gate
  green; no Nutrition overlap.
- Test harness: first diagnostic run hit console encoding and two selector
  errors; corrected before treating it as red regression evidence.

- Reproduction: 6 expected failures / 1 pass on unchanged production; canonical 2 meals and 725 kcal versus visible 1 meal and 200 kcal, open History likewise stale.
- Reconciliation: old HEAD 648f94568cf37b74a79c1ac6ff482758d1ccb509 to rebased HEAD 8214ed99003e469882849ea9d448c4a44be16694; conflict-free, range-diff equivalent, merge-base f4b5fcd832b3c2c202d3334db58fa1d537e1fdcf.
- First bounded fix: 7 browser cases passed and all 5 freshness mutations caught before rebase.
- Independent whole-branch review: 2 blocking P2 timing findings, no other material findings. Hidden open History while Plan is selected: 1 RED -> 1 GREEN. Older overlapping Today/History responses: 2 RED -> 2 GREEN. Added controlled mutations for all three timing protections.
- Ruling: hidden History invalidates its UI loaded marker rather than fetching eagerly; return/open fetches current canonical data. Cost if wrong: stale History on return, covered by delayed commit regression and mutation.
- Ruling: latest-request generation counters control rendering order only, never meal authority. If a newer read fails, retain last confirmed DOM with the existing localized unavailable toast. Cost if wrong: lost freshness or a stale overwrite; HTTP/network/delayed-response cases and mutations cover it.
- Focused broad set: 357 passed, 1 CRLF mutation-setup failure. Normalized only delivered test script bytes; final PR2 set passes 50/50 (10 behavioral cases, 8 freshness mutations, 32 existing contracts/browser/IA mutations). 314 other focused regressions passed. Plan rendering 9/9; initial route query count 9.
- Validation environment: default temporary-directory access errors; PowerShell UTF-16 diagnostics; inherited subprocess encoding; Git Bash ancestor traversal blocked by sandbox. Used fresh verified worktree-local temp/cache directories, UTF-8 process environment and approved unrestricted hermetic test execution. Four temporary-directory reproductions pass after correction; all 7 remaining reproductions pass after correction. No repository/backend test logic changed. Interrupted the invalid full-suite attempt at 23 percent to correct the environment.
- Final full repository suite is running against the final implementation with that corrected test context.

- Additional ordering regression: a newer failed ordinary read must retain the post-commit unavailable warning, including a hidden pre-commit History response released after mutation. Three real-browser RED -> GREEN cases; four additional controlled mutations. Final expanded PR2 suite: 57 passed (13 behavioral, 12 freshness mutations, 32 existing PR2 cases).
- Full monolithic run completed: 8002 passed, 62 skipped, 8 deselected; 160 failures and 123 errors. The 22 Progress Node cases exceed Windows command-line length; identical scripts via stdin pass 22/22 without changing tests or source. Remaining browser failures follow Windows file-descriptor exhaustion near 89 percent and are being rerun in isolated processes.
- Final read-only remote gate: origin/main remains f4b5fcd832b3c2c202d3334db58fa1d537e1fdcf, CI run 36376686878 completed success, no branch PR exists.

- Full-suite closure: all 282 unique failing/error cases pass in isolated reruns (260 browser/contract cases and 22 unchanged Node scripts through stdin). Original run: 8002 passed, 62 skipped, 8 deselected. No remaining functional failures; the monolithic Windows run itself was not green. Test logic and product source were unchanged by environment corrections.
- Final implementation syntax and all 9 Node plan-rendering cases pass. The expanded PR2 57-case suite covers the final source. Remediation remains bounded to canonical reads, UI response ordering and existing unavailable semantics.
