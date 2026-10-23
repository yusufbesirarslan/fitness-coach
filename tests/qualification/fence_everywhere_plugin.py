"""Pytest plugin: run PostgreSQL suites with the REVIEWED completion fence installed.

PRIV-K6 rollback gate, Layer A qualification. Synthetic, disposable databases
only. Every ``db.create_all()`` against PostgreSQL is followed by the reviewed
migration's own ``upgrade()`` (the fresh-boot adoption path) and, in ``raised``
mode, by ``completion_fence.raise_floor`` under the exclusive floor lock:

* ``FITX_FENCE_EVERYWHERE=dormant`` -- fence installed, floor absent: the state
  between the K6 release and activation. Historical writers must be unaffected;
  K6 claim writes are refused (AXF01) by design.
* ``FITX_FENCE_EVERYWHERE=raised`` -- the activated production state. Every
  canonical K6 writer path must pass; only a writer that adds completion
  evidence without the owner's claim is refused (AXF02).

Load with ``-p tests.qualification.fence_everywhere_plugin``. Unset or any
other value: the plugin does nothing.
"""
from __future__ import annotations

import os

MODE = os.environ.get("FITX_FENCE_EVERYWHERE", "")
_REVISION = "f" * 40


def _drop_leftover_functions(connection, migration):
    # drop_all removes the fence tables and their triggers but not the trigger
    # functions; a stale function set would make upgrade() refuse to adopt.
    for name in migration.FUNCTIONS:
        connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS public.{name}() CASCADE")


def _install(engine):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.services import completion_fence

    migration = completion_fence._migration()
    with engine.begin() as connection:
        _drop_leftover_functions(connection, migration)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration.upgrade()
    if MODE == "raised":
        with engine.connect() as connection:
            completion_fence.raise_floor(connection, _REVISION)


def pytest_configure(config):
    if MODE not in ("dormant", "raised"):
        return
    from app.extensions import db

    original = type(db).create_all

    def create_all(self, *args, **kwargs):
        original(self, *args, **kwargs)
        engine = self.engine
        if engine.dialect.name == "postgresql":
            _install(engine)

    type(db).create_all = create_all
    config.addinivalue_line("markers", "fence_everywhere: completion fence installed")


def pytest_report_header(config):
    if MODE in ("dormant", "raised"):
        return f"completion fence everywhere: {MODE}"
    return None

