import ast
from pathlib import Path


def _literal_assignment(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
    return None


def test_alembic_migrations_have_single_head():
    revisions = {}
    down_revisions = set()
    versions_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"

    for path in versions_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision = _literal_assignment(tree, "revision")
        down_revision = _literal_assignment(tree, "down_revision")
        assert revision, f"{path.name} is missing revision"
        revisions[revision] = path.name
        if isinstance(down_revision, (tuple, list)):
            down_revisions.update(item for item in down_revision if item)
        elif down_revision:
            down_revisions.add(down_revision)

    heads = sorted(set(revisions) - down_revisions)

    assert heads == ["bb22cc33dd44"]


def test_activity_trigger_revision_is_postgresql_guarded():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "bb22cc33dd44_activity_calorie_trigger.py"
    )
    source = migration_path.read_text(encoding="utf-8")

    assert source.count('op.get_bind().dialect.name != "postgresql"') == 2
    assert "CREATE OR REPLACE FUNCTION calc_activity_calories" in source
    assert "CREATE TRIGGER trg_calc_activity" in source
    assert "DROP TRIGGER IF EXISTS trg_calc_activity ON daily_activity" in source
    assert "DROP FUNCTION IF EXISTS calc_activity_calories()" in source
