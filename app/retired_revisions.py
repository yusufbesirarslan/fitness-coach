"""Retired Alembic revisions: refuse them, or reconcile one verified case.

``c1a9b7e5d301`` (C1 Nutrition edit history) shipped on main 744ca3b2 with
parent ``ad17de1e0001`` and was reverted by the R1 containment (05147a04). C1
re-lands as ``19cb998acd2f`` AFTER ``e5b27aa4101c`` (owner-private exercises),
with byte-identical schema code. A database still stamped ``c1a9b7e5d301``
therefore carries C1's schema but NOT ``private_exercise``. Reusing the old
id, or stamping past it by hand, would let Alembic skip the private-exercise
migration silently.

``guard`` runs inside the migration transaction (migrations/env.py), before
Alembic reads the current heads:

* no retired stamp: nothing happens, nothing is written;
* a retired stamp without the matching opt-in: ``RetiredRevisionStamp`` names
  the stamp and the single operation that resolves it; nothing is written;
* ``FITX_RECONCILE_RETIRED_REVISION=c1a9b7e5d301`` with exactly one version
  row: the C1 schema is verified in place by the successor's own frozen
  verifier, then ONLY the version pointer moves back to the shared parent
  ``ad17de1e0001``. The same upgrade then runs ``e5b27aa4101c`` and
  ``19cb998acd2f``; both verify-or-create, so every existing row is kept.

After the migrations run, ``confirm`` requires that this invocation was a
complete upgrade to head: no stamp step, every revision from the shared parent
to head applied, and the database at head. ``flask db stamp``, ``current``,
``downgrade`` or a partial ``upgrade <rev>`` under the opt-in therefore raise.

Nothing is dropped, rewritten or faked. On PostgreSQL the pointer move and the
whole chain commit together or not at all (one migration transaction); any
failure leaves the database exactly as it was, still stamped with the retired
revision. Remove the variable from the environment afterwards; a leftover
opt-in with nothing to reconcile is logged.
"""
import logging
import os

import sqlalchemy as sa

RECONCILE_ENV = "FITX_RECONCILE_RETIRED_REVISION"

# retired revision -> (its parent, still in the graph;
#                      the successor whose frozen verifier proves its schema)
RETIRED = {"c1a9b7e5d301": ("ad17de1e0001", "19cb998acd2f")}


logger = logging.getLogger(__name__)


class RetiredRevisionStamp(RuntimeError):
    """The database is stamped with a retired revision (fail closed)."""


def current_versions(connection):
    if not sa.inspect(connection).has_table("alembic_version"):
        return []
    return sorted(row[0] for row in connection.execute(
        sa.text("SELECT version_num FROM alembic_version")))


def _refuse(retired, current, detail):
    revision = retired[0]
    parent, successor = RETIRED[revision]
    raise RetiredRevisionStamp(
        f"database is stamped with retired revision {', '.join(retired)} "
        f"(alembic_version={current}): {detail}. Nothing was migrated or stamped. "
        f"A {revision} database holds the {successor} schema but not private_exercise. "
        f"Resolve with ONE migration run under {RECONCILE_ENV}={revision}: it verifies "
        f"that schema in place and moves only the version pointer to {parent} inside "
        f"the upgrade transaction.")


def guard(connection, script, environ=None):
    """Refuse a retired stamp, or reconcile it when explicitly requested.

    Returns ``None`` when there is nothing to do, or ``(retired, parent)``
    after a verified pointer move inside the caller's transaction.
    """
    environ = os.environ if environ is None else environ
    current = current_versions(connection)
    retired = sorted(set(current) & RETIRED.keys())
    requested = environ.get(RECONCILE_ENV, "").strip()
    if not retired:
        if requested:
            logger.warning("%s=%s is set but the database is not stamped with a retired "
                           "revision; nothing to reconcile, remove the variable",
                           RECONCILE_ENV, requested)
        return None
    if len(current) != 1:
        _refuse(retired, current, "more than one version row")
    revision = current[0]
    if requested != revision:
        _refuse(retired, current, f"{RECONCILE_ENV} is not set to {revision}")
    parent, successor = RETIRED[revision]
    graph = {item.revision for item in script.walk_revisions()}
    if revision in graph:
        _refuse(retired, current, "the retired revision is back in the migration graph")
    if parent not in graph or successor not in graph or parent not in {
            item.revision for item in script.iterate_revisions(successor, "base")}:
        _refuse(retired, current, f"{parent} is not an ancestor of {successor} in this graph")
    try:
        script.get_revision(successor).module._verify(connection)
    except Exception as exc:  # noqa: BLE001 - any verifier failure refuses
        _refuse(retired, current, f"schema verification failed ({exc!r})")
    moved = connection.execute(
        sa.text("UPDATE alembic_version SET version_num = :parent "
                "WHERE version_num = :retired"),
        {"parent": parent, "retired": revision}).rowcount
    if moved != 1 or current_versions(connection) != [parent]:
        _refuse(retired, current, "the version row changed during reconciliation")
    return revision, parent


def confirm(connection, script, reconciled, applied):
    """Keep a pointer move only after a complete upgrade to head.

    ``applied`` is every ``MigrationInfo`` this invocation ran (env.py records
    them through ``on_version_apply``). Raising here, inside the migration
    transaction, rolls the pointer move back on PostgreSQL.
    """
    revision, parent = reconciled
    heads = sorted(script.get_heads())
    required = {item.revision for item in script.iterate_revisions("heads", parent)} - {parent}
    upgraded = set()
    for step in applied:
        if step.is_stamp or not step.is_upgrade:
            _refuse([revision], [parent], "the opt-in run was not a plain upgrade "
                    "(stamp/downgrade); nothing may be stamped past the retired revision")
        upgraded.update(step.up_revision_ids)
    if not required <= upgraded or current_versions(connection) != heads:
        _refuse([revision], [parent], f"the opt-in run did not upgrade through "
                f"{sorted(required - upgraded) or required} to head {heads}")
