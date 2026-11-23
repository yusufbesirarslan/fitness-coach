"""Retired ``c1a9b7e5d301`` stamps: refused by default, reconciled only verified.

C1 re-lands as ``19cb998acd2f`` after ``e5b27aa4101c``. A database stamped with
the reverted ``c1a9b7e5d301`` holds the C1 schema without ``private_exercise``.
These tests exercise ``app.retired_revisions.guard`` on synthetic SQLite
databases. The real chain on PostgreSQL (env.py, one transaction, populated
rows) is ``tests/test_r6_forward_chain_pg.py::test_retired_c1_stamp_*``.
"""
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app import retired_revisions as rr

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ScriptDirectory(str(ROOT / "migrations"))
RETIRED, PARENT, SUCCESSOR = "c1a9b7e5d301", "ad17de1e0001", "19cb998acd2f"
OPT_IN = {rr.RECONCILE_ENV: RETIRED}


def test_graph_retires_c1a9_and_relands_c1_after_private_exercises():
    graph = {item.revision for item in SCRIPT.walk_revisions()}
    assert RETIRED not in graph
    assert SCRIPT.get_heads() == [SUCCESSOR]
    assert SCRIPT.get_revision(SUCCESSOR).down_revision == "e5b27aa4101c"
    assert SCRIPT.get_revision("e5b27aa4101c").down_revision == PARENT
    assert rr.RETIRED == {RETIRED: (PARENT, SUCCESSOR)}
    assert not list((ROOT / "migrations" / "versions").glob(f"{RETIRED}_*.py"))


def _historical(conn, stamp=(RETIRED,)):
    """What the reverted C1 left behind: C1 schema and history, no private_exercise."""
    conn.execute(sa.text('CREATE TABLE "user" (id INTEGER PRIMARY KEY)'))
    conn.execute(sa.text("CREATE TABLE nutrition_plan (id INTEGER PRIMARY KEY, "
                         "user_id INTEGER NOT NULL, plan_data TEXT NOT NULL)"))
    conn.execute(sa.text('INSERT INTO "user" (id) VALUES (1)'))
    conn.execute(sa.text("INSERT INTO nutrition_plan (id, user_id, plan_data) "
                         "VALUES (1, 1, ' exact bytes ')"))
    with Operations.context(MigrationContext.configure(conn)):
        SCRIPT.get_revision(SUCCESSOR).module.upgrade()
    conn.execute(sa.text(
        "INSERT INTO nutrition_plan_mutation_record (id, public_id, user_id, plan_lineage_id, "
        "idempotency_key, command_fingerprint, operation_kind, before_version, after_version, "
        "before_snapshot, after_snapshot, created_at) VALUES (1, 'pub-1', 1, 'lineage-1', "
        "'key-1', :f, 'mutation', 0, 1, 'before', 'after', '2026-11-20 00:00:00')"),
        {"f": "f" * 64})
    conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
    for revision in stamp:
        conn.execute(sa.text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})


def _state(conn):
    return {
        "versions": rr.current_versions(conn),
        "plans": conn.execute(sa.text(
            "SELECT id, plan_data, lineage_id, mutation_version FROM nutrition_plan")).all(),
        "journal": conn.execute(sa.text("SELECT * FROM nutrition_plan_mutation_record")).all(),
        "private_exercise": sa.inspect(conn).has_table("private_exercise"),
    }


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        yield connection
    engine.dispose()


def test_no_version_table_head_or_other_stamps_are_untouched(conn):
    assert rr.guard(conn, SCRIPT, OPT_IN) is None
    conn.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
    for revision in (PARENT, "e5b27aa4101c", SUCCESSOR):
        conn.execute(sa.text("DELETE FROM alembic_version"))
        conn.execute(sa.text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision})
        assert rr.guard(conn, SCRIPT, OPT_IN) is None
        assert rr.current_versions(conn) == [revision]


@pytest.mark.parametrize("environ", [{}, {rr.RECONCILE_ENV: ""},
                                     {rr.RECONCILE_ENV: PARENT},
                                     {rr.RECONCILE_ENV: SUCCESSOR}])
def test_retired_stamp_fails_closed_without_the_exact_opt_in(conn, environ):
    _historical(conn)
    before = _state(conn)
    with pytest.raises(rr.RetiredRevisionStamp) as refused:
        rr.guard(conn, SCRIPT, environ)
    message = str(refused.value)
    assert RETIRED in message and "private_exercise" in message
    assert f"{rr.RECONCILE_ENV}={RETIRED}" in message and "Nothing was migrated" in message
    assert _state(conn) == before


def test_retired_stamp_among_several_version_rows_fails_closed(conn):
    _historical(conn, stamp=(RETIRED, "e5b27aa4101c"))
    before = _state(conn)
    with pytest.raises(rr.RetiredRevisionStamp, match="more than one version row"):
        rr.guard(conn, SCRIPT, OPT_IN)
    assert _state(conn) == before


@pytest.mark.parametrize("damage", [
    "DROP INDEX uq_nutrition_edit_active_owner",
    "DROP TABLE nutrition_plan_mutation_record",
    "ALTER TABLE nutrition_plan_edit_operation ADD COLUMN wrong INTEGER",
])
def test_opt_in_with_a_drifted_schema_fails_closed(conn, damage):
    _historical(conn)
    conn.execute(sa.text(damage))
    versions = rr.current_versions(conn)
    with pytest.raises(rr.RetiredRevisionStamp, match="schema verification failed"):
        rr.guard(conn, SCRIPT, OPT_IN)
    assert rr.current_versions(conn) == versions == [RETIRED]


def test_verified_opt_in_moves_only_the_version_pointer(conn):
    _historical(conn)
    before = _state(conn)
    assert before["plans"][0][2] and before["plans"][0][3] == 0
    assert rr.guard(conn, SCRIPT, OPT_IN) == (RETIRED, PARENT)
    after = _state(conn)
    assert after["versions"] == [PARENT]
    # Rows, lineage identities and history are untouched; private_exercise is
    # still absent and is created by e5b27aa4101c in the same upgrade.
    assert {**after, "versions": before["versions"]} == before
    assert after["private_exercise"] is False


def test_a_graph_that_still_carries_the_retired_revision_is_refused(conn):
    _historical(conn)

    class _Graph:
        def walk_revisions(self):
            yield from SCRIPT.walk_revisions()
            yield type("R", (), {"revision": RETIRED})()

        def __getattr__(self, name):
            return getattr(SCRIPT, name)

    with pytest.raises(rr.RetiredRevisionStamp, match="back in the migration graph"):
        rr.guard(conn, _Graph(), OPT_IN)
    assert rr.current_versions(conn) == [RETIRED]
