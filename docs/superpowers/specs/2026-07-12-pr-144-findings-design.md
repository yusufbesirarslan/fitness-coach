# PR #144 Findings Verification and Remediation Design

## Goal

Verify every finding recorded in `FIXES.md` against `origin/main`, preserve fixes
that already landed, and close each remaining actionable gap with the smallest
safe change and an isolated regression test.

## Source of truth and baseline

- Audit target: `origin/main` at merge commit `a4b25de`.
- Working branch: `fix/pr144-findings`, created from that commit in an isolated
  worktree.
- Baseline: `python -m pytest -q` reports 1,270 passed and no failures.
- `FIXES.md` is evidence to verify, not an assumed-current specification.

## Audit disposition

### Priority findings

1. **Meal dates without a year — resolved.** Commit `2e0f141` introduced the
   Istanbul-aware ISO day key and data migration. The model, write paths, read
   paths, and tests use `YYYY-MM-DD`.
2. **Committed backup in the production image — partially resolved.** Commit
   `07f918d` removed `starter.py.bak`; the recommended build-context exclusions
   for backup files, tests, workflow metadata, and repository documentation are
   still absent.
3. **No migration strategy / boot-time DDL — partially resolved.** Commits
   `07f918d` and `69ca9b9` established Alembic and removed the legacy `ALTER`
   loop. `app/db_init.py` still creates the PostgreSQL activity-calorie function
   and trigger during every boot.
4. **Stale `CLAUDE.md` — resolved.** Commit `07f918d` and subsequent updates
   describe the application factory, current model set, Redis, RDS, migrations,
   and deployment constraints.
5. **Malformed check-in/activity numbers — resolved.** Commits `09e742b` and
   `a911bc2` route these values through safe coercion.
6. **One serving-weight LLM call per result — resolved.** Commit `b8c5851`
   batches unique serving names into one estimator call.
7. **Incomplete-profile weight update crash — resolved.** Commit `32560f9`
   centralizes profile-safe recalculation and preserves incomplete profiles.
8. **Unthrottled authenticated writes — partially resolved.** Commit `0513d7e`
   protects friend request, chat, and suggestion writes with user-aware limits.
   The named nutrition, supplement, water, and activity writes still have only
   the global IP-keyed default.
9. **Dead `register_hooks` duplicate — resolved.** Removed by `9f11924`.
10. **Missing route/CI tests — resolved.** Commit `324b071` added CI and the
    current suite covers application, route, auth, database, and service paths.
11. **Loose/runtime dependency mixture — partially resolved.** Commit `b068b31`
    pinned versions, but pytest, coverage, and MCP CLI tooling are still installed
    in the web image.
12. **Orphaned/misnamed 500 page — resolved.** Commit `8aca29d` renders
    `templates/500.html`; `9f11924` removed `505.html`.

### Lower-priority findings

- Tracking numeric `TypeError` paths are resolved, but `/chat` still catches
  only `ValueError` for numeric JSON fields and can return 500 for arrays or
  objects. This remains actionable.
- Redis authentication remains absent. This remains actionable, with an explicit
  deployment prerequisite described below.
- FatSecret plaintext-token paths are resolved by `09e742b`; both Flask and MCP
  enforce HTTPS outside loopback.
- The progress-message concatenation bug is resolved.
- Friend-search enumeration is mitigated by the explicit limit added in
  `ab65cb3`, matching the report's recommended remedy for a discoverable social
  username feature.
- The account-scoped login failure budget is intentionally retained. It limits
  distributed password guessing; removing it would trade a short, self-healing
  denial-of-service window for weaker authentication. No safe literal removal is
  in scope.
- `analytics_engine.py` and `nutrition_pipeline.py` remain root-level and are
  imported through repository-root path assumptions. This remains actionable.
- Railway remnants are resolved by `9f11924`, and the CLI documentation now
  describes EC2 cron.
- `.env.example` still omits active `LOG_LEVEL` and `FLASK_ENV` settings. `PORT`
  is no longer consumed and will not be documented as active.
- Commit `4384123` made rollover checks fleet-safe, but Gunicorn still hard-codes
  one worker. Worker count can safely become configurable while retaining one as
  the default.

### Previously ruled-out claims

The current code and tests continue to support the report's non-findings:

- `respond_suggestion` initializes `nutrients` and safely handles workout,
  accepted meal, rejected, and failed-macro paths (`5a9b5b6`).
- First-supplement XP is serialized with a user-row lock and covered by route
  tests.
