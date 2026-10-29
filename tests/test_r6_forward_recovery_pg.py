"""R6-R1-28 on real PostgreSQL: the evidence forward recovery acts on.

The hermetic matrix (``tests/test_r6_forward_recovery.py``) models the host.
What only a real database can show is here:

* ``flask schema-heads`` reports the exact live Alembic heads inside a READ
  ONLY transaction and writes nothing (schema, reference data, versions);
* the transaction's real ``_recovery_schema_gate`` admits exactly the post-
  migration states (fully migrated, interrupted midway) and refuses the
  pre-migration, foreign-revision and missing-table states;
* the boundary it draws is the boot boundary: after migration the PREVIOUS
  tree's read-only boot refuses (so the old writer cannot be resurrected)
  while the candidate's boots; before migration the previous tree still boots;
* an interrupted migration is completed forward by the real release-prepare.

"Previous" is the repository graph minus its newest twelve revisions (the R1
delta is twelve migrations); the candidate is the full repository graph.
Never point PG_TEST_DATABASE_URL at a database you care about.
"""
import shutil
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.test_r6_dual_revision_pg import (  # noqa: F401 - pg_url is a fixture
    _WRITE_SQL, _boot_tool, _dispose, _fingerprint, _versions, pg_url,
)

pytestmark = pytest.mark.pg_concurrency

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import migration_expand_contract as mec  # noqa: E402
import r6_deploy_transaction as tx  # noqa: E402

DELTA = 12
PREV_SHA = "a" * 40
CAND_SHA = "c" * 40


