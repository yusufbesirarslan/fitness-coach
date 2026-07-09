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

    assert heads == ["f8a9b0c1d2e3"]
