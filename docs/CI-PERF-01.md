# CI-PERF-01: isolated pytest sharding

## Baseline and evidence

Main at `c66c586ccfa84e22c40b050629d09f23ef2a37c4` collects 11,423 selected
items, with eight marker deselections. This change adds 25 infrastructure
regression items: plain root collection and all four shard full manifests agree
on 11,448 exact node IDs. The initial count-balanced shard sizes were 2,862 each;
the measured-duration partition selects 2,809 / 3,041 / 2,912 / 2,686 items.
Nineteen modules report
collection skips locally; these remain collection skips, not fabricated items.

Verified GitHub step timestamps (seconds):

| Run | Dependency installation | Test step | pytest job |
| --- | ---: | ---: | ---: |
| [37585279052](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/37585279052) | 44 | 3,212 | 3,266 |
| [37671038757](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/37671038757) | 82 | 3,149 | 3,238 |
| [37726080376](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/37726080376) | 30 | 2,456 | 2,493 |

The last run's pytest summary is 11,346 passed, 96 skipped, eight deselected in
2,346.48 seconds. The test step also includes output/reporting overhead. The
pytest job dominates the CI critical path; its baseline allocation is roughly
41.6–54.4 runner-minutes. Historical runs do not include phase-duration reports,
so they cannot establish whether app creation, database setup, browsers or test
calls dominate. Do not interpret selected-item count as a duration measurement.

Representative local profiling of `test_auth.py` and
`test_ux4_pr2_foundations_browser.py` passed 106 tests in 245.59 seconds. The
slowest call was a browser keyboard-focus check (13.63 seconds); the slowest
setup was also a browser fixture (5.86 seconds). Calls dominate the top-30
duration list. This sample establishes meaningful browser costs, but does not
measure the whole suite or isolate app creation from browser startup. An initial
sandboxed browser run could not launch Chromium; the authorized unsandboxed
rerun passed. It is not evidence of a test isolation failure.

Shared `app` fixtures create an app, create all SQLite tables, yield a context,
remove the session and drop all tables for each test. Browser fixtures launch
Chromium per test; representative browser modules route HTTP through Flask's
client instead of owning a listening port. Other tests use subprocesses, files,
environment variables and mutable module state. File boundaries keep local
fixture and test ordering intact; each runner still imports the full suite, then
executes only its assigned files in original collection order. Cross-file
execution-state dependencies must be diagnosed if CI exposes them. No fixture
scopes, assertions or production behavior change.

## Mechanism and gate

`scripts/pytest_sharding.py` is an explicitly loaded CI-only pytest plugin.
Every shard uses the same default repository-root discovery and existing
`pytest.ini`. It partitions only after pytest's marker selection finishes.
Explicit narrower discovery paths are rejected.

Deterministic longest-first packing uses measured setup + call + teardown seconds
per complete file from `scripts/pytest_file_timings.json`, with lexical file ties,
then lowest shard load/index. The reviewed profile records 392 files from the
first successful sharded CI execution, with its run URL and checkout revision.
Measured file cost scales by current selected count divided by measured count.
New files use the profile's mean seconds per selected item; if no profile exists,
all files use selected item count instead. Malformed profiles fail closed. Every
manifest records the profile's SHA-256 digest, which must match the verifier's
checked-out profile. This prevents execution and validation from using different
weights. New files enter automatically. Neither Python's randomized hash nor
xdist is used. Within each
runner, original item ordering is preserved. Timing artifacts provide file sums
and per-item setup/call/teardown values for later measured balancing.

Four Ubuntu runners execute one pytest process each, `fail-fast: false`, with a
maximum of four concurrent shards. `tests` remains the matrix job ID. Dependency
installation has a ten-minute step deadline: an outage fails the shard and the
required gate; it cannot produce test-success evidence. Test execution itself
retains the existing deadline behavior. Each
runner uploads a unique JSON artifact containing its exact full selected IDs,
marker-deselected IDs, collection-skipped modules, assigned IDs, revision,
exit status and every executed phase report. Artifact names include the workflow
attempt number, so reruns cannot reuse earlier-attempt evidence or collide with
prior uploads. The default artifact retention is
14 days. Session interruptions cannot produce successful execution evidence.

The `pytest-gate` job retains the required status name `pytest`, depends on the
entire matrix and runs with `always()`. Its first step rejects any matrix result
except success. Missing/cancelled/skipped/failed shards cannot pass. Downloads
are scoped to the current workflow run and attempt. The verifier requires exactly four
distinct, successful, revision-matching execution manifests. It checks identical
authoritative collections, deselections and collection skips; recomputes the
partition; checks disjoint exact union parity; and requires a complete successful
or legitimately skipped execution for each assigned ID, including teardown.
Collection-only evidence is accepted solely through an explicit local validation
flag, never by the CI gate. Malformed or missing fields fail the command.

Main's active `Protect main` ruleset requires `pytest`, `schema-drift guard`,
`PostgreSQL concurrency`, `authoritative Linux production locks` and
`authoritative image revision immutability`. All those names remain stable.
The four existing safety job definitions and deploy workflow are unchanged.
Deployment still requires successful completion of the entire `CI` workflow on
the exact main push revision. All new actions use immutable commit SHA pins.
No AWS operations, production code, models, migrations, runtime Docker settings
or release approvals are changed.

