# Task 2 Report: Move Activity Trigger DDL into Alembic

## TDD evidence

### RED

Command:

```text
python -m pytest tests/test_db_init.py::test_db_init_contains_no_schema_trigger_ddl tests/test_migration_graph.py -q
```

Result: exit code 1, 3 failed. The failures were expected and specific:

- `app/db_init.py` still contained `CREATE OR REPLACE FUNCTION calc_activity_calories`.
- The migration graph still ended at `aa11bb22cc33` rather than `bb22cc33dd44`.
- `bb22cc33dd44_activity_calorie_trigger.py` did not yet exist.

No production code was changed before this RED run.

### GREEN

Command:

```text
python -m pytest tests/test_db_init.py tests/test_migration_graph.py -q
```

Result: exit code 0, 7 passed, 3 pre-existing deprecation warnings.

Alembic head check:

```text
$env:FLASK_DEBUG='1'; python -m flask --app starter db heads
```

Result: exit code 0, `bb22cc33dd44 (head)`.

The bare `python -m flask --app starter db heads` was also attempted first. It stopped during application configuration because this shell has neither `DATABASE_URL` nor development mode configured. Setting the documented local `FLASK_DEBUG=1` SQLite fallback allowed the same Flask command to complete.

## Implementation

- Added Alembic revision `bb22cc33dd44`, chained from `aa11bb22cc33`.
- Guarded both upgrade and downgrade so non-PostgreSQL dialects return without executing trigger DDL.
- Moved the existing `calc_activity_calories` function logic without behavioral edits.
- Upgrade creates/replaces the function, drops any prior trigger, and creates `trg_calc_activity`.
- Downgrade drops the trigger and function idempotently.
- Removed the complete trigger DDL/exception block from `init_database` while preserving migration/upgrade handling, `db.create_all()`, stamping, and quest seeding.
- Added source regression coverage for boot-time DDL removal, migration guards, create/drop SQL, and the single migration head.

## Files changed

- `app/db_init.py`
- `migrations/versions/bb22cc33dd44_activity_calorie_trigger.py`
- `tests/test_db_init.py`
- `tests/test_migration_graph.py`
- `.superpowers/sdd/task-2-report.md`

## Verification and self-review

- Compared the original boot-time function SQL with `CREATE_FUNCTION_SQL` after trimming indentation; all SQL tokens match.
- `git diff --check` passed.
- Focused covering tests passed: 7 passed, 0 failed.
- Flask-Migrate reports exactly one head: `bb22cc33dd44`.
- Revision identifiers and PostgreSQL guards match the brief exactly.
- Diff review found no changes to migration/stamp behavior or quest seeding.
- Scope is limited to the four implementation/test files named in the brief plus this required report.

## Concerns

- No live PostgreSQL instance was available, so the PL/pgSQL trigger was verified by exact source comparison and migration source tests rather than execution against PostgreSQL.
- Test and Flask output contains existing Authlib, SQLAlchemy, and `datetime.utcnow()` deprecation warnings; there were no new failures.