- Weekly rollover is idempotent through `WeeklyResetLog` and database uniqueness.
- Coach tool dispatch injects the authenticated principal's user ID rather than
  accepting an LLM-provided ID.
- Menu fetch validates every redirect, blocks private/metadata addresses, and
  pins resolved destinations; tests cover these boundaries.
- Raw SQL values remain parameterized or static.
- Avatar, username, password, cookie, CSRF, CSP, and ownership controls remain in
  place and covered by the existing security tests.

## Remediation design

Changes proceed in the priority order from `FIXES.md`. Resolved findings receive
no code churn.

### 1. Production build context

Extend `.dockerignore` with `*.bak`, `tests/`, `.github/`, repository Markdown,
and developer-only requirement files. Add a source-level deployment test so a
future backup or test-tree regression cannot silently re-enter the image.

### 2. PostgreSQL trigger migration

Add one Alembic revision after the current migration head. On PostgreSQL it will
create `calc_activity_calories` and `trg_calc_activity`; on other dialects it will
do nothing. Downgrade will drop the trigger and function on PostgreSQL. Remove
the corresponding DDL and exception-swallowing block from `init_database`, so
schema evolution has one owner. Migration graph and `db_init` tests will verify
the transition without requiring PostgreSQL for the unit suite.

### 3. Authenticated write throttling

Introduce one configurable `AUTH_WRITE_RATELIMIT` and apply it with
`_user_or_ip_key` to the unprotected state-changing routes named by the report:
nutrition diary writes, supplement add/edit/delete, water updates, and daily
activity logging. Existing AI and social limits remain unchanged and are not
stacked redundantly. Tests will inspect registered limits and exercise a small
limit in an isolated app where practical.

### 4. Dependency boundaries

Keep `requirements.txt` limited to the web runtime. Add
`requirements-mcp.txt` for MCP CLI/runtime tooling and `requirements-dev.txt`
for MCP plus pytest/coverage. CI installs the development file; Docker continues
to install only the web runtime. Tests will assert that production requirements
contain neither MCP CLI nor test packages and that the layered files include the
correct base.

### 5. Malformed coach numerics

Add a route regression test proving array/object numeric values return 400, watch
it fail with the current 500, then catch `(ValueError, TypeError)` in the numeric
conversion block. No broader validation behavior changes.

### 6. Redis authentication

Require `REDIS_PASSWORD` in Compose, start Redis with `requirepass`, authenticate
its health check, and document a password-bearing `REDIS_URL`. Configuration
tests will assert the server, client URL example, and health check agree.

Operational prerequisite: before deploying this commit, production must set a
strong `REDIS_PASSWORD` and update `REDIS_URL` to the matching URL-encoded
password. The repository will not generate, store, or commit the secret.

### 7. Service module placement

Move `nutrition_pipeline.py` and `analytics_engine.py` into `app/services/` and
update application, MCP, and test imports to their package paths. The modules'
public functions and behavior remain unchanged; this is a path-only refactor.
Targeted module and consumer tests will run before the full suite.

### 8. Environment documentation

Document `LOG_LEVEL`, `FLASK_ENV`, authenticated Redis, and worker configuration
in `.env.example`. Do not document unused `PORT` as a supported application
setting.

### 9. Configurable Gunicorn workers

Add a small `gunicorn.conf.py` that reads `FITX_WEB_WORKERS` and
`FITX_WEB_THREADS`, retaining defaults of one worker and eight threads. Docker
will invoke Gunicorn through this configuration without a shell. Tests will
verify defaults and environment overrides. Documentation will warn that Redis
must be available and per-process AI concurrency budgets multiply by worker
count.

## Error handling and compatibility

- Migration failures remain fail-fast under the existing database policy.
- SQLite tests and local development do not attempt PostgreSQL trigger DDL.
- Rate-limit responses continue through the shared localized 429 handler.
- Redis authentication intentionally fails deployment when the required secret
  is absent rather than silently running without authentication.
- The default web concurrency is unchanged.
- Module relocation changes import paths only, not function signatures or data
  contracts.

## Verification strategy

Each actionable cluster follows red-green-refactor independently: write or
strengthen the focused test, observe the expected failure, apply the smallest
change, rerun the focused test, then continue. Final verification includes:

1. All focused tests for deployment, migrations, routes, dependencies, module
   imports, and Gunicorn configuration.
2. `python -m pytest -q` for the complete suite.
3. `flask --app starter db heads` and migration graph checks.
4. `git diff --check` and a final review of changed files against this spec.

No GitHub comments will be posted and no review threads will be resolved unless
separately requested.
