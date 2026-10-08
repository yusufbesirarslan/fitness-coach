# CI-PERF-01: isolated pytest sharding

## Baseline and evidence

Main at `c66c586ccfa84e22c40b050629d09f23ef2a37c4` collects 11,423 selected
items, with eight marker deselections. This change adds 25 infrastructure
regression items: plain root collection and all four shard full manifests agree
on 11,448 exact node IDs. Shard sizes are 2,862 each. Nineteen modules report
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

Without measured file timings, deterministic longest-first packing uses selected
item count per complete file, lexical file ties, then lowest shard load/index.
This is a documented fallback, not a claim of equal runtime. New files enter
automatically. Neither Python's randomized hash nor xdist is used. Within each
runner, original item ordering is preserved. Timing artifacts provide file sums
and per-item setup/call/teardown values for later measured balancing.

Four Ubuntu runners execute one pytest process each, `fail-fast: false`, with a
maximum of four concurrent shards. `tests` remains the matrix job ID. Each
runner uploads a unique JSON artifact containing its exact full selected IDs,
marker-deselected IDs, collection-skipped modules, assigned IDs, revision,
exit status and every executed phase report. The default artifact retention is
14 days. Session interruptions cannot produce successful execution evidence.

The `pytest-gate` job retains the required status name `pytest`, depends on the
entire matrix and runs with `always()`. Its first step rejects any matrix result
except success. Missing/cancelled/skipped/failed shards cannot pass. Downloads
are scoped to the current workflow run. The verifier requires exactly four
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

Local workflow/security/sharding guards: **129 passed in 10.57s**. Full local
collection parity: **11,448 IDs, zero missing, zero duplicates, zero unexpected
deselections**; all four full manifests independently match unsharded collection.
Baseline-to-change selected count difference is entirely the 25 new regression
items. YAML parses; the four existing safety jobs compare structurally identical
to main; `git diff --check` passes.

Optimized GitHub timings and full execution parity must be measured before a
merge-readiness verdict. Compare the slowest shard, aggregate runner-minutes
(including gate and repeated installations/collection), full CI critical path,
and the manifests' phase profiles. Repeat on representative CI runs when
feasible. Duration estimates are not measured speedups. Fixture optimization or
browser lifecycle changes belong in a separately evidenced follow-up.