def _graph():
    from alembic.script import ScriptDirectory
    script = ScriptDirectory(str(ROOT / "migrations"))
    (head,) = script.get_heads()
    order = [rev.revision for rev in script.walk_revisions()]   # head -> base
    previous_head = order[DELTA]
    previous = {rev.revision for rev in script.iterate_revisions(previous_head, "base")}
    delta = set(order) - previous
    assert len(delta) == DELTA and head in delta
    midway = order[DELTA // 2]
    assert midway in delta
    return head, previous_head, midway, previous, delta


@pytest.fixture
def trees(tmp_path):
    """The candidate tree (the repository) and a previous tree without the delta."""
    head, previous_head, midway, previous, delta = _graph()
    previous_dir = tmp_path / "previous-migrations"
    shutil.copytree(ROOT / "migrations", previous_dir,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for path in (previous_dir / "versions").glob("*.py"):
        if path.name.split("_", 1)[0] in delta:
            path.unlink()
    candidate_revisions = frozenset(
        tx._revisions_with_digests(ROOT / "migrations" / "versions", mec))
    previous_revisions = frozenset(
        tx._revisions_with_digests(previous_dir / "versions", mec))
    assert previous_revisions == previous and candidate_revisions - previous_revisions == delta
    return {"head": head, "previous_head": previous_head, "midway": midway,
            "previous_dir": previous_dir, "candidate": candidate_revisions,
            "previous": previous_revisions}


def _set_state(monkeypatch, state, trees):
    import flask_migrate

    from app.extensions import db
    tool = _boot_tool(monkeypatch)
    try:
        with tool.app_context():
            target = {"pre-migration": trees["previous_head"], "midway": trees["midway"],
                      "foreign": trees["head"], "missing": trees["head"],
                      "migrated": trees["head"]}[state]
            flask_migrate.upgrade(revision=target)
            if state == "foreign":
                db.session.execute(sa.text(
                    "UPDATE alembic_version SET version_num = 'ffffffffffff'"))
                db.session.commit()
            elif state == "missing":
                db.session.execute(sa.text("DROP TABLE alembic_version"))
                db.session.commit()
    finally:
        _dispose(tool)


def _schema_heads(monkeypatch, statements=None):
    """The real CLI, as the one-off container runs it (FITX_SKIP_DB_INIT=1)."""
    import json
    tool = _boot_tool(monkeypatch)

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    if statements is not None:
        sa.event.listen(sa.engine.Engine, "before_cursor_execute", record)
    try:
        result = tool.test_cli_runner().invoke(args=["schema-heads"])
    finally:
        if statements is not None:
            sa.event.remove(sa.engine.Engine, "before_cursor_execute", record)
        _dispose(tool)
    lines = [line for line in result.output.splitlines()
             if line.startswith(tx.SCHEMA_HEADS_PREFIX)]
    assert len(lines) == 1, result.output
    return result.exit_code, json.loads(lines[0][len(tx.SCHEMA_HEADS_PREFIX):])


class GateOps:
    """Only what the real gate reads; the payload comes from the real CLI."""

    def __init__(self, payload, trees):
        self.payload, self.trees = payload, trees

    def schema_heads(self, image_id, revision, timeout):
        assert image_id == "sha256:" + "c" * 64 and revision == CAND_SHA
        return self.payload

    def source_migration_revisions(self, which, timeout):
        return self.trees[which]


def _gate(payload, trees):
    transaction = tx.Transaction(GateOps(payload, trees), deploy_sha=CAND_SHA,
                                 previous_commit=PREV_SHA,
                                 public_health_url="https://fitx.example/health",
                                 log=lambda message: None, mode="forward-recovery")
    transaction.candidate_image_id = "sha256:" + "c" * 64
    transaction._recovery_schema_gate()
    return transaction


# ── schema-heads is exact and read-only ─────────────────────────────────────

def test_schema_heads_reports_the_exact_heads_and_writes_nothing(pg_url, monkeypatch, trees):
    _set_state(monkeypatch, "migrated", trees)
    before = _fingerprint(pg_url)
    statements = []
    code, payload = _schema_heads(monkeypatch, statements)
    assert code == 0
    assert payload == {"result": "ok", "has_table": True, "current": [trees["head"]]}
    assert "SET TRANSACTION READ ONLY" in statements
    assert [s for s in statements if _WRITE_SQL.match(s)] == []
    assert _fingerprint(pg_url) == before


def test_schema_heads_reports_a_missing_version_table_as_such(pg_url, monkeypatch, trees):
    _set_state(monkeypatch, "missing", trees)
    code, payload = _schema_heads(monkeypatch)
    assert code == 0 and payload == {"result": "ok", "has_table": False, "current": []}
    assert tx.parse_schema_heads(payload) is None


# ── the real gate on real schema states ─────────────────────────────────────

@pytest.mark.parametrize("state,admitted", [
    ("migrated", True), ("midway", True),
    ("pre-migration", False), ("foreign", False), ("missing", False),
])
def test_recovery_gate_admits_exactly_the_post_migration_states(
        pg_url, monkeypatch, trees, state, admitted):
    _set_state(monkeypatch, state, trees)
    _, payload = _schema_heads(monkeypatch)
    if admitted:
        _gate(payload, trees)
    else:
        with pytest.raises(tx.StepFailed):
            _gate(payload, trees)


# ── the gate's boundary is the boot boundary (I: no resurrection) ───────────

def test_after_migration_the_previous_writer_cannot_boot_but_the_candidate_can(
        pg_url, monkeypatch, trees):
    from app.schema_safety import SchemaNotReady
    _set_state(monkeypatch, "migrated", trees)
    before = _fingerprint(pg_url)
    with monkeypatch.context() as scoped:
        scoped.setattr("app.schema_safety.migrations_directory",
                       lambda app: str(trees["previous_dir"]))
        scoped.delenv("FITX_SKIP_DB_INIT", raising=False)
        scoped.setenv("FITX_STARTUP_MODE", "read-only")
        from app import create_app
        with pytest.raises(SchemaNotReady) as refused:
            create_app()
    assert refused.value.reason == "unknown_revision"
    assert _fingerprint(pg_url) == before
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")
    from app import create_app
    candidate = create_app()
    try:
        assert candidate.test_client().get("/health").status_code == 200
    finally:
        _dispose(candidate)


def test_before_migration_the_previous_writer_still_boots(pg_url, monkeypatch, trees):
    """Scenario B's fact: no post-migration evidence, previous restartable."""
    _set_state(monkeypatch, "pre-migration", trees)
    monkeypatch.setattr("app.schema_safety.migrations_directory",
                        lambda app: str(trees["previous_dir"]))
    monkeypatch.delenv("FITX_SKIP_DB_INIT", raising=False)
    monkeypatch.setenv("FITX_STARTUP_MODE", "read-only")
    from app import create_app
    previous = create_app()
    _dispose(previous)


# ── G: an interrupted migration is completed forward ────────────────────────

def test_interrupted_migration_is_completed_by_the_real_release_prepare(
        pg_url, monkeypatch, trees):
    _set_state(monkeypatch, "midway", trees)
    _, payload = _schema_heads(monkeypatch)
    assert payload["current"] == [trees["midway"]]
    _gate(payload, trees)
    tool = _boot_tool(monkeypatch)
    try:
        result = tool.test_cli_runner().invoke(args=["release-prepare"])
    finally:
        _dispose(tool)
    assert result.exit_code == 0, result.output
    assert f"schema at head {trees['head']}" in result.output
    assert _versions(pg_url) == [trees["head"]]
    _, payload = _schema_heads(monkeypatch)
    assert payload["current"] == [trees["head"]]
    _gate(payload, trees)                                     # still admitted: idempotent