## Reproduction

Use a Python 3.11 environment with `requirements-dev.txt` and Chromium installed:

```sh
python -m pytest -q tests/test_pytest_sharding.py tests/test_deploy_workflow.py tests/test_dependency_boundaries.py
python -m pytest --collect-only -q
python -m pytest --collect-only -q -p scripts.pytest_sharding --ci-shard=0 --ci-manifest=evidence/shard-0.json
# Repeat the preceding command for indices 1, 2 and 3.
python -m scripts.pytest_sharding evidence --revision="$(git rev-parse HEAD)" --result=success --collection-only
python -m pytest -q --durations=30 tests/test_auth.py tests/test_ux4_pr2_foundations_browser.py
```

For actual shard execution omit `--collect-only`, add `--durations=50`, and
validate without the verifier's `--collection-only` flag. Full discovery occurs
in all four processes, so module import skips may appear four times in the logs;
the gate compares them separately from selected test IDs.

Local workflow/security/sharding guards: **129 passed in 10.57s** initially and
**129 passed in 9.53s** after measured balancing. Guards including the installation
deadline passed again: **129 in 18.54s**. Full local
collection parity: **11,448 IDs, zero missing, zero duplicates, zero unexpected
deselections**; all four full manifests independently match unsharded collection.
Baseline-to-change selected count difference is entirely the 25 new regression
items. YAML parses; the four existing safety jobs compare structurally identical
to main; `git diff --check` passes.

## Initial GitHub measurements

Both initial count-balanced runs passed all shards, the aggregate parity gate
and all four safety jobs. Both proved 11,448 selected IDs, zero missing/duplicate
items and zero unexpected deselections. Execution is 11,371 passed plus 77
runtime skips; the 19 distinct import-skipped modules give the original 96 skips
after normalization. Summing shard terminal skip counts repeats import skips and
is not a valid full-suite count.

| Run | Slowest pytest process | Slowest test step | CI critical path | pytest jobs + gate runner-minutes | Shard installs |
| --- | ---: | ---: | ---: | ---: | --- |
| [37733904015](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/37733904015) | 984.50s | 1,047s | 1,121s | 57.18 | 43–50s |
| [37735782838](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/37735782838) | 970.68s | 1,035s | 1,097s | 54.70 | 39–46s |

The baseline last-run CI path was 2,497 seconds; the first two sharded paths were
55.1% and 56.1% shorter. This is a measured cross-run comparison, not a controlled
same-revision sequential benchmark. Baseline pytest allocation was 41.55 minutes
on the fastest supplied run and 53.97–54.43 on the other two verified runs. The
sharded allocations are within 0.5–5.9% of those two slower baselines, but
31.6–37.6% above the fastest baseline. Repeated setup/import work and runner
variation both matter. Latency improvement does not imply lower total cost.

Count balancing gave unequal measured pytest times (449.78–984.50 seconds in
the first run). Full phase reports total 1,029.65 setup seconds, 1,772.97 call
seconds and 97.57 teardown seconds. Browser-named modules account for 1,629.48 of
2,900.18 phase seconds (56.2%); browser helpers in other named files are additional
cost. The largest files are `test_ux4_pr8_browser.py` (242.07s),
`test_ux4_pr2_foundations_browser.py` (205.01s),
`test_nutrition_vnext_pr5_plan_browser.py` (150.24s) and
`test_nutrition_vnext_pr4_log_food_browser.py` (111.91s). The slowest operation is
a browser notification matrix call (26.87s). These are aggregated test phases,
not separate measurements of `create_app()` or SQLAlchemy internals. Changing
fixture scopes or caching database setup is not justified by this evidence.

The first measured-duration attempt
([37737768943](https://github.com/yusufbesirarslan/fitness-coach/actions/runs/37737768943))
passed three shards but stalled in dependency installation on the fourth. It was
canceled after over 16 minutes of installation without starting pytest there.
This is explicitly incomplete execution evidence, not a passing benchmark.
Pip had completed; the last progress was APT repository update invoked by
Playwright. The log cannot prove the underlying network or mirror cause.
The required `pytest` gate still ran after cancellation, observed
`SHARD_RESULT: cancelled` and failed with exit code 1. The missing shard manifest
also made that shard's evidence upload fail. This is an observed negative-path
proof, not merely a workflow-text assertion. An install-step deadline now makes
this class of stall fail closed sooner. The
final run results, exact commands and merge verdict are maintained in
[PR #411's engineering report](https://github.com/yusufbesirarslan/fitness-coach/pull/411).
Compare the slowest shard, aggregate runner-minutes (including gate and repeated
installations and collection), full CI critical path and phase profiles.
Duration estimates are not measured speedups. A separate follow-up could profile browser startup and
matrix calls in the four largest files while preserving all assertions and
per-test isolation. Production refactoring and broad fixture caching remain
outside this task.
